from django.db import models


class ReasonType(models.TextChoices):
    FREEZE = "FREEZE", "下單凍結"
    UNFREEZE = "UNFREEZE", "取消後解凍"
    SETTLE = "SETTLE", "結算"
    REFUND = "REFUND", "全部成交多凍結退款"  # 全部成交後退回多凍結的款項
    DEPOSIT = "DEPOSIT", "入金"
    WITHDRAW = "WITHDRAW", "出金"
    TRADING_FEE = "TRADING_FEE", "交易手續費"


class BalanceFieldType(models.TextChoices):
    AVAILABLE = 'AVAILABLE', '可用'
    FROZEN = 'FROZEN', '凍結'

