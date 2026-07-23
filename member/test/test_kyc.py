"""
M-KYC / KYC-A（第一步）— 身份驗證流程測試。

規格：docs/08-1_kyc_spec.md（§4 狀態機、§5 API、§6 出金閘門）。
本檔是「正確行為」的定義（測試即規格），實作由使用者完成、讓本檔變綠。

------- 契約 -------
狀態機（KycStatus，member/constants.py）：
    UNVERIFIED --送審--> PENDING --staff通過--> APPROVED（終態）
                            └----staff拒絕----> REJECTED --修正重送--> PENDING

端點（皆在 /api/user/kyc/ 底下；KycViewSet，lookup_field='user_id'）：
  - POST /api/user/kyc/                    送審，body {legal_name,id_number,birth_date,nationality}
                                           綁 request.user；UNVERIFIED/REJECTED -> PENDING     [IsAuthenticated]
  - GET  /api/user/kyc/me/                 查自己的 KYC 狀態                                    [IsAuthenticated]
  - POST /api/user/kyc/{user_id}/approve/  PENDING -> APPROVED，記 reviewed_by/at              [IsAdminUser]
  - POST /api/user/kyc/{user_id}/reject/   PENDING -> REJECTED，body {reason}                  [IsAdminUser]

出金閘門（WalletViewSet.withdraw）：
  KYC 未 APPROVED -> 403（PermissionDenied），且餘額完全不變；動錢之前就擋。

注意：狀態值請引用 member.constants.KycStatus，別在測試裡硬打字串（見規格 §3.1）。
"""

from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from currency.models import CurrencyModel
from member.constants import KycStatus
from member.models import UserProfileModel, WalletModel

KYC_URL = "/api/user/kyc/"
KYC_ME_URL = "/api/user/kyc/me/"
WITHDRAW_URL = "/api/user/wallet/withdraw/"


def D(x):
    return Decimal(str(x))


def approve_url(user):
    return f"/api/user/kyc/{user.id}/approve/"


def reject_url(user):
    return f"/api/user/kyc/{user.id}/reject/"


SUBMIT_BODY = {
    "legal_name": "王小明",
    "id_number": "A123456789",
    "birth_date": "1990-01-01",
    "nationality": "TW",
}


class KycStateMachineTest(APITestCase):
    """狀態機 + 送審 + admin 審核。"""

    def setUp(self):
        self.user = User.objects.create(username="trader")
        self.profile = UserProfileModel.objects.create(user=self.user)  # 預設 UNVERIFIED
        self.other = User.objects.create(username="other")
        self.other_profile = UserProfileModel.objects.create(user=self.other)
        self.admin = User.objects.create(username="admin", is_staff=True)
        self.client.force_authenticate(user=self.user)

    # ---- 輔助 ----
    def _submit(self, body=None):
        return self.client.post(KYC_URL, body or SUBMIT_BODY, format="json")

    def _as(self, user):
        self.client.force_authenticate(user=user)

    # ---- 預設狀態 ----
    def test_new_profile_defaults_unverified(self):
        """剛建立的 profile 預設為 UNVERIFIED。"""
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.kyc_status, KycStatus.UNVERIFIED)

    # ---- 送審 ----
    def test_submit_moves_to_pending(self):
        """送審成功 -> 狀態 PENDING，欄位存對，記錄送審時間。"""
        resp = self._submit()

        self.assertIn(resp.status_code, (200, 201))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.kyc_status, KycStatus.PENDING)
        self.assertEqual(self.profile.legal_name, "王小明")
        self.assertEqual(self.profile.nationality, "TW")
        self.assertIsNotNone(self.profile.kyc_submitted_at)

    def test_submit_requires_auth(self):
        """未登入送審 -> 401。"""
        self.client.force_authenticate(user=None)
        resp = self._submit()
        self.assertEqual(resp.status_code, 401)

    def test_submit_binds_request_user_not_body(self):
        """送審綁 request.user：body 夾帶別人的 id 也只會改到自己，別人不受影響。"""
        body = dict(SUBMIT_BODY, user=self.other.id, user_id=self.other.id)
        resp = self._submit(body)

        self.assertIn(resp.status_code, (200, 201))
        self.profile.refresh_from_db()
        self.other_profile.refresh_from_db()
        self.assertEqual(self.profile.kyc_status, KycStatus.PENDING)
        self.assertEqual(self.other_profile.kyc_status, KycStatus.UNVERIFIED)

    def test_cannot_resubmit_while_pending(self):
        """已在審核中（PENDING）不可重複送審 -> 400。"""
        self._submit()
        resp = self._submit()
        self.assertEqual(resp.status_code, 400)

    def test_cannot_submit_when_approved(self):
        """已通過（終態）不可再送審 -> 400。"""
        self.profile.kyc_status = KycStatus.APPROVED
        self.profile.save()

        resp = self._submit()

        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.kyc_status, KycStatus.APPROVED)

    # ---- admin 審核 ----
    def test_admin_approve(self):
        """staff 對 PENDING 用戶 approve -> APPROVED，記 reviewed_by / reviewed_at。"""
        self._submit()  # -> PENDING
        self._as(self.admin)

        resp = self.client.post(approve_url(self.user), {}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.kyc_status, KycStatus.APPROVED)
        self.assertEqual(self.profile.kyc_reviewed_by, self.admin)
        self.assertIsNotNone(self.profile.kyc_reviewed_at)

    def test_admin_reject_with_reason(self):
        """staff reject -> REJECTED，回寫 reject_reason。"""
        self._submit()  # -> PENDING
        self._as(self.admin)

        resp = self.client.post(
            reject_url(self.user), {"reason": "證件模糊"}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.kyc_status, KycStatus.REJECTED)
        self.assertEqual(self.profile.kyc_reject_reason, "證件模糊")

    def test_normal_user_cannot_approve(self):
        """一般用戶不能 approve 別人 -> 403（角色層權限）。"""
        self.other_profile.kyc_status = KycStatus.PENDING
        self.other_profile.save()

        resp = self.client.post(approve_url(self.other), {}, format="json")

        self.assertEqual(resp.status_code, 403)
        self.other_profile.refresh_from_db()
        self.assertEqual(self.other_profile.kyc_status, KycStatus.PENDING)

    def test_approve_non_pending_rejected(self):
        """對非 PENDING（此處 UNVERIFIED）的人 approve -> 400（沒有在等審的東西）。"""
        self._as(self.admin)

        resp = self.client.post(approve_url(self.user), {}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.kyc_status, KycStatus.UNVERIFIED)

    def test_rejected_can_resubmit(self):
        """被拒後可修正重送 -> 回到 PENDING（REJECTED 不是死路）。"""
        self.profile.kyc_status = KycStatus.REJECTED
        self.profile.save()

        resp = self._submit()

        self.assertIn(resp.status_code, (200, 201))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.kyc_status, KycStatus.PENDING)

    # ---- 查自己 ----
    def test_me_returns_own_status(self):
        """GET /me/ 回自己的 KYC 狀態。"""
        resp = self.client.get(KYC_ME_URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["kyc_status"], KycStatus.UNVERIFIED)

    def test_me_does_not_leak_full_id_number(self):
        """查詢不可回傳完整證件號碼（PII 遮罩，規格 §3.2 / §5）。"""
        self._submit()
        resp = self.client.get(KYC_ME_URL)

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("A123456789", resp.content.decode())

    def test_me_requires_auth(self):
        """未登入查自己 -> 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.get(KYC_ME_URL)
        self.assertEqual(resp.status_code, 401)


class WithdrawKycGateTest(APITestCase):
    """出金閘門：KYC 未通過擋出金。"""

    def setUp(self):
        self.user = User.objects.create(username="trader")
        self.profile = UserProfileModel.objects.create(user=self.user)  # UNVERIFIED
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.wallet = WalletModel.objects.create(
            user=self.user, asset_type=self.usdt,
            available_balance=D(100000), frozen_balance=D(0),
        )
        self.client.force_authenticate(user=self.user)

    def _withdraw(self, amount):
        payload = {"asset_type_id": self.usdt.id, "quantity": str(amount)}
        return self.client.post(WITHDRAW_URL, payload, format="json")

    def test_withdraw_blocked_when_unverified(self):
        """KYC 未通過 -> 出金 403，餘額完全不變。"""
        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 403)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))
        self.assertEqual(self.wallet.frozen_balance, D(0))

    def test_withdraw_blocked_when_pending(self):
        """審核中（PENDING）仍不能出金 -> 403。"""
        self.profile.kyc_status = KycStatus.PENDING
        self.profile.save()

        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 403)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))

    def test_withdraw_allowed_when_approved(self):
        """KYC 通過後 -> 出金 200，可用餘額正常扣減。"""
        self.profile.kyc_status = KycStatus.APPROVED
        self.profile.save()

        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(70000))

    def test_gate_runs_before_balance_checks(self):
        """閘門在動錢之前：未通過 KYC 就算金額超額，也回 403（資格先於輸入）。"""
        resp = self._withdraw(999999)  # 遠超餘額

        self.assertEqual(resp.status_code, 403)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))
