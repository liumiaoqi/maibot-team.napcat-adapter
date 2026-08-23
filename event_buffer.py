"""事件缓冲区 — 插件连接恢复前缓存事件。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.plugin_runtime_v2.sdk.context import PluginContext


class EventBuffer:
    """插件连接恢复前的事件缓冲区。

    flush 失败时保留未重放的部分不丢弃。
    """

    def __init__(self, max_size: int = 1000, hard_limit: int = 5000, dedup_limit: int = 10000) -> None:
        self._events: list[tuple[str, dict]] = []
        self._max_size = max_size
        self._hard_limit = hard_limit
        self._dropped_count: int = 0
        self._seen_ids: set[str] = set()
        self._dedup_limit = dedup_limit

    def is_duplicate(self, msg_id: str) -> bool:
        if not msg_id:
            return False
        if msg_id in self._seen_ids:
            return True
        # _seen_ids 加上限清理，防止内存无限增长（A25b P1-1 内存泄漏）
        if len(self._seen_ids) >= self._dedup_limit:
            self._seen_ids.clear()
        self._seen_ids.add(msg_id)
        return False

    def enqueue(self, event_name: str, payload: dict) -> bool:
        return self.push(event_name, payload)

    def push(self, event_name: str, payload: dict) -> bool:
        """推入一个事件。超过硬限制时移除最早的。"""
        if len(self._events) >= self._hard_limit:
            self._events.pop(0)
            self._dropped_count += 1
        elif len(self._events) >= self._max_size:
            self._events.pop(0)
            self._dropped_count += 1
        self._events.append((event_name, payload))
        return True

    async def flush(self, ctx: PluginContext) -> int:
        """重放缓冲区中的所有事件。失败的事件保留。"""
        remaining: list[tuple[str, dict]] = []
        count = 0
        for event_name, payload in self._events:
            try:
                await ctx.emit_event(event_name, payload)
                count += 1
            except Exception:
                remaining.append((event_name, payload))
        self._events = remaining
        return count

    def clear(self) -> None:
        """清空缓冲区。"""
        self._events.clear()

    def size(self) -> int:
        """当前缓冲区大小。"""
        return len(self._events)
