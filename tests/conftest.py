"""用例层夹具：统一提供配置、客户端、业务流与"被测系统可用性"判定。

关键设计：被测系统没启动时，用例是 **skip** 而不是 fail。
这样框架本身可以在没有靶子的情况下先跑绿（见 test_framework_selfcheck.py），
靶子起来后再跑真实用例。

但门禁里不允许这样：QAGATE_ENV=ci 时配置把 skip_when_unreachable 设为 false，
再加上 QAGATE_NO_SKIP=1 这道防线——**只要出现一条 skip，整次运行就判定失败**。
原因是"全部跳过 + 退出码 0"是最典型的假绿：CI 显示成功，实际一条接口都没测。
"""

import os
import sys
from pathlib import Path

import pytest

# 让 tests/ 下的用例能 import 到 core / api / business
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from business.order_flow import ShopFlow  # noqa: E402
from core.config_loader import Settings, load_settings  # noqa: E402
from core.http_client import HttpClient  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    """整份配置，session 级复用。QAGATE_ENV 可覆盖（本地 local / 门禁 ci）。"""
    return load_settings(env=os.environ.get("QAGATE_ENV") or None)


def pytest_sessionfinish(session, exitstatus) -> None:
    """门禁不变量：QAGATE_NO_SKIP=1 时，出现任何跳过都算失败。"""
    if os.environ.get("QAGATE_NO_SKIP") != "1":
        return
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    if reporter is None:
        return
    skipped = len(reporter.stats.get("skipped", []))
    if skipped:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter.write_line(
            f"\n[门禁失败] 本次运行有 {skipped} 条用例被跳过，"
            "说明靶子没有被真正测到（典型假绿），本次门禁判定为失败。",
            red=True,
        )


@pytest.fixture(scope="session")
def client(settings: Settings) -> HttpClient:
    """管理员凭据的 HTTP 客户端，主流程使用。"""
    http = HttpClient(settings.env)
    http.use_basic_auth(settings.credential("admin"))
    return http


@pytest.fixture(scope="session")
def customer_client(settings: Settings) -> HttpClient:
    """低权限（客户角色）客户端，用于越权负向用例。"""
    http = HttpClient(settings.env)
    http.use_basic_auth(settings.credential("customer"))
    return http


@pytest.fixture(scope="session")
def sut_alive(client: HttpClient) -> bool:
    """探测被测系统；不可用时返回 False，由 require_sut 决定是否跳过。"""
    return client.is_alive()


@pytest.fixture
def require_sut(sut_alive: bool, settings: Settings) -> None:
    """真实接口用例统一依赖此夹具。"""
    if not sut_alive:
        if settings.env.skip_when_unreachable:
            pytest.skip(
                f"被测系统 {settings.env.base_url} 不可达，跳过真实接口用例"
                "（启动靶子后重跑即可）"
            )
        pytest.fail(f"被测系统 {settings.env.base_url} 不可达，且配置禁止跳过")


@pytest.fixture
def flow(client: HttpClient, settings: Settings) -> ShopFlow:
    """店铺业务流编排器。"""
    return ShopFlow(client, settings)
