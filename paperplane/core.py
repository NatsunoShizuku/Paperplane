"""扫描、匹配、下载的主流程。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient, errors, utils
from telethon.tl.custom import Message
from telethon.tl.types import MessageMediaWebPage, MessageService

from .client import ACCOUNT_FATAL, CHANNEL_FATAL, AbortRun, Throttle, handle_flood
from .config import DEFAULT_PATH_TEMPLATE, ChannelConfig, Config
from .matcher import Matcher
from .store import CachedMessage, Store

log = logging.getLogger(__name__)

CHUNK = 512 * 1024          # 分块下载大小，也是断点续传的对齐单位
SAVE_EVERY = 500            # 扫描时每多少条落盘一次


# ---------------------------------------------------------------- 工具

def media_type(msg: Message) -> str | None:
    if msg.media is None or isinstance(msg.media, MessageMediaWebPage):
        return None          # 链接预览里的图片不算
    if msg.photo:
        return "photo"
    if msg.sticker:
        return "sticker"
    if msg.gif:
        return "gif"
    if msg.video_note:
        return "video_note"
    if msg.video:
        return "video"
    if msg.voice:
        return "voice"
    if msg.audio:
        return "audio"
    if msg.document:
        return "document"
    return None


def to_cached(chat_id: int, msg: Message) -> CachedMessage:
    mtype = media_type(msg)
    f = msg.file if mtype else None
    return CachedMessage(
        chat_id=chat_id,
        msg_id=msg.id,
        grouped_id=msg.grouped_id,
        date=int(msg.date.timestamp()),
        text=msg.message or "",
        file_name=f.name if f else None,
        media_type=mtype,
        size=f.size if f else None,
        reply_to=msg.reply_to_msg_id,
    )


_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name: str, limit: int = 120) -> str:
    name = unicodedata.normalize("NFC", name)
    name = _ILLEGAL.sub("_", name).strip(" .")
    if len(name.encode()) > limit:
        stem, ext = os.path.splitext(name)
        while len((stem + ext).encode()) > limit and stem:
            stem = stem[:-1]
        name = stem + ext
    return name or "unnamed"


def include_folder_name(pattern: str) -> str:
    """将命中的 include 正则转为目录名：去除 #，将空白替换为 _，并清理非法路径字符。"""
    cleaned = pattern.replace("#", "").strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return safe_name(cleaned) if cleaned else "all"


_REGEX_META = re.compile(r"[.*+?\[\](){}\\]")


def derive_search_queries(ch: ChannelConfig) -> list[str]:
    """为 scan_mode='search' 提取 Telegram 服务端搜索词。

    优先使用显式配置的 search_queries；否则从 rules / include 中拆解字面关键词。
    """
    if ch.search_queries:
        return list(dict.fromkeys(q.strip() for q in ch.search_queries if q.strip()))

    raw_patterns: list[str] = []
    if ch.rules:
        for r in ch.rules:
            raw_patterns.extend(r.include)
    else:
        raw_patterns.extend(ch.include)

    queries: list[str] = []
    for pat in raw_patterns:
        for part in pat.split("|"):
            s = part.strip()
            s = re.sub(r"\\b", "", s)
            s = s.lstrip("^").rstrip("$")
            s = re.sub(r"\\([#\-_./])", r"\1", s)
            s = s.strip()
            if s and not _REGEX_META.search(s):
                queries.append(s)
    return list(dict.fromkeys(queries))


def render_folder(
    download_dir: Path,
    template: str,
    *,
    include: str = "",
    media_type: str = "",
    channel: str = "",
    ts: int = 0,
) -> Path:
    """根据自定义路径模板生成安全的下载子目录。"""
    dt = datetime.fromtimestamp(ts)
    mapping = {
        "include": include_folder_name(include),
        "media_type": safe_name(media_type or "other"),
        "channel": safe_name(channel or "unnamed"),
        "date": dt.strftime("%Y-%m-%d"),
        "month": dt.strftime("%Y-%m"),
        "year": dt.strftime("%Y"),
    }
    tpl = template.strip() if template is not None else DEFAULT_PATH_TEMPLATE
    if not tpl:
        return download_dir
    rendered = tpl.format_map(mapping)
    folder = download_dir
    for seg in re.split(r"[\\/]+", rendered):
        seg = seg.strip()
        if not seg or seg in (".", ".."):
            continue
        folder = folder / safe_name(seg)
    return folder


def target_path(folder: Path, m: CachedMessage, msg: Message) -> Path:
    name = m.file_name or f"{m.media_type}{msg.file.ext or ''}"
    return folder / safe_name(f"{m.msg_id}_{name}")


# ---------------------------------------------------------------- 频道

class ChannelJob:
    def __init__(
        self,
        client: TelegramClient,
        cfg: Config,
        ch: ChannelConfig,
        store: Store,
        throttle: Throttle,
        on_progress=None,
        sem: asyncio.Semaphore | None = None,
    ):
        self.client, self.cfg, self.ch, self.store, self.throttle = client, cfg, ch, store, throttle
        self.on_progress = on_progress
        self.sem = sem
        self.matcher = Matcher(ch)
        self.entity = None
        self.chat_id = 0
        self.title = ch.name or str(ch.chat)
        self.folder = cfg.download_dir
        self.matched_patterns: dict[int, str] = {}

    def _emit(self, msg_id: int, name: str, done: int, total: int, status: str):
        if self.on_progress:
            try:
                self.on_progress(self.chat_id, msg_id, name, done, total, status)
            except Exception:
                log.debug("进度回调异常", exc_info=True)

    async def resolve(self):
        """Telethon 会先查 session 缓存，命中就不会调用 ResolveUsername。"""
        await self.throttle.wait()
        self.entity = await self.client.get_entity(self.ch.chat)
        self.chat_id = utils.get_peer_id(self.entity)
        title = self.ch.name or utils.get_display_name(self.entity) or str(self.chat_id)
        self.title = title
        self.store.remember_chat(self.ch.chat, self.chat_id, title)
        log.info("频道：%s（id=%s）", title, self.chat_id)

    # ---- 扫描：增量拉取历史并缓存到本地 ----
    async def scan(self, *, min_id: int = 0):
        while True:
            try:
                if self.ch.scan_mode == "search":
                    await self._scan_search(min_id=min_id)
                else:
                    await self._scan_once()
                return
            except errors.FloodError as e:
                await handle_flood(e, self.cfg)   # 等完后继续

    async def _scan_search(self, *, min_id: int = 0):
        """频道内服务端搜索模式：按关键词独立记录 search_state 游标，不污染 full 模式的 scan_state。"""
        queries = derive_search_queries(self.ch)
        if not queries:
            log.warning("频道 %s 未提取到可用的字面搜索词，自动回退到全量遍历扫描", self.ch.chat)
            await self._scan_once()
            return

        since, until = self.ch.since, self.ch.until
        since_ts = int(since.timestamp()) if since else 0
        log.info("频道内搜索：关键词 %s%s%s", queries,
                 f"，since={since}" if since else "",
                 f"，until={until}" if until else "")

        hits: dict[int, CachedMessage] = {}
        for q in queries:
            q_last = self.store.search_last_id(self.chat_id, q)
            q_floor = self.store.search_floor_ts(self.chat_id, q)
            if min_id > 0:
                effective_min_id = min_id
            elif q_last > 0 and q_floor is not None and since_ts >= q_floor:
                effective_min_id = q_last
            else:
                effective_min_id = 0

            kwargs: dict = dict(search=q, wait_time=self.cfg.request_interval)
            if effective_min_id > 0:
                kwargs["min_id"] = effective_min_id
            if until:
                kwargs["offset_date"] = until + timedelta(seconds=1)

            q_max_id = 0
            async for msg in self.client.iter_messages(self.entity, **kwargs):
                if until and msg.date > until:
                    continue
                if since and msg.date < since:
                    break
                if isinstance(msg, MessageService):
                    continue
                hits[msg.id] = to_cached(self.chat_id, msg)
                if msg.id > q_max_id:
                    q_max_id = msg.id

            if q_max_id > 0:
                self.store.set_search_last_id(self.chat_id, q, q_max_id)
            if min_id == 0 and (q_floor is None or since_ts < q_floor):
                self.store.set_search_floor_ts(self.chat_id, q, since_ts)

        if hits:
            self._flush(list(hits.values()), update_scan_state=False)

        # 自动补拉同相册成员与 context_before 后续媒体消息，保证匹配完整性
        neighbor_ids: set[int] = set()
        for m in hits.values():
            if m.grouped_id:
                neighbor_ids.update(range(max(1, m.msg_id - 9), m.msg_id + 10))
            if self.ch.context_before > 0 and not m.media_type:
                span = max(self.ch.context_before * 2, 10)
                neighbor_ids.update(range(m.msg_id + 1, m.msg_id + 1 + span))

        neighbor_ids -= set(hits.keys())
        if neighbor_ids:
            neighbor_ids -= self.store.existing_ids(self.chat_id, neighbor_ids)

        fetched_neighbors: list[CachedMessage] = []
        sorted_ids = sorted(neighbor_ids)
        for i in range(0, len(sorted_ids), 100):
            chunk_ids = sorted_ids[i : i + 100]
            msgs = await self._get_messages(chunk_ids)
            for msg in msgs:
                if msg is None or isinstance(msg, MessageService):
                    continue
                if until and msg.date > until:
                    continue
                fetched_neighbors.append(to_cached(self.chat_id, msg))

        if fetched_neighbors:
            self._flush(fetched_neighbors, update_scan_state=False)

        log.info("搜索完成：关键词 %d 个，命中 %d 条，补拉邻近上下文 %d 条",
                 len(queries), len(hits), len(fetched_neighbors))

    async def _scan_once(self):
        """缓存区间是 [floor_ts, last_id]：
        - 首次扫描从 since 开始（没有 since 就从频道开头）；
        - since 往前调了，就向前补齐缺的那一段；
        - 向后扫到最新消息，或者扫到 until 为止（下次 until 往后调时从这里接着扫）。
        """
        since, until = self.ch.since, self.ch.until
        since_ts = int(since.timestamp()) if since else 0
        last = self.store.last_id(self.chat_id)
        floor = self.store.floor_ts(self.chat_id)

        if last and floor is not None and since_ts < floor:
            await self._backfill(floor, since_ts)

        kwargs = dict(min_id=last, reverse=True, wait_time=self.cfg.request_interval)
        if not last:
            self.store.set_floor_ts(self.chat_id, since_ts)
            if since:
                kwargs["offset_date"] = since
        log.info("扫描：从 %s 开始%s", f"msg_id > {last}" if last else (since or "频道开头"),
                 f"，到 {until} 为止" if until else "")

        batch: list[CachedMessage] = []
        total = 0
        async for msg in self.client.iter_messages(self.entity, **kwargs):
            if until and msg.date > until:
                break
            if isinstance(msg, MessageService):
                continue
            batch.append(to_cached(self.chat_id, msg))
            if len(batch) >= SAVE_EVERY:
                total += self._flush(batch)
                batch = []
        total += self._flush(batch)
        log.info("扫描完成：新增 %d 条", total)

    async def _backfill(self, floor: int, since_ts: int):
        """since 比已缓存的最早时间还早：从 floor 往回扫到 since。"""
        start = datetime.fromtimestamp(floor + 1, tz=timezone.utc)
        log.info("向前补齐：%s → %s", start.astimezone(), self.ch.since or "频道开头")
        batch: list[CachedMessage] = []
        async for msg in self.client.iter_messages(
            self.entity, offset_date=start, wait_time=self.cfg.request_interval
        ):
            if msg.date.timestamp() < since_ts:
                break
            if isinstance(msg, MessageService):
                continue
            batch.append(to_cached(self.chat_id, msg))
            if len(batch) >= SAVE_EVERY:
                self._flush(batch)
                self.store.set_floor_ts(self.chat_id, min(m.date for m in batch))
                batch = []
        self._flush(batch)
        self.store.set_floor_ts(self.chat_id, since_ts)

    def _flush(self, batch: list[CachedMessage], *, update_scan_state: bool = True) -> int:
        if batch:
            self.store.save_messages(batch)
            if update_scan_state:
                self.store.set_last_id(self.chat_id, max(m.msg_id for m in batch))
        return len(batch)

    # ---- 匹配 ----
    def pending(self, min_id: int = 0) -> list[CachedMessage]:
        msgs = self.store.messages(
            self.chat_id,
            min_id=min_id,
            context_before=self.ch.context_before,
            follow_replies=self.ch.follow_replies,
        )
        matched = self.matcher.select_with_patterns(msgs)
        done = self.store.finished_ids(self.chat_id)
        todo: list[CachedMessage] = []
        for m, pattern in matched:
            if m.msg_id not in done and m.msg_id > min_id:
                self.matched_patterns[m.msg_id] = pattern
                todo.append(m)
        return todo

    # ---- 下载 ----
    async def download(self, todo: list[CachedMessage]):
        if not todo:
            log.info("没有需要下载的文件")
            return
        log.info("待下载：%d 个文件", len(todo))
        sem = self.sem or asyncio.Semaphore(max(self.cfg.concurrency, 1))
        worker_count = min(max(self.cfg.concurrency, 1), len(todo))
        queue: asyncio.Queue[tuple[CachedMessage, Message | None] | None] = asyncio.Queue(
            maxsize=max(worker_count * 2, 10)
        )
        stats = {"done": 0, "failed": 0, "skipped": 0}

        # 生产者按需每 100 条拉取新鲜的 file_reference，消费者持续消费，避免整批木桶效应
        async def producer():
            for i in range(0, len(todo), 100):
                chunk = todo[i : i + 100]
                fresh = await self._get_messages([m.msg_id for m in chunk])
                for m, msg in zip(chunk, fresh):
                    await queue.put((m, msg))
            for _ in range(worker_count):
                await queue.put(None)

        async def worker():
            while True:
                item = await queue.get()
                if item is None:
                    return
                m, msg = item
                async with sem:
                    status = await self._download_one(m, msg)
                stats[status] += 1

        tasks = [asyncio.create_task(producer())] + [asyncio.create_task(worker()) for _ in range(worker_count)]
        try:
            done_set, pending_set = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            fatal: BaseException | None = None
            for t in done_set:
                if exc := t.exception():
                    fatal = exc
                    break
            if fatal is not None:
                for t in pending_set:
                    t.cancel()
                await asyncio.gather(*pending_set, return_exceptions=True)
                raise fatal
            if pending_set:
                await asyncio.gather(*pending_set)
        except BaseException:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        log.info("下载结束：成功 %(done)d，跳过 %(skipped)d，失败 %(failed)d", stats)
        for msg_id, err in self.store.failed(self.chat_id):
            log.warning("  失败 msg_id=%s：%s", msg_id, err)

    async def _get_messages(self, ids: list[int]):
        while True:
            await self.throttle.wait()
            try:
                return await self.client.get_messages(self.entity, ids=ids)
            except errors.FloodError as e:
                await handle_flood(e, self.cfg)

    async def _download_one(self, m: CachedMessage, msg: Message | None) -> str:
        if msg is None or msg.media is None:
            self.store.mark(self.chat_id, m.msg_id, "skipped", error="消息已删除或媒体不存在")
            return "skipped"

        dl_root = self.ch.download_dir or self.cfg.download_dir
        path_tpl = self.ch.path_template if self.ch.path_template is not None else self.cfg.path_template
        folder = render_folder(
            dl_root,
            path_tpl,
            include=self.matched_patterns.get(m.msg_id, ""),
            media_type=m.media_type or "other",
            channel=self.title,
            ts=m.date,
        )
        path = target_path(folder, m, msg)
        if path.exists() and (not m.size or path.stat().st_size == m.size):
            self.store.mark(self.chat_id, m.msg_id, "done", str(path))
            self._emit(m.msg_id, path.name, m.size or 0, m.size or 0, "done")
            return "done"
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        self._emit(m.msg_id, path.name, 0, m.size or 0, "downloading")

        last_err = ""
        for attempt in range(self.cfg.max_retries + 1):
            await self.throttle.wait()
            try:
                if msg.document:
                    await self._fetch_resumable(msg, part, m.size, msg_id=m.msg_id, display_name=path.name)
                else:
                    with open(part, "wb") as f:
                        cb = (lambda cur, tot: self._emit(m.msg_id, path.name, cur, tot or m.size or 0, "downloading"))
                        if await self.client.download_media(msg, file=f, progress_callback=cb) is None:
                            raise RuntimeError("download_media 未返回内容")
                part.replace(path)
                os.utime(path, (m.date, m.date))
                self.store.mark(self.chat_id, m.msg_id, "done", str(path))
                final_size = m.size or path.stat().st_size
                self._emit(m.msg_id, path.name, final_size, final_size, "done")
                log.info("✓ %s（%s）", path.relative_to(dl_root), _human(m.size))
                return "done"

            except errors.FileReferenceExpiredError:
                log.info("file_reference 过期，重新获取 msg_id=%s", m.msg_id)
                msg = (await self._get_messages([m.msg_id]))[0]
                if msg is None or msg.media is None:
                    self.store.mark(self.chat_id, m.msg_id, "skipped", error="消息已删除")
                    self._emit(m.msg_id, path.name, 0, m.size or 0, "skipped")
                    return "skipped"
                last_err = "FileReferenceExpired"
            except errors.FloodError as e:
                await handle_flood(e, self.cfg)
                last_err = type(e).__name__
            except (*ACCOUNT_FATAL, *CHANNEL_FATAL, AbortRun):
                raise
            except (OSError, ConnectionError, asyncio.TimeoutError, errors.RPCError, RuntimeError) as e:
                last_err = f"{type(e).__name__}: {e}"
                delay = 5 * 2**attempt
                log.warning("msg_id=%s 下载出错（%s），%ds 后重试", m.msg_id, last_err, delay)
                await asyncio.sleep(delay)

        self.store.mark(self.chat_id, m.msg_id, "failed", error=last_err)
        self._emit(m.msg_id, path.name, 0, m.size or 0, "failed")
        return "failed"

    async def _fetch_resumable(
        self,
        msg: Message,
        part: Path,
        size: int | None,
        *,
        msg_id: int = 0,
        display_name: str = "",
    ):
        """文档类文件分块下载，支持从 .part 断点续传。"""
        offset = part.stat().st_size if part.exists() else 0
        if size and offset >= size:
            offset = 0
        offset -= offset % CHUNK
        written = offset
        label = display_name or part.name
        with open(part, "r+b" if part.exists() else "wb") as f:
            f.truncate(offset)
            f.seek(offset)
            if offset:
                log.info("续传 %s：从 %s 开始", part.name, _human(offset))
                self._emit(msg_id, label, written, size or 0, "downloading")
            async for data in self.client.iter_download(
                msg.document, offset=offset, request_size=CHUNK, file_size=size
            ):
                f.write(data)
                written += len(data)
                self._emit(msg_id, label, written, size or written, "downloading")
        actual = part.stat().st_size
        if size and actual != size:
            if actual > size:
                part.unlink(missing_ok=True)
            raise RuntimeError(f"文件大小不符：{actual} != {size}")


def _human(n: int | None) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"
