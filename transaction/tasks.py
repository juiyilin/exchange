from member.models import WalletModel

from .models import OrderModel, TransactionModel
from .constants import OrderType, OrderStatus
from django.db import transaction
from collections import deque


def send_to_match_market():
    pass

def match_order(order_id):
    """
    撮合方法：傳入 taker 訂單的 pk，在單一 transaction 內完成撮合與結算。
    依「價格優先、時間其次」逐筆吃掉對手單，成交價採 maker（先掛者）的價格。
    1. 傳入taker
    2. 找出maker列表
    3. 撮合
    4. 根據成交數量修改狀態
    """
    with transaction.atomic():
        try:
            taker = OrderModel.objects.select_for_update().get(id=order_id)
        except OrderModel.DoesNotExist:
            return

        maker_orders = deque(
            OrderModel.objects.select_for_update().get_waiting_match_orders(taker)
        )
        if not maker_orders:
            return

        taker_remaining = taker.quantity

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
