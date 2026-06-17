# 幣對表(TradingPair)設計

## 目標

把目前隱含在 `OrderModel.currency1`/`currency2` 的「幣對」概念,提升為一張正式的幣對表 `TradingPairModel`。訂單改以「幣對 + 買賣方向(order_type)」描述,移除冗餘的 currency1/currency2,讓資料模型符合交易所標準,並簡化未來 M3 撮合引擎的對手單查詢。

## 背景:現況

幣對概念目前散落於:

- `transaction/models.py:18-31` — `OrderModel.currency1`(出去的貨幣)、`currency2`(進來的貨幣),兩個 FK 加 `order_type` 共同表達一個幣對與方向。
- `transaction/test_matching.py:13-17` — 撮合用「賣單.currency1==買單.currency2 且 賣單.currency2==買單.currency1」判斷同一交易對、相反方向。
- `transaction/views.py:21-36` — `required_currency1_amount` / `check_wallet_balance` 用 currency1 算凍結。
- `transaction/serializers.py:22-26` — validate 檢查 currency1 != currency2。

## 決策

- **取代**,而非並存:OrderModel 移除 currency1/currency2,改用 `trading_pair` FK。理由:M3 撮合尚未實作,現在改成本最低;單一真實來源、無冗餘。
- **只實作基本欄位**:交易限制(min_order_amount、price/amount precision)列入升級路徑,本次不做。

## 資料模型

### TradingPairModel(新增於 `currency/models.py`)

| 欄位 | 型別 | 說明 |
|---|---|---|
| `base_currency` | FK → CurrencyModel, `related_name="base_pairs"` | 標的幣,如 BTC |
| `quote_currency` | FK → CurrencyModel, `related_name="quote_pairs"` | 計價幣,如 USDT |
| `symbol` | CharField, unique | 如 "BTC/USDT",`save()` 自動由 base/quote 組出(比照 CurrencyModel.save() 把 code 轉大寫的慣例) |
| `is_active` | BooleanField, default=True | 是否開放交易 |
| created_at / updated_at | 繼承 BaseTimeModel | |

規則:

- `unique_together = (base_currency, quote_currency)`
- `save()`:`base_currency == quote_currency` 時 raise(防呆);並由 base/quote 的 code 自動組出 `symbol`(如 `f"{base.code}/{quote.code}"`)。
- `__str__` 回傳 symbol。

### OrderModel 改動(`transaction/models.py`)

- 移除 `currency1`、`currency2`。
- 新增 `trading_pair = FK(TradingPairModel, on_delete=SET_NULL, null=True, verbose_name="幣對")`。
- 保留 `order_type`(BUY/SELL)、`amount`、`price`、`status`、`order_number`、`user`。

新增 helper(下單與結算共用,語意清楚):

- `base`(property)= `trading_pair.base_currency`
- `quote`(property)= `trading_pair.quote_currency`
- `freeze_currency`(property):BUY → quote;SELL → base
- `freeze_amount`(property):BUY → `amount * price`;SELL → `amount`

### 方向推導(取代四個 currency 互比)

- `BUY`:用 quote 買 base → 出 quote、進 base → 凍結 quote,數量 = `amount × price`
- `SELL`:賣 base 換 quote → 出 base、進 quote → 凍結 base,數量 = `amount`

## 下單流程(`transaction/views.py`)

- `required_currency1_amount` → 改用 `order.freeze_amount`(可移除原函式,改讀 property)。
- `check_wallet_balance`:錢包查詢的 `asset_type` 改用 `freeze_currency`。
- `queryset` 的 `select_related` 從 `currency1, currency2` 改為 `trading_pair__base_currency, trading_pair__quote_currency`。

## Serializer(`transaction/serializers.py`)

- `OrderSerializer`:以 `trading_pair`(顯示 symbol)取代 currency1/currency2 兩個唯讀欄位。
- `OrderCreateUpdateSerializer.validate`:移除「currency1 != currency2」檢查(幣對表 unique + save 防呆已保證),改為驗證 `trading_pair.is_active` 為真。

## Currency app API / Admin

- `currency/admin.py`:註冊 `TradingPairAdmin`(list_display: symbol / base_currency / quote_currency / is_active;search_fields: symbol)。
- `currency/serializers.py`:新增 `TradingPairSerializer`。
- `currency/views.py`:新增 `TradingPairViewSet`。
- `currency/urls.py`:`router.register(r"trading-pair", TradingPairViewSet)`。

## Migration

- currency:新增 TradingPairModel。
- transaction:移除 currency1/currency2、新增 trading_pair。**破壞性**改動。
- 開發階段(db.sqlite3 為本機測試資料):直接 `makemigrations` 重建,不寫資料搬遷。
- 註:正式環境若已有訂單資料,需另寫 data migration(由 currency1/currency2 反查/建立對應 TradingPair 後回填),本次不涵蓋。

## 撮合引擎(M3,不在本次範圍)

本次改完後,未來 `transaction/matching.py` 找對手單可簡化為:
`filter(trading_pair=order.trading_pair, order_type=<相反>, status in [PENDING, PARTIALLY_FILLED])`,
不再需要四個 currency 互比。spec 此處註明以利 M3 實作。

## 測試

- `transaction/test_orders.py`:建單改用 trading_pair。
- `transaction/test_matching.py`:`place()` helper(L85-118)改用 trading_pair 建單;此檔為 M3 規格,僅調整「建單欄位」使其能 import/建物件,不動撮合契約本身。
- 新增 currency 幣對測試:symbol 自動產生、base == quote 被擋、unique_together、is_active 預設。
- 全程 TDD。

## 實作順序

1. TradingPairModel + 其單元測試
2. currency API / Admin(serializer、view、url、admin)
3. 改 OrderModel + 下單流程(views、serializers)+ helper
4. 更新既有測試(test_orders、test_matching 建單欄位)
5. makemigrations + 跑全測試確認綠燈

## 升級路徑(本次不做)

- 幣對交易限制欄位:`min_order_amount`、`price_precision`、`amount_precision`,並在下單 validate 套用。
- 正式環境的資料搬遷 migration。
