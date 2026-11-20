"""现代化 Material Design 3 风格 GUI 主应用装配与启动入口。"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi.responses import FileResponse, Response

from ..config import ensure_config_file
from ..service import AuthSession, LongRunningRunner
from .pages_auth import build_auth_page
from .pages_channels import build_channels_page
from .pages_config import build_config_page
from .pages_dashboard import build_dashboard_page
from .prefs import _avatar_file_for, _load_dark_mode, _save_dark_mode
from .theme import M3_HEAD_CSS, avatar_color, initial_char

log = logging.getLogger("paperplane.gui")


def build_gui_app(config_path: str | Path):
    """构建 Material Design 3 风格 NiceGUI 应用（关闭浏览器标签页不影响后台长期挂机）。"""
    try:
        from nicegui import app, ui
    except ImportError as e:
        raise SystemExit(
            f"缺少 GUI 依赖（{e.name}），请运行：pip install nicegui tomlkit"
        ) from e

    cfg_path = ensure_config_file(Path(config_path).resolve())
    runner = LongRunningRunner(cfg_path, catchup_interval=1800.0)
    auth_holder: dict[str, AuthSession | None] = {"session": None}

    @app.get("/api/avatar/{chat_id}")
    def serve_avatar(chat_id: int):
        p = _avatar_file_for(cfg_path, chat_id)
        if p is not None:
            return FileResponse(p, media_type="image/jpeg")
        return Response(status_code=404)

    @app.on_shutdown
    async def _cleanup():
        if runner.is_running:
            await runner.stop()
        if auth_holder["session"]:
            await auth_holder["session"].disconnect()
            auth_holder["session"] = None

    def render_avatar(chat_id: int | None, name: str, has_avatar: bool | None = None):
        if has_avatar is None:
            has_avatar = _avatar_file_for(cfg_path, chat_id) is not None
        if has_avatar and chat_id is not None:
            ui.html(
                f'<img class="m3-avatar-img" src="/api/avatar/{chat_id}?t={int(time.time() // 60)}" alt="" />',
                sanitize=False,
            )
        else:
            bg = avatar_color(chat_id if chat_id is not None else name)
            ch = initial_char(name).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            ui.html(f'<span class="m3-avatar-fallback" style="background:{bg}">{ch}</span>', sanitize=False)

    @ui.page("/")
    def index():
        ui.page_title("Paperplane · Material 控制台")
        ui.colors(
            primary="#4F46E5",
            secondary="#0EA5E9",
            accent="#6366F1",
            positive="#10B981",
            negative="#EF4444",
            warning="#F59E0B",
            info="#3B82F6",
        )
        ui.add_head_html(M3_HEAD_CSS)
        saved_dark = _load_dark_mode(cfg_path)
        dark = ui.dark_mode(value=saved_dark)

        nav_buttons: dict[str, ui.button] = {}
        pages: dict[str, ui.column] = {}

        def switch_page(name: str):
            for key, container in pages.items():
                container.set_visibility(key == name)
            for key, btn in nav_buttons.items():
                if key == name:
                    btn.classes(add="m3-nav-active")
                else:
                    btn.classes(remove="m3-nav-active")

        # ==================== 顶部 App Bar ====================
        with ui.header(elevated=False).classes("items-center justify-between px-4 py-2 m3-header"):
            with ui.row().classes("items-center gap-3"):
                ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round dense")
                with ui.avatar(color="primary", text_color="white", size="34px"):
                    ui.icon("send", size="18px")
                with ui.column().classes("gap-0"):
                    ui.label("Paperplane").classes("text-base font-bold leading-tight m3-text")
                    ui.label("Telegram 媒体长期挂机下载器").classes("text-xs m3-muted leading-tight")

            with ui.row().classes("items-center gap-3"):
                header_chip = ui.chip("空闲待命", icon="pause_circle", color="grey-6", text_color="white").props("dense")
                dark_btn = ui.button(icon="light_mode" if saved_dark else "dark_mode").props("flat round dense")

                def toggle_dark():
                    dark.toggle()
                    _save_dark_mode(cfg_path, bool(dark.value))
                    dark_btn.props(f"icon={'light_mode' if dark.value else 'dark_mode'}")

                dark_btn.on_click(toggle_dark)

        # ==================== 左侧 Material 3 导航抽屉 ====================
        with ui.left_drawer(value=True, fixed=True).classes("m3-drawer p-3").props("width=240") as drawer:
            ui.label("导航菜单").classes("text-xs font-semibold uppercase tracking-wider m3-muted px-3 pt-2 pb-2")
            nav_items = [
                ("dashboard", "挂机控制台", "dashboard"),
                ("channels", "频道与预览", "subscriptions"),
                ("config", "配置中心", "tune"),
                ("account", "账号与登录", "verified_user"),
            ]
            with ui.column().classes("w-full gap-1"):
                for key, label, icon in nav_items:
                    btn = ui.button(label, icon=icon, on_click=lambda k=key: switch_page(k)).props("flat no-caps").classes(
                        "w-full m3-nav-btn"
                    )
                    nav_buttons[key] = btn

            ui.space()
            with ui.column().classes("w-full m3-subcard p-3 gap-0.5"):
                ui.label("当前配置文件").classes("text-xs m3-muted")
                ui.label(cfg_path.name).classes("text-sm font-semibold truncate m3-text")
                ui.label(str(cfg_path.parent)).classes("text-[11px] m3-muted break-all")

        # ==================== 主内容区 ====================
        callbacks: dict[str, object] = {}

        def _on_channels_page_change(*, refresh_preview: bool = True):
            if fn := callbacks.get("reload_cfg_form"):
                fn()
            if fn := callbacks.get("reload_scope_picker"):
                fn()
            if refresh_preview and (fn := callbacks.get("do_preview")):
                fn()

        def _on_config_saved():
            if fn := callbacks.get("do_preview"):
                fn()
            if fn := callbacks.get("reload_scope_picker"):
                fn()

        with ui.column().classes("w-full max-w-7xl mx-auto p-4 md:p-6 gap-5"):
            page_dash, reload_scope_picker = build_dashboard_page(
                ui,
                cfg_path=cfg_path,
                runner=runner,
                header_chip=header_chip,
                render_avatar=render_avatar,
            )
            callbacks["reload_scope_picker"] = reload_scope_picker
            pages["dashboard"] = page_dash

            page_channels, do_preview = build_channels_page(
                ui,
                cfg_path=cfg_path,
                render_avatar=render_avatar,
                on_config_changed=_on_channels_page_change,
            )
            callbacks["do_preview"] = do_preview
            pages["channels"] = page_channels

            page_config, reload_cfg_form = build_config_page(
                ui,
                cfg_path=cfg_path,
                render_avatar=render_avatar,
                on_saved=_on_config_saved,
            )
            callbacks["reload_cfg_form"] = reload_cfg_form
            pages["config"] = page_config

            pages["account"] = build_auth_page(
                ui,
                cfg_path=cfg_path,
                auth_holder=auth_holder,
            )

        switch_page("dashboard")

    return ui


def launch_gui(config_path: str | Path, *, host: str = "127.0.0.1", port: int = 8765, native: bool = False):
    ui = build_gui_app(config_path)
    ui.run(
        host=host,
        port=port,
        native=native,
        reload=False,
        show=not native,
        title="Paperplane",
    )
