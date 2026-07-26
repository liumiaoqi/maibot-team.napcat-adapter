"""入站消息编解码 — 将 OneBot 11 payload 转为 v4 Event payload。

v1 输出 MessageDict → v4 输出 Event payload dict（通过 ctx.emit_event 推送）。
消息段解析逻辑复用 cards.py / text.py（CX 已迁移）。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, TYPE_CHECKING

from ..cards import NapCatInboundCardMixin
from ..text import NapCatInboundTextMixin

if TYPE_CHECKING:
    from ...session_mapper import SessionIdMapper
    from ...services import NapCatQueryService


class NapCatInboundCodec(NapCatInboundCardMixin, NapCatInboundTextMixin):
    """入站消息编解码器 — v1 兼容层重写为 v4 Event 输出。"""

    def __init__(
        self,
        self_id: str,
        query_service: NapCatQueryService,
        sid_mapper: SessionIdMapper,
    ) -> None:
        self._self_id = self_id
        self._query_service = query_service
        self._sid_mapper = sid_mapper

    async def build_event_payload(
        self, payload: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """将 OneBot 11 消息 payload 转为 v4 Event payload。

        Returns:
            (event_name, event_payload) — event_name 为 napcat.message 或 napcat.group_message。
        """
        message_type = payload.get("message_type", "private")
        sender = payload.get("sender", {})
        qq_user_id = str(sender.get("user_id", ""))
        self_id = str(payload.get("self_id", self._self_id))

        if qq_user_id == self_id:
            return ("", {})  # 过滤自身消息

        if message_type == "group":
            group_id = str(payload.get("group_id", ""))
            session_id = self._sid_mapper.calculate_group_session_id(group_id)
            self._sid_mapper.register_session(session_id, qq_user_id, group_id)
            event_name = "napcat.group_message"
        else:
            session_id = self._sid_mapper.calculate_private_session_id(qq_user_id)
            self._sid_mapper.register_session(session_id, qq_user_id)
            event_name = "napcat.message"

        segments, _ = await self.convert_segments_with_metadata(payload, self_id)
        raw_message = payload.get("raw_message", "")
        message_id = str(payload.get("message_id", ""))

        return event_name, {
            "session_id": session_id,
            "message_id": message_id,
            "qq_user_id": qq_user_id,
            "qq_group_id": str(payload.get("group_id", "")) if message_type == "group" else "",
            "sender_name": str(sender.get("nickname", "")),
            "raw_message": raw_message,
            "segments": [s.to_dict() for s in segments],
            "timestamp": payload.get("time", 0),
            "is_group": message_type == "group",
        }

    async def build_notice_event_payload(
        self, payload: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """将 OneBot 11 通知 payload 转为 v4 通知 Event payload。

        Returns:
            (event_name, event_payload) — event_name 固定为 napcat.notice。
        """
        notice_type = str(payload.get("notice_type", ""))
        sub_type = str(payload.get("sub_type", ""))
        qq_user_id = str(payload.get("user_id", ""))
        group_id = str(payload.get("group_id", ""))

        event_payload = {
            "napcat_notice_type": notice_type,
            "napcat_sub_type": sub_type,
            "qq_user_id": qq_user_id,
            "qq_group_id": group_id,
            "raw": payload,
        }
        return "napcat.notice", event_payload
