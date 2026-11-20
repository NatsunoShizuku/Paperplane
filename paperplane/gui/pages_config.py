"""页面 3：可视化配置中心（保留注释无损保存、支持 [channel_defaults] 与单频道启停）。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from ..config import (
    DEFAULT_PATH_TEMPLATE,
    MEDIA_TYPES,
    PATH_TEMPLATE_VARS,
    ConfigError,
    load_config,
    load_raw_config,
    save_raw_config,
)
from ..core import render_folder
from .config_sync import build_raw_from_form, default_channel_dict, normalize_channel_rules


def build_config_page(
    ui,
    *,
    cfg_path: Path,
    render_avatar: Callable,
    on_saved: Callable,
):
    """构建配置中心页面，返回 (page_container, reload_cfg_form)。"""
    with ui.column().classes("w-full gap-5") as page_config:
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
            ch_def = raw.get("channel_defaults", {}) or {}
            chs = list(raw.get("channels", []))
            if not chs:
                chs = [default_channel_dict(chat="@some_public_channel")]

            ch_editors: list[dict] = []

            def add_blank_channel():
                raw_now = load_raw_config(cfg_path) if cfg_path.exists() else dict(raw)
                cur = list(raw_now.get("channels", chs))
                cur.append(default_channel_dict(chat=""))
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
                on_saved()

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

                # 频道默认配置（[channel_defaults]）
                with ui.expansion("频道默认配置（[channel_defaults]，未填写字段自动继承）", icon="settings_suggest", value=False).classes("w-full m3-expansion"):
                    with ui.column().classes("w-full p-4 gap-3"):
                        with ui.row().classes("w-full gap-3 items-center flex-wrap"):
                            def_enabled = ui.switch("默认启用", value=bool(ch_def.get("enabled", True)))
                            def_scan = ui.select(
                                {"full": "全量遍历 (full)", "search": "频道内搜索 (search)"},
                                value=ch_def.get("scan_mode", "full") if ch_def.get("scan_mode") in ("full", "search") else "full",
                                label="默认扫描模式",
                            ).props("outlined dense").classes("w-48")
                            def_exc = ui.input("默认 exclude（逗号分隔）", value=", ".join(ch_def.get("exclude", []))).props("outlined dense").classes("w-64")
                            def_media = ui.select(
                                sorted(MEDIA_TYPES),
                                multiple=True,
                                value=list(ch_def.get("media", ["video", "document"])),
                                label="默认媒体类型",
                            ).props("outlined dense use-chips").classes("w-72")
                        with ui.row().classes("w-full gap-3 items-center flex-wrap"):
                            def_min = ui.input("默认最小体积", value=str(ch_def.get("min_size", "10MB"))).props("outlined dense").classes("w-32")
                            def_max = ui.input("默认最大体积(0不限)", value=str(ch_def.get("max_size", 0))).props("outlined dense").classes("w-36")
                            def_ctx = ui.number("默认前文条数", value=int(ch_def.get("context_before", 1)), min=0, max=20).props("outlined dense").classes("w-28")
                            def_reply = ui.checkbox("默认跟踪回复", value=bool(ch_def.get("follow_replies", True)))

                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(f"监控频道列表（{len(chs)}）").classes("text-base font-semibold m3-text")
                    ui.button("新增频道", icon="add", on_click=add_blank_channel).props("outline color=primary").classes("m3-pill")

                for idx, c in enumerate(chs):
                    chat_raw = c.get("chat", "")
                    chat_int = int(chat_raw) if str(chat_raw).lstrip("-").isdigit() else None
                    title_label = c.get("name") or str(chat_raw) or "未填写"
                    init_rules = normalize_channel_rules(c, ch_def)

                    with ui.card().classes("w-full m3-card p-5"):
                        with ui.row().classes("w-full items-center justify-between mb-3"):
                            with ui.row().classes("items-center gap-3"):
                                render_avatar(chat_int, title_label)
                                ui.label(f"频道 #{idx + 1} · {title_label}").classes("text-sm font-semibold text-primary")
                            with ui.row().classes("items-center gap-2"):
                                e_enabled = ui.switch("启用", value=bool(c.get("enabled", ch_def.get("enabled", True)))).props("dense")
                                ui.button("删除频道", icon="delete", color="negative", on_click=lambda i=idx: remove_channel(i)).props("flat dense size=sm")
                        with ui.row().classes("w-full gap-3 flex-wrap"):
                            e_chat = ui.input("chat（数字 ID 或 @用户名）", value=str(chat_raw)).props("outlined dense").classes("w-56")
                            e_name = ui.input("保存子目录名（可选）", value=str(c.get("name", "") or "")).props("outlined dense").classes("w-48")
                            e_exc = ui.input("exclude 排除正则（逗号分隔）", value=", ".join(c.get("exclude", ch_def.get("exclude", [])))).props("outlined dense").classes("w-64")
                            cur_mode = c.get("scan_mode", ch_def.get("scan_mode", "full"))
                            e_scan_mode = ui.select(
                                {"full": "全量遍历 (full)", "search": "频道内搜索 (search)"},
                                value=cur_mode if cur_mode in ("full", "search") else "full",
                                label="扫描模式",
                            ).props("outlined dense").classes("w-48")
                        with ui.row().classes("w-full gap-3 items-center flex-wrap mt-2"):
                            e_min = ui.input("最小体积", value=str(c.get("min_size", ch_def.get("min_size", "10MB")))).props("outlined dense").classes("w-28")
                            e_max = ui.input("最大体积(0不限)", value=str(c.get("max_size", ch_def.get("max_size", 0)))).props("outlined dense").classes("w-32")
                            e_since = ui.input("since (YYYY-MM-DD)", value=str(c.get("since", "") or "")).props("outlined dense").classes("w-40")
                            e_until = ui.input("until (YYYY-MM-DD)", value=str(c.get("until", "") or "")).props("outlined dense").classes("w-40")
                            e_ctx = ui.number("前文条数", value=int(c.get("context_before", ch_def.get("context_before", 1))), min=0, max=20).props("outlined dense").classes("w-24")
                            e_reply = ui.checkbox("跟踪回复", value=bool(c.get("follow_replies", ch_def.get("follow_replies", True))))
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
                            "chat": e_chat, "name": e_name, "enabled": e_enabled, "exc": e_exc,
                            "scan_mode": e_scan_mode, "search_q": e_search_q,
                            "rules": rule_editors, "min": e_min, "max": e_max,
                            "since": e_since, "until": e_until, "ctx": e_ctx, "reply": e_reply,
                        })

                def save_form():
                    form_payload = {
                        "api_id": in_api_id.value,
                        "api_hash": in_api_hash.value,
                        "download_dir": in_dl_dir.value,
                        "concurrency": in_conc.value,
                        "path_template": in_path_tpl.value,
                        "proxy": {
                            "enabled": bool(in_px_on.value),
                            "type": in_px_type.value,
                            "host": in_px_host.value,
                            "port": in_px_port.value,
                            "secret": in_px_secret.value,
                        },
                        "channel_defaults": {
                            "enabled": bool(def_enabled.value),
                            "scan_mode": def_scan.value,
                            "exclude": def_exc.value,
                            "media": list(def_media.value or ["video", "document"]),
                            "min_size": def_min.value,
                            "max_size": def_max.value,
                            "context_before": def_ctx.value,
                            "follow_replies": bool(def_reply.value),
                        },
                        "channels": [
                            {
                                "chat": ed["chat"].value,
                                "name": ed["name"].value,
                                "enabled": bool(ed["enabled"].value),
                                "exclude": ed["exc"].value,
                                "scan_mode": ed["scan_mode"].value,
                                "search_queries": ed["search_q"].value,
                                "min_size": ed["min"].value,
                                "max_size": ed["max"].value,
                                "since": ed["since"].value,
                                "until": ed["until"].value,
                                "context_before": ed["ctx"].value,
                                "follow_replies": bool(ed["reply"].value),
                                "rules": [
                                    {"include": r["inc"].value, "media": list(r["media"].value or ["video"])}
                                    for r in ed["rules"]
                                ],
                            }
                            for ed in ch_editors
                        ],
                    }
                    try:
                        new_raw = build_raw_from_form(raw, form_payload)
                    except ValueError as e:
                        ui.notify(str(e), type="negative")
                        return

                    save_raw_config(cfg_path, new_raw)
                    try:
                        load_config(cfg_path)
                    except ConfigError as e:
                        ui.notify(f"已写入但校验失败：{e}", type="warning")
                        return
                    ui.notify("配置已保存并通过校验（保留原注释，重启挂机后生效）", type="positive")
                    on_saved()

                with ui.row().classes("w-full justify-end"):
                    ui.button("保存全部配置", icon="save", color="primary", on_click=save_form).props("unelevated size=md").classes("m3-pill px-6")

        reload_cfg_form()

    return page_config, reload_cfg_form
