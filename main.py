"""GitHub Auto Committer — Flet app entry point.

Run on desktop:
    python main.py

Build for Android (debug APK):
    flet build apk --debug

Build for Android (release APK):
    flet build apk

The accompanying ``.github/workflows/build-apk.yml`` lets GitHub Actions
build the APK automatically when you push this repo.
"""
from __future__ import annotations

import threading

import flet as ft

from src.github_client import GitHubClient, GitHubError
from src.storage import Credentials, Storage
from ui.login_view import LoginView
from ui.main_view import MainView
from ui.history_view import HistoryView
from ui.theme import ACCENT, BG, apply_dark


def main(page: ft.Page) -> None:
    """App entry — Flet calls this with the active page."""
    apply_dark(page)
    storage = Storage(page)

    # Cached creds available — these are mutable across nested funcs.
    state = {"creds": storage.load_credentials()}
    nav_state = {"view": None}  # holds the active MainView for refresh

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------
    def _show_login() -> None:
        page.controls.clear()
        page.overlay.clear()
        view = LoginView(page, on_success=_on_login_success)
        page.add(view.build())
        page.update()

    def _show_main() -> None:
        page.controls.clear()
        page.overlay.clear()
        creds = state["creds"]
        if creds is None:
            _show_login()
            return

        view = MainView(page, creds=creds, on_sign_out=_on_sign_out)
        view.repo_dropdown.on_change = view.on_repo_change
        nav_state["view"] = view

        # Build the main view first; it appends its file_picker to overlay.
        main_view = view.build()

        nav = ft.NavigationBar(
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.UPLOAD_FILE, label="Commit"),
                ft.NavigationBarDestination(icon=ft.Icons.HISTORY, label="History"),
            ],
            selected_index=0,
            on_change=_on_nav_change,
            bgcolor=BG,
        )

        # content placeholder holds the active screen
        content = ft.Container(content=main_view, expand=True)
        nav_state["content"] = content

        page.add(content, nav)
        view.load_repos_async()
        page.update()

    def _show_history() -> None:
        # Keep nav rail, swap content area
        content = nav_state.get("content")
        if content is None:
            return
        content.content = HistoryView(page).build()
        page.update()

    def _on_nav_change(e: ft.ControlEvent) -> None:
        idx = e.control.selected_index
        if idx == 0:
            _show_main()
        else:
            _show_history()

    # ------------------------------------------------------------------
    # Lifecycle callbacks
    # ------------------------------------------------------------------
    def _on_login_success() -> None:
        state["creds"] = storage.load_credentials()
        _show_main()

    def _on_sign_out() -> None:
        storage.clear_credentials()
        state["creds"] = None
        _show_login()

    # ------------------------------------------------------------------
    # Boot
    # ------------------------------------------------------------------
    creds = state["creds"]
    if creds is None:
        _show_login()
        return

    # Saved creds present — show a quick verifying splash, then route.
    page.add(
        ft.Container(
            content=ft.Column(
                controls=[
                    ft.ProgressRing(color=ACCENT),
                    ft.Text("Verifying saved credentials...", color=ACCENT),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            expand=True,
            alignment=ft.Alignment.CENTER,
        )
    )
    page.update()

    def _verify_worker():
        try:
            with GitHubClient(creds.username, creds.token, timeout=15.0) as gh:
                gh.verify_credentials()
            page.controls.clear()
            page.update()
            _show_main()
        except GitHubError as e:
            if e.status == 401:
                storage.clear_credentials()
                state["creds"] = None
            page.controls.clear()
            page.update()
            _show_login()
        except Exception:
            # Network down etc. — let user retry by going to login screen.
            page.controls.clear()
            page.update()
            _show_login()

    threading.Thread(target=_verify_worker, daemon=True).start()


if __name__ == "__main__":
    ft.run(main)
