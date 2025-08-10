import json
import mysql.connector
import redis
import requests
import os
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

try:
    primary_cache = redis.Redis(
        host=os.environ['VALKEY_PRIMARY_ENDPOINT'],
        port=6379,
        decode_responses=True,
        ssl=True,
        username=os.environ['VALKEY_USER_NAME'],
        password=os.environ['VALKEY_PASSWORD']
    )
except redis.RedisError as e:
    logger.error(f"Failed to initialize Valkey connection: {e}")
    raise

# Biến toàn cục để lưu token và thời gian hết hạn
cached_token = None
token_expiry = 0

def get_db_token():
    global cached_token, token_expiry
    current_time = time.time()

    # Kiểm tra nếu token còn hợp lệ (giả sử TTL là 840 giây để có buffer 60 giây trước khi hết hạn)
    if cached_token and current_time < token_expiry:
        return cached_token

    # Tạo token mới
    rds_client = boto3.client('rds')
    cached_token = rds_client.generate_db_auth_token(
        DBHostname=os.environ['PROXY_ENDPOINT'],
        Port=3306,
        DBUsername=os.environ['DB_USER'],
        Region=os.environ['AWS_REGION']
    )
    # Cập nhật thời gian hết hạn (15 phút = 900 giây, trừ 60 giây để an toàn)
    token_expiry = current_time + 840
    return cached_token


def get_db_connection():
    """Establish a MySQL database connection via RDS Proxy."""
    try:
        db_token = get_db_token()
        # secret_dict = get_secret()
        return mysql.connector.connect(
            # host=os.environ.get('PROXY_ENDPOINT'),
            # user=secret_dict['username'],
            # password=secret_dict['password'],
            # database=secret_dict['dbname'],
            # port=int(secret_dict['port']),
            # connection_timeout=10
            host=os.environ['PROXY_ENDPOINT'],
            port=3306,
            user=os.environ['DB_USER'],
            password=db_token,
            database=os.environ['DB_NAME'],
            connection_timeout=10
        )
    except mysql.connector.Error as e:
        logger.error(f"Database connection error: {e}")
        raise
    except Exception as e:
        logger.error(f"Error creating database connection: {str(e)}")
        raise

def filter_orders(customer_id, status, start_date, end_date):
    start_time = time.time()
    cache_key = f"orders:filter:{customer_id or ''}:{status or ''}:{start_date or ''}:{end_date or ''}"

    try:
        cached_orders = primary_cache.get(cache_key)
        if cached_orders:
            latency_ms = (time.time() - start_time) * 1000
            logger.info(f"Cache hit, latency: {latency_ms:.2f} ms")
            return {
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'statusCode': 200,
                'body': cached_orders
            }
    except redis.RedisError as e:
        logger.error(f"Valkey error (reader): {e}")

    sql = "SELECT order_id, order_date, customer_id, total_amount, status FROM orders WHERE 1=1"
    params = []

    if customer_id:
        sql += " AND customer_id = %s"
        params.append(customer_id)
    if status:
        sql += " AND status = %s"
        params.append(status)
    if start_date and end_date:
        sql += " AND order_date BETWEEN %s AND %s"
        params.extend([start_date, end_date])

    sql += " LIMIT 100"

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        orders = cursor.fetchall()

        try:
            primary_cache.setex(cache_key, 60, json.dumps(orders, default=str))
        except redis.RedisError as e:
            logger.error(f"Valkey error (primary): {e}")

        latency_ms = (time.time() - start_time) * 1000
        logger.info(f"Cache miss, query latency: {latency_ms:.2f} ms")

        cursor.close()
        conn.close()

        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 200,
            'body': json.dumps({'orders': orders, 'latency_ms': latency_ms}, default=str)
        }
    except mysql.connector.Error as e:
        logger.error(f"Database error: {e}")
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Database error: {e}'})
        }
    finally:
        if conn and conn.is_connected():
            conn.close()
            logger.info("Database connection closed")

def get_order(order_id, order_date):
    start_time = time.time()
    cache_key = f"order:{order_id}:{order_date}"

    try:
        cached_order = primary_cache.get(cache_key)
        if cached_order:
            latency_ms = (time.time() - start_time) * 1000
            logger.info(f"Cache hit, latency: {latency_ms:.2f} ms")
            return {
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'statusCode': 200,
                'body': cached_order
            }
    except redis.RedisError as e:
        logger.error(f"Valkey error (reader): {e}")

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT order_id, order_date, customer_id, total_amount, status, shipping_address "
            "FROM orders WHERE order_id = %s AND order_date = %s",
            (order_id, order_date)
        )
        order = cursor.fetchone()

        if not order:
            return {
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'statusCode': 404,
                'body': json.dumps({'error': 'Order not found'})
            }

        try:
            primary_cache.setex(cache_key, 60, json.dumps(order, default=str))
        except redis.RedisError as e:
            logger.error(f"Valkey error (primary): {e}")

        latency_ms = (time.time() - start_time) * 1000
        logger.info(f"Cache miss, query latency: {latency_ms:.2f} ms")

        cursor.close()
        conn.close()

        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 200,
            'body': json.dumps({'order': order, 'latency_ms': latency_ms}, default=str)
        }
    except mysql.connector.Error as e:
        logger.error(f"Database error: {e}")
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Database error: {e}'})
        }
    finally:
        if conn and conn.is_connected():
            conn.close()
            logger.info("Database connection closed")

def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event, default=str)}")
    try:
        http_method = event.get('httpMethod', '')
        query_params = event.get('queryStringParameters', {}) or {}
        body = {} if event.get('body') is None else json.loads(event.get('body', '{}'))

        if query_params.get('order_id') and query_params.get('order_date'):
            return get_order(query_params.get('order_id'), query_params.get('order_date'))
        else:
            customer_id = query_params.get('customer_id') or body.get('customer_id')
            status = query_params.get('status') or body.get('status')
            start_date = query_params.get('start_date') or body.get('start_date')
            end_date = query_params.get('end_date') or body.get('end_date')
            return filter_orders(customer_id, status, start_date, end_date)
    except Exception as e:
        logger.error(f"Unexpected error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }
