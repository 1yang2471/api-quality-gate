"""请求封装层：统一 base_url、超时、重试、鉴权、日志与耗时统计。

设计要点（面试可讲）：
  1. 用例层不直接调用 requests，只调用本层，便于统一加日志/重试/鉴权；
  2. 每个请求返回 RequestRecord（响应 + 耗时 + 请求摘要），
     断言层与报告层都能拿到同一份证据；
  3. 重试只针对"网络抖动 / 5xx"，不对 4xx 重试——否则会把业务缺陷重试成偶发。
"""

from dataclasses import dataclass, field

import requests
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from core.config_loader import Credential, EnvConfig
from core.logger import get_logger, mask

logger = get_logger()

# 只有这几类错误值得重试：连接失败、读写超时、服务端 5xx
RETRYABLE_STATUS = {500, 502, 503, 504}


class UpstreamServerError(Exception):
    """服务端 5xx，触发重试。"""


@dataclass
class RequestRecord:
    """一次请求的完整证据，供断言层与报告层共用。"""

    method: str
    url: str
    status_code: int
    elapsed_ms: float
    request_headers: dict = field(default_factory=dict)
    request_body: object = None
    response_body: object = None
    response_headers: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return f"{self.method} {self.url} -> {self.status_code} ({self.elapsed_ms:.0f}ms)"

    @property
    def total_count(self) -> int | None:
        """读取 X-WP-Total 响应头（WooCommerce 分页总数），缺失时返回 None。"""
        for key, value in self.response_headers.items():
            if key.lower() == "x-wp-total":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None


class HttpClient:
    """接口调用的唯一入口。"""

    def __init__(self, env: EnvConfig, default_headers: dict | None = None):
        self.env = env
        self.session = requests.Session()
        self.default_headers = {"Accept": "application/json"}
        self.default_headers.update(default_headers or {})

    # ── 鉴权 ──────────────────────────────────────────────────────────
    def use_basic_auth(self, credential: Credential) -> None:
        """启用 HTTP Basic 鉴权（WooCommerce 密钥 / WordPress 应用密码）。"""
        self.session.auth = credential.basic_auth
        logger.info("启用 Basic 鉴权: %s / %s", credential.name, mask(credential.secret))

    def use_basic_auth_values(self, username: str, secret: str) -> None:
        """直接用用户名/密钥启用 Basic 鉴权，便于负向用例临时切换。"""
        self.session.auth = (username, secret)

    def clear_auth(self) -> None:
        """清空鉴权，用于"未携带凭据必须被拒绝"的负向用例。"""
        self.session.auth = None

    def set_bearer(self, token: str) -> None:
        """Bearer 鉴权（保留给使用 token 的被测系统）。"""
        self.default_headers["Authorization"] = f"Bearer {token}"
        logger.info("启用 Bearer 鉴权: %s", mask(token))

    # ── 连通性 ────────────────────────────────────────────────────────
    def is_alive(self) -> bool:
        """探测被测系统是否可用，用于决定用例是跳过还是执行。"""
        try:
            resp = self.session.get(f"{self.env.base_url}{self.env.health_path}", timeout=3)
            return resp.status_code < 500
        except requests.RequestException:
            return False

    # ── 核心请求 ──────────────────────────────────────────────────────
    def request(self, method: str, path: str, **kwargs) -> RequestRecord:
        """发送请求并记录证据。path 支持绝对路径或完整 URL。

        kwargs 里的 auth=(user, secret) 可对单次请求覆盖鉴权，
        用于"换个凭据再试一次"的越权用例。
        """
        url = path if path.startswith("http") else f"{self.env.base_url}{path}"
        headers = {**self.default_headers, **(kwargs.pop("headers", None) or {})}
        kwargs.setdefault("timeout", self.env.timeout)

        record = self._request_with_retry(method, url, headers, **kwargs)
        logger.info("%s | %.0fms", record.summary, record.elapsed_ms)
        return record

    def _request_with_retry(self, method: str, url: str, headers: dict, **kwargs):
        """按配置重试；重试策略由 tenacity 声明，避免手写循环。"""

        def _raise_if_retryable(resp: requests.Response) -> None:
            if resp.status_code in RETRYABLE_STATUS:
                raise UpstreamServerError(f"{resp.status_code} on {url}")

        def _before_sleep(state: RetryCallState) -> None:
            logger.warning("第 %s 次重试: %s", state.attempt_number, state.outcome.exception())

        @retry(
            stop=stop_after_attempt(max(1, self.env.retries + 1)),
            wait=wait_fixed(0.5),
            retry=retry_if_exception_type(
                (requests.ConnectionError, requests.Timeout, UpstreamServerError)
            ),
            before_sleep=_before_sleep,
            reraise=True,
        )
        def _send() -> RequestRecord:
            resp = self.session.request(method, url, headers=headers, **kwargs)
            _raise_if_retryable(resp)
            return self._to_record(resp, method, url, headers, kwargs)

        return _send()

    @staticmethod
    def _to_record(resp, method, url, headers, kwargs) -> RequestRecord:
        """把 requests 的响应转成统一证据对象，JSON 解析失败时保留原文。"""
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return RequestRecord(
            method=method.upper(),
            url=url,
            status_code=resp.status_code,
            elapsed_ms=resp.elapsed.total_seconds() * 1000,
            request_headers=headers,
            request_body=kwargs.get("json") or kwargs.get("data"),
            response_body=body,
            response_headers=dict(resp.headers),
        )

    # ── 语法糖 ────────────────────────────────────────────────────────
    def get(self, path: str, **kwargs) -> RequestRecord:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> RequestRecord:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> RequestRecord:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> RequestRecord:
        return self.request("DELETE", path, **kwargs)
