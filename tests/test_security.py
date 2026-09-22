"""权限与鉴权用例：未授权访问、低权限角色越权、凭据不泄漏。

这类用例的价值最高——权限缺陷通常不会在正常流程中暴露，
但一旦漏到线上就是越权事故。

实测基准（2026-09-22，本地 WooCommerce 靶子）：
  - 无凭据 GET /wc/v3/products        -> 401
  - admin 应用密码 GET /wc/v3/products -> 200
  - customer 应用密码 GET /wc/v3/products -> 403 (woocommerce_rest_cannot_view)
"""

import pytest

from api.coupon_api import CouponApi
from api.product_api import ProductApi
from api.user_api import UserApi
from core.assertions import assert_status
from core.http_client import HttpClient

pytestmark = [pytest.mark.security]


def test_unauthenticated_read_is_rejected(settings, require_sut):
    """负向：未携带任何凭据访问商城接口必须 401。"""
    anon = HttpClient(settings.env)
    anon.clear_auth()
    record = ProductApi(anon).list_products(per_page=1)
    assert_status(record, 401)


def test_admin_can_read_products(client, require_sut):
    """正向：管理员凭据读取商品必须成功（确认后续负向用例的前提成立）。"""
    record = ProductApi(client).list_products(per_page=1)
    assert_status(record, 200)


def test_customer_cannot_read_products(customer_client, require_sut):
    """越权：客户角色没有商品管理权限，必须被拒绝而不是返回数据。"""
    record = ProductApi(customer_client).list_products(per_page=1)
    assert record.status_code in (401, 403), (
        f"低权限角色竟然能读取商品管理接口，实际 {record.status_code}：{record.summary}"
    )


def test_customer_cannot_create_product(customer_client, require_sut):
    """越权：客户角色创建商品必须被拒绝，绝不能静默成功。"""
    record = ProductApi(customer_client).create_product(name="越权测试商品")
    assert record.status_code in (401, 403), (
        f"低权限角色竟然可以创建商品，实际 {record.status_code}：{record.summary}"
    )


def test_customer_cannot_create_coupon(customer_client, require_sut):
    """越权：客户角色创建优惠券必须被拒绝。"""
    record = CouponApi(customer_client).create_coupon(code="QAGATE_OVERREACH", amount="1")
    assert record.status_code in (401, 403), (
        f"低权限角色竟然可以创建优惠券，实际 {record.status_code}：{record.summary}"
    )


def test_wp_customer_identity_is_verified(client, settings, require_sut):
    """身份：用低权限角色的应用密码查询身份，必须返回该角色自己的账号。"""
    customer = settings.credential("customer")
    record = UserApi(client).me_with(customer)
    assert_status(record, 200)
    assert record.response_body.get("slug") == customer.username or (
        record.response_body.get("name") == customer.username
    ), f"身份信息与凭据不匹配：{record.response_body}"


def test_users_endpoint_disclosure_is_documented(settings, require_sut):
    """风险观察：/wp-json/wp/v2/users 对匿名请求开放。

    实测修正（2026-09-22）：原本预期低权限角色访问该接口会被拒绝，
    实际匿名请求也返回 200，并暴露管理员账号的 id / name / slug / link。

    定性：这是 WordPress 的默认行为（用于公开作者归档），**不是配置错误**，
    因此不作为缺陷上报；但它确实构成一处信息暴露面，是否需要收敛
    取决于产品口径，故记录为"符合基准、待产品确认"。
    该用例断言的是"当前实际行为"，一旦站点收紧了权限，
    这条用例会失败并提醒我们更新结论——这正是回归用例的价值。
    """
    anon = HttpClient(settings.env)
    anon.clear_auth()
    record = UserApi(anon).list_users()
    assert_status(record, 200)
    exposed = record.response_body
    assert isinstance(exposed, list) and exposed, "用户列表为空，暴露面结论需重新评估"
    slugs = [item.get("slug") for item in exposed]
    assert "admin" in slugs, (
        f"预期能观察到管理员账号 slug 被暴露，实际 slugs={slugs}；"
        "若站点已收紧权限，请更新本条结论"
    )


def test_forged_credentials_are_rejected(settings, require_sut):
    """负向：伪造的凭据必须 401，不能因为校验缺失就放行。"""
    forged = HttpClient(settings.env)
    forged.use_basic_auth_values("admin", "definitely-not-a-valid-password")
    record = ProductApi(forged).list_products(per_page=1)
    assert_status(record, 401)


def test_response_does_not_leak_secret(client, settings, require_sut):
    """安全：接口响应不得把密码原样回吐。"""
    secret = settings.credential("admin").secret
    record = ProductApi(client).list_products(per_page=1)
    assert secret not in str(record.response_body), "响应体中泄漏了凭据"
