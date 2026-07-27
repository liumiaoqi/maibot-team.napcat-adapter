"""NapCat 入站消息过滤。"""

from __future__ import annotations

import re
from typing import Any, List, Pattern

from .config import ChatConfig, FilterConfig


class NapCatRegexFilter:
    """NapCat 正则表达式消息内容过滤器。

    通过配置的正则表达式列表对消息纯文本进行匹配，
    支持黑名单（匹配则丢弃）和白名单（仅放行匹配）两种模式。
    """

    def __init__(self, logger: Any = None) -> None:
        """初始化正则表达式过滤器。

        Args:
            logger: 插件日志对象。
        """
        self._logger = logger
        self._compiled_patterns: List[Pattern[str]] = []
        self._source_patterns: List[str] = []

    def reload_patterns(self, patterns: List[str]) -> None:
        """根据正则表达式列表重新编译。

        无效的正则表达式会被记录警告并跳过。

        Args:
            patterns: 正则表达式字符串列表。
        """
        compiled: List[Pattern[str]] = []
        source: List[str] = []
        for pattern_text in patterns:
            try:
                compiled.append(re.compile(pattern_text))
                source.append(pattern_text)
            except re.error as exc:
                self._logger.warning(f"NapCat 正则过滤器忽略无效正则表达式 '{pattern_text}': {exc}")
        self._compiled_patterns = compiled
        self._source_patterns = source
        self._logger.debug(
            f"NapCat 正则过滤器已加载 {len(compiled)} 条规则: {source}"
        )

    def is_message_allowed(self, plain_text: str, filter_config: FilterConfig) -> bool:
        if not filter_config.regex_filter_enabled:
            return True

        if not self._compiled_patterns:
            if filter_config.regex_filter_mode == "whitelist":
                self._logger.warning("NapCat 白名单正则过滤器无有效规则，消息被丢弃")
                return False
            return True

        matched = self._matches_any_pattern(plain_text)

        if filter_config.regex_filter_mode == "blacklist":
            if matched:
                self._logger.warning(f"NapCat 消息匹配黑名单正则，消息被丢弃: {plain_text!r}")
                return False
            return True

        if not matched:
            self._logger.warning(f"NapCat 消息未匹配白名单正则，消息被丢弃: {plain_text!r}")
            return False
        return True

    def _matches_any_pattern(self, text: str) -> bool:
        """判断文本是否匹配任意一条已编译的正则表达式。

        Args:
            text: 待匹配的文本。

        Returns:
            bool: 若匹配到任意一条正则，则返回 ``True``。
        """
        for pattern in self._compiled_patterns:
            if pattern.search(text):
                return True
        return False




class NapCatChatFilter:
    """NapCat 聊天名单过滤器。"""

    def __init__(self, logger: Any = None) -> None:
        """初始化聊天名单过滤器。

        Args:
            logger: 插件日志对象。
        """
        self._logger = logger

    def is_inbound_chat_allowed(
        self,
        sender_user_id: str,
        group_id: str,
        chat_config: ChatConfig,
    ) -> bool:
        if not chat_config.enable_chat_list_filter:
            return True

        if group_id:
            if group_id not in chat_config.group_list:
                self._logger.warning(f"NapCat 群聊 {group_id} 未通过聊天名单过滤，消息被丢弃")
                return False
            return True

        if sender_user_id not in chat_config.private_list:
            self._logger.warning(f"NapCat 私聊用户 {sender_user_id} 未通过聊天名单过滤，消息被丢弃")
            return False
        return True


