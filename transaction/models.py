from django.contrib.auth.models import User
from django.db import models

from common.func import generate_hex_uuid
from common.models import BaseTimeModel
from currency.models import CurrencyModel

from .constants import OrderStatus, OrderType


class OrderModel(BaseTimeModel):
    order_number = models.CharField(
        default=generate_hex_uuid, max_length=32, unique=True, verbose_name="訂單代號"
    )
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, verbose_name="用戶"
    )
    currency1 = models.ForeignKey(
        CurrencyModel,
        on_delete=models.SET_NULL,
        null=True,
        related_name="out_currency",
        verbose_name="出去的貨幣",
    )
    currency2 = models.ForeignKey(
        CurrencyModel,
        on_delete=models.SET_NULL,
        null=True,
        related_name="in_currency",
        verbose_name="進來的貨幣",
    )
    amount = models.DecimalField(max_digits=20, decimal_places=2, verbose_name="數量")
    price = models.DecimalField(max_digits=20, decimal_places=2, verbose_name="價格")
    order_type = models.CharField(
        choices=OrderType.choices,
        max_length=10,
        verbose_name="訂單類型",
        help_text="例如：買、賣等",
    )
    status = models.CharField(
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
        max_length=20,
        verbose_name="訂單狀態",
        help_text="例如：等待中、部分成交、已取消等",
    )

    class Meta:
        verbose_name = "訂單"
        verbose_name_plural = "訂單"

    def executed_transaction_amount(self):
        # 已成交的數量
        if self.order_type == self.OrderType.BUY:
            return sum(transaction.amount for transaction in self.buy_order.all())
        return sum(transaction.amount for transaction in self.sell_order.all())

    def waiting_transaction_amount(self):
        # 計算等待成交的數量
        return self.amount - self.executed_transaction_amount()


class TransactionModel(BaseTimeModel):
    transaction_number = models.CharField(
        default=generate_hex_uuid, max_length=32, unique=True, verbose_name="交易代號"
    )
    order1 = models.ForeignKey(
        OrderModel,
        on_delete=models.SET_NULL,
        null=True,
        related_name="buy_order",
        verbose_name="買單",
    )
    order2 = models.ForeignKey(
        OrderModel,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sell_order",
        verbose_name="賣單",
    )
    amount = models.DecimalField(
        max_digits=20, decimal_places=2, verbose_name="成交數量"
    )
    price = models.DecimalField(
        max_digits=20, decimal_places=2, verbose_name="成交價格"
    )

    class Meta:
        verbose_name = "交易"
        verbose_name_plural = "交易"
