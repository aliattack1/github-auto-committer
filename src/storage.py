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

# Possible Android package ids for this app. Flet's build template
# joins ``org_name`` + the project name (with hyphens → underscores).
# In practice the actual package on the user's device has been observed
# as ``com.flet.github_auto_committer`` (Flet's default org_name when
# [tool.flet].org_name isn't applied by the build). We list several
# variants so the static fallback can find the right sandbox even if
# /proc/self/cmdline detection fails.
_PACKAGE_VARIANTS = (
    # Observed in the wild (Flet default org_name = "com.flet"):
    "com.flet.github_auto_committer",
    # If [tool.flet].org_name = "com.zai" IS applied:
    "com.zai.githubautocommitter",
    "com.zai.github_auto_committer",
    # Other plausible normalizations:
    "com.flet.githubautocommitter",
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

    On Android the app's **files** dir is the right place (persistent,
    not auto-cleaned by the OS). The **cache** dir (which is what
    ``Path.home()`` and ``tempfile.gettempdir()`` return on Android via
    serious_python) is NOT safe — Android can wipe it at any time,
    mid-operation, which caused the ``FileNotFoundError`` bugs on
    ``history.json`` and on extracted project files.

    Tries candidates in order; first one that's writable wins. The
    final fallback is the system temp directory (not persistent across
    app reinstalls but at least the app runs).
    """
    candidates: list[Path] = []

    # 1. Android app-private sandbox — ALWAYS try these, even if
    #    _is_android() is unsure. On desktop these paths don't exist,
    #    so _is_writable() returns False and they're skipped. On Android
    #    the app's files dir is always writable by the app.
    detected_pkg = _detect_android_package()
    android_pkgs: list[str] = []
    if detected_pkg:
        android_pkgs.append(detected_pkg)
    android_pkgs.extend(_PACKAGE_VARIANTS)

    for pkg in android_pkgs:
        # /data/data/<pkg>/files/ — private, persistent, OS-encrypted.
        candidates.append(Path("/data/data") / pkg / "files" / ".ghcommitter")
        # /data/user/0/<pkg>/files/ — same path via multi-user symlink
        candidates.append(Path("/data/user/0") / pkg / "files" / ".ghcommitter")
        # External storage app-specific dir — also persistent, on SD card
        candidates.append(
            Path("/storage/emulated/0/Android/data") / pkg / "files" / ".ghcommitter"
        )

    # 2. Desktop: $HOME/.ghcommitter. On Android $HOME is the cache dir,
    #    which we DO NOT want — so skip $HOME if it looks like a cache path.
    home = os.path.expanduser("~")
    is_cache_path = home and ("/cache" in home or "/tmp" in home)
    if home and home not in ("", "/", "/data", "/root", "/var/empty") and not is_cache_path:
        candidates.append(Path(home) / ".ghcommitter")

    # 3. macOS Application Support
    if sys.platform == "darwin" and home and not is_cache_path:
        candidates.append(
            Path(home) / "Library" / "Application Support" / "ghcommitter"
        )

    # 4. Linux XDG
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg and "/cache" not in xdg and "/tmp" not in xdg:
        candidates.append(Path(xdg) / "ghcommitter")

    # 5. Last resort — system temp dir. On Android this IS the cache dir
    #    (bad, but better than nothing — at least the app runs).
    candidates.append(Path(tempfile.gettempdir()) / ".ghcommitter")

    for cand in candidates:
        if _is_writable(cand):
            return cand

    # Truly last resort — should never reach here because the temp dir
    # candidate above is always writable on a sane system.
    fallback = Path(tempfile.gettempdir()) / ".ghcommitter"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def diagnose_data_dir() -> dict:
    """Return a diagnostic dict showing which candidate paths were tried.

    Useful for debugging on-device: shows every candidate, whether it
    was writable, and which one was ultimately chosen.
    """
    detected_pkg = _detect_android_package()
    android_pkgs: list[str] = []
    if detected_pkg:
        android_pkgs.append(detected_pkg)
    android_pkgs.extend(_PACKAGE_VARIANTS)

    candidates: list[Path] = []
    for pkg in android_pkgs:
        candidates.append(Path("/data/data") / pkg / "files" / ".ghcommitter")
        candidates.append(Path("/data/user/0") / pkg / "files" / ".ghcommitter")
        candidates.append(Path("/storage/emulated/0/Android/data") / pkg / "files" / ".ghcommitter")

    home = os.path.expanduser("~")
    is_cache_path = home and ("/cache" in home or "/tmp" in home)
    if home and home not in ("", "/", "/data", "/root", "/var/empty") and not is_cache_path:
        candidates.append(Path(home) / ".ghcommitter")

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg and "/cache" not in xdg and "/tmp" not in xdg:
        candidates.append(Path(xdg) / "ghcommitter")

    candidates.append(Path(tempfile.gettempdir()) / ".ghcommitter")

    results = []
    for cand in candidates:
        writable = _is_writable(cand)
        exists = cand.exists()
        results.append({
            "path": str(cand),
            "exists": exists,
            "writable": writable,
        })

    chosen = _resolve_data_dir()
    return {
        "is_android": _is_android(),
        "detected_package": detected_pkg,
        "home": home,
        "is_cache_path": bool(is_cache_path),
        "candidates": results,
        "chosen": str(chosen),
        "chosen_is_cache": "/cache/" in str(chosen) or "/tmp/" in str(chosen),
    }


def get_tmp_dir() -> Path:
    """Return a writable directory for temporary file extraction.

    On Android this MUST NOT be the system temp / cache dir — Android
    can clean up the cache mid-operation, causing FileNotFoundError on
    files that were just extracted. We use a ``tmp/`` subdirectory
    inside the persistent data dir instead.

    On desktop we use the system temp dir (which is fine — it's not
    auto-cleaned mid-operation there).
    """
    if _is_android():
        base = _resolve_data_dir()
        tmp = base / "tmp"
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            return tmp
        except (OSError, PermissionError):
            pass
    # Desktop fallback
    return Path(tempfile.gettempdir())


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
