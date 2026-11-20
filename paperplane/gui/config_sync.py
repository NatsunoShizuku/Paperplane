"""配置字典与 GUI 表单之间的纯逻辑映射层（无 NiceGUI 依赖，便于独立单元测试）。"""
from __future__ import annotations

from typing import Any

from ..config import DEFAULT_PATH_TEMPLATE


def parse_csv_list(text: str | None) -> list[str]:
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def parse_chat_value(text: str) -> str | int:
    s = text.strip()
    return int(s) if s.lstrip("-").isdigit() else s


def parse_size_input(text: str | int | None, default: int | str = 0) -> int | str:
    if text is None:
        return default
    s = str(text).strip()
    if not s:
        return default
    return int(s) if s.isdigit() else s


def default_channel_dict(chat: str | int = "", name: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "chat": chat,
        "enabled": True,
        "include": [],
        "exclude": [],
        "media": ["video", "document"],
        "min_size": "10MB",
        "max_size": 0,
        "context_before": 1,
        "follow_replies": True,
    }
    if name:
        entry["name"] = name
    return entry


def normalize_channel_rules(ch_raw: dict[str, Any], defaults: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """将频道配置中的 include/media 或 rules 归一化为统一的规则列表供表单渲染。"""
    defaults = defaults or {}
    init_rules = list(ch_raw.get("rules") or [])
    if init_rules:
        return [
            {
                "include": list(r.get("include", [])),
                "media": list(r.get("media", ch_raw.get("media", defaults.get("media", ["video", "document"])))),
            }
            for r in init_rules
        ]
    return [{
        "include": list(ch_raw.get("include", [])),
        "media": list(ch_raw.get("media", defaults.get("media", ["video", "document"]))),
    }]


def build_raw_from_form(existing_raw: dict[str, Any], form: dict[str, Any]) -> dict[str, Any]:
    """将表单采集的数据结构转换回可写回 config.toml 的 raw dict。"""
    api_id_str = str(form.get("api_id", "")).strip()
    try:
        api_id_val = int(api_id_str)
    except ValueError as e:
        raise ValueError("api_id 必须是纯数字") from e

    new_raw = dict(existing_raw)
    dl_old = existing_raw.get("download", {})

    new_raw["telegram"] = {
        "api_id": api_id_val,
        "api_hash": str(form.get("api_hash", "")).strip(),
    }
    new_raw["download"] = {
        "dir": str(form.get("download_dir", "")).strip() or "downloads",
        "data_dir": dl_old.get("data_dir", "data"),
        "concurrency": int(form.get("concurrency") or 3),
        "path_template": str(form.get("path_template", DEFAULT_PATH_TEMPLATE) or "").strip(),
    }
    new_raw.setdefault("limits", {
        "request_interval": 1.0,
        "request_jitter": 0.5,
        "flood_sleep_threshold": 120,
        "max_flood_wait": 3600,
        "max_retries": 3,
    })

    proxy_form = form.get("proxy")
    if proxy_form and proxy_form.get("enabled"):
        p_dict: dict[str, Any] = {
            "type": proxy_form.get("type", "socks5"),
            "host": str(proxy_form.get("host", "")).strip(),
            "port": int(proxy_form.get("port") or 7890),
        }
        secret = str(proxy_form.get("secret", "")).strip()
        if secret:
            p_dict["secret"] = secret
        new_raw["proxy"] = p_dict
    else:
        new_raw.pop("proxy", None)

    ch_defaults_form = form.get("channel_defaults")
    if isinstance(ch_defaults_form, dict):
        d_entry: dict[str, Any] = {
            "enabled": bool(ch_defaults_form.get("enabled", True)),
            "scan_mode": ch_defaults_form.get("scan_mode", "full") or "full",
            "exclude": parse_csv_list(ch_defaults_form.get("exclude")),
            "media": list(ch_defaults_form.get("media") or ["video", "document"]),
            "min_size": parse_size_input(ch_defaults_form.get("min_size"), 0),
            "max_size": parse_size_input(ch_defaults_form.get("max_size"), 0),
            "context_before": int(ch_defaults_form.get("context_before") or 0),
            "follow_replies": bool(ch_defaults_form.get("follow_replies", True)),
        }
        new_raw["channel_defaults"] = d_entry

    new_chs: list[dict[str, Any]] = []
    for ch in form.get("channels", []):
        chat_s = str(ch.get("chat", "")).strip()
        if not chat_s:
            continue
        parsed_rules = [
            {
                "include": parse_csv_list(r.get("include")),
                "media": list(r.get("media") or ["video"]),
            }
            for r in ch.get("rules", [])
        ]
        if not parsed_rules:
            parsed_rules = [{"include": [], "media": ["video", "document"]}]

        entry: dict[str, Any] = {
            "chat": parse_chat_value(chat_s),
            "enabled": bool(ch.get("enabled", True)),
            "exclude": parse_csv_list(ch.get("exclude")),
            "min_size": parse_size_input(ch.get("min_size"), 0),
            "max_size": parse_size_input(ch.get("max_size"), 0),
            "context_before": int(ch.get("context_before") or 0),
            "follow_replies": bool(ch.get("follow_replies", True)),
        }
        if len(parsed_rules) == 1:
            entry["include"] = parsed_rules[0]["include"]
            entry["media"] = parsed_rules[0]["media"]
        else:
            entry["rules"] = parsed_rules

        name_s = str(ch.get("name", "")).strip()
        if name_s:
            entry["name"] = name_s
        since_s = str(ch.get("since", "")).strip()
        if since_s:
            entry["since"] = since_s
        until_s = str(ch.get("until", "")).strip()
        if until_s:
            entry["until"] = until_s
        scan_mode_val = ch.get("scan_mode") or "full"
        if scan_mode_val != "full":
            entry["scan_mode"] = scan_mode_val
        sq = parse_csv_list(ch.get("search_queries"))
        if sq:
            entry["search_queries"] = sq
        ch_tpl = str(ch.get("path_template", "") or "").strip()
        if ch_tpl:
            entry["path_template"] = ch_tpl
        ch_dl = str(ch.get("download_dir", "") or "").strip()
        if ch_dl:
            entry["download_dir"] = ch_dl

        new_chs.append(entry)

    new_raw["channels"] = new_chs
    return new_raw
