# Paperplane

[English](README.md) | **简体中文**

基于 Python 与 [Telethon](https://github.com/LonamiWebs/Telethon) 构建的跨平台（Windows / macOS）**Telegram 频道关键词媒体下载器**，内置 **GUI 界面**与**交互式 CLI**，专为长期挂机稳定下载设计。

---

## 步骤教程

### 方式一：使用 GUI `.bat` 文件启动（Windows 推荐，零命令行操作）

1. **安装 Python 3.11+**（推荐 Python 3.12），安装时勾选 *Add Python to PATH*。
2. **获取 Telegram API 凭证**：前往 [my.telegram.org](https://my.telegram.org) → *API development tools*，申请并记下 `api_id` 和 `api_hash`。
3. **直接双击根目录下的 [`run-gui.bat`](run-gui.bat)**：
   - 首次运行会自动创建 `.venv-win` 虚拟环境并安装 `requirements.txt` 中的依赖，完成后自动在浏览器打开 `http://127.0.0.1:8765`。
4. **在界面中完成配置与登录**：
   - 打开左侧 **「配置中心」**，填写 `api_id`、`api_hash` 和代理（如需），点击 **「保存全部配置」**（也可直接编辑项目根目录自带的 [`config.toml`](config.toml)）。
   - 打开左侧 **「账号与登录」**，输入手机号获取验证码并完成登录（仅需一次）。
   - 打开左侧 **「频道与预览」**，点击 **「拉取对话与头像」**，一键将目标频道加入配置，再到 **「配置中心」** 填写关键词与媒体类型规则并保存。
5. **开始挂机下载**：
   - 回到 **「挂机控制台」**，勾选本次需要下载的频道与关键词，点击 **「启动长期挂机」**（或 **「单次下载」**）。关闭浏览器标签页不影响后台继续运行。

---

### 方式二：使用 CLI 命令行启动（Windows / macOS）

1. **准备环境并安装依赖**（需要 Python 3.11+）：
   - **Windows (PowerShell)**：
     ```powershell
     py -3.12 -m venv .venv-win
     .\.venv-win\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
     ```
     *(也可以直接双击 [`run-cli.bat`](run-cli.bat) 自动完成虚拟环境创建、依赖安装并进入交互式菜单。)*
   - **macOS**：
     ```bash
     python3 -m venv .venv
     ./.venv/bin/pip install -r requirements.txt
     ```
2. **修改配置文件**：直接编辑项目根目录的 [`config.toml`](config.toml)（内含详尽中文注释），填写 `api_id`、`api_hash`、代理（按需）和频道配置；或运行交互式向导自动生成：
   ```powershell
   # Windows
   .\.venv-win\Scripts\python.exe -m paperplane init
   # macOS
   ./.venv/bin/python -m paperplane init
   ```
3. **首次登录 Telegram**：
   ```powershell
   # Windows
   .\.venv-win\Scripts\python.exe -m paperplane login
   # macOS
   ./.venv/bin/python -m paperplane login
   ```
4. **选择频道并启动下载 / 挂机**：
   - 推荐进入交互式菜单（方向键选择，支持空格勾选频道、离线预览、进度条与长期挂机）：
     ```powershell
     # Windows
     .\.venv-win\Scripts\python.exe -m paperplane ui
     # macOS
     ./.venv/bin/python -m paperplane ui
     ```
   - 或直接按步骤运行经典子命令：
     ```bash
     python -m paperplane chats      # 查看频道数字 ID 并填入 config.toml
     python -m paperplane scan       # 扫描并缓存消息到本地 SQLite
     python -m paperplane match      # 纯本地预览命中结果（不发网络请求）
     python -m paperplane download   # 单次并发下载
     python -m paperplane watch      # 补齐历史 + 长期挂机监听
     ```

> **双系统共用提示**：macOS 与 Windows 共用同一项目目录时，请分别保留 `.venv` 和 `.venv-win`；`data/` 下的 session 与 SQLite 可跨平台轮流使用，但**切勿两台机器同时运行同一份 session**。

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
- **现代化 Material 3 GUI**：深/浅色模式自动记忆、频道头像缓存、控制台勾选本次下载频道与关键词、实时下载队列与日志终端。

---

## 常用命令行参数

- `-c, --config <path>`：指定配置文件路径（默认 `config.toml`）
- `-v, --verbose`：输出调试日志
- `--host 0.0.0.0 --port 8765`：自定义 GUI 监听地址与端口（局域网远程访问）
- `--catchup 30`：`watch` 模式定时补扫间隔（单位：分钟，默认 30）

---

## 配置说明（[`config.toml`](config.toml)）

项目根目录已内置带详细注释的 [`config.toml`](config.toml)，直接修改即可。敏感信息推荐通过环境变量 `TG_API_ID` 与 `TG_API_HASH` 提供；若直接写入 `config.toml` 且需要推送代码，请执行 `git update-index --skip-worktree config.toml` 防止凭证泄露。

---

## 24 小时挂机建议（如 Mac mini / 服务器）

```bash
# macOS 防休眠并允许局域网访问 GUI
caffeinate -i ./.venv/bin/python -m paperplane gui --host 0.0.0.0 --port 8765
```

- 在 GUI「挂机控制台」中勾选本次要监控的频道与关键词后点击「启动长期挂机」，关闭浏览器页面后台仍会持续运行。
- 如需外网远程管理，建议通过 Tailscale / WireGuard 等私网访问，切勿直接将未鉴权端口暴露至公网。
- 安装可选依赖 `cryptg`（已在 `requirements.txt` 中）可大幅加速本地 AES 加解密，且不会影响账号安全。

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
├── config.toml            # 内置详细注释的配置文件（直接修改使用）
├── pyproject.toml         # 项目打包与脚本入口定义
├── requirements.txt       # 依赖列表
├── run-cli.bat            # Windows 交互式 CLI 一键启动脚本（自动建环境）
└── run-gui.bat            # Windows GUI 一键启动脚本（自动建环境）
```

---

## 运行测试

```powershell
# Windows
.\.venv-win\Scripts\python.exe -m pytest -v

# macOS
./.venv/bin/python -m pytest -v
```
