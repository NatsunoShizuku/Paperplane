"""页面 2：频道与离线匹配预览。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import ConfigError, load_config, load_raw_config, save_raw_config
from ..core import _human
from ..service import fetch_dialogs, preview_matches
from ..store import Store
from .config_sync import default_channel_dict


def build_channels_page(
    ui,
    *,
    cfg_path: Path,
    render_avatar: Callable,
    on_config_changed: Callable,
):
    """构建频道与预览页面，返回 (page_container, do_preview)。"""
    with ui.column().classes("w-full gap-5") as page_channels:
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
                chs.append(default_channel_dict(chat=d.id, name=d.name))
                raw["channels"] = chs
                save_raw_config(cfg_path, raw)
                ui.notify(f"已加入配置：{d.name}（可前往「配置中心」设置关键词）", type="positive")
                on_config_changed()

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
                on_config_changed(refresh_preview=False)
                ui.notify(f"已加载 {len(dialogs_cache)} 个对话（头像已缓存）", type="positive")
            except (ConfigError, Exception) as e:
                ui.notify(f"拉取失败：{e}", type="negative")
            finally:
                btn_dialogs.props(remove="loading")

        btn_dialogs.on_click(load_dialogs_ui)
        render_dialogs()

    return page_channels, do_preview
