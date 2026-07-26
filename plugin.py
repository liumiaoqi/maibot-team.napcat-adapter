"""NapCatAdapterPlugin — v4 插件主类。"""
from __future__ import annotations


from src.plugin_runtime_v2.sdk import MaiBotPlugin


class NapCatAdapterPlugin(MaiBotPlugin):
    """NapCat QQ 适配器插件（v4）。

    通过 NapCatQQ OneBot 11 协议与 QQ 平台通信。
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

    async def on_load(self) -> None:
        """插件加载时初始化 WS 连接和消息处理。"""
        pass

    async def on_unload(self) -> None:
        """插件卸载时清理资源。"""
        pass
