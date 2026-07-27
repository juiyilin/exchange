"""
M-KYC / KYC-A（第一步）— 身份驗證流程測試。

規格：docs/08-1_kyc_spec.md（§3 兩層模型、§4 狀態機、§5 API、§6 出金閘門）。
本檔是「正確行為」的定義（測試即規格），實作由使用者完成、讓本檔變綠。

------- 設計（兩層，見規格 §3.4）-------
當前狀態層（一人一份，會覆寫）：UserProfileModel.latest_kyc_status + 身分欄位（legal_name/id_number/...）
歷史層（一人多筆，append-only）：KycRecordModel —— 每次事件寫一列，永不改、永不刪
  → 這跟 WalletModel（餘額）vs LedgerEntryModel（分錄）是同一套分層。

------- 狀態機（KycStatus）-------
    UNVERIFIED --送審--> VERIFYING --staff通過--> APPROVED
        ▲   ▲              └----staff拒絕----> REJECTED --修正重送--> VERIFYING
        │   └──────────────（被拒重送，回 VERIFYING）
        └──────── staff revoke（撤銷/重驗）──── APPROVED

每次轉移都在同一 atomic 內：改 profile 當前狀態 ＋ 寫一筆 KycRecordModel(event_status=...)。

------- 端點（/api/user/kyc/；KycViewSet，lookup_field='user_id'）-------
  POST /api/user/kyc/                    送審 {legal_name,id_number,birth_date,nationality}，綁 request.user  [IsAuthenticated]
  GET  /api/user/kyc/me/                 查自己的 KYC 狀態                                                    [IsAuthenticated]
  POST /api/user/kyc/{user_id}/approve/  VERIFYING -> APPROVED                                                 [IsAdminUser]
  POST /api/user/kyc/{user_id}/reject/   VERIFYING -> REJECTED，body {reason}（必填）                          [IsAdminUser]
  POST /api/user/kyc/{user_id}/revoke/   APPROVED -> UNVERIFIED，記 REVOKED（撤銷），body {reason}（必填）    [IsAdminUser]
  POST /api/user/kyc/{user_id}/reverify/ APPROVED -> UNVERIFIED，記 REVERIFY_REQUIRED，body {reason}（必填）   [IsAdminUser]

reason：reject/revoke/reverify 三者皆必填（沒帶 -> 400）；欄位在 KycRecordModel.reason（TextField）。

出金閘門（WalletViewSet.withdraw）：KYC 未 APPROVED -> 403，餘額不變，動錢之前就擋。

注意：狀態/事件值請引用 member.constants.KycStatus / KycEvent，別在測試裡硬打字串。
"""

from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from currency.models import CurrencyModel
from member.constants import KycStatus, KycEvent
from member.models import UserProfileModel, WalletModel, KycRecordModel


KYC_URL = "/api/user/kyc/"
KYC_ME_URL = "/api/user/kyc/me/"
WITHDRAW_URL = "/api/user/wallet/withdraw/"


def D(x):
    return Decimal(str(x))


def approve_url(user):
    return f"/api/user/kyc/{user.id}/approve/"


def reject_url(user):
    return f"/api/user/kyc/{user.id}/reject/"


def revoke_url(user):
    return f"/api/user/kyc/{user.id}/revoke/"


def reverify_url(user):
    return f"/api/user/kyc/{user.id}/reverify/"


SUBMIT_BODY = {
    "legal_name": "王小明",
    "id_number": "A123456789",
    "birth_date": "1990-01-01",
    "nationality": "TW",
}


class KycStateMachineTest(APITestCase):
    """狀態機 + 送審 + admin 審核 + 重新 KYC。"""

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

    def _records(self, user, event_status=None):
        qs = KycRecordModel.objects.filter(user=user)
        return qs.filter(event_status=event_status) if event_status else qs

    # ---- 預設狀態 ----
    def test_new_profile_defaults_unverified(self):
        """剛建立的 profile 預設為 UNVERIFIED。"""
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.UNVERIFIED)

    # ---- 送審 ----
    def test_submit_moves_to_pending(self):
        """送審成功 -> 當前狀態 VERIFYING、profile 身分欄位存對。"""
        resp = self._submit()

        self.assertIn(resp.status_code, (200, 201))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.VERIFYING)
        self.assertEqual(self.profile.legal_name, "王小明")
        self.assertEqual(self.profile.nationality, "TW")

    def test_submit_writes_snapshot_record(self):
        """送審 -> 歷史層多一筆 SUBMITTED，且快照當次送審內容（operator 是本人）。"""
        self._submit()

        rec = self._records(self.user, KycEvent.SUBMITTED).latest("created_at")
        self.assertEqual(rec.legal_name, "王小明")
        self.assertEqual(rec.operator, self.user)

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
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.VERIFYING)
        self.assertEqual(self.other_profile.latest_kyc_status, KycStatus.UNVERIFIED)

    def test_cannot_resubmit_while_pending(self):
        """已在審核中（VERIFYING）不可重複送審 -> 400。"""
        self._submit()
        resp = self._submit()
        self.assertEqual(resp.status_code, 400)

    def test_cannot_submit_when_approved(self):
        """已通過不可直接再送審（要先 revoke）-> 400。"""
        self.profile.latest_kyc_status = KycStatus.APPROVED
        self.profile.save()

        resp = self._submit()

        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.APPROVED)

    # ---- admin 審核 ----
    def test_admin_approve(self):
        """staff 對 VERIFYING approve -> APPROVED，歷史多一筆 APPROVED(operator=admin)。"""
        self._submit()  # -> VERIFYING
        self._as(self.admin)

        resp = self.client.post(approve_url(self.user), {}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.APPROVED)
        rec = self._records(self.user, KycEvent.APPROVED).latest("created_at")
        self.assertEqual(rec.operator, self.admin)

    def test_admin_reject_writes_reason(self):
        """staff reject -> REJECTED，歷史 REJECTED 帶 reason。"""
        self._submit()  # -> VERIFYING
        self._as(self.admin)

        resp = self.client.post(
            reject_url(self.user), {"reason": "證件模糊"}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.REJECTED)
        rec = self._records(self.user, KycEvent.REJECTED).latest("created_at")
        self.assertEqual(rec.reason, "證件模糊")

    def test_normal_user_cannot_approve(self):
        """一般用戶不能 approve 別人 -> 403（角色層權限）。"""
        self.other_profile.latest_kyc_status = KycStatus.VERIFYING
        self.other_profile.save()

        resp = self.client.post(approve_url(self.other), {}, format="json")

        self.assertEqual(resp.status_code, 403)
        self.other_profile.refresh_from_db()
        self.assertEqual(self.other_profile.latest_kyc_status, KycStatus.VERIFYING)

    def test_approve_non_pending_rejected(self):
        """對非 VERIFYING（此處 UNVERIFIED）的人 approve -> 400。"""
        self._as(self.admin)

        resp = self.client.post(approve_url(self.user), {}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.UNVERIFIED)

    # ---- 重新 KYC：情況一（被拒重送）----
    def test_rejected_can_resubmit(self):
        """被拒後可修正重送 -> 回到 VERIFYING（REJECTED 不是死路）。"""
        self.profile.latest_kyc_status = KycStatus.REJECTED
        self.profile.save()

        resp = self._submit()

        self.assertIn(resp.status_code, (200, 201))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.VERIFYING)

    def test_history_preserved_across_resubmit(self):
        """重送覆寫的是 profile 當前值，歷史層仍留得住「上一次交了什麼」。"""
        # 第一次送審（名字 A）-> admin 拒絕
        self._submit(dict(SUBMIT_BODY, legal_name="舊名字A"))
        self._as(self.admin)
        self.client.post(reject_url(self.user), {"reason": "資料有誤"}, format="json")
        # 用戶修正後重送（名字 B）
        self._as(self.user)
        self._submit(dict(SUBMIT_BODY, legal_name="新名字B"))

        self.profile.refresh_from_db()
        # 當前層：只剩最新值
        self.assertEqual(self.profile.legal_name, "新名字B")
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.VERIFYING)
        # 歷史層：舊名字仍查得到，且事件序完整（SUBMITTED, REJECTED, SUBMITTED）
        self.assertTrue(
            self._records(self.user, KycEvent.SUBMITTED).filter(legal_name="舊名字A").exists()
        )
        self.assertEqual(self._records(self.user).count(), 3)

    # ---- 重新 KYC：情況二（已通過後打回）----
    # revoke（撤銷）與 reverify（要求重驗）狀態轉移相同（APPROVED -> UNVERIFIED），只差記的事件。
    def test_admin_revoke_writes_revoked_event(self):
        """staff revoke 已通過用戶 -> UNVERIFIED，歷史多一筆 REVOKED（含 reason）。"""
        self.profile.latest_kyc_status = KycStatus.APPROVED
        self.profile.save()
        self._as(self.admin)

        resp = self.client.post(revoke_url(self.user), {"reason": "涉嫌詐欺"}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.UNVERIFIED)
        rec = self._records(self.user, KycEvent.REVOKED).latest("created_at")
        self.assertEqual(rec.reason, "涉嫌詐欺")
        # 不可誤記成另一個事件
        self.assertFalse(self._records(self.user, KycEvent.REVERIFY_REQUIRED).exists())

    def test_admin_reverify_writes_reverify_event(self):
        """staff reverify 已通過用戶 -> UNVERIFIED，歷史多一筆 REVERIFY_REQUIRED（含 reason）。"""
        self.profile.latest_kyc_status = KycStatus.APPROVED
        self.profile.save()
        self._as(self.admin)

        resp = self.client.post(reverify_url(self.user), {"reason": "證件到期"}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.UNVERIFIED)
        rec = self._records(self.user, KycEvent.REVERIFY_REQUIRED).latest("created_at")
        self.assertEqual(rec.reason, "證件到期")
        self.assertFalse(self._records(self.user, KycEvent.REVOKED).exists())

    def test_reject_without_reason_rejected(self):
        """reject 沒帶 reason -> 400（reason 必填），狀態不變。"""
        self._submit()  # -> VERIFYING
        self._as(self.admin)

        resp = self.client.post(reject_url(self.user), {}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.VERIFYING)

    def test_revoke_without_reason_rejected(self):
        """revoke 沒帶 reason -> 400（reason 必填），狀態不變。"""
        self.profile.latest_kyc_status = KycStatus.APPROVED
        self.profile.save()
        self._as(self.admin)

        resp = self.client.post(revoke_url(self.user), {}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.APPROVED)

    def test_reverify_without_reason_rejected(self):
        """reverify 沒帶 reason -> 400（reason 必填），狀態不變。"""
        self.profile.latest_kyc_status = KycStatus.APPROVED
        self.profile.save()
        self._as(self.admin)

        resp = self.client.post(reverify_url(self.user), {}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.latest_kyc_status, KycStatus.APPROVED)

    def test_revoke_non_approved_rejected(self):
        """對非 APPROVED 的人 revoke -> 400（沒通過過，談不上撤銷）。帶 reason 以確保 400 來自狀態守衛。"""
        self._as(self.admin)

        resp = self.client.post(revoke_url(self.user), {"reason": "x"}, format="json")

        self.assertEqual(resp.status_code, 400)

    def test_reverify_non_approved_rejected(self):
        """對非 APPROVED 的人 reverify -> 400（同一道守衛）。帶 reason 以確保 400 來自狀態守衛。"""
        self._as(self.admin)

        resp = self.client.post(reverify_url(self.user), {"reason": "x"}, format="json")

        self.assertEqual(resp.status_code, 400)

    def test_normal_user_cannot_revoke_or_reverify(self):
        """一般用戶不能 revoke / reverify 別人 -> 403。"""
        self.other_profile.latest_kyc_status = KycStatus.APPROVED
        self.other_profile.save()

        resp_revoke = self.client.post(revoke_url(self.other), {}, format="json")
        resp_reverify = self.client.post(reverify_url(self.other), {}, format="json")

        self.assertEqual(resp_revoke.status_code, 403)
        self.assertEqual(resp_reverify.status_code, 403)

    # ---- 查自己 ----
    def test_me_returns_own_status(self):
        """GET /me/ 回自己的 KYC 狀態。"""
        resp = self.client.get(KYC_ME_URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["latest_kyc_status"], KycStatus.UNVERIFIED.label)

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


class KycRecordAppendOnlyTest(APITestCase):
    """KycRecordModel 的 append-only 契約（照 LedgerEntryModel 同款）。"""

    def setUp(self):
        self.user = User.objects.create(username="trader")

    def test_cannot_update_existing_record(self):
        """已存在的 record 不可更新 -> ValueError。"""
        rec = KycRecordModel.objects.create(
            user=self.user, event_status=KycEvent.SUBMITTED, legal_name="王小明"
        )
        rec.legal_name = "被竄改"
        with self.assertRaises(ValueError):
            rec.save()

    def test_cannot_delete_record(self):
        """record 不可刪除 -> ValueError。"""
        rec = KycRecordModel.objects.create(user=self.user, event_status=KycEvent.SUBMITTED)
        with self.assertRaises(ValueError):
            rec.delete()


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

    def _set_status(self, status):
        self.profile.latest_kyc_status = status
        self.profile.save()

    def test_withdraw_blocked_when_unverified(self):
        """KYC 未通過 -> 出金 403，餘額完全不變。"""
        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 403)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))
        self.assertEqual(self.wallet.frozen_balance, D(0))

    def test_withdraw_blocked_when_pending(self):
        """審核中（VERIFYING）仍不能出金 -> 403。"""
        self._set_status(KycStatus.VERIFYING)

        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 403)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))

    def test_withdraw_allowed_when_approved(self):
        """KYC 通過後 -> 出金 200，可用餘額正常扣減。"""
        self._set_status(KycStatus.APPROVED)

        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 200)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(70000))

    def test_withdraw_blocked_again_after_revoke(self):
        """通過後被 revoke（重驗）-> 閘門重新關上，出金 403。"""
        self._set_status(KycStatus.APPROVED)
        self._set_status(KycStatus.UNVERIFIED)  # 模擬 revoke 後的當前狀態

        resp = self._withdraw(30000)

        self.assertEqual(resp.status_code, 403)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))

    def test_gate_runs_before_balance_checks(self):
        """閘門在動錢之前：未通過 KYC 就算金額超額，也回 403（資格先於輸入）。"""
        resp = self._withdraw(999999)  # 遠超餘額

        self.assertEqual(resp.status_code, 403)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, D(100000))
