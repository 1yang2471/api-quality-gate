"""业务层：把多个接口调用编排成一条完整业务流，并负责测试数据清理。

业务流：创建优惠券 → 创建订单（带券）→ 读回订单 → 勾稽金额 → 清理数据。
用例层只调用这里的方法，不关心底层调了哪些接口、按什么顺序。
"""

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

from api.coupon_api import CouponApi
from api.order_api import OrderApi
from api.product_api import ProductApi
from core.config_loader import Settings
from core.http_client import HttpClient
from core.logger import get_logger

logger = get_logger()


@dataclass
class OrderContext:
    """一条下单链路产生的关键数据。"""

    product_id: int
    coupon_id: int | None = None
    coupon_code: str | None = None
    order_id: int | None = None
    order: dict = field(default_factory=dict)


class TestDataTracker:
    """测试数据清理器：按登记的清理动作逆序执行。

    为什么要有它（面试可讲）：
      自动化用例必须能重复运行。如果每次跑都留下订单和优惠券，
      第二次跑就会因为数据污染而失败——这类"跑第二遍才挂"的问题
      是自动化最常见的坑之一。
      清理必须放在 finally 里，即使断言失败也要执行。
    """

    def __init__(self) -> None:
        self._actions: list[tuple[str, Callable[[], object]]] = []

    def track(self, label: str, action: Callable[[], object]) -> None:
        """登记一个清理动作。"""
        self._actions.append((label, action))

    def cleanup(self) -> list[str]:
        """执行全部清理动作，返回失败项说明。逆序执行以尊重依赖关系。"""
        failures: list[str] = []
        for label, action in reversed(self._actions):
            try:
                action()
            except Exception as exc:
                # 刻意捕获所有异常：清理失败不应该掩盖用例本身的结论
                failures.append(f"{label}: {exc}")
                logger.warning("清理失败 %s: %s", label, exc)
        self._actions.clear()
        return failures


class ShopFlow:
    """店铺业务流编排。"""

    def __init__(self, client: HttpClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.products = ProductApi(client)
        self.coupons = CouponApi(client)
        self.orders = OrderApi(client)

    # ── 数据构造 ──
    def require_published_product(self) -> int:
        """取一个已上架商品的 id，作为下单对象。"""
        record = self.products.list_products(per_page=1, status="publish")
        items = record.response_body
        if not isinstance(items, list) or not items:
            raise AssertionError(
                f"被测站点没有已上架商品，用例前提不成立：{record.summary}"
            )
        return int(items[0]["id"])

    def create_coupon(self, tracker: TestDataTracker, percent: str = "10", **extra) -> dict:
        """创建一张百分比优惠券，并登记清理。"""
        code = f"QAGATE{_unique_suffix()}"
        record = self.coupons.create_coupon(code=code, amount=percent, **extra)
        if record.status_code not in (200, 201):
            raise AssertionError(f"创建优惠券失败：{record.summary} / {record.response_body}")
        coupon = record.response_body
        tracker.track(
            f"删除优惠券 {coupon.get('id')}",
            lambda: self.coupons.delete_coupon(int(coupon["id"])),
        )
        return coupon

    def create_order(
        self,
        tracker: TestDataTracker,
        product_id: int,
        coupon_code: str | None = None,
        quantity: int = 1,
    ) -> dict:
        """创建订单，并登记清理。"""
        record = self.orders.create_order(
            product_id=product_id, quantity=quantity, coupon_code=coupon_code
        )
        if record.status_code not in (200, 201):
            raise AssertionError(f"创建订单失败：{record.summary} / {record.response_body}")
        order = record.response_body
        tracker.track(
            f"删除订单 {order.get('id')}",
            lambda: self.orders.delete_order(int(order["id"])),
        )
        return order

    # ── 金额勾稽 ──
    @staticmethod
    def line_items_subtotal(order: dict) -> float:
        """行项目原价小计之和（未扣折扣）。"""
        return round(sum(float(item["subtotal"]) for item in order.get("line_items", [])), 2)

    @staticmethod
    def line_items_total(order: dict) -> float:
        """行项目折后小计之和。"""
        return round(sum(float(item["total"]) for item in order.get("line_items", [])), 2)

    @staticmethod
    def discount_total(order: dict) -> float:
        """订单折扣合计。"""
        return float(order.get("discount_total") or 0)

    def assert_discount_matches_line_items(self, order: dict) -> None:
        """勾稽一：订单折扣合计必须等于「行项目原价小计 − 折后小计」。

        这是定义级关系，与税率设置无关，因此在任何配置下都必须成立。
        真实项目里"汇总口径与明细不一致"是高频缺陷（实习中遇到过统计卡
        口径与列表条数对不上），所以这条是必测项。

        实测修正（2026-09-22）：订单对象**没有**顶层 subtotal 字段，
        原价与折后金额都在 line_items 里，因此勾稽必须基于行项目计算。
        """
        expected = round(
            self.line_items_subtotal(order) - self.line_items_total(order), 2
        )
        actual = self.discount_total(order)
        if abs(expected - actual) > self.settings.amount_tolerance:
            raise AssertionError(
                f"折扣合计与行项目差额不一致：折扣={actual} 行项目差额={expected}"
            )

    def assert_total_composition(self, order: dict) -> None:
        """勾稽二：订单总额 == 行项目折后合计 + 运费 + 税额。

        该公式在有税场景下依赖站点的含税设置（prices_include_tax）。
        已核实本地靶子为 prices_include_tax=False，故按不含税口径断言；
        切换到其它站点前必须重新核实该设置，否则断言不成立。
        """
        if order.get("prices_include_tax"):
            return  # 含税站点口径不同，跳过以免给出错误结论
        expected = round(
            self.line_items_total(order)
            + float(order.get("shipping_total") or 0)
            + float(order.get("total_tax") or 0),
            2,
        )
        actual = float(order["total"])
        if abs(expected - actual) > self.settings.amount_tolerance:
            raise AssertionError(
                f"订单总额与分项之和不一致：总额={actual} 分项合计={expected}"
            )

    def assert_total_not_negative(self, order: dict) -> None:
        """边界：订单总额不得为负。"""
        total = float(order["total"])
        if total < 0:
            raise AssertionError(f"订单总额为负：{total}")


@contextmanager
def temporary_test_data(flow: ShopFlow):
    """上下文管理器：退出时（含异常）一定会清理测试数据。"""
    tracker = TestDataTracker()
    try:
        yield tracker
    finally:
        failures = tracker.cleanup()
        if failures:
            logger.warning("存在未清理干净的数据：%s", failures)


def _unique_suffix() -> str:
    """生成短随机后缀，保证优惠券码在重复运行时也不冲突。"""
    import uuid

    return uuid.uuid4().hex[:8].upper()
