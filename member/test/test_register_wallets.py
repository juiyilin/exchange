"""
M-KYC 暖身（一）— 註冊時順帶建立初始錢包。

## 這一階段要做什麼

現況：`RegisterSerializer.create` 只建 User + UserProfileModel，新用戶登入後
一個錢包都沒有。雖然 `transfer_asset` / `deposit` 都有 `get_or_create` 兜底不會炸，
但「註冊完錢包列表是空的」對前端不友善。

目標：註冊時讓使用者**勾選要開哪些幣別的錢包**，一併建出來（餘額 0）。

## API 契約

POST /api/user/register/（免登入）
body:
    {
        "username": "alice",
        "password": "...",
        "phone_number": "0900000000",
        "address": "Taipei",
        "wallet_currency_ids": [1, 2]      # ← 本階段新增，選填
    }

- `wallet_currency_ids` 是一組 `CurrencyModel.id`（與 `WithdrawSerializer` /
  `DepositSerializer` 用 `asset_type_id` 的風格一致）。
- 選填：不帶、帶空陣列 → 不建任何錢包，註冊照樣成功。
- 回應格式**維持 M7-B 現況**（username / secret / issuer / qrcode_link），
  本階段不改。錢包建了沒，用 `GET /api/user/wallet/` 或直接查 DB 驗證。

## 為什麼是「勾選」而不是寫死 USDT/BTC

**幣別是資料，不是常數。** 若在 `create()` 裡寫死 `code="USDT"`、`code="BTC"`，
你哪天上架 ETH 就得回頭改註冊邏輯；而且測試環境沒建 USDT 幣別時註冊會直接炸。
讓呼叫端指定，註冊邏輯就與「目前上架哪些幣」解耦。

## 實作提示（不給程式碼，給方向）

- 欄位加在 `RegisterSerializer`：一個 `write_only` 的 list 欄位。
  想想用 `PrimaryKeyRelatedField(many=True, queryset=...)` 還是
  `ListField(child=IntegerField())` + 自己 validate——前者 DRF 會**自動幫你驗
  「幣別存在嗎」並回 400**，後者要自己寫。（提示：前者比較省事，且錯誤訊息較標準。）
- `required=False` 讓它變選填。
- `create()` 裡記得 `validated_data.pop(...)`，否則會被當成 User 的欄位傳進
  `User.objects.create_user(**validated_data)` → TypeError。這跟現有的
  `phone_number` / `address` 是同一個坑。
- 原子性：`RegisterView.create` 已經有 `@transaction.atomic`，所以你不用另外包。
  但要理解**它為什麼在那裡**——見 `test_invalid_currency_rolls_back_everything`。
- 重複 id 的去重：想想 `set()` 或 `bulk_create(..., ignore_conflicts=True)`，
  但注意 `unique_together` 的錯誤是在 DB 層爆的，讓它爆到用戶臉上不是好體驗。

前提：全套測試以 `member/test/test_2fa.py` 的註冊流程為基礎，同樣不需 mock 外部服務。
"""

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from currency.models import CurrencyModel
from member.models import WalletModel

REGISTER_URL = "/api/user/register/"


class RegisterWithWalletsTest(APITestCase):
    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.username = "alice"
        self.password = "s3cret-pass-123"

    # ---- 輔助 ----
    def _register(self, wallet_currency_ids=None, username=None):
        payload = {
            "username": username or self.username,
            "password": self.password,
            "phone_number": "0900000000",
            "address": "Taipei",
        }
        if wallet_currency_ids is not None:
            payload["wallet_currency_ids"] = wallet_currency_ids
        return self.client.post(REGISTER_URL, payload, format="json")

    # ---- 測試 ----
    def test_register_creates_selected_wallets(self):
        """勾選 USDT + BTC → 註冊成功，兩個錢包都建出來。"""
        resp = self._register(wallet_currency_ids=[self.usdt.id, self.btc.id])

        self.assertIn(resp.status_code, (200, 201))
        user = User.objects.get(username=self.username)
        wallets = WalletModel.objects.filter(user=user)
        self.assertEqual(wallets.count(), 2)
        self.assertCountEqual(
            list(wallets.values_list("asset_type__code", flat=True)),
            ["USDT", "BTC"],
        )

    def test_created_wallets_are_empty(self):
        """新錢包餘額必須是 0——註冊不等於入金。

        白送餘額是「憑空鑄錢」，跟 admin-only 入金端點擋的是同一件事
        （見 07-1 §4.1 / TASKS.md「為什麼入金是 admin-only」）。
        """
        self._register(wallet_currency_ids=[self.usdt.id])

        wallet = WalletModel.objects.get(user__username=self.username)
        self.assertEqual(wallet.available_balance, 0)
        self.assertEqual(wallet.frozen_balance, 0)

    def test_register_with_subset_of_currencies(self):
        """只勾 USDT → 只建 USDT 錢包，不會自作主張多建 BTC。"""
        self._register(wallet_currency_ids=[self.usdt.id])

        wallets = WalletModel.objects.filter(user__username=self.username)
        self.assertEqual(wallets.count(), 1)
        self.assertEqual(wallets.first().asset_type, self.usdt)

    def test_register_without_field_creates_no_wallet(self):
        """完全不帶 wallet_currency_ids → 註冊照樣成功，只是沒有錢包。

        這條保證欄位是「選填」，也保證 M7-B 既有的註冊呼叫端不會因為本階段而壞掉。
        """
        resp = self._register()

        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(User.objects.filter(username=self.username).exists())
        self.assertEqual(WalletModel.objects.filter(user__username=self.username).count(), 0)

    def test_register_with_empty_list_creates_no_wallet(self):
        """帶空陣列 → 等同不帶，註冊成功、0 個錢包。"""
        resp = self._register(wallet_currency_ids=[])

        self.assertIn(resp.status_code, (200, 201))
        self.assertEqual(WalletModel.objects.filter(user__username=self.username).count(), 0)

    def test_duplicate_currency_ids_create_one_wallet(self):
        """同一幣別重複勾選 → 只建一個錢包，不可炸 unique_together。

        為什麼要測：`unique_together = (user, asset_type)` 是「餘額不會分裂」的
        最後防線（02-1 §7）。前端多送一次同樣的 id 是很常見的事，
        這種輸入應該被安靜地正規化掉，而不是回 500。
        """
        resp = self._register(wallet_currency_ids=[self.usdt.id, self.usdt.id])

        self.assertIn(resp.status_code, (200, 201))
        self.assertEqual(WalletModel.objects.filter(user__username=self.username).count(), 1)

    def test_invalid_currency_rejected(self):
        """勾了不存在的幣別 id → 400。"""
        resp = self._register(wallet_currency_ids=[99999])

        self.assertEqual(resp.status_code, 400)

    def test_invalid_currency_rolls_back_everything(self):
        """★重點★ 幣別 id 非法 → User 與 Profile 都不可留下半成品。

        這是在測 `RegisterView.create` 的 `@transaction.atomic` 有沒有真的生效。
        想像沒有它會怎樣：User 建好了、Profile 建好了，然後建錢包時炸掉 →
        資料庫留下一個「有帳號、沒錢包、但也沒拿到 TOTP secret 回應」的殭屍用戶。
        使用者重試註冊會撞 username 已存在，帳號等於被自己卡死。

        「要嘛全部成功，要嘛什麼都沒發生」——這就是原子性。
        你在 M3 結算學過同一件事（四個錢包要嘛一起動、要嘛都不動）：
        **同一條原則，換個場景。**
        """
        resp = self._register(wallet_currency_ids=[self.usdt.id, 99999])

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(username=self.username).exists())
        self.assertEqual(WalletModel.objects.count(), 0)

    def test_wallets_belong_to_the_registering_user(self):
        """錢包要掛在「剛註冊的那個人」身上，不可掛錯人。

        M7 之前的 `get_random_user()` 就是掛錯人的慣犯，這條是回歸網。
        """
        self._register(wallet_currency_ids=[self.usdt.id], username="alice")
        self._register(wallet_currency_ids=[self.btc.id], username="bob")

        alice_wallets = WalletModel.objects.filter(user__username="alice")
        bob_wallets = WalletModel.objects.filter(user__username="bob")
        self.assertEqual(alice_wallets.count(), 1)
        self.assertEqual(bob_wallets.count(), 1)
        self.assertEqual(alice_wallets.first().asset_type, self.usdt)
        self.assertEqual(bob_wallets.first().asset_type, self.btc)

    def test_register_response_unchanged(self):
        """回應格式維持 M7-B 現況（secret / qrcode_link 還在）。

        本階段只加輸入欄位，不動輸出。這條擋的是「順手把 to_representation 改壞」。
        """
        resp = self._register(wallet_currency_ids=[self.usdt.id])

        data = resp.json()
        self.assertEqual(data["username"], self.username)
        self.assertIn("secret", data)
        self.assertTrue(data["qrcode_link"].startswith("otpauth://"))
