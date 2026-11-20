"""UI 偏好持久化（深浅色主题、控制台勾选状态）与头像缓存路径。"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import ConfigError, load_merged_raw_config


def _data_dir_for(cfg_path: Path) -> Path:
    try:
        raw = load_merged_raw_config(cfg_path) if cfg_path.exists() else {}
    except ConfigError:
        raw = {}
    dl = raw.get("download", {})
    return (cfg_path.parent / dl.get("data_dir", "data")).resolve()


def _avatar_file_for(cfg_path: Path, chat_id: int | None) -> Path | None:
    if chat_id is None:
        return None
    p = _data_dir_for(cfg_path) / "avatars" / f"{chat_id}.jpg"
    return p if p.exists() else None


def _load_ui_prefs(cfg_path: Path) -> dict:
    prefs_file = _data_dir_for(cfg_path) / "ui_prefs.json"
    if not prefs_file.exists():
        return {}
    try:
        data = json.loads(prefs_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_ui_prefs(cfg_path: Path, updates: dict) -> None:
    data_dir = _data_dir_for(cfg_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    prefs_file = data_dir / "ui_prefs.json"
    data = _load_ui_prefs(cfg_path)
    data.update(updates)
    prefs_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_dark_mode(cfg_path: Path) -> bool:
    return bool(_load_ui_prefs(cfg_path).get("dark_mode", False))


def _save_dark_mode(cfg_path: Path, enabled: bool) -> None:
    _save_ui_prefs(cfg_path, {"dark_mode": bool(enabled)})


def load_dashboard_scope(cfg_path: Path) -> dict[str, dict]:
    """读取上次控制台勾选状态：{chat_key: {"checked": bool, "keywords": [str, ...]}}。"""
    scope = _load_ui_prefs(cfg_path).get("dashboard_scope")
    if not isinstance(scope, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in scope.items():
        if isinstance(v, dict):
            out[str(k)] = {
                "checked": bool(v.get("checked", True)),
                "keywords": [str(x) for x in v.get("keywords", [])] if isinstance(v.get("keywords"), list) else None,
            }
    return out


def save_dashboard_scope(cfg_path: Path, scope: dict[str, dict]) -> None:
    """持久化控制台频道与关键词勾选状态。"""
    clean: dict[str, dict] = {}
    for k, v in scope.items():
        clean[str(k)] = {
            "checked": bool(v.get("checked", True)),
            "keywords": sorted(str(x) for x in (v.get("keywords") or [])),
        }
    _save_ui_prefs(cfg_path, {"dashboard_scope": clean})
