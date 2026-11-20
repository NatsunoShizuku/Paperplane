# Paperplane

**English** | [简体中文](README-zh.md)

A cross-platform (Windows / macOS) **Telegram channel keyword media downloader** built with Python and [Telethon](https://github.com/LonamiWebs/Telethon), featuring a **Material Design 3 Web GUI**, an **interactive CLI**, and resilient 24/7 unattended watching.

---

## Key Features

- **Cache-First Offline Matching**: Message metadata is persisted in SQLite so you can iterate on regex filters with `match` completely offline without consuming Telegram API quota.
- **Dual Scan Modes (Per-Channel)**:
  - `full` (default): Incremental history traversal with automatic backward backfill when `since` is moved earlier.
  - `search`: Uses Telegram's server-side in-channel search for literal keywords and automatically fetches neighboring album siblings and `context_before` media—significantly faster on large channels.
- **Flexible Matching Rules**:
  - Configure multiple `include → media` rules per channel (e.g., download `video` for `#4K` and `photo`/`document` for `#Gallery`).
  - Supports album (`grouped_id`) caption sharing, preceding text context (`context_before`), reply tracking (`follow_replies`), `exclude` regexes, file size bounds, and date ranges.
- **Customizable Download Path Templates**:
  - Defaults to `{include}/{media_type}` (strips `#`, replaces spaces with `_`), and supports combining `{include}`, `{media_type}`, `{channel}`, `{date}`, `{month}`, and `{year}` with live preview in the GUI.
- **Built for 24/7 Unattended Operation**:
  - Global cross-channel concurrency limit (shared `Semaphore`) + producer-consumer worker queue to avoid slow-file head-of-line blocking.
  - Request throttling with jitter, automatic `FloodWait` sleeping & max-wait circuit breaker, periodic `file_reference` refresh, and 512KB chunk-aligned resumable downloads.
  - `watch` mode features exponential-backoff auto-reconnect and periodic catch-up scans.
- **Three Interfaces**:
  - **Material 3 Web GUI** (NiceGUI): Persistent dark/light mode, cached channel avatars, dashboard-level channel & keyword scope checkboxes, live download queue & log terminal (closing the browser tab keeps background downloads running).
  - **Interactive CLI** (Rich + Questionary): Arrow-key menu, interactive config wizard, channel picker, and live progress bars.
  - **Classic CLI**: Script-friendly subcommands for headless servers.

---

## Requirements

- Python **3.11+** (Python 3.12 recommended)
- Telegram API credentials: `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org) → *API development tools*

---

## Quick Start

### Windows

```powershell
# 1. Create a Windows virtual environment and install dependencies
py -3.12 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install -r requirements.txt

# 2. Copy the example config and edit it (or use the GUI / init wizard)
Copy-Item config.example.toml config.toml

# 3. Launch the GUI or interactive CLI (or double-click run-gui.bat / run-cli.bat)
.\.venv-win\Scripts\python.exe -m paperplane gui
# or
.\.venv-win\Scripts\python.exe -m paperplane ui
```

### macOS

```bash
# 1. Create a virtual environment and install dependencies
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 2. Copy the example config and edit it
cp config.example.toml config.toml

# 3. Launch the GUI or interactive CLI
./.venv/bin/python -m paperplane gui
# or
./.venv/bin/python -m paperplane ui
```

> If you share the same project directory across macOS and Windows, keep separate virtual environments (`.venv` for macOS, `.venv-win` for Windows). The `data/` SQLite files (`paperplane.session`, `state.sqlite3`) are cross-platform, but **never run the same session file on two machines simultaneously**.

---

## CLI Commands

```bash
python -m paperplane              # Launch the interactive CLI menu (same as `ui`)
python -m paperplane gui          # Launch the Material 3 Web GUI (default http://127.0.0.1:8765)
python -m paperplane init         # Interactive setup wizard for config.toml
python -m paperplane pick         # Fetch dialogs and interactively pick channels
python -m paperplane login        # Interactive Telegram login (one-time)
python -m paperplane chats        # List dialogs and numeric chat IDs
python -m paperplane scan         # Scan and cache messages into SQLite
python -m paperplane match        # Offline preview of matched files (zero API requests)
python -m paperplane download     # Scan + match + download concurrently
python -m paperplane watch        # Catch up history + long-running watch (auto-reconnect + catch-up)
```

Common options:
- `-c, --config <path>`: Path to config file (default `config.toml`)
- `-v, --verbose`: Enable debug logging
- `--host 0.0.0.0 --port 8765`: Bind GUI to a custom host/port (e.g., LAN access)
- `--catchup 30`: Periodic catch-up scan interval in minutes for `watch` mode (default `30`)

---

## Configuration (`config.toml`)

See [`config.example.toml`](config.example.toml) for a full template. Setting `TG_API_ID` and `TG_API_HASH` as environment variables is recommended over hardcoding them.

```toml
[telegram]
# api_id = 123456
# api_hash = "your_api_hash"

[download]
dir = "downloads"
data_dir = "data"
concurrency = 3
path_template = "{include}/{media_type}"   # Available: {include} {media_type} {channel} {date} {month} {year}

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
chat = -1001234567890                      # Numeric ID or "@username"
exclude = ["trailer"]
min_size = "10MB"
max_size = 0                               # 0 = unlimited
context_before = 1                         # Include N preceding text-only messages in matching
follow_replies = true
since = 2026-01-01                         # Optional start date
scan_mode = "search"                       # "full" (traversal) or "search" (server-side search)
# Per-channel rules mapping different include regexes to different media types:
rules = [
  { include = ["#4K Video"], media = ["video"] },
  { include = ["#Gallery"], media = ["photo", "document"] },
]
```

> **Note**: In TOML, `api_hash`, IP addresses (e.g. `"127.0.0.1"`), paths, and keyword entries must be quoted strings. Using `paperplane init` or the GUI Config Center avoids TOML syntax errors automatically.

---

## 24/7 Headless / Server Setup (e.g. Mac mini)

```bash
# Prevent macOS sleep and expose the GUI on your LAN
caffeinate -i ./.venv/bin/python -m paperplane gui --host 0.0.0.0 --port 8765
```

- Select your target channels and keywords in the GUI Dashboard and start **Watch Mode**; closing the browser tab will not interrupt background downloads.
- For remote access outside your LAN, use a private VPN such as Tailscale or WireGuard rather than exposing the port directly to the public internet.
- Installing the optional `cryptg` package (included in `requirements.txt`) accelerates local AES encryption/decryption for large files without affecting account safety.

---

## Project Structure

```text
Paperplane/
├── paperplane/
│   ├── __main__.py        # CLI entry point & subcommands
│   ├── config.py          # TOML config loading, validation & safe serialization
│   ├── client.py          # Telethon client builder, proxy, throttling & error classification
│   ├── store.py           # SQLite cache for messages, scan state & downloads
│   ├── matcher.py         # Offline multi-rule regex matching engine
│   ├── core.py            # Scanning/searching, worker-queue downloads & resume
│   ├── service.py         # Shared long-running service layer for CLI & GUI
│   ├── cli_ui.py          # Rich + Questionary interactive CLI
│   └── gui.py             # Material Design 3 NiceGUI web interface
├── tests/                 # Unit tests (matcher / store / watch / concurrency / rules)
├── config.example.toml    # Example configuration
├── pyproject.toml         # Package metadata & console scripts
├── requirements.txt       # Dependencies
├── run-cli.bat            # Windows launcher for interactive CLI
└── run-gui.bat            # Windows launcher for GUI
```

---

## Running Tests

```powershell
# Windows
.\.venv-win\Scripts\python.exe -m pytest -v

# macOS
./.venv/bin/python -m pytest -v
```
