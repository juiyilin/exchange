# myproject/celery.py
import os
from celery import Celery

# 設定 Django 的設定模組
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'exchange.settings')

app = Celery('exchange_project')

# 使用 Django 的 settings.py 來設定 Celery，所有 Celery 設定都以 CELERY_ 開頭
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自動去每個 app 裡面尋找 tasks.py
app.autodiscover_tasks()