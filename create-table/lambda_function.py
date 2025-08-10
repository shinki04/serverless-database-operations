import json
import mysql.connector
import redis
import requests
import os
import logging

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
