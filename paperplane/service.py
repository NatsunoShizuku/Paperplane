"""供 CLI 与 GUI 共用的服务层：分步登录、频道查询、离线匹配预览、长期挂机运行器。"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

from telethon import errors, events

from .client import ACCOUNT_FATAL, CHANNEL_FATAL, AbortRun, Throttle, build_client
from .config import ChannelConfig, Config, MatchRule, load_config
from .core import ChannelJob, to_cached
from .matcher import Matcher
from .store import CachedMessage, Store

log = logging.getLogger("paperplane.service")


@dataclass
class DialogInfo:
    id: int
    kind: str
    name: str
    username: str | None
    avatar_path: str | None = None


@dataclass
class MatchSummary:
    chat_key: str
    title: str
    scanned: bool
    items: list[CachedMessage]
    done_ids: set[int]
    chat_id: int | None = None

    @property
    def total_bytes(self) -> int:
        return sum(m.size or 0 for m in self.items)

    @property
    def done_count(self) -> int:
        return len(self.done_ids & {m.msg_id for m in self.items})


@dataclass
class DownloadProgress:
    chat_id: int
    msg_id: int
    name: str
    done: int
    total: int
    status: str          # downloading / done / failed / skipped
    updated_at: float


class AuthSession:
    """非阻塞分步登录会话，供 GUI 表单使用（不调用终端 input()）。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = build_client(cfg, receive_updates=False)
        self._phone: str = ""
        self._phone_code_hash: str = ""

    async def connect(self):
        if not self.client.is_connected():
            await self.client.connect()

    async def get_me(self):
        await self.connect()
        if not await self.client.is_user_authorized():
            return None
        return await self.client.get_me()

    async def send_code(self, phone: str):
        await self.connect()
        self._phone = phone.strip()
        sent = await self.client.send_code_request(self._phone)
        self._phone_code_hash = sent.phone_code_hash

    async def sign_in(self, code: str, password: str = ""):
        await self.connect()
        try:
            await self.client.sign_in(
                phone=self._phone,
                code=code.strip(),
                phone_code_hash=self._phone_code_hash,
            )
        except errors.SessionPasswordNeededError:
            if not password:
                raise
            await self.client.sign_in(password=password)
        return await self.client.get_me()

    async def sign_in_password(self, password: str):
        await self.connect()
        await self.client.sign_in(password=password)
        return await self.client.get_me()

    async def disconnect(self):
        if self.client.is_connected():
            await self.client.disconnect()


async def fetch_dialogs(
    cfg: Config,
    *,
    with_avatars: bool = False,
    avatar_limit: int = 80,
) -> list[DialogInfo]:
    client = build_client(cfg, receive_updates=False)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("尚未登录 Telegram，请先完成登录")
        avatar_dir = cfg.data_dir / "avatars"
        if with_avatars:
            avatar_dir.mkdir(parents=True, exist_ok=True)

        dialogs_raw = []
        result: list[DialogInfo] = []
        async for d in client.iter_dialogs():
            kind = "频道" if d.is_channel and not d.is_group else "群组" if d.is_group else "私聊"
            uname = getattr(d.entity, "username", None)
            cached_file = avatar_dir / f"{d.id}.jpg"
            info = DialogInfo(
                id=d.id,
                kind=kind,
                name=d.name or str(d.id),
                username=uname,
                avatar_path=str(cached_file) if cached_file.exists() else None,
            )
            result.append(info)
            dialogs_raw.append((d, info, cached_file))

        if with_avatars:
            sem = asyncio.Semaphore(4)
            flood_hit = False

            async def _grab(d, info: DialogInfo, dest: Path):
                nonlocal flood_hit
                if flood_hit or info.avatar_path or not getattr(d.entity, "photo", None):
                    return
                async with sem:
                    if flood_hit:
                        return
                    try:
                        buf = io.BytesIO()
                        out = await client.download_profile_photo(d.entity, file=buf, download_big=False)
                        if out is not None and buf.tell() > 0:
                            dest.write_bytes(buf.getvalue())
                            info.avatar_path = str(dest)
                    except errors.FloodError:
                        flood_hit = True
                    except Exception:
                        log.debug("下载头像失败 chat_id=%s", d.id, exc_info=True)

            await asyncio.gather(*(_grab(d, info, f) for d, info, f in dialogs_raw[:avatar_limit]))

        return result
    finally:
        await client.disconnect()


def preview_matches(cfg: Config, store: Store) -> list[MatchSummary]:
    summaries: list[MatchSummary] = []
    for ch in cfg.channels:
        found = store.lookup_chat(ch.chat)
        if not found:
            summaries.append(MatchSummary(str(ch.chat), str(ch.chat), False, [], set()))
            continue
        chat_id, title = found
        items = Matcher(ch).select(store.messages(chat_id))
        done = store.finished_ids(chat_id)
        summaries.append(MatchSummary(str(ch.chat), title, True, items, done, chat_id=chat_id))
    return summaries


def filter_channels_by_scope(
    channels: list[ChannelConfig],
    scope: dict[int, set[str] | None] | None,
) -> list[ChannelConfig]:
    """按挂机控制台勾选的频道索引与关键词过滤运行时频道配置（不修改磁盘 config.toml）。"""
    if scope is None:
        return list(channels)

    filtered: list[ChannelConfig] = []
    for idx, ch in enumerate(channels):
        if idx not in scope:
            continue
        sel = scope[idx]
        if sel is None:
            filtered.append(ch)
            continue
        if not sel:
            continue

        if ch.rules:
            new_rules: list[MatchRule] = []
            for r in ch.rules:
                if not r.include:
                    if "" in sel:
                        new_rules.append(MatchRule(include=[], media=set(r.media)))
                else:
                    kept = [p for p in r.include if p in sel]
                    if kept:
                        new_rules.append(MatchRule(include=kept, media=set(r.media)))
            if new_rules:
                filtered.append(replace(ch, rules=new_rules))
        else:
            if not ch.include:
                if "" in sel:
                    filtered.append(ch)
            else:
                kept = [p for p in ch.include if p in sel]
                if kept:
                    filtered.append(replace(ch, include=kept))
    return filtered


class _RingLogHandler(logging.Handler):
    def __init__(self, buf: deque[str]):
        super().__init__()
        self.buf = buf
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord):
        try:
            self.buf.append(self.format(record))
        except Exception:
            pass


class LongRunningRunner:
    """支持长期挂机的任务运行器（断线自动重连 + 定时补扫防漏 + 实时进度跟踪）。"""

    def __init__(
        self,
        config_path: str | Path,
        *,
        catchup_interval: float = 1800.0,
        debounce: float = 5.0,
        on_progress=None,
    ):
        self.config_path = Path(config_path)
        self.catchup_interval = catchup_interval
        self.debounce = debounce
        self._external_progress = on_progress

        self.status: str = "idle"
        self.mode: str = ""
        self.last_error: str = ""
        self.started_at: float | None = None
        self.reconnects: int = 0
        self.done_count: int = 0
        self.downloads: dict[tuple[int, int], DownloadProgress] = {}
        self.logs: deque[str] = deque(maxlen=300)

        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._client = None
        self._selected_scope: dict[int, set[str] | None] | None = None
        self._log_handler = _RingLogHandler(self.logs)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _on_progress(self, chat_id: int, msg_id: int, name: str, done: int, total: int, status: str):
        key = (chat_id, msg_id)
        prev = self.downloads.get(key)
        if status == "done" and (not prev or prev.status != "done"):
            self.done_count += 1
        self.downloads[key] = DownloadProgress(
            chat_id=chat_id,
            msg_id=msg_id,
            name=name,
            done=done,
            total=total,
            status=status,
            updated_at=time.time(),
        )
        # 控制内存：仅保留最近 50 条记录
        if len(self.downloads) > 50:
            oldest = sorted(self.downloads.items(), key=lambda kv: kv[1].updated_at)[:-50]
            for k, _ in oldest:
                if self.downloads[k].status != "downloading":
                    self.downloads.pop(k, None)
        if self._external_progress:
            self._external_progress(chat_id, msg_id, name, done, total, status)

    def start(
        self,
        mode: str = "watch",
        *,
        selected_scope: dict[int, set[str] | None] | None = None,
    ) -> asyncio.Task:
        if self.is_running:
            raise RuntimeError("已有任务正在运行")
        self.mode = mode
        self._selected_scope = selected_scope
        self.last_error = ""
        self.started_at = time.time()
        self.reconnects = 0
        self.done_count = 0
        self.downloads.clear()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(mode))
        return self._task

    async def stop(self):
        if not self.is_running:
            return
        self.status = "stopping"
        if self._stop_event:
            self._stop_event.set()
        if self._client and self._client.is_connected():
            try:
                await self._client.disconnect()
            except Exception:
                pass
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status = "idle"

    async def _run(self, mode: str):
        pkg_log = logging.getLogger("paperplane")
        pkg_log.addHandler(self._log_handler)
        backoff = 5.0
        try:
            while self._stop_event and not self._stop_event.is_set():
                self.status = "starting" if self.reconnects == 0 else "reconnecting"
                cfg = load_config(self.config_path)
                store = Store(cfg.db_path)
                try:
                    await self._run_session(cfg, store, mode)
                    if mode != "watch" or self._stop_event.is_set():
                        self.status = "idle"
                        return
                    # watch 模式若意外断开且未主动停止，触发自动重连
                    self.reconnects += 1
                    log.warning("连接已断开，%.0f 秒后自动重连（第 %d 次）", backoff, self.reconnects)
                except (AbortRun, *ACCOUNT_FATAL) as e:
                    self.last_error = f"{type(e).__name__}: {e}"
                    self.status = "error"
                    log.error("遇到致命错误，停止挂机：%s", self.last_error)
                    return
                except asyncio.CancelledError:
                    self.status = "idle"
                    raise
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {e}"
                    if mode != "watch":
                        self.status = "error"
                        log.exception("执行失败")
                        return
                    self.reconnects += 1
                    log.warning("挂机连接异常（%s），%.0f 秒后自动重连（第 %d 次）", self.last_error, backoff, self.reconnects)
                finally:
                    store.close()

                # 等待退避时间或停止信号
                self.status = "reconnecting"
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    self.status = "idle"
                    return
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2, 60.0)
        finally:
            self._client = None
            pkg_log.removeHandler(self._log_handler)

    async def _run_session(self, cfg: Config, store: Store, mode: str):
        watch = mode == "watch"
        download = mode in ("download", "watch")
        client = build_client(cfg, receive_updates=watch)
        self._client = client
        throttle = Throttle(cfg.request_interval, cfg.request_jitter)
        global_sem = asyncio.Semaphore(max(cfg.concurrency, 1))
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise AbortRun("尚未登录 Telegram，请先完成登录")
            self.status = "running"
            active_channels = filter_channels_by_scope(cfg.channels, self._selected_scope)
            if not active_channels:
                raise AbortRun("未勾选任何有效的下载频道或关键词")
            jobs: list[ChannelJob] = []
            for ch in active_channels:
                if self._stop_event and self._stop_event.is_set():
                    return
                job = ChannelJob(
                    client, cfg, ch, store, throttle,
                    on_progress=self._on_progress,
                    sem=global_sem,
                )
                try:
                    await job.resolve()
                    await job.scan()
                    if download:
                        await job.download(job.pending())
                    jobs.append(job)
                except CHANNEL_FATAL as e:
                    log.error("无法访问频道 %s：%s，跳过", ch.chat, type(e).__name__)

            if watch and jobs and self._stop_event and not self._stop_event.is_set():
                await self._watch_with_catchup(client, store, jobs)
        finally:
            if client.is_connected():
                await client.disconnect()
            self._client = None

    async def _watch_with_catchup(self, client, store: Store, jobs: list[ChannelJob]):
        loop = asyncio.get_running_loop()
        by_chat = {j.chat_id: j for j in jobs}
        start_ids = {j.chat_id: store.last_id(j.chat_id) for j in jobs}
        timers: dict[int, asyncio.TimerHandle] = {}
        workers: dict[int, asyncio.Task] = {}
        dirty: set[int] = set()
        fatal_error: BaseException | None = None

        async def run_worker(job: ChannelJob):
            nonlocal fatal_error
            try:
                while True:
                    dirty.discard(job.chat_id)
                    await job.download(job.pending(min_id=start_ids[job.chat_id]))
                    if job.chat_id not in dirty:
                        break
            except CHANNEL_FATAL as e:
                log.error("无法访问频道 %s：%s，停止监听该频道", job.ch.chat, type(e).__name__)
                by_chat.pop(job.chat_id, None)
                if not by_chat:
                    await client.disconnect()
            except (*ACCOUNT_FATAL, AbortRun) as e:
                if fatal_error is None:
                    fatal_error = e
                await client.disconnect()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("监听处理频道 %s 时出错", job.ch.chat)

        def trigger(job: ChannelJob):
            timers.pop(job.chat_id, None)
            worker = workers.get(job.chat_id)
            if worker and not worker.done():
                dirty.add(job.chat_id)
                return
            workers[job.chat_id] = asyncio.create_task(run_worker(job))

        @client.on(events.NewMessage(chats=[j.entity for j in jobs]))
        async def on_message(event):
            job = by_chat.get(event.chat_id)
            if not job:
                return
            if job.ch.until and event.message.date > job.ch.until:
                return
            store.save_messages([to_cached(job.chat_id, event.message)])
            store.set_last_id(job.chat_id, event.message.id)
            if t := timers.pop(job.chat_id, None):
                t.cancel()
            timers[job.chat_id] = loop.call_later(self.debounce, trigger, job)

        async def catchup_loop():
            nonlocal fatal_error
            while self._stop_event and not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.catchup_interval)
                    return
                except asyncio.TimeoutError:
                    pass
                log.info("定期补扫检查开始（防止休眠或断网漏消息）")
                for job in list(by_chat.values()):
                    if self._stop_event.is_set():
                        return
                    try:
                        await job.scan(min_id=store.last_id(job.chat_id))
                        trigger(job)
                    except CHANNEL_FATAL as e:
                        log.error("定期补扫无法访问频道 %s：%s", job.ch.chat, type(e).__name__)
                        by_chat.pop(job.chat_id, None)
                    except (*ACCOUNT_FATAL, AbortRun) as e:
                        if fatal_error is None:
                            fatal_error = e
                        await client.disconnect()
                        return
                    except Exception as e:
                        log.warning("定期补扫频道 %s 失败：%s", job.ch.chat, e)

        catchup_task = asyncio.create_task(catchup_loop()) if self.catchup_interval > 0 else None
        log.info("长期挂机已启动：监听 %d 个频道（每 %.0f 分钟自动补扫防漏）", len(jobs), self.catchup_interval / 60)
        try:
            await client.run_until_disconnected()
        finally:
            if catchup_task and not catchup_task.done():
                catchup_task.cancel()
                await asyncio.gather(catchup_task, return_exceptions=True)
            for t in timers.values():
                t.cancel()
            timers.clear()
            running = [w for w in workers.values() if not w.done()]
            for w in running:
                w.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)

        if fatal_error is not None:
            raise fatal_error
