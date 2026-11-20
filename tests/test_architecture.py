import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from paperplane.client import Throttle
from paperplane.config import (
    ChannelConfig,
    Config,
    ConfigError,
    ensure_config_file,
    load_config,
    load_raw_config,
    save_raw_config,
)
from paperplane.core import ChannelJob
from paperplane.gui import (
    _load_dark_mode,
    _save_dark_mode,
    load_dashboard_scope,
    save_dashboard_scope,
)
from paperplane.gui.config_sync import build_raw_from_form, normalize_channel_rules
from paperplane.service import filter_channels_by_scope
from paperplane.store import CachedMessage, Store

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_ensure_config_file_and_local_overlay(tmp_path: Path):
    example = tmp_path / "config.example.toml"
    shutil.copy2(REPO_ROOT / "config.example.toml", example)
    cfg_file = tmp_path / "config.toml"
    assert not cfg_file.exists()

    ensure_config_file(cfg_file)
    assert cfg_file.exists()
    assert "# Paperplane 配置文件模板" in cfg_file.read_text(encoding="utf-8")

    # config.local.toml 覆盖敏感凭证与并发数，不修改 config.toml
    local_file = tmp_path / "config.local.toml"
    save_raw_config(local_file, {
        "telegram": {"api_id": 99999, "api_hash": "local_secret_hash"},
        "download": {"concurrency": 5},
    })
    cfg = load_config(cfg_file)
    assert cfg.api_id == 99999
    assert cfg.api_hash == "local_secret_hash"
    assert cfg.concurrency == 5
    # 其余未覆盖项仍来自 config.toml
    assert cfg.path_template == "{include}/{media_type}"
    assert len(cfg.channels) == 1


def test_channel_defaults_inheritance_and_overrides(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    save_raw_config(cfg_file, {
        "telegram": {"api_id": 1, "api_hash": "abc"},
        "channel_defaults": {
            "enabled": True,
            "scan_mode": "search",
            "exclude": ["广告"],
            "media": ["video"],
            "min_size": "5MB",
            "context_before": 2,
            "follow_replies": False,
            "path_template": "{channel}/{media_type}",
        },
        "channels": [
            {"chat": -1001, "include": ["#4K"]},
            {
                "chat": -1002,
                "enabled": False,
                "scan_mode": "full",
                "media": ["photo"],
                "min_size": "1MB",
                "download_dir": "custom_dl",
                "path_template": "{include}",
            },
        ],
    })
    cfg = load_config(cfg_file)
    ch0, ch1 = cfg.channels
    assert ch0.enabled is True
    assert ch0.scan_mode == "search"
    assert ch0.exclude == ["广告"]
    assert ch0.media == {"video"}
    assert ch0.min_size == 5 * 1024 * 1024
    assert ch0.context_before == 2
    assert ch0.follow_replies is False
    assert ch0.path_template == "{channel}/{media_type}"

    assert ch1.enabled is False
    assert ch1.scan_mode == "full"
    assert ch1.media == {"photo"}
    assert ch1.min_size == 1024 * 1024
    assert ch1.download_dir == (tmp_path / "custom_dl").resolve()
    assert ch1.path_template == "{include}"

    # 缺少 chat 字段应抛出友好的 ConfigError
    bad_cfg = tmp_path / "bad.toml"
    save_raw_config(bad_cfg, {
        "telegram": {"api_id": 1, "api_hash": "abc"},
        "channels": [{"include": ["#tag"]}],
    })
    with pytest.raises(ConfigError, match="chat"):
        load_config(bad_cfg)


def test_per_channel_download_dir_and_path_template(tmp_path: Path):
    class FakeClient:
        async def iter_download(self, doc, offset=0, request_size=0, file_size=None):
            yield b"abcde"

    cfg = Config(api_id=1, api_hash="x", data_dir=tmp_path, download_dir=tmp_path / "global_dl", channels=[])
    ch = ChannelConfig(
        chat=-1001,
        name="MyChan",
        include=["#Tag"],
        media={"video"},
        download_dir=tmp_path / "chan_dl",
        path_template="{channel}/{include}",
    )
    store = Store(tmp_path / "s.sqlite3")
    try:
        m = CachedMessage(-1001, 7, None, 1735689600, "#Tag hello", "v.mp4", "video", 5, None)
        store.save_messages([m])
        job = ChannelJob(FakeClient(), cfg, ch, store, Throttle(0, 0))
        job.chat_id = -1001
        job.title = "MyChan"
        todo = job.pending()
        fake_msg = SimpleNamespace(media=object(), document=object(), file=SimpleNamespace(ext=".mp4"))
        status = asyncio.run(job._download_one(todo[0], fake_msg))
        assert status == "done"
        assert (tmp_path / "chan_dl" / "MyChan" / "Tag" / "7_v.mp4").exists()
        assert not (tmp_path / "global_dl").exists()
    finally:
        store.close()


def test_save_raw_config_preserves_comments(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    shutil.copy2(REPO_ROOT / "config.example.toml", cfg_file)

    raw = load_raw_config(cfg_file)
    raw["telegram"] = {"api_id": 123456, "api_hash": "fedcba9876543210"}
    raw["download"]["concurrency"] = 4
    raw["proxy"] = {"type": "socks5", "host": "127.0.0.1", "port": 7890}
    save_raw_config(cfg_file, raw)

    text = cfg_file.read_text(encoding="utf-8")
    # 头部、分组注释和行尾注释都应被保留
    assert "# Paperplane 配置文件模板" in text
    assert "# 3. 防风控限流与重试设置" in text
    assert "# 媒体文件保存根目录" in text
    assert "# 两次 API 请求之间的最小间隔" in text
    cfg = load_config(cfg_file)
    assert cfg.api_id == 123456
    assert cfg.concurrency == 4
    assert cfg.proxy == {"type": "socks5", "host": "127.0.0.1", "port": 7890}

    # 删除 proxy 后应从文件中干净移除且仍保留注释
    raw.pop("proxy")
    save_raw_config(cfg_file, raw)
    assert load_config(cfg_file).proxy is None
    assert "# Paperplane 配置文件模板" in cfg_file.read_text(encoding="utf-8")


def test_enabled_flag_and_dashboard_scope_persistence(tmp_path: Path):
    ch0 = ChannelConfig(chat=-1001, include=["#A"], enabled=True)
    ch1 = ChannelConfig(chat=-1002, include=["#B"], enabled=False)
    # scope=None 时自动跳过 enabled=False 的频道
    assert [c.chat for c in filter_channels_by_scope([ch0, ch1], None)] == [-1001]
    # 显式 scope 勾选时以用户勾选为准
    assert [c.chat for c in filter_channels_by_scope([ch0, ch1], {1: {"#B"}})] == [-1002]

    cfg_file = tmp_path / "config.toml"
    save_raw_config(cfg_file, {
        "telegram": {"api_id": 1, "api_hash": "abc"},
        "download": {"data_dir": "data"},
        "channels": [{"chat": -1001}],
    })
    _save_dark_mode(cfg_file, True)
    save_dashboard_scope(cfg_file, {
        "-1001": {"checked": True, "keywords": ["#A"]},
        "-1002": {"checked": False, "keywords": []},
    })
    # 深色模式与控制台勾选状态互不覆盖
    assert _load_dark_mode(cfg_file) is True
    loaded = load_dashboard_scope(cfg_file)
    assert loaded["-1001"] == {"checked": True, "keywords": ["#A"]}
    assert loaded["-1002"] == {"checked": False, "keywords": []}


def test_search_state_decoupled_from_full_scan_state_and_wal(tmp_path: Path):
    def _tg_msg(msg_id: int, text: str, *, video: bool = True):
        return SimpleNamespace(
            id=msg_id,
            grouped_id=None,
            date=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
            message=text,
            media=object() if video else None,
            photo=None,
            sticker=None,
            gif=None,
            video_note=None,
            video=object() if video else None,
            voice=None,
            audio=None,
            document=None,
            file=SimpleNamespace(name=f"v{msg_id}.mp4", size=1024, ext=".mp4") if video else None,
            reply_to_msg_id=None,
        )

    history = {
        5: _tg_msg(5, "普通历史视频"),
        10: _tg_msg(10, "#K1 第一个"),
        15: _tg_msg(15, "#K2 第二个"),
        20: _tg_msg(20, "#K1 新的"),
    }
    recorded_calls: list[tuple[str | None, int]] = []

    class FakeClient:
        async def iter_messages(self, entity, **kwargs):
            q = kwargs.get("search")
            min_id = kwargs.get("min_id", 0)
            recorded_calls.append((q, min_id))
            if q is not None:
                for mid in sorted(history.keys(), reverse=True):
                    if mid > min_id and q in history[mid].message:
                        yield history[mid]
            else:
                for mid in sorted(history.keys()):
                    if mid > min_id:
                        yield history[mid]

        async def get_messages(self, entity, ids):
            return [history.get(i) for i in ids]

    db_path = tmp_path / "state.sqlite3"
    store = Store(db_path)
    try:
        # 校验 WAL 模式已启用
        mode = store.db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

        cfg = Config(api_id=1, api_hash="x", data_dir=tmp_path, download_dir=tmp_path / "dl", channels=[])
        # 第 1 次：search 模式只搜 #K1
        ch_s1 = ChannelConfig(chat=-1001, include=["#K1"], media={"video"}, scan_mode="search")
        job1 = ChannelJob(FakeClient(), cfg, ch_s1, store, Throttle(0, 0))
        job1.chat_id = -1001
        asyncio.run(job1.scan())

        # scan_state (full) 不应被污染，仍为 0；search_state 记录了 #K1 的 last_id=20
        assert store.last_id(-1001) == 0
        assert store.floor_ts(-1001) is None
        assert store.search_last_id(-1001, "#K1") == 20

        # 第 2 次：新增关键词 #K2 -> #K1 应走增量(min_id=20)，#K2 应走全历史(min_id=0)
        recorded_calls.clear()
        ch_s2 = ChannelConfig(chat=-1001, include=["#K1", "#K2"], media={"video"}, scan_mode="search")
        job2 = ChannelJob(FakeClient(), cfg, ch_s2, store, Throttle(0, 0))
        job2.chat_id = -1001
        asyncio.run(job2.scan())
        assert recorded_calls == [("#K1", 20), ("#K2", 0)]
        assert store.search_last_id(-1001, "#K2") == 15
        assert store.last_id(-1001) == 0

        # 第 3 次：切换到 full 模式 -> 从 min_id=0 完整扫描，不会漏掉 #5！
        recorded_calls.clear()
        ch_full = ChannelConfig(chat=-1001, include=[], media={"video"}, scan_mode="full")
        job3 = ChannelJob(FakeClient(), cfg, ch_full, store, Throttle(0, 0))
        job3.chat_id = -1001
        asyncio.run(job3.scan())
        assert recorded_calls == [(None, 0)]
        assert store.last_id(-1001) == 20
        assert {m.msg_id for m in store.messages(-1001)} == {5, 10, 15, 20}
    finally:
        store.close()


def test_gui_config_sync_roundtrip():
    raw = {
        "telegram": {"api_id": 1, "api_hash": "old"},
        "download": {"dir": "downloads", "data_dir": "custom_data", "concurrency": 2},
    }
    form = {
        "api_id": "42",
        "api_hash": "new_hash",
        "download_dir": "my_dl",
        "concurrency": 4,
        "path_template": "{channel}/{include}",
        "proxy": {"enabled": True, "type": "socks5", "host": "127.0.0.1", "port": 7890, "secret": ""},
        "channel_defaults": {
            "enabled": True,
            "scan_mode": "full",
            "exclude": "广告, 预告",
            "media": ["video"],
            "min_size": "5MB",
            "max_size": "0",
            "context_before": 1,
            "follow_replies": True,
        },
        "channels": [
            {
                "chat": "-100123",
                "name": "测试",
                "enabled": False,
                "exclude": "spam",
                "scan_mode": "search",
                "search_queries": "#4K",
                "min_size": "10MB",
                "max_size": "0",
                "since": "2026-01-01",
                "until": "",
                "context_before": 1,
                "follow_replies": True,
                "rules": [
                    {"include": "#4K, #原盘", "media": ["video"]},
                    {"include": "#图集", "media": ["photo"]},
                ],
            }
        ],
    }
    out = build_raw_from_form(raw, form)
    assert out["telegram"] == {"api_id": 42, "api_hash": "new_hash"}
    assert out["download"]["data_dir"] == "custom_data"
    assert out["proxy"] == {"type": "socks5", "host": "127.0.0.1", "port": 7890}
    assert out["channel_defaults"]["exclude"] == ["广告", "预告"]
    ch = out["channels"][0]
    assert ch["chat"] == -100123
    assert ch["enabled"] is False
    assert ch["scan_mode"] == "search"
    assert ch["search_queries"] == ["#4K"]
    assert len(ch["rules"]) == 2
    assert normalize_channel_rules(ch) == [
        {"include": ["#4K", "#原盘"], "media": ["video"]},
        {"include": ["#图集"], "media": ["photo"]},
    ]
