"""日志封装：统一输出格式，便于失败时定位到具体请求。"""

import logging
import sys

_CONFIGURED = False


def get_logger(name: str = "api-quality-gate") -> logging.Logger:
    """返回统一格式的 logger；重复调用不会重复挂 handler。"""
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s",
                              datefmt="%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _CONFIGURED = True
    return logger


def mask(value: str) -> str:
    """脱敏：日志里不出现完整 token / 密码。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
