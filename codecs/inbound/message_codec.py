"""入站消息编解码 — 将 OneBot 11 payload 转为 v4 Event payload。

v1 输出 MessageDict → v4 输出 Event payload dict（通过 ctx.emit_event 推送）。
消息段解析逻辑复用 cards.py / text.py（CX 已迁移）。
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple, TYPE_CHECKING

from .cards import NapCatInboundCardMixin
from .text import NapCatInboundTextMixin
from ...types import NapCatSegments

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

    async def convert_segments_with_metadata(
        self, payload: Dict[str, Any], self_id: str,
    ) -> Tuple[NapCatSegments, Dict[str, Any]]:
        """将 OneBot 11 消息数组转为 NapCatHostMessageSegment 列表 + 元数据。

        Args:
            payload: OneBot 11 消息事件 payload。
            self_id: 机器人自身 QQ 号。

        Returns:
            (segments, metadata) — segments 为转换后的消息段列表，
            metadata 包含 at 列表、reply 信息等。
        """
        raw_segments = payload.get("message", [])
        if not isinstance(raw_segments, list):
            raw_segments = []

        segments: NapCatSegments = []
        metadata: Dict[str, Any] = {
            "at_user_ids": [],
            "reply_message_id": None,
            "platform_card_payloads": [],
        }

        for seg in raw_segments:
            if not isinstance(seg, Mapping):
                continue
            seg_type = str(seg.get("type") or "").strip()
            seg_data = seg.get("data", {})

            if seg_type == "text":
                text = str(seg_data.get("text", "") if isinstance(seg_data, Mapping) else seg_data or "")
                segments.append(self._build_text_segment(text))

            elif seg_type == "at":
                if isinstance(seg_data, Mapping):
                    at_qq = str(seg_data.get("qq", ""))
                    if at_qq and at_qq != self_id:
                        metadata["at_user_ids"].append(at_qq)
                    at_name = str(
                        seg_data.get("name")
                        or seg_data.get("nickname")
                        or ""
                    ).strip()
                    display = f"@{at_name}" if at_name else f"@{at_qq}"
                    segments.append({"type": "at", "data": {"qq": at_qq, "name": at_name}})
                    segments.append(self._build_text_segment(display))

            elif seg_type == "reply":
                reply_id = str(seg_data.get("id", "") if isinstance(seg_data, Mapping) else "")
                metadata["reply_message_id"] = reply_id
                segments.append({"type": "reply", "data": reply_id})

            elif seg_type == "face":
                face_id = str(seg_data.get("id", "0") if isinstance(seg_data, Mapping) else "0")
                segments.append({"type": "face", "data": face_id})

            elif seg_type == "image":
                image_segment = await self._convert_image_segment(seg_data)
                segments.append(image_segment)

            elif seg_type == "json":
                json_segments = await self._build_json_segments(
                    seg_data if isinstance(seg_data, Mapping) else {"data": str(seg_data)},
                    platform_card_payloads=metadata["platform_card_payloads"],
                )
                segments.extend(json_segments)

            elif seg_type == "forward":
                segments.append({"type": "forward", "data": str(seg_data.get("id", "") if isinstance(seg_data, Mapping) else "")})

            elif seg_type in {"record", "video"}:
                segments.append({"type": seg_type, "data": ""})

            elif seg_type == "poke":
                segments.append(self._build_text_segment("[戳一戳]"))

            else:
                fallback = f"[{seg_type}]" if seg_type else "[unsupported]"
                segments.append(self._build_text_segment(fallback))

        return segments, metadata

    async def _convert_image_segment(self, seg_data: Any) -> Dict[str, Any]:
        """将 OneBot 11 图片段转为 NapCatHostMessageSegment。

        尝试下载图片获取二进制数据；失败则保留 URL 引用。
        """
        if not isinstance(seg_data, Mapping):
            return {"type": "image", "data": ""}

        image_url = str(seg_data.get("url") or "").strip()
        if image_url:
            binary_data = await self._query_service.download_binary(image_url)
            if binary_data:
                import hashlib
                return {
                    "type": "image",
                    "data": "",
                    "hash": hashlib.sha256(binary_data).hexdigest(),
                    "binary_data_base64": self._encode_binary(binary_data),
                }

        return {"type": "image", "data": ""}

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
            "segments": [dict(s) for s in segments],
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
