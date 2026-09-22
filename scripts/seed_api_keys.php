<?php
/**
 * 生成 WooCommerce REST API 密钥（仅用于本地/CI 测试环境）。
 *
 * 用法：
 *   wp eval-file /tmp/seed_api_keys.php <user_login> <permissions> <description>
 *
 * 输出：
 *   consumer_key|consumer_secret
 *
 * 依据（读自容器内 WooCommerce 源码，非猜测）：
 *   includes/class-wc-rest-authentication.php
 *     - get_user_data_by_consumer_key(): consumer_key 以 wc_api_hash() 后的值查询，
 *       因此表中 consumer_key 存的是 sha256 HMAC（64 字符）；
 *     - hash_equals( $this->user->consumer_secret, $consumer_secret ):
 *       consumer_secret 是明文比对，因此表中存原文（cs_ + 40 位十六进制 = 43 字符）。
 *
 * permissions 取值：read / write / read_write
 *   用 read 生成只读密钥，供"越权写入必须被拒绝"的负向用例使用。
 */

$login       = isset( $args[0] ) ? $args[0] : 'admin';
$permissions = isset( $args[1] ) ? $args[1] : 'read_write';
$description = isset( $args[2] ) ? $args[2] : 'qagate';

$user = get_user_by( 'login', $login );
if ( ! $user ) {
	fwrite( STDERR, "找不到用户: {$login}\n" );
	exit( 1 );
}

if ( ! in_array( $permissions, array( 'read', 'write', 'read_write' ), true ) ) {
	fwrite( STDERR, "permissions 只能是 read / write / read_write\n" );
	exit( 1 );
}

$consumer_key    = 'ck_' . bin2hex( random_bytes( 20 ) );
$consumer_secret = 'cs_' . bin2hex( random_bytes( 20 ) );

global $wpdb;

$inserted = $wpdb->insert(
	$wpdb->prefix . 'woocommerce_api_keys',
	array(
		'user_id'         => $user->ID,
		'description'     => $description,
		'permissions'     => $permissions,
		'consumer_key'    => wc_api_hash( $consumer_key ),
		'consumer_secret' => $consumer_secret,
		'truncated_key'   => substr( $consumer_key, -7 ),
	),
	array( '%d', '%s', '%s', '%s', '%s', '%s' )
);

if ( ! $inserted ) {
	fwrite( STDERR, "写入失败: " . $wpdb->last_error . "\n" );
	exit( 1 );
}

echo $consumer_key . '|' . $consumer_secret . "\n";
