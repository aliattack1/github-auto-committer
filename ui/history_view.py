"""History view — shows the last N commits the app pushed."""
from __future__ import annotations

import time

import flet as ft

from src.storage import Storage
from ui.theme import ACCENT, BG_SOFT, BG_SOFTER, FG, FG_MUTED, status_color


class HistoryView:
    def __init__(self, page: ft.Page):
        self.page = page
        self.storage = Storage(page)

    def build(self) -> ft.Control:
        items = self.storage.list_history()

        header = ft.Row(
            controls=[
                ft.Icon(ft.Icons.HISTORY, color=ACCENT, size=22),
                ft.Text(
                    "Commit history",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=FG,
                ),
                ft.Container(expand=True),
                ft.TextButton(
                    "Clear",
                    icon=ft.Icons.DELETE_OUTLINE,
                    on_click=self._on_clear,
                ),
            ],
        )

        if not items:
            body = ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Icon(ft.Icons.INBOX, color=FG_MUTED, size=48),
                        ft.Text(
                            "Nothing pushed yet.",
                            color=FG_MUTED,
                            size=14,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                expand=True,
                alignment=ft.Alignment.CENTER,
            )
        else:
            rows = []
            for it in items:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(it.timestamp))
                color = status_color(it.status)
                icon = ft.Icons.CHECK_CIRCLE if it.status == "ok" else ft.Icons.ERROR_OUTLINE
                row = ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(icon, color=color, size=22),
                            ft.Column(
                                controls=[
                                    ft.Text(it.message, color=FG, size=14, weight=ft.FontWeight.BOLD),
                                    ft.Text(
                                        f"{it.repo_full_name} @ {it.branch}  ·  {it.file_count} file(s)",
                                        color=FG_MUTED,
                                        size=12,
                                    ),
                                    ft.Text(ts, color=FG_MUTED, size=11),
                                ] + (
                                    [ft.Text(f"Error: {it.error}", color=color, size=11)]
                                    if it.error else []
                                ) + (
                                    [
                                        ft.Text(
                                            f"sha {it.commit_sha[:12]}",
                                            color=FG_MUTED,
                                            size=11,
                                            selectable=True,
                                        )
                                    ] if it.commit_sha else []
                                ),
                                spacing=2,
                                expand=True,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.START,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.all(14),
                    bgcolor=BG_SOFT,
                    border_radius=10,
                    border=ft.border.BorderSide(1, BG_SOFTER),
                )
                rows.append(row)
            body = ft.Column(
                controls=rows,
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )

        return ft.Column(
            controls=[header, body],
            spacing=14,
            expand=True,
        )

    def _on_clear(self, _e) -> None:
        self.storage.clear_history()
        self.page.controls.clear()
        self.page.add(self.build())
        self.page.update()
