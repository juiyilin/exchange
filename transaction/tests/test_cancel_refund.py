"""
M4 訂單生命週期 — 單元測試（這是「規格」，不是實作）

============================================================================
這份測試定義了 M4 三件事該有的行為，請寫實作讓底下每條測試變綠：

  (1) 取消訂單   POST /api/transaction/order/{pk}/cancel/
        - 只有 PENDING / PARTIALLY_FILLED 能取消；終態(FULLY_FILLED/CANCELED)回 400。
        - 取消時把「這張單還凍著、不會再花掉」的差額，從 frozen 搬回 available。
        - 狀態改 CANCELED。整段包 transaction.atomic，鎖 order(select_for_update)
          以與撮合互斥；鎖到後要「重新檢查狀態」。

  (2) 多凍結退款（買單終態時退「原凍結 − 實際花費」差額）
        - 買單以「低於掛價」成交時會多凍 QUOTE。當這張買單變 FULLY_FILLED，
          要把多凍的退回可用。與取消共用同一個釋放函式。
        - 賣單不需要（凍的是固定數量的 BASE）。

  (3) 擋改單
        - OrderViewSet 關掉 PUT/PATCH（http_method_names），打了回 405。

------- 統一公式（取消與退款共用）-------
一張單「還凍著、之後不會再花掉」的差額 =
    買單： quantity*price − Σ(每筆成交 t.quantity * t.price)
    賣單： quantity      − Σ(每筆成交 t.quantity)
這條差額同時涵蓋「未成交剩餘」與「成交部分的多凍」。
建議寫成 member.models.WalletQuerySet.release_frozen(order)（與 transfer_asset 作伴），
取消 action 與撮合走到買單 FULLY_FILLED 時都呼叫它。

防重複退：FULLY_FILLED 與 CANCELED 互斥、終態不可變，每張單只會退一次——
前提是 (1) 的「鎖後重檢狀態」與 (2) 的「只在剛轉 FULLY_FILLED 時退」都確實做到。
============================================================================
"""

from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from currency.models import CurrencyModel, TradingPairModel
from member.models import WalletModel
from transaction.constants import OrderStatus, OrderType
from transaction.models import OrderModel, TransactionModel
from transaction.services import match_order


def D(x):
    return Decimal(str(x))


ORDER_URL = "/api/transaction/order/"


class CancelRefundBaseTestCase(APITestCase):
    """共用 setUp 與輔助函式（沿用 test_matching.py 的 wallet/place 慣例）。"""

    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        self.alice = User.objects.create(username="alice")
        self.bob = User.objects.create(username="bob")

    # ---- 輔助函式 ----
    def wallet(self, user, currency, available, frozen=0):
        return WalletModel.objects.create(
            user=user,
            asset_type=currency,
            available_balance=D(available),
            frozen_balance=D(frozen),
        )

    def get_wallet(self, user, currency):
        w = WalletModel.objects.get(user=user, asset_type=currency)
        w.refresh_from_db()
        return w

    def place(self, user, side, amount, price, ordered_at=None):
        """模擬「下單並凍結餘額」：建 PENDING 訂單並把該凍的從 available 搬到 frozen。"""
        amount, price = D(amount), D(price)
        if side == OrderType.SELL:
            freeze_amount, freeze_currency = amount, self.btc          # 賣→凍 base(BTC)
        else:
            freeze_amount, freeze_currency = amount * price, self.usdt  # 買→凍 quote(USDT)

        w = self.get_wallet(user, freeze_currency)
        w.available_balance -= freeze_amount
        w.frozen_balance += freeze_amount
        w.save()

        kwargs = dict(
            user=user, trading_pair=self.pair, quantity=amount, price=price,
            order_type=side, status=OrderStatus.PENDING,
        )
        if ordered_at is not None:
            kwargs["ordered_at"] = ordered_at
        return OrderModel.objects.create(**kwargs)

    def cancel(self, order, as_user=None):
        # 全域 IsAuthenticated + cancel 綁擁有者：預設以該單擁有者身分取消，
        # 傳 as_user 可模擬「別人來取消」。
        self.client.force_authenticate(user=as_user or order.user)
        return self.client.post(f"{ORDER_URL}{order.pk}/cancel/", format="json")


# ============================================================================
# (1) 取消：未成交單，全額退凍
# ============================================================================
class CancelPendingOrderTest(CancelRefundBaseTestCase):
    def test_cancel_pending_buy_refunds_all(self):
        """掛買 1@30000（凍 30000 USDT）後取消 → 全退、狀態 CANCELED。"""
        self.wallet(self.bob, self.usdt, "100000")
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)
        # 凍結後：可用 70000、凍結 30000
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(30000))

        resp = self.cancel(buy)

        self.assertEqual(resp.status_code, 200)
        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.CANCELED)
        self.assertEqual(self.get_wallet(self.bob, self.usdt).available_balance, D(100000))
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(0))

    def test_cancel_pending_sell_refunds_all(self):
        """掛賣 1@30000（凍 1 BTC）後取消 → 全退、狀態 CANCELED。"""
        self.wallet(self.alice, self.btc, "5")
        sell = self.place(self.alice, OrderType.SELL, 1, 30000)
        self.assertEqual(self.get_wallet(self.alice, self.btc).frozen_balance, D(1))

        resp = self.cancel(sell)

        self.assertEqual(resp.status_code, 200)
        sell.refresh_from_db()
        self.assertEqual(sell.status, OrderStatus.CANCELED)
        self.assertEqual(self.get_wallet(self.alice, self.btc).available_balance, D(5))
        self.assertEqual(self.get_wallet(self.alice, self.btc).frozen_balance, D(0))


# ============================================================================
# (1) 取消：部分成交單，只退剩餘
# ============================================================================
class CancelPartiallyFilledTest(CancelRefundBaseTestCase):
    def test_cancel_partially_filled_buy_refunds_remaining(self):
        """
        賣 0.4@30000（maker）對買 1@30000（taker）→ 買成交 0.4、PARTIALLY_FILLED。
        買方凍 30000、花 0.4*30000=12000、剩凍 18000。
        取消買單 → 退回 18000，USDT 可用回到 88000、凍結 0、狀態 CANCELED。
        已拿到的 0.4 BTC 不受影響。
        """
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")

        self.place(self.alice, OrderType.SELL, "0.4", 30000)   # maker，先掛
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)    # taker
        match_order(buy.pk, buy.trading_pair.id)

        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(18000))

        resp = self.cancel(buy)

        self.assertEqual(resp.status_code, 200)
        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.CANCELED)
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(0))
        self.assertEqual(self.get_wallet(self.bob, self.usdt).available_balance, D(88000))
        self.assertEqual(self.get_wallet(self.bob, self.btc).available_balance, D("0.4"))


# ============================================================================
# (2) 多凍結退款：買單以低於掛價成交、FULLY_FILLED 後自動退多凍
# ============================================================================
class OverFreezeRefundOnFullFillTest(CancelRefundBaseTestCase):
    def test_buy_full_fill_below_price_refunds_overfreeze(self):
        """
        賣 1@29000（maker，先掛）對買 1@30000（taker）→ 成交價＝maker 29000。
        買方凍 30000、實花 29000 → 買單 FULLY_FILLED 時要退多凍的 1000。
        結果：USDT 凍結 0、可用 71000（100000−29000）、拿到 1 BTC。
        """
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")

        self.place(self.alice, OrderType.SELL, 1, 29000)   # maker，較早
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)  # taker
        match_order(buy.pk, buy.trading_pair.id)

        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)
        # 重點：多凍的 1000 已退回可用
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(0))
        self.assertEqual(self.get_wallet(self.bob, self.usdt).available_balance, D(71000))
        self.assertEqual(self.get_wallet(self.bob, self.btc).available_balance, D(1))


# ============================================================================
# (2) 多凍結退款：先當 taker 部分成交（撿便宜→多凍），之後當 maker 成交完
#     —— 釘死「maker 進終態也要退多凍」這條盲點（曾因 release_frozen 擺在
#         mark_maker_status 之前而 no-op）
# ============================================================================
class OverFreezeTakerThenMakerTest(CancelRefundBaseTestCase):
    def test_overfreeze_refunded_when_order_fills_as_maker(self):
        """
        bob 買 1@31000（凍 31000）。
        第一步（bob 當 taker）：吃便宜的賣 0.5@29000 → 花 14500、凍剩 16500、
            bob PARTIALLY_FILLED 掛在簿上（這 0.5 有多凍但還活著，先不退）。
        第二步（bob 當 maker）：carol 賣 0.5@30000 進來吃 bob 的剩餘，成交價＝
            maker(bob) 的 31000 → 花 15500、凍剩 1000，bob 變 FULLY_FILLED。
        重點：bob 進終態時，第一步留下的多凍 1000 必須退回可用。
        最終 bob：BTC 1、USDT 凍結 0、USDT 可用 70000（100000−14500−15500）。
        """
        carol = User.objects.create(username="carol")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(carol, self.btc, "5")
        self.wallet(carol, self.usdt, "0")

        # 第一步：alice 便宜賣 maker，bob 買 taker 部分成交
        self.place(self.alice, OrderType.SELL, "0.5", 29000)
        buy = self.place(self.bob, OrderType.BUY, 1, 31000)
        match_order(buy.pk, buy.trading_pair.id)

        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.PARTIALLY_FILLED)
        # 還活著，剩餘＋多凍都還凍著，不退
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(16500))

        # 第二步：carol 賣 taker，吃掉 bob 剩餘 → bob 變 maker 並 FULLY_FILLED
        sell = self.place(carol, OrderType.SELL, "0.5", 30000)
        match_order(sell.pk, sell.trading_pair.id)

        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)
        # 多凍的 1000 已退：凍結 0、可用 70000、拿到 1 BTC
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(0))
        self.assertEqual(self.get_wallet(self.bob, self.usdt).available_balance, D(70000))
        self.assertEqual(self.get_wallet(self.bob, self.btc).available_balance, D(1))


# ============================================================================
# (1) 終態不可再取消；防重複退
# ============================================================================
class CancelTerminalRejectedTest(CancelRefundBaseTestCase):
    def test_cancel_fully_filled_returns_400(self):
        """完全成交的單再取消 → 400，餘額不變。"""
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")

        self.place(self.alice, OrderType.SELL, 1, 30000)
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)
        match_order(buy.pk, buy.trading_pair.id)
        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)

        before_avail = self.get_wallet(self.bob, self.usdt).available_balance
        before_frozen = self.get_wallet(self.bob, self.usdt).frozen_balance

        resp = self.cancel(buy)

        self.assertEqual(resp.status_code, 400)
        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)  # 狀態沒變
        self.assertEqual(self.get_wallet(self.bob, self.usdt).available_balance, before_avail)
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, before_frozen)

    def test_cancel_twice_does_not_double_refund(self):
        """連取消兩次：第二次回 400，且只退一次（餘額不會多退）。"""
        self.wallet(self.bob, self.usdt, "100000")
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)

        first = self.cancel(buy)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.get_wallet(self.bob, self.usdt).available_balance, D(100000))

        second = self.cancel(buy)
        self.assertEqual(second.status_code, 400)
        # 沒有第二次退款：可用仍是 100000、凍結仍是 0
        self.assertEqual(self.get_wallet(self.bob, self.usdt).available_balance, D(100000))
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(0))

    def test_other_user_cannot_cancel(self):
        """別人不能取消你的單 → 400（綁 user，看不到當作不存在）、不退款、狀態不變。"""
        self.wallet(self.bob, self.usdt, "100000")
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)

        resp = self.cancel(buy, as_user=self.alice)  # alice 想取消 bob 的單

        self.assertEqual(resp.status_code, 400)
        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.PENDING)
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(30000))


# ============================================================================
# (3) 擋改單
# ============================================================================
class BlockUpdateTest(CancelRefundBaseTestCase):
    def _detail_url(self, order):
        return f"{ORDER_URL}{order.pk}/"

    def _payload(self):
        return {
            "trading_pair": self.pair.id,
            "quantity": "999",
            "price": "1",
            "order_type": OrderType.BUY,
        }

    def test_put_not_allowed(self):
        self.wallet(self.bob, self.usdt, "100000")
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)
        self.client.force_authenticate(user=self.bob)  # 先登入，否則回 401 而非 405
        resp = self.client.put(self._detail_url(buy), self._payload(), format="json")
        self.assertEqual(resp.status_code, 405)

    def test_patch_not_allowed(self):
        self.wallet(self.bob, self.usdt, "100000")
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)
        self.client.force_authenticate(user=self.bob)
        resp = self.client.patch(self._detail_url(buy), {"quantity": "999"}, format="json")
        self.assertEqual(resp.status_code, 405)
