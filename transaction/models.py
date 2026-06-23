from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from collections import deque
from common.func import generate_hex_uuid
from common.models import BaseTimeModel
from currency.models import CurrencyModel, TradingPairModel
from .constants import OrderStatus, OrderType


class OrderQuerySet(models.QuerySet):
    def get_waiting_match_orders(self, order):
        waiting_match_order_type = set(OrderType.values) - set([order.order_type])
        available_match_status = [OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED]
        if order.order_type == OrderType.BUY:
            # 買單找最低賣價
            price_filter = {'price__lte': order.price}
            order_by_price = 'price'
        else:
            # 賣單找最高買價
            price_filter = {'price__gte': order.price}
            order_by_price = '-price'

        waiting_match_orders = self.filter(
                trading_pair=order.trading_pair,
                order_type__in=waiting_match_order_type,
                status__in=available_match_status,
                ordered_at__lte=order.ordered_at,
                **price_filter
            ).order_by(order_by_price, 'ordered_at', 'id')
        return waiting_match_orders


class OrderManager(models.Manager.from_queryset(OrderQuerySet)):
    pass


class OrderModel(BaseTimeModel):
    order_number = models.CharField(default=generate_hex_uuid, max_length=32, unique=True, verbose_name="訂單代號")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name="用戶")
    trading_pair = models.ForeignKey(TradingPairModel, on_delete=models.SET_NULL, null=True, related_name="trading_pair_orders", verbose_name="幣對")
    trading_pair_symbol = models.CharField(max_length=20, default='', verbose_name='幣對名稱')
    quantity = models.DecimalField(max_digits=20, decimal_places=2, verbose_name="數量")
    price = models.DecimalField(max_digits=20, decimal_places=2, verbose_name="價格")
    order_type = models.CharField(choices=OrderType.choices, max_length=10, verbose_name="訂單類型", help_text="例如：買、賣等")
    status = models.CharField(choices=OrderStatus.choices, default=OrderStatus.PENDING, max_length=20, verbose_name="訂單狀態", help_text="例如：等待中、部分成交、已取消等")
    ordered_at = models.DateTimeField(default=timezone.now, verbose_name='下單時間')

    objects = OrderManager()

    class Meta:
        verbose_name = "訂單"
        verbose_name_plural = "訂單"

    def save(self, *args, **kwargs):
        if not self.trading_pair_symbol:
            self.trading_pair_symbol = self.trading_pair.symbol
        return super().save(*args, **kwargs)

    def executed_transaction_quantity(self):
        # 已成交的數量
        if self.order_type == OrderType.BUY:
            return sum(transaction.quantity for transaction in self.buy_transactions.all())
        return sum(transaction.quantity for transaction in self.sell_transactions.all())

    def waiting_transaction_quantity(self):
        # 計算等待成交的數量
        return self.quantity - self.executed_transaction_quantity()

    def mark_maker_status(self, matched_quantity, remaining):
        """修改先掛單者(maker)的狀態"""
        if matched_quantity >= remaining:
            self.status = OrderStatus.FULLY_FILLED
        else:
            self.status = OrderStatus.PARTIALLY_FILLED
        self.save()

    def mark_taker_status(self, remaining):
        """修改吃單者(taker)的狀態"""
        if remaining <= 0:
            self.status = OrderStatus.FULLY_FILLED
        else:
            self.status = OrderStatus.PARTIALLY_FILLED
        self.save()

    def get_current_frozen(self):
        """取得目前的凍結餘額"""
        if self.order_type == OrderType.BUY:
            return self.quantity * self.price - sum(transaction.quantity * transaction.price for transaction in self.buy_transactions.all())
        return self.quantity - sum(transaction.quantity for transaction in self.sell_transactions.all())

    def get_asset_type(self):
        if self.order_type == OrderType.BUY:
            return self.trading_pair.quote_currency
        return self.trading_pair.base_currency


class TransactionModel(BaseTimeModel):
    transaction_number = models.CharField(default=generate_hex_uuid, max_length=32, unique=True, verbose_name="交易代號")
    buy_order = models.ForeignKey(OrderModel, on_delete=models.SET_NULL, null=True, related_name="buy_transactions", verbose_name="買單")
    sell_order = models.ForeignKey(OrderModel, on_delete=models.SET_NULL, null=True, related_name="sell_transactions", verbose_name="賣單")
    quantity = models.DecimalField(max_digits=20, decimal_places=2, verbose_name="成交數量")
    price = models.DecimalField(max_digits=20, decimal_places=2, verbose_name="成交價格")

    class Meta:
        verbose_name = "交易"
        verbose_name_plural = "交易"
