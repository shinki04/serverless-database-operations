import json
import mysql.connector
import redis
import requests
import os
import logging
import time
import boto3
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

def create_orders_table():
    CREATE_ORDERS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS orders (
        order_id VARCHAR(36) NOT NULL,
        customer_id VARCHAR(36) NOT NULL,
        order_date DATETIME NOT NULL,
        total_amount DECIMAL(10, 2) NOT NULL,
        status ENUM('pending', 'processing', 'shipped', 'delivered', 'cancelled') NOT NULL,
        shipping_address VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (order_id, order_date),
        INDEX idx_customer_id (customer_id),
        INDEX idx_order_date (order_date),
        INDEX idx_status (status),
        INDEX idx_composite (customer_id, order_date, status)
    ) PARTITION BY RANGE (TO_DAYS(order_date)) (
        PARTITION p2023 VALUES LESS THAN (TO_DAYS('2024-01-01')),
        PARTITION p2024 VALUES LESS THAN (TO_DAYS('2025-01-01')),
        PARTITION p2025 VALUES LESS THAN (TO_DAYS('2026-01-01')),
        PARTITION p_future VALUES LESS THAN (MAXVALUE)
    );
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        logger.info("Executing CREATE TABLE statement for 'orders' table")
        cursor.execute(CREATE_ORDERS_TABLE_SQL)
        conn.commit()
        logger.info("Table 'orders' created successfully")
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 200,
            'body': json.dumps({'message': 'Table "orders" created successfully!'})
        }
    except mysql.connector.Error as e:
        logger.error(f"Database error when creating table: {e}")
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Database error: {e}'})
        }
    except Exception as e:
        logger.error(f"Unexpected error when creating table: {e}")
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
        return create_orders_table()
    except Exception as e:
        logger.error(f"Unexpected error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'statusCode': 500,
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }
