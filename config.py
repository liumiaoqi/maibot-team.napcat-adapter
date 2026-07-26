"""v4 配置模型 — NapCat 适配器的 TOML 配置结构。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NapCatServerConfig:
    """NapCat 服务端连接配置。"""
    ws_url: str = "ws://127.0.0.1:3001"
    http_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    connection_id: str = ""


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
class NapCatConfig:
    """NapCat 适配器完整配置。"""
    napcat_server: NapCatServerConfig = field(default_factory=NapCatServerConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)
    buffer: BufferConfig = field(default_factory=BufferConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)

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
