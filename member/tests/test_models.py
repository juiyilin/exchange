"""
M1 — 用戶與錢包 model 行為測試。

重點驗證錢包的兩個鐵則：
  1. 每人每幣只能有一個錢包（unique_together），避免餘額分裂。
  2. 餘額預設為 0。
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from currency.models import CurrencyModel
from member.models import WalletModel


class WalletModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="tester")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")

    def test_default_balances_are_zero(self):
        w = WalletModel.objects.create(user=self.user, asset_type=self.btc)
        self.assertEqual(w.available_balance, Decimal(0))
        self.assertEqual(w.frozen_balance, Decimal(0))

    def test_one_wallet_per_user_per_currency(self):
        """同一人同一幣建第二個錢包應被 unique_together 擋下。"""
        WalletModel.objects.create(user=self.user, asset_type=self.btc)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                WalletModel.objects.create(user=self.user, asset_type=self.btc)

    def test_same_currency_different_users_ok(self):
        """不同用戶可以各自有同一幣別的錢包。"""
        other = User.objects.create(username="tester2")
        WalletModel.objects.create(user=self.user, asset_type=self.btc)
        WalletModel.objects.create(user=other, asset_type=self.btc)
        self.assertEqual(WalletModel.objects.filter(asset_type=self.btc).count(), 2)
