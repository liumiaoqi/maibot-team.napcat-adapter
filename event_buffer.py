"""事件缓冲区 — 插件连接恢复前缓存事件。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.plugin_runtime_v2.sdk.context import PluginContext


class EventBuffer:
    """插件连接恢复前的事件缓冲区。

    flush 失败时保留未重放的部分不丢弃。
    """

    def __init__(self, max_size: int = 1000, hard_limit: int = 5000) -> None:
        self._events: list[tuple[str, dict]] = []
        self._max_size = max_size
        self._hard_limit = hard_limit
        self._dropped_count: int = 0

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
