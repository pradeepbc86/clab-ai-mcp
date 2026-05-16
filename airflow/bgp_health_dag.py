from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta

default_args = {
    'owner': 'network-ops',
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'bgp_health_check',
    default_args=default_args,
    description='Daily BGP health check via LLM agent',
    schedule_interval='@daily',
    start_date=datetime(2024, 1, 1),
    catchup=False,
)

check_neighbors = BashOperator(
    task_id='check_bgp_neighbors',
    bash_command='python /path/to/agent.py "check BGP on spine1"',
    dag=dag,
)

check_evpn = BashOperator(
    task_id='check_evpn_vni',
    bash_command='python /path/to/agent.py "check EVPN on leaf1"',
    dag=dag,
)

alert = BashOperator(
    task_id='alert_on_anomaly',
    bash_command='echo "BGP health check complete"',
    dag=dag,
)

check_neighbors >> check_evpn >> alert
