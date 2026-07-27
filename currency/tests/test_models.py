"""
M1 — 幣別 model 行為測試。

這些是「現況該有的行為」的測試，你的程式碼大部分應該已經能通過
（CurrencyModel 已加了 save() 自動大寫與 __str__）。
"""

from django.test import TestCase

from currency.models import CurrencyModel, TradingPairModel


class CurrencyModelTest(TestCase):
    def test_code_is_uppercased_on_save(self):
        """save() 應把 code 自動轉大寫，避免 btc / BTC 被當兩種幣。"""
        c = CurrencyModel.objects.create(code="btc", name="Bitcoin")
        c.refresh_from_db()
        self.assertEqual(c.code, "BTC")

    def test_str_representation(self):
        """__str__ 應顯示成「名稱(代碼)」，後台下拉才好讀。"""
        c = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.assertEqual(str(c), "Tether(USDT)")

    def test_code_is_unique(self):
        """code 不可重複。"""
        from django.db import IntegrityError, transaction

        CurrencyModel.objects.create(code="ETH", name="Ethereum")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                CurrencyModel.objects.create(code="ETH", name="Ethereum 2")


class TradingPairModelTest(TestCase):
    """
    幣對(TradingPair)model 行為測試。

    幣對 = base(標的幣) + quote(計價幣),例如 BTC/USDT 代表「用 USDT 買賣 BTC」。
    這份測試定義 TradingPairModel 該有的行為,實作時請讓底下每條都變綠:

      1. symbol 由 base/quote 的 code 自動組成「BASE/QUOTE」,save() 時產生,不必手動傳。
      2. __str__ 顯示 symbol(後台下拉好讀)。
      3. is_active 預設 True。
      4. base 與 quote 相同 → save() 丟出 ValueError(同幣不能成對)。
      5. (base, quote) 不可重複(unique_together)。
    """

    def setUp(self):
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")

    def test_symbol_auto_generated_from_base_quote(self):
        """symbol 應由 base/quote 自動組成「BTC/USDT」,不需手動指定。"""
        pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        pair.refresh_from_db()
        self.assertEqual(pair.symbol, "BTC/USDT")

    def test_str_is_symbol(self):
        """__str__ 應回傳 symbol。"""
        pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        self.assertEqual(str(pair), "BTC/USDT")

    def test_is_active_defaults_true(self):
        """is_active 預設為 True(新幣對預設開放交易)。"""
        pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        self.assertTrue(pair.is_active)

    def test_base_equals_quote_rejected(self):
        """base 與 quote 相同 → save() 丟 ValueError(不能用同一種幣自己配自己)。"""
        with self.assertRaises(ValueError):
            TradingPairModel.objects.create(
                base_currency=self.btc, quote_currency=self.btc
            )

    def test_base_quote_pair_is_unique(self):
        """同一組 (base, quote) 不可重複建立。"""
        from django.db import IntegrityError, transaction

        TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                TradingPairModel.objects.create(
                    base_currency=self.btc, quote_currency=self.usdt
                )
