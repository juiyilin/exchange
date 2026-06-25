"""
M3 撮合引擎 — 單元測試（這是「規格」，不是實作）

============================================================================
這份測試定義了「正確的撮合 + 結算」應該有的行為。
撮合進入點 transaction.tasks.match_order(order_id, trading_pair_id)，讓底下每一條測試都變綠。

------- match_order(order_id, trading_pair_id) 的契約（測試假設它這樣運作）-------
輸入：order_id 是一張「剛下、且已凍結好餘額」的訂單（taker）的 pk。
       （凍結是下單時做的；在測試裡我們用 place() 幫你模擬凍結好的狀態。）
       trading_pair_id 是該訂單所屬交易對的 pk；撮合會先 select_for_update 鎖住
       這個交易對，確保同一交易對的撮合彼此序列化（避免並發撮合互踩）。
行為：
  1. 找出「同一個交易對、相反方向、價格能交叉、狀態為 PENDING/PARTIALLY_FILLED」
     的對手單，依「價格優先、時間其次」排序。
       - 幫買單找賣單：賣單.trading_pair==買單.trading_pair 且 order_type 相反，
         且 賣價 <= 買價；排序＝賣價低→高、ordered_at 早→晚。
       - 幫賣單找買單：對稱；排序＝買價高→低、ordered_at 早→晚。
  2. 逐筆配對，成交量 = min(我方待成交量, 對手待成交量)。
  3. 成交價 = maker（先掛在簿上那方）的價格。
  4. 每筆成交：
       - 建立 TransactionModel(buy_order=買單, sell_order=賣單, quantity=成交量, price=成交價)
       - 結算四個錢包（見下）
       - 更新雙方訂單 status：完全成交→FULLY_FILLED；部分→PARTIALLY_FILLED
  5. 我方吃飽就停；沒吃飽就找下一個；對手掃完仍有剩，剩餘量留在簿上。

------- 結算規則（一筆成交：買方 B、賣方 S、數量 q、成交價 p）-------
  買方 B 的 USDT 錢包：frozen -= q*p
  買方 B 的 BTC  錢包：available += q
  賣方 S 的 BTC  錢包：frozen -= q
  賣方 S 的 USDT 錢包：available += q*p
  （整筆成交：建 Transaction + 改四錢包 + 改兩單狀態，包在同一個 transaction.atomic）

  注意：本階段(M3)「買方以低於掛價成交」產生的多凍結，先不退款（那是 M4）。
  為了避開這個情況，凡是會牽涉買方多凍結的測試，這裡都刻意用「買單當 maker」
  的情境，讓買方凍結金額剛好等於成交金額，結算後 frozen 歸零、沒有殘留。
============================================================================
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from currency.models import CurrencyModel, TradingPairModel
from member.models import WalletModel
from transaction.constants import OrderStatus, OrderType
from transaction.models import OrderModel, TransactionModel

# 撮合進入點：傳入 taker 訂單的 pk，在交易內完成撮合與結算。
from transaction.tasks import match_order


def D(x):
    """方便把字串/數字轉成 Decimal。"""
    return Decimal(str(x))


class MatchingBaseTestCase(TestCase):
    """共用的 setUp 與輔助函式。"""

    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        # 標的幣 BTC、計價幣 USDT；所有測試訂單都掛在這個幣對上
        self.pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        self.alice = User.objects.create(username="alice")
        self.bob = User.objects.create(username="bob")
        self.carol = User.objects.create(username="carol")

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
        """
        模擬「下單並凍結餘額」：建立一張 PENDING 訂單，並把該凍結的餘額
        從 available 搬到 frozen（就像 OrderViewSet.create 做的那樣）。
        回傳這張訂單。撮合時請把它當作簿上的單 / 或 taker。
        """
        amount, price = D(amount), D(price)
        if side == OrderType.SELL:
            # 賣：出 base(BTC)、進 quote(USDT) → 凍 base
            freeze_amount, freeze_currency = amount, self.btc
        else:
            # 買：出 quote(USDT)、進 base(BTC) → 凍 quote
            freeze_amount, freeze_currency = amount * price, self.usdt

        w = self.get_wallet(user, freeze_currency)
        w.available_balance -= freeze_amount
        w.frozen_balance += freeze_amount
        w.save()

        create_kwargs = dict(
            user=user,
            trading_pair=self.pair,
            quantity=amount,
            price=price,
            order_type=side,
            status=OrderStatus.PENDING,
        )
        # ordered_at 是一般欄位（default=timezone.now），可在建立時直接指定，
        # 用來做出可預測的時間先後（測時間優先用）。
        if ordered_at is not None:
            create_kwargs["ordered_at"] = ordered_at
        return OrderModel.objects.create(**create_kwargs)


class FullMatchTest(MatchingBaseTestCase):
    def test_full_match_same_price(self):
        """賣 1 BTC@30000（先掛）對買 1 BTC@30000（後到）→ 完全成交。"""
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")

        sell = self.place(self.alice, OrderType.SELL, 1, 30000)   # maker
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)       # taker

        match_order(buy.pk, buy.trading_pair.id)

        # 一筆成交、兩張單都 FULLY_FILLED
        self.assertEqual(TransactionModel.objects.count(), 1)
        tx = TransactionModel.objects.get()
        self.assertEqual(tx.quantity, D(1))
        self.assertEqual(tx.price, D(30000))
        self.assertEqual(tx.buy_order_id, buy.pk)    # order1 = 買單
        self.assertEqual(tx.sell_order_id, sell.pk)   # order2 = 賣單

        buy.refresh_from_db()
        sell.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)
        self.assertEqual(sell.status, OrderStatus.FULLY_FILLED)

        # 結算：賣方 alice 少 1 BTC（凍結歸零）、多 30000 USDT
        self.assertEqual(self.get_wallet(self.alice, self.btc).frozen_balance, D(0))
        self.assertEqual(self.get_wallet(self.alice, self.btc).available_balance, D(4))
        self.assertEqual(self.get_wallet(self.alice, self.usdt).available_balance, D(30000))
        # 買方 bob 凍結的 30000 USDT 歸零、多 1 BTC
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(0))
        self.assertEqual(self.get_wallet(self.bob, self.usdt).available_balance, D(70000))
        self.assertEqual(self.get_wallet(self.bob, self.btc).available_balance, D(1))


class PartialFillTest(MatchingBaseTestCase):
    def test_taker_smaller_than_maker(self):
        """賣 1 BTC@30000（掛單），買 0.4 → 成交 0.4，買 FULLY_FILLED、賣 PARTIALLY_FILLED。"""
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")

        sell = self.place(self.alice, OrderType.SELL, 1, 30000)
        buy = self.place(self.bob, OrderType.BUY, "0.4", 30000)

        match_order(buy.pk, buy.trading_pair.id)

        self.assertEqual(TransactionModel.objects.count(), 1)
        tx = TransactionModel.objects.get()
        self.assertEqual(tx.quantity, D("0.4"))

        buy.refresh_from_db()
        sell.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.FULLY_FILLED)
        self.assertEqual(sell.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(sell.waiting_transaction_quantity(), D("0.6"))

        # 賣方：賣掉 0.4 BTC，凍結剩 0.6，得到 12000 USDT
        self.assertEqual(self.get_wallet(self.alice, self.btc).frozen_balance, D("0.6"))
        self.assertEqual(self.get_wallet(self.alice, self.usdt).available_balance, D(12000))
        # 買方：得到 0.4 BTC，凍結歸零（0.4*30000=12000 全部花掉）
        self.assertEqual(self.get_wallet(self.bob, self.btc).available_balance, D("0.4"))
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(0))

    def test_taker_larger_than_maker(self):
        """賣 0.4 BTC@30000（掛單），買 1 → 成交 0.4，賣 FULLY_FILLED、買 PARTIALLY_FILLED、剩 0.6 留簿上。"""
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")

        sell = self.place(self.alice, OrderType.SELL, "0.4", 30000)
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)

        match_order(buy.pk, buy.trading_pair.id)

        self.assertEqual(TransactionModel.objects.count(), 1)
        buy.refresh_from_db()
        sell.refresh_from_db()
        self.assertEqual(sell.status, OrderStatus.FULLY_FILLED)
        self.assertEqual(buy.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(buy.waiting_transaction_quantity(), D("0.6"))

        # 買方：得 0.4 BTC，花 12000，凍結剩 30000-12000=18000（仍為未成交的 0.6 凍著）
        self.assertEqual(self.get_wallet(self.bob, self.btc).available_balance, D("0.4"))
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(18000))


class PriceTimePriorityTest(MatchingBaseTestCase):
    def test_price_priority(self):
        """
        兩張掛買單：bob@30000、carol@31000。
        進來一張賣單 alice@29000 → 應先吃「最高買價」carol@31000，成交價＝maker 31000。
        （用買單當 maker，避免買方多凍結殘留。）
        """
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")
        self.wallet(self.carol, self.usdt, "100000")
        self.wallet(self.carol, self.btc, "0")
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")

        buy_bob = self.place(self.bob, OrderType.BUY, 1, 30000)
        buy_carol = self.place(self.carol, OrderType.BUY, 1, 31000)
        sell = self.place(self.alice, OrderType.SELL, 1, 29000)  # taker

        match_order(sell.pk, sell.trading_pair.id)

        self.assertEqual(TransactionModel.objects.count(), 1)
        tx = TransactionModel.objects.get()
        self.assertEqual(tx.buy_order_id, buy_carol.pk)   # 吃到 carol（最高買價）
        self.assertEqual(tx.price, D(31000))           # 成交價＝maker(carol) 的 31000
        self.assertEqual(tx.quantity, D(1))

        # bob 的單沒被動到
        buy_bob.refresh_from_db()
        self.assertEqual(buy_bob.status, OrderStatus.PENDING)
        self.assertEqual(self.get_wallet(self.bob, self.btc).available_balance, D(0))
        # carol 拿到 1 BTC、凍結歸零
        self.assertEqual(self.get_wallet(self.carol, self.btc).available_balance, D(1))
        self.assertEqual(self.get_wallet(self.carol, self.usdt).frozen_balance, D(0))

    def test_time_priority(self):
        """
        兩張同價掛買單 bob@30000（早）、carol@30000（晚）。
        進來賣單 alice 0.5@30000 → 應先吃「較早」的 bob。
        """
        from django.utils import timezone

        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")
        self.wallet(self.carol, self.usdt, "100000")
        self.wallet(self.carol, self.btc, "0")
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")

        early = timezone.now() - timezone.timedelta(minutes=10)
        late = timezone.now() - timezone.timedelta(minutes=1)
        buy_bob = self.place(self.bob, OrderType.BUY, 1, 30000, ordered_at=early)
        buy_carol = self.place(self.carol, OrderType.BUY, 1, 30000, ordered_at=late)
        sell = self.place(self.alice, OrderType.SELL, "0.5", 30000)

        match_order(sell.pk, sell.trading_pair.id)

        self.assertEqual(TransactionModel.objects.count(), 1)
        tx = TransactionModel.objects.get()
        self.assertEqual(tx.buy_order_id, buy_bob.pk)   # 較早的 bob 先成交
        self.assertEqual(tx.quantity, D("0.5"))

        buy_carol.refresh_from_db()
        self.assertEqual(buy_carol.status, OrderStatus.PENDING)   # carol 沒被動到
        buy_bob.refresh_from_db()
        self.assertEqual(buy_bob.status, OrderStatus.PARTIALLY_FILLED)


class MakerPriceTest(MatchingBaseTestCase):
    def test_execution_uses_maker_price(self):
        """
        買單 bob@31000 先掛（maker），賣單 alice@30000 後到（taker）。
        兩者交叉（買 31000 >= 賣 30000），成交價要用 maker（bob 的 31000），
        不是 taker 的 30000。
        """
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")

        buy = self.place(self.bob, OrderType.BUY, 1, 31000)    # maker
        sell = self.place(self.alice, OrderType.SELL, 1, 30000)  # taker

        match_order(sell.pk, sell.trading_pair.id)

        tx = TransactionModel.objects.get()
        self.assertEqual(tx.price, D(31000))   # ← 重點：用 maker 的價
        self.assertEqual(tx.quantity, D(1))
        # 賣方 alice 收到 31000 USDT（不是 30000）
        self.assertEqual(self.get_wallet(self.alice, self.usdt).available_balance, D(31000))
        # 買方 bob 凍結 31000 全花掉、歸零
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(0))


class NoCrossTest(MatchingBaseTestCase):
    def test_no_cross_no_trade(self):
        """賣單 31000、買單 30000 不交叉 → 不成交，買單留 PENDING。"""
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")

        self.place(self.alice, OrderType.SELL, 1, 31000)
        buy = self.place(self.bob, OrderType.BUY, 1, 30000)

        match_order(buy.pk, buy.trading_pair.id)

        self.assertEqual(TransactionModel.objects.count(), 0)
        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.PENDING)
        # 餘額不變（凍結還在）
        self.assertEqual(self.get_wallet(self.bob, self.usdt).frozen_balance, D(30000))
        self.assertEqual(self.get_wallet(self.bob, self.btc).available_balance, D(0))


class ExecutedWaitingAmountTest(MatchingBaseTestCase):
    def test_executed_and_waiting_amount(self):
        """
        驗證 OrderModel.executed_transaction_quantity() / waiting_transaction_quantity()。
        （順帶逼你修掉 self.OrderType 的 bug，改用 import 進來的 OrderType。）
        """
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "0")
        self.wallet(self.bob, self.usdt, "100000")
        self.wallet(self.bob, self.btc, "0")

        sell = self.place(self.alice, OrderType.SELL, 1, 30000)
        buy = self.place(self.bob, OrderType.BUY, "0.3", 30000)

        match_order(buy.pk, buy.trading_pair.id)

        sell.refresh_from_db()
        self.assertEqual(sell.executed_transaction_quantity(), D("0.3"))
        self.assertEqual(sell.waiting_transaction_quantity(), D("0.7"))
        buy.refresh_from_db()
        self.assertEqual(buy.executed_transaction_quantity(), D("0.3"))
        self.assertEqual(buy.waiting_transaction_quantity(), D(0))


class SelfTradePreventionTest(MatchingBaseTestCase):
    """
    自我成交防護（STP）：同一個人的買單與賣單即使價格交叉，也不該互相成交。
    撮合應在 get_waiting_match_orders 用 .exclude(user=order.user) 跳過自己的單。
    （實作前這條會紅；加了 exclude 後變綠。）
    """

    def test_no_self_trade(self):
        """alice 同時掛賣 1@30000 與買 1@30000 → 不成交，兩張單都留簿上、凍結不動。"""
        self.wallet(self.alice, self.btc, "5")
        self.wallet(self.alice, self.usdt, "100000")

        sell = self.place(self.alice, OrderType.SELL, 1, 30000)  # maker，先掛
        buy = self.place(self.alice, OrderType.BUY, 1, 30000)    # taker，同一人

        match_order(buy.pk, buy.trading_pair.id)

        # 不該產生任何成交
        self.assertEqual(TransactionModel.objects.count(), 0)

        # 兩張單都還在等待中
        sell.refresh_from_db()
        buy.refresh_from_db()
        self.assertEqual(sell.status, OrderStatus.PENDING)
        self.assertEqual(buy.status, OrderStatus.PENDING)

        # 凍結原封不動：BTC 凍 1、USDT 凍 30000
        self.assertEqual(self.get_wallet(self.alice, self.btc).frozen_balance, D(1))
        self.assertEqual(self.get_wallet(self.alice, self.usdt).frozen_balance, D(30000))


class TakerStatusTest(MatchingBaseTestCase):
    """
    mark_taker_status 的狀態判定：
      - 完全沒撮到（remaining == quantity）→ 維持 PENDING（不是 PARTIALLY_FILLED）
      - 部分撮到（0 < remaining < quantity）→ PARTIALLY_FILLED
      - 全部撮到（remaining <= 0）→ FULLY_FILLED
    第一條在修正前會紅（舊版把沒撮到的誤標成 PARTIALLY_FILLED）。
    """

    def _order(self, qty):
        return OrderModel.objects.create(
            user=self.alice, trading_pair=self.pair, quantity=D(qty),
            price=D(30000), order_type=OrderType.BUY, status=OrderStatus.PENDING,
        )

    def test_nothing_matched_stays_pending(self):
        o = self._order(1)
        o.mark_taker_status(D(1))          # remaining == quantity：一筆都沒撮到
        o.refresh_from_db()
        self.assertEqual(o.status, OrderStatus.PENDING)

    def test_partial_match_is_partially_filled(self):
        o = self._order(1)
        o.mark_taker_status(D("0.4"))      # 撮掉 0.6、剩 0.4
        o.refresh_from_db()
        self.assertEqual(o.status, OrderStatus.PARTIALLY_FILLED)

    def test_full_match_is_fully_filled(self):
        o = self._order(1)
        o.mark_taker_status(D(0))          # 全撮完
        o.refresh_from_db()
        self.assertEqual(o.status, OrderStatus.FULLY_FILLED)
