"""Persistent storage for credentials and commit history.

Implementation note:
    Flet 0.23 exposed ``page.client_storage`` (a sync key/value store).
    Flet 1.0 removed it in favor of an async ``SharedPreferences``
    Service that has to be attached to the page. To stay simple and
    cross-platform without rewriting every caller as async, we persist
    to a JSON file in a writable directory.

    On desktop this is ``$HOME/.ghcommitter/``. On Android
    ``Path.home()`` returns the broken ``/data`` (root-owned), so we
    probe a list of candidate paths and use the first writable one —
    typically ``/data/data/<package>/files/.ghcommitter`` (the app's
    private sandbox, already OS-encrypted and inaccessible to other
    apps). The ``page`` argument is kept for forward compatibility.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

CREDS_KEY = "ghcommitter.creds.v1"
HISTORY_KEY = "ghcommitter.history.v1"
MAX_HISTORY = 50

# Package name constructed from pyproject.toml:
#   [tool.flet] org_name = "com.zai"
#   [project] name = "github-auto-committer"
# Flet's build template joins them and lowercases / strips hyphens for
# the Android package id. We try several variants because the exact
# normalization depends on the Flet/cookiecutter version.
_PACKAGE_VARIANTS = (
    "com.zai.githubautocommitter",
    "com.zai.github_auto_committer",
    "com.zai.githubautocommitter.githubautocommitter",
)


def _is_android() -> bool:
    """Heuristic: are we running inside an Android app sandbox?"""
    if sys.platform != "linux":
        return False
    if os.path.exists("/system/build.prop"):
        return True
    if os.environ.get("ANDROID_ROOT"):
        return True
    # serious_python extracts main.py to /tmp/serious_python_tempXXX/
    # at startup — a strong signal that we're inside an Android Flet app.
    try:
        main_file = sys.argv[0] or ""
        if "serious_python_temp" in main_file:
            return True
    except Exception:
        pass
    return False


def _detect_android_package() -> str | None:
    """Read /proc/self/cmdline to find our own package name on Android.

    On Android the main process is named after the package, so the
    first non-empty token of /proc/self/cmdline is usually the package
    id (e.g. ``com.zai.githubautocommitter``). Falls back to ``None``
    on non-Android or if anything goes wrong.
    """
    try:
        with open("/proc/self/cmdline", "rb") as f:
            raw = f.read()
        parts = [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]
        for p in parts:
            # A package id looks like com.example.foo — dots, no slashes,
            # reasonably short.
            if "." in p and "/" not in p and len(p) <= 100 and not p.startswith("-"):
                return p
    except Exception:
        pass
    return None


def _is_writable(p: Path) -> bool:
    """Can we actually create files in directory ``p``?"""
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_probe"
        probe.touch()
        probe.unlink()
        return True
    except (OSError, PermissionError):
        return False


def _resolve_data_dir() -> Path:
    """Return a writable, ideally-persistent directory for app data.

    Tries candidates in order; first one that's writable wins. The
    final fallback is the system temp directory (not persistent across
    app reinstalls but at least the app runs).
    """
    candidates: list[Path] = []

    # 1. $HOME/.ghcommitter — works on desktop. Skip broken Android $HOME.
    home = os.path.expanduser("~")
    if home and home not in ("", "/", "/data", "/root", "/var/empty"):
        candidates.append(Path(home) / ".ghcommitter")

    # 2. macOS Application Support
    if sys.platform == "darwin":
        candidates.append(
            Path(home) / "Library" / "Application Support" / "ghcommitter"
        )

    # 3. Linux XDG
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(Path(xdg) / "ghcommitter")

    # 4. Android app-private sandbox. Try the dynamically-detected
    #    package name first, then a list of static variants as a
    #    fallback in case /proc/self/cmdline doesn't yield the id.
    if _is_android():
        detected_pkg = _detect_android_package()
        android_pkgs: list[str] = []
        if detected_pkg:
            android_pkgs.append(detected_pkg)
        android_pkgs.extend(_PACKAGE_VARIANTS)

        for pkg in android_pkgs:
            candidates.append(Path("/data/data") / pkg / "files" / ".ghcommitter")
            candidates.append(
                Path("/storage/emulated/0/Android/data") / pkg / "files" / ".ghcommitter"
            )

    # 5. Last resort — system temp dir (not persistent, but the app will run).
    candidates.append(Path(tempfile.gettempdir()) / ".ghcommitter")

    for cand in candidates:
        if _is_writable(cand):
            return cand

    # Truly last resort — should never reach here because the temp dir
    # candidate above is always writable on a sane system.
    fallback = Path(tempfile.gettempdir()) / ".ghcommitter"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


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
        self._dir = _resolve_data_dir()
        self._creds_file = self._dir / "creds.json"
        self._history_file = self._dir / "history.json"

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        """Path to the directory used for persistence (read-only info)."""
        return self._dir

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
        # HistoryEntry is a slotted dataclass — no __dict__, so use asdict().
        if isinstance(entry, dict):
            payload = entry
        else:
            payload = asdict(entry)
        items.insert(0, payload)
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
