# Paperplane

**English** | [简体中文](README-zh.md)

A cross-platform (Windows / macOS) **Telegram channel keyword media downloader** built with Python and [Telethon](https://github.com/LonamiWebs/Telethon), featuring a **Web GUI**, an **interactive CLI**, and resilient 24/7 unattended watching.

---

## Step-by-Step Guide

### Option 0: Download Zero-Config Offline Release Bundle (Easiest, No Python Required)

1. Download the archive for your platform from **GitHub Releases**:
   - **Windows (x64)**: `Paperplane-*-windows-x64.zip`
   - **macOS (Apple Silicon M1–M4)**: `Paperplane-*-macos-arm64.tar.gz`
   - **macOS (Intel)**: `Paperplane-*-macos-x64.tar.gz`
2. Extract and **double-click to launch** (bundles a standalone Python 3.12 runtime and all dependencies pre-installed):
   - **Windows**: double-click `run-gui.bat` (or `run-cli.bat`)
   - **macOS**: double-click `run-gui.command` (or `run-cli.command`). If macOS Gatekeeper blocks the first launch, run `xattr -dr com.apple.quarantine .` inside the extracted folder once.
3. Configure your API credentials, sign in, pick channels, and start watching right in the GUI.

> To build an offline bundle locally for your current platform, run `python3 scripts/build_release.py --run-tests` (outputs to `dist/` without modifying `main`).

---

### Option A: One-Click GUI Launcher from Source (Zero CLI Needed)

1. **Install Python 3.11+** (Python 3.12 recommended); on Windows, check *Add Python to PATH*.
2. **Obtain Telegram API credentials**: visit [my.telegram.org](https://my.telegram.org) → *API development tools* and note your `api_id` and `api_hash`.
3. **Double-click the launcher for your OS**:
   - **Windows**: double-click [`run-gui.bat`](run-gui.bat)
   - **macOS**: double-click [`run-gui.command`](run-gui.command) (if Gatekeeper warns on first run, right-click → Open)
   - On first run, it automatically creates the virtual environment (`.venv-win` / `.venv`), installs dependencies from `requirements.txt`, and opens the Material 3 GUI in your browser at `http://127.0.0.1:8765`.
4. **Configure & log in inside the GUI**:
   - Open **Config Center** in the left drawer, fill in `api_id`, `api_hash`, and proxy (if needed), then click **Save** (or edit [`config.toml`](config.toml) directly).
   - Open **Account & Login**, enter your phone number to receive a verification code, and sign in (one-time).
   - Open **Channels & Preview**, click **Fetch Dialogs & Avatars** to add channels in one click, then configure keywords and media types in **Config Center**.
5. **Start watching / downloading**:
   - Go to **Dashboard**, check the channels and keywords you want for this run, and click **Start Long-Running Watch** (or **Download Once**). Closing the browser tab keeps background downloads running.

---

### Option B: Launch via CLI (Windows & macOS)

1. **Set up the virtual environment and install dependencies** (Python 3.11+ required):
   - **Windows (PowerShell)**:
     ```powershell
     py -3.12 -m venv .venv-win
     .\.venv-win\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
     ```
     *(You can also double-click [`run-cli.bat`](run-cli.bat) to auto-bootstrap `.venv-win` and open the interactive CLI menu.)*
   - **macOS**:
     ```bash
     python3 -m venv .venv
     ./.venv/bin/pip install -r requirements.txt
     ```
     *(You can also double-click [`run-cli.command`](run-cli.command) to auto-bootstrap `.venv` and open the interactive CLI menu.)*
2. **Edit the configuration**: edit [`config.toml`](config.toml) directly (it includes detailed inline comments) to set `api_id`, `api_hash`, proxy (optional), and channels—or run the interactive setup wizard:
   ```powershell
   # Windows
   .\.venv-win\Scripts\python.exe -m paperplane init
   # macOS
   ./.venv/bin/python -m paperplane init
   ```
3. **Log in to Telegram (one-time)**:
   ```powershell
   # Windows
   .\.venv-win\Scripts\python.exe -m paperplane login
   # macOS
   ./.venv/bin/python -m paperplane login
   ```
4. **Pick channels and start downloading / watching**:
   - Launch the interactive CLI menu (arrow-key navigation, channel picker, offline preview, live progress bars, and watch mode):
     ```powershell
     # Windows
     .\.venv-win\Scripts\python.exe -m paperplane ui
     # macOS
     ./.venv/bin/python -m paperplane ui
     ```
   - Or run classic subcommands directly:
     ```bash
     python -m paperplane chats      # List dialogs and numeric IDs for config.toml
     python -m paperplane scan       # Scan and cache messages into SQLite
     python -m paperplane match      # Offline preview of matched files (zero API calls)
     python -m paperplane download   # Concurrent one-shot download
     python -m paperplane watch      # Catch up history + 24/7 watch mode
     ```

> **Cross-platform note**: Keep separate virtual environments when sharing the project folder between macOS (`.venv`) and Windows (`.venv-win`). The SQLite files in `data/` are cross-platform, but **never run the same session file on two machines simultaneously**.

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
- **Modern Material 3 GUI**: Persistent dark/light mode, cached channel avatars, dashboard channel & keyword scope checkboxes, live download queue, and log terminal.

---

## Common CLI Options

- `-c, --config <path>`: Path to config file (default `config.toml`)
- `-v, --verbose`: Enable debug logging
- `--host 0.0.0.0 --port 8765`: Bind GUI to a custom host/port (e.g., LAN access)
- `--catchup 30`: Periodic catch-up scan interval in minutes for `watch` mode (default `30`)

---

## Configuration ([`config.example.toml`](config.example.toml))

The repository ships a fully commented template at [`config.example.toml`](config.example.toml). On first launch, Paperplane automatically copies it to `config.toml` (both `config.toml` and `config.local.toml` are in `.gitignore`, preventing accidental credential leaks).
- Supports `[channel_defaults]` inheritance and per-channel `enabled = true/false` toggles.
- Supports a sibling `config.local.toml` overlay as well as `TG_API_ID` / `TG_API_HASH` environment variables.
- Saving from the GUI or CLI wizard uses `tomlkit` in-place updates so all comments and formatting are preserved.

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
│   └── gui/               # Material Design 3 NiceGUI web interface (modular package)
├── scripts/
│   └── build_release.py   # Zero-config offline release bundle builder (standalone Python 3.12)
├── tests/                 # Unit tests (matcher / store / watch / concurrency / rules / config)
├── config.example.toml    # Detailed commented config template (auto-copied to config.toml)
├── pyproject.toml         # Package metadata & console scripts
├── requirements.txt       # Dependencies
├── run-cli.bat            # Windows launcher for interactive CLI (auto-bootstraps venv)
├── run-gui.bat            # Windows launcher for GUI (auto-bootstraps venv)
├── run-cli.command        # macOS launcher for interactive CLI (auto-bootstraps venv)
└── run-gui.command        # macOS launcher for GUI (auto-bootstraps venv)
```

---

## Running Tests

```powershell
# Windows
.\.venv-win\Scripts\python.exe -m pytest -v

# macOS
./.venv/bin/python -m pytest -v
```
