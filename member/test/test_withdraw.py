"""
M-基本收尾 — 模擬出金 API 測試（這是「規格」，不是實作）

============================================================================
你的任務：在 member 加一個出金端點，讓底下測試變綠。

------- 假設的契約（測試照這個打）-------
端點：POST /api/user/wallet/withdraw/
      （建議做成 WalletViewSet 的 @action(detail=False, methods=['post'])，
       名稱 withdraw；DRF 會自動產生這個 URL。）
Body：{"asset_type_id": <currency_id>, "quantity": "100"}
取用戶：沿用現有暫時做法 get_random_user()（測試會 mock member.views.get_random_user）。

行為（全部包在 transaction.atomic()）：
  1. select_for_update() 鎖住「該用戶 + 該幣」的錢包。
  2. 錢包不存在 → 400。
  3. quantity <= 0 → 400（不可用負數出金，否則等於偷偷加錢）。
  4. available_balance < quantity → 400（且餘額完全不變）。
  5. 否則 available_balance -= quantity 200。
  ※ 只動 available_balance，絕不碰 frozen_balance（凍結中的錢是掛單佔用的，不可領走）。

v0.1 不真的把幣送到任何地方，純粹數字減少。tx_hash / address 等鏈上欄位
留到範圍 2 再說（規格 06）。
============================================================================
"""

from decimal import Decimal
from unittest.mock import patch

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

    def _withdraw(self, amount, asset_type=None):
        payload = {"asset_type_id": (asset_type or self.usdt).id, "quantity": str(amount)}
        return self.client.post(WITHDRAW_URL, payload, format="json")

    @patch("member.views.get_random_user")
    def test_withdraw_decreases_available(self, mock_user):
        """正常出金 30000 → 200，可用 −30000，凍結不動。"""
        mock_user.return_value = self.user

        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(70000))
        self.assertEqual(self.wallet.frozen_balance, D(0))

    @patch("member.views.get_random_user")
    def test_withdraw_exact_available(self, mock_user):
        """剛好領光可用餘額 → 200，可用歸零。"""
        mock_user.return_value = self.user

        resp = self._withdraw(100000)

        self.assertEqual(resp.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(0))

    @patch("member.views.get_random_user")
    def test_withdraw_insufficient_rejected(self, mock_user):
        """超過可用餘額 → 400，餘額完全不變。"""
        mock_user.return_value = self.user

        resp = self._withdraw(100001)

        self.assertEqual(resp.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))
        self.assertEqual(self.wallet.frozen_balance, D(0))

    @patch("member.views.get_random_user")
    def test_withdraw_cannot_touch_frozen(self, mock_user):
        """
        凍結中的餘額不可被領走。
        可用只有 100、凍結有 50000，想領 200 應被擋（200 > 可用 100），
        即使「可用+凍結」遠大於 200。
        """
        mock_user.return_value = self.user
        self.wallet.available_balance = D(100)
        self.wallet.frozen_balance = D(50000)
        self.wallet.save()

        resp = self._withdraw(200)

        self.assertEqual(resp.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100))
        self.assertEqual(self.wallet.frozen_balance, D(50000))

    @patch("member.views.get_random_user")
    def test_withdraw_non_positive_rejected(self, mock_user):
        """金額 <= 0 → 400（防止用負數出金偷偷加錢）。"""
        mock_user.return_value = self.user

        resp_zero = self._withdraw(0)
        resp_neg = self._withdraw(-5000)

        self.assertEqual(resp_zero.status_code, 400)
        self.assertEqual(resp_neg.status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))

    @patch("member.views.get_random_user")
    def test_withdraw_no_wallet_rejected(self, mock_user):
        """對沒有錢包的幣別出金 → 400。"""
        mock_user.return_value = self.user
        btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")

        resp = self._withdraw(1, asset_type=btc)

        self.assertEqual(resp.status_code, 400)
