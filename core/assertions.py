"""断言封装层：把断言失败信息做成"可复核的证据"，而不是一句 assert False。

设计要点（面试可讲）：
  断言失败必须能回答三个问题——请求是什么、实际返回什么、期望什么。
  因此所有断言统一接收 RequestRecord，失败时把证据一起抛出去。
"""

from typing import Any

from jsonschema import ValidationError, validate

from core.http_client import RequestRecord


class AssertionFailure(AssertionError):
    """断言失败，携带请求证据，便于在报告里直接复核。"""


def _evidence(record: RequestRecord, expected: str) -> str:
    body = record.response_body
    if isinstance(body, str) and len(body) > 400:
        body = body[:400] + "...(截断)"
    return (
        f"\n  期望: {expected}"
        f"\n  实际: {record.summary}"
        f"\n  请求体: {record.request_body}"
        f"\n  响应体: {body}"
    )


def assert_status(record: RequestRecord, expected: int | tuple[int, ...]) -> None:
    """断言 HTTP 状态码。"""
    if isinstance(expected, tuple):
        ok = record.status_code in expected
    else:
        ok = record.status_code == expected
    if not ok:
        raise AssertionFailure(
            _evidence(record, f"状态码 {expected}，实际 {record.status_code}")
        )


def assert_field(record: RequestRecord, path: str, expected: Any = None) -> Any:
    """断言响应 JSON 中某个字段存在（expected 为 None 时只判断存在）。

    path 支持点号路径，例如 data.items.0.id
    """
    current: Any = record.response_body
    walked: list[str] = []
    for part in path.split("."):
        walked.append(part)
        try:
            if isinstance(current, list):
                current = current[int(part)]
            else:
                current = current[part]
        except (KeyError, IndexError, TypeError, ValueError):
            raise AssertionFailure(
                _evidence(record, f"响应中存在字段 {path}（走到 {'.'.join(walked)} 时缺失）")
            ) from None
    if expected is not None and current != expected:
        raise AssertionFailure(
            _evidence(record, f"字段 {path} == {expected!r}，实际 {current!r}")
        )
    return current


def assert_schema(instance: Any, schema: dict) -> None:
    """按 JSON Schema 校验响应结构，防止字段改名/类型漂移。"""
    try:
        validate(instance=instance, schema=schema)
    except ValidationError as exc:
        raise AssertionFailure(
            f"\n  响应结构不符合 schema：{exc.message}\n  出错位置：{list(exc.path)}"
        ) from None


def assert_response_time(record: RequestRecord, limit_ms: float = 3000) -> None:
    """断言响应耗时，用于捕获性能退化。"""
    if record.elapsed_ms > limit_ms:
        raise AssertionFailure(
            _evidence(record, f"响应耗时 <= {limit_ms:.0f}ms，实际 {record.elapsed_ms:.0f}ms")
        )


def assert_no_sensitive_leak(record: RequestRecord, secrets: list[str]) -> None:
    """断言响应体里没有把密码、token 等敏感信息原样回吐。"""
    raw = str(record.response_body)
    for secret in secrets:
        if secret and secret in raw:
            raise AssertionFailure(
                _evidence(record, f"响应体中不应出现敏感信息 {secret[:4]}***")
            )
