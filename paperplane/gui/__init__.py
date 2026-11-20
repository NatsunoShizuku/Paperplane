"""Paperplane Material Design 3 图形界面子包。"""
from .app import build_gui_app, launch_gui
from .prefs import (
    _avatar_file_for,
    _data_dir_for,
    _load_dark_mode,
    _save_dark_mode,
    load_dashboard_scope,
    save_dashboard_scope,
)

__all__ = [
    "build_gui_app",
    "launch_gui",
    "_load_dark_mode",
    "_save_dark_mode",
    "_data_dir_for",
    "_avatar_file_for",
    "load_dashboard_scope",
    "save_dashboard_scope",
]
