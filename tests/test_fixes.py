import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from paperplane.__main__ import watch_loop
from paperplane.client import AbortRun, Throttle
from paperplane.config import ChannelConfig, Config
from paperplane.core import CHUNK, ChannelJob
from paperplane.matcher import Matcher
from paperplane.store import CachedMessage, Store


def _msg(i, text="", media=None, grouped=None, reply=None, name=None, size=100):
    return CachedMessage(1, i, grouped, 1000 + i, text, name, media, size if media else None, reply)


def test_store_incremental_messages_matches_full_history(tmp_path: Path):
    store = Store(tmp_path / "state.sqlite3")
    try:
        msgs = [
            _msg(1, "旧的无关消息"),
            _msg(2, "被回复的关键词介绍"),
            _msg(3, "无关1"),
            _msg(4, "前文关键词"),
            _msg(5, "相册标题 关键词", "photo", grouped=99),
            # --- min_id = 5 ---
            _msg(6, "", "video", grouped=99),      # 同相册（跨越 min_id）
            _msg(7, "", "video"),                  # 依赖前文纯文本 #4
            _msg(8, "无关分隔"),                    # 刷新前文纯文本上下文
            _msg(9, "", "document", reply=2),      # 依赖回复 #2
            _msg(10, "普通视频", "video"),
        ]
        store.save_messages(msgs)

        cfg = ChannelConfig(
            chat="x",
            include=["关键词"],
            media={"photo", "video", "document"},
            context_before=1,
            follow_replies=True,
        )
        matcher = Matcher(cfg)

        full_ids = [m.msg_id for m in matcher.select(store.messages(1)) if m.msg_id > 5]
        inc_msgs = store.messages(1, min_id=5, context_before=1, follow_replies=True)
        inc_ids = [m.msg_id for m in matcher.select(inc_msgs) if m.msg_id > 5]

        assert inc_ids == full_ids == [6, 7, 9]
        # 确认未加载无关旧消息 #1 和 #3
        assert {1, 3}.isdisjoint({m.msg_id for m in inc_msgs})
        # 无新消息时直接返回空
        assert store.messages(1, min_id=10, context_before=1, follow_replies=True) == []
    finally:
        store.close()


def test_fetch_resumable_recovers_from_oversized_part(tmp_path: Path):
    payload = b"A" * CHUNK + b"B" * 123
    seen_offsets = []

    class FakeClient:
        async def iter_download(self, doc, offset=0, request_size=CHUNK, file_size=None):
            seen_offsets.append(offset)
            yield payload[offset:]

    cfg = Config(api_id=1, api_hash="x", data_dir=tmp_path, download_dir=tmp_path, channels=[])
    job = ChannelJob(FakeClient(), cfg, ChannelConfig(chat="x"), Store(tmp_path / "s.sqlite3"), Throttle(0, 0))
    part = tmp_path / "file.bin.part"
    try:
        # 1) 损坏且超过目标大小的 .part 应归零重下
        part.write_bytes(b"X" * (len(payload) + 50))
        asyncio.run(job._fetch_resumable(SimpleNamespace(document=object()), part, len(payload)))
        assert seen_offsets == [0]
        assert part.read_bytes() == payload

        # 2) 正常中断的 .part 应按 CHUNK 对齐续传
        seen_offsets.clear()
        part.write_bytes(b"A" * (CHUNK + 17))
        asyncio.run(job._fetch_resumable(SimpleNamespace(document=object()), part, len(payload)))
        assert seen_offsets == [CHUNK]
        assert part.read_bytes() == payload
    finally:
        job.store.close()


def test_fetch_resumable_unlinks_only_oversized_mismatch(tmp_path: Path):
    class OversizeClient:
        async def iter_download(self, doc, offset=0, request_size=CHUNK, file_size=None):
            yield b"0123456789"

    class ShortClient:
        async def iter_download(self, doc, offset=0, request_size=CHUNK, file_size=None):
            yield b"012"

    cfg = Config(api_id=1, api_hash="x", data_dir=tmp_path, download_dir=tmp_path, channels=[])
    store = Store(tmp_path / "s.sqlite3")
    try:
        part_over = tmp_path / "over.part"
        job_over = ChannelJob(OversizeClient(), cfg, ChannelConfig(chat="x"), store, Throttle(0, 0))
        with pytest.raises(RuntimeError, match="文件大小不符"):
            asyncio.run(job_over._fetch_resumable(SimpleNamespace(document=object()), part_over, 5))
        assert not part_over.exists()

        part_short = tmp_path / "short.part"
        job_short = ChannelJob(ShortClient(), cfg, ChannelConfig(chat="x"), store, Throttle(0, 0))
        with pytest.raises(RuntimeError, match="文件大小不符"):
            asyncio.run(job_short._fetch_resumable(SimpleNamespace(document=object()), part_short, 5))
        assert part_short.exists()
    finally:
        store.close()


class _FakeWatchClient:
    def __init__(self):
        self.handler = None
        self.disconnected = asyncio.Event()

    def on(self, _event):
        def decorator(fn):
            self.handler = fn
            return fn
        return decorator

    async def disconnect(self):
        self.disconnected.set()

    async def run_until_disconnected(self):
        await self.disconnected.wait()


def _fake_event(chat_id: int, msg_id: int, text: str = ""):
    msg = SimpleNamespace(
        id=msg_id,
        grouped_id=None,
        date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        message=text,
        media=None,
        file=None,
        reply_to_msg_id=None,
    )
    return SimpleNamespace(chat_id=chat_id, message=msg)


def test_watch_loop_does_not_cancel_active_download(tmp_path: Path):
    async def scenario():
        client = _FakeWatchClient()
        store = Store(tmp_path / "s.sqlite3")
        started = asyncio.Event()
        release = asyncio.Event()
        done_second = asyncio.Event()
        calls = []
        cancelled = False

        class FakeJob:
            chat_id = 100
            entity = object()
            ch = ChannelConfig(chat="x")

            def pending(self, min_id: int = 0):
                return [store.last_id(self.chat_id)]

            async def download(self, todo):
                nonlocal cancelled
                calls.append(todo[0])
                if len(calls) == 1:
                    started.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled = True
                        raise
                else:
                    done_second.set()

        try:
            task = asyncio.create_task(watch_loop(client, store, [FakeJob()], debounce=0.03))
            await asyncio.sleep(0)
            await client.handler(_fake_event(100, 1))
            await asyncio.wait_for(started.wait(), timeout=1.0)

            # 下载进行期间又来新消息：不能取消正在进行的下载，且完成后应补跑一次
            await client.handler(_fake_event(100, 2))
            await asyncio.sleep(0.06)
            assert not cancelled

            release.set()
            await asyncio.wait_for(done_second.wait(), timeout=1.0)
            await client.disconnect()
            await asyncio.wait_for(task, timeout=1.0)

            assert not cancelled
            assert calls == [1, 2]
        finally:
            store.close()

    asyncio.run(scenario())


def test_watch_loop_propagates_abort_run(tmp_path: Path):
    async def scenario():
        client = _FakeWatchClient()
        store = Store(tmp_path / "s.sqlite3")

        class FailingJob:
            chat_id = 200
            entity = object()
            ch = ChannelConfig(chat="x")

            def pending(self, min_id: int = 0):
                return [1]

            async def download(self, todo):
                raise AbortRun("flood too long")

        try:
            task = asyncio.create_task(watch_loop(client, store, [FailingJob()], debounce=0.01))
            await asyncio.sleep(0)
            await client.handler(_fake_event(200, 1))
            with pytest.raises(AbortRun, match="flood too long"):
                await asyncio.wait_for(task, timeout=1.0)
        finally:
            store.close()

    asyncio.run(scenario())
