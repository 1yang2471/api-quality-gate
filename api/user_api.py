"""身份与权限相关接口（WordPress 核心 REST，走应用密码鉴权）。

用途：验证"低权限角色不得访问高权限资源"，这是权限类缺陷最典型的形态。
"""

from api.base_api import BaseApi
from api.endpoints import Endpoints
from core.http_client import RequestRecord


class UserApi(BaseApi):
    """当前身份查询与用户列表访问。"""

    def me(self) -> RequestRecord:
        """查询当前凭据对应的身份。"""
        return self.client.get(Endpoints.WP_ME.url())

    def me_with(self, credential) -> RequestRecord:
        """用指定凭据查询当前身份（单次请求覆盖鉴权）。"""
        return self.client.get(Endpoints.WP_ME.url(), auth=credential.basic_auth)

    def list_users(self) -> RequestRecord:
        """列出用户——低权限角色访问必须被拒绝。"""
        return self.client.get(Endpoints.WP_USERS.url())
