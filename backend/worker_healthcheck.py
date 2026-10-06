"""
Healthcheck of the worker (see worker.sh): is it connected to the queue, and does it answer?
It doesn't import the backend, which takes seconds and memory, at every check.
"""
import os
import socket
import sys

from celery import Celery

# Same defaults as backend/celery_app.py
redis_url = f"redis://{os.environ.get('REDIS_HOST', 'localhost')}:{os.environ.get('REDIS_PORT', '6379')}"
broker_url = os.environ.get('QABOARD_TASKS_BROKER_URL', f'{redis_url}/1')

app = Celery(broker=broker_url)
app.conf.broker_connection_timeout = 5
replies = app.control.ping(destination=[f"celery@{socket.gethostname()}"], timeout=10)
sys.exit(0 if replies else 1)
