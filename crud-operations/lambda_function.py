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

_secret_cache = None

def get_secret():
    global _secret_cache
    if _secret_cache is not None:
        logger.info("Using cached secret")
        return _secret_cache

    try:
        secret_arn = os.environ.get('SECRET_ARN')
        if not secret_arn:
            raise Exception("SECRET_ARN environment variable not set")

        secrets_extension_endpoint = f"http://localhost:2773/secretsmanager/get?secretId={secret_arn}"
        headers = {"X-Aws-Parameters-Secrets-Token": os.environ.get('AWS_SESSION_TOKEN')}
        
        response = requests.get(secrets_extension_endpoint, headers=headers, timeout=10)
        if response.status_code != 200:
            raise Exception(f"Failed to retrieve secret, status code: {response.status_code}, response: {response.text}")
        
        secret_dict = json.loads(response.text).get("SecretString")
        if not secret_dict:
            raise Exception("SecretString not found in response")
        
        _secret_cache = json.loads(secret_dict)
        logger.info("Secret retrieved and cached")
        return _secret_cache
    except requests.RequestException as e:
        logger.error(f"Network error retrieving secret: {str(e)}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error for secret: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error retrieving secret: {str(e)}")
        raise

def get_db_connection():
    try:
        secret_dict = get_secret()
        return mysql.connector.connect(
            host=os.environ.get('PROXY_ENDPOINT'),
            user=secret_dict['username'],
            password=secret_dict['password'],
            database=secret_dict['dbname'],
            port=int(secret_dict['port']),
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
