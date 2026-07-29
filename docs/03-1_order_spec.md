# 細部規格 — 訂單（order）〔範圍一〕

> 上層文件：`00_overall_spec.md`　相關：`04-1_matching_engine_spec.md`、`05-1_settlement_spec.md`
>
> 範圍二（測試鏈）無對應檔案：本模組在範圍二不變。訂單只操作內部帳本的數字，鏈的複雜度全被關在入出金模組（見 `06-2`），訂單這層不受影響。

## 一、需求方規格

### 1. 模組職責

承接使用者的買賣意圖，將其轉為一張訂單並保存，並在下單當下完成兩件事：驗證餘額是否足夠、凍結將要付出的金額。訂單保存之後，撮合為另一獨立行為（見 `04`）。

### 2. 一張訂單描述什麼

一張訂單描述:買或賣、多少數量、什麼價格、涉及哪兩種幣。以交易對 BASE/QUOTE 為對照:

- 買單:想得到 BASE、付出 QUOTE。
- 賣單:想賣掉 BASE、得到 QUOTE。

### 3. 買賣方向與凍結

下單時凍結的一定是使用者要付出去的那種幣:

- 買單:付出的是計價幣。凍結金額為「數量 × 價格」。例:買 1 BTC @ 30000，凍結 30000 計價幣。
- 賣單:付出的是標的幣。凍結金額為「數量」。例:賣 1 BTC，凍結 1 BTC。

凍結方向與金額不得算錯:買單凍結計價幣、賣單凍結標的幣，兩者不可相反。

### 4. 餘額不足

下單前需檢查可用餘額是否足夠支付所需凍結金額。若不足，拒絕下單，且使用者餘額維持不變(不得發生「凍了錢卻沒建立訂單」或「建立了訂單卻沒凍錢」的情形)。

### 5. 限價單與市價單

- 限價單:指定價格。買單的價格是「最多願意付的價」，賣單的價格是「最少要收的價」。範圍一只做限價單。
- 市價單:不指定價格，以市場最好價立即成交;凍結金額需另行設計(買單可能需凍結一個上限)。屬進階功能。

### 6. 訂單查詢

需提供訂單查詢能力:列出訂單、查詢單一訂單，並可看到訂單狀態與尚未成交的數量。

### 7. 訂單狀態

一張訂單有四種狀態:

- 待成交:已建立、尚未有任何成交。
- 部分成交:已有部分數量成交，仍有剩餘。
- 完全成交:全部數量皆已成交。
- 已取消:訂單被取消。

狀態規則:

- 待成交、部分成交為可變動狀態,只有這兩種狀態能被撮合或取消。
- 完全成交、已取消為終態,不能再改變。

### 8. 取消訂單(進階)

只有尚未完全成交的訂單(待成交、部分成交)可取消。取消時,將這張訂單尚未使用到的凍結金額(剩餘數量對應的付出金額)退回可用餘額,並將訂單標記為已取消。取消需與撮合互斥,避免撮合進行中同時被取消。

## 二、開發方規格

> 對應 app：`transaction`　主要 model：`OrderModel`

### 1. 資料模型

#### OrderModel

| 欄位                  | 型別                                              | 說明                               |
| --------------------- | ------------------------------------------------- | ---------------------------------- |
| `order_number`        | unique                                            | 訂單代號，預設 `generate_hex_uuid` |
| `user`                | FK → User                                         | 下單者                             |
| `trading_pair`        | FK → TradingPairModel (related_name=`trading_pair_orders`) | 交易對                    |
| `trading_pair_symbol` | CharField                                         | 交易對代號                         |
| `quantity`            | Decimal(20,2)                                     | 數量（指 base_currency 的數量）    |
| `price`               | Decimal(20,2)                                     | 價格                               |
| `order_type`          | choices `OrderType`（BUY/SELL）                   | 買/賣                              |
| `status`              | choices `OrderStatus`                             | 訂單狀態，預設 `PENDING`           |
| `ordered_at`          | DateTime                                          | 下單時間                           |

方向與幣別由 `trading_pair`（`base_currency` / `quote_currency`）搭配 `order_type` 表示。對照交易對 BASE/QUOTE:

- 買單（BUY）:得到 `base_currency`、付出 `quote_currency`。
- 賣單（SELL）:付出 `base_currency`、得到 `quote_currency`。

**狀態（`OrderStatus`）**:`PENDING`、`PARTIALLY_FILLED`、`FULLY_FILLED`、`CANCELED`。

模型方法:

- `executed_transaction_quantity()` — 加總這張單已成交的數量。
- `waiting_transaction_quantity()` — `quantity - 已成交`，即尚未成交的數量。
- `mark_maker_status()` — 依成交結果標記此單作為 maker 時的狀態。
- `mark_taker_status()` — 依成交結果標記此單作為 taker 時的狀態。
- `get_current_frozen()` — 取得此單當前尚未使用的凍結量。
- `get_asset_type()` — 取得此單凍結所對應的幣別。

#### TransactionModel（成交關聯）

- `buy_order`：FK → OrderModel（related_name=`buy_transactions`）。
- `sell_order`：FK → OrderModel（related_name=`sell_transactions`）。
- `quantity`、`price`、`transaction_number`。

### 2. 凍結公式

凍結對象與金額由 `order_type` 決定:

- 買單:凍結 `quote_currency`，金額為 `quantity × price`。
- 賣單:凍結 `base_currency`，金額為 `quantity`。

以 `OrderCreateUpdateSerializer` 計算所需凍結金額（`required_balance`）。

### 3. 訂單狀態機

```
            下單成功
              │
              ▼
          ┌────────┐   部分撮合    ┌──────────────────┐
          │PENDING │ ───────────▶ │ PARTIALLY_FILLED │
          └───┬────┘              └────────┬─────────┘
              │                            │
   完全撮合    │                完全撮合      │
              ▼                            ▼
          ┌──────────────┐          ┌──────────────┐
          │ FULLY_FILLED │          │ FULLY_FILLED │
          └──────────────┘          └──────────────┘

   待成交 / 部分成交 皆可被取消:
              │
              ▼
          ┌──────────┐
          │ CANCELED │   （把剩餘凍結餘額退回可用）
          └──────────┘
```

- `FULLY_FILLED`、`CANCELED` 為終態,不可再變。
- 只有 `PENDING`、`PARTIALLY_FILLED` 能被撮合或取消。

### 4. 下單流程與端點

對應 `OrderViewSet.create()`，全程包在 `@transaction.atomic`:

1. 下單者一律取自 `request.user`。
2. 以 `OrderCreateUpdateSerializer`（context 帶 user）驗證輸入。
3. 取得該用戶對應幣別的 `wallet` 與 `required_balance`。
4. `transfer_to_frozen(wallet, required_balance)`:可用 −= 金額、凍結 += 金額。
5. 保存訂單。
6. 以 `LedgerEntryModel.create_order_ledgers()` 寫入帳本。
7. 交易 commit 後,`send_to_match_market.delay(order.id, order.trading_pair.id)` 丟撮合任務。

端點與查詢:

- `http_method_names` 僅開放 `get` / `post`;改單（`PUT` / `PATCH`）回 405。
- `get_queryset` 依 `request.user` 過濾;具有 view 權限者可看全部。
- 取消訂單為 `@action(detail=True)` 的 `cancel`。
- 成交查詢（`TransactionViewSet`）是否開放可留至結算章節決定。

### 5. 技術約束/注意事項

- **凍結原子性**:驗證、凍結餘額、保存訂單、寫帳本必須包在同一 atomic 交易內,同生同死;中途失敗需一併 rollback。撮合任務須待交易 commit 後才派送。
- **餘額不變量**:餘額不足時必須完全擋下,不得改動任何餘額。
- **下單與撮合分離**:`create` 僅負責驗證、凍結、存單、寫帳本,不得在其中直接改動對手方餘額;撮合為獨立步驟。
- **凍結方向**:買單凍結 `quote_currency`、賣單凍結 `base_currency`,不可相反。
- **終態不可變**:`FULLY_FILLED`、`CANCELED` 不得再被撮合或取消。
- **取消與撮合互斥**:取消時需鎖訂單,避免撮合進行中同時被取消。

### 6. 進階項目

- 取消訂單:以 `get_current_frozen()` 計算剩餘凍結量退回可用,狀態改 `CANCELED`,並與撮合互斥(鎖訂單)。
- 市價單:不帶 price,吃訂單簿最好價;凍結金額需另設計(買單可能凍結一上限)。
- 參數驗證強化:數量／價格 > 0、符合最小下單量與精度(配合 `trading_pair`)。
- 訂單簿快照 API:回傳某交易對的買賣掛單聚合(供前端畫深度圖)。
