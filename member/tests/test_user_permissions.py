"""
M-KYC 暖身（二）— UserViewSet 權限收斂。〔規格：02-1 §6.5〕

## 這是在修一個真的資安洞

`member/views.py` 的 `UserViewSet` 目前長這樣：

    class UserViewSet(ModelViewSet):
        # TODO:
        queryset = User.objects.select_related("profile").all()
        serializer_class = UserListSerializer

沒有 `get_queryset` 過濾、沒有 `permission_classes` 覆寫，所以它只吃到全域的
`IsAuthenticated`——意思是**任何一個登入的普通用戶，都能 `GET /api/user/user/`
撈出全站所有人的 username、email、is_staff、電話、地址**（`UserListSerializer`
是 `exclude = ["password"]`，除了密碼什麼都給）。

對照一下：`WalletViewSet` 在 M7 就做了 `get_queryset` 過濾 `request.user`，
訂單也做了。**只有 User 這張表漏掉。** 這就是 M7 留下的尾巴。

在真實交易所，用戶名單外洩是重大事故：它讓攻擊者拿到完整的目標清單
（可以拿去撞庫、釣魚），而 `is_staff` 欄位等於直接告訴攻擊者「先打哪幾個帳號
最有價值」。這也是為什麼下一階段的 KYC 資料（身分證號、證件照）**權限只會更嚴**
——本階段等於先把地基打好。

## 目標

- `list` / `retrieve` 限 `IsAdminUser`（TASKS.md 既有決議）。
- 未登入一律 401。
- 這裡在做的是 **02-1 §6.5 的「角色層」**：管的是「你能不能做這種操作」，
  跟 `WalletViewSet` 那種「你只能碰自己的資料」（擁有權層）是**兩個正交的維度**。
  本階段先用最粗的角色（is_staff），完整 RBAC（Django Group）留給 M-RBAC。

## 實作提示

- 最小改動：在 `UserViewSet` 掛 `permission_classes = [IsAdminUser]`，順手把
  那行 `# TODO:` 刪掉。
- `IsAdminUser` 檢查的是 `request.user.is_staff`（不是 `is_superuser`，名字有點誤導）。
- 注意 DRF 對「未登入」與「登入但沒權限」的回應碼不同——見
  `test_anonymous_gets_401` 的 docstring。
- **注意 `RegisterView` 不受影響**：註冊走的是獨立的 `/api/user/register/`，
  它自己設了 `authentication_classes = []` / `permission_classes = []`。
  這正是 M7-B 當初把註冊拆出來的好處：現在要鎖 `UserViewSet` 完全不會鎖死註冊。
  （`test_register_still_open` 是這條的保險絲。）

## 一個懸而未決的問題（本階段不做，但你該想）

鎖成 admin-only 之後，**一般用戶要怎麼查自己的資料？** 現在沒有任何端點能做到。
慣例解法是加一個 `GET /api/user/user/me/`（`@action(detail=False)`，回
`request.user` 自己）。本階段先不做——因為 KYC 階段會需要「查自己的 KYC 狀態」，
屆時一起設計比較不會改兩次。先把洞補上。
"""

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from member.models import UserProfileModel

USER_LIST_URL = "/api/user/user/"
REGISTER_URL = "/api/user/register/"


class UserViewSetPermissionTest(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw-alice-123")
        self.bob = User.objects.create_user(username="bob", password="pw-bob-123")
        self.admin = User.objects.create_user(
            username="admin", password="pw-admin-123", is_staff=True
        )
        for user in (self.alice, self.bob, self.admin):
            UserProfileModel.objects.create(user=user)

    # ---- 未登入 ----
    def test_anonymous_gets_401(self):
        """未登入 → 401，不是 403。

        DRF 的慣例：帶了認證但權限不足是 403（你是誰我知道，但你不能做這件事）；
        完全沒帶認證則看有沒有 WWW-Authenticate header——JWT 有，所以是 401
        （你先表明身分再說）。這個區分是 HTTP 語意，不是 DRF 自己發明的。
        """
        resp = self.client.get(USER_LIST_URL)

        self.assertEqual(resp.status_code, 401)

    def test_anonymous_retrieve_gets_401(self):
        resp = self.client.get(f"{USER_LIST_URL}{self.alice.id}/")

        self.assertEqual(resp.status_code, 401)

    # ---- 一般用戶 ----
    def test_normal_user_cannot_list_users(self):
        """★核心★ 一般用戶列出全站用戶 → 403。這條現在是紅的，就是那個洞。"""
        self.client.force_authenticate(user=self.alice)

        resp = self.client.get(USER_LIST_URL)

        self.assertEqual(resp.status_code, 403)

    def test_normal_user_cannot_retrieve_other_user(self):
        """一般用戶查別人 → 403。"""
        self.client.force_authenticate(user=self.alice)

        resp = self.client.get(f"{USER_LIST_URL}{self.bob.id}/")

        self.assertEqual(resp.status_code, 403)

    def test_normal_user_cannot_retrieve_even_self(self):
        """一般用戶查「自己」也是 403。

        看起來不合直覺，但這是本階段刻意的取捨：`IsAdminUser` 是**角色層**判斷，
        它只問「你是不是 staff」，不問「你要查的是不是自己」。
        要讓用戶查自己，正解是另開 `/me/` 端點（見檔頭「懸而未決的問題」），
        而不是把 retrieve 放寬成「可以查自己」——後者會讓一個端點同時承擔
        兩種權限語意，日後很難維護。

        **權限不足時回 403 而不是 404，本身就是資訊洩漏的取捨。** 嚴格來說
        「403 全站用戶列表」等於承認這個端點存在。本專案接受這個程度，
        知道有這回事即可。
        """
        self.client.force_authenticate(user=self.alice)

        resp = self.client.get(f"{USER_LIST_URL}{self.alice.id}/")

        self.assertEqual(resp.status_code, 403)

    def test_normal_user_cannot_create_user_via_viewset(self):
        """一般用戶不能用這個端點建用戶 → 403。（註冊有自己的專屬端點。）"""
        self.client.force_authenticate(user=self.alice)

        resp = self.client.post(
            USER_LIST_URL, {"username": "mallory", "password": "pw-123"}, format="json"
        )

        self.assertEqual(resp.status_code, 403)

    # ---- admin ----
    def test_admin_can_list_all_users(self):
        """staff → 200，且看得到全部三個用戶。

        注意這裡跟 `WalletViewSet.get_queryset` 的 staff 分支是同一種思路：
        管理者看全站，一般人看自己。
        """
        self.client.force_authenticate(user=self.admin)

        resp = self.client.get(USER_LIST_URL)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        usernames = [u["username"] for u in (data["results"] if isinstance(data, dict) else data)]
        self.assertCountEqual(usernames, ["alice", "bob", "admin"])

    def test_admin_can_retrieve_any_user(self):
        self.client.force_authenticate(user=self.admin)

        resp = self.client.get(f"{USER_LIST_URL}{self.alice.id}/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "alice")

    def test_password_never_exposed(self):
        """就算是 admin，回應也不可含密碼雜湊。

        `UserListSerializer` 用 `exclude = ["password"]` 已經擋掉了，
        這條是回歸網——擋的是哪天有人把它改成 `fields = "__all__"`。
        """
        self.client.force_authenticate(user=self.admin)

        resp = self.client.get(f"{USER_LIST_URL}{self.alice.id}/")

        self.assertNotIn("password", resp.json())

    # ---- 不可誤傷註冊 ----
    def test_register_still_open(self):
        """★重要★ 鎖了 UserViewSet 之後，註冊端點必須仍然免登入可用。

        這條擋的是經典死鎖：把用戶相關端點全鎖成要登入 → 新用戶無法註冊 →
        永遠沒有帳號可以登入。02-1 §6.5 結尾那段警告講的就是這件事。

        本專案因為 M7-B 已經把註冊拆成獨立的 `RegisterView`（自帶
        `authentication_classes = []`），所以天生免疫。這條測試是把
        「天生免疫」固定下來，免得日後有人把註冊搬回 UserViewSet 又踩一次。
        """
        resp = self.client.post(
            REGISTER_URL,
            {
                "username": "newcomer",
                "password": "pw-newcomer-123",
                "phone_number": "0911111111",
                "address": "Taipei",
            },
            format="json",
        )

        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(User.objects.filter(username="newcomer").exists())
