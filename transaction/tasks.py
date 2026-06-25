from celery import shared_task
from member.models import WalletModel
from currency.models import TradingPairModel
from .models import OrderModel, TransactionModel
from .constants import OrderType, OrderStatus
from django.db import transaction
from collections import deque


@shared_task
def send_to_match_market(order_id, trading_pair_id):
    match_order(order_id, trading_pair_id)

def match_order(order_id, trading_pair_id):
    """
    撮合方法：傳入 taker 訂單的 pk，在單一 transaction 內完成撮合與結算。
    依「價格優先、時間其次」逐筆吃掉對手單，成交價採 maker（先掛者）的價格。
    1. 傳入taker
    2. 找出maker列表
    3. 撮合
    4. 根據成交數量修改狀態
    """
    with transaction.atomic():
        # 同一trading_pair的撮合一次只准一個流程。必須在鎖任何訂單之前先拿，
        # 否則「先鎖自己的單、再搶對手單」會與反向撮合互鎖成環 → PostgreSQL deadlock。
        TradingPairModel.objects.select_for_update().get(id=trading_pair_id)
        try:
            # 極端情況下，這張單可能在「自己的撮合任務」跑起來之前，就已經被別張單當 maker 吃掉而成FULLY_FILLED。(單元測試 test_cross_fire_buy_sell)
            # 加上 Celery 是 at-least-once、
            # 同一任務可能重送 → 不擋的話會把已成交的單再撮一次 → 超賣（凍結變負被 CHECK 擋下）。
            taker = OrderModel.objects.select_for_update().get(id=order_id, status__in=[OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED])
        except OrderModel.DoesNotExist:
            return

        maker_orders = deque(
            OrderModel.objects.select_for_update().get_waiting_match_orders(taker)
        )
        if not maker_orders:
            return

        # 用「剩餘待成交量」而非 taker.quantity：這張單若先前已被當 maker 部分成交，
        # 用總量會把已成交的部分再撮一次
        taker_remaining = taker.waiting_transaction_quantity()

        while taker_remaining > 0 and maker_orders:
            maker = maker_orders.popleft()
            maker_remaining = maker.waiting_transaction_quantity()
            if maker_remaining <= 0:
                continue

            matched = min(taker_remaining, maker_remaining)
            if taker.order_type == OrderType.BUY:
                buy_order, sell_order = taker, maker
            else:
                buy_order, sell_order = maker, taker

            new_transaction = TransactionModel.objects.create(
                buy_order=buy_order,
                sell_order=sell_order,
                quantity=matched,
                price=maker.price,
            )
            WalletModel.objects.transfer_asset(new_transaction)

            taker_remaining -= matched
            maker.mark_maker_status(matched, maker_remaining)
            WalletModel.objects.release_frozen(maker)

        taker.mark_taker_status(taker_remaining)
        WalletModel.objects.release_frozen(taker)
    return 
