"""直观交互式 CLI（基于 rich + questionary），支持配置向导、频道勾选、进度条与长期挂机。"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

from .config import MEDIA_TYPES, ConfigError, ensure_config_file, load_config, load_raw_config, save_raw_config
from .core import _human
from .service import LongRunningRunner, fetch_dialogs, preview_matches
from .store import Store

log = logging.getLogger("paperplane.cli")


def _require_cli_deps():
    try:
        import questionary
        from rich.console import Console
        from rich.panel import Panel
        from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
        from rich.table import Table
    except ImportError as e:
        raise SystemExit(
            f"缺少交互式 CLI 依赖（{e.name}），请运行：pip install rich questionary tomlkit"
        ) from e
    return questionary, Console, Panel, Table, Progress, BarColumn, DownloadColumn, TextColumn, TimeRemainingColumn, TransferSpeedColumn


def run_init_wizard(config_path: str | Path) -> bool:
    """交互式配置向导，安全写入合法的 config.toml。"""
    questionary, Console, Panel, *_ = _require_cli_deps()
    console = Console()
    path = ensure_config_file(config_path)

    try:
        raw = load_raw_config(path) if path.exists() else {}
    except ConfigError:
        raw = {}

    console.print(Panel.fit(f"[bold cyan]Paperplane 配置向导[/bold cyan]\n目标文件：{path.resolve()}"))

    tg = raw.get("telegram", {})
    api_id_str = questionary.text(
        "Telegram api_id（数字，my.telegram.org 申请）:",
        default=str(tg.get("api_id", "")),
        validate=lambda s: s.strip().isdigit() or "请输入纯数字 api_id",
    ).ask()
    if api_id_str is None:
        return False

    api_hash = questionary.text(
        "Telegram api_hash（字符串）:",
        default=str(tg.get("api_hash", "")),
        validate=lambda s: len(s.strip()) > 0 or "api_hash 不能为空",
    ).ask()
    if api_hash is None:
        return False

    use_proxy = questionary.confirm(
        "是否启用代理连接 Telegram？",
        default=bool(raw.get("proxy")),
    ).ask()
    if use_proxy is None:
        return False

    proxy_cfg = None
    if use_proxy:
        old_p = raw.get("proxy", {})
        ptype = questionary.select(
            "代理类型:",
            choices=["socks5", "http", "mtproxy"],
            default=old_p.get("type", "socks5") if old_p.get("type") in ("socks5", "http", "mtproxy") else "socks5",
        ).ask()
        if ptype is None:
            return False
        host = questionary.text("代理主机:", default=str(old_p.get("host", "127.0.0.1"))).ask()
        port_str = questionary.text(
            "代理端口:",
            default=str(old_p.get("port", 7890)),
            validate=lambda s: s.strip().isdigit() or "请输入数字端口",
        ).ask()
        if host is None or port_str is None:
            return False
        proxy_cfg = {"type": ptype, "host": host.strip(), "port": int(port_str.strip())}
        if ptype == "mtproxy":
            secret = questionary.text("MTProxy secret:", default=str(old_p.get("secret", ""))).ask()
            if secret is None:
                return False
            proxy_cfg["secret"] = secret.strip()

    dl = raw.get("download", {})
    dl_dir = questionary.text("下载保存目录:", default=str(dl.get("dir", "downloads"))).ask()
    conc_str = questionary.text(
        "并发下载数（建议 3-5）:",
        default=str(dl.get("concurrency", 3)),
        validate=lambda s: (s.strip().isdigit() and int(s) >= 1) or "请输入正整数",
    ).ask()
    if dl_dir is None or conc_str is None:
        return False

    raw["telegram"] = {"api_id": int(api_id_str.strip()), "api_hash": api_hash.strip()}
    raw["download"] = {
        "dir": dl_dir.strip() or "downloads",
        "data_dir": dl.get("data_dir", "data"),
        "concurrency": int(conc_str.strip()),
    }
    raw.setdefault("limits", {
        "request_interval": 1.0,
        "request_jitter": 0.5,
        "flood_sleep_threshold": 120,
        "max_flood_wait": 3600,
        "max_retries": 3,
    })
    if proxy_cfg:
        raw["proxy"] = proxy_cfg
    else:
        raw.pop("proxy", None)

    channels = list(raw.get("channels", []))
    if not channels:
        chat = questionary.text("首个频道（@username 或数字 ID，可稍后用“勾选频道”添加）:", default="@some_public_channel").ask()
        if chat is None:
            return False
        chat_val: str | int = int(chat.strip()) if chat.strip().lstrip("-").isdigit() else chat.strip()
        channels.append({
            "chat": chat_val,
            "include": [],
            "exclude": [],
            "media": ["video", "document"],
            "min_size": "10MB",
            "max_size": 0,
            "context_before": 1,
            "follow_replies": True,
        })
    raw["channels"] = channels

    save_raw_config(path, raw)
    console.print(f"[green]✓ 配置已保存到 {path.resolve()}[/green]")
    return True


def run_pick_channels(config_path: str | Path):
    """在线拉取对话列表，方向键空格勾选后自动写入 config.toml。"""
    questionary, Console, *_ = _require_cli_deps()
    console = Console()
    path = Path(config_path)

    cfg = load_config(path)
    with console.status("正在从 Telegram 拉取频道/群组列表..."):
        dialogs = asyncio.run(fetch_dialogs(cfg))

    if not dialogs:
        console.print("[yellow]未找到任何对话[/yellow]")
        return

    choices = [
        questionary.Choice(
            title=f"[{d.kind}] {d.name}" + (f" (@{d.username})" if d.username else "") + f"  ({d.id})",
            value=d,
        )
        for d in dialogs
    ]
    picked = questionary.checkbox("空格勾选要添加的频道，回车确认:", choices=choices).ask()
    if not picked:
        return

    include_str = questionary.text("包含关键词（正则，逗号分隔，留空=全部）:", default="").ask()
    exclude_str = questionary.text("排除关键词（正则，逗号分隔，留空=无）:", default="").ask()
    media = questionary.checkbox(
        "下载的媒体类型:",
        choices=[questionary.Choice(m, checked=(m in ("video", "document"))) for m in sorted(MEDIA_TYPES)],
    ).ask()
    min_size = questionary.text("最小体积（如 10MB，0=不限）:", default="10MB").ask()
    since = questionary.text("起始日期（如 2026-01-01，留空=不限）:", default="").ask()
    if include_str is None or exclude_str is None or not media or min_size is None or since is None:
        return

    include = [x.strip() for x in include_str.split(",") if x.strip()]
    exclude = [x.strip() for x in exclude_str.split(",") if x.strip()]

    raw = load_raw_config(path)
    existing = list(raw.get("channels", []))
    existing_keys = {str(c.get("chat")) for c in existing}

    added = 0
    for d in picked:
        if str(d.id) in existing_keys or (d.username and f"@{d.username}" in existing_keys):
            continue
        entry: dict = {
            "chat": d.id,
            "name": d.name,
            "include": include,
            "exclude": exclude,
            "media": media,
            "min_size": min_size.strip() or 0,
            "max_size": 0,
            "context_before": 1,
            "follow_replies": True,
        }
        if since.strip():
            entry["since"] = since.strip()
        existing.append(entry)
        added += 1

    raw["channels"] = existing
    save_raw_config(path, raw)
    console.print(f"[green]✓ 已新增 {added} 个频道到 {path.resolve()}[/green]")


def show_match_table(config_path: str | Path):
    """用 Rich 彩色表格展示本地缓存匹配结果。"""
    _, Console, _, Table, *_ = _require_cli_deps()
    console = Console()
    cfg = load_config(config_path)
    store = Store(cfg.db_path)
    try:
        summaries = preview_matches(cfg, store)
    finally:
        store.close()

    for s in summaries:
        if not s.scanned:
            console.print(f"[yellow]== {s.chat_key}：尚未扫描，请先运行 scan[/yellow]")
            continue
        table = Table(
            title=f"{s.title}  (命中 {len(s.items)} 个 / {_human(s.total_bytes)} / 已完成 {s.done_count})",
            show_lines=False,
        )
        table.add_column("状态", width=4, justify="center")
        table.add_column("ID", width=8)
        table.add_column("日期", width=10)
        table.add_column("类型", width=9)
        table.add_column("大小", width=10, justify="right")
        table.add_column("文件名 / 摘要")

        for m in s.items[:50]:
            flag = "[green]✓[/green]" if m.msg_id in s.done_ids else "[cyan]•[/cyan]"
            when = datetime.fromtimestamp(m.date).strftime("%Y-%m-%d")
            preview = (m.text or "").replace("\n", " ")[:40]
            label = f"{m.file_name or '-'}  [dim]{preview}[/dim]"
            table.add_row(flag, f"#{m.msg_id}", when, m.media_type or "-", _human(m.size), label)
        console.print(table)
        if len(s.items) > 50:
            console.print(f"[dim]... 共 {len(s.items)} 条，仅展示前 50 条[/dim]")


def run_with_progress(config_path: str | Path, mode: str, *, catchup_minutes: float = 30.0):
    """带实时进度条运行 scan / download / 长期挂机 watch。"""
    _, Console, Panel, _, Progress, BarColumn, DownloadColumn, TextColumn, TimeRemainingColumn, TransferSpeedColumn = _require_cli_deps()
    console = Console()

    async def _exec():
        progress = Progress(
            TextColumn("[bold blue]{task.fields[filename]}", justify="left"),
            BarColumn(bar_width=28),
            "[progress.percentage]{task.percentage:>3.1f}%",
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        )
        tasks_map: dict[tuple[int, int], int] = {}

        def on_progress(chat_id: int, msg_id: int, name: str, done: int, total: int, status: str):
            key = (chat_id, msg_id)
            short = name if len(name) <= 32 else name[:29] + "..."
            tid = tasks_map.get(key)
            if tid is None:
                tid = progress.add_task("dl", filename=short, total=max(total, 1), completed=done)
                tasks_map[key] = tid
            if status == "done":
                progress.update(tid, completed=max(total, 1), total=max(total, 1), filename=f"[green]✓ {short}[/green]")
            elif status == "failed":
                progress.update(tid, filename=f"[red]✗ {short}[/red]")
            elif status == "skipped":
                progress.update(tid, filename=f"[yellow]- {short}[/yellow]")
            else:
                progress.update(tid, completed=done, total=max(total, done, 1))

        runner = LongRunningRunner(
            config_path,
            catchup_interval=catchup_minutes * 60.0,
            on_progress=on_progress,
        )
        title = {
            "scan": "增量扫描频道消息",
            "download": "扫描并下载匹配文件",
            "watch": f"长期挂机模式（断线自动重连 + 每 {catchup_minutes:.0f} 分钟补扫，Ctrl+C 停止）",
        }[mode]
        console.print(Panel.fit(f"[bold cyan]{title}[/bold cyan]"))

        with progress:
            run_task = runner.start(mode)
            try:
                await run_task
            except (KeyboardInterrupt, asyncio.CancelledError):
                console.print("\n[yellow]正在停止挂机并保存进度...[/yellow]")
                await runner.stop()

        elapsed = int(time.time() - (runner.started_at or time.time()))
        if runner.status == "error":
            console.print(f"[red]已停止（错误）：{runner.last_error}[/red]")
        else:
            console.print(f"[green]已结束：累计完成 {runner.done_count} 个文件，重连 {runner.reconnects} 次，耗时 {elapsed}s[/green]")

    try:
        asyncio.run(_exec())
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断，进度已保存[/yellow]")


def run_interactive_menu(config_path: str | Path):
    """交互式 CLI 主菜单。"""
    questionary, Console, Panel, *_ = _require_cli_deps()
    console = Console()
    path = Path(config_path)

    while True:
        console.print(Panel.fit(f"[bold cyan]Paperplane 交互式控制台[/bold cyan]  [dim]({path})[/dim]"))
        action = questionary.select(
            "请选择操作（方向键选择，回车确认）:",
            choices=[
                questionary.Choice("🚀 启动长期挂机（补齐历史 + 实时监听 + 断线重连）", value="watch"),
                questionary.Choice("⬇️  单次扫描并下载（带进度条）", value="download"),
                questionary.Choice("🔍 仅扫描并缓存消息", value="scan"),
                questionary.Choice("📋 离线预览匹配结果（不发网络请求）", value="match"),
                questionary.Choice("➕ 从 Telegram 勾选添加频道", value="pick"),
                questionary.Choice("🔑 登录 Telegram 账号", value="login"),
                questionary.Choice("⚙️  配置向导（修改 config.toml）", value="init"),
                questionary.Choice("🖥️  启动图形界面 GUI（浏览器 / 桌面窗口）", value="gui"),
                questionary.Choice("🚪 退出", value="exit"),
            ],
        ).ask()

        if action in (None, "exit"):
            break

        try:
            if action == "init":
                run_init_wizard(path)
            elif action == "login":
                from .__main__ import cmd_login
                asyncio.run(cmd_login(load_config(path)))
            elif action == "pick":
                run_pick_channels(path)
            elif action == "match":
                show_match_table(path)
            elif action in ("scan", "download", "watch"):
                run_with_progress(path, action)
            elif action == "gui":
                from .gui import launch_gui
                launch_gui(path)
        except ConfigError as e:
            console.print(f"[red]配置错误：{e}[/red]")
        except Exception as e:
            console.print(f"[red]运行出错：{type(e).__name__}: {e}[/red]")
