"""GitHub REST API client.

Uses HTTP Basic auth with username + Personal Access Token (PAT).
The PAT plays the role of "password" because GitHub disabled
password-based API auth in August 2021.

Implements the create-blob → tree → commit → update-ref pipeline so a
whole folder of files lands as a single commit (one HTTP round-trip per
file for blobs, then three more calls total for tree/commit/ref).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Iterable

import httpx

GITHUB_API = "https://api.github.com"


class GitHubError(Exception):
    """Raised when GitHub returns an error or non-success status."""

    def __init__(self, status: int, message: str, payload: dict | None = None):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload or {}


@dataclass(slots=True)
class RepoInfo:
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str

    @classmethod
    def from_api(cls, data: dict) -> "RepoInfo":
        return cls(
            name=data["name"],
            full_name=data["full_name"],
            private=data.get("private", False),
            default_branch=data.get("default_branch") or "main",
            html_url=data.get("html_url", ""),
        )


class GitHubClient:
    """Thin wrapper around the GitHub REST API.

    One instance per login session. All methods raise ``GitHubError`` on
    failure so the caller can show the user a friendly message.
    """

    def __init__(self, username: str, token: str, *, timeout: float = 30.0):
        self.username = username
        self.token = token
        self._client = httpx.Client(
            base_url=GITHUB_API,
            auth=(username, token),
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "github-auto-committer/1.0",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                payload = resp.json()
                msg = payload.get("message") or resp.text
            except Exception:
                payload = {}
                msg = resp.text
            raise GitHubError(resp.status_code, msg, payload)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ------------------------------------------------------------------
    # Auth / user
    # ------------------------------------------------------------------
    def verify_credentials(self) -> dict:
        """Return the authenticated user's profile, or raise."""
        return self._request("GET", "/user")

    # ------------------------------------------------------------------
    # Repos
    # ------------------------------------------------------------------
    def list_repos(self, *, per_page: int = 100, sort: str = "updated") -> list[RepoInfo]:
        """List repositories the authenticated user owns or collaborates on."""
        repos: list[RepoInfo] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                "/user/repos",
                params={
                    "per_page": per_page,
                    "page": page,
                    "sort": sort,
                    "direction": "desc",
                    "affiliation": "owner,collaborator",
                },
            )
            if not data:
                break
            repos.extend(RepoInfo.from_api(r) for r in data)
            if len(data) < per_page:
                break
            page += 1
            if page > 20:  # safety cap, ~2000 repos
                break
        return repos

    def create_repo(
        self,
        name: str,
        *,
        private: bool = False,
        description: str = "",
        auto_init: bool = True,
    ) -> RepoInfo:
        payload = {
            "name": name,
            "private": private,
            "description": description,
            "auto_init": auto_init,
        }
        data = self._request("POST", "/user/repos", json=payload)
        return RepoInfo.from_api(data)

    def get_repo(self, owner: str, repo: str) -> RepoInfo:
        data = self._request("GET", f"/repos/{owner}/{repo}")
        return RepoInfo.from_api(data)

    # ------------------------------------------------------------------
    # Commit pipeline
    # ------------------------------------------------------------------
    def get_ref(self, owner: str, repo: str, ref: str = "heads/main") -> dict:
        """Return {'ref': ..., 'object': {'sha': ...}} for the given ref.

        Raises ``GitHubError(404)`` if the ref does not exist — the caller
        is expected to handle that case (e.g. by creating the branch).
        """
        return self._request("GET", f"/repos/{owner}/{repo}/git/refs/{ref}")

    def get_commit(self, owner: str, repo: str, sha: str) -> dict:
        return self._request("GET", f"/repos/{owner}/{repo}/git/commits/{sha}")

    def create_blob(self, owner: str, repo: str, content: bytes, *, encoding: str = "base64") -> str:
        """Upload a single file's content as a blob. Returns the blob SHA."""
        if encoding == "base64":
            content_str = base64.b64encode(content).decode("ascii")
        else:
            content_str = content.decode("utf-8")
        data = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/blobs",
            json={"content": content_str, "encoding": encoding},
        )
        return data["sha"]

    def create_tree(
        self,
        owner: str,
        repo: str,
        *,
        base_tree: str | None,
        tree_entries: Iterable[dict],
    ) -> str:
        """Create a tree from a list of {path, mode, type, sha} entries.

        ``base_tree`` may be ``None`` (or empty string) for the very first
        commit on an empty repo — in that case the tree is created from
        scratch with no parent tree.
        """
        payload: dict = {"tree": list(tree_entries)}
        if base_tree:
            payload["base_tree"] = base_tree
        data = self._request("POST", f"/repos/{owner}/{repo}/git/trees", json=payload)
        return data["sha"]

    def create_commit(
        self,
        owner: str,
        repo: str,
        *,
        message: str,
        tree_sha: str,
        parent_shas: list[str],
    ) -> str:
        payload = {
            "message": message,
            "tree": tree_sha,
            "parents": [s for s in parent_shas if s],
        }
        data = self._request("POST", f"/repos/{owner}/{repo}/git/commits", json=payload)
        return data["sha"]

    def update_ref(
        self,
        owner: str,
        repo: str,
        *,
        ref: str,
        sha: str,
        force: bool = False,
    ) -> dict:
        return self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/heads/{ref}",
            json={"sha": sha, "force": force},
        )

    def create_ref(self, owner: str, repo: str, *, ref: str, sha: str) -> dict:
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{ref}", "sha": sha},
        )

    def ensure_branch(self, owner: str, repo: str, branch: str, default_branch: str) -> tuple[str, str | None]:
        """Return (commit_sha, tree_sha) for the head of ``branch``.

        - If the branch exists, returns its head commit SHA + tree SHA.
        - If the branch doesn't exist but the default branch does, creates
          the branch from the default branch and returns those SHAs.
        - If the repo is completely empty (no branches at all), returns
          ``("", None)`` — the caller must create the initial commit
          with no parents and no base_tree.

        Previously this method had a bug where ``get_ref`` swallowed the
        404 and returned a fake ``{"object": {"sha": ""}}`` — that made
        the ``except`` block dead code, so missing branches were never
        created and downstream calls 404'd with empty-SHAs.
        """
        # 1. Try the requested branch.
        try:
            ref = self.get_ref(owner, repo, f"heads/{branch}")
            commit_sha = ref["object"]["sha"]
            commit = self.get_commit(owner, repo, commit_sha)
            return commit_sha, commit["tree"]["sha"]
        except GitHubError as e:
            if e.status != 404:
                raise

        # 2. Branch missing — try the default branch.
        try:
            default_ref = self.get_ref(owner, repo, f"heads/{default_branch}")
            commit_sha = default_ref["object"]["sha"]
        except GitHubError as e2:
            if e2.status == 404:
                # Repo is completely empty (no commits anywhere).
                # Signal caller to do an initial commit with no parents.
                return "", None
            raise

        # 3. Create the requested branch from the default branch's head.
        commit = self.get_commit(owner, repo, commit_sha)
        self.create_ref(owner, repo, ref=branch, sha=commit_sha)
        return commit_sha, commit["tree"]["sha"]

    # ------------------------------------------------------------------
    # Contents API — for the File Manager page
    # ------------------------------------------------------------------
    def list_contents(self, owner: str, repo: str, path: str = "", *, ref: str | None = None) -> list[dict]:
        """List files and subdirectories at ``path`` in the repo.

        Returns a list of dicts with keys: ``name``, ``path``, ``type``
        (``"file"`` or ``"dir"``), ``size``, ``sha``.
        """
        url = f"/repos/{owner}/{repo}/contents/{path}" if path else f"/repos/{owner}/{repo}/contents"
        params = {}
        if ref:
            params["ref"] = ref
        data = self._request("GET", url, params=params)
        if isinstance(data, list):
            return data
        # Single object returned (path pointed at a file, not a dir)
        return [data]

    def get_file_content(self, owner: str, repo: str, path: str, *, ref: str | None = None) -> tuple[bytes, dict]:
        """Fetch a single file's content. Returns (decoded_bytes, metadata)."""
        url = f"/repos/{owner}/{repo}/contents/{path}"
        params = {}
        if ref:
            params["ref"] = ref
        data = self._request("GET", url, params=params)
        if not isinstance(data, dict):
            raise GitHubError(422, f"Path {path!r} is not a file", {})
        import base64 as _b64
        content_b64 = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64":
            content = _b64.b64decode(content_b64)
        else:
            content = content_b64.encode("utf-8")
        return content, data

    def put_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: bytes,
        *,
        message: str,
        branch: str | None = None,
        sha: str | None = None,
    ) -> dict:
        """Create or update a single file via the Contents API.

        This performs its own commit (one file per commit — a Contents
        API limitation). For multi-file commits, use the
        blob → tree → commit → update_ref pipeline instead.
        """
        import base64 as _b64
        payload: dict = {
            "message": message,
            "content": _b64.b64encode(content).decode("ascii"),
        }
        if branch:
            payload["branch"] = branch
        if sha:
            payload["sha"] = sha  # required when updating an existing file
        return self._request("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=payload)

    def delete_file(
        self,
        owner: str,
        repo: str,
        path: str,
        *,
        message: str,
        sha: str,
        branch: str | None = None,
    ) -> dict:
        """Delete a single file via the Contents API."""
        payload: dict = {"message": message, "sha": sha}
        if branch:
            payload["branch"] = branch
        return self._request("DELETE", f"/repos/{owner}/{repo}/contents/{path}", json=payload)
