"""命令行入口。

    python -m paperplane                    进入交互式 CLI 控制台（方向键菜单）
    python -m paperplane init               交互式配置向导（自动生成合法 config.toml）
    python -m paperplane pick               交互式勾选频道写入配置
    python -m paperplane gui                启动图形界面 GUI（支持长期挂机）
    python -m paperplane login              交互式登录（只需一次，之后复用 session）
    python -m paperplane chats              列出对话及 id（用来填配置里的 chat）
    python -m paperplane scan               只扫描，把消息缓存到本地
    python -m paperplane match              只用本地缓存匹配，列出将要下载的文件（不发请求）
    python -m paperplane download           扫描 + 匹配 + 下载
    python -m paperplane watch              先补齐历史，再长期挂机监听（断线自动重连 + 定时补扫）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from .client import build_client
from .config import Config, ensure_config_file, load_config
from .core import ChannelJob
from .matcher import Matcher
from .service import LongRunningRunner, watch_channels
from .store import Store

log = logging.getLogger("paperplane")


async def cmd_login(cfg: Config):
    client = build_client(cfg)
    await client.start()          # 依次询问手机号、验证码、两步验证密码
    me = await client.get_me()
    print(f"已登录：{me.first_name}（id={me.id}）。session 保存在 {cfg.session_path}.session")
    await client.disconnect()


async def cmd_chats(cfg: Config):
    async with build_client(cfg) as client:
        async for d in client.iter_dialogs():
            kind = "频道" if d.is_channel and not d.is_group else "群组" if d.is_group else "私聊"
            uname = getattr(d.entity, "username", None)
            print(f"{d.id:>16}  {kind}  {d.name}" + (f"  @{uname}" if uname else ""))


def cmd_match(cfg: Config, store: Store):
    """纯本地：用缓存的消息重新匹配，不发任何请求。调关键词时反复跑这个。"""
    for ch in cfg.channels:
        found = store.lookup_chat(ch.chat)
        if not found:
            print(f"== {ch.chat}：还没扫描过，先运行 scan")
            continue
        chat_id, title = found
        selected = Matcher(ch).select(store.messages(chat_id))
        done = store.finished_ids(chat_id)
        total = sum(m.size or 0 for m in selected)
        status_note = "" if ch.enabled else " [已停用]"
        print(
            f"== {title}{status_note}：命中 {len(selected)} 个文件，"
            f"{total / 1024**2:.1f} MB（已完成 {len(done & {m.msg_id for m in selected})}）"
        )
        for msg in selected:
            when = datetime.fromtimestamp(msg.date).strftime("%Y-%m-%d")
            flag = "✓" if msg.msg_id in done else " "
            preview = (msg.text or "").replace("\n", " ")[:40]
            print(f"  {flag} #{msg.msg_id:<7} {when} {msg.media_type:<9} {msg.file_name or '-'}  {preview}")


async def watch_loop(client, store: Store, jobs: list[ChannelJob], *, debounce: float = 5.0):
    """兼容旧版调用入口：复用 service.watch_channels。"""
    await watch_channels(client, store, jobs, debounce=debounce, catchup_interval=0.0)


async def _run_via_runner(config_path: str, mode: str, *, catchup_minutes: float = 30.0):
    runner = LongRunningRunner(config_path, catchup_interval=catchup_minutes * 60.0)
    task = runner.start(mode)
    try:
        await task
    except asyncio.CancelledError:
        await runner.stop()


def main():
    ap = argparse.ArgumentParser(prog="paperplane", description="Telegram 频道关键词媒体下载")
    ap.add_argument(
        "command",
        nargs="?",
        default="ui",
        choices=["ui", "init", "pick", "gui", "login", "chats", "scan", "match", "download", "watch"],
    )
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--host", default="127.0.0.1", help="GUI 监听地址（仅 gui 命令）")
    ap.add_argument("--port", type=int, default=8765, help="GUI 监听端口（仅 gui 命令）")
    ap.add_argument("--native", action="store_true", help="尝试以独立原生窗口启动 GUI（需安装 pywebview）")
    ap.add_argument("--catchup", type=float, default=30.0, help="watch 模式定时补扫间隔（分钟，默认 30）")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("telethon").setLevel(logging.INFO if args.verbose else logging.WARNING)

    ensure_config_file(args.config)

    if args.command == "ui":
        from .cli_ui import run_interactive_menu
        run_interactive_menu(args.config)
        return
    if args.command == "init":
        from .cli_ui import run_init_wizard
        run_init_wizard(args.config)
        return
    if args.command == "pick":
        from .cli_ui import run_pick_channels
        run_pick_channels(args.config)
        return
    if args.command == "gui":
        from .gui import launch_gui
        launch_gui(args.config, host=args.host, port=args.port, native=args.native)
        return

    try:
        if args.command in ("scan", "download", "watch"):
            asyncio.run(_run_via_runner(args.config, args.command, catchup_minutes=args.catchup))
            return

        cfg = load_config(args.config)
        store = Store(cfg.db_path)
        try:
            if args.command == "login":
                asyncio.run(cmd_login(cfg))
            elif args.command == "chats":
                asyncio.run(cmd_chats(cfg))
            elif args.command == "match":
                cmd_match(cfg, store)
        finally:
            try:
                store.close()
            except Exception:
                pass
    except KeyboardInterrupt:
        log.info("已中断，进度已保存")


def main_gui():
    """独立 GUI 入口（供 pyproject.toml script 使用）。"""
    ap = argparse.ArgumentParser(prog="paperplane-gui", description="Paperplane 图形界面")
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--native", action="store_true")
    args = ap.parse_args()
    ensure_config_file(args.config)
    from .gui import launch_gui
    launch_gui(args.config, host=args.host, port=args.port, native=args.native)


if __name__ == "__main__":
    main()
