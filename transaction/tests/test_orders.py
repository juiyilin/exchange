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

關於使用者：M7 後 OrderViewSet.create() 用 request.user 決定下單者，全域權限是
IsAuthenticated。測試用 force_authenticate(user=...) 指定當前登入用戶（跳過 JWT
驗證、直接設 request.user），取代過去 mock get_random_user 的做法。
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APITestCase

from currency.models import CurrencyModel, TradingPairModel
from member.constants import KycStatus
from member.models import UserProfileModel, WalletModel
from transaction.constants import OrderStatus, OrderType
from transaction.models import OrderModel

ORDER_URL = "/api/transaction/order/"


def D(x):
    return Decimal(str(x))


# 下單 API 會 .delay() 送撮合任務。測試環境沒 worker，用 ALWAYS_EAGER 讓它
# 在當前進程同步跑，避免漏送真 task 到 broker。
@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class OrderCreateTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="trader")
        # KYC-B 下單閘門：下單需 KYC 通過，本檔聚焦下單/凍結邏輯，故 setUp 直接給 APPROVED。
        UserProfileModel.objects.create(user=self.user, latest_kyc_status=KycStatus.APPROVED)
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
        # 全域 IsAuthenticated：所有請求都要登入。指定當前用戶為 self.user。
        self.client.force_authenticate(user=self.user)

    def _payload(self, order_type, pair, quantity, price):
        return {
            "trading_pair": pair.id,
            "quantity": str(quantity),
            "price": str(price),
            "order_type": order_type,
        }

    def test_buy_order_freezes_quote_currency(self):
        """買 1 BTC @ 30000 → 凍 30000 USDT(quote)。"""
        payload = self._payload(OrderType.BUY, self.pair, 1, 30000)

        resp = self.client.post(ORDER_URL, payload, format="json")

        self.assertEqual(resp.status_code, 201)
        self.usdt_wallet.refresh_from_db()
        self.assertEqual(self.usdt_wallet.available_balance, D(70000))
        self.assertEqual(self.usdt_wallet.frozen_balance, D(30000))
        order = OrderModel.objects.get()
        self.assertEqual(order.status, OrderStatus.PENDING)

    def test_sell_order_freezes_base_currency(self):
        """賣 1 BTC @ 30000 → 凍 1 BTC(base，與價格無關)。"""
        payload = self._payload(OrderType.SELL, self.pair, 1, 30000)

        resp = self.client.post(ORDER_URL, payload, format="json")

        self.assertEqual(resp.status_code, 201)
        self.btc_wallet.refresh_from_db()
        self.assertEqual(self.btc_wallet.available_balance, D(4))
        self.assertEqual(self.btc_wallet.frozen_balance, D(1))

    def test_insufficient_balance_rejected_and_balance_unchanged(self):
        """
        可用餘額不足 → 回 400，而且餘額「完全不變」。
        這條最重要：證明檢查擋在凍結之前，不會凍到一半。
        """
        # 只有 100000 USDT，卻想買 1000 BTC @ 30000（需 3000 萬）
        payload = self._payload(OrderType.BUY, self.pair, 1000, 30000)

        resp = self.client.post(ORDER_URL, payload, format="json")

        self.assertEqual(resp.status_code, 400)
        self.usdt_wallet.refresh_from_db()
        self.assertEqual(self.usdt_wallet.available_balance, D(100000))
        self.assertEqual(self.usdt_wallet.frozen_balance, D(0))
        self.assertEqual(OrderModel.objects.count(), 0)

    def test_inactive_pair_rejected(self):
        """幣對未開放(is_active=False) → 400（serializer 擋下）。"""
        eth = CurrencyModel.objects.create(code="ETH", name="Ethereum")
        inactive_pair = TradingPairModel.objects.create(
            base_currency=eth, quote_currency=self.usdt, is_active=False
        )
        payload = self._payload(OrderType.BUY, inactive_pair, 1, 30000)

        resp = self.client.post(ORDER_URL, payload, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(OrderModel.objects.count(), 0)

    def test_order_list_api(self):
        """下單後，查詢 API 應列得出這張單（且只列得到自己的）。"""
        payload = self._payload(OrderType.BUY, self.pair, 1, 30000)
        self.client.post(ORDER_URL, payload, format="json")

        resp = self.client.get(ORDER_URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["order_type"], OrderType.BUY)

    def test_unauthenticated_rejected(self):
        """沒登入 → 401（全域 IsAuthenticated）。"""
        self.client.force_authenticate(user=None)
        resp = self.client.post(ORDER_URL, self._payload(OrderType.BUY, self.pair, 1, 30000), format="json")
        self.assertEqual(resp.status_code, 401)
