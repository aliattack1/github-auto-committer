"""Persistent storage for credentials and commit history.

Implementation note:
    Flet 0.23 exposed ``page.client_storage`` (a sync key/value store).
    Flet 1.0 removed it in favor of an async ``SharedPreferences``
    Service that has to be attached to the page. To stay simple and
    cross-platform without rewriting every caller as async, we persist
    to a JSON file under ``$HOME/.ghcommitter/`` (on Android this
    resolves to the app's private files sandbox, which is already
    OS-encrypted and inaccessible to other apps). The page argument
    is kept on the API for forward compatibility.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

CREDS_KEY = "ghcommitter.creds.v1"
HISTORY_KEY = "ghcommitter.history.v1"
MAX_HISTORY = 50


@dataclass(slots=True)
class Credentials:
    username: str
    token: str  # PAT — plays the role of "password"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Credentials":
        return cls(username=d.get("username", ""), token=d.get("token", ""))


@dataclass(slots=True)
class HistoryEntry:
    timestamp: float
    repo_full_name: str
    branch: str
    message: str
    file_count: int
    status: str  # "ok" | "error"
    error: str = ""
    commit_sha: str = ""


class Storage:
    """JSON-file-backed persistent storage."""

    def __init__(self, page: Any | None = None):
        # The page argument is accepted for forward compatibility (e.g.
        # if we later switch to Flet's SharedPreferences Service).
        self.page = page
        self._dir = Path.home() / ".ghcommitter"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._creds_file = self._dir / "creds.json"
        self._history_file = self._dir / "history.json"

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------
    def save_credentials(self, creds: Credentials) -> None:
        self._creds_file.write_text(json.dumps(creds.to_dict()))

    def load_credentials(self) -> Credentials | None:
        if not self._creds_file.exists():
            return None
        try:
            return Credentials.from_dict(json.loads(self._creds_file.read_text()))
        except Exception:
            return None

    def clear_credentials(self) -> None:
        if self._creds_file.exists():
            try:
                self._creds_file.unlink()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def _load_history_raw(self) -> list[dict]:
        if not self._history_file.exists():
            return []
        try:
            data = json.loads(self._history_file.read_text())
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_history_raw(self, items: list[dict]) -> None:
        self._history_file.write_text(json.dumps(items))

    def add_history(self, entry: HistoryEntry) -> None:
        items = self._load_history_raw()
        items.insert(0, entry.__dict__ if not isinstance(entry, dict) else entry)
        if len(items) > MAX_HISTORY:
            items = items[:MAX_HISTORY]
        self._save_history_raw(items)

    def list_history(self) -> list[HistoryEntry]:
        items = self._load_history_raw()
        out: list[HistoryEntry] = []
        for it in items:
            out.append(
                HistoryEntry(
                    timestamp=it.get("timestamp", time.time()),
                    repo_full_name=it.get("repo_full_name", ""),
                    branch=it.get("branch", "main"),
                    message=it.get("message", ""),
                    file_count=it.get("file_count", 0),
                    status=it.get("status", "ok"),
                    error=it.get("error", ""),
                    commit_sha=it.get("commit_sha", ""),
                )
            )
        return out

    def clear_history(self) -> None:
        self._save_history_raw([])
