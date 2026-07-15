"""
M6 — 撮合併發安全壓力測試。

重點：多個 taker 同時搶同一批 maker 流動性時，不可超賣、不可重複撮合、餘額不可變負。

★ M6 收尾（cancel 的交易對鎖）——本檔新增的要求，見 `docs/04-1_matching_engine_spec.md` §6.1：

  1) 取消的核心邏輯要抽成可重用函式 `cancel_order(order_id, user)`（已落在 transaction/services.py，
     與 match_order 作伴），`OrderViewSet.cancel` 改成只是呼叫它的薄殼。
     為什麼：本檔原本自己複製了一份 view 的取消邏輯（_cancel_order），
     那等於「測試在測複製品，不是測線上真正跑的程式碼」——實作改了測試也不會紅。

  2) cancel_order 必須遵守全系統統一的鎖順序：**TradingPair → Order → Wallet**，
     與 match_order 完全一致。順序不一致 = 有機會繞環 = deadlock。
     實作提示：先「不鎖」讀出 trading_pair_id（trading_pair 建立後永不變，這個預讀安全），
     用它鎖 pair，再 select_for_update 鎖訂單並重新檢查狀態與擁有權。

  3) 契約：
       - 找不到該單、或該單不屬於 user  → raise OrderModel.DoesNotExist
       - 該單已是終態（FULLY_FILLED / CANCELED）→ raise OrderNotCancelable
         （自訂例外，建議放 transaction/exceptions.py）
       - 成功 → 狀態改 CANCELED、release_frozen、回傳該訂單
     view 把這兩種例外都轉成 400（既有 test_cancel_refund.py 的行為不可改變）。

⚠️ 執行條件（缺一不可）：
  1. 資料庫必須是 PostgreSQL —— SQLite 不支援 select_for_update 的列鎖，測不到競態。
  2. 用 TransactionTestCase（不是 TestCase）—— TestCase 把每條測試包在單一未提交交易裡，
     其他執行緒的連線看不到資料、鎖也不會生效;TransactionTestCase 會真的 commit。
  3. 每個工作執行緒用自己的 DB 連線，結束時 connection.close()。

設計：maker 只有 SUPPLY=10 顆 BTC、掛賣 10;開 N=20 個買家各買 1，
      用 Barrier 讓 20 條撮合執行緒盡量同時起跑。
      正確的列鎖會把它們序列化在 maker 那張單上 → 剛好成交 10、其餘 PENDING。
      若鎖壞掉（雙重撮合 / lost update）→ 總成交量會 > 10，或餘額變負被 CHECK 擋下。
"""

import threading
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Sum
from django.test import TransactionTestCase

from currency.models import CurrencyModel, TradingPairModel
from member.models import WalletModel
from transaction.constants import OrderStatus, OrderType
from transaction.exceptions import OrderNotCancelable
from transaction.models import OrderModel, TransactionModel
from transaction.services import cancel_order, match_order


def D(x):
    return Decimal(str(x))


def _try_cancel(order_id, user):
    """取消，但把「已成交完、不能取消」視為正常結果。

    在 match/cancel 對撞的壓力測試裡，這張單很可能在 cancel 拿到鎖之前就已被撮掉，
    此時 OrderNotCancelable 是**預期**的競態結果、不是錯誤。
    但 deadlock（OperationalError）絕不可被吞掉——那正是這條測試要抓的東西。
    """
    try:
        cancel_order(order_id, user)
    except OrderNotCancelable:
        pass


class MatchingConcurrencyTest(TransactionTestCase):
    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )

    def _total(self, currency):
        """某幣別在全系統的總量（available + frozen），守恆斷言用。"""
        agg = WalletModel.objects.filter(asset_type=currency).aggregate(
            a=Sum("available_balance"), f=Sum("frozen_balance"))
        return (agg["a"] or D(0)) + (agg["f"] or D(0))

    def _executed(self, order):
        """某張單的已成交總量。"""
        if order.order_type == OrderType.BUY:
            return sum((t.quantity for t in order.buy_transactions.all()), D(0))
        return sum((t.quantity for t in order.sell_transactions.all()), D(0))

    def _run_concurrently(self, fns):
        """同時起跑一批 callable（各自一條執行緒、Barrier 對齊、收集例外）。"""
        barrier = threading.Barrier(len(fns))
        errors = []

        def wrap(fn):
            def runner():
                try:
                    barrier.wait()
                    fn()
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))
                finally:
                    connection.close()
            return runner

        threads = [threading.Thread(target=wrap(fn)) for fn in fns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return errors

    def _freeze_and_place(self, user, side, qty, price):
        """模擬下單並凍結（等同 OrderViewSet.create 的凍結那步）。"""
        qty, price = D(qty), D(price)
        if side == OrderType.SELL:
            amount, currency = qty, self.btc
        else:
            amount, currency = qty * price, self.usdt
        w = WalletModel.objects.get(user=user, asset_type=currency)
        w.available_balance -= amount
        w.frozen_balance += amount
        w.save()
        return OrderModel.objects.create(
            user=user, trading_pair=self.pair, quantity=qty, price=price,
            order_type=side, status=OrderStatus.PENDING,
        )

    def test_no_oversell_under_concurrency(self):
        N = 20         # 買家數
        SUPPLY = 10    # maker 的 BTC 供給

        # maker：有 10 顆 BTC，掛賣 10 @ 30000（凍結 10 BTC）
        maker = User.objects.create(username="maker")
        WalletModel.objects.create(user=maker, asset_type=self.btc, available_balance=D(SUPPLY))
        WalletModel.objects.create(user=maker, asset_type=self.usdt, available_balance=D(0))
        sell = self._freeze_and_place(maker, OrderType.SELL, SUPPLY, 30000)

        # N 個買家，各買 1 @ 30000
        buys = []
        for i in range(N):
            buyer = User.objects.create(username=f"buyer{i}")
            WalletModel.objects.create(user=buyer, asset_type=self.usdt, available_balance=D(30000))
            WalletModel.objects.create(user=buyer, asset_type=self.btc, available_balance=D(0))
            buys.append(self._freeze_and_place(buyer, OrderType.BUY, 1, 30000))

        # 20 條執行緒，用 Barrier 同時起跑，最大化競態
        barrier = threading.Barrier(N)
        errors = []

        def run(order_id, trading_pair_id):
            try:
                barrier.wait()
                match_order(order_id, trading_pair_id)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(b.pk, b.trading_pair.id)) for b in buys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 撮合過程不該拋例外（例如 CHECK 擋下的負餘額、或死鎖）
        self.assertEqual(errors, [])

        # 核心：不超賣 —— 總成交量剛好等於供給
        total = sum((tx.quantity for tx in TransactionModel.objects.all()), D(0))
        self.assertEqual(total, D(SUPPLY))

        sell.refresh_from_db()
        self.assertEqual(sell.status, OrderStatus.FULLY_FILLED)

        # 剛好 SUPPLY 張買單成交、其餘留 PENDING（沒被誤標）
        filled = OrderModel.objects.filter(
            order_type=OrderType.BUY, status=OrderStatus.FULLY_FILLED).count()
        pending = OrderModel.objects.filter(
            order_type=OrderType.BUY, status=OrderStatus.PENDING).count()
        self.assertEqual(filled, SUPPLY)
        self.assertEqual(pending, N - SUPPLY)

        # maker：BTC 凍結歸零、收到 10*30000 USDT
        self.assertEqual(WalletModel.objects.get(user=maker, asset_type=self.btc).frozen_balance, D(0))
        self.assertEqual(WalletModel.objects.get(user=maker, asset_type=self.usdt).available_balance, D(SUPPLY * 30000))

        # 不變量：所有錢包餘額非負
        for w in WalletModel.objects.all():
            self.assertGreaterEqual(w.available_balance, D(0))
            self.assertGreaterEqual(w.frozen_balance, D(0))

    def test_cross_fire_buy_sell(self):
        """買賣 taker 對撞：交錯掛買賣單後同時雙向撮合。

        前一個測試 taker 全是買、maker 全是賣（兩集合不相交），逼不出鎖序倒置。
        這裡交錯建立 sell/buy，使兩邊都有「比自己早」的對手單可吃 → 兩個方向都會
        主動撮合、互相把對方當 maker 候選去鎖 → 才真正考驗鎖序與序列化。

        不預測精確成交筆數（看交錯），改驗鐵則：
          - 無死鎖、無 CHECK 觸發（errors 空）
          - 守恆：全系統 BTC、USDT 總量（available+frozen）前後不變
          - 不超賣：每張單成交量 <= 自身數量
          - 餘額非負；且確實有撮到
        """
        M = 12          # 每邊張數
        PRICE = 30000

        orders = []
        for i in range(M):
            s_user = User.objects.create(username=f"s{i}")
            WalletModel.objects.create(user=s_user, asset_type=self.btc, available_balance=D(1))
            WalletModel.objects.create(user=s_user, asset_type=self.usdt, available_balance=D(0))
            orders.append(self._freeze_and_place(s_user, OrderType.SELL, 1, PRICE))

            b_user = User.objects.create(username=f"b{i}")
            WalletModel.objects.create(user=b_user, asset_type=self.usdt, available_balance=D(PRICE))
            WalletModel.objects.create(user=b_user, asset_type=self.btc, available_balance=D(0))
            orders.append(self._freeze_and_place(b_user, OrderType.BUY, 1, PRICE))

        btc0, usdt0 = self._total(self.btc), self._total(self.usdt)

        errors = self._run_concurrently(
            [lambda oid=o.pk, tpid=o.trading_pair.id: match_order(oid, tpid) for o in orders]
        )

        self.assertEqual(errors, [])
        # 守恆：錢沒被憑空生出或消滅
        self.assertEqual(self._total(self.btc), btc0)
        self.assertEqual(self._total(self.usdt), usdt0)
        # 不超賣 / 不重複撮合
        for o in OrderModel.objects.all():
            self.assertLessEqual(self._executed(o), o.quantity)
        # 確實有發生撮合（否則這測試沒意義）
        self.assertGreater(TransactionModel.objects.count(), 0)
        # 餘額非負
        for w in WalletModel.objects.all():
            self.assertGreaterEqual(w.available_balance, D(0))
            self.assertGreaterEqual(w.frozen_balance, D(0))

    def test_release_frozen_under_match_cancel_race(self):
        """release_frozen 的併發回歸：同一用戶的兩張買單，一張被撮合、一張同時被取消。

        兩條流程鎖的是不同訂單列（撮合鎖 buy_fill+sell、取消鎖 buy_cancel），彼此不互斥，
        於是會「同時」對 U 的同一個 USDT 錢包做退款。這正是 release_frozen 從讀改寫改成
        F() 要防的情境：舊寫法兩筆退款會互相覆蓋、掉一筆;F() 相對運算則兩筆都生效。

        買單以 31000 掛、吃到 30000 的賣單：
          - buy_fill 成交 → 退多凍 31000-30000 = 1000
          - buy_cancel 取消 → 退整筆 31000
          → U 的 USDT 最終 available=32000、frozen=0（兩筆退款都在）
        """
        U = User.objects.create(username="U")
        WalletModel.objects.create(user=U, asset_type=self.usdt, available_balance=D(62000))
        WalletModel.objects.create(user=U, asset_type=self.btc, available_balance=D(0))

        # 賣方先掛（maker，較早），1 BTC @ 30000
        seller = User.objects.create(username="seller")
        WalletModel.objects.create(user=seller, asset_type=self.btc, available_balance=D(1))
        WalletModel.objects.create(user=seller, asset_type=self.usdt, available_balance=D(0))
        self._freeze_and_place(seller, OrderType.SELL, 1, 30000)

        # U 兩張買單 @ 31000，各凍 31000（共 62000）
        buy_fill = self._freeze_and_place(U, OrderType.BUY, 1, 31000)
        buy_cancel = self._freeze_and_place(U, OrderType.BUY, 1, 31000)

        errors = self._run_concurrently([
            lambda: match_order(buy_fill.pk, buy_fill.trading_pair.id),
            lambda: _try_cancel(buy_cancel.pk, U),
        ])

        self.assertEqual(errors, [])

        usdt = WalletModel.objects.get(user=U, asset_type=self.usdt)
        self.assertEqual(usdt.available_balance, D(32000))  # 1000 + 31000，無一筆掉更新
        self.assertEqual(usdt.frozen_balance, D(0))
        self.assertEqual(
            WalletModel.objects.get(user=U, asset_type=self.btc).available_balance, D(1))

        for w in WalletModel.objects.all():
            self.assertGreaterEqual(w.available_balance, D(0))
            self.assertGreaterEqual(w.frozen_balance, D(0))

    def test_cancel_match_stress_no_deadlock(self):
        """撮合與取消在同一交易對上大量交錯 → 不可 deadlock、守恆、不超賣。

        這是 cancel 取交易對鎖的壓力測試。match_order 走 pair → order → wallet；
        若 cancel 只鎖 order 就直接動錢包（沒先過 pair 閘門），兩條流程對
        「訂單列 / 錢包列」的爭搶順序就不一致 → PostgreSQL 有機會偵測到
        `deadlock detected` 並讓其中一方拋 OperationalError，被 errors 收集到。

        注意誠實話：deadlock 本質是時序相依的，不保證每次都能重現。
        這條測試是**回歸網**（真的繞環時有機會抓到），不是「沒紅就證明鎖對了」。
        鎖順序的正確性主要靠架構保證（見 04-1 §6.1），測試只是輔助。
        """
        M = 10
        PRICE = 30000

        sells, buys = [], []
        for i in range(M):
            s_user = User.objects.create(username=f"cs{i}")
            WalletModel.objects.create(user=s_user, asset_type=self.btc, available_balance=D(1))
            WalletModel.objects.create(user=s_user, asset_type=self.usdt, available_balance=D(0))
            sells.append(self._freeze_and_place(s_user, OrderType.SELL, 1, PRICE))

            b_user = User.objects.create(username=f"cb{i}")
            WalletModel.objects.create(user=b_user, asset_type=self.usdt, available_balance=D(PRICE))
            WalletModel.objects.create(user=b_user, asset_type=self.btc, available_balance=D(0))
            buys.append(self._freeze_and_place(b_user, OrderType.BUY, 1, PRICE))

        btc0, usdt0 = self._total(self.btc), self._total(self.usdt)

        # 買單全部去撮合，同時取消半數賣單 → 製造 match 與 cancel 的正面對撞
        fns = [lambda oid=b.pk, tpid=b.trading_pair.id: match_order(oid, tpid) for b in buys]
        fns += [lambda oid=s.pk, u=s.user: _try_cancel(oid, u) for s in sells[::2]]

        errors = self._run_concurrently(fns)

        # 重點：不可有 deadlock detected（或任何其他例外）
        self.assertEqual(errors, [])

        # 守恆：錢沒被憑空生出或消滅
        self.assertEqual(self._total(self.btc), btc0)
        self.assertEqual(self._total(self.usdt), usdt0)

        # 不超賣、不重複撮合
        for o in OrderModel.objects.all():
            self.assertLessEqual(self._executed(o), o.quantity)

        # 被取消的單不可再有新成交（取消後 release_frozen 已退款，再成交就會超賣）
        for o in OrderModel.objects.filter(status=OrderStatus.CANCELED):
            self.assertEqual(self._executed(o), D(0))

        # 餘額非負
        for w in WalletModel.objects.all():
            self.assertGreaterEqual(w.available_balance, D(0))
            self.assertGreaterEqual(w.frozen_balance, D(0))


class CancelOrderContractTest(TransactionTestCase):
    """cancel_order 的契約（非併發）：擁有權、終態、退款。

    這些行為原本只透過 HTTP 層被 test_cancel_refund.py 蓋到；
    抽成函式後，直接釘住函式本身的契約，view 才能安心變成薄殼。
    """

    def setUp(self):
        self.usdt = CurrencyModel.objects.create(code="USDT", name="Tether")
        self.btc = CurrencyModel.objects.create(code="BTC", name="Bitcoin")
        self.pair = TradingPairModel.objects.create(
            base_currency=self.btc, quote_currency=self.usdt
        )
        self.bob = User.objects.create(username="bob")
        self.alice = User.objects.create(username="alice")

    def _place_buy(self, user, qty, price):
        qty, price = D(qty), D(price)
        w = WalletModel.objects.get(user=user, asset_type=self.usdt)
        w.available_balance -= qty * price
        w.frozen_balance += qty * price
        w.save()
        return OrderModel.objects.create(
            user=user, trading_pair=self.pair, quantity=qty, price=price,
            order_type=OrderType.BUY, status=OrderStatus.PENDING,
        )

    def test_cancel_releases_frozen(self):
        WalletModel.objects.create(user=self.bob, asset_type=self.usdt, available_balance=D(100000))
        buy = self._place_buy(self.bob, 1, 30000)

        cancel_order(buy.pk, self.bob)

        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.CANCELED)
        w = WalletModel.objects.get(user=self.bob, asset_type=self.usdt)
        self.assertEqual(w.available_balance, D(100000))
        self.assertEqual(w.frozen_balance, D(0))

    def test_cancel_other_users_order_raises(self):
        """別人的單看不到 → DoesNotExist（view 轉 400），且不退款。"""
        WalletModel.objects.create(user=self.bob, asset_type=self.usdt, available_balance=D(100000))
        buy = self._place_buy(self.bob, 1, 30000)

        with self.assertRaises(OrderModel.DoesNotExist):
            cancel_order(buy.pk, self.alice)

        buy.refresh_from_db()
        self.assertEqual(buy.status, OrderStatus.PENDING)
        self.assertEqual(
            WalletModel.objects.get(user=self.bob, asset_type=self.usdt).frozen_balance, D(30000))

    def test_cancel_terminal_raises(self):
        """終態不可取消 → OrderNotCancelable（view 轉 400），且不重複退款。"""
        WalletModel.objects.create(user=self.bob, asset_type=self.usdt, available_balance=D(100000))
        buy = self._place_buy(self.bob, 1, 30000)
        cancel_order(buy.pk, self.bob)

        with self.assertRaises(OrderNotCancelable):
            cancel_order(buy.pk, self.bob)

        w = WalletModel.objects.get(user=self.bob, asset_type=self.usdt)
        self.assertEqual(w.available_balance, D(100000))  # 沒退第二次
        self.assertEqual(w.frozen_balance, D(0))
