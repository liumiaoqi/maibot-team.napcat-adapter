"""NapCatAdapterPlugin — v4 插件主类。

NapCatQQ OneBot 11 适配器，通过 WebSocket 接收事件、HTTP API 发送消息。
3 Event + 5 Tool，完整映射 v1 MessageGateway 生命周期到 v4 MaiBotPlugin。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict

from src.plugin_runtime_v2.sdk import MaiBotPlugin, Event, Tool

from .codecs.inbound.message_codec import NapCatInboundCodec
from .codecs.outbound.message_codec import NapCatOutboundCodec
from .config import NapCatConfig
from .types import NapCatPayload
from .event_buffer import EventBuffer
from .filters import NapCatChatFilter, NapCatRegexFilter
from .heartbeat_monitor import NapCatHeartbeatMonitor
from .services import NapCatQueryService
from .session_mapper import SessionIdMapper
from .transport import NapCatTransportClient
from .maim_message_bridge import MaimMessageBridge


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
        self._bridge: MaimMessageBridge | None = None

    def _load_config(self) -> None:
        """从插件目录下的 config.toml 加载配置。"""
        import inspect
        plugin_file = Path(inspect.getfile(type(self)))
        config_path = plugin_file.parent / "config.toml"
        self._cfg = NapCatConfig.from_toml(config_path)

    # ── 生命周期 ──────────────────────────────────────────────

    async def on_load(self) -> None:
        """插件加载：验证配置、初始化组件、建立 WS 连接。"""
        self._load_config()
        self.ctx.logger.info("[napcat] on_load 开始, ws_url=%s", self._cfg.napcat_server.ws_url)

        # 验证配置
        errors = self._cfg.validate_runtime_config()
        if errors:
            self.ctx.logger.error("[napcat] 配置缺失: %s", errors)
            return
        if not self._cfg.should_connect():
            self.ctx.logger.info("[napcat] 未配置连接信息，跳过")
            return

        # 初始化组件
        _logger = self.ctx.logger
        self._sid_mapper = SessionIdMapper()

        # 创建 transport（传入回调）
        self._transport = NapCatTransportClient(
            logger=_logger,
            on_connection_opened=self._on_ws_connected,
            on_connection_closed=self._on_ws_disconnected,
            on_payload=self._handle_transport_payload,
        )

        # 创建依赖 transport 的服务
        from .services.action_service import NapCatActionService
        _action_service = NapCatActionService(logger=_logger, transport=self._transport)
        self._query_service = NapCatQueryService(action_service=_action_service, logger=_logger)

        # 创建 codec
        self._inbound_codec = NapCatInboundCodec(
            self_id="", query_service=self._query_service, sid_mapper=self._sid_mapper,
        )
        self._outbound_codec = NapCatOutboundCodec(sid_mapper=self._sid_mapper)

        # 创建过滤器和缓冲区
        self._chat_filter = NapCatChatFilter(logger=_logger)
        self._regex_filter = NapCatRegexFilter(logger=_logger)
        self._event_buffer = EventBuffer(max_size=self._cfg.buffer.event_buffer_size)

        # 配置并启动 transport
        self._transport.configure(self._cfg.napcat_server)
        await self._transport.start()
        self._heartbeat = NapCatHeartbeatMonitor(logger=_logger, on_timeout=self._on_heartbeat_timeout)
        await self._heartbeat.start(self_id="", default_interval_sec=30.0)

        self.ctx.logger.info("[napcat] 已连接 NapCat 服务器")

    async def on_unload(self) -> None:
        """插件卸载：停止接收、关闭连接、清理资源。"""
        self.ctx.logger.info("[napcat] on_unload 开始")
        if self._recv_task is not None:
            self._recv_task.cancel()
            self._recv_task = None
        if self._bridge is not None:
            await self._bridge.stop()
            self._bridge = None
        if self._heartbeat is not None:
            await self._heartbeat.stop()
        if self._transport is not None:
            await self._transport.stop()
        self.ctx.logger.info("[napcat] 已断开连接")

    # ── WebSocket 回调 ───────────────────────────────────────

    async def _on_ws_connected(self) -> None:
        self.ctx.logger.info("[napcat] WebSocket 已连接")
        if self._cfg.maim_message_bridge.enabled:
            self._bridge = MaimMessageBridge(
                logger=self.ctx.logger,
                ws_host=self._cfg.maim_message_bridge.ws_host,
                ws_port=self._cfg.maim_message_bridge.ws_port,
                on_outbound_message=self._handle_outbound_message,
                reconnect_delay_sec=self._cfg.maim_message_bridge.reconnect_delay_sec,
            )
            await self._bridge.start()

    async def _on_ws_disconnected(self) -> None:
        self.ctx.logger.warning("[napcat] WebSocket 已断开")
        if self._bridge is not None:
            await self._bridge.stop()
            self._bridge = None

    async def _on_heartbeat_timeout(self, reason: str) -> None:
        self.ctx.logger.warning("[napcat] 心跳超时: %s", reason)

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
        self.ctx.logger.info("[napcat] 收到 payload: post_type=%s", post_type)

        try:
            if post_type == "message":
                await self._handle_message_event(payload)
            elif post_type == "notice":
                await self._handle_notice_event(payload)
            elif post_type == "meta_event":
                await self._handle_meta_event(payload)
        except Exception as exc:
            self.ctx.logger.error("[napcat] _handle_transport_payload 异常: post_type=%s err=%s",
                                  post_type, exc, exc_info=True)

    async def _handle_message_event(self, payload: NapCatPayload) -> None:
        """处理 OneBot 11 消息事件 → emit Event 到 Host。"""
        if self._inbound_codec is None:
            self.ctx.logger.warning("[napcat] _handle_message_event: inbound_codec 未初始化，跳过")
            return
        try:
            event_name, event_payload = await self._inbound_codec.build_event_payload(payload)
            if not event_name:
                self.ctx.logger.debug("[napcat] _handle_message_event: event_name 空（自身消息过滤），跳过")
                return
            if not self._chat_filter.should_process(event_payload, self._cfg.chat):
                self.ctx.logger.debug("[napcat] _handle_message_event: chat_filter 拒绝，跳过")
                return
            if not self._regex_filter.should_process(event_payload.get("raw_message", ""), self._cfg.filters):
                self.ctx.logger.debug("[napcat] _handle_message_event: regex_filter 拒绝，跳过")
                return

            msg_id = event_payload.get("message_id", "")
            if self._event_buffer.is_duplicate(msg_id):
                self.ctx.logger.debug("[napcat] _handle_message_event: 重复消息 %s，跳过", msg_id)
                return

            if self._transport is not None and not self._transport.is_connected:
                self._event_buffer.enqueue(event_name, event_payload)
                self.ctx.logger.debug("[napcat] _handle_message_event: transport 断连，已缓冲")
                return

            if self._event_buffer.size() > 0:
                await self._event_buffer.flush(self.ctx.emit_event)

            self.ctx.logger.info("[napcat] emit_event: %s session=%s user=%s raw=%s",
                                 event_name,
                                 event_payload.get("session_id", "?"),
                                 event_payload.get("qq_user_id", "?"),
                                 event_payload.get("raw_message", "")[:50])
            await self.ctx.emit_event(event_name, event_payload)
        except Exception as exc:
            self.ctx.logger.error("[napcat] _handle_message_event 失败: %s", exc, exc_info=True)

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
        """处理元事件（heartbeat 等）→ 更新心跳监测。"""
        _meta_type = payload.get("meta_event_type", "")
        if self._heartbeat is not None:
            await self._heartbeat.observe_meta_event(payload, default_interval_sec=30.0)

    # ── @Event：消息入站声明 ─────────────────────────────────────

    @Event(name="napcat.message", description="QQ 私聊消息入站",
           event_schema={"type": "object", "properties": {
               "session_id": {"type": "string"},
               "message_id": {"type": "string"},
               "qq_user_id": {"type": "string"},
               "sender_name": {"type": "string"},
               "raw_message": {"type": "string"},
               "timestamp": {"type": "number"},
           }})
    async def on_private_message(self) -> None:
        """声明：QQ 私聊消息事件。"""
        pass

    @Event(name="napcat.group_message", description="QQ 群聊消息入站",
           event_schema={"type": "object", "properties": {
               "session_id": {"type": "string"},
               "message_id": {"type": "string"},
               "qq_user_id": {"type": "string"},
               "qq_group_id": {"type": "string"},
               "sender_name": {"type": "string"},
               "raw_message": {"type": "string"},
               "timestamp": {"type": "number"},
               "is_group": {"type": "boolean"},
           }})
    async def on_group_message(self) -> None:
        """声明：QQ 群聊消息事件。"""
        pass

    @Event(name="napcat.notice", description="QQ 通知事件（群成员变动等）",
           event_schema={"type": "object", "properties": {
               "napcat_notice_type": {"type": "string"},
               "napcat_sub_type": {"type": "string"},
               "qq_user_id": {"type": "string"},
               "qq_group_id": {"type": "string"},
           }})
    async def on_notice(self) -> None:
        """声明：QQ 通知事件。"""
        pass

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

    async def _handle_outbound_message(self, payload: Dict[str, Any]) -> None:
        """处理 maim_message WS 出站消息，桥接到 NapCat OneBot API。"""
        if self._transport is None or not self._transport.is_connected:
            self.ctx.logger.debug("[bridge] transport 未连接，跳过出站消息")
            return
        if self._outbound_codec is None:
            self.ctx.logger.debug("[bridge] outbound_codec 未初始化，跳过出站消息")
            return

        try:
            msg_info = payload.get("message_info", {})
            group_info = msg_info.get("group_info") or {}
            user_info = msg_info.get("user_info") or {}
            receiver_info = msg_info.get("receiver_info") or {}
            receiver_user_info = receiver_info.get("user_info") or {}

            group_id = group_info.get("group_id", "")
            # 私聊时接收者在 receiver_info.user_info.user_id（H3 修复：不走 sid_mapper，
            # 直接用 group_id / user_id 构造 OneBot action——message_info 无 session_id 字段）
            target_user_id = receiver_user_info.get("user_id", "") or user_info.get("user_id", "")

            # H2 修复：字段名是 message_segment（seglist 结构），不是 raw_message
            msg_segment = payload.get("message_segment", {})
            if isinstance(msg_segment, dict):
                raw_segments = msg_segment.get("data", [])
            else:
                raw_segments = []
            if not isinstance(raw_segments, list):
                raw_segments = []

            segments = []
            for seg in raw_segments:
                seg_type = seg.get("type", "text")
                seg_data = seg.get("data", {})
                # maim_message text 的 data 是字符串，需转为 dict
                if seg_type == "text" and isinstance(seg_data, str):
                    segments.append({"type": "text", "data": {"text": seg_data}})
                else:
                    segments.append({"type": seg_type, "data": seg_data})

            if not segments:
                self.ctx.logger.debug("[bridge] 出站消息无内容，跳过")
                return

            if group_id:
                action = "send_group_msg"
                params = {"group_id": group_id, "message": segments}
            elif target_user_id:
                action = "send_private_msg"
                params = {"user_id": target_user_id, "message": segments}
            else:
                self.ctx.logger.warning("[bridge] 无法确定目标（无 group_id/user_id），跳过出站消息")
                return

            await self._transport.call_action(action, params)
            self.ctx.logger.info("[bridge] 出站消息已发送: action=%s group=%s user=%s", action, group_id, target_user_id)
        except Exception as exc:
            self.ctx.logger.warning("[bridge] 出站消息处理失败: %s", exc, exc_info=True)
