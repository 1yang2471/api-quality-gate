"""优惠券模块接口。优惠券有大量边界规则，是很好的等价类/边界值练习对象。"""

from api.base_api import BaseApi
from api.endpoints import Endpoints
from core.http_client import RequestRecord


class CouponApi(BaseApi):
    """优惠券的增删查。"""

    def list_coupons(self, per_page: int = 10, page: int = 1, **filters) -> RequestRecord:
        """优惠券列表。"""
        params = {"per_page": per_page, "page": page, **filters}
        return self.client.get(Endpoints.COUPON_LIST.url(), params=params)

    def get_coupon(self, coupon_id: int) -> RequestRecord:
        """优惠券详情。"""
        return self.client.get(Endpoints.COUPON_DETAIL.url(coupon_id=coupon_id))

    def create_coupon(
        self,
        code: str,
        discount_type: str = "percent",
        amount: str = "10",
        **extra,
    ) -> RequestRecord:
        """创建优惠券。extra 透传边界字段（usage_limit / minimum_amount 等）。"""
        payload = {
            "code": code,
            "discount_type": discount_type,
            "amount": amount,
            **extra,
        }
        return self.client.post(Endpoints.COUPON_CREATE.url(), json=payload)

    def delete_coupon(self, coupon_id: int, force: bool = True) -> RequestRecord:
        """删除优惠券，用于用例清理。"""
        return self.client.delete(
            Endpoints.COUPON_DETAIL.url(coupon_id=coupon_id),
            params={"force": str(force).lower()},
        )
