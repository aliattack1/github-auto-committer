"""High-level GitHub file operations for the File Manager page.

Wraps :class:`GitHubClient`'s Contents API methods with friendly
dataclasses and progress callbacks. Used by ``ui/file_manager_view.py``.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Callable

from .github_client import GitHubClient, GitHubError, RepoInfo

ProgressCb = Callable[[str, str], None]


@dataclass(slots=True)
class RepoEntry:
    """A file or folder inside a repository."""

    name: str
    path: str  # full path within the repo
    type: str  # "file" | "dir"
    size: int
    sha: str


def _split_owner_name(repo: RepoInfo) -> tuple[str, str]:
    owner = repo.full_name.split("/", 1)[0]
    return owner, repo.name


def list_repo_path(
    client: GitHubClient,
    repo: RepoInfo,
    path: str = "",
    *,
    branch: str | None = None,
) -> list[RepoEntry]:
    """Return files and subdirs at ``path`` in ``repo``."""
    owner, name = _split_owner_name(repo)
    ref = branch or repo.default_branch
    raw = client.list_contents(owner, name, path, ref=ref)
    out: list[RepoEntry] = []
    for item in raw:
        out.append(
            RepoEntry(
                name=item.get("name", ""),
                path=item.get("path", ""),
                type=item.get("type", "file"),
                size=int(item.get("size", 0)),
                sha=item.get("sha", ""),
            )
        )
    # Folders first, then files, alphabetical within each group
    out.sort(key=lambda e: (e.type != "dir", e.name.lower()))
    return out


def upload_file_to_repo(
    client: GitHubClient,
    repo: RepoInfo,
    dest_path: str,
    content: bytes,
    *,
    message: str | None = None,
    branch: str | None = None,
    progress: ProgressCb | None = None,
) -> str:
    """Upload a single file to ``dest_path`` in ``repo``. Returns the new commit SHA."""
    owner, name = _split_owner_name(repo)
    target_branch = branch or repo.default_branch
    msg = message or f"chore: upload {dest_path}"

    # Check if file already exists → need its SHA to update instead of create.
    existing_sha: str | None = None
    try:
        _, meta = client.get_file_content(owner, name, dest_path, ref=target_branch)
        existing_sha = meta.get("sha")
        if progress:
            progress("upload", f"File exists (sha={existing_sha[:8]}...) — will update.")
    except GitHubError as e:
        if e.status != 404:
            raise
        if progress:
            progress("upload", f"New file at {dest_path!r}.")

    if progress:
        progress("upload", f"Uploading {len(content)} bytes to {repo.full_name}:{dest_path}...")
    result = client.put_file(
        owner, name, dest_path, content,
        message=msg, branch=target_branch, sha=existing_sha,
    )
    commit_sha = result.get("commit", {}).get("sha", "")
    if progress:
        progress("upload", f"Done — commit {commit_sha[:8]}...")
    return commit_sha


def create_folder_in_repo(
    client: GitHubClient,
    repo: RepoInfo,
    folder_path: str,
    *,
    message: str | None = None,
    branch: str | None = None,
    progress: ProgressCb | None = None,
) -> str:
    """Create a folder by committing a ``.gitkeep`` file inside it.

    Git doesn't actually have empty folders — a folder only exists if it
    contains at least one file. We create ``<folder_path>/.gitkeep``.
    """
    folder_path = folder_path.strip("/")
    if not folder_path:
        raise ValueError("Folder path cannot be empty.")
    file_path = f"{folder_path}/.gitkeep"
    msg = message or f"chore: create folder {folder_path}/"
    if progress:
        progress("folder", f"Creating {folder_path}/ via .gitkeep...")
    return upload_file_to_repo(
        client, repo, file_path, b"", message=msg, branch=branch, progress=progress,
    )


def create_text_file_in_repo(
    client: GitHubClient,
    repo: RepoInfo,
    dest_path: str,
    text: str,
    *,
    message: str | None = None,
    branch: str | None = None,
    progress: ProgressCb | None = None,
) -> str:
    """Create a new text file with the given content."""
    msg = message or f"chore: create {dest_path}"
    return upload_file_to_repo(
        client, repo, dest_path, text.encode("utf-8"), message=msg, branch=branch, progress=progress,
    )


def copy_file_between_repos(
    client: GitHubClient,
    src: RepoInfo,
    src_path: str,
    dst: RepoInfo,
    dst_path: str,
    *,
    message: str | None = None,
    src_branch: str | None = None,
    dst_branch: str | None = None,
    progress: ProgressCb | None = None,
) -> str:
    """Copy a single file from one repo to another."""
    src_owner, src_name = _split_owner_name(src)
    src_ref = src_branch or src.default_branch

    if progress:
        progress("copy", f"Fetching {src.full_name}:{src_path}...")
    content, _meta = client.get_file_content(src_owner, src_name, src_path, ref=src_ref)
    if progress:
        progress("copy", f"Got {len(content)} bytes. Uploading to {dst.full_name}:{dst_path}...")

    msg = message or f"chore: copy {src_path} from {src.full_name}"
    return upload_file_to_repo(
        client, dst, dst_path, content, message=msg, branch=dst_branch, progress=progress,
    )


def delete_file_in_repo(
    client: GitHubClient,
    repo: RepoInfo,
    path: str,
    file_sha: str,
    *,
    message: str | None = None,
    branch: str | None = None,
    progress: ProgressCb | None = None,
) -> str:
    """Delete a single file from ``repo``. Returns the new commit SHA."""
    owner, name = _split_owner_name(repo)
    target_branch = branch or repo.default_branch
    msg = message or f"chore: delete {path}"
    if progress:
        progress("delete", f"Deleting {repo.full_name}:{path}...")
    result = client.delete_file(
        owner, name, path, message=msg, sha=file_sha, branch=target_branch,
    )
    commit_sha = result.get("commit", {}).get("sha", "")
    if progress:
        progress("delete", f"Done — commit {commit_sha[:8]}...")
    return commit_sha


def read_text_file_in_repo(
    client: GitHubClient,
    repo: RepoInfo,
    path: str,
    *,
    branch: str | None = None,
) -> str:
    """Return the decoded text content of a file in the repo."""
    owner, name = _split_owner_name(repo)
    ref = branch or repo.default_branch
    content, _meta = client.get_file_content(owner, name, path, ref=ref)
    return content.decode("utf-8", errors="replace")
