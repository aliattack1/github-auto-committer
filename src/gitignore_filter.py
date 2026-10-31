""".gitignore-aware file filter.

Layered matcher:
  1. Built-in exclude defaults (``.git/``, ``node_modules/``, ``__pycache__``,
     ``.venv/``, ``build/``, ``dist/``, OS junk) — always applied.
  2. ``.gitignore`` files discovered while walking the tree. Each
     ``.gitignore`` applies to its containing directory and below (git
     semantics). Patterns are accumulated with ``pathspec``.

Files matched by any negative pattern (``!path``) are re-included.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

import pathspec

# Patterns always excluded, even with no .gitignore present. These mirror
# what seasoned devs almost always mean to ignore.
BUILTIN_EXCLUDES: list[str] = [
    # VCS internal state only — .gitignore/.gitattributes/.gitmodules are
    # project files that SHOULD be committed (e.g. so the app can update
    # its own repo via Commit & Push).
    ".git/",
    ".hg/",
    ".svn/",
    # Python
    "__pycache__/",
    "*.py[cod]",
    "*$py.class",
    "*.egg-info/",
    ".eggs/",
    "*.egg",
    "build/",
    "dist/",
    "develop-eggs.py",
    ".tox/",
    ".coverage",
    ".coverage.*",
    "htmlcov/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv/",
    "venv/",
    "env/",
    "ENV/",
    # JS/TS
    "node_modules/",
    ".next/",
    ".nuxt/",
    ".svelte-kit/",
    "bower_components/",
    ".parcel-cache/",
    "coverage/",
    "*.log",
    "npm-debug.log*",
    "yarn-debug.log*",
    "yarn-error.log*",
    # OS junk
    ".DS_Store",
    "Thumbs.db",
    "ehthumbs.db",
    "Desktop.ini",
    "$RECYCLE.BIN/",
    # IDE
    ".idea/",
    "*.iml",
    ".vscode/",
    "*.swp",
    "*.swo",
    "*~",
    # Common build outputs
    "*.apk",
    "*.aab",
    "*.ipa",
    "*.dex",
    "*.class",
    "bin/",
    "obj/",
    "target/",
]


@dataclass(slots=True)
class FilteredFile:
    """A file that survived the filter and is ready to be committed."""

    abs_path_str: str
    repo_path: str  # path relative to the extracted project root
    is_binary: bool


class GitIgnoreFilter:
    """Stateful filter applied to an extracted project tree.

    Call :py:meth:`scan` once on the project root, then iterate the
    returned list of :class:`FilteredFile` objects. Each ``.gitignore``
    discovered while walking is merged into the spec, preserving git's
    "later patterns win" rule.
    """

    def __init__(self, project_root: str, *, extra_excludes: list[str] | None = None):
        self.project_root = PurePosixPath(project_root)
        self._specs: list[tuple[PurePosixPath, pathspec.PathSpec]] = [
            (PurePosixPath("."), pathspec.PathSpec.from_lines("gitwildmatch", BUILTIN_EXCLUDES))
        ]
        if extra_excludes:
            self._specs.append(
                (PurePosixPath("."), pathspec.PathSpec.from_lines("gitwildmatch", extra_excludes))
            )

    def _is_ignored(self, rel_path: PurePosixPath) -> bool:
        """Check a single relative path against every layer.

        Walks layers in order; the *last* match wins, mirroring git.
        """
        ignored = False
        rel_str = rel_path.as_posix()
        # Match against both the file path and the trailing-slash dir form
        # so patterns like ``foo/`` correctly catch directory contents.
        rel_as_dir = rel_str + "/"
        for _root, spec in self._specs:
            if spec.match_file(rel_str):
                ignored = True
            elif spec.match_file(rel_as_dir):
                ignored = True
        return ignored

    def _load_gitignore(self, abs_path: PurePosixPath, root_for_rel: PurePosixPath) -> None:
        try:
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return
        spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
        self._specs.append((root_for_rel, spec))

    def scan(self) -> list[FilteredFile]:
        """Walk the tree and return only the files that should be committed."""
        import os

        results: list[FilteredFile] = []
        root_str = str(self.project_root)

        for dirpath, dirnames, filenames in os.walk(root_str, topdown=True):
            current_abs = PurePosixPath(dirpath)
            rel_dir = PurePosixPath(current_abs.relative_to(root_str))

            # If this dir contains a .gitignore, register it BEFORE deciding
            # on this dir's files (git processes .gitignore when entering
            # the directory). The .gitignore file itself is INCLUDED in the
            # commit — it's a project file, not something to drop. This
            # matters for self-update: when the app commits its own source
            # to its GitHub repo, the .gitignore needs to come along.
            if ".gitignore" in filenames:
                gi_abs = current_abs / ".gitignore"
                self._load_gitignore(gi_abs, rel_dir)

            # Prune ignored subdirectories in-place so os.walk doesn't
            # descend into them (huge speed win on node_modules etc.).
            kept_dirs: list[str] = []
            for d in dirnames:
                d_rel = rel_dir / d if rel_dir != PurePosixPath(".") else PurePosixPath(d)
                if self._is_ignored(d_rel):
                    continue
                kept_dirs.append(d)
            dirnames[:] = kept_dirs

            # Now per-file decision
            for fname in filenames:
                f_rel = (
                    rel_dir / fname
                    if rel_dir != PurePosixPath(".")
                    else PurePosixPath(fname)
                )
                if self._is_ignored(f_rel):
                    continue
                abs_path_str = str(current_abs / fname)
                is_binary = _looks_binary(abs_path_str)
                results.append(
                    FilteredFile(
                        abs_path_str=abs_path_str,
                        repo_path=f_rel.as_posix(),
                        is_binary=is_binary,
                    )
                )

        return results


def _looks_binary(path: str, *, sample: int = 2048) -> bool:
    """Heuristic: if the file contains a NUL byte in the first chunk,
    treat it as binary. Otherwise text."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample)
    except OSError:
        return True
    return b"\x00" in chunk


def filter_files(project_root: str, *, extra_excludes: list[str] | None = None) -> list[FilteredFile]:
    """Convenience wrapper: build a filter and return its scan result."""
    return GitIgnoreFilter(project_root, extra_excludes=extra_excludes).scan()
