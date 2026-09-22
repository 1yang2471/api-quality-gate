"""订单模块接口。"""

from api.base_api import BaseApi
from api.endpoints import Endpoints
from core.http_client import RequestRecord


class OrderApi(BaseApi):
    """订单的创建、查询、状态流转、删除。"""

    def list_orders(self, per_page: int = 10, page: int = 1, **filters) -> RequestRecord:
        """订单列表。"""
        params = {"per_page": per_page, "page": page, **filters}
        return self.client.get(Endpoints.ORDER_LIST.url(), params=params)

    def get_order(self, order_id: int) -> RequestRecord:
        """订单详情。"""
        return self.client.get(Endpoints.ORDER_DETAIL.url(order_id=order_id))

    def create_order(
        self,
        product_id: int,
        quantity: int = 1,
        coupon_code: str | None = None,
        set_paid: bool = True,
        **extra,
    ) -> RequestRecord:
        """创建订单。coupon_code 非空时带上优惠券，用于折扣金额勾稽。"""
        payload = {
            "payment_method": "bacs",
            "payment_method_title": "Direct Bank Transfer",
            "set_paid": set_paid,
            "line_items": [{"product_id": product_id, "quantity": quantity}],
            **extra,
        }
        if coupon_code:
            payload["coupon_lines"] = [{"code": coupon_code}]
        return self.client.post(Endpoints.ORDER_CREATE.url(), json=payload)

    def update_status(self, order_id: int, status: str) -> RequestRecord:
        """更新订单状态，用于状态机流转用例。"""
        return self.client.put(
            Endpoints.ORDER_UPDATE.url(order_id=order_id),
            json={"status": status},
        )

    def delete_order(self, order_id: int, force: bool = True) -> RequestRecord:
        """删除订单，用于用例清理。"""
        return self.client.delete(
            Endpoints.ORDER_DETAIL.url(order_id=order_id),
            params={"force": str(force).lower()},
        )
