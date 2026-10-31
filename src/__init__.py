"""Package marker for src/ — exposes the public API."""
from .commit_pipeline import commit_zip_to_repo, CommitResult
from .github_client import GitHubClient, GitHubError, RepoInfo
from .gitignore_filter import filter_files, FilteredFile
from .storage import Credentials, HistoryEntry, Storage
from .zip_processor import extract_zip, temp_extract
from .file_manager import (
    RepoEntry,
    list_repo_path,
    upload_file_to_repo,
    create_folder_in_repo,
    create_text_file_in_repo,
    copy_file_between_repos,
    delete_file_in_repo,
    read_text_file_in_repo,
)

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
    "RepoEntry",
    "list_repo_path",
    "upload_file_to_repo",
    "create_folder_in_repo",
    "create_text_file_in_repo",
    "copy_file_between_repos",
    "delete_file_in_repo",
    "read_text_file_in_repo",
]
