"""
KYC-B B2 — 分級額度（每日出金上限）測試。

------- 契約（見 docs/08-1_kyc_spec.md 開發方規格「分級額度」）-------
出金在「KYC 已通過」閘門之後、動錢之前，多一道分級每日額度閘門：
  「今日(自然日)累計出金(法幣) + 本次出金(法幣) ≤ 該等級每日上限」才放行，超限 → 403，餘額不變。

三個定案的設計決策：
  1. 計價單位：法幣計價。各幣別出金以匯率換算成同一種法幣再加總比較。
     匯率由管理者維護在 `CurrencyModel.fiat_rate`（真實「指數價」的範圍一佔位，見規格）。
  2. 時間窗：自然日重置（每日 00:00 歸零，看「今天」累計）。
  3. 計量來源：帳本流水 `LedgerEntryModel`（reason=WITHDRAW）在窗內加總。

範圍一契約值（本檔即契約）：
  參考法幣：`LegalTenderModel`（`code` 唯一、最多一筆 `enable=True`），啟用的那筆即目前參考法幣。
  匯率由管理者維護：`CurrencyModel.fiat_rate`（本檔建立幣別時設定 USDT=1、BTC=30000）。
  KYC_TIER_DAILY_LIMIT = {0: 0, 1: 100000, 2: None}（`member/constants.py`，None = 無上限，單位為參考法幣）。
  tier 存 `UserProfileModel.kyc_tier`（整數，預設 0）＝交易者等級，決定額度，與驗證狀態正交。

tier 直接在 setUp 設定，不依賴 approve 流程（approve 指派 tier 另有一條測試）。
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from currency.models import CurrencyModel, LegalTenderModel
from member.constants import KycStatus, KycEvent, KYC_TIER_DAILY_LIMIT
from member.models import UserProfileModel, WalletModel
from ledger.constants import ReasonType
from ledger.models import LedgerEntryModel

WITHDRAW_URL = "/api/user/wallet/withdraw/"


def D(x):
    return Decimal(str(x))


class TierConfigContractTest(APITestCase):
    """把範圍一的每日上限政策數字釘成契約（匯率改由管理者維護在 CurrencyModel.fiat_rate）。"""

    def test_tier_daily_limits(self):
        self.assertEqual(KYC_TIER_DAILY_LIMIT[0], D(0))
        self.assertEqual(KYC_TIER_DAILY_LIMIT[1], D(100000))
        self.assertIsNone(KYC_TIER_DAILY_LIMIT[2])  # 無上限


class LegalTenderModelTest(APITestCase):
    """參考法幣清單由管理者維護：code 不可重複、最多只能啟用一種。"""

    def test_code_unique(self):
        LegalTenderModel.objects.create(code="TWD")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LegalTenderModel.objects.create(code="TWD")

    def test_at_most_one_enabled(self):
        LegalTenderModel.objects.create(code="TWD", enable=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LegalTenderModel.objects.create(code="USD", enable=True)

    def test_multiple_disabled_allowed(self):
        LegalTenderModel.objects.create(code="TWD", enable=False)
        LegalTenderModel.objects.create(code="USD", enable=False)
        self.assertEqual(LegalTenderModel.objects.filter(enable=False).count(), 2)

    def test_enabled_is_reference_fiat(self):
        LegalTenderModel.objects.create(code="TWD", enable=False)
        LegalTenderModel.objects.create(code="USD", enable=True)
        enabled = LegalTenderModel.objects.filter(enable=True)
        self.assertEqual(enabled.count(), 1)
        self.assertEqual(enabled.first().code, "USD")


class WithdrawTierLimitTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="tiered")
        # 已通過 KYC，等級 1（每日上限法幣 100000）。
        UserProfileModel.objects.create(
            user=self.user, latest_kyc_status=KycStatus.APPROVED, kyc_tier=1
        )
        # 匯率由管理者維護在 CurrencyModel.fiat_rate；本檔以建立幣別時設定當管理者已設好。
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether", fiat_rate=D(1))
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin", fiat_rate=D(30000))
        self.usdt_wallet = WalletModel.objects.create(
            user=self.user, asset_type=self.usdt, available_balance=D(1000000)
        )
        self.btc_wallet = WalletModel.objects.create(
            user=self.user, asset_type=self.btc, available_balance=D(10)
        )
        self.client.force_authenticate(user=self.user)

    def _withdraw(self, currency, qty):
        return self.client.post(
            WITHDRAW_URL,
            {"asset_type_id": currency.id, "quantity": str(qty)},
            format="json",
        )

    # ---- 額度內 / 邊界 ----

    def test_within_daily_limit_ok(self):
        """單筆法幣值 40000 < 上限 100000 → 200。"""
        resp = self._withdraw(self.usdt, 40000)
        self.assertEqual(resp.status_code, 200)

    def test_cumulative_at_limit_ok(self):
        """今日累計剛好等於上限（40000 + 60000 = 100000）→ 兩筆皆 200。"""
        self.assertEqual(self._withdraw(self.usdt, 40000).status_code, 200)
        self.assertEqual(self._withdraw(self.usdt, 60000).status_code, 200)

    def test_cumulative_over_limit_rejected(self):
        """累計超過上限（40000 + 70000 = 110000 > 100000）→ 第二筆 403、餘額不變、不寫帳本。"""
        self.assertEqual(self._withdraw(self.usdt, 40000).status_code, 200)

        before = self._usdt_available()
        withdraw_ledgers_before = self._withdraw_ledger_count()
        resp = self._withdraw(self.usdt, 70000)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._usdt_available(), before)
        self.assertEqual(self._withdraw_ledger_count(), withdraw_ledgers_before)

    # ---- 跨幣別以法幣加總 ----

    def test_cross_currency_counts_together(self):
        """
        不同幣別的出金以法幣加總比同一上限。
        40000 USDT(=40000) 通過後，再出 3 BTC(=90000) → 累計 130000 > 100000 → 403。
        """
        self.assertEqual(self._withdraw(self.usdt, 40000).status_code, 200)
        resp = self._withdraw(self.btc, 3)  # 3 * 30000 = 90000
        self.assertEqual(resp.status_code, 403)

    def test_cross_currency_within_limit_ok(self):
        """40000 USDT(=40000) + 2 BTC(=60000) = 100000 剛好等於上限 → 皆 200。"""
        self.assertEqual(self._withdraw(self.usdt, 40000).status_code, 200)
        self.assertEqual(self._withdraw(self.btc, 2).status_code, 200)

    # ---- 自然日窗：昨天不算今天 ----

    def test_previous_day_not_counted(self):
        """昨天已用滿上限，今天額度重置 → 今天仍可出金。"""
        yesterday = timezone.now() - timedelta(days=1)
        with mock.patch("django.utils.timezone.now", return_value=yesterday):
            # 昨天出滿 100000（USDT）
            self.assertEqual(self._withdraw(self.usdt, 100000).status_code, 200)

        # 今天（真實時間）再出 40000，昨天那筆不計入今天 → 200
        resp = self._withdraw(self.usdt, 40000)
        self.assertEqual(resp.status_code, 200)

    # ---- Tier 2 無上限 ----

    def test_tier2_no_limit(self):
        """等級 2 每日上限為無上限 → 遠超 Tier1 上限的出金也放行。"""
        self.user.profile.kyc_tier = 2
        self.user.profile.save()

        resp = self._withdraw(self.usdt, 500000)  # 遠大於 100000
        self.assertEqual(resp.status_code, 200)

    # ---- 額度閘門在 KYC 通過閘門之後 ----

    def test_not_approved_still_blocked(self):
        """未通過 KYC 者，額度再高也先被 KYC 閘門擋（403），與分級無關。"""
        self.user.profile.latest_kyc_status = KycStatus.UNVERIFIED
        self.user.profile.save()

        resp = self._withdraw(self.usdt, 1)
        self.assertEqual(resp.status_code, 403)

    # ---- 等級與驗證正交：已通過但等級 0，額度為 0 ----

    def test_tier0_approved_but_zero_quota(self):
        """已通過驗證但等級 0（出金上限 0）→ 出金被額度閘門擋（403）；這是額度不是驗證問題。"""
        self.user.profile.kyc_tier = 0
        self.user.profile.save()

        resp = self._withdraw(self.usdt, 1)
        self.assertEqual(resp.status_code, 403)

    # ---- 未設匯率的幣別不可繞過額度 ----

    def test_zero_rate_currency_cannot_withdraw(self):
        """有額度上限時，未設匯率（fiat_rate=0）的幣別不可出金——不能因無法估值而以 0 值繞過額度。"""
        eth = CurrencyModel.objects.create(code="ETH", name="Ethereum")  # fiat_rate 預設 0
        WalletModel.objects.create(user=self.user, asset_type=eth, available_balance=D(100))

        resp = self._withdraw(eth, 1)
        self.assertEqual(resp.status_code, 403)

    # ---- helpers ----

    def _usdt_available(self):
        self.usdt_wallet.refresh_from_db()
        return self.usdt_wallet.available_balance

    def _withdraw_ledger_count(self):
        return LedgerEntryModel.objects.filter(
            user=self.user, reason=ReasonType.WITHDRAW
        ).count()


class ApproveAssignsTierTest(APITestCase):
    """審核通過時，由審核人員在核准當下決定核給的等級（Tier）。"""

    def setUp(self):
        self.staff = User.objects.create(
            username="reviewer", is_staff=True, is_superuser=True
        )

    def _make_applicant(self, username):
        user = User.objects.create(username=username)
        UserProfileModel.objects.create(
            user=user, latest_kyc_status=KycStatus.VERIFYING, kyc_tier=0
        )
        return user

    def _approve(self, applicant, kyc_tier):
        self.client.force_authenticate(user=self.staff)
        return self.client.post(
            f"/api/user/kyc/{applicant.id}/approve/", {"kyc_tier": kyc_tier}, format="json"
        )

    def test_reviewer_grants_tier1(self):
        applicant = self._make_applicant("grant1")
        resp = self._approve(applicant, 1)
        self.assertIn(resp.status_code, (200, 201))

        applicant.profile.refresh_from_db()
        self.assertEqual(applicant.profile.latest_kyc_status, KycStatus.APPROVED)
        self.assertEqual(applicant.profile.kyc_tier, 1)
        # 歷史層仍寫一筆 APPROVED 事件（不因分級而改變）
        self.assertTrue(
            applicant.kyc_records.filter(event_status=KycEvent.APPROVED).exists()
        )

    def test_reviewer_grants_tier2(self):
        applicant = self._make_applicant("grant2")
        resp = self._approve(applicant, 2)
        self.assertIn(resp.status_code, (200, 201))

        applicant.profile.refresh_from_db()
        self.assertEqual(applicant.profile.latest_kyc_status, KycStatus.APPROVED)
        self.assertEqual(applicant.profile.kyc_tier, 2)

    def test_reviewer_grants_tier0(self):
        """核給等級 0 是有效的（最低額度），不代表驗證無效——狀態仍為 APPROVED。"""
        applicant = self._make_applicant("grant0")
        resp = self._approve(applicant, 0)
        self.assertIn(resp.status_code, (200, 201))

        applicant.profile.refresh_from_db()
        self.assertEqual(applicant.profile.latest_kyc_status, KycStatus.APPROVED)
        self.assertEqual(applicant.profile.kyc_tier, 0)
