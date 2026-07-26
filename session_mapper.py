"""Session ID 映射服务 — 将 QQ platform + user_id + group_id 映射为 session_id。

算法必须与 src/common/utils/utils_session.py 的 SessionUtils.calculate_session_id() 一致。
插件运行在独立进程中，无法导入 src/，故在此复制算法。
"""

from __future__ import annotations

import hashlib
from typing import Optional


class SessionIdMapper:
    """会话 ID 映射器 — 提供 calculate_session_id / register / query 功能。"""

    def __init__(self, platform: str = "qq", account_id: str = "", scope: str = "") -> None:
        self._platform = platform
        self._account_id = account_id
        self._scope = scope
        # session_id → (qq_user_id, qq_group_id)
        self._by_session: dict[str, tuple[str, str]] = {}

    # ── session_id 计算（复制 SessionUtils 算法） ──────────────

    @staticmethod
    def calculate_session_id(
        platform: str,
        *,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> str:
        """计算 session_id。算法与 SessionUtils.calculate_session_id() 完全一致。

        - 群聊 key = platform[_account:xxx][_scope:xxx]_group_id
        - 私聊 key = platform[_account:xxx][_scope:xxx]_user_id_private
        """
        if not user_id and not group_id:
            raise ValueError("UserID 或 GroupID 必须提供其一")

        route_components: list[str] = []
        if account_id:
            route_components.append(f"account:{account_id}")
        if scope:
            route_components.append(f"scope:{scope}")

        if group_id:
            components = [platform, *route_components, group_id]
        else:
            components = [platform, *route_components, user_id, "private"]
        return hashlib.md5("_".join(components).encode()).hexdigest()

    def calculate_private_session_id(self, user_id: str) -> str:
        return self.calculate_session_id(
            self._platform, user_id=user_id,
            account_id=self._account_id, scope=self._scope,
        )

    def calculate_group_session_id(self, group_id: str) -> str:
        return self.calculate_session_id(
            self._platform, group_id=group_id,
            account_id=self._account_id, scope=self._scope,
        )

    # ── 注册与查询 ────────────────────────────────────────────

    def register_session(
        self, session_id: str, qq_user_id: str, qq_group_id: str = "",
    ) -> None:
        """注册 session_id 与 QQ 用户/群 ID 的映射。"""
        self._by_session[session_id] = (qq_user_id, qq_group_id)

    def resolve_qq_ids(self, session_id: str) -> tuple[str, str]:
        """根据 session_id 反查 (qq_user_id, qq_group_id)。不存在返回 ("", "")。"""
        return self._by_session.get(session_id, ("", ""))

    def get_qq_user_id(self, session_id: str) -> str:
        return self._by_session.get(session_id, ("", ""))[0]

    def get_qq_group_id(self, session_id: str) -> str:
        return self._by_session.get(session_id, ("", ""))[1]

    # ── 持久化（预留） ────────────────────────────────────────

    def to_dict(self) -> dict[str, list[str]]:
        """序列化映射为可 JSON 的 dict。"""
        return {
            sid: [uid, gid] for sid, (uid, gid) in self._by_session.items()
        }

    def restore(self, data: dict[str, list[str]]) -> None:
        """从 dict 恢复映射。"""
        self._by_session = {
            sid: (vals[0], vals[1]) for sid, vals in data.items()
        }
