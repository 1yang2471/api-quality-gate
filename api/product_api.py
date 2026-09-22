"""商品模块接口。路径全部来自 api/endpoints.py，本文件不出现裸路径。"""

from api.base_api import BaseApi
from api.endpoints import Endpoints
from core.http_client import RequestRecord


class ProductApi(BaseApi):
    """商品查询与创建。"""

    def list_products(self, per_page: int = 10, page: int = 1, **filters) -> RequestRecord:
        """商品列表。WooCommerce 返回数组，总数在 X-WP-Total 响应头。

        分页参数是 per_page（1-100）与 page（从 1 开始），与常见的 limit/offset
        不同——这是切换被测系统时必须逐条核对的部分。
        """
        params = {"per_page": per_page, "page": page, **filters}
        return self.client.get(Endpoints.PRODUCT_LIST.url(), params=params)

    def get_product(self, product_id: int) -> RequestRecord:
        """商品详情。"""
        return self.client.get(Endpoints.PRODUCT_DETAIL.url(product_id=product_id))

    def get_missing_product(self, product_id: int = 999_999_999) -> RequestRecord:
        """负向用例：查询不存在的商品，必须 404 而不是返回空对象。"""
        return self.get_product(product_id)

    def create_product(self, name: str, price: str = "9.90", status: str = "publish") -> RequestRecord:
        """创建商品。注意：WooCommerce 新建商品默认是 draft，必须显式指定 status。"""
        return self.client.post(
            Endpoints.PRODUCT_LIST.url(),
            json={
                "name": name,
                "type": "simple",
                "status": status,
                "regular_price": price,
            },
        )

    def delete_product(self, product_id: int, force: bool = True) -> RequestRecord:
        """删除商品（force=true 才真正删除）。用于用例清理。"""
        return self.client.delete(
            Endpoints.PRODUCT_DETAIL.url(product_id=product_id),
            params={"force": str(force).lower()},
        )
