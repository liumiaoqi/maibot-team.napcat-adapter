"""消息去重器 — 基于消息 ID 的滑动窗口去重。"""

from __future__ import annotations

import time
from collections import OrderedDict


class MessageDeduplicator:
    """基于消息 ID 的滑动窗口去重器。"""

    def __init__(self, max_size: int = 1000, ttl_sec: float = 300.0) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._max_size = max_size
        self._ttl_sec = ttl_sec

    def is_duplicate(self, message_id: str) -> bool:
        """检查消息是否已在去重窗口中。"""
        self._cleanup()
        return message_id in self._seen

    def record(self, message_id: str) -> None:
        """记录一条消息到去重窗口。"""
        self._cleanup()
        if message_id in self._seen:
            self._seen.move_to_end(message_id)
        else:
            self._seen[message_id] = time.time()
        while len(self._seen) > self._max_size:
            self._seen.popitem(last=False)

    def _cleanup(self) -> None:
        """清除过期的消息 ID。"""
        now = time.time()
        expired = [k for k, v in self._seen.items() if now - v > self._ttl_sec]
        for k in expired:
            del self._seen[k]
