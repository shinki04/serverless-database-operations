import json
import mysql.connector
import redis
import requests
import os
import logging
import uuid
from datetime import datetime, timedelta
import random

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

def insert_bulk_orders():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        orders = []
        start_date = datetime(2023, 1, 1)
        statuses = ['pending', 'processing', 'shipped', 'delivered', 'cancelled']
        batch_size = 1000
        total_orders = 10000

        for i in range(total_orders):
            order_id = str(uuid.uuid4())
            customer_id = str(uuid.uuid4())
            order_date = (start_date + timedelta(days=random.randint(0, 730))).strftime('%Y-%m-%d %H:%M:%S')
            total_amount = round(random.uniform(10.0, 1000.0), 2)
            status = random.choice(statuses)
            shipping_address = f"Address {i}, Sample City, Country"
            orders.append((order_id, customer_id, order_date, total_amount, status, shipping_address))

            if len(orders) == batch_size or i == total_orders - 1:
                sql = """
                INSERT INTO orders (order_id, customer_id, order_date, total_amount, status, shipping_address)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
                cursor.executemany(sql, orders)
                conn.commit()
                logger.info(f"Inserted {len(orders)} orders")
                orders = []

        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 201,
            'body': json.dumps({'message': f'Inserted {total_orders} orders successfully'})
        }
    except mysql.connector.Error as e:
        logger.error(f"Database error during bulk insert: {e}")
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Database error: {e}'})
        }
    except Exception as e:
        logger.error(f"Unexpected error during bulk insert: {e}")
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Unexpected error: {e}'})
        }
    finally:
        if conn and conn.is_connected():
            conn.close()
            logger.info("Database connection closed")

def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event, default=str)}")
    try:
        return insert_bulk_orders()
    except Exception as e:
        logger.error(f"Unexpected error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }
