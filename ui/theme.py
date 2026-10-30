"""Material 3 dark theme with GitHub-purple accent.

Imported by main.py. Exposes :func:`apply_dark` so the rest of the app
stays theme-agnostic.
"""
from __future__ import annotations

import flet as ft

# Color tokens — kept here so any view can reference them by name.
GITHUB_PURPLE = "#6e40c9"
GITHUB_PURPLE_BRIGHT = "#8957e5"
GITHUB_BG = "#0d1117"
GITHUB_BG_SOFT = "#161b22"
GITHUB_BG_SOFTER = "#21262d"
GITHUB_FG = "#e6edf3"
GITHUB_FG_MUTED = "#7d8590"
GITHUB_GREEN = "#3fb950"
GITHUB_RED = "#f85149"
GITHUB_YELLOW = "#d29922"

ACCENT = GITHUB_PURPLE
ACCENT_BRIGHT = GITHUB_PURPLE_BRIGHT
BG = GITHUB_BG
BG_SOFT = GITHUB_BG_SOFT
BG_SOFTER = GITHUB_BG_SOFTER
FG = GITHUB_FG
FG_MUTED = GITHUB_FG_MUTED
GREEN = GITHUB_GREEN
RED = GITHUB_RED
YELLOW = GITHUB_YELLOW


def build_theme() -> ft.Theme:
    """Build the dark Material 3 theme used across the app."""
    cs = ft.ColorScheme(
        primary=ACCENT,
        on_primary="#ffffff",
        primary_container=ACCENT_BRIGHT,
        on_primary_container="#ffffff",
        secondary=ACCENT_BRIGHT,
        on_secondary="#ffffff",
        surface=BG_SOFT,
        on_surface=FG,
        on_surface_variant=FG_MUTED,
        surface_container=BG,
        surface_container_low=BG,
        surface_container_high=BG_SOFT,
        surface_container_lowest=BG,
        error=RED,
        on_error="#ffffff",
        outline=BG_SOFTER,
        outline_variant=BG_SOFTER,
    )
    return ft.Theme(color_scheme=cs, use_material3=True)


def apply_dark(page: ft.Page) -> None:
    """Configure the page for the dark GitHub-ish look in one shot."""
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = build_theme()
    page.bgcolor = BG
    page.padding = 20
    page.title = "GitHub Auto Committer"


def status_color(status: str) -> str:
    return {
        "ok": GREEN,
        "error": RED,
    }.get(status, YELLOW)
