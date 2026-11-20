"""GUI 主题样式、状态徽章与格式化辅助函数。"""
from __future__ import annotations

import time

STATUS_META = {
    "idle": ("空闲待命", "grey-6", "pause_circle"),
    "starting": ("正在启动", "blue-6", "hourglass_top"),
    "running": ("挂机运行中", "positive", "bolt"),
    "reconnecting": ("断线重连中", "warning", "sync_problem"),
    "stopping": ("正在停止", "orange-7", "stop_circle"),
    "error": ("异常停止", "negative", "error"),
}

AVATAR_PALETTE = [
    "#4F46E5", "#0EA5E9", "#10B981", "#F59E0B",
    "#EC4899", "#8B5CF6", "#14B8A6", "#F97316",
]

M3_HEAD_CSS = """
<style>
  :root {
    --m3-bg: #F4F6FA;
    --m3-surface: #FFFFFF;
    --m3-surface-2: #F8FAFC;
    --m3-border: rgba(148, 163, 184, 0.28);
    --m3-text: #0F172A;
    --m3-muted: #64748B;
    --m3-hover: rgba(15, 23, 42, 0.04);
    --m3-nav-active-bg: rgba(79, 70, 229, 0.12);
    --m3-nav-active-fg: #4F46E5;
    --m3-warn-bg: rgba(245, 158, 11, 0.12);
    --m3-warn-fg: #B45309;
    --m3-kind-bg: rgba(79, 70, 229, 0.10);
    --m3-kind-fg: #4338CA;
  }
  body.body--dark {
    --m3-bg: #0B1120;
    --m3-surface: #1E293B;
    --m3-surface-2: #162032;
    --m3-border: rgba(148, 163, 184, 0.16);
    --m3-text: #F1F5F9;
    --m3-muted: #94A3B8;
    --m3-hover: rgba(241, 245, 249, 0.06);
    --m3-nav-active-bg: rgba(129, 140, 248, 0.22);
    --m3-nav-active-fg: #A5B4FC;
    --m3-warn-bg: rgba(245, 158, 11, 0.16);
    --m3-warn-fg: #FCD34D;
    --m3-kind-bg: rgba(129, 140, 248, 0.18);
    --m3-kind-fg: #C7D2FE;
  }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Microsoft YaHei', sans-serif;
    background: var(--m3-bg) !important;
    color: var(--m3-text) !important;
  }
  .m3-header {
    background: var(--m3-surface) !important;
    color: var(--m3-text) !important;
    border-bottom: 1px solid var(--m3-border) !important;
  }
  .m3-drawer {
    background: var(--m3-surface) !important;
    color: var(--m3-text) !important;
    border-right: 1px solid var(--m3-border) !important;
  }
  .m3-card {
    background: var(--m3-surface) !important;
    color: var(--m3-text) !important;
    border-radius: 16px !important;
    border: 1px solid var(--m3-border) !important;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05), 0 4px 12px rgba(15, 23, 42, 0.04) !important;
  }
  .m3-subcard {
    background: var(--m3-surface-2) !important;
    color: var(--m3-text) !important;
    border-radius: 12px !important;
    border: 1px solid var(--m3-border) !important;
  }
  .m3-text {
    color: var(--m3-text) !important;
  }
  .m3-muted {
    color: var(--m3-muted) !important;
  }
  .m3-nav-btn {
    color: var(--m3-text) !important;
    border-radius: 9999px !important;
    justify-content: flex-start !important;
    padding: 10px 16px !important;
    font-weight: 500 !important;
    text-transform: none !important;
  }
  .m3-nav-btn:hover {
    background: var(--m3-hover) !important;
  }
  .m3-nav-active {
    background: var(--m3-nav-active-bg) !important;
    color: var(--m3-nav-active-fg) !important;
    font-weight: 600 !important;
  }
  .m3-row-item {
    border-radius: 12px;
    transition: background 0.15s ease;
  }
  .m3-row-item:hover {
    background: var(--m3-hover);
  }
  .m3-expansion {
    background: var(--m3-surface-2) !important;
    color: var(--m3-text) !important;
    border: 1px solid var(--m3-border) !important;
    border-radius: 14px !important;
    overflow: hidden;
  }
  .m3-warn-box {
    background: var(--m3-warn-bg) !important;
    color: var(--m3-warn-fg) !important;
    border-radius: 12px;
  }
  .m3-kind-badge {
    background: var(--m3-kind-bg) !important;
    color: var(--m3-kind-fg) !important;
    border-radius: 9999px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
  }
  .m3-log-box {
    background: #090E1A;
    color: #CBD5E1;
    border: 1px solid var(--m3-border);
    border-radius: 12px;
    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
    font-size: 12px;
    line-height: 1.55;
    padding: 14px;
    height: 360px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .m3-pill {
    border-radius: 9999px !important;
    text-transform: none !important;
    font-weight: 500 !important;
  }
  .m3-avatar-img {
    width: 38px;
    height: 38px;
    border-radius: 9999px;
    object-fit: cover;
    flex-shrink: 0;
    border: 1px solid var(--m3-border);
  }
  .m3-avatar-fallback {
    width: 38px;
    height: 38px;
    border-radius: 9999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: #FFFFFF;
    font-weight: 700;
    font-size: 15px;
    flex-shrink: 0;
    user-select: none;
  }
  body.body--dark .q-table,
  body.body--dark .q-table__card {
    background: transparent !important;
    color: var(--m3-text) !important;
  }
</style>
"""


def fmt_uptime(started_at: float | None) -> str:
    if not started_at:
        return "00:00:00"
    sec = max(0, int(time.time() - started_at))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def avatar_color(seed: int | str) -> str:
    return AVATAR_PALETTE[abs(hash(str(seed))) % len(AVATAR_PALETTE)]


def initial_char(name: str) -> str:
    s = (name or "?").strip().lstrip("@")
    return s[0].upper() if s else "?"
