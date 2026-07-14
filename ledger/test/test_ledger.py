"""
M-日誌與帳本 — 單元測試（這是「規格」，不是實作）

============================================================================
這份測試定義了 `ledger` app 該有的行為，請寫實作讓底下每條測試變綠。
完整設計見 docs/07-1_logging_audit_spec.md。實作 checklist 見 07 §6.2。

核心契約：
  (A) LedgerEntryModel 是 append-only 的帳本流水：只新增、不可更新、不可刪除。
  (B) 每動一個錢包的一個 balance_field(AVAILABLE/FROZEN)，就在同一個 atomic 內寫一筆 LedgerEntryModel。
      欄位：reason、balance_field、delta(帶正負)、balance_after(變動後快照)、ref_type/ref_id(軟參照)。
  (C) 對帳不變量（最重要）：任一錢包
        available_balance == Σ(delta where balance_field=AVAILABLE)
        frozen_balance    == Σ(delta where balance_field=FROZEN)
      前提是「從零開始、所有變動都有記」——所以對帳測試用會寫 log 的 deposit() 注資。

套用點（07 §5；reason 對照）：
  下單凍結 transfer_to_frozen → FREEZE
  撮合結算 transfer_asset     → SETTLE（收=AVAILABLE+，付=FROZEN−，靠 balance_field+正負區分）
  取消     release_frozen     → UNFREEZE   (order.status == CANCELED)
  多凍退款 release_frozen     → REFUND     (order.status == FULLY_FILLED)
  出金     withdraw           → WITHDRAW
注意：release_frozen 在「實際差額為 0」時不要寫 entry（避免 0-delta 雜訊）——
      見 LedgerOnSettleTest.test_exact_price_fill_writes_no_refund。

choices 一律用字串值比對（"FREEZE"/"AVAILABLE"…），不綁實作的 enum 類別位置。
============================================================================
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APITestCase

from currency.models import CurrencyModel, TradingPairModel
from member.models import WalletModel
from transaction.constants import OrderStatus, OrderType
from transaction.models import OrderModel

from ledger.models import LedgerEntryModel  # 實作前這行會 import 失敗 → 測試紅，屬預期


def D(x):
    return Decimal(str(x))


ORDER_URL = "/api/transaction/order/"
WITHDRAW_URL = "/api/user/wallet/withdraw/"


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class LedgerBaseTestCase(APITestCase):
    """共用 setUp 與輔助函式（沿用既有測試的 wallet/place 慣例）。"""

    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        self.alice = User.objects.create(username="alice")
        self.bob = User.objects.create(username="bob")

    # ---- 注資 ----
    def fund(self, user, currency, available):
        """純設定錢包餘額，不寫 LedgerEntryModel。用於只檢查「某操作自己寫了哪些 entry」的測試。"""
        return WalletModel.objects.create(
            user=user, asset_type=currency, available_balance=D(available)
        )

    def deposit(self, user, currency, amount):
        """模擬「會記帳的入金」：available += amount 並寫一筆 DEPOSIT。用於對帳測試。"""
        amount = D(amount)
        wallet, _ = WalletModel.objects.get_or_create(user=user, asset_type=currency)
        wallet.available_balance += amount
        wallet.save()
        LedgerEntryModel.objects.create(
            user=user, asset_type=currency,
            reason="DEPOSIT", balance_field="AVAILABLE",
            delta=amount, balance_after=wallet.available_balance,
            ref_type="manual", ref_id="",
        )
        return wallet

    # ---- 查詢 ----
    def get_wallet(self, user, currency):
        w = WalletModel.objects.get(user=user, asset_type=currency)
        w.refresh_from_db()
        return w

    def entries(self, user, currency, **filters):
        return LedgerEntryModel.objects.filter(
            user=user, asset_type=currency, **filters
        ).order_by("id")

    def one_entry(self, user, currency, **filters):
        qs = self.entries(user, currency, **filters)
        self.assertEqual(qs.count(), 1, f"預期剛好 1 筆 entry，實際 {qs.count()}：{filters}")
        return qs.first()

    # ---- 下單 / 出金（走真 API，才會觸發實作裡的記帳）----
    def _payload(self, order_type, quantity, price):
        return {
            "trading_pair": self.pair.id,
            "quantity": str(quantity),
            "price": str(price),
            "order_type": order_type,
        }

    def place(self, user, order_type, quantity, price):
        self.client.force_authenticate(user=user)
        resp = self.client.post(
            ORDER_URL, self._payload(order_type, quantity, price), format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp

    def cancel(self, order, as_user=None):
        self.client.force_authenticate(user=as_user or order.user)
        return self.client.post(f"{ORDER_URL}{order.pk}/cancel/", format="json")

    def withdraw(self, user, currency, quantity):
        self.client.force_authenticate(user=user)
        return self.client.post(
            WITHDRAW_URL,
            {"asset_type_id": currency.id, "quantity": str(quantity)},
            format="json",
        )


# ============================================================================
# (A) LedgerEntryModel 模型契約：append-only
# ============================================================================
class LedgerEntryModelTest(LedgerBaseTestCase):
    def _make(self):
        return LedgerEntryModel.objects.create(
            user=self.bob, asset_type=self.usdt,
            reason="DEPOSIT", balance_field="AVAILABLE",
            delta=D(100), balance_after=D(100),
            ref_type="manual", ref_id="",
        )

    def test_create_persists_fields(self):
        e = self._make()
        e.refresh_from_db()
        self.assertEqual(e.reason, "DEPOSIT")
        self.assertEqual(e.balance_field, "AVAILABLE")
        self.assertEqual(e.delta, D(100))
        self.assertEqual(e.balance_after, D(100))

    def test_update_is_blocked(self):
        """append-only：更新既有列要被擋下（覆寫 save() raise）。"""
        e = self._make()
        e.delta = D(999)
        with self.assertRaises(Exception):
            e.save()
        e.refresh_from_db()
        self.assertEqual(e.delta, D(100))  # 沒被改掉

    def test_delete_is_blocked(self):
        """append-only：刪除要被擋下（覆寫 delete() raise）。"""
        e = self._make()
        with self.assertRaises(Exception):
            e.delete()
        self.assertTrue(LedgerEntryModel.objects.filter(pk=e.pk).exists())


# ============================================================================
# (B) 下單凍結 → FREEZE（兩筆：AVAILABLE −、FROZEN +）
# ============================================================================
class LedgerOnFreezeTest(LedgerBaseTestCase):
    def test_buy_freeze_writes_two_entries(self):
        """買 1@30000：凍 30000 USDT → FREEZE/AVAILABLE −30000、FREEZE/FROZEN +30000。"""
        self.fund(self.bob, self.usdt, "100000")
        self.place(self.bob, OrderType.BUY, 1, 30000)  # 無對手，不會成交

        avail = self.one_entry(self.bob, self.usdt, reason="FREEZE", balance_field="AVAILABLE")
        frozen = self.one_entry(self.bob, self.usdt, reason="FREEZE", balance_field="FROZEN")
        self.assertEqual(avail.delta, D(-30000))
        self.assertEqual(avail.balance_after, D(70000))
        self.assertEqual(frozen.delta, D(30000))
        self.assertEqual(frozen.balance_after, D(30000))

    def test_sell_freeze_writes_two_entries(self):
        """賣 2@30000：凍 2 BTC → FREEZE/AVAILABLE −2、FREEZE/FROZEN +2。"""
        self.fund(self.alice, self.btc, "5")
        self.place(self.alice, OrderType.SELL, 2, 30000)

        avail = self.one_entry(self.alice, self.btc, reason="FREEZE", balance_field="AVAILABLE")
        frozen = self.one_entry(self.alice, self.btc, reason="FREEZE", balance_field="FROZEN")
        self.assertEqual(avail.delta, D(-2))
        self.assertEqual(avail.balance_after, D(3))
        self.assertEqual(frozen.delta, D(2))
        self.assertEqual(frozen.balance_after, D(2))


# ============================================================================
# (B) 撮合結算 → SETTLE（買賣雙方共四筆；收/付靠 balance_field + delta 正負區分）
# ============================================================================
class LedgerOnSettleTest(LedgerBaseTestCase):
    def test_exact_price_fill_writes_settle_entries(self):
        """
        賣 1@30000(maker) × 買 1@30000(taker)，成交價 30000：
          買方 bob：SETTLE BTC AVAILABLE +1   、SETTLE USDT FROZEN −30000
          賣方 alice：SETTLE BTC FROZEN −1     、SETTLE USDT AVAILABLE +30000
        """
        self.fund(self.alice, self.btc, "5")
        self.fund(self.bob, self.usdt, "100000")
        self.place(self.alice, OrderType.SELL, 1, 30000)  # maker，先掛
        self.place(self.bob, OrderType.BUY, 1, 30000)     # taker

        # 買方收到 BTC（available +1）
        b_in = self.one_entry(self.bob, self.btc, reason="SETTLE", balance_field="AVAILABLE")
        self.assertEqual(b_in.delta, D(1))
        self.assertEqual(b_in.balance_after, D(1))
        # 買方付出 USDT（frozen −30000）
        b_out = self.one_entry(self.bob, self.usdt, reason="SETTLE", balance_field="FROZEN")
        self.assertEqual(b_out.delta, D(-30000))
        self.assertEqual(b_out.balance_after, D(0))

        # 賣方付出 BTC（frozen −1）
        s_out = self.one_entry(self.alice, self.btc, reason="SETTLE", balance_field="FROZEN")
        self.assertEqual(s_out.delta, D(-1))
        self.assertEqual(s_out.balance_after, D(0))
        # 賣方收到 USDT（available +30000）
        s_in = self.one_entry(self.alice, self.usdt, reason="SETTLE", balance_field="AVAILABLE")
        self.assertEqual(s_in.delta, D(30000))
        self.assertEqual(s_in.balance_after, D(30000))

    def test_exact_price_fill_writes_no_refund(self):
        """成交價 == 掛價時沒有多凍，release_frozen 差額為 0 → 不可寫 REFUND/UNFREEZE entry。"""
        self.fund(self.alice, self.btc, "5")
        self.fund(self.bob, self.usdt, "100000")
        self.place(self.alice, OrderType.SELL, 1, 30000)
        self.place(self.bob, OrderType.BUY, 1, 30000)

        self.assertFalse(
            LedgerEntryModel.objects.filter(reason__in=["REFUND", "UNFREEZE"]).exists()
        )


# ============================================================================
# (B) 取消 → UNFREEZE（FROZEN −、AVAILABLE +）
# ============================================================================
class LedgerOnCancelTest(LedgerBaseTestCase):
    def test_cancel_writes_unfreeze_entries(self):
        """買 1@30000 後取消：UNFREEZE/FROZEN −30000、UNFREEZE/AVAILABLE +30000。"""
        self.fund(self.bob, self.usdt, "100000")
        self.place(self.bob, OrderType.BUY, 1, 30000)
        buy = OrderModel.objects.get(user=self.bob, order_type=OrderType.BUY)

        resp = self.cancel(buy)
        self.assertEqual(resp.status_code, 200, resp.content)

        un_frozen = self.one_entry(self.bob, self.usdt, reason="UNFREEZE", balance_field="FROZEN")
        un_avail = self.one_entry(self.bob, self.usdt, reason="UNFREEZE", balance_field="AVAILABLE")
        self.assertEqual(un_frozen.delta, D(-30000))
        self.assertEqual(un_frozen.balance_after, D(0))
        self.assertEqual(un_avail.delta, D(30000))
        self.assertEqual(un_avail.balance_after, D(100000))


# ============================================================================
# (B) 多凍退款 → REFUND（買單以低於掛價成交、FULLY_FILLED 時退多凍）
# ============================================================================
class LedgerOnOverFreezeRefundTest(LedgerBaseTestCase):
    def test_buy_below_price_full_fill_writes_refund(self):
        """
        賣 1@29000(maker) × 買 1@30000(taker)，成交價 29000：
        bob 凍 30000、實花 29000 → FULLY_FILLED 時退多凍 1000：
          REFUND/FROZEN −1000(→0)、REFUND/AVAILABLE +1000(→71000)。
        """
        self.fund(self.alice, self.btc, "5")
        self.fund(self.bob, self.usdt, "100000")
        self.place(self.alice, OrderType.SELL, 1, 29000)
        self.place(self.bob, OrderType.BUY, 1, 30000)

        buy = OrderModel.objects.get(user=self.bob, order_type=OrderType.BUY)
        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)

        r_frozen = self.one_entry(self.bob, self.usdt, reason="REFUND", balance_field="FROZEN")
        r_avail = self.one_entry(self.bob, self.usdt, reason="REFUND", balance_field="AVAILABLE")
        self.assertEqual(r_frozen.delta, D(-1000))
        self.assertEqual(r_frozen.balance_after, D(0))
        self.assertEqual(r_avail.delta, D(1000))
        self.assertEqual(r_avail.balance_after, D(71000))


# ============================================================================
# (B) 出金 → WITHDRAW（AVAILABLE −）
# ============================================================================
class LedgerOnWithdrawTest(LedgerBaseTestCase):
    def test_withdraw_writes_entry(self):
        """出金 5000 USDT → WITHDRAW/AVAILABLE −5000(→95000)。"""
        self.fund(self.bob, self.usdt, "100000")
        resp = self.withdraw(self.bob, self.usdt, 5000)
        self.assertEqual(resp.status_code, 200, resp.content)

        w = self.one_entry(self.bob, self.usdt, reason="WITHDRAW", balance_field="AVAILABLE")
        self.assertEqual(w.delta, D(-5000))
        self.assertEqual(w.balance_after, D(95000))


# ============================================================================
# (C) 對帳不變量（最重要）：跑一連串真實流程後，每個錢包都對得上帳
# ============================================================================
class LedgerReconciliationTest(LedgerBaseTestCase):
    def assert_reconciled(self):
        """每個錢包：available == Σ(AVAILABLE delta)、frozen == Σ(FROZEN delta)；
        且最後一筆 entry 的 balance_after == 當前餘額。"""
        for w in WalletModel.objects.all():
            for field, current in (
                ("AVAILABLE", w.available_balance),
                ("FROZEN", w.frozen_balance),
            ):
                qs = LedgerEntryModel.objects.filter(
                    user=w.user, asset_type=w.asset_type, balance_field=field
                ).order_by("id")
                total = sum((e.delta for e in qs), Decimal("0"))
                self.assertEqual(
                    total, current,
                    f"{w.user.username}/{w.asset_type.code}/{field}: "
                    f"Σdelta={total} 但錢包={current}",
                )
                last = qs.last()
                if last is not None:
                    self.assertEqual(
                        last.balance_after, current,
                        f"{w.user.username}/{w.asset_type.code}/{field}: "
                        f"最後 balance_after={last.balance_after} 但錢包={current}",
                    )

    def test_full_lifecycle_reconciles(self):
        """
        注資(記帳) → 部分成交 → 不交叉掛單 → 取消，全程跑完後對帳。
        涵蓋 FREEZE / SETTLE / UNFREEZE / DEPOSIT 各路徑。
        """
        # 用會記帳的 deposit() 注資，對帳基準才完整
        self.deposit(self.bob, self.usdt, "100000")
        self.deposit(self.alice, self.btc, "5")

        # alice 掛賣 2@30000(maker)，bob 買 1@30000(taker) → 成交 1，alice 部分成交、bob 全成交
        self.place(self.alice, OrderType.SELL, 2, 30000)
        self.place(self.bob, OrderType.BUY, 1, 30000)

        # bob 再掛一張不交叉的買 0.5@25000 → PENDING，之後取消
        self.place(self.bob, OrderType.BUY, "0.5", 25000)
        pending = OrderModel.objects.get(user=self.bob, price=D(25000))
        resp = self.cancel(pending)
        self.assertEqual(resp.status_code, 200, resp.content)

        # 收款錢包（alice 的 USDT、bob 的 BTC）由結算自動建立並記 SETTLE
        self.assertTrue(WalletModel.objects.filter(user=self.alice, asset_type=self.usdt).exists())
        self.assertTrue(WalletModel.objects.filter(user=self.bob, asset_type=self.btc).exists())

        self.assert_reconciled()
