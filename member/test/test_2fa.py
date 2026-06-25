"""
M7-B — 2FA（TOTP）流程測試。

本專案採「強制 2FA」：使用者註冊後拿到 TOTP 密鑰，必須先「啟用」(輸入一次正確碼)，
之後登入都要附上當下的 TOTP 碼，才會發 JWT。

端點（都在 /api/user/ 底下、皆免登入）：
  - POST /api/user/register/  註冊 → 回 {username, secret, issuer, qrcode_link}
  - PUT  /api/user/register/  啟用 2FA，body {username, totp}
  - POST /api/user/login/     登入，body {username, password, totp} → {access, refresh}

注意：測試用 pyotp 以註冊回傳的 secret 自算當下正確碼，不需 mock 任何外部服務。
前提：verify_totp 需設 valid_window=1，否則跨 30 秒邊界時本檔可能間歇性失敗。
"""

import pyotp
from rest_framework.test import APITestCase

REGISTER_URL = "/api/user/register/"
LOGIN_URL = "/api/user/login/"


class TwoFactorFlowTest(APITestCase):
    def setUp(self):
        self.username = "alice"
        self.password = "s3cret-pass-123"

    # ---- 輔助 ----
    def _register(self):
        """註冊一個新用戶，回傳註冊 response。"""
        payload = {
            "username": self.username,
            "password": self.password,
            "phone_number": "0900000000",
            "address": "Taipei",
        }
        return self.client.post(REGISTER_URL, payload, format="json")

    def _code(self, secret):
        """用 secret 算出當下正確的 TOTP 碼。"""
        return pyotp.TOTP(secret).now()

    def _enable(self, secret):
        """啟用 2FA。"""
        return self.client.put(
            REGISTER_URL, {"username": self.username, "totp": self._code(secret)}, format="json"
        )

    def _login(self, totp=None):
        body = {"username": self.username, "password": self.password}
        if totp is not None:
            body["totp"] = totp
        return self.client.post(LOGIN_URL, body, format="json")

    # ---- 測試 ----
    def test_register_returns_secret_and_qr(self):
        """註冊成功 → 回傳 secret 與 otpauth QR 連結;此時 2FA 尚未啟用。"""
        resp = self._register()

        self.assertIn(resp.status_code, (200, 201))
        data = resp.json()
        self.assertEqual(data["username"], self.username)
        self.assertIn("secret", data)
        self.assertTrue(data["qrcode_link"].startswith("otpauth://"))

        from member.models import UserProfileModel
        profile = UserProfileModel.objects.get(user__username=self.username)
        self.assertFalse(profile.two_factor_enabled)

    def test_enable_2fa_with_correct_code(self):
        """輸入正確 TOTP 碼 → 啟用成功，profile.two_factor_enabled 變 True。"""
        secret = self._register().json()["secret"]

        resp = self._enable(secret)

        self.assertEqual(resp.status_code, 200)
        from member.models import UserProfileModel
        profile = UserProfileModel.objects.get(user__username=self.username)
        self.assertTrue(profile.two_factor_enabled)

    def test_enable_2fa_wrong_code_rejected(self):
        """錯誤的碼 → 啟用失敗 400，仍維持未啟用。"""
        self._register()

        resp = self.client.put(
            REGISTER_URL, {"username": self.username, "totp": "000000"}, format="json"
        )

        self.assertEqual(resp.status_code, 400)
        from member.models import UserProfileModel
        profile = UserProfileModel.objects.get(user__username=self.username)
        self.assertFalse(profile.two_factor_enabled)

    def test_login_before_enable_rejected(self):
        """強制 2FA：還沒啟用就登入 → 400（尚未啟用 2FA）。"""
        secret = self._register().json()["secret"]

        resp = self._login(totp=self._code(secret))

        self.assertEqual(resp.status_code, 400)

    def test_login_success_with_2fa(self):
        """註冊 → 啟用 → 帶當下正確碼登入 → 200，拿到 access + refresh。"""
        secret = self._register().json()["secret"]
        self._enable(secret)

        resp = self._login(totp=self._code(secret))

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("access", data)
        self.assertIn("refresh", data)

    def test_login_wrong_totp_rejected(self):
        """已啟用，但 TOTP 碼錯 → 400。"""
        secret = self._register().json()["secret"]
        self._enable(secret)

        resp = self._login(totp="000000")

        self.assertEqual(resp.status_code, 400)

    def test_login_missing_totp_rejected(self):
        """已啟用，但沒帶 totp → 400（totp 是必填欄位）。"""
        secret = self._register().json()["secret"]
        self._enable(secret)

        resp = self._login(totp=None)

        self.assertEqual(resp.status_code, 400)

    def test_login_wrong_password_rejected(self):
        """密碼錯 → 401（帳密驗證先擋下，輪不到 2FA）。"""
        secret = self._register().json()["secret"]
        self._enable(secret)

        resp = self.client.post(
            LOGIN_URL,
            {"username": self.username, "password": "wrong-pass", "totp": self._code(secret)},
            format="json",
        )

        self.assertEqual(resp.status_code, 401)
