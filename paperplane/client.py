"""TelegramClient 构建、请求节流、错误分类。"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from telethon import TelegramClient, connection, errors

from .config import Config

log = logging.getLogger(__name__)


def build_client(cfg: Config, receive_updates: bool = False) -> TelegramClient:
    kwargs: dict = {}
    p = cfg.proxy
    if p:
        ptype = p.get("type", "socks5").lower()
        if ptype == "mtproxy":
            kwargs["connection"] = connection.ConnectionTcpMTProxyRandomizedIntermediate
            kwargs["proxy"] = (p["host"], int(p["port"]), p["secret"])
        elif ptype in ("socks5", "socks4", "http"):
            kwargs["proxy"] = {
                "proxy_type": ptype,
                "addr": p["host"],
                "port": int(p["port"]),
                "username": p.get("username"),
                "password": p.get("password"),
                "rdns": True,
            }
        else:
            raise SystemExit(f"不支持的代理类型: {ptype}")

    return TelegramClient(
        str(cfg.session_path),
        cfg.api_id,
        cfg.api_hash,
        flood_sleep_threshold=cfg.flood_sleep_threshold,
        receive_updates=receive_updates,
        auto_reconnect=True,
        connection_retries=None if receive_updates else 5,
        retry_delay=5,
        **kwargs,
    )


class Throttle:
    """全局请求节流：两次请求之间至少间隔 interval + [0, jitter) 秒。"""

    def __init__(self, interval: float, jitter: float):
        self.interval = interval
        self.jitter = jitter
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
            self._next = time.monotonic() + self.interval + random.uniform(0, self.jitter)


class AbortRun(Exception):
    """账号级问题或超长 FloodWait：整个运行应立即停止。"""


# 账号或 session 已失效：不重试，直接停
ACCOUNT_FATAL = (
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
    errors.PhoneNumberBannedError,
)

# 当前频道无法访问：跳过该频道
CHANNEL_FATAL = (
    errors.ChannelPrivateError,
    errors.ChannelInvalidError,
    errors.ChatAdminRequiredError,
)


async def handle_flood(e: errors.FloodError, cfg: Config):
    """FloodWait 按服务器给的秒数等待（加一点抖动）；太长就停止。"""
    seconds = int(getattr(e, "seconds", 60) or 60)
    if seconds > cfg.max_flood_wait:
        raise AbortRun(f"FloodWait {seconds}s 超过上限 {cfg.max_flood_wait}s，停止运行（{type(e).__name__}）") from e
    wait = seconds + random.uniform(1, 5)
    log.warning("%s：等待 %.0f 秒", type(e).__name__, wait)
    await asyncio.sleep(wait)
