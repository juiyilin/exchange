"""
M-RBAC — 身份組與權限（角色層）契約測試。〔規格：09-1_permission_spec.md〕

本檔是「正確行為」的定義（測試即規格）,實作由使用者完成、讓本檔變綠。
它測的是**角色層**：不同角色（trader/support/compliance/admin）打同一端點,誰該過、誰該擋。
擁有權層（只能碰自己的）在 test_withdraw / test_orders / test_kyc 已測,本檔不重複。

------- 四角色（見規格 09-1 §2）-------
Group.name 存**英文穩定碼**（`Role` 的 value:trader/support/compliance/admin,永不變）,
中文只是**顯示標籤**（`Role` 的 label）——改中文名不動 Group.name、不會多出重複 group。
程式一律以 member.constants.Role 常數引用（`Role.TRADER` 在字串情境即 "trader"）。
  交易者（Role.TRADER）      一般交易用戶,新註冊自動歸入。只碰自己的（擁有權層）,無跨用戶/管理權。
  客服（Role.SUPPORT）       唯讀跨用戶（用戶/KYC/錢包）。不能改、不能審、不能動錢。
  合規（Role.COMPLIANCE）    專責 KYC 審核（review_kyc）。可唯讀用戶/KYC。不能動錢、不能管用戶。
  管理員（Role.ADMIN）       系統與金流（跨用戶 CRUD、入金 can_deposit）。**刻意不含 KYC 審核權**。

------- 這些測試依賴 `sync_roles`（規格 09-1 §4）-------
角色與權限由 `member/rbac.py` 的 ROLES + `sync_roles()`（掛 post_migrate）建立。
測試 DB 建好時 post_migrate 會跑 → 四個 Group 連同權限就緒。所以下面直接
`Group.objects.get(name=...)` 取得角色再指派給測試用戶。**實作 sync_roles 之前,本檔會整片紅,
這是預期的**（測試先定契約,實作讓它變綠）。

------- 踩過的坑（見規格 09-1 §7）-------
`user.has_perm(...)` 第一次呼叫會把權限快取在 user 物件上。務必「加入 group」之後才發第一個請求,
否則快取住空權限、角色測試會假紅。本檔一律在 setUp 指派好 group,才在各測試發請求。
"""

from decimal import Decimal

from django.contrib.auth.models import User, Group
from rest_framework.test import APITestCase

from currency.models import CurrencyModel
from member.constants import KycStatus, Role
from member.models import UserProfileModel, WalletModel


USER_LIST_URL = "/api/user/user/"
WALLET_URL = "/api/user/wallet/"
DEPOSIT_URL = "/api/user/wallet/deposit/"
KYC_URL = "/api/user/kyc/"


def D(x):
    return Decimal(str(x))


def kyc_retrieve_url(user):
    # KYCViewSet lookup_field='user_id'
    return f"/api/user/kyc/{user.id}/"


def approve_url(user):
    return f"/api/user/kyc/{user.id}/approve/"


def rows_of(resp):
    """相容分頁與非分頁：回傳列表本體。"""
    data = resp.json()
    return data["results"] if isinstance(data, dict) and "results" in data else data


def make_user(username, group_name=None, **kwargs):
    user = User.objects.create(username=username, **kwargs)
    if group_name:
        user.groups.add(Group.objects.get(name=group_name))
    return user


class UserViewSetRoleTest(APITestCase):
    """`/api/user/user/`：純管理型資源,套 read-gating（見規格 09-1 §3 / 09-1 §7）。

    讀（GET）要 auth.view_user → support/compliance/admin 可讀,trader 讀不到。
    寫（此處以 DELETE 代表,對應 auth.delete_user）只有 admin 能做。
    """

    def setUp(self):
        self.trader = make_user("trader", Role.TRADER)
        self.support = make_user("support", Role.SUPPORT)
        self.compliance = make_user("compliance", Role.COMPLIANCE)
        self.admin = make_user("admin", Role.ADMIN)

    # ---- 讀取閘門 ----
    def test_anonymous_list_401(self):
        """未登入 → 401（JWT 有 WWW-Authenticate,是 401 不是 403）。"""
        self.assertEqual(self.client.get(USER_LIST_URL).status_code, 401)

    def test_trader_cannot_list_users(self):
        """★核心★ trader 沒有 view_user → 查全站用戶 403。"""
        self.client.force_authenticate(self.trader)
        self.assertEqual(self.client.get(USER_LIST_URL).status_code, 403)

    def test_support_can_read_users(self):
        """support 有 view_user → 唯讀查全站 200。"""
        self.client.force_authenticate(self.support)
        self.assertEqual(self.client.get(USER_LIST_URL).status_code, 200)

    def test_compliance_can_read_users(self):
        self.client.force_authenticate(self.compliance)
        self.assertEqual(self.client.get(USER_LIST_URL).status_code, 200)

    def test_admin_can_read_users(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(USER_LIST_URL).status_code, 200)

    # ---- 寫入閘門（DELETE 對應 delete_user）----
    def test_support_cannot_delete_user(self):
        """★核心★ support 是唯讀:能看不能改。刪用戶 → 403。

        這條是 read-gating 存在的理由:沒有它,support 要嘛看不到、要嘛連刪都能刪,
        做不出「看得到但不能動手」。
        """
        target = make_user("victim")
        self.client.force_authenticate(self.support)
        resp = self.client.delete(f"{USER_LIST_URL}{target.id}/")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(User.objects.filter(id=target.id).exists())

    def test_trader_cannot_delete_user(self):
        target = make_user("victim")
        self.client.force_authenticate(self.trader)
        self.assertEqual(
            self.client.delete(f"{USER_LIST_URL}{target.id}/").status_code, 403
        )

    def test_admin_can_delete_user(self):
        """admin 有 delete_user → 刪用戶 204。"""
        target = make_user("victim")
        self.client.force_authenticate(self.admin)
        resp = self.client.delete(f"{USER_LIST_URL}{target.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(User.objects.filter(id=target.id).exists())


class WalletRoleTest(APITestCase):
    """`/api/user/wallet/`：擁有權型資源。

    owner 永遠讀得到自己的（不套 read-gating）;角色層只決定「能不能額外看到別人的」——
    有 view_walletmodel（support/admin）看全站,沒有的（trader/compliance）只看自己。
    入金 deposit 要 can_deposit → admin-only。
    """

    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")

        self.trader = make_user("trader", Role.TRADER)
        self.support = make_user("support", Role.SUPPORT)
        self.compliance = make_user("compliance", Role.COMPLIANCE)
        self.admin = make_user("admin", Role.ADMIN)

        # 兩個不同人的錢包,總共 2 筆,用來驗「看全站 vs 看自己」。
        self.holder = make_user("holder")
        WalletModel.objects.create(user=self.trader, asset_type=self.usdt,
                                   available_balance=D(100), frozen_balance=D(0))
        WalletModel.objects.create(user=self.holder, asset_type=self.btc,
                                   available_balance=D(1), frozen_balance=D(0))

    # ---- see-all vs own ----
    def test_trader_sees_only_own_wallet(self):
        """trader 沒有 view_walletmodel → 只看得到自己的那 1 筆。"""
        self.client.force_authenticate(self.trader)
        resp = self.client.get(WALLET_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(rows_of(resp)), 1)

    def test_support_sees_all_wallets(self):
        """support 有 view_walletmodel → 看得到全站 2 筆（客服查餘額）。"""
        self.client.force_authenticate(self.support)
        resp = self.client.get(WALLET_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(rows_of(resp)), 2)

    def test_compliance_does_not_see_all_wallets(self):
        """compliance 沒有 view_walletmodel（最小權限:合規管身份、不管錢）→ 只看自己（0 筆）。"""
        self.client.force_authenticate(self.compliance)
        resp = self.client.get(WALLET_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(rows_of(resp)), 0)

    def test_admin_sees_all_wallets(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(WALLET_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(rows_of(resp)), 2)

    # ---- 入金閘門（can_deposit → admin-only）----
    def _deposit_body(self):
        return {"user_id": self.holder.id, "asset_type_id": self.usdt.id, "quantity": "50"}

    def test_admin_can_deposit(self):
        """admin 有 can_deposit → 入金 200。"""
        self.client.force_authenticate(self.admin)
        resp = self.client.post(DEPOSIT_URL, self._deposit_body(), format="json")
        self.assertEqual(resp.status_code, 200)

    def test_trader_cannot_deposit(self):
        """★核心★ 入金＝憑空增加餘額,一般用戶不能碰 → 403。"""
        self.client.force_authenticate(self.trader)
        self.assertEqual(
            self.client.post(DEPOSIT_URL, self._deposit_body(), format="json").status_code, 403
        )

    def test_support_cannot_deposit(self):
        """support 唯讀,不能動錢 → 403。"""
        self.client.force_authenticate(self.support)
        self.assertEqual(
            self.client.post(DEPOSIT_URL, self._deposit_body(), format="json").status_code, 403
        )

    def test_compliance_cannot_deposit(self):
        """compliance 管身份不管錢 → 403。"""
        self.client.force_authenticate(self.compliance)
        self.assertEqual(
            self.client.post(DEPOSIT_URL, self._deposit_body(), format="json").status_code, 403
        )


class KycReadRoleTest(APITestCase):
    """`/api/user/kyc/` 的跨用戶查詢（list/retrieve）：read-gating,要 view_userprofilemodel。

    送審/查自己（create、me）是本人操作、任何登入者對自己都能做,不在本檔（見 test_kyc）。
    """

    def setUp(self):
        self.trader = make_user("trader", Role.TRADER)
        self.support = make_user("support", Role.SUPPORT)
        self.compliance = make_user("compliance", Role.COMPLIANCE)
        self.admin = make_user("admin", Role.ADMIN)
        # 一個被查對象
        self.target = make_user("target")
        UserProfileModel.objects.create(user=self.target)

    def test_anonymous_list_401(self):
        self.assertEqual(self.client.get(KYC_URL).status_code, 401)

    def test_trader_cannot_list_kyc(self):
        """★核心★ trader 沒有 view_userprofilemodel → 查全站 KYC 403。"""
        self.client.force_authenticate(self.trader)
        self.assertEqual(self.client.get(KYC_URL).status_code, 403)

    def test_trader_cannot_retrieve_other_kyc(self):
        self.client.force_authenticate(self.trader)
        self.assertEqual(self.client.get(kyc_retrieve_url(self.target)).status_code, 403)

    def test_support_can_read_kyc(self):
        """support 唯讀查 KYC（協助客戶）→ 200。"""
        self.client.force_authenticate(self.support)
        self.assertEqual(self.client.get(KYC_URL).status_code, 200)

    def test_compliance_can_read_kyc(self):
        self.client.force_authenticate(self.compliance)
        self.assertEqual(self.client.get(KYC_URL).status_code, 200)

    def test_admin_can_read_kyc(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(KYC_URL).status_code, 200)


class KycReviewRoleTest(APITestCase):
    """KYC 審核（approve/reject/revoke/reverify）：要 review_kyc → **compliance 專屬**。

    重點是職責分離（規格 09-1 §5）:admin 管金流/系統,**刻意沒有審核權**,
    避免「同一角色又放行帳號又能動錢」。唯一繞得過的是 Django superuser（誠實標註）。
    """

    def setUp(self):
        self.trader = make_user("trader", Role.TRADER)
        self.support = make_user("support", Role.SUPPORT)
        self.compliance = make_user("compliance", Role.COMPLIANCE)
        self.admin = make_user("admin", Role.ADMIN)
        self.root = make_user("root", is_superuser=True, is_staff=True)

        # 被審對象:狀態 VERIFYING,approve 才有意義。
        self.target = make_user("target")
        self.target_profile = UserProfileModel.objects.create(
            user=self.target, latest_kyc_status=KycStatus.VERIFYING
        )

    def _approve_as(self, actor):
        self.client.force_authenticate(actor)
        return self.client.post(approve_url(self.target), {}, format="json")

    def test_compliance_can_approve(self):
        """compliance 有 review_kyc → approve 200,對象變 APPROVED。"""
        resp = self._approve_as(self.compliance)
        self.assertEqual(resp.status_code, 200)
        self.target_profile.refresh_from_db()
        self.assertEqual(self.target_profile.latest_kyc_status, KycStatus.APPROVED)

    def test_admin_cannot_approve(self):
        """★核心★ admin 沒有 review_kyc → approve 403（職責分離:動錢的人不審身份）。

        權限層先擋下,狀態不變。
        """
        resp = self._approve_as(self.admin)
        self.assertEqual(resp.status_code, 403)
        self.target_profile.refresh_from_db()
        self.assertEqual(self.target_profile.latest_kyc_status, KycStatus.VERIFYING)

    def test_support_cannot_approve(self):
        resp = self._approve_as(self.support)
        self.assertEqual(resp.status_code, 403)

    def test_trader_cannot_approve(self):
        resp = self._approve_as(self.trader)
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_cannot_approve(self):
        resp = self.client.post(approve_url(self.target), {}, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_superuser_bypasses(self):
        """誠實標註:Django superuser 天生繞過所有權限檢查,仍能審核 → 200。

        RBAC 擋的是一般 staff 帳號的越權,不是 superuser;superuser 要靠作業紀律嚴管
        （帳號極少）。見規格 09-1 §5。
        """
        resp = self._approve_as(self.root)
        self.assertEqual(resp.status_code, 200)
        self.target_profile.refresh_from_db()
        self.assertEqual(self.target_profile.latest_kyc_status, KycStatus.APPROVED)


REGISTER_URL = "/api/user/register/"


class RegisterAutoJoinsTraderTest(APITestCase):
    """註冊自動歸「交易者」群組（見規格 09-1 §4「成員關係不寫進 ROLES_PERMISSIONS」）。

    這是 M-RBAC 唯一沒被其他測試覆蓋到的實作點:`RegisterView.create` 要把新用戶
    `user.groups.add(Group.objects.get(name=Role.TRADER))`。沒接的話,新註冊用戶不屬於任何角色,
    read-gating 下什麼都看不到、也不會被當交易者——這條就是那個缺口的回歸網。
    免登入端點,不需 force_authenticate。
    """

    def test_new_user_auto_joins_trader_group(self):
        resp = self.client.post(
            REGISTER_URL,
            {
                "username": "newcomer",
                "password": "pw-newcomer-123",
                "phone_number": "0900000000",
                "address": "Taipei",
            },
            format="json",
        )
        self.assertIn(resp.status_code, (200, 201), resp.content)

        user = User.objects.get(username="newcomer")
        self.assertTrue(
            user.groups.filter(name=Role.TRADER).exists(),
            "新註冊用戶應自動加入「交易者」群組",
        )
