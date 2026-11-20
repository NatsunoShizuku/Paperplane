"""SQLite 状态：消息缓存、扫描进度、下载记录。

消息文本缓存在本地，换关键词重新匹配时不需要再请求 Telegram。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    chat_id     INTEGER NOT NULL,
    msg_id      INTEGER NOT NULL,
    grouped_id  INTEGER,
    date        INTEGER NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    file_name   TEXT,
    media_type  TEXT,
    size        INTEGER,
    reply_to    INTEGER,
    PRIMARY KEY (chat_id, msg_id)
);
CREATE TABLE IF NOT EXISTS scan_state (
    chat_id  INTEGER PRIMARY KEY,
    last_id  INTEGER NOT NULL DEFAULT 0,
    floor_ts INTEGER                    -- 已缓存的最早时间点；0 = 从频道开头，NULL = 未扫描过
);
CREATE TABLE IF NOT EXISTS chats (
    chat_key  TEXT PRIMARY KEY,         -- 配置里写的 chat（字符串化）
    chat_id   INTEGER NOT NULL,
    title     TEXT
);
CREATE TABLE IF NOT EXISTS downloads (
    chat_id     INTEGER NOT NULL,
    msg_id      INTEGER NOT NULL,
    status      TEXT NOT NULL,          -- done / failed / skipped
    path        TEXT,
    error       TEXT,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (chat_id, msg_id)
);
"""


@dataclass
class CachedMessage:
    chat_id: int
    msg_id: int
    grouped_id: int | None
    date: int
    text: str
    file_name: str | None
    media_type: str | None
    size: int | None
    reply_to: int | None


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(scan_state)")}
        if "floor_ts" not in cols:     # 旧版数据库升级；旧数据都是从开头扫描的
            self.db.execute("ALTER TABLE scan_state ADD COLUMN floor_ts INTEGER DEFAULT 0")
        self.db.commit()

    def close(self):
        self.db.close()

    # ---- 配置 chat → chat_id 映射（让 match 命令可以离线运行）----
    def remember_chat(self, key, chat_id: int, title: str):
        self.db.execute("INSERT OR REPLACE INTO chats VALUES (?,?,?)", (str(key), chat_id, title))
        self.db.commit()

    def lookup_chat(self, key) -> tuple[int, str] | None:
        return self.db.execute("SELECT chat_id, title FROM chats WHERE chat_key=?", (str(key),)).fetchone()

    # ---- 扫描进度 ----
    def last_id(self, chat_id: int) -> int:
        row = self.db.execute("SELECT last_id FROM scan_state WHERE chat_id=?", (chat_id,)).fetchone()
        return row[0] if row else 0

    def set_last_id(self, chat_id: int, last_id: int):
        self.db.execute(
            "INSERT INTO scan_state(chat_id,last_id) VALUES(?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET last_id=MAX(last_id, excluded.last_id)",
            (chat_id, last_id),
        )
        self.db.commit()

    def floor_ts(self, chat_id: int) -> int | None:
        row = self.db.execute("SELECT floor_ts FROM scan_state WHERE chat_id=?", (chat_id,)).fetchone()
        return row[0] if row else None

    def set_floor_ts(self, chat_id: int, ts: int):
        self.db.execute(
            "INSERT INTO scan_state(chat_id,last_id,floor_ts) VALUES(?,0,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET floor_ts=excluded.floor_ts",
            (chat_id, ts),
        )
        self.db.commit()

    # ---- 消息缓存 ----
    def save_messages(self, msgs: list[CachedMessage]):
        self.db.executemany(
            "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
            [(m.chat_id, m.msg_id, m.grouped_id, m.date, m.text, m.file_name,
              m.media_type, m.size, m.reply_to) for m in msgs],
        )
        self.db.commit()

    def existing_ids(self, chat_id: int, msg_ids: set[int]) -> set[int]:
        if not msg_ids:
            return set()
        found: set[int] = set()
        ids_list = sorted(msg_ids)
        for i in range(0, len(ids_list), 500):
            chunk = ids_list[i : i + 500]
            ph = ",".join("?" * len(chunk))
            rows = self.db.execute(
                f"SELECT msg_id FROM messages WHERE chat_id=? AND msg_id IN ({ph})",
                (chat_id, *chunk),
            ).fetchall()
            found.update(r[0] for r in rows)
        return found

    def messages(
        self,
        chat_id: int,
        *,
        min_id: int = 0,
        context_before: int = 0,
        follow_replies: bool = True,
    ) -> list[CachedMessage]:
        if min_id <= 0:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE chat_id=? ORDER BY msg_id", (chat_id,)
            ).fetchall()
            return [CachedMessage(*r) for r in rows]

        rows = self.db.execute(
            "SELECT * FROM messages WHERE chat_id=? AND msg_id>? ORDER BY msg_id",
            (chat_id, min_id),
        ).fetchall()
        if not rows:
            return []

        by_id = {r[1]: CachedMessage(*r) for r in rows}

        if context_before > 0:
            ctx_rows = self.db.execute(
                "SELECT * FROM messages WHERE chat_id=? AND msg_id<=? "
                "AND media_type IS NULL AND text!='' ORDER BY msg_id DESC LIMIT ?",
                (chat_id, min_id, context_before),
            ).fetchall()
            for r in ctx_rows:
                by_id.setdefault(r[1], CachedMessage(*r))

        if follow_replies:
            reply_ids = sorted({m.reply_to for m in by_id.values() if m.reply_to and m.reply_to <= min_id})
            if reply_ids:
                ph = ",".join("?" * len(reply_ids))
                for r in self.db.execute(
                    f"SELECT * FROM messages WHERE chat_id=? AND msg_id IN ({ph})",
                    (chat_id, *reply_ids),
                ).fetchall():
                    by_id.setdefault(r[1], CachedMessage(*r))

        group_ids = sorted({m.grouped_id for m in by_id.values() if m.grouped_id})
        if group_ids:
            ph = ",".join("?" * len(group_ids))
            for r in self.db.execute(
                f"SELECT * FROM messages WHERE chat_id=? AND msg_id<=? AND grouped_id IN ({ph})",
                (chat_id, min_id, *group_ids),
            ).fetchall():
                by_id.setdefault(r[1], CachedMessage(*r))

        return [by_id[k] for k in sorted(by_id)]

    # ---- 下载记录 ----
    def finished_ids(self, chat_id: int) -> set[int]:
        rows = self.db.execute(
            "SELECT msg_id FROM downloads WHERE chat_id=? AND status IN ('done','skipped')", (chat_id,)
        ).fetchall()
        return {r[0] for r in rows}

    def mark(self, chat_id: int, msg_id: int, status: str, path: str | None = None, error: str | None = None):
        self.db.execute(
            "INSERT OR REPLACE INTO downloads VALUES (?,?,?,?,?,?)",
            (chat_id, msg_id, status, path, error, int(time.time())),
        )
        self.db.commit()

    def failed(self, chat_id: int) -> list[tuple[int, str]]:
        return self.db.execute(
            "SELECT msg_id, error FROM downloads WHERE chat_id=? AND status='failed' ORDER BY msg_id",
            (chat_id,),
        ).fetchall()
