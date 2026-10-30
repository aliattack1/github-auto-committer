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

    try:
        with temp_extract(zip_path) as project_root:
            _log("extract", f"Extracted to temp dir: {project_root}", 0.05)

            files = filter_files(project_root, extra_excludes=extra_excludes)
            if not files:
                raise ValueError("No files survived the .gitignore filter — nothing to commit.")
            _log("filter", f"{len(files)} file(s) will be committed", 0.15)

            # Resolve target branch HEAD + tree SHA (may create branch)
            _log("refs", f"Resolving branch '{target_branch}' on GitHub...", 0.2)
            commit_sha_head, tree_sha_head = client.ensure_branch(owner, name, target_branch, repo.default_branch)
            _log("refs", f"Head commit: {commit_sha_head[:8]}...", 0.25)

            # 1. Upload blobs
            tree_entries = []
            total = len(files)
            for i, f in enumerate(files):
                with open(f.abs_path_str, "rb") as fh:
                    content = fh.read()
                sha = client.create_blob(owner, name, content)
                tree_entries.append(
                    {
                        "path": f.repo_path,
                        "mode": "100644",  # regular file
                        "type": "blob",
                        "sha": sha,
                    }
                )
                pct = 0.25 + 0.55 * ((i + 1) / total)
                _log("blobs", f"Uploaded blob {i + 1}/{total}: {f.repo_path}", pct)

            # 2. Create tree (with base_tree so existing files are preserved)
            _log("tree", "Creating tree...", 0.82)
            new_tree_sha = client.create_tree(
                owner, name, base_tree=tree_sha_head, tree_entries=tree_entries
            )
            _log("tree", f"New tree SHA: {new_tree_sha[:8]}...", 0.88)

            # 3. Create commit
            _log("commit", "Creating commit...", 0.92)
            parents = [commit_sha_head] if commit_sha_head else []
            new_commit_sha = client.create_commit(
                owner,
                name,
                message=msg,
                tree_sha=new_tree_sha,
                parent_shas=parents,
            )
            _log("commit", f"Commit SHA: {new_commit_sha[:8]}...", 0.95)

            # 4. Update branch ref
            _log("push", f"Updating ref refs/heads/{target_branch}...", 0.97)
            client.update_ref(
                owner, name, ref=target_branch, sha=new_commit_sha, force=False
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
