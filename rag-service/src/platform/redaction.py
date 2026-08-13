"""日志脱敏：替换 TEST_SECRET_SENTINEL 与常见认证头值（规范 11 LOG_REDACTION 场景）。"""
from __future__ import annotations

import logging
import os
import re


class SecretRedactionFilter(logging.Filter):
    """把夹具密钥与认证头值替换为 [REDACTED]。

    注册到 root logger；值只从环境读取（TEST_SECRET_SENTINEL），
    密钥不写入任何代码常量。无 sentinel 环境变量时只处理认证头。
    """

    _SENTINEL_ENV = "TEST_SECRET_SENTINEL"
    _HEADER_PATTERNS = (
        re.compile(r"(X-Signature[:=]\s*)[A-Za-z0-9+/=_-]+"),
        re.compile(r"(X-Nonce[:=]\s*)[A-Za-z0-9+/=_-]+"),
        re.compile(r"(Authorization[:=]\s*Bearer\s+)[A-Za-z0-9._~+/=-]+"),
        re.compile(r"(rag_database_session=[^;,\s]+)"),
    )

    def __init__(self) -> None:
        super().__init__()
        self._sentinel = os.environ.get(self._SENTINEL_ENV, "")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self._redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True

    def _redact(self, text: str) -> str:
        if self._sentinel and self._sentinel in text:
            text = text.replace(self._sentinel, "[REDACTED]")
        for pattern in self._HEADER_PATTERNS:
            text = pattern.sub(r"\1[REDACTED]", text)
        return text


def install_redaction() -> None:
    """注册到 root logger（幂等）。"""
    root = logging.getLogger()
    for existing in root.filters:
        if isinstance(existing, SecretRedactionFilter):
            return
    root.addFilter(SecretRedactionFilter())
