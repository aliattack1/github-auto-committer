"""File Manager page — browse repos, upload/create/delete/copy files.

Second page of the app, accessed via the bottom NavigationBar.

Layout:
  - Top: repo selector dropdown + refresh
  - Path breadcrumb (clickable to navigate up)
  - File/folder list (clickable folders to descend, files to select)
  - Action buttons: Upload, New folder, New file, Copy from repo, Delete
  - Live progress log
"""

from __future__ import annotations

import threading
import time

import flet as ft

from src.file_manager import (
    RepoEntry,
    list_repo_path,
    upload_file_to_repo,
    create_folder_in_repo,
    create_text_file_in_repo,
    copy_file_between_repos,
    delete_file_in_repo,
    read_text_file_in_repo,
)
from src.github_client import GitHubClient, GitHubError, RepoInfo
from src.storage import Credentials, Storage
from ui.theme import (
    ACCENT, BG_SOFT, BG_SOFTER, FG, FG_MUTED, GREEN, RED, YELLOW,
)


class FileManagerView:
    """Builder for the File Manager page."""

    def __init__(self, page: ft.Page, *, creds: Credentials):
        self.page = page
        self.creds = creds
        self.storage = Storage(page)

        # State
        self.repos: list[RepoInfo] = []
        self.selected_repo: RepoInfo | None = None
        self.current_path: str = ""  # path within the repo
        self.entries: list[RepoEntry] = []
        self.selected_entry: RepoEntry | None = None
        self._busy = False

        # File picker for uploads
        self.file_picker = ft.FilePicker(on_result=self._on_file_picked)

        # UI elements
        self.repo_dropdown = ft.Dropdown(
            label="Repository",
            hint_text="Pick a repo to browse",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=15,
            expand=True,
        )
        self.refresh_button = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip="Refresh repos + files",
            on_click=self._on_refresh,
        )

        self.breadcrumb = ft.Row(
            controls=[ft.Text("Path: /", size=13, color=FG_MUTED, selectable=True)],
            scroll=ft.ScrollMode.AUTO,
        )

        self.file_list = ft.ListView(
            expand=True,
            spacing=2,
            padding=ft.Padding.all(8),
        )

        self.action_bar = ft.Row(
            controls=[
                ft.FilledButton("Upload", icon=ft.Icons.UPLOAD_FILE, on_click=self._on_upload_click),
                ft.OutlinedButton("New folder", icon=ft.Icons.CREATE_NEW_FOLDER, on_click=self._on_new_folder_click),
                ft.OutlinedButton("New file", icon=ft.Icons.NOTE_ADD, on_click=self._on_new_file_click),
                ft.OutlinedButton("Copy from repo", icon=ft.Icons.DRIVE_FILE_MOVE, on_click=self._on_copy_click),
                ft.OutlinedButton("Delete", icon=ft.Icons.DELETE, on_click=self._on_delete_click),
            ],
            wrap=True,
            spacing=8,
        )

        self.status_text = ft.Text("Pick a repository to start.", color=FG_MUTED, size=13)
        self.log_box = ft.Column(
            controls=[],
            scroll=ft.ScrollMode.AUTO,
            spacing=2,
            height=160,
        )

    # ------------------------------------------------------------------
    def build(self) -> ft.Control:
        # Register FilePicker on page.services (Flet 1.0+)
        if self.file_picker not in self.page.services:
            self.page.services.append(self.file_picker)

        top = ft.Row(
            controls=[
                ft.Icon(ft.Icons.FOLDER_SHARED, color=ACCENT, size=24),
                ft.Text("File Manager", size=18, weight=ft.FontWeight.BOLD, color=FG),
                ft.Container(expand=True),
                ft.Text(self.creds.username, color=FG_MUTED, size=13),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        repo_row = ft.Row(
            controls=[
                ft.Container(expand=True, content=self.repo_dropdown),
                self.refresh_button,
            ],
            spacing=8,
        )

        browser_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Repository", size=15, weight=ft.FontWeight.BOLD, color=FG),
                    repo_row,
                    self.breadcrumb,
                    ft.Container(
                        content=self.file_list,
                        bgcolor=BG_SOFT,
                        border_radius=8,
                        padding=ft.Padding.all(8),
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

        actions_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Actions", size=15, weight=ft.FontWeight.BOLD, color=FG),
                    self.action_bar,
                    self.status_text,
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
                        padding=ft.Padding.all(8),
                        bgcolor=BG_SOFT,
                        border_radius=6,
                    ),
                ],
                spacing=8,
            ),
            padding=ft.Padding.all(16),
            bgcolor=BG_SOFT,
            border_radius=12,
            border=ft.border.BorderSide(1, BG_SOFTER),
        )

        return ft.Column(
            controls=[top, browser_card, actions_card, log_card],
            spacing=12,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        """Called when this page becomes active. Loads repos if needed."""
        if not self.repos:
            self.load_repos_async()

    # ------------------------------------------------------------------
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
        )
        self.log_box.controls.append(line)
        if len(self.log_box.controls) > 100:
            self.log_box.controls = self.log_box.controls[-100:]
        self.page.update()

    # ------------------------------------------------------------------
    # Repo loading
    # ------------------------------------------------------------------
    def load_repos_async(self) -> None:
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
                if repos:
                    self._append_log("repos", f"Loaded {len(repos)} repo(s).", GREEN)
                else:
                    self._set_status("No repositories found.", YELLOW)
            except GitHubError as e:
                self.repo_dropdown.options = []
                self.repo_dropdown.disabled = False
                self._set_status(f"Failed: {e.message}", RED)
                self._append_log("repos", f"GitHub error: {e.message}", RED)
            except Exception as e:
                self.repo_dropdown.options = []
                self.repo_dropdown.disabled = False
                self._set_status(f"Network: {e}", RED)
                self._append_log("repos", f"Error: {e}", RED)
            self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh(self, _e) -> None:
        self.load_repos_async()

    def on_repo_select(self, _e) -> None:
        full = self.repo_dropdown.value
        self.selected_repo = next(
            (r for r in self.repos if r.full_name == full), None
        )
        self.current_path = ""
        self.selected_entry = None
        if self.selected_repo:
            self._append_log("select", f"Browsing {self.selected_repo.full_name}", GREEN)
            self.load_files_async()
        else:
            self._append_log("select", f"No matching repo for {full!r}", YELLOW)
            self.page.update()

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------
    def load_files_async(self) -> None:
        if not self.selected_repo:
            return
        repo = self.selected_repo
        path = self.current_path

        def worker():
            try:
                with GitHubClient(self.creds.username, self.creds.token, timeout=20.0) as gh:
                    entries = list_repo_path(gh, repo, path)
                self.entries = entries
                self._render_file_list()
                self._render_breadcrumb()
                self._set_status(f"Showing {len(entries)} item(s) at /{path}".rstrip("/"))
            except GitHubError as e:
                self._set_status(f"Failed: {e.message}", RED)
                self._append_log("list", f"GitHub error: {e.message}", RED)
                self.file_list.controls.clear()
                self.page.update()
            except Exception as e:
                self._set_status(f"Error: {e}", RED)
                self._append_log("list", f"Error: {e}", RED)
                self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    def _render_breadcrumb(self) -> None:
        self.breadcrumb.controls.clear()
        self.breadcrumb.controls.append(
            ft.Text("Path:", size=13, color=FG_MUTED),
        )
        # Root link
        root_link = ft.TextButton(
            "/",
            on_click=lambda _: self._navigate_to(""),
            style=ft.ButtonStyle(color=ACCENT, padding=0),
        )
        self.breadcrumb.controls.append(root_link)
        if self.current_path:
            parts = self.current_path.split("/")
            for i, part in enumerate(parts):
                self.breadcrumb.controls.append(
                    ft.Text("/", size=13, color=FG_MUTED),
                )
                target = "/".join(parts[: i + 1])
                self.breadcrumb.controls.append(
                    ft.TextButton(
                        part,
                        on_click=lambda _e, t=target: self._navigate_to(t),
                        style=ft.ButtonStyle(color=ACCENT, padding=0),
                    ),
                )
        self.page.update()

    def _render_file_list(self) -> None:
        self.file_list.controls.clear()
        # If we're inside a subfolder, show ".." entry
        if self.current_path:
            up_btn = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.ARROW_UPWARD, color=FG_MUTED, size=18),
                        ft.Text("..", color=FG_MUTED, size=14),
                    ],
                ),
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                on_click=lambda _: self._navigate_up(),
            )
            self.file_list.controls.append(up_btn)

        if not self.entries:
            self.file_list.controls.append(
                ft.Container(
                    content=ft.Text(
                        "(empty — this folder has no files yet)",
                        color=FG_MUTED, italic=True, size=13,
                    ),
                    padding=ft.Padding.all(12),
                )
            )
        else:
            for entry in self.entries:
                self.file_list.controls.append(self._make_entry_row(entry))
        self.page.update()

    def _make_entry_row(self, entry: RepoEntry) -> ft.Control:
        if entry.type == "dir":
            icon = ft.Icons.FOLDER
            color = ACCENT
            on_click = lambda _e, p=entry.path: self._navigate_to(p)
        else:
            icon = ft.Icons.INSERT_DRIVE_FILE
            color = FG_MUTED
            on_click = lambda _e, e=entry: self._select_entry(e)

        size_text = ""
        if entry.type == "file":
            if entry.size < 1024:
                size_text = f"{entry.size} B"
            elif entry.size < 1024 * 1024:
                size_text = f"{entry.size / 1024:.1f} KB"
            else:
                size_text = f"{entry.size / (1024 * 1024):.1f} MB"

        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(icon, color=color, size=20),
                    ft.Text(entry.name, color=FG, size=14, expand=True),
                    ft.Text(size_text, color=FG_MUTED, size=11),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            border_radius=6,
            on_click=on_click,
            ink=True,
        )

    def _navigate_to(self, path: str) -> None:
        self.current_path = path
        self.selected_entry = None
        self.load_files_async()

    def _navigate_up(self) -> None:
        if not self.current_path:
            return
        parts = self.current_path.split("/")
        parts.pop()
        self.current_path = "/".join(parts)
        self.selected_entry = None
        self.load_files_async()

    def _select_entry(self, entry: RepoEntry) -> None:
        if entry.type != "file":
            return
        self.selected_entry = entry
        self._set_status(
            f"Selected: {entry.path}  ·  {entry.size} bytes",
            GREEN,
        )

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def _on_upload_click(self, _e) -> None:
        if not self.selected_repo:
            self._set_status("Pick a repository first.", YELLOW)
            return
        self.file_picker.pick_files(allow_multiple=False)

    def _on_file_picked(self, e: ft.FilePickerResultEvent) -> None:
        if not e.files:
            return
        if not self.selected_repo:
            return
        picked = e.files[0]
        local_path = picked.path
        dest_default = picked.name
        if self.current_path:
            dest_default = f"{self.current_path}/{picked.name}"

        # Ask for destination path
        dest_field = ft.TextField(
            label="Destination path in repo",
            value=dest_default,
            autofocus=True,
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )
        msg_field = ft.TextField(
            label="Commit message",
            value=f"chore: upload {picked.name}",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )

        def do_upload(_e):
            dest = (dest_field.value or "").strip()
            if not dest:
                return
            msg = (msg_field.value or "").strip() or None
            dlg.open = False
            self.page.update()
            self._run_upload(local_path, dest, msg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Upload {picked.name}", color=FG),
            content=ft.Column(
                controls=[
                    ft.Text(f"Local file: {picked.name} ({picked.size} bytes)", color=FG_MUTED, size=12),
                    dest_field,
                    msg_field,
                ],
                spacing=10,
                tight=True,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self._close_dialog(dlg)),
                ft.FilledButton("Upload", on_click=do_upload),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._open_dialog(dlg)

    def _run_upload(self, local_path: str, dest_path: str, message: str | None) -> None:
        if self._busy:
            self._set_status("Busy — please wait.", YELLOW)
            return
        self._busy = True
        repo = self.selected_repo

        def worker():
            try:
                with open(local_path, "rb") as f:
                    content = f.read()
                with GitHubClient(self.creds.username, self.creds.token, timeout=60.0) as gh:
                    sha = upload_file_to_repo(
                        gh, repo, dest_path, content,
                        message=message,
                        progress=lambda phase, m: self._append_log(phase, m),
                    )
                self._set_status(f"Uploaded. Commit {sha[:8]}...", GREEN)
                self._append_log("upload", f"Commit URL: https://github.com/{repo.full_name}/commit/{sha}", GREEN)
                self.load_files_async()
            except GitHubError as e:
                self._set_status(f"Failed: {e.message}", RED)
                self._append_log("upload", f"GitHub error: {e.message}", RED)
            except Exception as e:
                self._set_status(f"Error: {e}", RED)
                self._append_log("upload", f"Error: {e}", RED)
            finally:
                self._busy = False
                self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # New folder
    # ------------------------------------------------------------------
    def _on_new_folder_click(self, _e) -> None:
        if not self.selected_repo:
            self._set_status("Pick a repository first.", YELLOW)
            return
        name_field = ft.TextField(
            label="Folder name",
            autofocus=True,
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )
        msg_field = ft.TextField(
            label="Commit message",
            value="chore: create folder",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )

        def do_create(_e):
            name = (name_field.value or "").strip().strip("/")
            if not name:
                return
            full_path = f"{self.current_path}/{name}" if self.current_path else name
            msg = (msg_field.value or "").strip() or None
            dlg.open = False
            self.page.update()
            self._run_create_folder(full_path, msg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Create folder", color=FG),
            content=ft.Column(
                controls=[name_field, msg_field],
                spacing=10,
                tight=True,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self._close_dialog(dlg)),
                ft.FilledButton("Create", on_click=do_create),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._open_dialog(dlg)

    def _run_create_folder(self, folder_path: str, message: str | None) -> None:
        if self._busy:
            return
        self._busy = True
        repo = self.selected_repo

        def worker():
            try:
                with GitHubClient(self.creds.username, self.creds.token, timeout=30.0) as gh:
                    sha = create_folder_in_repo(
                        gh, repo, folder_path, message=message,
                        progress=lambda phase, m: self._append_log(phase, m),
                    )
                self._set_status(f"Created folder. Commit {sha[:8]}...", GREEN)
                self.load_files_async()
            except GitHubError as e:
                self._set_status(f"Failed: {e.message}", RED)
                self._append_log("folder", f"GitHub error: {e.message}", RED)
            except Exception as e:
                self._set_status(f"Error: {e}", RED)
                self._append_log("folder", f"Error: {e}", RED)
            finally:
                self._busy = False
                self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # New text file
    # ------------------------------------------------------------------
    def _on_new_file_click(self, _e) -> None:
        if not self.selected_repo:
            self._set_status("Pick a repository first.", YELLOW)
            return
        name_field = ft.TextField(
            label="File name (e.g. notes.txt)",
            autofocus=True,
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )
        content_field = ft.TextField(
            label="File content",
            multiline=True,
            min_lines=4,
            max_lines=10,
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )
        msg_field = ft.TextField(
            label="Commit message",
            value="chore: create file",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )

        def do_create(_e):
            name = (name_field.value or "").strip().strip("/")
            if not name:
                return
            full_path = f"{self.current_path}/{name}" if self.current_path else name
            content = content_field.value or ""
            msg = (msg_field.value or "").strip() or None
            dlg.open = False
            self.page.update()
            self._run_create_file(full_path, content, msg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Create file", color=FG),
            content=ft.Column(
                controls=[name_field, content_field, msg_field],
                spacing=10,
                tight=True,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self._close_dialog(dlg)),
                ft.FilledButton("Create", on_click=do_create),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._open_dialog(dlg)

    def _run_create_file(self, path: str, content: str, message: str | None) -> None:
        if self._busy:
            return
        self._busy = True
        repo = self.selected_repo

        def worker():
            try:
                with GitHubClient(self.creds.username, self.creds.token, timeout=30.0) as gh:
                    sha = create_text_file_in_repo(
                        gh, repo, path, content, message=message,
                        progress=lambda phase, m: self._append_log(phase, m),
                    )
                self._set_status(f"Created file. Commit {sha[:8]}...", GREEN)
                self.load_files_async()
            except GitHubError as e:
                self._set_status(f"Failed: {e.message}", RED)
                self._append_log("file", f"GitHub error: {e.message}", RED)
            except Exception as e:
                self._set_status(f"Error: {e}", RED)
                self._append_log("file", f"Error: {e}", RED)
            finally:
                self._busy = False
                self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Copy file between repos
    # ------------------------------------------------------------------
    def _on_copy_click(self, _e) -> None:
        if not self.repos:
            self._set_status("Load repos first.", YELLOW)
            return

        src_dropdown = ft.Dropdown(
            label="Source repo",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
            expand=True,
        )
        src_dropdown.options = [
            ft.dropdown.Option(r.full_name, r.full_name) for r in self.repos
        ]
        src_path_field = ft.TextField(
            label="Source file path (e.g. assets/icon.png)",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )
        dst_dropdown = ft.Dropdown(
            label="Destination repo",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
            expand=True,
        )
        dst_dropdown.options = [
            ft.dropdown.Option(r.full_name, r.full_name) for r in self.repos
        ]
        dst_path_field = ft.TextField(
            label="Destination path (e.g. assets/icon.png)",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )
        msg_field = ft.TextField(
            label="Commit message",
            value="chore: copy file from another repo",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )

        def do_copy(_e):
            src_full = src_dropdown.value
            dst_full = dst_dropdown.value
            src_path = (src_path_field.value or "").strip().strip("/")
            dst_path = (dst_path_field.value or "").strip().strip("/")
            if not (src_full and dst_full and src_path and dst_path):
                return
            msg = (msg_field.value or "").strip() or None
            src_repo = next((r for r in self.repos if r.full_name == src_full), None)
            dst_repo = next((r for r in self.repos if r.full_name == dst_full), None)
            if not src_repo or not dst_repo:
                return
            dlg.open = False
            self.page.update()
            self._run_copy(src_repo, src_path, dst_repo, dst_path, msg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Copy file between repos", color=FG),
            content=ft.Column(
                controls=[
                    ft.Text("Source", color=ACCENT, size=12, weight=ft.FontWeight.BOLD),
                    src_dropdown,
                    src_path_field,
                    ft.Text("Destination", color=ACCENT, size=12, weight=ft.FontWeight.BOLD),
                    dst_dropdown,
                    dst_path_field,
                    msg_field,
                ],
                spacing=8,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self._close_dialog(dlg)),
                ft.FilledButton("Copy", on_click=do_copy),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._open_dialog(dlg)

    def _run_copy(self, src: RepoInfo, src_path: str, dst: RepoInfo, dst_path: str, message: str | None) -> None:
        if self._busy:
            return
        self._busy = True

        def worker():
            try:
                with GitHubClient(self.creds.username, self.creds.token, timeout=60.0) as gh:
                    sha = copy_file_between_repos(
                        gh, src, src_path, dst, dst_path, message=message,
                        progress=lambda phase, m: self._append_log(phase, m),
                    )
                self._set_status(f"Copied. Commit {sha[:8]}...", GREEN)
                self._append_log(
                    "copy",
                    f"Commit URL: https://github.com/{dst.full_name}/commit/{sha}",
                    GREEN,
                )
                if dst.full_name == self.selected_repo.full_name if self.selected_repo else False:
                    self.load_files_async()
            except GitHubError as e:
                self._set_status(f"Failed: {e.message}", RED)
                self._append_log("copy", f"GitHub error: {e.message}", RED)
            except Exception as e:
                self._set_status(f"Error: {e}", RED)
                self._append_log("copy", f"Error: {e}", RED)
            finally:
                self._busy = False
                self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def _on_delete_click(self, _e) -> None:
        if not self.selected_repo:
            self._set_status("Pick a repository first.", YELLOW)
            return
        if not self.selected_entry or self.selected_entry.type != "file":
            self._set_status("Select a file to delete first.", YELLOW)
            return
        entry = self.selected_entry
        msg_field = ft.TextField(
            label="Commit message",
            value=f"chore: delete {entry.name}",
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=14,
        )

        def do_delete(_e):
            msg = (msg_field.value or "").strip() or None
            dlg.open = False
            self.page.update()
            self._run_delete(entry, msg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Delete {entry.name}?", color=RED),
            content=ft.Column(
                controls=[
                    ft.Text(
                        f"This will delete {entry.path} from {self.selected_repo.full_name}. "
                        f"This cannot be undone.",
                        color=FG_MUTED, size=13,
                    ),
                    msg_field,
                ],
                spacing=10,
                tight=True,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self._close_dialog(dlg)),
                ft.FilledButton("Delete", on_click=do_delete,
                                style=ft.ButtonStyle(bgcolor=RED, color="#ffffff")),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._open_dialog(dlg)

    def _run_delete(self, entry: RepoEntry, message: str | None) -> None:
        if self._busy:
            return
        self._busy = True
        repo = self.selected_repo

        def worker():
            try:
                with GitHubClient(self.creds.username, self.creds.token, timeout=30.0) as gh:
                    sha = delete_file_in_repo(
                        gh, repo, entry.path, entry.sha, message=message,
                        progress=lambda phase, m: self._append_log(phase, m),
                    )
                self._set_status(f"Deleted. Commit {sha[:8]}...", GREEN)
                self.selected_entry = None
                self.load_files_async()
            except GitHubError as e:
                self._set_status(f"Failed: {e.message}", RED)
                self._append_log("delete", f"GitHub error: {e.message}", RED)
            except Exception as e:
                self._set_status(f"Error: {e}", RED)
                self._append_log("delete", f"Error: {e}", RED)
            finally:
                self._busy = False
                self.page.update()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Dialog helpers
    # ------------------------------------------------------------------
    def _open_dialog(self, dlg: ft.AlertDialog) -> None:
        if dlg not in self.page.overlay:
            self.page.overlay.append(dlg)
        dlg.open = True
        self.page.update()

    def _close_dialog(self, dlg: ft.AlertDialog) -> None:
        dlg.open = False
        self.page.update()
