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
