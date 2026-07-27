"""
端到端測試：透過 HTTP 打 OrderViewSet.create()，驗證「下單即撮合」整條鏈。

test_orders.py 已驗證「下單→凍結」那半邊；這支補另一半：
  create() 存完訂單後會呼叫 match_order(order.id)，所以「掛一張賣單、再掛一張
  能交叉的買單」應該透過 API 就自動成交、結算四個錢包——不需要手動呼叫撮合。

這支同時驗證 transfer_asset 的 get_or_create：賣方一開始沒有 USDT 錢包、
買方一開始沒有 BTC 錢包，成交後這兩個收款錢包應被自動建立並入帳。

使用者：M7 後 create() 用 request.user 決定下單者。這裡在兩次 POST 之間切換
force_authenticate（先賣方、再買方），避免自我成交、且結果可預測。
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APITestCase

from currency.models import CurrencyModel, TradingPairModel
from member.models import WalletModel
from transaction.constants import OrderStatus, OrderType
from transaction.models import OrderModel, TransactionModel

ORDER_URL = "/api/transaction/order/"


def D(x):
    return Decimal(str(x))


# 非同步化後，下單只 .delay() 丟任務。測試環境跑單元測試沒有 worker，
# 用 ALWAYS_EAGER 讓 send_to_match_market 在當前進程同步執行
@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class OrderCreateMatchingTest(APITestCase):
    def setUp(self):
        self.seller = User.objects.create(username="seller")
        self.buyer = User.objects.create(username="buyer")
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        # 賣方只有 BTC（付款幣）；USDT（收款幣）故意不建，驗證結算會自動建。
        WalletModel.objects.create(user=self.seller, asset_type=self.btc, available_balance=D(5))
        # 買方只有 USDT（付款幣）；BTC（收款幣）故意不建。
        WalletModel.objects.create(user=self.buyer, asset_type=self.usdt, available_balance=D(100000))

    def _payload(self, order_type, quantity, price):
        return {
            "trading_pair": self.pair.id,
            "quantity": str(quantity),
            "price": str(price),
            "order_type": order_type,
        }

    def get_wallet(self, user, currency):
        return WalletModel.objects.get(user=user, asset_type=currency)

    def _post_sell_then_buy(self, sell_qty, sell_price, buy_qty, buy_price):
        """先掛賣（maker），再掛買（taker）。回傳兩個 response。"""
        # 先以賣方身分掛賣單
        self.client.force_authenticate(user=self.seller)
        sell_resp = self.client.post(
            ORDER_URL, self._payload(OrderType.SELL, sell_qty, sell_price), format="json"
        )
        # 再切換成買方身分掛買單（不同人，避免自我成交防護擋掉）
        self.client.force_authenticate(user=self.buyer)
        buy_resp = self.client.post(
            ORDER_URL, self._payload(OrderType.BUY, buy_qty, buy_price), format="json"
        )
        return sell_resp, buy_resp

    # ---------- 測試 ----------
    def test_create_triggers_full_match(self):
        """賣 1@30000、買 1@30000 → API 下單就自動成交、四錢包正確結算。"""
        sell_resp, buy_resp = self._post_sell_then_buy(1, 30000, 1, 30000)

        self.assertEqual(sell_resp.status_code, 201)
        self.assertEqual(buy_resp.status_code, 201)

        # 產生一筆成交
        self.assertEqual(TransactionModel.objects.count(), 1)
        tx = TransactionModel.objects.get()
        self.assertEqual(tx.quantity, D(1))
        self.assertEqual(tx.price, D(30000))

        # 兩張單都全部成交
        sell = OrderModel.objects.get(order_type=OrderType.SELL)
        buy = OrderModel.objects.get(order_type=OrderType.BUY)
        self.assertEqual(sell.status, OrderStatus.FULLY_FILLED)
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)

        # 賣方：BTC 凍結歸零、剩 4 可用；USDT 錢包被自動建立並收到 30000
        self.assertEqual(self.get_wallet(self.seller, self.btc).frozen_balance, D(0))
        self.assertEqual(self.get_wallet(self.seller, self.btc).available_balance, D(4))
        self.assertEqual(self.get_wallet(self.seller, self.usdt).available_balance, D(30000))
        # 買方：USDT 凍結歸零、剩 70000 可用；BTC 錢包被自動建立並收到 1
        self.assertEqual(self.get_wallet(self.buyer, self.usdt).frozen_balance, D(0))
        self.assertEqual(self.get_wallet(self.buyer, self.usdt).available_balance, D(70000))
        self.assertEqual(self.get_wallet(self.buyer, self.btc).available_balance, D(1))

    def test_create_triggers_partial_fill(self):
        """賣 1@30000、買 0.4@30000 → 成交 0.4，買 FULLY_FILLED、賣 PARTIALLY_FILLED。"""
        self._post_sell_then_buy(1, 30000, "0.4", 30000)

        self.assertEqual(TransactionModel.objects.count(), 1)
        self.assertEqual(TransactionModel.objects.get().quantity, D("0.4"))

        sell = OrderModel.objects.get(order_type=OrderType.SELL)
        buy = OrderModel.objects.get(order_type=OrderType.BUY)
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)
        self.assertEqual(sell.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(sell.waiting_transaction_quantity(), D("0.6"))

        # 賣方 BTC 還凍 0.6、收到 12000 USDT；買方拿到 0.4 BTC、USDT 凍結歸零
        self.assertEqual(self.get_wallet(self.seller, self.btc).frozen_balance, D("0.6"))
        self.assertEqual(self.get_wallet(self.seller, self.usdt).available_balance, D(12000))
        self.assertEqual(self.get_wallet(self.buyer, self.btc).available_balance, D("0.4"))
        self.assertEqual(self.get_wallet(self.buyer, self.usdt).frozen_balance, D(0))

    def test_create_no_cross_leaves_both_pending(self):
        """賣 1@31000、買 1@30000 不交叉 → 不成交，兩張單留 PENDING、凍結不動。"""
        self._post_sell_then_buy(1, 31000, 1, 30000)

        self.assertEqual(TransactionModel.objects.count(), 0)
        sell = OrderModel.objects.get(order_type=OrderType.SELL)
        buy = OrderModel.objects.get(order_type=OrderType.BUY)
        self.assertEqual(sell.status, OrderStatus.PENDING)
        self.assertEqual(buy.status, OrderStatus.PENDING)

        # 凍結都還在：賣方 1 BTC、買方 30000 USDT
        self.assertEqual(self.get_wallet(self.seller, self.btc).frozen_balance, D(1))
        self.assertEqual(self.get_wallet(self.buyer, self.usdt).frozen_balance, D(30000))
        # 收款錢包不該被建立（沒成交）
        self.assertFalse(WalletModel.objects.filter(user=self.buyer, asset_type=self.btc).exists())
        self.assertFalse(WalletModel.objects.filter(user=self.seller, asset_type=self.usdt).exists())
