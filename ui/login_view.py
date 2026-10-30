"""Login screen.

Two fields: GitHub Username, and Password (which is actually a PAT —
GitHub disabled password-based API auth in Aug 2021, so this is the
only viable path; a help link explains it).

On successful auth, the controller stores creds via :class:`Storage`
and routes to the main view.
"""
from __future__ import annotations

import flet as ft

from src.github_client import GitHubClient, GitHubError
from src.storage import Credentials, Storage
from ui.theme import ACCENT, BG_SOFT, BG_SOFTER, FG, FG_MUTED, RED, YELLOW


class LoginView:
    """Builder for the login page."""

    def __init__(self, page: ft.Page, on_success):
        self.page = page
        self.on_success = on_success
        self.storage = Storage(page)

        self.username_field = ft.TextField(
            label="GitHub Username",
            hint_text="e.g. octocat",
            autofocus=True,
            prefix_icon=ft.Icons.PERSON_OUTLINED,
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=15,
        )
        self.password_field = ft.TextField(
            label="Password (Personal Access Token)",
            hint_text="ghp_xxxxxxxxxxxxxxxxxxxx",
            password=True,
            can_reveal_password=True,
            prefix_icon=ft.Icons.LOCK_OUTLINE,
            border_color=BG_SOFTER,
            focused_border_color=ACCENT,
            text_size=15,
        )
        self.save_switch = ft.Switch(
            label="Save credentials on this device",
            value=True,
            active_color=ACCENT,
        )
        self.error_text = ft.Text("", color=RED, size=13, visible=False)
        self.progress = ft.ProgressRing(visible=False, color=ACCENT)
        self.login_button = ft.FilledButton(
            "Sign in",
            icon=ft.Icons.LOGIN,
            on_click=self._on_login,
            style=ft.ButtonStyle(
                bgcolor=ACCENT,
                color="#ffffff",
                padding=ft.Padding.symmetric(horizontal=24, vertical=14),
            ),
        )

    # ------------------------------------------------------------------
    def build(self) -> ft.Control:
        pre = self.storage.load_credentials()
        if pre:
            self.username_field.value = pre.username
            self.password_field.value = pre.token
            self.save_switch.value = True

        header = ft.Column(
            controls=[
                ft.Icon(ft.Icons.CLOUD_UPLOAD, color=ACCENT, size=44),
                ft.Text(
                    "GitHub Auto Committer",
                    size=26,
                    weight=ft.FontWeight.BOLD,
                    color=FG,
                ),
                ft.Text(
                    "Drop a zip → land as a single commit on GitHub.",
                    size=13,
                    color=FG_MUTED,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
        )

        help_text = ft.Text(
            "GitHub requires a Personal Access Token instead of your "
            "account password. Create one at "
            "https://github.com/settings/tokens (scopes: repo, workflow).",
            size=11,
            color=FG_MUTED,
            text_align=ft.TextAlign.CENTER,
        )

        form = ft.Column(
            controls=[
                self.username_field,
                self.password_field,
                self.save_switch,
                self.error_text,
                ft.Row(
                    controls=[self.login_button, self.progress],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=10,
                ),
                help_text,
            ],
            spacing=14,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        card = ft.Container(
            content=form,
            padding=ft.Padding.all(28),
            bgcolor=BG_SOFT,
            border_radius=14,
            border=ft.border.BorderSide(1, BG_SOFTER),
        )

        return ft.Column(
            controls=[
                ft.Container(height=40),
                header,
                ft.Container(height=20),
                card,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
        )

    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self.progress.visible = busy
        self.login_button.disabled = busy
        self.page.update()

    def _show_error(self, msg: str) -> None:
        self.error_text.value = msg
        self.error_text.visible = True
        self.page.update()

    def _on_login(self, _e) -> None:
        u = (self.username_field.value or "").strip()
        p = (self.password_field.value or "").strip()
        if not u or not p:
            self._show_error("Please fill in both fields.")
            return
        self.error_text.visible = False
        self._set_busy(True)
        try:
            with GitHubClient(u, p, timeout=20.0) as gh:
                gh.verify_credentials()
            # Verified.
            if self.save_switch.value:
                self.storage.save_credentials(Credentials(username=u, token=p))
            self.on_success()
        except GitHubError as e:
            if e.status == 401:
                self._show_error("Invalid credentials. Double-check the username and PAT.")
            elif e.status == 403:
                self._show_error("Rate limited or 2FA required. Try again in a moment.")
            else:
                self._show_error(f"GitHub error: {e.message}")
        except Exception as e:
            self._show_error(f"Network error: {e}")
        finally:
            self._set_busy(False)
