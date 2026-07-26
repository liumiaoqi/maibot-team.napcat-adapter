"""NapCatAdapterPlugin — v4 插件主类。

NapCatQQ OneBot 11 适配器，通过 WebSocket 接收事件、HTTP API 发送消息。
3 Event + 5 Tool，完整映射 v1 MessageGateway 生命周期到 v4 MaiBotPlugin。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from src.plugin_runtime_v2.sdk import MaiBotPlugin, Tool

from .codecs.inbound.message_codec import NapCatInboundCodec
from .codecs.outbound.message_codec import NapCatOutboundCodec
from .config import NapCatConfig
from .constants import NapCatPayload
from .event_buffer import EventBuffer
from .filters import NapCatChatFilter, NapCatRegexFilter
from .heartbeat_monitor import NapCatHeartbeatMonitor
from .services import NapCatQueryService
from .session_mapper import SessionIdMapper
from .transport import NapCatTransportClient


class NapCatAdapterPlugin(MaiBotPlugin):
    """NapCat QQ 适配器插件（v4）。

    通过 NapCatQQ OneBot 11 协议与 QQ 平台通信。
    生命周期：on_load → 初始化组件 → transport.start() → on_unload → transport.stop()
    """

    plugin_id = "maibot-team.napcat-adapter"
    plugin_version = "2.0.0"
    scopes = [
        "message:send:text",
        "message:send:image",
        "message:send:emoji",
        "message:send:forward",
        "message:send:hybrid",
        "session:read:detail",
        "database:read:self",
        "database:write:self",
        "system:execute:command",
    ]

    def __init__(self) -> None:
        self._cfg = NapCatConfig()
        self._transport: NapCatTransportClient | None = None
        self._inbound_codec: NapCatInboundCodec | None = None
        self._outbound_codec: NapCatOutboundCodec | None = None
        self._sid_mapper = SessionIdMapper()
        self._query_service: NapCatQueryService | None = None
        self._chat_filter = NapCatChatFilter()
        self._regex_filter = NapCatRegexFilter()
        self._heartbeat: NapCatHeartbeatMonitor | None = None
        self._event_buffer = EventBuffer()
        self._recv_task: asyncio.Task | None = None

    # ── 生命周期 ──────────────────────────────────────────────

    async def on_load(self) -> None:
        """插件加载：验证配置、初始化组件、建立 WS 连接。"""
        self.ctx.logger.info("[napcat] on_load 开始")

        # 验证配置
        errors = self._cfg.validate_runtime_config()
        if errors:
            self.ctx.logger.error("[napcat] 配置缺失: %s", errors)
            return
        if not self._cfg.should_connect():
            self.ctx.logger.info("[napcat] 未配置连接信息，跳过")
            return

        # 初始化组件
        self._query_service = NapCatQueryService(self._cfg.napcat_server.http_url)
        self._sid_mapper = SessionIdMapper()
        self._inbound_codec = NapCatInboundCodec(
            self_id="", query_service=self._query_service, sid_mapper=self._sid_mapper,
        )
        self._outbound_codec = NapCatOutboundCodec(sid_mapper=self._sid_mapper)
        self._chat_filter = NapCatChatFilter(self._cfg.chat)
        self._regex_filter = NapCatRegexFilter(self._cfg.filters)
        self._event_buffer = EventBuffer(max_size=self._cfg.buffer.event_buffer_size)

        # 建立 WS 连接并启动接收循环
        self._transport = NapCatTransportClient(self._cfg)
        await self._transport.start()
        self._heartbeat = NapCatHeartbeatMonitor(self._transport)
        await self._heartbeat.start()
        self._recv_task = asyncio.create_task(self._recv_loop(), name="napcat-recv")
        self.ctx.logger.info("[napcat] 已连接 NapCat 服务器")

    async def on_unload(self) -> None:
        """插件卸载：停止接收、关闭连接、清理资源。"""
        self.ctx.logger.info("[napcat] on_unload 开始")
        if self._recv_task is not None:
            self._recv_task.cancel()
            self._recv_task = None
        if self._heartbeat is not None:
            await self._heartbeat.stop()
        if self._transport is not None:
            await self._transport.stop()
        self.ctx.logger.info("[napcat] 已断开连接")

    # ── 消息接收循环 ─────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """持续接收 WS payload → codec → emit_event。"""
        if self._transport is None:
            return
        try:
            while True:
                payload = await self._transport.receive()
                await self._handle_transport_payload(payload)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.ctx.logger.warning("[napcat] 接收循环异常: %s", exc)

    async def _handle_transport_payload(self, payload: NapCatPayload) -> None:
        """路由 WS payload 到对应处理器。"""
        post_type = payload.get("post_type", "")

        if post_type == "message":
            await self._handle_message_event(payload)
        elif post_type == "notice":
            await self._handle_notice_event(payload)
        elif post_type == "meta_event":
            await self._handle_meta_event(payload)

    async def _handle_message_event(self, payload: NapCatPayload) -> None:
        """处理 OneBot 11 消息事件 → emit Event 到 Host。"""
        if self._inbound_codec is None:
            return
        event_name, event_payload = await self._inbound_codec.build_event_payload(payload)
        if not event_name:
            return  # 自身消息过滤

        # 频道过滤 + 正则过滤
        if not self._chat_filter.should_process(event_payload):
            return
        if not self._regex_filter.should_process(event_payload.get("raw_message", "")):
            return

        # 去重检查
        msg_id = event_payload.get("message_id", "")
        if self._event_buffer.is_duplicate(msg_id):
            return

        if self._transport is not None and not self._transport.is_connected:
            self._event_buffer.enqueue(event_name, event_payload)
            return

        # 发送前刷新缓冲区
        if self._event_buffer.size > 0:
            await self._event_buffer.flush(self.ctx.emit_event)

        await self.ctx.emit_event(event_name, event_payload)

    async def _handle_notice_event(self, payload: NapCatPayload) -> None:
        """处理 OneBot 11 通知事件 → emit Event 到 Host。"""
        if self._inbound_codec is None:
            return
        event_name, event_payload = await self._inbound_codec.build_notice_event_payload(
            payload,
        )
        if not event_name:
            return
        await self.ctx.emit_event(event_name, event_payload)

    async def _handle_meta_event(self, payload: NapCatPayload) -> None:
        """处理元事件（heartbeat 等）→ 仅记录日志。"""
        meta_type = payload.get("meta_event_type", "")
        self.ctx.logger.debug("[napcat] meta_event: %s", meta_type)
        if meta_type == "heartbeat":
            if self._heartbeat is not None:
                self._heartbeat.record_heartbeat()

    # ── @Tool：发送消息 ───────────────────────────────────────

    @Tool(name="napcat.send_text", description="发送文本消息到 QQ")
    async def tool_send_text(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args["session_id"]
        text = args["text"]
        action, params = self._outbound_codec.build_send_text_action(session_id, text)
        return await self._call_transport(action, params)

    @Tool(name="napcat.send_image", description="发送图片消息到 QQ")
    async def tool_send_image(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args["session_id"]
        image_base64 = args["image_base64"]
        action, params = self._outbound_codec.build_send_image_action(
            session_id, image_base64,
        )
        return await self._call_transport(action, params)

    @Tool(name="napcat.send_emoji", description="发送表情包到 QQ")
    async def tool_send_emoji(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args["session_id"]
        emoji_base64 = args["emoji_base64"]
        action, params = self._outbound_codec.build_send_emoji_action(
            session_id, emoji_base64,
        )
        return await self._call_transport(action, params)

    @Tool(name="napcat.send_forward", description="转发消息到 QQ")
    async def tool_send_forward(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args["session_id"]
        message_id = args["message_id"]
        action, params = self._outbound_codec.build_send_forward_action(
            session_id, message_id,
        )
        return await self._call_transport(action, params)

    @Tool(name="napcat.send_hybrid", description="发送混合消息到 QQ")
    async def tool_send_hybrid(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args["session_id"]
        segments = json.loads(args.get("segments", "[]"))
        action, params = self._outbound_codec.build_send_hybrid_action(
            session_id, segments,
        )
        return await self._call_transport(action, params)

    async def _call_transport(
        self, action: str, params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """调用 NapCat HTTP API 并返回标准化结果。"""
        if self._transport is None:
            return {"success": False, "error": "TRANSPORT_NOT_INITIALIZED"}
        try:
            result = await self._transport.call_action(action, params)
            return {"success": True, "result": result}
        except Exception as exc:
            self.ctx.logger.warning(
                "[napcat] call_action %s 失败: %s", action, exc,
            )
            return {"success": False, "error": str(exc)}
