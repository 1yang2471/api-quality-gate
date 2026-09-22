# 被测系统启动与 seed 步骤

> ✅ 已实测通过（2026-09-22，Windows 11 + Docker Desktop 4.91.0 / 引擎 29.8.0）。
> 下面的命令是**实际执行过的版本**，不是候选方案。

## 0. 一键初始化（推荐，本地与 CI 用的是同一份代码）

```powershell
# 起容器 → 装 WordPress/WooCommerce → 设固定链接 → 造商品 → 生成凭据 → 自证路由生效
python scripts/seed_target.py --update-config
```

脚本最后会做一次**自证**：请求 `/wp-json/wc/v3/products` 并检查返回的是 JSON 而不是
首页 HTML。这一步就是防 F-001——固定链接没生效时接口返回 `200 + text/html`，
只断言状态码的测试会把「一个接口都没通」当成「全部正常」。

脚本是幂等的，重复执行不会报错；重装靶子后加 `--update-config` 会把新凭据写回
`config/config.yaml`。

<details>
<summary>下面是等价的**手工步骤**（想理解每一步在做什么时看）</summary>

## 1. 启动容器

```powershell
docker compose -f docker/docker-compose.yml up -d
```

## 2. 初始化 WordPress

```powershell
# 借助 wpcli 容器执行（命令前缀固定）
docker compose -f docker/docker-compose.yml exec wpcli wp core install `
  --url="http://127.0.0.1:8080" --title="QA Gate Store" `
  --admin_user=admin --admin_password=Admin1234! --admin_email=admin@test.local `
  --skip-email
```

## 3. 安装 WooCommerce 并导入样例数据

```powershell
docker compose -f docker/docker-compose.yml exec wpcli wp plugin install woocommerce --activate
docker compose -f docker/docker-compose.yml exec wpcli wp wc tool run install_pages --user=admin
```

### 3.1 必须设置固定链接（否则接口全部失效）

```powershell
docker compose -f docker/docker-compose.yml exec -T wpcli wp rewrite structure '/%postname%/'
docker compose -f docker/docker-compose.yml exec -T wpcli wp rewrite flush
```

**不设这一步的后果**：`/wp-json/wc/v3/...` 会返回 **HTTP 200 + HTML 首页**，
看起来"成功"，实际是路由没生效。详见 `docs/测试发现记录.md` 的 F-001。

### 3.2 造商品数据

```powershell
1..3 | ForEach-Object {
  docker compose -f docker/docker-compose.yml exec -T wpcli `
    wp wc product create --name="QA 测试商品 $_" --type=simple --status=publish `
    --regular_price="$($_ * 10).00" --user=admin --porcelain
}
```

必须保证有至少一个 `status=publish` 且价格正常的商品，否则订单类用例的前置条件不成立
（框架会给出明确提示而不是静默失败）。

## 4. 生成凭据（填入 config/config.yaml）

### 重要：本地 HTTP 环境下不要用 WooCommerce API 密钥

API 密钥（`ck_` / `cs_`）**只在 HTTPS 下生效**，HTTP 下必然 401。
源码依据与实测见 `docs/测试发现记录.md` 的 F-002。
因此在本地与 CI 统一使用 **WordPress 应用密码**。

### 创建客户账号与应用密码

```powershell
# 客户账号（低权限，用于越权用例）
docker compose -f docker/docker-compose.yml exec -T wpcli `
  wp user create customer customer@test.local --role=customer --user_pass='Customer1234!' --porcelain

# 应用密码：admin 与 customer 各一条，输出即为 config.yaml 里的 application_password
docker compose -f docker/docker-compose.yml exec -T wpcli `
  wp user application-password create admin qagate-admin --porcelain
docker compose -f docker/docker-compose.yml exec -T wpcli `
  wp user application-password create customer qagate --porcelain
```

> `scripts/seed_api_keys.php` 用于生成 WooCommerce 官方 API 密钥（HTTPS 环境才可用），
> 本地 HTTP 靶子不适用，保留给将来启用 HTTPS 时使用。

## 5. 直接查数据库（核对数据口径）

WP-CLI 自带的 MariaDB 客户端与 MySQL 8 的 `caching_sha2_password` 不兼容，
会报 `Plugin caching_sha2_password could not be loaded`。
改用 db 容器自带的客户端：

```powershell
docker compose -f docker/docker-compose.yml exec -T db `
  mysql -uroot -prootpass -e "SELECT ID, post_title, post_status FROM wordpress.wp_posts WHERE post_type='product';"
```

## 6. 已实测的坑

- **接口返回 200 但内容是 HTML 首页**：固定链接没设。见 3.1。
- **密钥正确但 401**：HTTP 下密钥认证不生效。见第 4 节。
- **`wp db query` 报认证插件错误**：改用 db 容器的 mysql 客户端。见第 5 节。
- **商品创建后是草稿**：WooCommerce 新建商品默认 `draft`，用例里必须显式指定 `status`。
- **应用密码不可用**：非 HTTPS 环境必须设置 `WP_ENVIRONMENT_TYPE=local`。
- **`wp eval-file` 的路径权限**：wpcli 容器以 uid 33 运行，脚本需放在可读目录（如 `/tmp`）。

</details>
