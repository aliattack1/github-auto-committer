"""End-to-end commit pipeline.

Reads a zip, extracts it under a temp dir, filters files through the
.gitignore-aware matcher, then pushes the surviving files as a single
commit to GitHub using the create-blob → tree → commit → update-ref
REST sequence. Emits progress callbacks so the UI can show a live log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .github_client import GitHubClient, GitHubError, RepoInfo
from .gitignore_filter import filter_files
from .storage import HistoryEntry, Storage
from .zip_processor import temp_extract

# Progress callback signature: (phase: str, message: str, percent: float 0..1)
ProgressCb = Callable[[str, str, float], None]


@dataclass(slots=True)
class CommitResult:
    success: bool
    commit_sha: str
    file_count: int
    error: str = ""
    message: str = ""
    html_url: str = ""


def _default_commit_message() -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    return f"chore: auto-commit export {ts}"


def commit_zip_to_repo(
    *,
    client: GitHubClient,
    storage: Storage,
    zip_path: str,
    repo: RepoInfo,
    branch: str | None = None,
    message: str | None = None,
    extra_excludes: list[str] | None = None,
    progress: ProgressCb | None = None,
) -> CommitResult:
    """Commit every surviving file from ``zip_path`` to ``repo``.

    Returns a :class:`CommitResult` and appends to the in-app history.
    """
    target_branch = branch or repo.default_branch or "main"
    msg = message or _default_commit_message()
    owner = repo.full_name.split("/", 1)[0]
    name = repo.name

    def _log(phase: str, msg: str, pct: float = 0.0) -> None:
        if progress:
            progress(phase, msg, pct)

    _log("init", f"Preparing commit to {repo.full_name}@{target_branch}")
    _log("init", f"Message: {msg}")
    _log("init", f"Owner: {owner!r}, repo name: {name!r}")

    try:
        with temp_extract(zip_path) as project_root:
            _log("extract", f"Extracted to temp dir: {project_root}", 0.05)

            files = filter_files(project_root, extra_excludes=extra_excludes)
            if not files:
                raise ValueError("No files survived the .gitignore filter — nothing to commit.")
            _log("filter", f"{len(files)} file(s) will be committed", 0.15)

            # CRITICAL: Read ALL file contents into memory IMMEDIATELY, before
            # starting any uploads. On Android the cache dir (where temp_extract
            # extracts to, if the persistent files dir isn't writable) can be
            # wiped mid-operation by the OS — causing FileNotFoundError when
            # we try to open() files later during the blob-upload loop.
            # By reading everything into RAM now, we're immune to cache wipes.
            _log("cache", f"Reading {len(files)} file(s) into memory...", 0.18)
            in_memory_files: list[tuple[str, bytes]] = []
            total_bytes = 0
            for f in files:
                try:
                    with open(f.abs_path_str, "rb") as fh:
                        content = fh.read()
                    in_memory_files.append((f.repo_path, content))
                    total_bytes += len(content)
                except OSError as e:
                    raise OSError(
                        f"Failed to read {f.repo_path!r} from {f.abs_path_str!r}: {e}. "
                        f"The temp extraction dir may have been wiped mid-scan."
                    ) from e
            _log(
                "cache",
                f"Cached {len(in_memory_files)} file(s), {total_bytes / 1024:.1f} KB in RAM — "
                f"immune to cache-dir wipes during upload.",
                0.2,
            )

            # Resolve target branch HEAD + tree SHA (may create branch).
            # For a completely empty repo, returns ("", None) — we'll do
            # an initial commit with no parents and no base_tree.
            _log("refs", f"Resolving branch '{target_branch}' on GitHub...", 0.22)
            commit_sha_head, tree_sha_head = client.ensure_branch(owner, name, target_branch, repo.default_branch)
            if commit_sha_head:
                _log("refs", f"Head commit: {commit_sha_head[:8]}...", 0.25)
                _log("refs", f"Base tree: {(tree_sha_head or '')[:8]}...", 0.27)
            else:
                _log("refs", "Repo is empty — will create initial commit.", 0.25)

            # 1. Upload blobs — from the in-memory cache, NOT from disk.
            #    Even if the temp extraction dir has been wiped by Android
            #    at this point, the uploads will still succeed.
            tree_entries = []
            total = len(in_memory_files)
            for i, (repo_path, content) in enumerate(in_memory_files):
                try:
                    sha = client.create_blob(owner, name, content)
                except GitHubError as e:
                    raise GitHubError(
                        e.status,
                        f"create_blob failed for {repo_path!r}: {e.message}",
                        e.payload,
                    )
                tree_entries.append(
                    {
                        "path": repo_path,
                        "mode": "100644",  # regular file
                        "type": "blob",
                        "sha": sha,
                    }
                )
                pct = 0.27 + 0.55 * ((i + 1) / total)
                _log("blobs", f"Uploaded blob {i + 1}/{total}: {repo_path}", pct)

            # Free the in-memory cache — no longer needed after all blobs are uploaded.
            in_memory_files.clear()

            # 2. Create tree. For an empty repo, base_tree is None and
            #    the tree is created from scratch (no parent tree).
            #    Detect entries under .github/workflows/ to pre-warn about
            #    the missing-workflow-scope issue (GitHub returns 404 here
            #    instead of 403 when the PAT lacks the `workflow` scope).
            workflow_entries = [
                e for e in tree_entries
                if e["path"].startswith(".github/workflows/")
                or e["path"] == ".github/workflows"
            ]
            if workflow_entries:
                _log(
                    "tree",
                    f"Note: {len(workflow_entries)} file(s) under .github/workflows/ "
                    f"— PAT needs `workflow` scope or this will 404.",
                    0.81,
                )
            _log("tree", f"Creating tree (base_tree={tree_sha_head!r})...", 0.82)
            try:
                new_tree_sha = client.create_tree(
                    owner, name, base_tree=tree_sha_head, tree_entries=tree_entries
                )
            except GitHubError as e:
                if e.status == 404 and workflow_entries:
                    raise GitHubError(
                        e.status,
                        "create_tree returned 404 — this is GitHub's buggy way of "
                        "saying your PAT is missing the `workflow` scope. "
                        f"{len(workflow_entries)} file(s) under .github/workflows/ "
                        f"were detected in this commit. Go to "
                        f"https://github.com/settings/tokens and create a new PAT "
                        f"with BOTH `repo` AND `workflow` scopes, then sign out and "
                        f"sign back in with the new token.",
                        e.payload,
                    )
                raise GitHubError(
                    e.status,
                    f"create_tree failed (base_tree={tree_sha_head!r}, "
                    f"{len(tree_entries)} entries): {e.message}",
                    e.payload,
                )
            _log("tree", f"New tree SHA: {new_tree_sha[:8]}...", 0.88)

            # 3. Create commit. For an empty repo, parents is [] (initial commit).
            _log("commit", "Creating commit...", 0.92)
            parents = [commit_sha_head] if commit_sha_head else []
            try:
                new_commit_sha = client.create_commit(
                    owner,
                    name,
                    message=msg,
                    tree_sha=new_tree_sha,
                    parent_shas=parents,
                )
            except GitHubError as e:
                raise GitHubError(
                    e.status,
                    f"create_commit failed (tree={new_tree_sha[:8]}..., "
                    f"{len(parents)} parent(s)): {e.message}",
                    e.payload,
                )
            _log("commit", f"Commit SHA: {new_commit_sha[:8]}...", 0.95)

            # 4. Update or create branch ref. For an empty repo the ref
            #    doesn't exist yet, so we CREATE it. For a non-empty repo
            #    where the target branch existed, we PATCH it.
            if commit_sha_head:
                # Branch existed — update its ref to point at the new commit.
                _log("push", f"Updating ref refs/heads/{target_branch}...", 0.97)
                try:
                    client.update_ref(
                        owner, name, ref=target_branch, sha=new_commit_sha, force=False
                    )
                except GitHubError as e:
                    raise GitHubError(
                        e.status,
                        f"update_ref failed for refs/heads/{target_branch}: {e.message}",
                        e.payload,
                    )
            else:
                # Empty repo — create the initial branch ref.
                _log("push", f"Creating initial ref refs/heads/{target_branch}...", 0.97)
                try:
                    client.create_ref(owner, name, ref=target_branch, sha=new_commit_sha)
                except GitHubError as e:
                    raise GitHubError(
                        e.status,
                        f"create_ref failed for refs/heads/{target_branch}: {e.message}",
                        e.payload,
                    )

            html_url = f"https://github.com/{repo.full_name}/commit/{new_commit_sha}"
            _log("done", f"Pushed {len(files)} file(s).", 1.0)
            _log("done", html_url, 1.0)

            # Record history
            storage.add_history(
                HistoryEntry(
                    timestamp=time.time(),
                    repo_full_name=repo.full_name,
                    branch=target_branch,
                    message=msg,
                    file_count=len(files),
                    status="ok",
                    commit_sha=new_commit_sha,
                )
            )

            return CommitResult(
                success=True,
                commit_sha=new_commit_sha,
                file_count=len(files),
                message=msg,
                html_url=html_url,
            )

    except (GitHubError, ValueError, OSError) as e:
        _log("error", f"Failed: {e}", 1.0)
        storage.add_history(
            HistoryEntry(
                timestamp=time.time(),
                repo_full_name=repo.full_name,
                branch=target_branch,
                message=msg,
                file_count=0,
                status="error",
                error=str(e),
            )
        )
        return CommitResult(
            success=False,
            commit_sha="",
            file_count=0,
            error=str(e),
            message=msg,
        )
