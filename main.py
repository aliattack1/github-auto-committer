"""GitHub Auto Committer — Flet app entry point.

Run on desktop:
    python main.py

Build for Android (debug APK):
    flet build apk --debug

Build for Android (release APK):
    flet build apk

The accompanying ``.github/workflows/build-apk.yml`` lets GitHub Actions
build the APK automatically when you push this repo.

Pages:
    0. Commit  — pick a zip, pick/create a repo, push as a single commit
    1. History — past commits made via this app
    2. Files   — browse repos, upload/create/delete/copy files (File Manager)
"""
from __future__ import annotations

import threading

import flet as ft

from src.github_client import GitHubClient, GitHubError
from src.storage import Credentials, Storage
from ui.login_view import LoginView
from ui.main_view import MainView
from ui.history_view import HistoryView
from ui.file_manager_view import FileManagerView
from ui.theme import ACCENT, BG, apply_dark


def main(page: ft.Page) -> None:
    """App entry — Flet calls this with the active page."""
    apply_dark(page)
    storage = Storage(page)

    state = {"creds": storage.load_credentials()}
    # Persistent view instances so they keep their state across nav switches.
    nav_state: dict = {
        "main_view": None,        # MainView instance
        "file_manager": None,     # FileManagerView instance
        "content": None,          # content Container whose .content we swap
    }

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
        creds = state["creds"]
        if creds is None:
            _show_login()
            return

        # Reuse existing MainView if we've already built it; otherwise build.
        if nav_state["main_view"] is None:
            view = MainView(page, creds=creds, on_sign_out=_on_sign_out)
            # Flet 1.0: Dropdown uses on_select (not on_change).
            view.repo_dropdown.on_select = view.on_repo_change
            nav_state["main_view"] = view
            built_view = view.build()
            view.load_repos_async()
            view.after_mount()
        else:
            view = nav_state["main_view"]
            built_view = view.build()

        _mount_into_content(built_view)

    def _show_history() -> None:
        _mount_into_content(HistoryView(page).build())

    def _show_file_manager() -> None:
        creds = state["creds"]
        if creds is None:
            _show_login()
            return
        if nav_state["file_manager"] is None:
            view = FileManagerView(page, creds=creds)
            view.repo_dropdown.on_select = view.on_repo_select
            nav_state["file_manager"] = view
            built_view = view.build()
            view.on_show()
        else:
            view = nav_state["file_manager"]
            built_view = view.build()
            view.on_show()

        _mount_into_content(built_view)

    def _mount_into_content(ctrl: ft.Control) -> None:
        """Swap the active screen into the persistent content container.

        Builds the NavigationBar + content shell on first call, then just
        swaps ``content.content`` on subsequent calls.
        """
        if nav_state["content"] is None:
            # First mount — build the shell (content + nav bar).
            content = ft.Container(content=ctrl, expand=True)
            nav_state["content"] = content
            nav = ft.NavigationBar(
                destinations=[
                    ft.NavigationBarDestination(icon=ft.Icons.UPLOAD_FILE, label="Commit"),
                    ft.NavigationBarDestination(icon=ft.Icons.HISTORY, label="History"),
                    ft.NavigationBarDestination(icon=ft.Icons.FOLDER_SHARED, label="Files"),
                ],
                selected_index=0,
                on_change=_on_nav_change,
                bgcolor=BG,
            )
            page.controls.clear()
            page.add(content, nav)
            page.update()
        else:
            nav_state["content"].content = ctrl
            page.update()

    def _on_nav_change(e: ft.ControlEvent) -> None:
        idx = e.control.selected_index
        if idx == 0:
            _show_main()
        elif idx == 1:
            _show_history()
        elif idx == 2:
            _show_file_manager()

    # ------------------------------------------------------------------
    # Lifecycle callbacks
    # ------------------------------------------------------------------
    def _on_login_success() -> None:
        state["creds"] = storage.load_credentials()
        nav_state["main_view"] = None       # force rebuild with new creds
        nav_state["file_manager"] = None
        nav_state["content"] = None
        _show_main()

    def _on_sign_out() -> None:
        storage.clear_credentials()
        state["creds"] = None
        nav_state["main_view"] = None
        nav_state["file_manager"] = None
        nav_state["content"] = None
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
            page.controls.clear()
            page.update()
            _show_login()

    threading.Thread(target=_verify_worker, daemon=True).start()


if __name__ == "__main__":
    ft.run(main)
