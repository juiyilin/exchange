from django.db import models


class OrderType(models.TextChoices):
    BUY = "BUY", "買"
    SELL = "SELL", "賣"


class OrderStatus(models.TextChoices):
    PENDING = "PENDING", "等待中"
    PARTIALLY_FILLED = "PARTIALLY_FILLED", "部分成交"
    FULLY_FILLED = "FULLY_FILLED", "全部成交"
    CANCELED = "CANCELED", "已取消"
