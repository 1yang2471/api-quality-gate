"""接口层基类：收敛公共路径前缀与通用响应提取。"""

from core.http_client import HttpClient, RequestRecord


class BaseApi:
    """所有业务接口的父类。"""

    prefix = ""

    def __init__(self, client: HttpClient):
        self.client = client

    def url(self, path: str) -> str:
        """拼接模块前缀，避免每个接口重复写路径。"""
        return f"{self.prefix}{path}"

    # ── 常用响应提取 ──
    @staticmethod
    def data_of(record: RequestRecord):
        """取响应中的 data 字段；不存在时返回 None 而不是抛异常。"""
        body = record.response_body
        if isinstance(body, dict):
            return body.get("data", body)
        return body
