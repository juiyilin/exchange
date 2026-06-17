# 細部規格 — 訂單（order）

> 對應 app：`transaction`　主要 model：`OrderModel`
> 上層文件：`00_overall_spec.md`　相關：`04_matching_engine_spec.md`、`05_settlement_spec.md`

## 1. 這個模組負責什麼

承接使用者的買賣意圖、把它變成一張「訂單」存進資料庫，並在下單當下完成最關鍵的兩件事：**驗證餘額夠不夠**、**凍結要花的錢**。訂單存進去之後，撮合是另一回事（見 `04`）。

## 2. 概念

### 一張訂單描述什麼
「我要買/賣、多少數量、什麼價格、哪兩種幣」。

現況 `OrderModel` 用 `currency1`（出去的幣）和 `currency2`（進來的幣）+ `order_type`（買/賣）來描述。對照交易對 `BASE/QUOTE`：

- **買單（BUY）**：我想得到 BASE、付出 QUOTE。`currency2 = BASE`（進來）、`currency1 = QUOTE`（出去）。
- **賣單（SELL）**：我想賣掉 BASE、得到 QUOTE。`currency1 = BASE`（出去）、`currency2 = QUOTE`（進來）。

> 這個 currency1/currency2 的設計有點繞，進階改用交易對 model 會更直覺（見 `01_currency_spec.md` 6.1）。基本階段先沿用現況。

### 下單要凍結多少（現況邏輯，已實作）
凍結的一定是 `currency1`（你要付出去的幣）：

- **買單**：要付 `數量 × 價格` 的計價幣。例：買 1 BTC @ 30000，凍結 30000 USDT。
- **賣單**：要付 `數量` 的標的幣。例：賣 1 BTC，凍結 1 BTC。

對應現有程式碼 `OrderViewSet.required_currency1_amount()`：買單回傳 `amount × price`，賣單回傳 `amount`。

### 限價單 vs 市價單
- **限價單（Limit）**：指定價格。買單的 price 是「我最多願意付的價」，賣單的 price 是「我最少要收的價」。v0.1 只做這種。
- **市價單（Market）**：不指定價、用市場最好價立刻成交。進階功能。

## 3. 資料模型（現況）

### OrderModel（繼承 BaseTimeModel）
| 欄位 | 型別 | 說明 |
|---|---|---|
| `order_number` | CharField(32), unique | 訂單代號，預設 `generate_hex_uuid` |
| `user` | FK → User (SET_NULL) | 下單者 |
| `currency1` | FK → Currency (related_name=out_currency) | 出去的幣 |
| `currency2` | FK → Currency (related_name=in_currency) | 進來的幣 |
| `amount` | Decimal(20,2) | 數量（指 BASE 的數量） |
| `price` | Decimal(20,2) | 價格 |
| `order_type` | choices(BUY/SELL) | 買/賣 |
| `status` | choices | 訂單狀態，預設 PENDING |

**狀態（`OrderStatus`）**：`PENDING`（等待中）、`PARTIALLY_FILLED`（部分成交）、`FILLED`（已成交）、`CANCELED`（已取消）。

模型上已有兩個方法：
- `executed_transaction_amount()` — 加總這張單已成交的數量。
- `waiting_transaction_amount()` — `amount - 已成交`，即還沒成交的數量。

> 小提醒：這兩個方法目前引用 `self.OrderType`/`self.buy_order`，要確認 related_name 對得上（`TransactionModel.order1` 的 related_name 是 `buy_order`、`order2` 是 `sell_order`）。這個之後驗證撮合時會一起檢查。

## 4. 訂單狀態機

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
          ┌────────┐                  ┌────────┐
          │ FILLED │ ◀────────────────│ FILLED │
          └────────┘                  └────────┘

   任何「未完全成交」狀態（PENDING / PARTIALLY_FILLED）都可以被取消：
              │
              ▼
          ┌──────────┐
          │ CANCELED │   （把剩餘凍結餘額退回可用）
          └──────────┘
```

規則：
- 已 `FILLED` 或 `CANCELED` 的單是終態，不能再變。
- 只有 `PENDING` / `PARTIALLY_FILLED` 能被撮合或取消。

## 5. 下單流程（現況，已有雛形）

對應 `OrderViewSet.create()`，整個包在 `@transaction.atomic`：

1. 決定下單用戶（現況：`get_random_user_id()` 隨機 — 技術債，進階改成 `request.user`）。
2. 用 `OrderCreateUpdateSerializer` 驗證輸入；其中 `validate()` 檢查 `currency1 != currency2`。
3. `check_wallet_balance()`：用 `select_for_update()` 鎖住該用戶 currency1 的錢包，算出要凍結的數量，檢查 `available_balance` 夠不夠，不夠就擋下。
4. `transfer_to_frozen()`：可用 -= total、凍結 += total，存錢包。
5. 存訂單（`perform_create`）。
6.（待補）丟任務到 Celery 撮合：`send_to_match_market.delay()` —— 現況是註解掉的。

## 6. 基本階段：你要完成的事

1. 確認下單 API 能成功建單、且餘額正確凍結（用 admin 看錢包變化驗證）。
2. 確認餘額不足時會被 `check_wallet_balance` 擋下、回 400。
3. 補上查詢 API：列出訂單、查單一訂單、看狀態與待成交數量。
4. 確認 `TransactionViewSet`（目前在 urls 被註解）要不要開——查成交可留到結算章節。

**驗證方式**：用戶有 100000 USDT，下單買 1 BTC @ 30000 → 訂單建立、USDT 可用變 70000、凍結變 30000。再下一張買 3 BTC @ 30000（需 90000）→ 因可用只剩 70000 被擋下。

## 7. 進階階段：逐步加深

- **取消訂單**：算出這張單還有多少凍結沒用到（剩餘數量 × 對應價），退回可用，狀態改 CANCELED。注意要和撮合互斥（鎖訂單），避免「正在撮合時被取消」。
- **市價單**：不帶 price，直接吃訂單簿最好價。凍結金額怎麼算要特別設計（買單可能要凍結一個上限）。
- **下單參數驗證強化**：數量/價格 > 0、符合最小下單量與精度（配合交易對 model）。
- **訂單簿快照 API**：回傳某交易對的買賣掛單聚合（給前端畫深度圖）。

## 8. 常見坑

- **凍結算錯方向**：買單凍結計價幣（USDT）、賣單凍結標的幣（BTC）。搞反就會凍錯錢包。
- **下單和撮合沒分清楚**：下單只負責「驗證+凍結+存單」，不要在 create 裡直接改對手方餘額。撮合是獨立步驟。
- **沒包在 atomic 裡**：凍結餘額和存訂單必須同生同死，中間出錯要一起 rollback，否則會「凍了錢卻沒單」或「有單卻沒凍錢」。
- **用隨機用戶**：`get_random_user_id` 會讓「凍結的錢包」和「真正下單者」對不上，基本階段測試時要心裡有數，進階務必換掉。
