import boto3
import json

def get_next_instance_type(current_instance):
    instance_types = [
        'db.t3.micro', 'db.t3.small', 'db.t3.medium', 'db.t3.large',
        'db.m5.large', 'db.m5.xlarge', 'db.m5.2xlarge'
    ]
    try:
        current_index = instance_types.index(current_instance)
        if current_index < len(instance_types) - 1:
            return instance_types[current_index + 1], True
        elif current_index > 0:
            return instance_types[current_index - 1], False
        else:
            return current_instance, None
    except ValueError:
        return current_instance, None

def lambda_handler(event, context):
    try:
        sns_message = event['Records'][0]['Sns']['Message']
        message = json.loads(sns_message)
        alarm_name = message['AlarmName']
        db_name = next(item['value'] for item in message['Trigger']['Dimensions'] if item['name'] == 'DBInstanceIdentifier')
        alarm_state = message['NewStateValue']

        rds = boto3.client('rds')
        cloudwatch = boto3.client('cloudwatch')
        response = rds.describe_db_instances(DBInstanceIdentifier=db_name)
        db_instance = response['DBInstances'][0]
        current_instance_type = db_instance['DBInstanceClass']
        alarm_info = cloudwatch.describe_alarms(AlarmNames=[alarm_name])['MetricAlarms'][0]
        alarm_actions = alarm_info['AlarmActions']
        evaluation_periods = alarm_info['EvaluationPeriods']
        new_instance_type, is_scale_up = get_next_instance_type(current_instance_type)

        if is_scale_up is None:
            print(f"Cannot scale {current_instance_type} further for DB {db_name}")
            return

        if (alarm_state == 'ALARM' and is_scale_up) or (alarm_state == 'OK' and not is_scale_up):
            print(f"Scaling {'up' if is_scale_up else 'down'} DB {db_name} from {current_instance_type} to {new_instance_type}")
            rds.modify_db_instance(
                DBInstanceIdentifier=db_name,
                DBInstanceClass=new_instance_type,
                ApplyImmediately=True
            )
            cloudwatch.put_metric_alarm(
                AlarmName=alarm_name,
                AlarmActions=alarm_actions,
                Period=60,
                MetricName='CPUUtilization',
                Dimensions=[{'Name': 'DBInstanceIdentifier', 'Value': db_name}],
                Statistic='Average',
                Namespace='AWS/RDS',
                EvaluationPeriods=evaluation_periods,
                ComparisonOperator='GreaterThanThreshold' if is_scale_up else 'LessThanThreshold',
                Threshold=60.0
            )

        print(f"Completed processing for DB {db_name}")
    except Exception as e:
        print(f"Error processing event: {str(e)}")
        raise e
