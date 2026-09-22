# 接口自动化质量门禁（api-quality-gate）

[![api-regression](https://github.com/1yang2471/api-quality-gate/actions/workflows/api-regression.yml/badge.svg)](https://github.com/1yang2471/api-quality-gate/actions/workflows/api-regression.yml)

面向 Pytest + Requests 的接口自动化框架，并在其上加一层**测试可信度门禁**：
不是"跑绿就算通过"，而是持续回答三个问题——

1. 用例断言的是**真实行为**还是自己的实现？（断言同源检查）
2. 接口路径是**有来源的证据**还是猜的？（路径来源门禁）
3. 用例**真的能抓到缺陷**吗？（变异测试度量断言有效性）

第 3 条是本项目与普通接口框架的分水岭，也是整个项目最值钱的部分。

## 分层结构

```
config/     配置层    环境地址、超时、重试、账号、阈值
core/       公共方法层 请求封装 / 断言封装 / 日志封装 / 配置加载
api/        接口层    一个模块一个文件 + endpoints.py 路径登记表
business/   业务层    把多个接口编排成一条业务流（登录→加购→结算）
tests/      用例层    只做编排与断言，不写裸路径、不写裸 requests
```

用例层调用 business，business 调用 api，api 调用 core，core 读 config。
依赖方向单向，任何一层都不允许反向依赖。

## 路径登记表（本项目的第一条门禁规则）

所有接口路径集中在 `api/endpoints.py`，且每条路径必须标注来源：
`docs`（官方文档）/ `frontend`（前端请求实证）/ `swagger`（自带 OpenAPI）。

这条规则来自真实教训：在真实项目里"凭感觉猜接口路径"曾导致 10 次误测、
撤销 8 条缺陷——路径猜错通常返回 404 或空数据，极易被误判成"功能缺失"。

`tests/test_framework_selfcheck.py::TestEndpointRegistry` 会强制校验：
**存在未标注来源的路径时，测试直接失败。**

## 快速开始

```powershell
# 1. 安装依赖
python -m pip install -r requirements.txt

# 2. 先跑框架自检（不需要被测系统，应当全绿）
python -m pytest tests/test_framework_selfcheck.py -v

# 3. 拉起被测系统（一条命令：容器 + 安装 + 造数据 + 凭据 + 自证路由生效）
python scripts/seed_target.py --update-config

# 4. 跑全部用例
python -m pytest -v
```

被测系统未启动时，真实接口用例会被**跳过**（带明确提示）而不是报错，
这样在没有靶子的环境下也能先验证框架本身。

> 注意：这个"没靶子就跳过"的行为**只在本地允许**。CI 里环境设为 `ci`
> （`skip_when_unreachable: false`）并额外加上 `QAGATE_NO_SKIP=1`：
> 只要出现一次跳过，整次运行就判定失败——否则"容器没起来 → 全部跳过 → 门禁变绿"
> 就是最典型的假绿。

## 环境与凭据（本地 / CI 同一份代码）

| 环境变量 | 作用 |
|---|---|
| `QAGATE_ENV` | 选择环境：`local`（默认）或 `ci` |
| `QAGATE_CONFIG` | 指定配置文件路径（默认 `config/config.yaml`） |
| `QAGATE_NO_SKIP` | 设为 `1` 时，出现任何跳过即判定失败 |
| `QAGATE_ADMIN_APP_PASSWORD` / `QAGATE_CUSTOMER_APP_PASSWORD` | `ci` 环境用它们注入凭据，密码不进仓库 |

真实密码**不进仓库**：仓库里只有 `config/config.yaml.example` 模板，
本地由 `scripts/seed_target.py` 生成后写入被 gitignore 的 `config/config.yaml`；
CI 里由同一个脚本现场生成并注入环境变量。

## 报告

```powershell
# Allure 报告（需先安装 allure 命令行）
python -m pytest --alluredir=allure-results
allure serve allure-results

# 不装 Allure 时的兜底 HTML 报告
python -m pytest --html=report.html --self-contained-html
```

## 用例标记

| 标记 | 含义 |
|---|---|
| `smoke` | 冒烟，发版前必跑 |
| `regression` | 回归，每日跑 |
| `security` | 越权 / 权限 / 认证类 |
| `framework` | 框架自检，不依赖被测系统 |

```powershell
python -m pytest -m smoke            # 只跑冒烟
python -m pytest -m "not framework"  # 只跑真实接口用例
```

## 被测系统

默认指向本地自建的开源电商系统：**WooCommerce（WordPress + MySQL）**，
`http://127.0.0.1:8080`。

选它的原因（也对齐岗位 JD 里的高频业务词）：

| 能力 | 说明 |
|---|---|
| 登录 / 鉴权 | WooCommerce 密钥（HTTP Basic）+ WordPress 应用密码 |
| 订单 / 购物车 | `/wc/v3/orders`，可创建、流转状态、勾选优惠券 |
| 优惠券 | `/wc/v3/coupons`，含 `usage_limit`、`minimum_amount`、有效期等边界规则 |
| 多角色权限 | WordPress 角色体系，可验证低权限角色越权 |
| 数据库 | MySQL，可直接连库核对数据口径 |

**切换被测系统只需要改 `config/config.yaml` 与 `api/endpoints.py` 两个文件**，
用例层与业务层不需要改动——这是分层设计最直接的收益。

### 凭据准备

`config/config.yaml` 里是占位值，启动靶子后需要替换为本地生成的：
`api_keys.admin`（读写密钥）、`api_keys.readonly`（只读密钥）、
`wp_users.customer`（客户角色的应用密码）。

## CI 门禁（L2）

`.github/workflows/api-regression.yml` 两道门：

| 门 | 内容 | 失败意味着 |
|---|---|---|
| 框架自检 | 断言层能正确失败、路径登记表没有未标注来源、配置层契约成立、ruff 静态检查 | 问题在框架自己身上，与靶子无关 |
| 真实接口回归 | 拉起 WooCommerce 靶子 → 造数据 → 生成凭据 → 跑全量用例 → 归档 Allure 结果 | 接口行为变了（或被环境问题挡住） |

第二道门刻意做了两件事来防止"绿得没意义"：靶子不可达时**失败而不是跳过**，
以及出现任何跳过即判失败。

## 后续路线

- **L3 可信度层**：
  - 断言同源检查（禁止用被测模块自己的常量断言自己）
  - 变异测试度量（向业务代码注入变异，统计杀伤率，暴露"跑绿但抓不到 bug"的用例）
  - AI 生成用例的元评测（成功率 / 有效率 / 变异得分变化）
