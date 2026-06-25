"""
M-基本收尾 — 模擬出金 API 測試。

------- 契約 -------
端點：POST /api/user/wallet/withdraw/（WalletViewSet 的 @action）
Body：{"asset_type_id": <currency_id>, "quantity": "100"}
取用戶：M7 後用 request.user（全域 IsAuthenticated）。測試用 force_authenticate
        指定當前登入用戶，取代過去 mock get_random_user 的做法。

行為（全部包在 transaction.atomic()）：
  1. select_for_update() 鎖住「該用戶 + 該幣」的錢包。
  2. 錢包不存在 → 400。
  3. quantity <= 0 → 400（不可用負數出金，否則等於偷偷加錢）。
  4. available_balance < quantity → 400（且餘額完全不變）。
  5. 否則 available_balance -= quantity 200。
  ※ 只動 available_balance，絕不碰 frozen_balance（凍結中的錢是掛單佔用的，不可領走）。
"""

from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from currency.models import CurrencyModel
from member.models import WalletModel

WITHDRAW_URL = "/api/user/wallet/withdraw/"


def D(x):
    return Decimal(str(x))


class WithdrawTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="trader")
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.wallet = WalletModel.objects.create(
            user=self.user, asset_type=self.usdt,
            available_balance=D(100000), frozen_balance=D(0),
        )
        self.client.force_authenticate(user=self.user)

    def _withdraw(self, amount, asset_type=None):
        payload = {"asset_type_id": (asset_type or self.usdt).id, "quantity": str(amount)}
        return self.client.post(WITHDRAW_URL, payload, format="json")

    def test_withdraw_decreases_available(self):
        """正常出金 30000 → 200，可用 −30000，凍結不動。"""
        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(70000))
        self.assertEqual(self.wallet.frozen_balance, D(0))

    def test_withdraw_exact_available(self):
        """剛好領光可用餘額 → 200，可用歸零。"""
        resp = self._withdraw(100000)

        self.assertEqual(resp.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(0))

    def test_withdraw_insufficient_rejected(self):
        """超過可用餘額 → 400，餘額完全不變。"""
        resp = self._withdraw(100001)

        self.assertEqual(resp.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))
        self.assertEqual(self.wallet.frozen_balance, D(0))

    def test_withdraw_cannot_touch_frozen(self):
        """
        凍結中的餘額不可被領走。
        可用只有 100、凍結有 50000，想領 200 應被擋（200 > 可用 100），
        即使「可用+凍結」遠大於 200。
        """
        self.wallet.available_balance = D(100)
        self.wallet.frozen_balance = D(50000)
        self.wallet.save()

        resp = self._withdraw(200)

        self.assertEqual(resp.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100))
        self.assertEqual(self.wallet.frozen_balance, D(50000))

    def test_withdraw_non_positive_rejected(self):
        """金額 <= 0 → 400（防止用負數出金偷偷加錢）。"""
        resp_zero = self._withdraw(0)
        resp_neg = self._withdraw(-5000)

        self.assertEqual(resp_zero.status_code, 400)
        self.assertEqual(resp_neg.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))

    def test_withdraw_no_wallet_rejected(self):
        """對沒有錢包的幣別出金 → 400。"""
        btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")

        resp = self._withdraw(1, asset_type=btc)

        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_rejected(self):
        """沒登入 → 401（全域 IsAuthenticated）。"""
        self.client.force_authenticate(user=None)
        resp = self._withdraw(100)
        self.assertEqual(resp.status_code, 401)
