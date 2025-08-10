import json
import mysql.connector
import redis
import requests
import os
import logging
import time
import uuid

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

def view_orders(page, page_size):
    start_time = time.time()
    if page < 1 or page_size < 1:
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid page or page_size'})
        }

    offset = (page - 1) * page_size
    cache_key = f"orders:all:page_{page}:size_{page_size}"

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

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT order_id, order_date, customer_id, total_amount, status, shipping_address "
            "FROM orders ORDER BY order_date DESC LIMIT %s OFFSET %s",
            (page_size, offset)
        )
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
            'body': json.dumps({'orders': orders, 'page': page, 'page_size': page_size, 'latency_ms': latency_ms}, default=str)
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

def insert_order(customer_id, order_date, total_amount, status, shipping_address):
    start_time = time.time()
    order_id = str(uuid.uuid4())

    if not all([customer_id, order_date, total_amount, status, shipping_address]):
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing required fields'})
        }

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO orders (order_id, customer_id, order_date, total_amount, status, shipping_address) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (order_id, customer_id, order_date, total_amount, status, shipping_address)
        )
        conn.commit()

        latency_ms = (time.time() - start_time) * 1000
        logger.info(f"Insert latency: {latency_ms:.2f} ms")

        cursor.close()
        conn.close()

        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 201,
            'body': json.dumps({'order_id': order_id, 'order_date': order_date, 'message': 'Order created'})
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

def update_order(order_id, order_date, total_amount, status, shipping_address):
    start_time = time.time()
    if not all([order_id, order_date]):
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing order_id or order_date'})
        }

    sql = "UPDATE orders SET"
    params = []
    updates = []

    if total_amount is not None:
        updates.append(" total_amount = %s")
        params.append(total_amount)
    if status:
        updates.append(" status = %s")
        params.append(status)
    if shipping_address:
        updates.append(" shipping_address = %s")
        params.append(shipping_address)

    if not updates:
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 400,
            'body': json.dumps({'error': 'No fields to update'})
        }

    sql += ",".join(updates) + " WHERE order_id = %s AND order_date = %s"
    params.extend([order_id, order_date])

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return {
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'statusCode': 404,
                'body': json.dumps({'error': 'Order not found'})
            }

        cache_key = f"order:{order_id}:{order_date}"
        try:
            primary_cache.delete(cache_key)
            logger.info(f"Cache invalidated: {cache_key}")
        except redis.RedisError as e:
            logger.error(f"Valkey error (primary): {e}")

        latency_ms = (time.time() - start_time) * 1000
        logger.info(f"Update latency: {latency_ms:.2f} ms")

        cursor.close()
        conn.close()

        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 200,
            'body': json.dumps({'message': 'Order updated'})
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

def delete_order(order_id, order_date):
    start_time = time.time()
    if not order_id or not order_date:
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing order_id or order_date'})
        }

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM orders WHERE order_id = %s AND order_date = %s",
            (order_id, order_date)
        )
        conn.commit()

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return {
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'statusCode': 404,
                'body': json.dumps({'error': 'Order not found'})
            }

        cache_key = f"order:{order_id}:{order_date}"
        try:
            primary_cache.delete(cache_key)
            logger.info(f"Cache invalidated: {cache_key}")
        except redis.RedisError as e:
            logger.error(f"Valkey error (primary): {e}")

        latency_ms = (time.time() - start_time) * 1000
        logger.info(f"Delete latency: {latency_ms:.2f} ms")

        cursor.close()
        conn.close()

        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 200,
            'body': json.dumps({'message': 'Order deleted'})
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
        path_params = event.get('pathParameters', {}) or {}
        query_params = event.get('queryStringParameters', {}) or {}
        body = {} if event.get('body') is None else json.loads(event.get('body', '{}'))

        if http_method == 'GET':
            try:
                page = int(query_params.get('page', 1))
                page_size = int(query_params.get('page_size', 100))
            except ValueError:
                return {
                    'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                    'statusCode': 400,
                    'body': json.dumps({'error': 'page and page_size must be integers'})
                }
            return view_orders(page, page_size)
        elif http_method == 'POST':
            customer_id = body.get('customer_id')
            order_date = body.get('order_date')
            total_amount = body.get('total_amount')
            status = body.get('status')
            shipping_address = body.get('shipping_address')
            return insert_order(customer_id, order_date, total_amount, status, shipping_address)
        elif http_method == 'DELETE':
            order_id = path_params.get('order_id') or query_params.get('order_id') or body.get('order_id')
            order_date = path_params.get('order_date') or query_params.get('order_date') or body.get('order_date')
            if not order_id or not order_date:
                return {
                    'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                    'statusCode': 400,
                    'body': json.dumps({'error': 'Missing order_id or order_date'})
                }
            return delete_order(order_id, order_date)
        else:
            return {
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'statusCode': 405,
                'body': json.dumps({'error': f'Method {http_method} not allowed'})
            }
    except Exception as e:
        logger.error(f"Unexpected error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }
