"""配置层加载器：把 config.yaml 读成对象，集中提供地址 / 超时 / 凭据 / 阈值。

三个刻意的设计（都是为"证据可信"服务的）：
  1. 环境可切换：QAGATE_ENV 可覆盖 yaml 里的 env，本地跑 local、门禁里跑 ci；
  2. 凭据可注入：配置值支持 ${VAR} 占位，真实密码从环境变量来，不进仓库；
  3. 缺口可暴露：ci 环境把 skip_when_unreachable 设为 false，
     靶子没起来时用例直接失败，杜绝"全部跳过 → 门禁假绿"。
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG = ROOT / "config" / "config.yaml.example"

# ${VAR} 形式的占位符，用于从环境变量注入凭据
_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 模板里的占位文案：命中说明配置没被真正填过，要早失败而不是让人对着 401 猜
_PLACEHOLDER_HINTS = ("请替换", "替换为", "<", "example")


def _expand_env(value):
    """递归展开配置里的 ${VAR}；变量缺失时直接报错，避免静默用空凭据。"""
    if isinstance(value, str):

        def _replace(match: re.Match) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise KeyError(
                    f"配置引用了未设置的环境变量 {name}，"
                    "请先 export（或写入 CI 的环境变量）后再运行"
                )
            return os.environ[name]

        return _ENV_PLACEHOLDER.sub(_replace, value)
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


@dataclass
class EnvConfig:
    """单个环境的连接参数。"""

    name: str
    base_url: str
    timeout: int
    retries: int
    skip_when_unreachable: bool
    health_path: str


@dataclass
class Credential:
    """一组 HTTP Basic 凭据（当前为 WordPress 应用密码）。"""

    name: str
    username: str
    secret: str

    @property
    def basic_auth(self) -> tuple[str, str]:
        """可直接传给 requests 的 auth 参数。"""
        return (self.username, self.secret)


@dataclass
class Settings:
    """整份配置的入口对象。"""

    env: EnvConfig
    credentials: dict
    thresholds: dict

    def credential(self, role: str) -> Credential:
        """按角色取凭据；缺失时直接报错，避免用例静默用错账号。"""
        if role not in self.credentials:
            raise KeyError(f"配置中不存在凭据角色: {role}")
        item = self.credentials[role]
        secret = str(item.get("application_password", "")).strip()
        if not secret or any(hint in secret for hint in _PLACEHOLDER_HINTS):
            raise ValueError(
                f"凭据 {role} 还是模板占位值：{secret!r}。\n"
                "请先运行 python scripts/seed_target.py 生成应用密码，"
                "再把输出写入 config/config.yaml（或导出环境变量）。"
            )
        return Credential(role, item["username"], item["application_password"])

    @property
    def amount_tolerance(self) -> float:
        """金额比对容差（元）。"""
        return float(self.thresholds.get("amount_tolerance", 0.02))


def load_settings(config_path: Path | str | None = None, env: str | None = None) -> Settings:
    """加载配置。

    配置文件优先级：入参 > QAGATE_CONFIG 环境变量 > config/config.yaml。
    环境优先级：入参 > QAGATE_ENV 环境变量 > yaml 里的 `env` 字段。
    """
    path = Path(config_path or os.environ.get("QAGATE_CONFIG") or DEFAULT_CONFIG)
    if not path.exists():
        raise FileNotFoundError(
            f"未找到配置文件：{path}\n"
            f"请先复制模板：{EXAMPLE_CONFIG}\n"
            "  Windows: copy config\\config.yaml.example config\\config.yaml\n"
            "  Linux/macOS: cp config/config.yaml.example config/config.yaml"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    env_name = env or os.environ.get("QAGATE_ENV") or raw["env"]
    if env_name not in raw["environments"]:
        raise KeyError(f"配置中不存在环境: {env_name}")
    # 只展开"本次真正用到"的部分：未被选中的环境里留着的 ${VAR} 占位
    # 不应该让本地运行整体挂掉（配置文件是本地/CI 共用的一份）
    env_raw = _expand_env(raw["environments"][env_name])
    credentials_raw = env_raw.get("credentials") or _expand_env(
        raw.get("credentials", {})
    )

    return Settings(
        env=EnvConfig(
            name=env_name,
            base_url=env_raw["base_url"].rstrip("/"),
            timeout=int(env_raw.get("timeout", 10)),
            retries=int(env_raw.get("retries", 0)),
            skip_when_unreachable=bool(env_raw.get("skip_when_unreachable", True)),
            health_path=env_raw.get("health_path", "/wp-json/"),
        ),
        # 环境块里的 credentials 优先，便于 ci 环境整体换成环境变量注入
        credentials=credentials_raw,
        thresholds=raw.get("thresholds", {}),
    )
