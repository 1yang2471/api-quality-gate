"""框架自检用例：不依赖被测系统，用来证明框架自身可用。

面试可讲：框架也是代码，也需要被测试。
这些用例保证"断言层真的能断言失败"、"路径登记表没有未验证来源"。
"""

import pytest

from api.endpoints import Endpoints
from core.assertions import (
    AssertionFailure,
    assert_field,
    assert_response_time,
    assert_status,
)
from core.config_loader import load_settings
from core.http_client import RequestRecord
from core.logger import mask

pytestmark = pytest.mark.framework


def _record(status_code: int = 200, body=None, elapsed_ms: float = 100.0) -> RequestRecord:
    """构造一条已知的请求证据，用于验证断言层。"""
    return RequestRecord(
        method="GET",
        url="http://example.test/x",
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        response_body=body if body is not None else {"data": {"items": [{"id": "a1"}]}},
    )


class TestAssertions:
    """断言层必须"该过就过、该炸就炸"，否则整套用例都不可信。"""

    def test_status_pass_and_fail(self):
        assert_status(_record(200), 200)
        with pytest.raises(AssertionFailure):
            assert_status(_record(500), 200)

    def test_field_walk_nested_path(self):
        assert_field(_record(), "data.items.0.id", "a1")
        with pytest.raises(AssertionFailure):
            assert_field(_record(), "data.items.0.id", "a2")

    def test_field_missing_path_fails(self):
        with pytest.raises(AssertionFailure):
            assert_field(_record(), "data.nope.deep")

    def test_response_time_threshold(self):
        assert_response_time(_record(elapsed_ms=100), 3000)
        with pytest.raises(AssertionFailure):
            assert_response_time(_record(elapsed_ms=9000), 3000)

    def test_failure_message_carries_evidence(self):
        """失败信息必须包含请求摘要，否则报告无法复核。"""
        with pytest.raises(AssertionFailure) as exc:
            assert_status(_record(500), 200)
        text = str(exc.value)
        assert "GET" in text and "500" in text and "响应体" in text


class TestEndpointRegistry:
    """路径登记表门禁：禁止未标注来源的路径进入用例。"""

    def test_all_endpoints_have_valid_source(self):
        unverified = Endpoints.unverified()
        assert not unverified, (
            "以下接口路径缺少有效来源（docs/frontend/swagger），"
            f"禁止凭猜测使用：{[ep.path for ep in unverified]}"
        )

    def test_endpoint_paths_are_well_formed(self):
        """路径格式校验。

        注意：同一个路径可以服务多个 HTTP 方法（例如 /wc/v3/orders 同时支持
        GET 列表与 POST 创建），因此"路径唯一"不是有效约束，这里改为校验格式。
        """
        for ep in Endpoints.all():
            assert ep.path.startswith("/"), f"路径必须以 / 开头：{ep.path}"
            assert " " not in ep.path, f"路径不应包含空格：{ep.path}"
            assert "//" not in ep.path, f"路径不应出现连续斜杠：{ep.path}"
            assert ep.path == ep.path.rstrip("/") or ep.path.endswith("/wp-json/"), (
                f"路径末尾不应有多余斜杠：{ep.path}"
            )

    def test_path_params_are_filled(self):
        """带占位符的路径必须能正常填充，避免用例里出现未替换的 {xxx}。"""
        assert Endpoints.PRODUCT_DETAIL.url(product_id=93) == "/wp-json/wc/v3/products/93"
        assert (
            Endpoints.COUPON_DETAIL.url(coupon_id=7) == "/wp-json/wc/v3/coupons/7"
        )

    def test_expected_endpoints_are_registered(self):
        registered = {ep.path for ep in Endpoints.all()}
        for path in (
            "/wp-json/wc/v3/products",
            "/wp-json/wc/v3/orders",
            "/wp-json/wc/v3/coupons",
            "/wp-json/wc/v3/customers",
        ):
            assert path in registered, f"{path} 未登记路径来源"


class TestRecordEvidence:
    """请求证据对象自身的行为。"""

    def test_total_count_reads_header(self):
        rec = _record()
        rec.response_headers = {"X-WP-Total": "42"}
        assert rec.total_count == 42

    def test_total_count_missing_header(self):
        assert _record().total_count is None

    def test_total_count_non_numeric_header(self):
        rec = _record()
        rec.response_headers = {"x-wp-total": "not-a-number"}
        assert rec.total_count is None


class TestConfig:
    """配置层自检。"""

    def test_load_default_config(self):
        settings = load_settings()
        assert settings.env.base_url.startswith("http")
        assert settings.env.timeout > 0

    def test_credential_roundtrip(self):
        settings = load_settings()
        cred = settings.credential("admin")
        assert cred.basic_auth == (cred.username, cred.secret)

    def test_required_roles_exist(self):
        """配置必须同时具备管理员与低权限两个角色，否则越权用例无从对比。"""
        settings = load_settings()
        assert settings.credential("admin").username
        assert settings.credential("customer").username

    def test_unknown_role_raises(self):
        settings = load_settings()
        with pytest.raises(KeyError):
            settings.credential("not_a_role")


class TestLogMasking:
    """日志脱敏：密钥 / 密码不得完整出现在日志里。"""

    def test_mask_hides_middle(self):
        assert mask("abcdefghijklmn") == "abcd...klmn"
        assert mask("short") == "*****"
