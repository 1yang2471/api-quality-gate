"""接口路径登记表 —— 全项目唯一允许出现路径字符串的地方。

⚠️ 待核实（重要）：
  下列路径取自 WooCommerce REST API v3 官方文档（source="docs"），
  但**尚未用运行中的站点逐条实跑核对**。
  按本文件自己的规则，"声明为 docs"不等于"已核实"。启动靶子后必须：
    1. 用真实请求逐条比对路径与响应结构；
    2. 核对通过后把 note 补上核实日期与文档链接。
  核对之前，这些路径只能视为"候选路径"，不能作为缺陷结论的依据。

为什么要有这个文件（这条规则来自真实项目教训）：
  在真实项目里，"凭感觉猜接口路径"曾导致 10 次误测、撤销 8 条缺陷。
  路径猜错通常返回 404 或空数据，极易被误判成"功能缺失"。
  本框架强制：任何路径都必须在这里登记并写明来源，没有来源的路径不允许进用例。

来源（source）取值：
  - "docs"    ：被测系统官方 API 文档
  - "frontend"：前端 JS / 网络请求抓包实证
  - "swagger" ：被测系统自带的 OpenAPI 文档
  其它来源一律视为未验证。

切换被测系统时，只需要改这一个文件 + config/config.yaml。
"""

from dataclasses import dataclass

VALID_SOURCES = {"docs", "frontend", "swagger"}


@dataclass(frozen=True)
class Endpoint:
    """一个接口路径及其证据来源。"""

    path: str
    source: str
    note: str = ""

    def url(self, **params) -> str:
        """填充路径参数，例如 url(product_id=93)。"""
        return self.path.format(**params)


class Endpoints:
    """按模块组织的路径登记表（WooCommerce REST API v3）。"""

    # ── 探活 ──
    SITE_ROOT = Endpoint("/wp-json/", "docs", "WP REST 根，无需鉴权")

    # ── 身份 / 越权（WordPress 核心 REST，走应用密码）──
    WP_ME = Endpoint("/wp-json/wp/v2/users/me", "docs", "应用密码鉴权")
    WP_USERS = Endpoint("/wp-json/wp/v2/users", "docs", "仅具备相应权限的角色可访问")

    # ── 商品 ──
    PRODUCT_LIST = Endpoint(
        "/wp-json/wc/v3/products", "docs", "列表返回数组，总数在 X-WP-Total 响应头"
    )
    PRODUCT_DETAIL = Endpoint("/wp-json/wc/v3/products/{product_id}", "docs")

    # ── 优惠券 ──
    COUPON_LIST = Endpoint("/wp-json/wc/v3/coupons", "docs")
    COUPON_CREATE = Endpoint("/wp-json/wc/v3/coupons", "docs")
    COUPON_DETAIL = Endpoint("/wp-json/wc/v3/coupons/{coupon_id}", "docs")
    COUPON_DELETE = Endpoint("/wp-json/wc/v3/coupons/{coupon_id}", "docs", "force=true 才真删")

    # ── 订单 ──
    ORDER_LIST = Endpoint("/wp-json/wc/v3/orders", "docs")
    ORDER_CREATE = Endpoint("/wp-json/wc/v3/orders", "docs")
    ORDER_DETAIL = Endpoint("/wp-json/wc/v3/orders/{order_id}", "docs")
    ORDER_UPDATE = Endpoint("/wp-json/wc/v3/orders/{order_id}", "docs")
    ORDER_DELETE = Endpoint("/wp-json/wc/v3/orders/{order_id}", "docs")

    # ── 客户 ──
    CUSTOMER_LIST = Endpoint("/wp-json/wc/v3/customers", "docs")
    CUSTOMER_DETAIL = Endpoint("/wp-json/wc/v3/customers/{customer_id}", "docs")

    @classmethod
    def all(cls) -> list[Endpoint]:
        """列出全部登记路径，供门禁脚本校验来源完整性。"""
        return [value for value in vars(cls).values() if isinstance(value, Endpoint)]

    @classmethod
    def unverified(cls) -> list[Endpoint]:
        """列出未标注有效来源的路径——这些不允许进入用例。"""
        return [ep for ep in cls.all() if ep.source not in VALID_SOURCES]
