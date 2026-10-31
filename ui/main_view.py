"""Main dashboard view.

Layout:
  - Top bar with sign out
  - File picker (zip selection)
  - Repo selector (dropdown fetched from GitHub) + "New repo" button
  - Branch input (default: repo's default_branch)
  - Commit message preview (auto-filled, editable)
  - Live progress log
  - Commit & Push button
"""
from __future__ import annotations

import threading
import time

import flet as ft

from src.commit_pipeline import commit_zip_to_repo
from src.github_client import GitHubClient, GitHubError, RepoInfo
from src.storage import Credentials, Storage
from src.zip_processor import summarize
from ui.theme import (
    ACCENT, BG_SOFT, BG_SOFTER, FG, FG_MUTED, GREEN, RED, YELLOW, status_color,
)


class MainView:
    """Builder for the main dashboard page."""

    def __init__(
        self,
        page: ft.Page,
        *,
        creds: Credentials,
        on_sign_out,
    ):
        self.page = page
        self.creds = creds
        self.storage = Storage(page)
        self.on_sign_out = on_sign_out

        # State
        self.zip_path: str | None = None
        self.repos: list[RepoInfo] = []
        self.selected_repo: RepoInfo | None = None
        self._busy = False

        # File picker — Flet 1.0+ registers FilePicker on page.services
        # (was page.overlay in 0.x). The button uses action=ft.PickFiles(...)
        # instead of on_click → file_picker.pick_files() because the
        # action-based approach is the one that reliably opens the native
        # file dialog on Android and iOS.
        self.file_picker = ft.FilePicker(on_result=self._on_file_picked)

        # UI elements
        self.zip_label = ft.Text(
            "No zip selected yet.",
            color=FG_MUTED,
            italic=True,
            size=13,
        )
        self.pick_button = ft.OutlinedButton(
            "Pick project zip",
            icon=ft.Icons.UPLOAD_FILE,
            action=ft.PickFiles(
                self.file_picker,
                allowed_extensions=["zip"],
                allow_multiple=False,
            ),
            style=ft.ButtonStyle(
                padding=ft.Padding.symmetric(horizontal=20, vertical=12),
            ),
        )

        self.repo_dropdown = ft.Dropdown(
            label="Repository",
            hint_text="Pick a repo from your account",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=15,
        )
        self.refresh_repos_button = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip="Refresh repo list",
            on_click=self._on_refresh_repos,
        )
        self.new_repo_button = ft.OutlinedButton(
            "New repo",
            icon=ft.Icons.ADD,
            on_click=self._open_new_repo_dialog,
        )

        self.branch_field = ft.TextField(
            label="Branch",
            hint_text="leave empty to use repo default",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=15,
        )
        self.commit_msg_field = ft.TextField(
            label="Commit message",
            hint_text="auto-filled with timestamp; edit freely",
            value=self._default_message(),
            multiline=True,
            min_lines=1,
            max_lines=3,
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=15,
        )
        self.commit_button = ft.FilledButton(
            "Commit & Push",
            icon=ft.Icons.CLOUD_UPLOAD,
            on_click=self._on_commit,
            disabled=True,
            style=ft.ButtonStyle(
                bgcolor=ACCENT,
                color="#ffffff",
                padding=ft.Padding.symmetric(horizontal=24, vertical=14),
            ),
        )

        self.log_box = ft.Column(
            controls=[],
            scroll=ft.ScrollMode.AUTO,
            spacing=2,
            expand=True,
        )

        self.progress_bar = ft.ProgressBar(
            value=0,
            visible=False,
            color=ACCENT,
            bgcolor=BG_SOFTER,
        )

        self.status_text = ft.Text("", color=FG_MUTED, size=13)

        self.sign_out_button = ft.TextButton(
            "Sign out",
            icon=ft.Icons.LOGOUT,
            on_click=self._on_sign_out,
        )

    # ------------------------------------------------------------------
    def build(self) -> ft.Control:
        # Flet 1.0+: FilePicker must be registered on page.services
        # (was page.overlay in 0.x — using overlay causes "Unknown
        # control: FilePicker" on Android).
        if self.file_picker not in self.page.services:
            self.page.services.append(self.file_picker)

        top = ft.Row(
            controls=[
                ft.Icon(ft.Icons.CLOUD_UPLOAD, color=ACCENT, size=24),
                ft.Text(
                    "GitHub Auto Committer",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=FG,
                ),
                ft.Container(expand=True),
                ft.Text(self.creds.username, color=FG_MUTED, size=13),
                self.sign_out_button,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        zip_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("1. Project zip", size=15, weight=ft.FontWeight.BOLD, color=FG),
                    self.zip_label,
                    self.pick_button,
                ],
                spacing=10,
            ),
            padding=ft.Padding.all(16),
            bgcolor=BG_SOFT,
            border_radius=12,
            border=ft.border.BorderSide(1, BG_SOFTER),
        )

        repo_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("2. Target repository", size=15, weight=ft.FontWeight.BOLD, color=FG),
                    ft.Row(
                        controls=[
                            ft.Container(expand=True, content=self.repo_dropdown),
                            self.refresh_repos_button,
                            self.new_repo_button,
                        ],
                        spacing=8,
                    ),
                ],
                spacing=10,
            ),
            padding=ft.Padding.all(16),
            bgcolor=BG_SOFT,
            border_radius=12,
            border=ft.border.BorderSide(1, BG_SOFTER),
        )

        commit_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("3. Commit details", size=15, weight=ft.FontWeight.BOLD, color=FG),
                    self.branch_field,
                    self.commit_msg_field,
                    self.progress_bar,
                    self.status_text,
                    self.commit_button,
                ],
                spacing=10,
            ),
            padding=ft.Padding.all(16),
            bgcolor=BG_SOFT,
            border_radius=12,
            border=ft.border.BorderSide(1, BG_SOFTER),
        )

        log_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Live progress", size=15, weight=ft.FontWeight.BOLD, color=FG),
                    ft.Container(
                        content=self.log_box,
                        padding=ft.Padding.all(12),
                        bgcolor=BG_SOFT,
                        border_radius=8,
                        expand=True,
                    ),
                ],
                spacing=10,
                expand=True,
            ),
            padding=ft.Padding.all(16),
            bgcolor=BG_SOFT,
            border_radius=12,
            border=ft.border.BorderSide(1, BG_SOFTER),
            expand=True,
        )

        return ft.Column(
            controls=[
                top,
                zip_card,
                repo_card,
                commit_card,
                log_card,
            ],
            spacing=14,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _default_message() -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        return f"chore: auto-commit export {ts}"

    # ------------------------------------------------------------------
    # Repo loading
    # ------------------------------------------------------------------
    def load_repos_async(self) -> None:
        """Kick off a background thread to fetch the user's repo list."""
        self.repo_dropdown.options = [ft.dropdown.Option("loading", "Loading...")]
        self.repo_dropdown.disabled = True
        self.page.update()

        def worker():
            try:
                with GitHubClient(self.creds.username, self.creds.token, timeout=20.0) as gh:
                    repos = gh.list_repos()
                self.repos = repos
                self.repo_dropdown.options = [
                    ft.dropdown.Option(r.full_name, f"{r.full_name}{' (private)' if r.private else ''}")
                    for r in repos
                ]
                self.repo_dropdown.disabled = False
                if not repos:
                    self._set_status("No repositories found on your account.", YELLOW)
            except GitHubError as e:
                self.repo_dropdown.options = []
                self.repo_dropdown.disabled = False
                self._set_status(f"Failed to load repos: {e.message}", RED)
            except Exception as e:
                self.repo_dropdown.options = []
                self.repo_dropdown.disabled = False
                self._set_status(f"Network error: {e}", RED)
            self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh_repos(self, _e) -> None:
        self.load_repos_async()

    # ------------------------------------------------------------------
    # New repo dialog
    # ------------------------------------------------------------------
    def _open_new_repo_dialog(self, _e) -> None:
        name_field = ft.TextField(
            label="New repo name",
            autofocus=True,
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
        )
        desc_field = ft.TextField(
            label="Description (optional)",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
        )
        private_switch = ft.Switch(label="Private", value=True, active_color=ACCENT)
        status_text = ft.Text("", color=RED, size=12, visible=False)
        progress = ft.ProgressRing(visible=False, color=ACCENT)

        def close(_e):
            dlg.open = False
            self.page.update()

        def create(_e):
            name = (name_field.value or "").strip()
            if not name:
                status_text.value = "Repo name is required."
                status_text.visible = True
                self.page.update()
                return
            status_text.visible = False
            progress.visible = True
            self.page.update()

            def worker():
                try:
                    with GitHubClient(self.creds.username, self.creds.token, timeout=30.0) as gh:
                        repo = gh.create_repo(
                            name,
                            private=private_switch.value,
                            description=(desc_field.value or ""),
                            auto_init=True,
                        )
                    # Prepend new repo
                    self.repos.insert(0, repo)
                    self.repo_dropdown.options.insert(
                        0,
                        ft.dropdown.Option(repo.full_name, f"{repo.full_name} (new)"),
                    )
                    self.repo_dropdown.value = repo.full_name
                    self.selected_repo = repo
                    self.branch_field.value = repo.default_branch
                    progress.visible = False
                    dlg.open = False
                    self._set_status(f"Created {repo.full_name}.", GREEN)
                except GitHubError as e:
                    progress.visible = False
                    status_text.value = f"GitHub: {e.message}"
                    status_text.visible = True
                except Exception as e:
                    progress.visible = False
                    status_text.value = f"Error: {e}"
                    status_text.visible = True
                self.page.update()

            threading.Thread(target=worker, daemon=True).start()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Create new repository", color=FG),
            content=ft.Column(
                controls=[
                    name_field,
                    desc_field,
                    private_switch,
                    status_text,
                    ft.Row([progress], alignment=ft.MainAxisAlignment.CENTER),
                ],
                spacing=10,
                tight=True,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=close),
                ft.FilledButton(
                    "Create",
                    on_click=create,
                    style=ft.ButtonStyle(bgcolor=ACCENT, color="#ffffff"),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        # Flet 1.0+ uses page.overlay for dialogs (page.dialog was removed).
        if dlg not in self.page.overlay:
            self.page.overlay.append(dlg)
        dlg.open = True
        self.page.update()

    # ------------------------------------------------------------------
    # File picking
    # ------------------------------------------------------------------
    def _on_file_picked(self, e: ft.FilePickerResultEvent) -> None:
        if not e.files:
            return
        picked = e.files[0]
        self.zip_path = picked.path
        try:
            summary = summarize_from_zip(self.zip_path)
            self.zip_label.value = (
                f"{picked.name}  ·  {summary['files']} files, "
                f"{summary['bytes'] / 1024:.1f} KB"
            )
            self.zip_label.color = FG
            self.zip_label.italic = False
        except Exception:
            self.zip_label.value = picked.name
        self._maybe_enable_commit()
        self.page.update()

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------
    def _maybe_enable_commit(self) -> None:
        ready = bool(self.zip_path) and self.selected_repo is not None and not self._busy
        self.commit_button.disabled = not ready
        self.page.update()

    def _set_status(self, msg: str, color: str = FG_MUTED) -> None:
        self.status_text.value = msg
        self.status_text.color = color
        self.page.update()

    def _append_log(self, phase: str, msg: str, color: str = FG_MUTED) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime())
        line = ft.Text(
            f"[{ts}] {phase}: {msg}",
            size=12,
            color=color,
            selectable=True,
            no_wrap=False,
            expand=True,
        )
        self.log_box.controls.append(line)
        if len(self.log_box.controls) > 200:
            self.log_box.controls = self.log_box.controls[-200:]
        self.page.update()

    def _on_commit(self, _e) -> None:
        if self._busy:
            return
        if not self.zip_path:
            self._set_status("Pick a zip first.", YELLOW)
            return
        if not self.selected_repo:
            self._set_status("Pick or create a repository first.", YELLOW)
            return

        self._busy = True
        self.commit_button.disabled = True
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.log_box.controls.clear()
        self.page.update()

        zip_path = self.zip_path
        repo = self.selected_repo
        branch_input = (self.branch_field.value or "").strip() or None
        msg = self.commit_msg_field.value or self._default_message()

        def progress_cb(phase: str, message: str, pct: float) -> None:
            self.progress_bar.value = pct
            color = (
                RED if phase == "error"
                else GREEN if phase == "done"
                else FG_MUTED
            )
            self._append_log(phase, message, color)

        def worker():
            try:
                with GitHubClient(self.creds.username, self.creds.token, timeout=60.0) as gh:
                    result = commit_zip_to_repo(
                        client=gh,
                        storage=self.storage,
                        zip_path=zip_path,
                        repo=repo,
                        branch=branch_input,
                        message=msg,
                        progress=progress_cb,
                    )
                if result.success:
                    self._set_status(
                        f"Done — {result.file_count} files pushed.",
                        GREEN,
                    )
                else:
                    self._set_status(f"Failed: {result.error}", RED)
            except Exception as e:
                self._set_status(f"Unexpected error: {e}", RED)
                self._append_log("error", f"{e}", RED)
            finally:
                self._busy = False
                self.progress_bar.value = 1.0
                self._maybe_enable_commit()
                # Refresh commit message to a fresh timestamp
                self.commit_msg_field.value = self._default_message()
                self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    def _on_sign_out(self, _e) -> None:
        if self._busy:
            self._set_status("Wait for the current commit to finish.", YELLOW)
            return
        self.storage.clear_credentials()
        self.on_sign_out()

    # ------------------------------------------------------------------
    # Dropdown change handler (wired by main.py)
    # ------------------------------------------------------------------
    def on_repo_change(self, _e) -> None:
        full = self.repo_dropdown.value
        self.selected_repo = next(
            (r for r in self.repos if r.full_name == full), None
        )
        if self.selected_repo:
            self.branch_field.value = self.selected_repo.default_branch
        self._maybe_enable_commit()


def summarize_from_zip(zip_path: str) -> dict:
    """Quick stats for the UI log. Imports lazily to avoid circulars."""
    from src.zip_processor import extract_zip, summarize, cleanup
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ghc_summary_")
    try:
        root = extract_zip(zip_path, tmp)
        return summarize(root)
    finally:
        cleanup(tmp)
