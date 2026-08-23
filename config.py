"""v4 配置模型 — NapCat 适配器的 TOML 配置结构。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class NapCatServerConfig:
    """NapCat 服务端连接配置。"""
    ws_url: str = "ws://127.0.0.1:3001"
    http_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    connection_id: str = ""
    token: str = ""
    heartbeat_interval: float | None = None
    reconnect_delay_sec: float = 5.0

    def build_ws_url(self) -> str:
        if self.connection_id:
            return f"{self.ws_url}?connection_id={self.connection_id}"
        return self.ws_url


@dataclass
class ReconnectConfig:
    """重连策略配置。"""
    max_retries: int = 10
    interval_sec: float = 5.0


@dataclass
class BufferConfig:
    """事件缓冲区配置。"""
    event_buffer_size: int = 1000


@dataclass
class ChatConfig:
    """聊天过滤配置。"""
    enable_chat_list_filter: bool = False
    group_list: list[str] = field(default_factory=list)
    private_list: list[str] = field(default_factory=list)
    ban_qq_bot: bool = True
    ignore_self_message: bool = True


@dataclass
class FilterConfig:
    """正则过滤配置。"""
    regex_filter_enabled: bool = False
    regex_filter_mode: str = "whitelist"
    regex_filter_patterns: list[str] = field(default_factory=list)


@dataclass
class MaimMessageBridgeConfig:
    enabled: bool = True
    ws_host: str = "127.0.0.1"
    ws_port: int = 8000
    reconnect_delay_sec: float = 5.0


@dataclass
class NapCatConfig:
    """NapCat 适配器完整配置。"""
    napcat_server: NapCatServerConfig = field(default_factory=NapCatServerConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)
    buffer: BufferConfig = field(default_factory=BufferConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    maim_message_bridge: MaimMessageBridgeConfig = field(default_factory=MaimMessageBridgeConfig)

    def should_connect(self) -> bool:
        """是否应该建立与 NapCat 服务器的连接。

        连接条件：ws_url 和 http_url 均非空。
        """
        return bool(self.napcat_server.ws_url and self.napcat_server.http_url)

    def validate_runtime_config(self) -> list[str]:
        """校验运行时必需配置项，返回缺失的配置项列表。"""
        errors: list[str] = []
        if not self.napcat_server.ws_url:
            errors.append("napcat_server.ws_url")
        if not self.napcat_server.http_url:
            errors.append("napcat_server.http_url")
        return errors

    @classmethod
    def from_toml(cls, path: Path) -> NapCatConfig:
        """从 TOML 文件加载配置，缺失字段使用默认值。"""
        if not path.is_file():
            return cls()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        server_data = data.get("napcat_server", {})
        reconnect_data = data.get("reconnect", {})
        buffer_data = data.get("buffer", {})
        chat_data = data.get("chat", {})
        bridge_data = data.get("maim_message_bridge", {})
        filters_data = data.get("filters", {})
        return cls(
            napcat_server=NapCatServerConfig(
                ws_url=server_data.get("ws_url", "ws://127.0.0.1:3001"),
                http_url=server_data.get("http_url", "http://127.0.0.1:3000"),
                access_token=server_data.get("access_token", ""),
                connection_id=server_data.get("connection_id", ""),
                token=server_data.get("access_token", ""),
            ),
            reconnect=ReconnectConfig(
                max_retries=reconnect_data.get("max_retries", 10),
                interval_sec=reconnect_data.get("interval_sec", 5.0),
            ),
            buffer=BufferConfig(
                event_buffer_size=buffer_data.get("event_buffer_size", 1000),
            ),
            chat=ChatConfig(
                enable_chat_list_filter=chat_data.get("enable_chat_list_filter", False),
                group_list=chat_data.get("group_list", []),
                private_list=chat_data.get("private_list", []),
                ban_qq_bot=chat_data.get("ban_qq_bot", True),
                ignore_self_message=chat_data.get("ignore_self_message", True),
            ),
            filters=FilterConfig(
                regex_filter_enabled=filters_data.get("regex_filter_enabled", False),
                regex_filter_mode=filters_data.get("regex_filter_mode", "whitelist"),
                regex_filter_patterns=filters_data.get("regex_filter_patterns", []),
            ),
            maim_message_bridge=MaimMessageBridgeConfig(
                enabled=bridge_data.get("enabled", True),
                ws_host=bridge_data.get("ws_host", "127.0.0.1"),
                ws_port=bridge_data.get("ws_port", 8000),
                reconnect_delay_sec=bridge_data.get("reconnect_delay_sec", 5.0),
            ),
        )
