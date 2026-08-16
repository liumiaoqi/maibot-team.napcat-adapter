"""maim_message WS 桥接 — 将 maim_message 旧版 WS Server 的出站消息桥接到 NapCat OneBot API。"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Callable, Coroutine, Dict, Optional


class MaimMessageBridge:
    """maim_message WS 桥接客户端。

    作为 WS 客户端连接到 maim_message 的旧版 WS Server，
    携带 platform: qq header 注册平台，
    收到出站消息后转发给 NapCat OneBot API。
    """

    def __init__(
        self,
        logger: Any,
        ws_host: str,
        ws_port: int,
        on_outbound_message: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
        reconnect_delay_sec: float = 5.0,
    ) -> None:
        self._logger = logger
        self._ws_host = ws_host
        self._ws_port = ws_port
        self._on_outbound_message = on_outbound_message
        self._reconnect_delay_sec = reconnect_delay_sec
        self._task: Optional[asyncio.Task] = None
        self._stop_requested: bool = False
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_requested = False
        self._task = asyncio.create_task(self._connection_loop(), name="napcat_bridge.maim_message")

    async def stop(self) -> None:
        self._stop_requested = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._connected = False

    async def _connection_loop(self) -> None:
        try:
            from aiohttp import ClientSession, WSMsgType
        except ImportError:
            self._logger.warning("[bridge] aiohttp 未安装，maim_message 桥接不可用")
            return

        ws_url = f"ws://{self._ws_host}:{self._ws_port}/ws"

        while not self._stop_requested:
            try:
                async with ClientSession() as session:
                    async with session.ws_connect(
                        ws_url,
                        headers={"platform": "qq"},
                        heartbeat=30,
                    ) as ws:
                        self._connected = True
                        self._logger.info(f"[bridge] 已连接 maim_message WS: {ws_url} (platform=qq)")

                        async for msg in ws:
                            if msg.type == WSMsgType.TEXT:
                                try:
                                    payload = json.loads(msg.data)
                                    await self._on_outbound_message(payload)
                                except json.JSONDecodeError as exc:
                                    self._logger.warning(f"[bridge] JSON 解析失败: {exc}")
                                except Exception as exc:
                                    self._logger.warning(f"[bridge] 出站消息处理失败: {exc}", exc_info=True)
                            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.debug(f"[bridge] 连接 maim_message WS 失败: {exc}")
            finally:
                self._connected = False

            if self._stop_requested:
                break
            await asyncio.sleep(self._reconnect_delay_sec)