"""读取与无损保存 TOML 配置。

特性：
- 敏感信息（api_id / api_hash）优先从环境变量 TG_API_ID / TG_API_HASH 读取；
- 支持同级 config.local.toml 覆盖层（仅覆盖差异字段，不污染主配置）；
- 支持 [channel_defaults] 频道默认配置继承，[[channels]] 只需填写差异项；
- 使用 tomlkit 无损保存，保留原文件中的注释、空行与字段顺序。
"""
from __future__ import annotations

import os
import shutil
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
    enabled: bool = True                 # 可选：false 临时停用该频道而不删除配置
    path_template: str | None = None     # 可选：覆盖全局 [download].path_template
    download_dir: Path | None = None     # 可选：覆盖全局 [download].dir

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
    if not s:
        return 0
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


def _validate_path_template(tpl: str) -> str:
    cleaned = tpl.strip()
    if cleaned:
        try:
            cleaned.format_map({k: k for k in PATH_TEMPLATE_VARS})
        except (KeyError, ValueError) as e:
            raise ConfigError(
                f"无效的 path_template ({cleaned!r})：{e}；可用变量："
                f"{', '.join('{' + k + '}' for k in PATH_TEMPLATE_VARS)}"
            ) from e
    return cleaned


def ensure_config_file(path: str | Path) -> Path:
    """若主配置文件不存在且同目录存在 config.example.toml，则自动复制初始化（保留全部注释）。"""
    p = Path(path)
    if p.exists():
        return p
    example = p.with_name(f"{p.stem}.example{p.suffix}") if p.suffix else p.parent / "config.example.toml"
    if not example.exists() and p.name == "config.toml":
        example = p.parent / "config.example.toml"
    if example.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(example, p)
    return p


def local_override_path(path: str | Path) -> Path | None:
    """返回同级 *.local.toml 覆盖文件路径（若 path 本身已是 *.local.toml 则返回 None）。"""
    p = Path(path)
    if p.stem.endswith(".local"):
        return None
    return p.with_name(f"{p.stem}.local{p.suffix}")


def _deep_merge_raw(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_raw(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_raw_config(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"配置文件语法错误（{path}）：{e}") from e


def load_merged_raw_config(path: str | Path) -> dict:
    """读取主配置，并在存在同级 config.local.toml 时自动深度合并覆盖。"""
    p = Path(path)
    raw = load_raw_config(p)
    ov_path = local_override_path(p)
    if ov_path and ov_path.exists():
        ov_raw = load_raw_config(ov_path)
        raw = _deep_merge_raw(raw, ov_raw)
    return raw


def _is_table_list(val) -> bool:
    return isinstance(val, list) and len(val) > 0 and all(isinstance(x, dict) for x in val)


def _sync_toml_container(container, data: dict) -> None:
    """原地将 Python dict 同步到 tomlkit 容器，尽量保留原有注释、空行与字段顺序。"""
    import tomlkit
    from tomlkit.items import AoT, Table

    # 移除已被删除的键
    for existing_key in list(container.keys()):
        if existing_key not in data or data[existing_key] is None:
            del container[existing_key]

    for key, val in data.items():
        if val is None:
            continue

        if isinstance(val, dict):
            if key in container and isinstance(container[key], (Table, dict)):
                _sync_toml_container(container[key], val)
            else:
                tbl = tomlkit.table()
                _sync_toml_container(tbl, val)
                container[key] = tbl
        elif _is_table_list(val):
            if key in container and isinstance(container[key], AoT):
                aot = container[key]
                common = min(len(aot), len(val))
                for i in range(common):
                    _sync_toml_container(aot[i], val[i])
                for i in range(common, len(val)):
                    tbl = tomlkit.table()
                    _sync_toml_container(tbl, val[i])
                    aot.append(tbl)
                while len(aot) > len(val):
                    del aot[-1]
            else:
                aot = tomlkit.aot()
                for entry in val:
                    tbl = tomlkit.table()
                    _sync_toml_container(tbl, entry)
                    aot.append(tbl)
                container[key] = aot
        else:
            if key in container:
                old_item = container.item(key)
                try:
                    if old_item.unwrap() == val:
                        continue
                except Exception:
                    pass
                new_item = tomlkit.item(val)
                # 保留原有的行尾注释
                old_comment = getattr(old_item.trivia, "comment", "")
                old_comment_ws = getattr(old_item.trivia, "comment_ws", "")
                if old_comment:
                    new_item.trivia.comment_ws = old_comment_ws or "  "
                    new_item.trivia.comment = old_comment
                container[key] = new_item
            else:
                container[key] = tomlkit.item(val)


def save_raw_config(path: str | Path, raw: dict) -> Path:
    """使用 tomlkit 无损保存配置：文件已存在时原地更新以保留注释与排版。"""
    import tomlkit

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = None
    if path.exists():
        try:
            doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        except Exception:
            doc = None
    if doc is None:
        doc = tomlkit.document()

    _sync_toml_container(doc, raw)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"找不到配置文件：{path}（可先运行 init 或在界面中创建配置）")
    raw = load_merged_raw_config(path)

    tg = raw.get("telegram", {})
    api_id = os.environ.get("TG_API_ID") or tg.get("api_id")
    api_hash = os.environ.get("TG_API_HASH") or tg.get("api_hash")
    if not api_id or not api_hash:
        raise ConfigError("缺少 api_id / api_hash：请设置环境变量 TG_API_ID、TG_API_HASH，或写入配置 [telegram]")

    base = path.parent
    dl = raw.get("download", {})
    lim = raw.get("limits", {})
    ch_defaults = raw.get("channel_defaults", {}) or {}
    if not isinstance(ch_defaults, dict):
        raise ConfigError("[channel_defaults] 必须是一个表（key = value 配置段）")

    default_media = list(ch_defaults.get("media", ["photo", "video", "document"]))
    channels = []
    for idx, c in enumerate(raw.get("channels", [])):
        if "chat" not in c or str(c["chat"]).strip() == "":
            raise ConfigError(f"第 {idx + 1} 个 [[channels]] 缺少必填字段 chat")

        # 合并 [channel_defaults]：仅当频道本身未定义某字段时继承默认值
        eff = dict(ch_defaults)
        eff.update(c)
        # 若频道显式配置了 include 但未配置 rules，避免误继承 defaults.rules
        if "include" in c and "rules" not in c:
            eff.pop("rules", None)
        # 若频道显式配置了 rules 但未配置 include，避免误继承 defaults.include
        if "rules" in c and "include" not in c:
            eff.pop("include", None)

        media = set(eff.get("media", default_media))
        unknown = media - MEDIA_TYPES
        if unknown:
            raise ConfigError(f"未知的媒体类型: {unknown}，可选: {sorted(MEDIA_TYPES)}")

        rules: list[MatchRule] = []
        for r in eff.get("rules", []):
            r_media = set(r.get("media", list(media)))
            r_unknown = r_media - MEDIA_TYPES
            if r_unknown:
                raise ConfigError(f"规则中未知的媒体类型: {r_unknown}，可选: {sorted(MEDIA_TYPES)}")
            rules.append(MatchRule(include=list(r.get("include", [])), media=r_media))

        scan_mode = str(eff.get("scan_mode", "full")).strip().lower() or "full"
        if scan_mode not in SCAN_MODES:
            raise ConfigError(f"未知的 scan_mode: {scan_mode!r}，可选: {sorted(SCAN_MODES)}")

        ch_path_tpl: str | None = None
        if "path_template" in eff and eff["path_template"] is not None:
            ch_path_tpl = _validate_path_template(str(eff["path_template"]))

        ch_dl_dir: Path | None = None
        if eff.get("download_dir"):
            ch_dl_dir = (base / str(eff["download_dir"])).resolve()

        channels.append(ChannelConfig(
            chat=c["chat"],
            include=list(eff.get("include", [])),
            exclude=list(eff.get("exclude", [])),
            media=media,
            rules=rules,
            min_size=_parse_size(eff.get("min_size", 0)),
            max_size=_parse_size(eff.get("max_size", 0)),
            context_before=int(eff.get("context_before", 0)),
            follow_replies=bool(eff.get("follow_replies", True)),
            name=c.get("name"),
            since=_parse_when(eff.get("since"), end_of_day=False),
            until=_parse_when(eff.get("until"), end_of_day=True),
            scan_mode=scan_mode,
            search_queries=[str(q).strip() for q in eff.get("search_queries", []) if str(q).strip()],
            enabled=bool(eff.get("enabled", True)),
            path_template=ch_path_tpl,
            download_dir=ch_dl_dir,
        ))
        ch = channels[-1]
        if ch.since and ch.until and ch.since > ch.until:
            raise ConfigError(f"{ch.chat}：since 晚于 until")
    if not channels:
        raise ConfigError("配置里至少需要一个 [[channels]]")

    data_dir = (base / dl.get("data_dir", "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    path_template = _validate_path_template(str(dl.get("path_template", DEFAULT_PATH_TEMPLATE)))

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
