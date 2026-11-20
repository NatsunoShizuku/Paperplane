"""关键词匹配。纯本地逻辑，不发起任何网络请求。

规则：
1. 单条消息的文字 + 文件名参与匹配；
2. 相册（grouped_id 相同）视为一个整体：任一条命中，整组都算命中；
3. follow_replies：媒体回复的那条消息命中也算；
4. context_before：媒体前 N 条纯文字消息命中也算（"先发介绍、再发文件"）；
5. exclude 在上述合并后的文本上判断，命中即排除；
6. 最后按媒体类型和大小过滤。
"""
from __future__ import annotations

import re
from collections import defaultdict

from .config import ChannelConfig
from .store import CachedMessage


class Matcher:
    def __init__(self, cfg: ChannelConfig):
        self.cfg = cfg
        self.exclude = [re.compile(p, re.IGNORECASE) for p in cfg.exclude]
        if cfg.rules:
            self.rules = [
                ([re.compile(p, re.IGNORECASE) for p in r.include], r.media)
                for r in cfg.rules
            ]
        else:
            self.rules = [
                ([re.compile(p, re.IGNORECASE) for p in cfg.include], cfg.media)
            ]

    def _hit(self, patterns, text: str) -> bool:
        return any(p.search(text) for p in patterns)

    def _range_and_size_ok(self, m: CachedMessage) -> bool:
        if not self.cfg.in_range(m.date):
            return False
        size = m.size or 0
        if self.cfg.min_size and size < self.cfg.min_size:
            return False
        if self.cfg.max_size and size > self.cfg.max_size:
            return False
        return True

    def _match_pattern(self, m: CachedMessage, text: str) -> str | None:
        """返回命中的 include 正则原串；若规则 include 为空则返回空串 ''；未命中返回 None。"""
        for patterns, media_set in self.rules:
            if m.media_type not in media_set:
                continue
            if not patterns:
                return ""
            for p in patterns:
                if p.search(text):
                    return p.pattern
        return None

    def select_with_patterns(self, msgs: list[CachedMessage]) -> list[tuple[CachedMessage, str]]:
        """返回需要下载的 (媒体消息, 命中的 include 正则) 列表（按 msg_id 升序）。"""
        msgs = sorted(msgs, key=lambda m: m.msg_id)
        by_id = {m.msg_id: m for m in msgs}

        def own_text(m: CachedMessage) -> str:
            return "\n".join(x for x in (m.text, m.file_name) if x)

        # 相册内合并文字
        album_text: dict[int, str] = defaultdict(str)
        for m in msgs:
            if m.grouped_id:
                album_text[m.grouped_id] += "\n" + own_text(m)

        # 每条消息之前最近的 N 条纯文字消息
        context: dict[int, str] = {}
        if self.cfg.context_before > 0:
            recent: list[str] = []
            for m in msgs:
                context[m.msg_id] = "\n".join(recent)
                if not m.media_type and m.text:
                    recent.append(m.text)
                    recent = recent[-self.cfg.context_before:]

        selected: list[tuple[CachedMessage, str]] = []
        for m in msgs:
            if not m.media_type:
                continue
            parts = [album_text[m.grouped_id] if m.grouped_id else own_text(m)]
            if self.cfg.follow_replies and m.reply_to and m.reply_to in by_id:
                parts.append(own_text(by_id[m.reply_to]))
            if m.msg_id in context:
                parts.append(context[m.msg_id])
            text = "\n".join(parts)

            if self.exclude and self._hit(self.exclude, text):
                continue
            if not self._range_and_size_ok(m):
                continue
            matched = self._match_pattern(m, text)
            if matched is None:
                continue
            selected.append((m, matched))
        return selected

    def select(self, msgs: list[CachedMessage]) -> list[CachedMessage]:
        """返回需要下载的媒体消息（按 msg_id 升序）。"""
        return [m for m, _ in self.select_with_patterns(msgs)]
