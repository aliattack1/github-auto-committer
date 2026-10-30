"""Package marker for src/ — exposes the public API."""
from .commit_pipeline import commit_zip_to_repo, CommitResult
from .github_client import GitHubClient, GitHubError, RepoInfo
from .gitignore_filter import filter_files, FilteredFile
from .storage import Credentials, HistoryEntry, Storage
from .zip_processor import extract_zip, temp_extract

__all__ = [
    "commit_zip_to_repo",
    "CommitResult",
    "GitHubClient",
    "GitHubError",
    "RepoInfo",
    "filter_files",
    "FilteredFile",
    "Credentials",
    "HistoryEntry",
    "Storage",
    "extract_zip",
    "temp_extract",
]
