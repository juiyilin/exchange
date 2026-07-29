"""
KYC-B B1 — 下單閘門測試（透過 HTTP 打 OrderViewSet.create）。

------- 契約（見 docs/08-1_kyc_spec.md §10.1）-------
下單前多一道 KYC 閘門，與出金閘門（§6）完全同型：
  KYC 未 APPROVED → 403 PermissionDenied，且「不建單、不凍結、不寫帳本」。

閘門要擋在動錢之前，所以即使餘額充足也一律 403——本檔的用戶都給了足夠餘額，
證明 403 純粹來自 KYC 資格、不是餘額不足（那會是 400）。

實作前這支測試是紅的；在 OrderViewSet.create 開頭加上閘門後轉綠。
cancel 不受閘門影響（錢留在系統內），不在本檔驗。
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APITestCase

from currency.models import CurrencyModel, TradingPairModel
from member.constants import KycStatus
from member.models import UserProfileModel, WalletModel
from transaction.constants import OrderType
from transaction.models import OrderModel

ORDER_URL = "/api/transaction/order/"


def D(x):
    return Decimal(str(x))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class OrderKycGateTest(APITestCase):
    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )

    def _funded_user(self, username, kyc_status=None):
        """建一個「餘額充足」的用戶；kyc_status=None 代表不建 profile。"""
        user = User.objects.create(username=username)
        if kyc_status is not None:
            UserProfileModel.objects.create(user=user, latest_kyc_status=kyc_status)
        WalletModel.objects.create(
            user=user, asset_type=self.usdt, available_balance=D(100000)
        )
        WalletModel.objects.create(
            user=user, asset_type=self.btc, available_balance=D(5)
        )
        return user

    def _buy_payload(self):
        # 買 1 BTC @ 30000，需 30000 USDT——餘額充足，所以擋下只可能是 KYC。
        return {
            "trading_pair": self.pair.id,
            "quantity": "1",
            "price": "30000",
            "order_type": OrderType.BUY,
        }

    def _assert_blocked(self, user):
        """下單被 KYC 閘門擋：403、不建單、餘額完全不動。"""
        self.client.force_authenticate(user=user)
        resp = self.client.post(ORDER_URL, self._buy_payload(), format="json")

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(OrderModel.objects.count(), 0)
        usdt_wallet = WalletModel.objects.get(user=user, asset_type=self.usdt)
        self.assertEqual(usdt_wallet.available_balance, D(100000))
        self.assertEqual(usdt_wallet.frozen_balance, D(0))

    def test_unverified_user_cannot_place_order(self):
        """UNVERIFIED（新用戶預設）下單 → 403，錢一分未動。"""
        user = self._funded_user("unverified", KycStatus.UNVERIFIED)
        self._assert_blocked(user)

    def test_verifying_user_cannot_place_order(self):
        """送審中（VERIFYING）尚未通過 → 403。"""
        user = self._funded_user("verifying", KycStatus.VERIFYING)
        self._assert_blocked(user)

    def test_rejected_user_cannot_place_order(self):
        """被拒（REJECTED）→ 403。"""
        user = self._funded_user("rejected", KycStatus.REJECTED)
        self._assert_blocked(user)

    def test_user_without_profile_blocked(self):
        """沒有 profile 的用戶（如 superuser）→ 403，不可因缺 profile 而放行或 500。"""
        user = self._funded_user("no_profile", kyc_status=None)
        self._assert_blocked(user)

    def test_approved_user_can_place_order(self):
        """APPROVED → 正常下單 201、凍結生效。"""
        user = self._funded_user("approved", KycStatus.APPROVED)
        self.client.force_authenticate(user=user)

        resp = self.client.post(ORDER_URL, self._buy_payload(), format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(OrderModel.objects.count(), 1)
        usdt_wallet = WalletModel.objects.get(user=user, asset_type=self.usdt)
        self.assertEqual(usdt_wallet.available_balance, D(70000))
        self.assertEqual(usdt_wallet.frozen_balance, D(30000))
