"""
M2 — 下單與凍結 API 測試（透過 HTTP 打 OrderViewSet）。

幣對改版後,訂單以「幣對(trading_pair) + 買賣方向(order_type)」描述,
不再傳 currency1/currency2。凍結規則由幣對的 base/quote 推導:
  - 買單(BUY)：用 quote(USDT)買 base(BTC) → 凍 quote,金額 = amount * price。
  - 賣單(SELL)：賣 base(BTC)換 quote(USDT) → 凍 base,金額 = amount(與價格無關)。

核心要驗證的事：
  1. 買單凍結 quote(USDT)，金額 = amount * price。
  2. 賣單凍結 base(BTC)，金額 = amount。
  3. 餘額不足 → 回 400，且餘額「完全不變」（不能凍到一半）。
  4. 幣對未開放(is_active=False) → 回 400（serializer 擋下）。
  5. 下單成功後狀態為 PENDING、查詢 API 列得出來。

關於使用者：OrderViewSet.create() 目前用 get_random_user()（暫時固定回傳 1）
決定下單者，這是 M7 才會修的技術債。測試裡我們用 mock 把它指到本測試建立的
使用者，讓測試不依賴那個暫時 hack，將來改認證後測試也不會壞。
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from currency.models import CurrencyModel, TradingPairModel
from member.models import WalletModel
from transaction.constants import OrderStatus, OrderType
from transaction.models import OrderModel

ORDER_URL = "/api/transaction/order/"


def D(x):
    return Decimal(str(x))


class OrderCreateTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="trader")
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        # 標的幣 BTC、計價幣 USDT
        self.pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        self.usdt_wallet = WalletModel.objects.create(
            user=self.user, asset_type=self.usdt, available_balance=D(100000)
        )
        self.btc_wallet = WalletModel.objects.create(
            user=self.user, asset_type=self.btc, available_balance=D(5)
        )

    def _payload(self, order_type, pair, amount, price):
        return {
            "trading_pair": pair.id,
            "amount": str(amount),
            "price": str(price),
            "order_type": order_type,
        }

    @patch("transaction.views.get_random_user")
    def test_buy_order_freezes_quote_currency(self, mock_uid):
        """買 1 BTC @ 30000 → 凍 30000 USDT(quote)。"""
        mock_uid.return_value = self.user
        payload = self._payload(OrderType.BUY, self.pair, 1, 30000)

        resp = self.client.post(ORDER_URL, payload, format="json")

        self.assertEqual(resp.status_code, 201)
        self.usdt_wallet.refresh_from_db()
        self.assertEqual(self.usdt_wallet.available_balance, D(70000))
        self.assertEqual(self.usdt_wallet.frozen_balance, D(30000))
        order = OrderModel.objects.get()
        self.assertEqual(order.status, OrderStatus.PENDING)

    @patch("transaction.views.get_random_user")
    def test_sell_order_freezes_base_currency(self, mock_uid):
        """賣 1 BTC @ 30000 → 凍 1 BTC(base，與價格無關)。"""
        mock_uid.return_value = self.user
        payload = self._payload(OrderType.SELL, self.pair, 1, 30000)

        resp = self.client.post(ORDER_URL, payload, format="json")

        self.assertEqual(resp.status_code, 201)
        self.btc_wallet.refresh_from_db()
        self.assertEqual(self.btc_wallet.available_balance, D(4))
        self.assertEqual(self.btc_wallet.frozen_balance, D(1))

    @patch("transaction.views.get_random_user")
    def test_insufficient_balance_rejected_and_balance_unchanged(self, mock_uid):
        """
        可用餘額不足 → 回 400，而且餘額「完全不變」。
        這條最重要：證明檢查擋在凍結之前，不會凍到一半。
        """
        mock_uid.return_value = self.user
        # 只有 100000 USDT，卻想買 1000 BTC @ 30000（需 3000 萬）
        payload = self._payload(OrderType.BUY, self.pair, 1000, 30000)

        resp = self.client.post(ORDER_URL, payload, format="json")

        self.assertEqual(resp.status_code, 400)
        self.usdt_wallet.refresh_from_db()
        self.assertEqual(self.usdt_wallet.available_balance, D(100000))
        self.assertEqual(self.usdt_wallet.frozen_balance, D(0))
        self.assertEqual(OrderModel.objects.count(), 0)

    @patch("transaction.views.get_random_user")
    def test_inactive_pair_rejected(self, mock_uid):
        """幣對未開放(is_active=False) → 400（serializer 擋下）。"""
        mock_uid.return_value = self.user
        eth = CurrencyModel.objects.create(code="ETH", name="Ethereum")
        inactive_pair = TradingPairModel.objects.create(
            base_currency=eth, quote_currency=self.usdt, is_active=False
        )
        payload = self._payload(OrderType.BUY, inactive_pair, 1, 30000)

        resp = self.client.post(ORDER_URL, payload, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(OrderModel.objects.count(), 0)

    @patch("transaction.views.get_random_user")
    def test_order_list_api(self, mock_uid):
        """下單後，查詢 API 應列得出這張單。"""
        mock_uid.return_value = self.user
        payload = self._payload(OrderType.BUY, self.pair, 1, 30000)
        self.client.post(ORDER_URL, payload, format="json")

        resp = self.client.get(ORDER_URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["order_type"], OrderType.BUY)
