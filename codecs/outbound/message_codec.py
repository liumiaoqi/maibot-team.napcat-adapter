"""出站消息编解码 — 将 v4 Tool args 转为 OneBot 11 HTTP API action。

v1 输入 MessageDict+route → v4 输入 Tool args（ctx.send 参数）+ SessionIdMapper。
segment 编码逻辑复用 segment_encoder.py（CX 已迁移）。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, TYPE_CHECKING

from .segment_encoder import NapCatOutboundSegmentEncoder

if TYPE_CHECKING:
    from ...session_mapper import SessionIdMapper


class NapCatOutboundCodec(NapCatOutboundSegmentEncoder):
    """出站消息编解码器 — 5 个发送方法对应 5 个 OneBot 11 action。"""

    def __init__(self, sid_mapper: SessionIdMapper) -> None:
        self._sid_mapper = sid_mapper

    def build_send_text_action(
        self, session_id: str, text: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """发送文本消息 → send_msg / send_private_msg / send_group_msg。"""
        action, params = self._build_base_action(session_id)
        params["message"] = [{"type": "text", "data": {"text": text}}]
        return action, params

    def build_send_image_action(
        self, session_id: str, image_base64: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """发送图片 → send_msg。"""
        _, params = self._build_base_action(session_id)
        params["message"] = [
            {"type": "image", "data": {"file": f"base64://{image_base64}"}}
        ]
        return "send_msg", params

    def build_send_emoji_action(
        self, session_id: str, emoji_base64: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """发送表情包 → send_msg。"""
        _, params = self._build_base_action(session_id)
        params["message"] = [
            {"type": "image", "data": {"file": f"base64://{emoji_base64}"}}
        ]
        return "send_msg", params

    def build_send_forward_action(
        self, session_id: str, message_id: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """转发消息 → forward_friend_single_msg / forward_group_single_msg。"""
        _, params = self._build_base_action(session_id)
        params["message_id"] = message_id
        # 群聊转发与私聊转发使用不同的 action
        if params.get("group_id"):
            return "forward_group_single_msg", params
        return "forward_friend_single_msg", params

    def build_send_hybrid_action(
        self, session_id: str, segments: list[Dict[str, Any]],
    ) -> Tuple[str, Dict[str, Any]]:
        """发送混合消息（图文等）→ 逐个 encode segment。"""
        _, params = self._build_base_action(session_id)
        encoded: list[Dict[str, Any]] = []
        for seg in segments:
            encoded.append(self._encode_segment(seg))
        params["message"] = encoded
        return "send_msg", params

    # ── 内部辅助 ──────────────────────────────────────────────

    def _build_base_action(self, session_id: str) -> Tuple[str, Dict[str, Any]]:
        """根据 session_id 构造 OneBot 11 action 和基础参数。

        群聊 → send_group_msg(group_id=...)
        私聊 → send_private_msg(user_id=...)
        """
        user_id = self._sid_mapper.get_qq_user_id(session_id)
        group_id = self._sid_mapper.get_qq_group_id(session_id)

        if group_id:
            return "send_group_msg", {"group_id": group_id}
        if user_id:
            return "send_private_msg", {"user_id": user_id}
        # fallback
        return "send_msg", {"user_id": user_id, "group_id": group_id}

    def _encode_segment(self, seg: Dict[str, Any]) -> Dict[str, Any]:
        """将 SDK 层 segment dict 编码为 OneBot 11 格式。"""
        seg_type = seg.get("type", "text")
        data = seg.get("data", {})

        if seg_type == "text":
            return {"type": "text", "data": {"text": data.get("text", "")}}
        if seg_type == "image":
            b64 = data.get("base64", data.get("file", ""))
            return {"type": "image", "data": {"file": f"base64://{b64}"}}
        if seg_type == "at":
            return {"type": "at", "data": {"qq": str(data.get("qq", "all"))}}
        if seg_type == "reply":
            return {"type": "reply", "data": {"id": str(data.get("id", ""))}}
        if seg_type == "face":
            return {"type": "face", "data": {"id": str(data.get("id", "0"))}}
        return {"type": seg_type, "data": data}
