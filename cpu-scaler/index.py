import boto3
import os
import json
import logging
import time
from botocore.exceptions import ClientError

# Configuration
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get instance types from environment variable
INSTANCE_TYPES_str = os.environ.get('INSTANCE_TYPES', 'db.t3.micro,db.t4g.micro,db.t4g.medium,db.m5.large')
if not INSTANCE_TYPES_str:
    raise ValueError("Environment variable 'INSTANCE_TYPES' is not set.")

# Ensure 'db.' prefix for each type
INSTANCE_TYPES = []
for instance in INSTANCE_TYPES_str.split(','):
    stripped = instance.strip()
    if not stripped.startswith('db.'):
        stripped = 'db.' + stripped
    INSTANCE_TYPES.append(stripped)

rds_client = boto3.client('rds')

def get_target_instance_type(current_instance_type, direction):
    try:
        current_index = INSTANCE_TYPES.index(current_instance_type)
        if direction == 'UP':
            if current_index < len(INSTANCE_TYPES) - 1:
                return INSTANCE_TYPES[current_index + 1]
            else:
                logger.info(f"'{current_instance_type}' is already the highest instance type.")
                return None
        elif direction == 'DOWN':
            if current_index > 0:
                return INSTANCE_TYPES[current_index - 1]
            else:
                logger.info(f"'{current_instance_type}' is already the lowest instance type.")
                return None
    except ValueError:
        logger.warning(f"Current instance type '{current_instance_type}' is not in the configured list.")
        return None

def scale_instance(db_name, scaling_direction):
    try:
        # Get current instance class with retry
        max_retries = 3
        retry_delay = 5  # Increased delay for better backoff
        for attempt in range(max_retries):
            try:
                logger.info(f"Calling describe_db_instances for '{db_name}' (attempt {attempt + 1}/{max_retries})")
                describe_start = time.time()
                response = rds_client.describe_db_instances(DBInstanceIdentifier=db_name)
                describe_end = time.time()
                logger.info(f"describe_db_instances took {describe_end - describe_start:.2f} seconds")
                break
            except ClientError as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Retryable error: {str(e)}. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    logger.error(f"Failed to describe instance '{db_name}' after {max_retries} attempts: {str(e)}")
                    return {'status': 'failed', 'reason': f'Failed to describe instance: {str(e)}'}
        
        instance = response['DBInstances'][0]
        logger.info(f"DB instance info: {json.dumps(instance, default=str)}")  # Safer logging
        
        # Verify engine is MySQL
        engine = instance.get('Engine', '')
        if engine != 'mysql':
            logger.error(f"DB instance '{db_name}' is not MySQL (engine: {engine}).")
            return {'status': 'failed', 'reason': f'Instance engine is {engine}, expected mysql'}

        current_type = instance['DBInstanceClass']
        logger.info(f"Current instance type: {current_type}")
        
        # Check if instance is available
        if instance['DBInstanceStatus'] != 'available':
            logger.warning(f"DB instance '{db_name}' is not in 'available' state (current: {instance['DBInstanceStatus']}). Aborting.")
            return {'status': 'aborted', 'reason': f'DB instance not available'}
            
        target_type = get_target_instance_type(current_type, scaling_direction)
        logger.info(f"Target instance type: {target_type}")
        
        if not target_type:
            logger.info(f"No target instance type found for scaling {scaling_direction} from {current_type}.")
            return {'status': 'no_action', 'reason': 'Already at min/max size or not in list'}

        # Perform scaling with retry
        for attempt in range(max_retries):
            try:
                logger.info(f"Scaling instance '{db_name}' {scaling_direction} from '{current_type}' to '{target_type}' (attempt {attempt + 1}/{max_retries}).")
                modify_start = time.time()
                rds_client.modify_db_instance(
                    DBInstanceIdentifier=db_name,
                    DBInstanceClass=target_type,
                    ApplyImmediately=True
                )
                modify_end = time.time()
                logger.info(f"modify_db_instance took {modify_end - modify_start:.2f} seconds")
                break
            except ClientError as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Retryable error: {str(e)}. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    logger.error(f"Failed to modify instance '{db_name}' after {max_retries} attempts: {str(e)}")
                    return {'status': 'failed', 'reason': f'Failed to modify instance: {str(e)}'}
        
        logger.info(f"Successfully initiated modification for '{db_name}' to '{target_type}'.")
        return {'status': 'success', 'scaled_to': target_type}
    
    except Exception as e:
        logger.error(f"Error scaling instance '{db_name}': {str(e)}")
        return {'status': 'failed', 'reason': str(e)}

def lambda_handler(event, context):
    start_time = time.time()
    logger.info(f"Start time: {start_time}")
    
    try:
        # Parse SNS message
        logger.info(f"Received event: {json.dumps(event, default=str)}")
        sns_message = event['Records'][0]['Sns']['Message']
        message = json.loads(sns_message)  # CloudWatch Alarm sends JSON payload
        logger.info(f"Parsed SNS message: {json.dumps(message, default=str)}")

        alarm_name = message['AlarmName']
        new_state = message['NewStateValue']
        logger.info(f"Parsed alarm_name: {alarm_name}, new_state: {new_state}")

        # Only act on ALARM state
        if new_state != 'ALARM':
            logger.info(f"Alarm state is '{new_state}', not 'ALARM'. No action will be taken.")
            return {'status': 'ignored', 'reason': f'State was {new_state}'}

        # Determine scaling direction
        scaling_direction = None
        if 'High' in alarm_name or "ScaleUp" in alarm_name:
            scaling_direction = 'UP'
        elif 'Low' in alarm_name or "ScaleDown" in alarm_name:
            scaling_direction = 'DOWN'
        else:
            logger.warning(f"Alarm name '{alarm_name}' does not indicate a scaling direction. Cannot determine action.")
            return {'status': 'ignored', 'reason': 'Action could not be determined from alarm name'}
        logger.info(f"Scaling direction: {scaling_direction}")

        # Get DBInstanceIdentifier(s) from dimensions or environment variable
        db_instances = []
        dimensions = message.get('Trigger', {}).get('Dimensions', [])
        db_name = next((dim['value'] for dim in dimensions if dim['name'] == 'DBInstanceIdentifier'), None)
        if db_name:
            db_instances.append(db_name)
        else:
            # Fallback to environment variable for multiple instances
            db_instances_str = os.environ.get('DB_INSTANCES', '')
            if db_instances_str:
                db_instances = [db.strip() for db in db_instances_str.split(',')]
            else:
                raise ValueError("No 'DBInstanceIdentifier' in event and 'DB_INSTANCES' not set.")

        logger.info(f"DB instances to scale: {db_instances}")

        # Scale each instance
        results = []
        for db_name in db_instances:
            logger.info(f"Processing DB instance '{db_name}'")
            result = scale_instance(db_name, scaling_direction)
            results.append({db_name: result})

        end_time = time.time()
        logger.info(f"Execution completed in {end_time - start_time:.2f} seconds")
        return {'status': 'completed', 'results': results}

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        end_time = time.time()
        logger.info(f"Execution failed after {end_time - start_time:.2f} seconds")
        raise e