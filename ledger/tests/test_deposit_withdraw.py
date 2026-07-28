"""
M-日誌與帳本 — 出入金紀錄 DepositWithdrawModel（測試即規格）

============================================================================
這份測試定義 DepositWithdrawModel 與「出入金記帳」該有的行為。完整設計見
docs/07-1_logging_audit_spec.md §4 / §4.1。請寫實作讓每條測試變綠。

要做的（07 §4.1）：
  1) DepositWithdrawModel（放 ledger app）：欄位 user / asset_type / amount /
     direction(DEPOSIT|WITHDRAW) / status(PENDING|DONE|FAILED) / tx_hash / address /
     created_at / updated_at。★ 不是 append-only：status 會轉移，可正常 save()。
     範圍 1 直接建成 status="DONE"，tx_hash/address 留空。

  2) 出金 POST /api/user/wallet/withdraw/（已存在，補記帳）：
     通過後追加一筆 DepositWithdrawModel(direction=WITHDRAW, DONE)，
     並把 WITHDRAW 的 LedgerEntry 由原本 ref_type="manual" 改成「指回這筆 DW 列」。

  3) 入金 POST /api/user/wallet/deposit/（新增，僅限有入金權者）：
     WalletViewSet 的 @action(detail=False, methods=['post'])。
     權限：原為 [IsAdminUser]，M-RBAC 後改為要求 can_deposit 權限的自訂 permission（[CanDeposit]），
     由「管理員」群組持有（見 09-1_permission_spec.md）。故 setUp 的入金者改綁管理員群組、不再靠 is_staff。
     body：{"user_id", "asset_type_id", "quantity"}（替某用戶入金，對象用 body 的 user_id）。
     quantity<=0 → 400；無入金權 → 403。通過：get_or_create 錢包、available += quantity、
     建 DW(direction=DEPOSIT, DONE)、寫 LedgerEntry(reason=DEPOSIT, AVAILABLE, +quantity, 指回 DW)。

關於 ref_type：規格建議用固定字串 "deposit_withdraw"；但你既有實作是用 `_meta.model_name`
（order→"ordermodel"…）。本測試只硬性檢查「ref 指回那筆 DW（ref_id == str(dw.id)）」且「不再是 manual」，
不鎖死 ref_type 的確切字串，讓你沿用既有風格即可。

choices 一律用字串值比對（"DEPOSIT"/"WITHDRAW"/"DONE"/"AVAILABLE"…）。
============================================================================
"""

from decimal import Decimal

from django.contrib.auth.models import User, Group
from rest_framework.test import APITestCase

from currency.models import CurrencyModel
from member.constants import Role, KycStatus
from member.models import WalletModel, UserProfileModel
from ledger.models import LedgerEntryModel, DepositWithdrawModel


def D(x):
    return Decimal(str(x))


WITHDRAW_URL = "/api/user/wallet/withdraw/"
DEPOSIT_URL = "/api/user/wallet/deposit/"


class DWBaseTestCase(APITestCase):
    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.bob = User.objects.create(username="bob")
        # 出金端點有 KYC 閘門（未 APPROVED → 403,見 08-1 §6）：出金者 bob 需先過 KYC。
        UserProfileModel.objects.create(user=self.bob, latest_kyc_status=KycStatus.APPROVED)
        # M-RBAC:入金權改由 can_deposit 權限承載（「管理員」群組持有），不再靠 is_staff。
        # 群組與權限由 member 的 sync_roles（post_migrate）建好,測試 DB 已就緒。
        self.admin = User.objects.create(username="admin")
        self.admin.groups.add(Group.objects.get(name=Role.ADMIN))

    def fund(self, user, currency, available):
        return WalletModel.objects.create(
            user=user, asset_type=currency, available_balance=D(available)
        )

    def get_wallet(self, user, currency):
        return WalletModel.objects.get(user=user, asset_type=currency)


# ============================================================================
# (A) DepositWithdrawModel 模型契約：可建、status 可更新（不是 append-only）
# ============================================================================
class DepositWithdrawModelTest(DWBaseTestCase):
    def test_create_and_update_status(self):
        dw = DepositWithdrawModel.objects.create(
            user=self.bob, asset_type=self.usdt, amount=D(100),
            direction="DEPOSIT", status="PENDING",
        )
        # 與 LedgerEntry 不同：DW 是業務紀錄、status 會轉移，必須能正常更新
        dw.status = "DONE"
        dw.save()
        dw.refresh_from_db()
        self.assertEqual(dw.status, "DONE")
        self.assertEqual(dw.amount, D(100))
        self.assertEqual(dw.direction, "DEPOSIT")
        self.assertEqual(dw.asset_type, self.usdt)


# ============================================================================
# (B) 出金：建 DW 列 + LedgerEntry 指回它（不再是 manual）
# ============================================================================
class WithdrawRecordsDWTest(DWBaseTestCase):
    def test_withdraw_creates_dw_and_links_ledger(self):
        self.fund(self.bob, self.usdt, "100000")
        self.client.force_authenticate(user=self.bob)
        resp = self.client.post(
            WITHDRAW_URL,
            {"asset_type_id": self.usdt.id, "quantity": "5000"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        # DW 列：WITHDRAW / DONE / 5000
        dw = DepositWithdrawModel.objects.get(user=self.bob, direction="WITHDRAW")
        self.assertEqual(dw.amount, D(5000))
        self.assertEqual(dw.status, "DONE")
        self.assertEqual(dw.asset_type, self.usdt)

        # LedgerEntry：WITHDRAW，且 ref 指回這筆 DW（不再 manual）
        le = LedgerEntryModel.objects.get(
            user=self.bob, reason="WITHDRAW", balance_field="AVAILABLE"
        )
        self.assertEqual(le.delta, D(-5000))
        self.assertEqual(le.balance_after, D(95000))
        self.assertEqual(le.ref_id, str(dw.id))       # 關鍵：指回 DW 列
        self.assertNotEqual(le.ref_type, "manual")    # 不再是 manual
        self.assertTrue(le.ref_type)                  # 有值（規格建議 "deposit_withdraw"）


# ============================================================================
# (C) 入金端點：admin-only，記 DW + LedgerEntry(DEPOSIT)
# ============================================================================
class DepositEndpointTest(DWBaseTestCase):
    def _deposit(self, as_user, target, currency, quantity):
        self.client.force_authenticate(user=as_user)
        return self.client.post(
            DEPOSIT_URL,
            {"user_id": target.id, "asset_type_id": currency.id, "quantity": str(quantity)},
            format="json",
        )

    def test_admin_deposit_credits_and_records(self):
        resp = self._deposit(self.admin, self.bob, self.usdt, 100000)
        self.assertIn(resp.status_code, (200, 201), resp.content)

        # 錢包被自動建立並入帳
        w = self.get_wallet(self.bob, self.usdt)
        self.assertEqual(w.available_balance, D(100000))

        # DW 列：DEPOSIT / DONE
        dw = DepositWithdrawModel.objects.get(user=self.bob, direction="DEPOSIT")
        self.assertEqual(dw.amount, D(100000))
        self.assertEqual(dw.status, "DONE")

        # LedgerEntry：DEPOSIT，ref 指回 DW
        le = LedgerEntryModel.objects.get(
            user=self.bob, reason="DEPOSIT", balance_field="AVAILABLE"
        )
        self.assertEqual(le.delta, D(100000))
        self.assertEqual(le.balance_after, D(100000))
        self.assertEqual(le.ref_id, str(dw.id))
        self.assertNotEqual(le.ref_type, "manual")

    def test_non_admin_forbidden(self):
        """一般用戶不能入金 → 403，且不產生任何餘額/紀錄。"""
        resp = self._deposit(self.bob, self.bob, self.usdt, 100000)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertFalse(WalletModel.objects.filter(user=self.bob, asset_type=self.usdt).exists())
        self.assertFalse(DepositWithdrawModel.objects.exists())
        self.assertFalse(LedgerEntryModel.objects.filter(reason="DEPOSIT").exists())

    def test_non_positive_quantity_rejected(self):
        """quantity<=0 → 400，不產生紀錄。"""
        resp = self._deposit(self.admin, self.bob, self.usdt, 0)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(DepositWithdrawModel.objects.exists())


# ============================================================================
# (D) 真實出入金路徑下，對帳不變量仍成立
# ============================================================================
class DepositWithdrawReconcileTest(DWBaseTestCase):
    def test_deposit_then_withdraw_reconciles(self):
        # admin 入金 100000
        self.client.force_authenticate(user=self.admin)
        r1 = self.client.post(
            DEPOSIT_URL,
            {"user_id": self.bob.id, "asset_type_id": self.usdt.id, "quantity": "100000"},
            format="json",
        )
        self.assertIn(r1.status_code, (200, 201), r1.content)

        # bob 出金 30000
        self.client.force_authenticate(user=self.bob)
        r2 = self.client.post(
            WITHDRAW_URL,
            {"asset_type_id": self.usdt.id, "quantity": "30000"},
            format="json",
        )
        self.assertEqual(r2.status_code, 200, r2.content)

        w = self.get_wallet(self.bob, self.usdt)
        self.assertEqual(w.available_balance, D(70000))

        # available == Σ(AVAILABLE delta)，且最後一筆 balance_after == 當前餘額
        entries = LedgerEntryModel.objects.filter(
            user=self.bob, asset_type=self.usdt, balance_field="AVAILABLE"
        ).order_by("id")
        total = sum((e.delta for e in entries), Decimal("0"))
        self.assertEqual(total, w.available_balance)
        self.assertEqual(entries.last().balance_after, w.available_balance)
