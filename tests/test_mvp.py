import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from paperplane.client import Throttle
from paperplane.config import ChannelConfig, Config, ConfigError, MatchRule, load_config, load_raw_config, save_raw_config
from paperplane.core import ChannelJob, derive_search_queries, include_folder_name, render_folder
from paperplane.gui import build_gui_app
from paperplane.matcher import Matcher
from paperplane.service import LongRunningRunner, filter_channels_by_scope
from paperplane.store import CachedMessage, Store


def test_save_and_load_config_roundtrip(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    raw = {
        "telegram": {"api_id": 12345, "api_hash": "0123abcdef"},
        "download": {"dir": "downloads", "data_dir": "data", "concurrency": 4},
        "proxy": {"type": "socks5", "host": "127.0.0.1", "port": 10808},
        "channels": [
            {
                "chat": -1001234567890,
                "include": ["#tag"],
                "exclude": [],
                "media": ["video"],
                "min_size": "10MB",
                "max_size": 0,
                "context_before": 1,
                "follow_replies": True,
                "since": "2026-05-01",
            }
        ],
    }
    save_raw_config(cfg_file, raw)
    assert load_raw_config(cfg_file)["proxy"]["host"] == "127.0.0.1"
    cfg = load_config(cfg_file)
    assert cfg.api_id == 12345
    assert cfg.api_hash == "0123abcdef"
    assert cfg.concurrency == 4
    assert cfg.channels[0].chat == -1001234567890


def test_missing_config_raises_config_error(tmp_path: Path):
    try:
        load_config(tmp_path / "missing.toml")
        assert False, "should raise ConfigError"
    except ConfigError as e:
        assert "找不到配置文件" in str(e)


def test_long_running_runner_auto_reconnects_and_stops(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    save_raw_config(cfg_file, {
        "telegram": {"api_id": 1, "api_hash": "abc"},
        "channels": [{"chat": -1001, "media": ["video"]}],
    })

    runner = LongRunningRunner(cfg_file, catchup_interval=60.0)
    attempts = 0
    second_started = asyncio.Event()

    async def fake_session(cfg, store, mode):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("network drop")
        second_started.set()
        await runner._stop_event.wait()

    monkeypatch.setattr(runner, "_run_session", fake_session)

    async def scenario():
        # 缩短退避等待，加速测试
        orig_wait_for = asyncio.wait_for

        async def fast_wait_for(aw, timeout):
            return await orig_wait_for(aw, min(timeout, 0.02))

        monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)

        runner.start("watch")
        await orig_wait_for(second_started.wait(), timeout=2.0)
        assert runner.reconnects == 1
        await runner.stop()
        assert runner.status == "idle"
        assert attempts == 2

    asyncio.run(scenario())


def test_cli_and_gui_build_smoke(tmp_path: Path):
    from paperplane import cli_ui

    assert callable(cli_ui.run_interactive_menu)
    assert callable(cli_ui.run_init_wizard)
    assert callable(cli_ui.run_with_progress)

    cfg_file = tmp_path / "config.toml"
    save_raw_config(cfg_file, {
        "telegram": {"api_id": 1, "api_hash": "abc"},
        "channels": [{"chat": -1001, "media": ["video"]}],
    })
    ui = build_gui_app(cfg_file)
    assert ui is not None


def test_per_channel_multi_rules_include_to_media(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    save_raw_config(cfg_file, {
        "telegram": {"api_id": 1, "api_hash": "abc"},
        "channels": [{
            "chat": -1001,
            "exclude": ["广告"],
            "rules": [
                {"include": ["#视频"], "media": ["video"]},
                {"include": ["#图集"], "media": ["photo", "document"]},
            ],
        }],
    })
    cfg = load_config(cfg_file)
    ch = cfg.channels[0]
    assert len(ch.rules) == 2

    msgs = [
        CachedMessage(1, 1, None, 100, "#视频 正片", None, "video", 100, None),
        CachedMessage(1, 2, None, 101, "#视频 封面", None, "photo", 100, None),     # 媒体类型不符
        CachedMessage(1, 3, None, 102, "#图集 合集", None, "photo", 100, None),
        CachedMessage(1, 4, None, 103, "#图集 打包", None, "document", 100, None),
        CachedMessage(1, 5, None, 104, "#图集 预览", None, "video", 100, None),     # 媒体类型不符
        CachedMessage(1, 6, None, 105, "#视频 广告", None, "video", 100, None),     # 被 exclude 排除
    ]
    assert [m.msg_id for m in Matcher(ch).select(msgs)] == [1, 3, 4]


def test_global_semaphore_and_worker_queue(tmp_path: Path):
    async def scenario():
        cfg = Config(api_id=1, api_hash="x", data_dir=tmp_path, download_dir=tmp_path, channels=[], concurrency=2)
        store = Store(tmp_path / "s.sqlite3")
        global_sem = asyncio.Semaphore(2)
        active = 0
        peak_active = 0
        slow_release = asyncio.Event()
        fast_done = 0
        fast_all_done = asyncio.Event()

        class FakeJob(ChannelJob):
            async def _get_messages(self, ids):
                return [object() for _ in ids]

            async def _download_one(self, m, msg):
                nonlocal active, peak_active, fast_done
                active += 1
                peak_active = max(peak_active, active)
                try:
                    if m.msg_id == 1:
                        await slow_release.wait()
                    else:
                        await asyncio.sleep(0.01)
                        fast_done += 1
                        if fast_done == 5:
                            fast_all_done.set()
                    return "done"
                finally:
                    active -= 1

        try:
            job1 = FakeJob(None, cfg, ChannelConfig(chat="c1"), store, Throttle(0, 0), sem=global_sem)
            job2 = FakeJob(None, cfg, ChannelConfig(chat="c2"), store, Throttle(0, 0), sem=global_sem)
            todo1 = [CachedMessage(1, i, None, 0, "", None, "video", 10, None) for i in (1, 2, 3, 4)]
            todo2 = [CachedMessage(2, i, None, 0, "", None, "video", 10, None) for i in (5, 6)]

            t1 = asyncio.create_task(job1.download(todo1))
            t2 = asyncio.create_task(job2.download(todo2))

            # 即使 #1 卡住，另一个槽位也能持续消费完其余 5 个文件，且跨频道总并发不超过 2
            await asyncio.wait_for(fast_all_done.wait(), timeout=2.0)
            assert peak_active <= 2
            slow_release.set()
            await asyncio.gather(t1, t2)
            assert peak_active == 2
        finally:
            store.close()

    asyncio.run(scenario())


def test_include_folder_name_and_download_path(tmp_path: Path):
    assert include_folder_name("#SAmart") == "SAmart"
    assert include_folder_name("#4K  原盘 合集") == "4K_原盘_合集"
    assert include_folder_name("原盘|REMUX") == "原盘_REMUX"
    assert include_folder_name("") == "all"
    assert include_folder_name("#") == "all"

    class FakeClient:
        async def iter_download(self, doc, offset=0, request_size=0, file_size=None):
            yield b"hello"

    cfg = Config(api_id=1, api_hash="x", data_dir=tmp_path, download_dir=tmp_path / "dl", channels=[])
    ch = ChannelConfig(
        chat=-1001,
        name="旧频道名不应出现",
        rules=[
            MatchRule(include=["#4K 视频"], media={"video"}),
            MatchRule(include=["#壁纸 图集"], media={"photo"}),
        ],
    )
    store = Store(tmp_path / "s.sqlite3")
    try:
        m1 = CachedMessage(-1001, 10, None, 1735689600, "#4K 视频 测试", "clip.mp4", "video", 5, None)
        store.save_messages([m1])
        job = ChannelJob(FakeClient(), cfg, ch, store, Throttle(0, 0))
        job.chat_id = -1001

        todo = job.pending()
        assert [m.msg_id for m in todo] == [10]

        fake_msg = SimpleNamespace(media=object(), document=object(), file=SimpleNamespace(ext=".mp4"))
        status = asyncio.run(job._download_one(todo[0], fake_msg))
        assert status == "done"

        # 默认规则：dl/4K_视频/video/10_clip.mp4（不再使用 YYYY-MM 或频道名）
        assert (cfg.download_dir / "4K_视频" / "video" / "10_clip.mp4").exists()
        assert not (cfg.download_dir / "旧频道名不应出现").exists()

        # 自定义路径模板 & 路径穿越防护
        custom = render_folder(
            cfg.download_dir,
            "../{media_type}/{include}/{year}",
            include="#壁纸 图集",
            media_type="photo",
            channel="测试频道",
            ts=1735689600,
        )
        assert custom == cfg.download_dir / "photo" / "壁纸_图集" / "2025"

        # 未知变量应触发 ConfigError
        bad_cfg = tmp_path / "bad.toml"
        save_raw_config(bad_cfg, {
            "telegram": {"api_id": 1, "api_hash": "abc"},
            "download": {"path_template": "{unknown_var}"},
            "channels": [{"chat": -1001}],
        })
        try:
            load_config(bad_cfg)
            assert False, "should raise ConfigError"
        except ConfigError as e:
            assert "path_template" in str(e)
    finally:
        store.close()


def test_derive_search_queries():
    ch1 = ChannelConfig(chat="x", include=["#SAmart", "原盘|REMUX", r"\b4K\b", "foo.*bar"])
    assert derive_search_queries(ch1) == ["#SAmart", "原盘", "REMUX", "4K"]

    ch2 = ChannelConfig(chat="x", include=["foo.*bar"], search_queries=["#手动词"])
    assert derive_search_queries(ch2) == ["#手动词"]


def test_search_scan_mode_backfills_album_and_context(tmp_path: Path):
    def _tg_msg(msg_id: int, text: str = "", *, video: bool = False, grouped_id: int | None = None):
        return SimpleNamespace(
            id=msg_id,
            grouped_id=grouped_id,
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

    # 模拟服务端：#10 是相册文字+视频，#11 是同相册无文字视频；#20 是纯文字介绍，#21 是紧跟的无文字视频
    all_msgs = {
        10: _tg_msg(10, "#SAmart 相册1", video=True, grouped_id=777),
        11: _tg_msg(11, "", video=True, grouped_id=777),
        20: _tg_msg(20, "#SAmart 先文后视频介绍", video=False),
        21: _tg_msg(21, "", video=True),
    }
    search_calls = []

    class FakeSearchClient:
        async def iter_messages(self, entity, **kwargs):
            search_calls.append(kwargs.get("search"))
            # 服务端搜索只返回带关键词的那两条（#20, #10）
            for mid in (20, 10):
                yield all_msgs[mid]

        async def get_messages(self, entity, ids):
            return [all_msgs.get(i) for i in ids]

    cfg = Config(api_id=1, api_hash="x", data_dir=tmp_path, download_dir=tmp_path / "dl", channels=[])
    ch = ChannelConfig(
        chat=-1001,
        include=["#SAmart"],
        media={"video"},
        context_before=1,
        scan_mode="search",
    )
    store = Store(tmp_path / "s.sqlite3")
    try:
        job = ChannelJob(FakeSearchClient(), cfg, ch, store, Throttle(0, 0))
        job.chat_id = -1001
        asyncio.run(job.scan())

        assert search_calls == ["#SAmart"]
        # 确认 #10、补拉的同相册 #11、以及补拉的后续媒体 #21 全部被 Matcher 命中
        assert [m.msg_id for m in job.pending()] == [10, 11, 21]
    finally:
        store.close()


def test_filter_channels_by_scope():
    ch0 = ChannelConfig(
        chat=-1001,
        rules=[
            MatchRule(include=["#视频", "#电影"], media={"video"}),
            MatchRule(include=["#图集"], media={"photo"}),
        ],
    )
    ch1 = ChannelConfig(chat=-1002, include=["#A", "#B"], media={"document"})
    ch2 = ChannelConfig(chat=-1003, include=[], media={"video"})

    # 仅勾选 ch0 的 "#视频" 和 ch2 的全部消息（""），排除 ch1
    out = filter_channels_by_scope([ch0, ch1, ch2], {0: {"#视频"}, 2: {""}})
    assert [c.chat for c in out] == [-1001, -1003]
    assert len(out[0].rules) == 1
    assert out[0].rules[0].include == ["#视频"]
    assert out[0].rules[0].media == {"video"}
    assert out[1].include == []

    # scope=None 保留全部
    assert len(filter_channels_by_scope([ch0, ch1, ch2], None)) == 3
