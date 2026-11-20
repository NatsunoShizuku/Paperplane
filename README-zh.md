# Paperplane

[English](README.md) | **简体中文**

基于 Python 与 [Telethon](https://github.com/LonamiWebs/Telethon) 构建的跨平台（Windows / macOS）**Telegram 频道关键词媒体下载器**，内置 **Material Design 3 图形界面**与**交互式 CLI**，专为长期挂机稳定下载设计。

---

## 核心特性

- **先缓存、后离线匹配**：消息元数据持久化到 SQLite，反复调试正则表达式执行 `match` 完全本地运行，不消耗 Telegram API 配额。
- **双扫描模式（按频道可选）**：
  - `full`（默认）：增量遍历历史消息，支持将 `since` 往前调整时自动向前补齐。
  - `search`：调用 Telegram 频道内服务端搜索直查关键词，自动补拉同相册成员与前置上下文媒体，在超大历史频道中提速显著。
- **精细化匹配规则**：
  - 同一频道支持配置多组 `include → media` 规则（例如命中 `#视频` 仅下视频、命中 `#图集` 仅下图片与文档）。
  - 支持相册（`grouped_id`）文字共享、前置纯文本上下文（`context_before`）、回复追踪（`follow_replies`）、排除正则（`exclude`）、体积与日期范围过滤。
- **自定义下载路径模板**：
  - 默认按 `{include}/{media_type}` 归档（自动去除 `#`、空白转 `_`），支持自由组合 `{include}`、`{media_type}`、`{channel}`、`{date}`、`{month}`、`{year}`，并可在 GUI 中实时预览。
- **长期挂机与防风控**：
  - 全局跨频道并发封顶（共享 `Semaphore`）+ 生产者-消费者 Worker 队列，消除大文件木桶效应。
  - 请求节流 + 随机抖动、`FloodWait` 自动等待与超限熔断、`file_reference` 批量刷新与过期重试。
  - `watch` 挂机模式支持断线指数退避自动重连 + 定期补扫防漏；文档类文件支持 512KB 分块断点续传。
- **三种交互方式**：
  - **Material 3 GUI**（NiceGUI）：深/浅色模式、频道头像缓存、控制台勾选本次下载频道与关键词、实时下载队列与日志终端、关闭浏览器标签页不影响后台继续挂机。
  - **交互式 CLI**（Rich + Questionary）：方向键菜单、配置向导、空格勾选频道、实时下载进度条。
  - **经典命令行**：适合脚本调用与无头服务器自动化。

---

## 环境要求

- Python **3.11+**（推荐 Python 3.12）
- Telegram API 凭证：`api_id` 与 `api_hash`（前往 [my.telegram.org](https://my.telegram.org) → *API development tools* 申请）

---

## 快速开始

### Windows

```powershell
# 1. 创建独立虚拟环境并安装依赖
py -3.12 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install -r requirements.txt

# 2. 复制配置模板并修改（或直接启动 GUI/向导填写）
Copy-Item config.example.toml config.toml

# 3. 启动图形界面或交互式 CLI（也可直接双击根目录 run-gui.bat / run-cli.bat）
.\.venv-win\Scripts\python.exe -m paperplane gui
# 或
.\.venv-win\Scripts\python.exe -m paperplane ui
```

### macOS

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 2. 复制配置模板并修改
cp config.example.toml config.toml

# 3. 启动图形界面或交互式 CLI
./.venv/bin/python -m paperplane gui
# 或
./.venv/bin/python -m paperplane ui
```

> 双系统共用同一项目目录时，请分别使用 `.venv`（macOS）与 `.venv-win`（Windows）；`data/` 下的 session 与 SQLite 状态可跨平台轮流使用，但**切勿两台机器同时运行同一份 session**。

---

## 命令行用法

```bash
python -m paperplane              # 默认进入交互式 CLI 主菜单（同 ui）
python -m paperplane gui          # 启动 Material 3 Web GUI（默认 http://127.0.0.1:8765）
python -m paperplane init         # 交互式配置向导，安全生成 config.toml
python -m paperplane pick         # 拉取对话列表，方向键勾选添加频道
python -m paperplane login        # 交互式登录 Telegram（只需一次）
python -m paperplane chats        # 列出所有对话及数字 ID
python -m paperplane scan         # 仅扫描并缓存消息到本地 SQLite
python -m paperplane match        # 纯本地预览匹配结果（不发网络请求）
python -m paperplane download     # 扫描 + 匹配 + 并发下载
python -m paperplane watch        # 补齐历史 + 长期挂机监听（断线重连 + 定时补扫）
```

常用参数：
- `-c, --config <path>`：指定配置文件路径（默认 `config.toml`）
- `-v, --verbose`：输出调试日志
- `--host 0.0.0.0 --port 8765`：自定义 GUI 监听地址与端口（局域网远程访问）
- `--catchup 30`：`watch` 模式定时补扫间隔（单位：分钟，默认 30）

---

## 配置说明（`config.toml`）

完整模板见 [`config.example.toml`](config.example.toml)。敏感信息推荐通过环境变量 `TG_API_ID` 与 `TG_API_HASH` 提供。

```toml
[telegram]
# api_id = 123456
# api_hash = "your_api_hash"

[download]
dir = "downloads"
data_dir = "data"
concurrency = 3
path_template = "{include}/{media_type}"   # 可用变量：{include} {media_type} {channel} {date} {month} {year}

[limits]
request_interval = 1.0
request_jitter = 0.5
flood_sleep_threshold = 120
max_flood_wait = 3600
max_retries = 3

[proxy]
type = "socks5"                            # socks5 / http / mtproxy
host = "127.0.0.1"
port = 7890

[[channels]]
chat = -1001234567890                      # 数字 ID 或 "@username"
exclude = ["预告"]
min_size = "10MB"
max_size = 0                               # 0 表示不限
context_before = 1                         # 媒体前 N 条纯文本消息参与匹配
follow_replies = true
since = 2026-01-01                         # 可选起始日期
scan_mode = "search"                       # full（全量遍历）或 search（频道内搜索）
# 同一频道按不同关键词分别指定下载的媒体类型：
rules = [
  { include = ["#4K 视频"], media = ["video"] },
  { include = ["#图集"], media = ["photo", "document"] },
]
```

> **提示**：TOML 中 `api_hash`、IP 地址（如 `"127.0.0.1"`）、路径、关键词列表项均为字符串，需使用引号；推荐使用 `paperplane init` 或 GUI「配置中心」保存，可完全避免手写语法错误。

---

## 24 小时挂机建议（如 Mac mini / 服务器）

```bash
# macOS 防休眠并允许局域网访问 GUI
caffeinate -i ./.venv/bin/python -m paperplane gui --host 0.0.0.0 --port 8765
```

- 在 GUI「挂机控制台」中勾选本次要监控的频道与关键词后点击「启动长期挂机」，关闭浏览器页面后台仍会持续运行。
- 如需外网远程管理，建议通过 Tailscale / WireGuard 等私网访问，切勿直接将未鉴权端口暴露至公网。
- 建议安装可选依赖 `cryptg`（已在 `requirements.txt` 中），本地 AES 加解密速度可提升数倍且不会影响账号安全。

---

## 项目结构

```text
Paperplane/
├── paperplane/
│   ├── __main__.py        # CLI 入口与子命令分发
│   ├── config.py          # TOML 配置解析、校验与安全写回
│   ├── client.py          # Telethon 客户端构建、代理、节流与错误分类
│   ├── store.py           # SQLite 消息缓存、扫描区间与下载状态
│   ├── matcher.py         # 纯本地多规则正则匹配引擎
│   ├── core.py            # 扫描/搜索、Worker 队列下载与断点续传
│   ├── service.py         # 供 CLI 与 GUI 共用的长期挂机服务层
│   ├── cli_ui.py          # Rich + Questionary 交互式 CLI
│   └── gui.py             # Material Design 3 风格 NiceGUI 图形界面
├── tests/                 # 单元测试（matcher / store / watch / concurrency / rules）
├── config.example.toml    # 配置文件模板
├── pyproject.toml         # 项目打包与脚本入口定义
├── requirements.txt       # 依赖列表
├── run-cli.bat            # Windows 交互式 CLI 快捷启动脚本
└── run-gui.bat            # Windows GUI 快捷启动脚本
```

---

## 运行测试

```powershell
# Windows
.\.venv-win\Scripts\python.exe -m pytest -v

# macOS
./.venv/bin/python -m pytest -v
```
