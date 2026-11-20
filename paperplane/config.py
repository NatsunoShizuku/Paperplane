"""读取 TOML 配置。敏感信息（api_id / api_hash）优先从环境变量读取。"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

MEDIA_TYPES = {"photo", "video", "gif", "audio", "voice", "video_note", "document", "sticker"}
SCAN_MODES = {"full", "search"}
DEFAULT_PATH_TEMPLATE = "{include}/{media_type}"
PATH_TEMPLATE_VARS = ("include", "media_type", "channel", "date", "month", "year")


class ConfigError(SystemExit):
    """配置校验或解析失败（兼容 SystemExit，同时可被 CLI 向导和 GUI 捕获）。"""

    def __str__(self) -> str:
        return str(self.code)


@dataclass
class MatchRule:
    """单条匹配规则：命中该组 include 时，仅下载对应的 media 类型。"""
    include: list[str] = field(default_factory=list)   # 正则，任一命中即算匹配；为空表示全部匹配
    media: set[str] = field(default_factory=lambda: {"photo", "video", "document"})


@dataclass
class ChannelConfig:
    chat: str | int                      # "@username"、"username" 或数字 id（-100...）
    include: list[str] = field(default_factory=list)   # 正则，任一命中即算匹配；为空表示全部匹配
    exclude: list[str] = field(default_factory=list)   # 正则，任一命中即排除
    media: set[str] = field(default_factory=lambda: {"photo", "video", "document"})
    rules: list[MatchRule] = field(default_factory=list)  # 可选：同一频道按不同 include 分别指定 media
    min_size: int = 0                    # 字节
    max_size: int = 0                    # 字节，0 表示不限
    context_before: int = 0              # 媒体前 N 条纯文字消息也参与匹配（"先发介绍、再发文件"的频道）
    follow_replies: bool = True          # 媒体回复的那条消息命中也算匹配
    name: str | None = None              # 下载子目录名，默认用频道标题
    since: datetime | None = None        # 只要这个时间之后（含）的消息
    until: datetime | None = None        # 只要这个时间之前（含）的消息
    scan_mode: str = "full"              # full（全量遍历）或 search（频道内服务端搜索）
    search_queries: list[str] = field(default_factory=list)  # 可选：search 模式手动指定搜索词；留空自动从 include 提取

    def in_range(self, ts: int) -> bool:
        if self.since and ts < self.since.timestamp():
            return False
        if self.until and ts > self.until.timestamp():
            return False
        return True


@dataclass
class Config:
    api_id: int
    api_hash: str
    data_dir: Path
    download_dir: Path
    channels: list[ChannelConfig]
    path_template: str = DEFAULT_PATH_TEMPLATE
    proxy: dict | None = None
    concurrency: int = 3
    request_interval: float = 1.0        # 两次 API 请求之间的最小间隔（秒）
    request_jitter: float = 0.5          # 额外随机抖动（秒）
    flood_sleep_threshold: int = 120     # 低于此秒数的 FloodWait 由 Telethon 自动等待
    max_flood_wait: int = 3600           # 超过此秒数的 FloodWait 直接停止
    max_retries: int = 3

    @property
    def session_path(self) -> Path:
        return self.data_dir / "paperplane"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.sqlite3"


def _parse_size(v) -> int:
    if isinstance(v, int):
        return v
    s = str(v).strip().upper()
    units = {"K": 1024, "M": 1024**2, "G": 1024**3}
    for suffix in ("KB", "MB", "GB", "K", "M", "G"):
        if s.endswith(suffix):
            return int(float(s[: -len(suffix)]) * units[suffix[0]])
    return int(s)


def _parse_when(v, end_of_day: bool) -> datetime | None:
    """接受 TOML 日期/日期时间，或 "2024-01-01"、"2024-01-01 18:00" 字符串。

    不带时区的按本机时区理解；只写日期时，since 取当天 00:00，until 取当天 23:59:59。
    """
    if v in (None, ""):
        return None
    if isinstance(v, str):
        s = v.strip()
        try:
            v = date.fromisoformat(s) if len(s) == 10 else datetime.fromisoformat(s)
        except ValueError:
            raise ConfigError(f"无法解析日期: {s!r}，格式例如 2024-01-01 或 2024-01-01 18:00")
    if isinstance(v, time):
        raise ConfigError("日期范围需要包含日期")
    if not isinstance(v, datetime):
        v = datetime.combine(v, time.max if end_of_day else time.min)
    if v.tzinfo is None:
        v = v.astimezone()            # 本机时区
    return v


def load_raw_config(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"配置文件语法错误（{path}）：{e}") from e


def save_raw_config(path: str | Path, raw: dict) -> Path:
    import tomli_w

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(raw, f)
    return path


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"找不到配置文件：{path}（可先运行 init 或在界面中创建配置）")
    raw = load_raw_config(path)

    tg = raw.get("telegram", {})
    api_id = os.environ.get("TG_API_ID") or tg.get("api_id")
    api_hash = os.environ.get("TG_API_HASH") or tg.get("api_hash")
    if not api_id or not api_hash:
        raise ConfigError("缺少 api_id / api_hash：请设置环境变量 TG_API_ID、TG_API_HASH，或写入配置 [telegram]")

    base = path.parent
    dl = raw.get("download", {})
    lim = raw.get("limits", {})

    channels = []
    for c in raw.get("channels", []):
        media = set(c.get("media", ["photo", "video", "document"]))
        unknown = media - MEDIA_TYPES
        if unknown:
            raise ConfigError(f"未知的媒体类型: {unknown}，可选: {sorted(MEDIA_TYPES)}")
        rules: list[MatchRule] = []
        for r in c.get("rules", []):
            r_media = set(r.get("media", list(media)))
            r_unknown = r_media - MEDIA_TYPES
            if r_unknown:
                raise ConfigError(f"规则中未知的媒体类型: {r_unknown}，可选: {sorted(MEDIA_TYPES)}")
            rules.append(MatchRule(include=list(r.get("include", [])), media=r_media))
        scan_mode = str(c.get("scan_mode", "full")).strip().lower() or "full"
        if scan_mode not in SCAN_MODES:
            raise ConfigError(f"未知的 scan_mode: {scan_mode!r}，可选: {sorted(SCAN_MODES)}")
        channels.append(ChannelConfig(
            chat=c["chat"],
            include=c.get("include", []),
            exclude=c.get("exclude", []),
            media=media,
            rules=rules,
            min_size=_parse_size(c.get("min_size", 0)),
            max_size=_parse_size(c.get("max_size", 0)),
            context_before=int(c.get("context_before", 0)),
            follow_replies=bool(c.get("follow_replies", True)),
            name=c.get("name"),
            since=_parse_when(c.get("since"), end_of_day=False),
            until=_parse_when(c.get("until"), end_of_day=True),
            scan_mode=scan_mode,
            search_queries=[str(q).strip() for q in c.get("search_queries", []) if str(q).strip()],
        ))
        ch = channels[-1]
        if ch.since and ch.until and ch.since > ch.until:
            raise ConfigError(f"{ch.chat}：since 晚于 until")
    if not channels:
        raise ConfigError("配置里至少需要一个 [[channels]]")

    data_dir = (base / dl.get("data_dir", "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    path_template = str(dl.get("path_template", DEFAULT_PATH_TEMPLATE)).strip()
    if path_template:
        try:
            path_template.format_map({k: k for k in PATH_TEMPLATE_VARS})
        except (KeyError, ValueError) as e:
            raise ConfigError(
                f"无效的 path_template ({path_template!r})：{e}；可用变量：{', '.join('{' + k + '}' for k in PATH_TEMPLATE_VARS)}"
            ) from e

    return Config(
        api_id=int(api_id),
        api_hash=str(api_hash),
        data_dir=data_dir,
        download_dir=(base / dl.get("dir", "downloads")).resolve(),
        channels=channels,
        path_template=path_template,
        proxy=raw.get("proxy") or None,
        concurrency=int(dl.get("concurrency", 3)),
        request_interval=float(lim.get("request_interval", 1.0)),
        request_jitter=float(lim.get("request_jitter", 0.5)),
        flood_sleep_threshold=int(lim.get("flood_sleep_threshold", 120)),
        max_flood_wait=int(lim.get("max_flood_wait", 3600)),
        max_retries=int(lim.get("max_retries", 3)),
    )
