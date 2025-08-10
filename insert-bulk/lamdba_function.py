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
