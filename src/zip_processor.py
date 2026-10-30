"""Zip extraction with safe path handling.

Zip-slip protection: refuse to extract any entry whose resolved path
escapes the destination root.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path


class ZipExtractionError(Exception):
    """Raised when a zip entry is unsafe to extract."""


def extract_zip(zip_path: str, dest_dir: str) -> str:
    """Extract ``zip_path`` into ``dest_dir`` and return the project root.

    The project root is determined by the layout of the archive:

      * If the top-level entries are all directories, return the single
        one (so ``foo/`` becomes the root). If there are multiple, the
        dest_dir itself is the root.
      * Otherwise, dest_dir is the root.
    """
    os.makedirs(dest_dir, exist_ok=True)
    abs_dest = os.path.abspath(dest_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        for name in names:
            target = os.path.normpath(os.path.join(abs_dest, name))
            # Zip-slip check
            if not target.startswith(abs_dest + os.sep) and target != abs_dest:
                raise ZipExtractionError(f"Unsafe zip entry refused: {name}")
        zf.extractall(abs_dest)

    # Determine root: a single top-level dir, or dest_dir itself.
    entries = [
        n
        for n in os.listdir(abs_dest)
        if not n.startswith(".") or n not in (".DS_Store", "Thumbs.db")
    ]
    if len(entries) == 1:
        only = os.path.join(abs_dest, entries[0])
        if os.path.isdir(only):
            return only
    return abs_dest


def cleanup(dest_dir: str) -> None:
    """Best-effort removal of a temp extraction directory."""
    try:
        import shutil

        shutil.rmtree(dest_dir, ignore_errors=True)
    except Exception:
        pass


def summarize(project_root: str) -> dict:
    """Quick stats for the UI log."""
    total_files = 0
    total_bytes = 0
    for dirpath, _dirnames, filenames in os.walk(project_root):
        for f in filenames:
            try:
                p = os.path.join(dirpath, f)
                total_files += 1
                total_bytes += os.path.getsize(p)
            except OSError:
                continue
    return {"files": total_files, "bytes": total_bytes}


# Convenience: a single-file context manager so callers don't have to
# remember to cleanup.
from contextlib import contextmanager
import tempfile


@contextmanager
def temp_extract(zip_path: str):
    """Yield the project root path; cleanup on exit."""
    tmp = tempfile.mkdtemp(prefix="ghcommitter_")
    try:
        root = extract_zip(zip_path, tmp)
        yield root
    finally:
        cleanup(tmp)


# Re-export Path for callers who prefer it
__all__ = [
    "ZipExtractionError",
    "extract_zip",
    "cleanup",
    "summarize",
    "temp_extract",
    "Path",
]
