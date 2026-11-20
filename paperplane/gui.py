"""现代化 Material Design 3 风格 GUI（基于 NiceGUI / Quasar），支持 Windows / macOS 双端长期挂机。"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from fastapi.responses import FileResponse, Response

from .config import DEFAULT_PATH_TEMPLATE, MEDIA_TYPES, PATH_TEMPLATE_VARS, ConfigError, load_config, load_raw_config, save_raw_config
from .core import _human, render_folder
from .service import AuthSession, LongRunningRunner, fetch_dialogs, preview_matches
from .store import Store

log = logging.getLogger("paperplane.gui")

STATUS_META = {
    "idle": ("空闲待命", "grey-6", "pause_circle"),
    "starting": ("正在启动", "blue-6", "hourglass_top"),
    "running": ("挂机运行中", "positive", "bolt"),
    "reconnecting": ("断线重连中", "warning", "sync_problem"),
    "stopping": ("正在停止", "orange-7", "stop_circle"),
    "error": ("异常停止", "negative", "error"),
}

AVATAR_PALETTE = [
    "#4F46E5", "#0EA5E9", "#10B981", "#F59E0B",
    "#EC4899", "#8B5CF6", "#14B8A6", "#F97316",
]

M3_HEAD_CSS = """
<style>
  :root {
    --m3-bg: #F4F6FA;
    --m3-surface: #FFFFFF;
    --m3-surface-2: #F8FAFC;
    --m3-border: rgba(148, 163, 184, 0.28);
    --m3-text: #0F172A;
    --m3-muted: #64748B;
    --m3-hover: rgba(15, 23, 42, 0.04);
    --m3-nav-active-bg: rgba(79, 70, 229, 0.12);
    --m3-nav-active-fg: #4F46E5;
    --m3-warn-bg: rgba(245, 158, 11, 0.12);
    --m3-warn-fg: #B45309;
    --m3-kind-bg: rgba(79, 70, 229, 0.10);
    --m3-kind-fg: #4338CA;
  }
  body.body--dark {
    --m3-bg: #0B1120;
    --m3-surface: #1E293B;
    --m3-surface-2: #162032;
    --m3-border: rgba(148, 163, 184, 0.16);
    --m3-text: #F1F5F9;
    --m3-muted: #94A3B8;
    --m3-hover: rgba(241, 245, 249, 0.06);
    --m3-nav-active-bg: rgba(129, 140, 248, 0.22);
    --m3-nav-active-fg: #A5B4FC;
    --m3-warn-bg: rgba(245, 158, 11, 0.16);
    --m3-warn-fg: #FCD34D;
    --m3-kind-bg: rgba(129, 140, 248, 0.18);
    --m3-kind-fg: #C7D2FE;
  }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Microsoft YaHei', sans-serif;
    background: var(--m3-bg) !important;
    color: var(--m3-text) !important;
  }
  .m3-header {
    background: var(--m3-surface) !important;
    color: var(--m3-text) !important;
    border-bottom: 1px solid var(--m3-border) !important;
  }
  .m3-drawer {
    background: var(--m3-surface) !important;
    color: var(--m3-text) !important;
    border-right: 1px solid var(--m3-border) !important;
  }
  .m3-card {
    background: var(--m3-surface) !important;
    color: var(--m3-text) !important;
    border-radius: 16px !important;
    border: 1px solid var(--m3-border) !important;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05), 0 4px 12px rgba(15, 23, 42, 0.04) !important;
  }
  .m3-subcard {
    background: var(--m3-surface-2) !important;
    color: var(--m3-text) !important;
    border-radius: 12px !important;
    border: 1px solid var(--m3-border) !important;
  }
  .m3-text {
    color: var(--m3-text) !important;
  }
  .m3-muted {
    color: var(--m3-muted) !important;
  }
  .m3-nav-btn {
    color: var(--m3-text) !important;
    border-radius: 9999px !important;
    justify-content: flex-start !important;
    padding: 10px 16px !important;
    font-weight: 500 !important;
    text-transform: none !important;
  }
  .m3-nav-btn:hover {
    background: var(--m3-hover) !important;
  }
  .m3-nav-active {
    background: var(--m3-nav-active-bg) !important;
    color: var(--m3-nav-active-fg) !important;
    font-weight: 600 !important;
  }
  .m3-row-item {
    border-radius: 12px;
    transition: background 0.15s ease;
  }
  .m3-row-item:hover {
    background: var(--m3-hover);
  }
  .m3-expansion {
    background: var(--m3-surface-2) !important;
    color: var(--m3-text) !important;
    border: 1px solid var(--m3-border) !important;
    border-radius: 14px !important;
    overflow: hidden;
  }
  .m3-warn-box {
    background: var(--m3-warn-bg) !important;
    color: var(--m3-warn-fg) !important;
    border-radius: 12px;
  }
  .m3-kind-badge {
    background: var(--m3-kind-bg) !important;
    color: var(--m3-kind-fg) !important;
    border-radius: 9999px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
  }
  .m3-log-box {
    background: #090E1A;
    color: #CBD5E1;
    border: 1px solid var(--m3-border);
    border-radius: 12px;
    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
    font-size: 12px;
    line-height: 1.55;
    padding: 14px;
    height: 360px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .m3-pill {
    border-radius: 9999px !important;
    text-transform: none !important;
    font-weight: 500 !important;
  }
  .m3-avatar-img {
    width: 38px;
    height: 38px;
    border-radius: 9999px;
    object-fit: cover;
    flex-shrink: 0;
    border: 1px solid var(--m3-border);
  }
  .m3-avatar-fallback {
    width: 38px;
    height: 38px;
    border-radius: 9999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: #FFFFFF;
    font-weight: 700;
    font-size: 15px;
    flex-shrink: 0;
    user-select: none;
  }
  body.body--dark .q-table,
  body.body--dark .q-table__card {
    background: transparent !important;
    color: var(--m3-text) !important;
  }
</style>
"""


def _fmt_uptime(started_at: float | None) -> str:
    if not started_at:
        return "00:00:00"
    sec = max(0, int(time.time() - started_at))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _avatar_color(seed: int | str) -> str:
    return AVATAR_PALETTE[abs(hash(str(seed))) % len(AVATAR_PALETTE)]


def _initial_char(name: str) -> str:
    s = (name or "?").strip().lstrip("@")
    return s[0].upper() if s else "?"


def _avatar_file_for(cfg_path: Path, chat_id: int | None) -> Path | None:
    if chat_id is None:
        return None
    try:
        raw = load_raw_config(cfg_path) if cfg_path.exists() else {}
    except ConfigError:
        raw = {}
    dl = raw.get("download", {})
    data_dir = (cfg_path.parent / dl.get("data_dir", "data")).resolve()
    p = data_dir / "avatars" / f"{chat_id}.jpg"
    return p if p.exists() else None


def build_gui_app(config_path: str | Path):
    """构建 Material Design 3 风格 NiceGUI 应用（关闭浏览器标签页不影响后台长期挂机）。"""
    try:
        from nicegui import app, ui
    except ImportError as e:
        raise SystemExit(
            f"缺少 GUI 依赖（{e.name}），请运行：pip install nicegui tomli-w"
        ) from e

    cfg_path = Path(config_path).resolve()
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
            bg = _avatar_color(chat_id if chat_id is not None else name)
            ch = _initial_char(name).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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
        dark = ui.dark_mode(value=False)

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
                dark_btn = ui.button(icon="dark_mode").props("flat round dense")

                def toggle_dark():
                    dark.toggle()
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
        with ui.column().classes("w-full max-w-7xl mx-auto p-4 md:p-6 gap-5"):

            # --------------------------------------------------
            # 页面 1：挂机控制台（Dashboard）
            # --------------------------------------------------
            with ui.column().classes("w-full gap-5") as page_dash:
                pages["dashboard"] = page_dash

                with ui.grid(columns="repeat(auto-fit, minmax(210px, 1fr))").classes("w-full gap-4"):
                    with ui.card().classes("m3-card p-4"):
                        with ui.row().classes("items-center justify-between w-full"):
                            with ui.column().classes("gap-1"):
                                ui.label("运行状态").classes("text-xs m3-muted")
                                kpi_status = ui.label("空闲待命").classes("text-xl font-bold m3-text")
                                kpi_mode = ui.label("模式：-").classes("text-xs m3-muted")
                            with ui.avatar(color="indigo-6", text_color="white", size="42px"):
                                ui.icon("monitor_heart", size="22px")

                    with ui.card().classes("m3-card p-4"):
                        with ui.row().classes("items-center justify-between w-full"):
                            with ui.column().classes("gap-1"):
                                ui.label("挂机运行时长").classes("text-xs m3-muted")
                                kpi_uptime = ui.label("00:00:00").classes("text-xl font-bold font-mono m3-text")
                                ui.label("关闭页面后台持续运行").classes("text-xs m3-muted")
                            with ui.avatar(color="light-blue-6", text_color="white", size="42px"):
                                ui.icon("schedule", size="22px")

                    with ui.card().classes("m3-card p-4"):
                        with ui.row().classes("items-center justify-between w-full"):
                            with ui.column().classes("gap-1"):
                                ui.label("本次完成文件").classes("text-xs m3-muted")
                                kpi_done = ui.label("0").classes("text-xl font-bold m3-text")
                                ui.label("支持断点续传").classes("text-xs m3-muted")
                            with ui.avatar(color="green-6", text_color="white", size="42px"):
                                ui.icon("download_done", size="22px")

                    with ui.card().classes("m3-card p-4"):
                        with ui.row().classes("items-center justify-between w-full"):
                            with ui.column().classes("gap-1"):
                                ui.label("自动重连次数").classes("text-xs m3-muted")
                                kpi_reconn = ui.label("0").classes("text-xl font-bold m3-text")
                                ui.label("指数退避防风控").classes("text-xs m3-muted")
                            with ui.avatar(color="amber-7", text_color="white", size="42px"):
                                ui.icon("autorenew", size="22px")

                # 控制操作栏
                with ui.card().classes("w-full m3-card p-4"):
                    with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
                        with ui.row().classes("items-center gap-2 flex-wrap"):
                            btn_watch = ui.button("启动长期挂机", icon="play_arrow", color="primary").props("unelevated").classes("m3-pill px-5")
                            btn_dl = ui.button("单次下载", icon="download", color="secondary").props("unelevated").classes("m3-pill px-4")
                            btn_scan = ui.button("仅扫描缓存", icon="search").props("outline color=primary").classes("m3-pill px-4")
                            btn_stop = ui.button("停止任务", icon="stop", color="negative").props("unelevated").classes("m3-pill px-4")
                        catchup_input = ui.number(
                            label="定时补扫间隔（分钟）", value=30, min=5, max=720, step=5
                        ).props("outlined dense").classes("w-48")
                    err_banner = ui.label("").classes("text-xs text-negative mt-2")
                    err_banner.set_visibility(False)

                # 运行范围选择（勾选本次下载的频道与关键词）
                scope_controls: list[dict] = []
                with ui.card().classes("w-full m3-card p-4"):
                    with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 mb-3"):
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("checklist", color="primary", size="20px")
                            ui.label("运行范围（勾选频道与关键词）").classes("text-base font-semibold m3-text")
                            ui.label("仅影响本次运行，不修改 config.toml").classes("text-xs m3-muted")
                        with ui.row().classes("items-center gap-1"):
                            ui.button("全选", on_click=lambda: set_all_scope(True)).props("flat dense size=sm color=primary").classes("m3-pill")
                            ui.button("全不选", on_click=lambda: set_all_scope(False)).props("flat dense size=sm").classes("m3-pill")
                            ui.button("刷新", icon="refresh", on_click=lambda: reload_scope_picker()).props("flat dense size=sm").classes("m3-pill")
                    scope_box = ui.column().classes("w-full gap-2")

                def set_all_scope(val: bool):
                    for item in scope_controls:
                        item["ch_cb"].set_value(val)
                        for kw_cb, _ in item["kw_cbs"]:
                            kw_cb.set_value(val)

                def reload_scope_picker():
                    scope_box.clear()
                    scope_controls.clear()
                    try:
                        cfg_now = load_config(cfg_path)
                    except ConfigError as e:
                        with scope_box:
                            ui.label(f"请先在「配置中心」完成配置：{e}").classes("text-xs text-negative")
                        return

                    with scope_box:
                        for idx, ch in enumerate(cfg_now.channels):
                            chat_int = int(ch.chat) if str(ch.chat).lstrip("-").isdigit() else None
                            title = ch.name or str(ch.chat)
                            # 收集该频道的所有关键词及其对应媒体类型
                            kw_media: dict[str, set[str]] = {}
                            if ch.rules:
                                for r in ch.rules:
                                    if not r.include:
                                        kw_media.setdefault("", set()).update(r.media)
                                    else:
                                        for p in r.include:
                                            kw_media.setdefault(p, set()).update(r.media)
                            else:
                                if not ch.include:
                                    kw_media[""] = set(ch.media)
                                else:
                                    for p in ch.include:
                                        kw_media[p] = set(ch.media)

                            with ui.column().classes("w-full m3-subcard p-3 gap-2"):
                                with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                                    with ui.row().classes("items-center gap-2"):
                                        ch_cb = ui.checkbox(value=True).props("dense")
                                        render_avatar(chat_int, title)
                                        ui.label(title).classes("text-sm font-semibold m3-text")
                                        ui.html(
                                            f'<span class="m3-kind-badge">{ch.scan_mode}</span>',
                                            sanitize=False,
                                        )
                                    ui.label(str(ch.chat)).classes("text-xs font-mono m3-muted")

                                kw_cbs: list[tuple] = []
                                with ui.row().classes("w-full items-center gap-3 flex-wrap pl-8"):
                                    for kw, mset in kw_media.items():
                                        m_tag = "/".join(sorted(mset))
                                        lbl = f"{kw} ({m_tag})" if kw else f"全部消息 ({m_tag})"
                                        k_cb = ui.checkbox(lbl, value=True).props("dense")
                                        k_cb.bind_enabled_from(ch_cb, "value")
                                        kw_cbs.append((k_cb, kw))

                                scope_controls.append({"idx": idx, "ch_cb": ch_cb, "kw_cbs": kw_cbs})

                reload_scope_picker()

                # 下方双栏：实时下载队列 + 实时日志终端
                with ui.grid(columns="repeat(auto-fit, minmax(360px, 1fr))").classes("w-full gap-4"):
                    with ui.card().classes("m3-card p-4"):
                        with ui.row().classes("w-full items-center justify-between mb-3"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("downloading", color="primary", size="20px")
                                ui.label("实时下载队列").classes("text-base font-semibold m3-text")
                            dl_count_badge = ui.badge("0 项", color="primary").props("outline")
                        progress_box = ui.column().classes("w-full gap-3")

                    with ui.card().classes("m3-card p-4"):
                        with ui.row().classes("w-full items-center justify-between mb-3"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("terminal", color="secondary", size="20px")
                                ui.label("实时运行日志").classes("text-base font-semibold m3-text")
                            ui.button("清空视图", icon="delete_sweep", on_click=lambda: runner.logs.clear()).props("flat dense size=sm")
                        log_view = ui.html("", sanitize=False).classes("w-full m3-log-box")

                async def start_mode(mode: str):
                    if runner.is_running:
                        ui.notify("已有任务正在运行，请先停止", type="warning")
                        return
                    try:
                        load_config(cfg_path)
                    except ConfigError as e:
                        ui.notify(f"配置校验失败：{e}", type="negative")
                        return

                    selected_scope: dict[int, set[str] | None] | None = None
                    if scope_controls:
                        selected_scope = {}
                        for item in scope_controls:
                            if not item["ch_cb"].value:
                                continue
                            picked_kws = {kw for k_cb, kw in item["kw_cbs"] if k_cb.value}
                            if picked_kws:
                                selected_scope[item["idx"]] = picked_kws
                        if not selected_scope:
                            ui.notify("请至少勾选一个频道及至少一个关键词", type="warning")
                            return

                    runner.catchup_interval = float(catchup_input.value or 30) * 60.0
                    runner.start(mode, selected_scope=selected_scope)
                    cnt = len(selected_scope) if selected_scope is not None else "全部"
                    ui.notify(f"已启动模式：{mode}（已选 {cnt} 个频道）", type="positive")

                async def stop_task():
                    if not runner.is_running:
                        return
                    ui.notify("正在优雅停止并保存进度...", type="info")
                    await runner.stop()
                    ui.notify("任务已停止", type="positive")

                btn_watch.on_click(lambda: start_mode("watch"))
                btn_dl.on_click(lambda: start_mode("download"))
                btn_scan.on_click(lambda: start_mode("scan"))
                btn_stop.on_click(stop_task)

                last_log_snapshot = {"text": ""}

                def refresh_dashboard():
                    text, color, icon = STATUS_META.get(runner.status, (runner.status, "grey-6", "info"))
                    header_chip.set_text(text)
                    header_chip.props(f"color={color} icon={icon}")

                    running = runner.is_running
                    btn_watch.set_enabled(not running)
                    btn_dl.set_enabled(not running)
                    btn_scan.set_enabled(not running)
                    btn_stop.set_enabled(running)

                    kpi_status.set_text(text)
                    kpi_mode.set_text(f"模式：{runner.mode or '-'}")
                    kpi_uptime.set_text(_fmt_uptime(runner.started_at) if running else "00:00:00")
                    kpi_done.set_text(str(runner.done_count))
                    kpi_reconn.set_text(str(runner.reconnects))

                    if runner.last_error:
                        err_banner.set_text(f"最近异常：{runner.last_error}")
                        err_banner.set_visibility(True)
                    else:
                        err_banner.set_visibility(False)

                    items = sorted(runner.downloads.values(), key=lambda x: x.updated_at, reverse=True)[:10]
                    dl_count_badge.set_text(f"{len(items)} 项")
                    progress_box.clear()
                    with progress_box:
                        if not items:
                            with ui.column().classes("w-full items-center justify-center py-10 m3-muted"):
                                ui.icon("cloud_queue", size="40px")
                                ui.label("暂无活跃下载任务").classes("text-sm mt-1")
                        for item in items:
                            ratio = (item.done / item.total) if item.total > 0 else (1.0 if item.status == "done" else 0.0)
                            pct = int(min(max(ratio, 0.0), 1.0) * 100)
                            chip_color = {"done": "positive", "failed": "negative", "skipped": "warning"}.get(item.status, "primary")
                            with ui.column().classes("w-full p-3 m3-subcard gap-1.5"):
                                with ui.row().classes("w-full items-center justify-between no-wrap gap-2"):
                                    ui.label(f"#{item.msg_id} · {item.name}").classes("text-xs font-medium truncate m3-text")
                                    ui.badge(f"{item.status} {pct}%", color=chip_color).props("rounded")
                                ui.linear_progress(value=min(max(ratio, 0.0), 1.0), show_value=False, color=chip_color).props("rounded size=6px")
                                ui.label(f"{_human(item.done)} / {_human(item.total)}").classes("text-[11px] m3-muted self-end")

                    raw_logs = "\n".join(list(runner.logs)[-100:])
                    if raw_logs != last_log_snapshot["text"]:
                        last_log_snapshot["text"] = raw_logs
                        safe_html = (
                            raw_logs.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                            if raw_logs
                            else "<span style='color:#64748B'>等待任务启动后输出日志...</span>"
                        )
                        log_view.set_content(safe_html)

                ui.timer(1.0, refresh_dashboard)

            # --------------------------------------------------
            # 页面 2：频道与匹配预览
            # --------------------------------------------------
            with ui.column().classes("w-full gap-5") as page_channels:
                pages["channels"] = page_channels
                page_channels.set_visibility(False)

                with ui.card().classes("w-full m3-card p-5"):
                    with ui.row().classes("w-full items-center justify-between flex-wrap gap-3 mb-3"):
                        with ui.column().classes("gap-0"):
                            ui.label("离线匹配预览").classes("text-lg font-semibold m3-text")
                            ui.label("基于本地 SQLite 缓存实时匹配，不产生任何 Telegram API 请求").classes("text-xs m3-muted")
                        ui.button("刷新匹配预览", icon="refresh", color="primary", on_click=lambda: do_preview()).props("unelevated").classes("m3-pill")
                    preview_box = ui.column().classes("w-full gap-4")

                def do_preview():
                    preview_box.clear()
                    try:
                        cfg = load_config(cfg_path)
                    except ConfigError as e:
                        with preview_box:
                            ui.label(f"配置错误：{e}").classes("text-negative")
                        return
                    store = Store(cfg.db_path)
                    try:
                        summaries = preview_matches(cfg, store)
                    finally:
                        store.close()

                    with preview_box:
                        for s in summaries:
                            if not s.scanned:
                                with ui.row().classes("items-center gap-2 p-3 m3-warn-box"):
                                    ui.icon("info")
                                    ui.label(f"{s.chat_key}：尚未扫描，请先在「挂机控制台」点击「仅扫描缓存」")
                                continue
                            with ui.expansion(value=True).classes("w-full m3-expansion") as exp:
                                with exp.add_slot("header"):
                                    with ui.row().classes("w-full items-center gap-3 py-1"):
                                        render_avatar(s.chat_id, s.title)
                                        with ui.column().classes("gap-0"):
                                            ui.label(s.title).classes("text-sm font-semibold m3-text")
                                            ui.label(
                                                f"命中 {len(s.items)} 个 · {_human(s.total_bytes)} · 已完成 {s.done_count}"
                                            ).classes("text-xs m3-muted")
                                rows = [
                                    {
                                        "done": "已完成" if m.msg_id in s.done_ids else "待下载",
                                        "msg_id": m.msg_id,
                                        "date": datetime.fromtimestamp(m.date).strftime("%Y-%m-%d"),
                                        "type": m.media_type or "-",
                                        "size": _human(m.size),
                                        "name": m.file_name or "-",
                                        "text": (m.text or "").replace("\n", " ")[:48],
                                    }
                                    for m in s.items[:100]
                                ]
                                cols = [
                                    {"name": "done", "label": "状态", "field": "done", "align": "left"},
                                    {"name": "msg_id", "label": "ID", "field": "msg_id", "align": "left"},
                                    {"name": "date", "label": "日期", "field": "date", "align": "left"},
                                    {"name": "type", "label": "类型", "field": "type", "align": "left"},
                                    {"name": "size", "label": "大小", "field": "size", "align": "right"},
                                    {"name": "name", "label": "文件名", "field": "name", "align": "left"},
                                    {"name": "text", "label": "文本摘要", "field": "text", "align": "left"},
                                ]
                                ui.table(columns=cols, rows=rows, row_key="msg_id", pagination=10).props("flat dense").classes("w-full")

                do_preview()

                with ui.card().classes("w-full m3-card p-5"):
                    with ui.row().classes("w-full items-center justify-between flex-wrap gap-3 mb-3"):
                        with ui.column().classes("gap-0"):
                            ui.label("在线拉取 Telegram 频道列表").classes("text-lg font-semibold m3-text")
                            ui.label("自动缓存频道头像到本地 data/avatars/，一键加入监控配置").classes("text-xs m3-muted")
                        with ui.row().classes("items-center gap-2"):
                            dialog_search = ui.input(placeholder="搜索名称 / 用户名 / ID...").props("outlined dense clearable").classes("w-60")
                            btn_dialogs = ui.button("拉取对话与头像", icon="cloud_download", color="secondary").props("unelevated").classes("m3-pill")
                    dialogs_cache: list = []
                    dialogs_box = ui.column().classes("w-full gap-1")

                def render_dialogs():
                    dialogs_box.clear()
                    kw = (dialog_search.value or "").strip().lower()
                    filtered = [
                        d for d in dialogs_cache
                        if not kw or kw in d.name.lower() or kw in str(d.id) or (d.username and kw in d.username.lower())
                    ]

                    def add_channel(d):
                        raw = load_raw_config(cfg_path) if cfg_path.exists() else {}
                        chs = list(raw.get("channels", []))
                        if any(str(c.get("chat")) == str(d.id) for c in chs):
                            ui.notify(f"频道 {d.name} 已在配置中", type="info")
                            return
                        chs.append({
                            "chat": d.id,
                            "name": d.name,
                            "include": [],
                            "exclude": [],
                            "media": ["video", "document"],
                            "min_size": "10MB",
                            "max_size": 0,
                            "context_before": 1,
                            "follow_replies": True,
                        })
                        raw["channels"] = chs
                        save_raw_config(cfg_path, raw)
                        ui.notify(f"已加入配置：{d.name}（可前往「配置中心」设置关键词）", type="positive")
                        reload_cfg_form()
                        reload_scope_picker()

                    with dialogs_box:
                        if not filtered:
                            ui.label("点击「拉取对话与头像」加载你的 Telegram 频道与群组").classes("text-xs m3-muted py-4")
                        for d in filtered[:80]:
                            with ui.row().classes("w-full items-center justify-between px-3 py-2 m3-row-item no-wrap"):
                                with ui.row().classes("items-center gap-3 min-w-0"):
                                    render_avatar(d.id, d.name, has_avatar=bool(d.avatar_path))
                                    with ui.column().classes("gap-0.5 min-w-0"):
                                        with ui.row().classes("items-center gap-2"):
                                            ui.label(d.name).classes("text-sm font-medium truncate m3-text")
                                            ui.html(f'<span class="m3-kind-badge">{d.kind}</span>', sanitize=False)
                                        sub = (f"@{d.username} · " if d.username else "") + str(d.id)
                                        ui.label(sub).classes("text-xs font-mono m3-muted")
                                ui.button("加入配置", icon="add", on_click=lambda d=d: add_channel(d)).props("flat dense color=primary").classes("m3-pill shrink-0")

                dialog_search.on_value_change(lambda _: render_dialogs())

                async def load_dialogs_ui():
                    btn_dialogs.props("loading")
                    try:
                        cfg = load_config(cfg_path)
                        dialogs_cache.clear()
                        dialogs_cache.extend(await fetch_dialogs(cfg, with_avatars=True))
                        render_dialogs()
                        do_preview()
                        reload_cfg_form()
                        ui.notify(f"已加载 {len(dialogs_cache)} 个对话（头像已缓存）", type="positive")
                    except (ConfigError, Exception) as e:
                        ui.notify(f"拉取失败：{e}", type="negative")
                    finally:
                        btn_dialogs.props(remove="loading")

                btn_dialogs.on_click(load_dialogs_ui)
                render_dialogs()

            # --------------------------------------------------
            # 页面 3：可视化配置中心
            # --------------------------------------------------
            with ui.column().classes("w-full gap-5") as page_config:
                pages["config"] = page_config
                page_config.set_visibility(False)

                cfg_container = ui.column().classes("w-full gap-4")

                def reload_cfg_form():
                    cfg_container.clear()
                    try:
                        raw = load_raw_config(cfg_path) if cfg_path.exists() else {}
                    except ConfigError as e:
                        raw = {}
                        ui.notify(f"读取配置出错：{e}", type="warning")

                    tg = raw.get("telegram", {})
                    dl = raw.get("download", {})
                    px = raw.get("proxy", {}) or {}
                    chs = list(raw.get("channels", []))
                    if not chs:
                        chs = [{
                            "chat": "@some_public_channel",
                            "include": [],
                            "exclude": [],
                            "media": ["video"],
                            "min_size": "10MB",
                            "max_size": 0,
                            "context_before": 1,
                            "follow_replies": True,
                        }]

                    ch_editors: list[dict] = []

                    def add_blank_channel():
                        raw_now = load_raw_config(cfg_path) if cfg_path.exists() else dict(raw)
                        cur = list(raw_now.get("channels", chs))
                        cur.append({
                            "chat": "",
                            "include": [],
                            "exclude": [],
                            "media": ["video", "document"],
                            "min_size": "10MB",
                            "max_size": 0,
                            "context_before": 1,
                            "follow_replies": True,
                        })
                        raw_now["channels"] = cur
                        save_raw_config(cfg_path, raw_now)
                        reload_cfg_form()

                    def remove_channel(idx: int):
                        raw_now = load_raw_config(cfg_path) if cfg_path.exists() else dict(raw)
                        cur = list(raw_now.get("channels", chs))
                        if len(cur) <= 1:
                            ui.notify("至少需要保留一个频道配置", type="warning")
                            return
                        cur.pop(idx)
                        raw_now["channels"] = cur
                        save_raw_config(cfg_path, raw_now)
                        ui.notify("已移除该频道", type="info")
                        reload_cfg_form()

                    with cfg_container:
                        with ui.card().classes("w-full m3-card p-5"):
                            ui.label("API 凭证与下载设置").classes("text-base font-semibold mb-3 m3-text")
                            with ui.row().classes("w-full gap-4 flex-wrap"):
                                in_api_id = ui.input("api_id", value=str(tg.get("api_id", ""))).props("outlined dense").classes("w-44")
                                in_api_hash = ui.input("api_hash", value=str(tg.get("api_hash", ""))).props("outlined dense").classes("w-80")
                                in_dl_dir = ui.input("下载保存目录", value=str(dl.get("dir", "downloads"))).props("outlined dense").classes("w-56")
                                in_conc = ui.number("并发下载数", value=int(dl.get("concurrency", 3)), min=1, max=10).props("outlined dense").classes("w-32")

                            ui.separator().classes("my-4")
                            ui.label("自定义下载路径规则").classes("text-base font-semibold mb-1 m3-text")
                            ui.label(
                                "可用变量：{include}（关键词） · {media_type}（文件类型） · {channel}（频道名） · {date}（YYYY-MM-DD） · {month}（YYYY-MM） · {year}（YYYY）"
                            ).classes("text-xs m3-muted mb-2")
                            in_path_tpl = ui.input(
                                "子目录路径模板（用 / 分隔层级，留空则直接存在下载根目录）",
                                value=str(dl.get("path_template", DEFAULT_PATH_TEMPLATE)),
                            ).props("outlined dense").classes("w-full max-w-2xl")
                            path_preview_lbl = ui.label("").classes("text-xs font-mono m3-muted mt-1")

                            def update_path_preview():
                                tpl = (in_path_tpl.value or "").strip()
                                base_dir = Path((in_dl_dir.value or "downloads").strip() or "downloads")
                                try:
                                    sample = render_folder(
                                        base_dir,
                                        tpl,
                                        include="#4K 视频",
                                        media_type="video",
                                        channel="示例频道",
                                        ts=int(time.time()),
                                    ) / "1024_sample.mp4"
                                    path_preview_lbl.set_text(f"示例落盘路径：{sample.as_posix()}")
                                    path_preview_lbl.classes(remove="text-negative", add="m3-muted")
                                except (KeyError, ValueError) as e:
                                    path_preview_lbl.set_text(
                                        f"模板变量无效：{e}（可用：{', '.join('{' + k + '}' for k in PATH_TEMPLATE_VARS)}）"
                                    )
                                    path_preview_lbl.classes(remove="m3-muted", add="text-negative")

                            in_path_tpl.on_value_change(lambda _: update_path_preview())
                            in_dl_dir.on_value_change(lambda _: update_path_preview())

                            presets = [
                                ("关键词 / 文件类型（默认）", "{include}/{media_type}"),
                                ("文件类型 / 关键词", "{media_type}/{include}"),
                                ("关键词 / 年月", "{include}/{month}"),
                                ("频道 / 关键词 / 文件类型", "{channel}/{include}/{media_type}"),
                                ("仅关键词目录", "{include}"),
                            ]
                            with ui.row().classes("w-full gap-2 flex-wrap mt-2"):
                                for p_label, p_val in presets:
                                    ui.button(
                                        p_label,
                                        on_click=lambda v=p_val: (in_path_tpl.set_value(v), update_path_preview()),
                                    ).props("outline dense size=sm color=primary").classes("m3-pill")
                            update_path_preview()

                            ui.separator().classes("my-4")
                            ui.label("网络代理设置").classes("text-base font-semibold mb-2 m3-text")
                            with ui.row().classes("w-full gap-4 items-center flex-wrap"):
                                in_px_on = ui.switch("启用代理", value=bool(raw.get("proxy")))
                                in_px_type = ui.select(["socks5", "http", "mtproxy"], value=px.get("type", "socks5"), label="代理类型").props("outlined dense").classes("w-36")
                                in_px_host = ui.input("代理主机", value=str(px.get("host", "127.0.0.1"))).props("outlined dense").classes("w-44")
                                in_px_port = ui.number("代理端口", value=int(px.get("port", 7890)), min=1, max=65535).props("outlined dense").classes("w-32")
                                in_px_secret = ui.input("MTProxy secret（可选）", value=str(px.get("secret", ""))).props("outlined dense").classes("w-60")

                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label(f"监控频道列表（{len(chs)}）").classes("text-base font-semibold m3-text")
                            ui.button("新增频道", icon="add", on_click=add_blank_channel).props("outline color=primary").classes("m3-pill")

                        for idx, c in enumerate(chs):
                            chat_raw = c.get("chat", "")
                            chat_int = int(chat_raw) if str(chat_raw).lstrip("-").isdigit() else None
                            title_label = c.get("name") or str(chat_raw) or "未填写"
                            init_rules = list(c.get("rules") or [])
                            if not init_rules:
                                init_rules = [{
                                    "include": list(c.get("include", [])),
                                    "media": list(c.get("media", ["video", "document"])),
                                }]

                            with ui.card().classes("w-full m3-card p-5"):
                                with ui.row().classes("w-full items-center justify-between mb-3"):
                                    with ui.row().classes("items-center gap-3"):
                                        render_avatar(chat_int, title_label)
                                        ui.label(f"频道 #{idx + 1} · {title_label}").classes("text-sm font-semibold text-primary")
                                    ui.button("删除频道", icon="delete", color="negative", on_click=lambda i=idx: remove_channel(i)).props("flat dense size=sm")
                                with ui.row().classes("w-full gap-3 flex-wrap"):
                                    e_chat = ui.input("chat（数字 ID 或 @用户名）", value=str(chat_raw)).props("outlined dense").classes("w-56")
                                    e_name = ui.input("保存子目录名（可选）", value=str(c.get("name", "") or "")).props("outlined dense").classes("w-48")
                                    e_exc = ui.input("exclude 排除正则（逗号分隔）", value=", ".join(c.get("exclude", []))).props("outlined dense").classes("w-64")
                                    e_scan_mode = ui.select(
                                        {"full": "全量遍历 (full)", "search": "频道内搜索 (search)"},
                                        value=c.get("scan_mode", "full") if c.get("scan_mode") in ("full", "search") else "full",
                                        label="扫描模式",
                                    ).props("outlined dense").classes("w-48")
                                with ui.row().classes("w-full gap-3 items-center flex-wrap mt-2"):
                                    e_min = ui.input("最小体积", value=str(c.get("min_size", "10MB"))).props("outlined dense").classes("w-28")
                                    e_max = ui.input("最大体积(0不限)", value=str(c.get("max_size", 0))).props("outlined dense").classes("w-32")
                                    e_since = ui.input("since (YYYY-MM-DD)", value=str(c.get("since", "") or "")).props("outlined dense").classes("w-40")
                                    e_until = ui.input("until (YYYY-MM-DD)", value=str(c.get("until", "") or "")).props("outlined dense").classes("w-40")
                                    e_ctx = ui.number("前文条数", value=int(c.get("context_before", 1)), min=0, max=20).props("outlined dense").classes("w-24")
                                    e_reply = ui.checkbox("跟踪回复", value=bool(c.get("follow_replies", True)))
                                e_search_q = ui.input(
                                    "搜索模式自定义词（仅 search 模式生效，逗号分隔；留空则自动从 include 提取）",
                                    value=", ".join(c.get("search_queries", [])),
                                ).props("outlined dense").classes("w-full mt-2")
                                e_search_q.bind_visibility_from(e_scan_mode, "value", backward=lambda v: v == "search")

                                # 同一频道多组 include -> media 规则
                                rule_editors: list[dict] = []
                                with ui.column().classes("w-full m3-subcard p-3 mt-3 gap-2"):
                                    with ui.row().classes("w-full items-center justify-between"):
                                        ui.label("关键词与媒体类型规则（不同 include 可下载不同媒体）").classes("text-xs font-semibold m3-muted")
                                        btn_add_rule = ui.button("添加规则", icon="add", color="primary").props("flat dense size=sm").classes("m3-pill")
                                    rules_box = ui.column().classes("w-full gap-2")

                                    def append_rule_row(
                                        r_inc: list[str] | None = None,
                                        r_media: list[str] | None = None,
                                        *,
                                        rbox=rules_box,
                                        reditors=rule_editors,
                                    ):
                                        if r_media is None:
                                            r_media = list(reditors[-1]["media"].value) if reditors else ["video", "document"]
                                        with rbox:
                                            row_el = ui.row().classes("w-full items-center gap-3 flex-wrap")
                                            with row_el:
                                                r_in = ui.input(
                                                    "include 正则（逗号分隔，留空=全部）",
                                                    value=", ".join(r_inc or []),
                                                ).props("outlined dense").classes("w-72")
                                                r_med = ui.select(
                                                    sorted(MEDIA_TYPES),
                                                    multiple=True,
                                                    value=list(r_media or ["video", "document"]),
                                                    label="下载媒体类型",
                                                ).props("outlined dense use-chips").classes("w-80")
                                                entry_ref = {"inc": r_in, "media": r_med, "row": row_el}

                                                def _del_rule(ref=entry_ref, box=rbox, editors=reditors):
                                                    if len(editors) <= 1:
                                                        ui.notify("每个频道至少保留一条规则", type="warning")
                                                        return
                                                    editors.remove(ref)
                                                    box.remove(ref["row"])

                                                ui.button(icon="close", color="negative", on_click=_del_rule).props("flat round dense size=sm")
                                                reditors.append(entry_ref)

                                    for r in init_rules:
                                        append_rule_row(r.get("include", []), r.get("media", ["video", "document"]))
                                    btn_add_rule.on_click(lambda fn=append_rule_row: fn([], None))

                                ch_editors.append({
                                    "chat": e_chat, "name": e_name, "exc": e_exc,
                                    "scan_mode": e_scan_mode, "search_q": e_search_q,
                                    "rules": rule_editors, "min": e_min, "max": e_max,
                                    "since": e_since, "until": e_until, "ctx": e_ctx, "reply": e_reply,
                                })

                        def save_form():
                            try:
                                api_id_val = int(in_api_id.value.strip())
                            except ValueError:
                                ui.notify("api_id 必须是纯数字", type="negative")
                                return
                            new_raw = dict(raw)
                            new_raw["telegram"] = {"api_id": api_id_val, "api_hash": in_api_hash.value.strip()}
                            new_raw["download"] = {
                                "dir": in_dl_dir.value.strip() or "downloads",
                                "data_dir": dl.get("data_dir", "data"),
                                "concurrency": int(in_conc.value or 3),
                                "path_template": (in_path_tpl.value or "").strip(),
                            }
                            new_raw.setdefault("limits", {
                                "request_interval": 1.0,
                                "request_jitter": 0.5,
                                "flood_sleep_threshold": 120,
                                "max_flood_wait": 3600,
                                "max_retries": 3,
                            })
                            if in_px_on.value:
                                p_dict = {
                                    "type": in_px_type.value,
                                    "host": in_px_host.value.strip(),
                                    "port": int(in_px_port.value or 7890),
                                }
                                if in_px_secret.value.strip():
                                    p_dict["secret"] = in_px_secret.value.strip()
                                new_raw["proxy"] = p_dict
                            else:
                                new_raw.pop("proxy", None)

                            new_chs = []
                            for ed in ch_editors:
                                chat_s = ed["chat"].value.strip()
                                if not chat_s:
                                    continue
                                chat_v: str | int = int(chat_s) if chat_s.lstrip("-").isdigit() else chat_s
                                parsed_rules = [
                                    {
                                        "include": [x.strip() for x in r["inc"].value.split(",") if x.strip()],
                                        "media": list(r["media"].value or ["video"]),
                                    }
                                    for r in ed["rules"]
                                ]
                                entry: dict = {
                                    "chat": chat_v,
                                    "exclude": [x.strip() for x in ed["exc"].value.split(",") if x.strip()],
                                    "min_size": int(ed["min"].value) if ed["min"].value.strip().isdigit() else ed["min"].value.strip(),
                                    "max_size": int(ed["max"].value) if ed["max"].value.strip().isdigit() else ed["max"].value.strip(),
                                    "context_before": int(ed["ctx"].value or 0),
                                    "follow_replies": bool(ed["reply"].value),
                                }
                                if len(parsed_rules) == 1:
                                    entry["include"] = parsed_rules[0]["include"]
                                    entry["media"] = parsed_rules[0]["media"]
                                else:
                                    entry["rules"] = parsed_rules
                                if ed["name"].value.strip():
                                    entry["name"] = ed["name"].value.strip()
                                if ed["since"].value.strip():
                                    entry["since"] = ed["since"].value.strip()
                                if ed["until"].value.strip():
                                    entry["until"] = ed["until"].value.strip()
                                scan_mode_val = ed["scan_mode"].value or "full"
                                if scan_mode_val != "full":
                                    entry["scan_mode"] = scan_mode_val
                                sq = [x.strip() for x in (ed["search_q"].value or "").split(",") if x.strip()]
                                if sq:
                                    entry["search_queries"] = sq
                                new_chs.append(entry)

                            new_raw["channels"] = new_chs
                            save_raw_config(cfg_path, new_raw)
                            try:
                                load_config(cfg_path)
                            except ConfigError as e:
                                ui.notify(f"已写入但校验失败：{e}", type="warning")
                                return
                            ui.notify("配置已保存并通过校验（重启挂机后生效）", type="positive")
                            do_preview()
                            reload_scope_picker()

                        with ui.row().classes("w-full justify-end"):
                            ui.button("保存全部配置", icon="save", color="primary", on_click=save_form).props("unelevated size=md").classes("m3-pill px-6")

                reload_cfg_form()

            # --------------------------------------------------
            # 页面 4：账号与登录
            # --------------------------------------------------
            with ui.column().classes("w-full items-center") as page_account:
                pages["account"] = page_account
                page_account.set_visibility(False)

                with ui.card().classes("w-full max-w-xl m3-card p-6 gap-3"):
                    with ui.row().classes("items-center gap-3"):
                        with ui.avatar(color="indigo-6", text_color="white", size="44px"):
                            ui.icon("verified_user", size="22px")
                        with ui.column().classes("gap-0"):
                            ui.label("Telegram 账号登录").classes("text-lg font-semibold m3-text")
                            ui.label("Session 仅保存在本地 data/ 目录，支持 macOS 与 Windows 共用").classes("text-xs m3-muted")

                    login_chip = ui.chip("尚未检测登录状态", icon="help_outline", color="grey-7", text_color="white").props("dense")

                    in_phone = ui.input("手机号（带国际区号，如 +8613800138000）").props("outlined dense").classes("w-full")
                    in_code = ui.input("验证码（查收 Telegram 官方消息或短信）").props("outlined dense").classes("w-full")
                    in_pwd = ui.input("两步验证密码（若开启 2FA 再填写）", password=True, password_toggle_button=True).props("outlined dense").classes("w-full")

                    async def check_login():
                        try:
                            cfg = load_config(cfg_path)
                            sess = AuthSession(cfg)
                            me = await sess.get_me()
                            await sess.disconnect()
                            if me:
                                login_chip.set_text(f"已登录：{me.first_name}（id={me.id}）")
                                login_chip.props("color=positive text-color=white icon=check_circle")
                            else:
                                login_chip.set_text("未登录，请填写手机号并获取验证码")
                                login_chip.props("color=warning text-color=white icon=warning")
                        except (ConfigError, Exception) as e:
                            login_chip.set_text(f"检测失败：{e}")
                            login_chip.props("color=negative text-color=white icon=error")

                    async def send_code_click():
                        try:
                            cfg = load_config(cfg_path)
                            if auth_holder["session"]:
                                await auth_holder["session"].disconnect()
                            sess = AuthSession(cfg)
                            await sess.send_code(in_phone.value)
                            auth_holder["session"] = sess
                            login_chip.set_text("验证码已发送，请在下方输入并确认登录")
                            login_chip.props("color=info text-color=white icon=mark_email_read")
                            ui.notify("验证码已发送", type="positive")
                        except (ConfigError, Exception) as e:
                            ui.notify(f"发送失败：{e}", type="negative")

                    async def sign_in_click():
                        sess = auth_holder.get("session")
                        if not sess:
                            ui.notify("请先点击「发送验证码」", type="warning")
                            return
                        try:
                            me = await sess.sign_in(in_code.value, in_pwd.value)
                            await sess.disconnect()
                            auth_holder["session"] = None
                            login_chip.set_text(f"登录成功：{me.first_name}（id={me.id}）")
                            login_chip.props("color=positive text-color=white icon=check_circle")
                            ui.notify("登录成功，session 已保存", type="positive")
                        except Exception as e:
                            ui.notify(f"登录失败：{type(e).__name__}: {e}", type="negative")

                    with ui.row().classes("gap-3 mt-2 flex-wrap"):
                        ui.button("检测状态", icon="refresh", on_click=check_login).props("outline color=primary").classes("m3-pill")
                        ui.button("发送验证码", icon="sms", color="secondary", on_click=send_code_click).props("unelevated").classes("m3-pill")
                        ui.button("确认登录", icon="login", color="primary", on_click=sign_in_click).props("unelevated").classes("m3-pill px-5")

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
