"""被测靶子一键初始化：WordPress 安装 → WooCommerce → 固定链接 → 商品 → 凭据。

为什么是 Python 而不是 bash / PowerShell：
  本地是 Windows、CI 是 Linux，而这段初始化逻辑必须**两边跑的是同一份代码**，
  否则就会出现"本地能起、CI 起不来"的漂移——那正是本项目最反对的
  「环境相关、无法复现的结论」。Python 是唯一的跨平台选择，且不用额外装工具。

设计要点：
  1. 幂等：重复执行不会报错（已安装的跳过、已有商品的补齐）；
  2. 自证：结束后做一次「路由是否真的生效」检查——直接防住 F-001
     （固定链接没设时 /wp-json/... 返回 200 + HTML 首页，看着像成功其实是全错）；
  3. 凭据不落仓库：应用密码写进 ci-credentials.env，CI 再注入环境变量。

用法：
    python scripts/seed_target.py                    # 初始化并输出凭据文件
    python scripts/seed_target.py --update-config    # 顺带把本地 config.yaml 的凭据填好
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMPOSE_FILE = ROOT / "docker" / "docker-compose.yml"
DEFAULT_CREDENTIALS_FILE = ROOT / "ci-credentials.env"
EXAMPLE_CONFIG = ROOT / "config" / "config.yaml.example"
DEFAULT_CONFIG = ROOT / "config" / "config.yaml"

ADMIN_USER = "admin"
ADMIN_PASSWORD = "Admin1234!"
ADMIN_EMAIL = "admin@test.local"
CUSTOMER_USER = "customer"
CUSTOMER_EMAIL = "customer@test.local"
CUSTOMER_PASSWORD = "Customer1234!"
PRODUCT_COUNT = 3

# 模板里的占位文案，--update-config 时按这个精确替换（不解析 YAML，避免注释被丢掉）
PLACEHOLDER_ADMIN = "请替换为 seed_target.py 生成的 admin 应用密码"
PLACEHOLDER_CUSTOMER = "请替换为 seed_target.py 生成的 customer 应用密码"


class SeedError(RuntimeError):
    """初始化过程中的可读错误。"""


class TargetSeeder:
    """把靶子从零初始化到"可以直接跑回归"的状态。"""

    def __init__(self, compose_file: Path, site_url: str, wait_seconds: int = 300):
        self.compose_file = Path(compose_file)
        self.site_url = site_url.rstrip("/")
        self.wait_seconds = wait_seconds

    # ── 底层命令 ──────────────────────────────────────────────────────
    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """执行命令并回显；失败时把 stdout/stderr 一起抛出来，方便定位。"""
        display = " ".join(args)
        print(f"  $ {display}", flush=True)
        result = subprocess.run(
            args,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        if check and result.returncode != 0:
            raise SeedError(
                f"命令失败（退出码 {result.returncode}）：{display}\n"
                f"stdout: {result.stdout.strip()}\n"
                f"stderr: {result.stderr.strip()}"
            )
        return result

    def compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """调用 docker compose。"""
        return self._run(
            ["docker", "compose", "-f", str(self.compose_file), *args], check=check
        )

    def wp(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """在 wpcli 容器里执行 WP-CLI。"""
        return self.compose("exec", "-T", "wpcli", "wp", *args, check=check)

    def wp_out(self, *args: str, check: bool = True) -> str:
        """执行 WP-CLI 并返回去掉首尾空白的标准输出。"""
        return self.wp(*args, check=check).stdout.strip()

    # ── 步骤 0：启动与等待 ────────────────────────────────────────────
    def start_stack(self) -> None:
        print("[1/8] 启动靶子容器（MySQL + WordPress + WP-CLI）", flush=True)
        self.compose("up", "-d", "--wait", "--wait-timeout", "300")

    def wait_for_wordpress_files(self) -> None:
        """等 WordPress 容器把 wp-config.php 写出来，否则 wp 命令无从下手。"""
        print("[2/8] 等待 WordPress 初始化（wp-config.php 生成）", flush=True)
        deadline = time.time() + self.wait_seconds
        while time.time() < deadline:
            probe = self.compose(
                "exec", "-T", "wpcli", "test", "-f", "/var/www/html/wp-config.php",
                check=False,
            )
            if probe.returncode == 0:
                print("      wp-config.php 已生成", flush=True)
                return
            time.sleep(3)
        raise SeedError(
            "等待 wp-config.php 超时：WordPress 容器可能没起来，"
            "用 `docker compose -f docker/docker-compose.yml logs wordpress` 看日志"
        )

    # ── 步骤 1：安装 WordPress ────────────────────────────────────────
    def install_wordpress(self) -> None:
        print("[3/8] 安装 WordPress", flush=True)
        if self.wp("core", "is-installed", check=False).returncode == 0:
            print("      已安装过，跳过", flush=True)
            return

        # 数据库刚就绪时 install 可能失败，重试到超时为止
        deadline = time.time() + self.wait_seconds
        last_error = ""
        while time.time() < deadline:
            result = self.wp(
                "core", "install",
                f"--url={self.site_url}",
                "--title=QA Gate Store",
                f"--admin_user={ADMIN_USER}",
                f"--admin_password={ADMIN_PASSWORD}",
                f"--admin_email={ADMIN_EMAIL}",
                "--skip-email",
                check=False,
            )
            if result.returncode == 0:
                return
            last_error = (result.stdout + result.stderr).strip()
            time.sleep(5)
        raise SeedError(f"WordPress 安装超时，最后一次错误：{last_error}")

    # ── 步骤 2：装插件与页面 ──────────────────────────────────────────
    def install_woocommerce(self) -> None:
        print("[4/8] 安装并启用 WooCommerce", flush=True)
        if self.wp("plugin", "is-active", "woocommerce", check=False).returncode == 0:
            print("      已启用，跳过安装", flush=True)
        else:
            self.wp("plugin", "install", "woocommerce", "--activate")
        self.wp("wc", "tool", "run", "install_pages", f"--user={ADMIN_USER}")

    def fix_permalinks(self) -> None:
        """设置固定链接：本地实测证明不做这一步，所有 /wp-json/ 路径都不可用。"""
        print("[5/8] 设置固定链接（缺这一步接口会返回 200 + HTML 首页）", flush=True)
        self.wp("rewrite", "structure", "/%postname%/", "--hard")
        self.wp("rewrite", "flush", "--hard")

    # ── 步骤 3：测试数据 ──────────────────────────────────────────────
    def seed_products(self) -> None:
        print("[6/8] 造测试数据（商品 / 客户账号）", flush=True)
        published = int(
            self.wp_out(
                "post", "list", "--post_type=product", "--post_status=publish",
                "--format=count",
            )
            or 0
        )
        for index in range(published + 1, PRODUCT_COUNT + 1):
            self.wp(
                "wc", "product", "create",
                f"--name=QA 测试商品 {index}",
                "--type=simple",
                "--status=publish",
                f"--regular_price={index * 10:.2f}",
                f"--user={ADMIN_USER}",
                "--porcelain",
            )
        total = int(
            self.wp_out(
                "post", "list", "--post_type=product", "--post_status=publish",
                "--format=count",
            )
            or 0
        )
        if total < 1:
            raise SeedError("靶子没有已上架商品，订单类用例的前提不成立")
        print(f"      已上架商品：{total} 件", flush=True)

        if self.wp("user", "get", CUSTOMER_USER, check=False).returncode != 0:
            self.wp(
                "user", "create", CUSTOMER_USER, CUSTOMER_EMAIL,
                "--role=customer",
                f"--user_pass={CUSTOMER_PASSWORD}",
                "--porcelain",
            )
            print(f"      已创建低权限账号：{CUSTOMER_USER}", flush=True)
        else:
            print(f"      低权限账号已存在：{CUSTOMER_USER}", flush=True)

    # ── 步骤 4：凭据 ──────────────────────────────────────────────────
    def create_credentials(self) -> dict:
        """生成应用密码：HTTP 下 WooCommerce 密钥必然 401，只能用应用密码（F-002）。"""
        print("[7/8] 生成应用密码（HTTP 环境下唯一可用的鉴权方式）", flush=True)
        secrets = {}
        for user, label in ((ADMIN_USER, "qagate-admin"), (CUSTOMER_USER, "qagate")):
            # 清掉旧密码，保证重复执行不会越攒越多
            self.wp("user", "application-password", "delete", user, "--all", check=False)
            secret = self.wp_out(
                "user", "application-password", "create", user, label, "--porcelain"
            )
            if not secret:
                raise SeedError(f"为用户 {user} 生成应用密码失败")
            secrets[user] = secret
        return secrets

    # ── 步骤 5：自证 ──────────────────────────────────────────────────
    @staticmethod
    def http_probe(url: str, timeout: float = 10.0) -> tuple[int, str]:
        """返回 (状态码, Content-Type)；连不上返回 (0, "")。"""
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "qagate-seed/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:  # 401/403 也是有效响应，不是"不可达"
            return exc.code, exc.headers.get("Content-Type", "")
        except Exception:  # noqa: BLE001 - 探活失败按不可达处理
            return 0, ""

    def verify(self) -> None:
        """自证：接口路由真的生效了吗？

        这是防 F-001 的门闩——固定链接没设时，这个请求会返回
        **200 + text/html 的首页**，只断言状态码的测试会把"没通"当成"通了"。
        """
        print("[8/8] 自证：确认接口路由真的生效（而不是返回首页 HTML）", flush=True)
        deadline = time.time() + 60
        last = ""
        while time.time() < deadline:
            code, content_type = self.http_probe(f"{self.site_url}/wp-json/wc/v3/products")
            last = f"HTTP {code} / Content-Type: {content_type or '(无)'}"
            if code in (200, 401, 403) and "application/json" in content_type.lower():
                print(f"      通过：{last}", flush=True)
                return
            time.sleep(3)
        raise SeedError(
            f"接口路由未生效：{last}\n"
            "常见原因：固定链接没设置（见 F-001），或 WordPress 还没就绪。"
        )

    # ── 编排 ──────────────────────────────────────────────────────────
    def run(self) -> dict:
        self.start_stack()
        self.wait_for_wordpress_files()
        self.install_wordpress()
        self.install_woocommerce()
        self.fix_permalinks()
        self.seed_products()
        secrets = self.create_credentials()
        self.verify()
        return secrets


def write_credentials_file(path: Path, secrets: dict) -> None:
    """写成 KEY=VALUE，CI 直接 cat 到 $GITHUB_ENV 即可。"""
    lines = [
        "# 由 scripts/seed_target.py 生成，仅用于本地/CI 的一次性靶子，不要提交",
        f"QAGATE_ADMIN_APP_PASSWORD={secrets[ADMIN_USER]}",
        f"QAGATE_CUSTOMER_APP_PASSWORD={secrets[CUSTOMER_USER]}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_local_config(secrets: dict, config_path: Path) -> None:
    """把应用密码填进本地 config.yaml：以模板为底，保证不残留旧密码。"""
    text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    text = text.replace(PLACEHOLDER_ADMIN, secrets[ADMIN_USER])
    text = text.replace(PLACEHOLDER_CUSTOMER, secrets[CUSTOMER_USER])
    config_path.write_text(text, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化 WooCommerce 靶子并生成凭据")
    parser.add_argument(
        "--compose-file", default=str(DEFAULT_COMPOSE_FILE), help="docker-compose 文件路径"
    )
    parser.add_argument(
        "--site-url",
        default="http://127.0.0.1:8080",
        help="靶子地址，必须与用例配置里的 base_url 一致",
    )
    parser.add_argument(
        "--credentials-file",
        default=str(DEFAULT_CREDENTIALS_FILE),
        help="凭据输出文件（KEY=VALUE 格式）",
    )
    parser.add_argument(
        "--update-config",
        action="store_true",
        help="同时用模板重建 config/config.yaml 并填入新凭据（会覆盖本地配置）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    seeder = TargetSeeder(Path(args.compose_file), args.site_url)
    try:
        secrets = seeder.run()
    except SeedError as exc:
        print(f"\n[失败] {exc}", file=sys.stderr)
        return 1

    credentials_file = Path(args.credentials_file)
    write_credentials_file(credentials_file, secrets)
    if args.update_config:
        update_local_config(secrets, DEFAULT_CONFIG)

    print("\n[完成] 靶子已就绪")
    print(f"  地址: {args.site_url}")
    print(f"  admin 应用密码: {secrets[ADMIN_USER]}")
    print(f"  customer 应用密码: {secrets[CUSTOMER_USER]}")
    print(f"  凭据文件: {credentials_file}")
    if args.update_config:
        print(f"  已写入: {DEFAULT_CONFIG}")
    else:
        print("  下一步: 把上面两个密码填进 config/config.yaml，或加 --update-config 自动写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
