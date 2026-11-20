"""页面 1：挂机控制台（KPI 卡片、控制栏、持久化运行范围选择、下载队列、实时日志）。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..config import ConfigError, load_config
from ..core import _human
from ..service import LongRunningRunner
from .prefs import load_dashboard_scope, save_dashboard_scope
from .theme import STATUS_META, fmt_uptime


def build_dashboard_page(
    ui,
    *,
    cfg_path: Path,
    runner: LongRunningRunner,
    header_chip,
    render_avatar: Callable,
):
    """构建挂机控制台页面，返回 (page_container, reload_scope_picker)。"""
    with ui.column().classes("w-full gap-5") as page_dash:
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

        # 运行范围选择（勾选状态持久化到 data/ui_prefs.json，不修改 config.toml）
        scope_controls: list[dict] = []
        with ui.card().classes("w-full m3-card p-4"):
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 mb-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("checklist", color="primary", size="20px")
                    ui.label("运行范围（勾选频道与关键词）").classes("text-base font-semibold m3-text")
                    ui.label("勾选状态自动记住，不修改 config.toml").classes("text-xs m3-muted")
                with ui.row().classes("items-center gap-1"):
                    ui.button("全选", on_click=lambda: set_all_scope(True)).props("flat dense size=sm color=primary").classes("m3-pill")
                    ui.button("全不选", on_click=lambda: set_all_scope(False)).props("flat dense size=sm").classes("m3-pill")
                    ui.button("刷新", icon="refresh", on_click=lambda: reload_scope_picker()).props("flat dense size=sm").classes("m3-pill")
            scope_box = ui.column().classes("w-full gap-2")

        def persist_scope():
            if not scope_controls:
                return
            state = {}
            for item in scope_controls:
                state[item["chat_key"]] = {
                    "checked": bool(item["ch_cb"].value),
                    "keywords": [kw for k_cb, kw in item["kw_cbs"] if k_cb.value],
                }
            save_dashboard_scope(cfg_path, state)

        def set_all_scope(val: bool):
            for item in scope_controls:
                item["ch_cb"].set_value(val)
                for kw_cb, _ in item["kw_cbs"]:
                    kw_cb.set_value(val)
            persist_scope()

        def reload_scope_picker():
            scope_box.clear()
            scope_controls.clear()
            try:
                cfg_now = load_config(cfg_path)
            except ConfigError as e:
                with scope_box:
                    ui.label(f"请先在「配置中心」完成配置：{e}").classes("text-xs text-negative")
                return

            saved_scope = load_dashboard_scope(cfg_path)

            with scope_box:
                for idx, ch in enumerate(cfg_now.channels):
                    chat_key = str(ch.chat)
                    chat_int = int(ch.chat) if chat_key.lstrip("-").isdigit() else None
                    title = ch.name or chat_key
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

                    saved_ch = saved_scope.get(chat_key)
                    default_checked = saved_ch["checked"] if saved_ch is not None else ch.enabled
                    saved_kws = set(saved_ch["keywords"]) if (saved_ch and saved_ch.get("keywords") is not None) else None

                    with ui.column().classes("w-full m3-subcard p-3 gap-2"):
                        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                            with ui.row().classes("items-center gap-2"):
                                ch_cb = ui.checkbox(value=default_checked).props("dense")
                                ch_cb.on_value_change(lambda _: persist_scope())
                                render_avatar(chat_int, title)
                                ui.label(title).classes("text-sm font-semibold m3-text")
                                ui.html(f'<span class="m3-kind-badge">{ch.scan_mode}</span>', sanitize=False)
                                if not ch.enabled:
                                    ui.badge("配置已停用", color="grey-6").props("outline dense")
                            ui.label(chat_key).classes("text-xs font-mono m3-muted")

                        kw_cbs: list[tuple] = []
                        with ui.row().classes("w-full items-center gap-3 flex-wrap pl-8"):
                            for kw, mset in kw_media.items():
                                m_tag = "/".join(sorted(mset))
                                lbl = f"{kw} ({m_tag})" if kw else f"全部消息 ({m_tag})"
                                kw_checked = (kw in saved_kws) if saved_kws is not None else True
                                k_cb = ui.checkbox(lbl, value=kw_checked).props("dense")
                                k_cb.bind_enabled_from(ch_cb, "value")
                                k_cb.on_value_change(lambda _: persist_scope())
                                kw_cbs.append((k_cb, kw))

                        scope_controls.append({"idx": idx, "chat_key": chat_key, "ch_cb": ch_cb, "kw_cbs": kw_cbs})

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
                persist_scope()
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
            kpi_uptime.set_text(fmt_uptime(runner.started_at) if running else "00:00:00")
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

    return page_dash, reload_scope_picker
