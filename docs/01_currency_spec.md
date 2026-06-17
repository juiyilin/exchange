# 細部規格 — 幣別與交易對（currency）

> 對應 app：`currency`　主要 model：`CurrencyModel`
> 上層文件：`00_overall_spec.md`

## 1. 這個模組負責什麼

定義「系統裡存在哪些幣」。幣別是一切的基礎——錢包要指定是哪種幣的錢包、訂單要指定買哪種幣賣哪種幣。所以這是第一個要做好的模組，資料量小但被所有人依賴。

## 2. 概念

### 幣別（Currency）
最小單位。每種幣需要兩個資訊：

- `code`：代碼，全大寫、唯一，例如 `USDT`、`BTC`、`ETH`。程式裡都用 code 來辨識。
- `name`：全名，給人看的，例如 `Tether`、`Bitcoin`。

### 交易對（Trading Pair）
兩種幣的買賣組合，寫作 `BASE/QUOTE`，例如 `BTC/USDT`：

- **BASE（標的幣）**：你想買賣的東西，這裡是 BTC。
- **QUOTE（計價幣）**：用來定價、付款的幣，這裡是 USDT。
- **價格**：1 顆 BASE 值多少 QUOTE。`BTC/USDT = 30000` 表示 1 BTC = 30000 USDT。

> **v0.1 的簡化**：目前沒有獨立的「交易對」model。訂單上直接記 `currency1`（出去的幣）和 `currency2`（進來的幣），由訂單自己描述買賣關係。交易對的概念隱含在訂單裡。等進階階段再把交易對獨立出來（見第 6 節）。

## 3. 資料模型（現況）

`CurrencyModel`（繼承 `BaseTimeModel`，自帶 `created_at` / `updated_at`）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `code` | CharField(10), unique | 幣別代碼，如 `BTC` |
| `name` | CharField(50) | 幣別名稱，如 `Bitcoin` |

這個模型目前已經夠用，基本階段不需要改。

## 4. API（基本階段）

目前 `currency/views.py` 是空的。基本階段要補上一個唯讀或可增刪的 API：

- `GET /api/currency/` — 列出所有幣別
- `GET /api/currency/{id}/` — 查單一幣別
- `POST /api/currency/`（選做）— 新增幣別（也可只在 admin 後台建）

> 提示：幣別資料很少變動，初期甚至可以只用 Django admin 後台手動建幾筆（USDT、BTC、ETH），先不做 API，把力氣留給核心的撮合。

## 5. 基本階段：你要完成的事

1. 在 admin 後台註冊 `CurrencyModel`，能手動新增幣別。
2. 建立至少 2 種幣（建議 `USDT` 和 `BTC`，方便之後測 `BTC/USDT`）。
3.（選做）寫一個 `CurrencyViewSet` 提供查詢 API，並掛上 URL。

**驗證方式**：進 admin 看得到幣別、或打 `GET /api/currency/` 拿得到列表。

## 6. 進階階段：逐步加深

當核心撮合做完後，回頭強化這個模組：

### 6.1 獨立的交易對 model（`TradingPairModel`）
把「哪些幣可以互相交易、以及交易規則」獨立出來。欄位構想：

| 欄位 | 說明 |
|---|---|
| `base` | FK 到 CurrencyModel（標的幣） |
| `quote` | FK 到 CurrencyModel（計價幣） |
| `min_order_amount` | 最小下單數量 |
| `price_precision` | 價格小數位數 |
| `amount_precision` | 數量小數位數 |
| `fee_rate` | 手續費率 |
| `is_active` | 是否開放交易 |

有了交易對後，下單就改成「指定交易對 + 買/賣方向」，比現在的 `currency1/currency2` 更清楚，也能集中管理每個市場的規則。

### 6.2 幣別精度（decimals）
不同幣的小數位數不同（BTC 到 8 位、USDT 到 2 位）。現在 `DecimalField` 統一用 `decimal_places=2`，進階時可為每種幣設定自己的精度。

### 6.3 啟用/停用
加 `is_active` 欄位，下架某幣別時擋掉相關下單。

## 7. 常見坑

- **code 大小寫不一致**：統一存大寫，存進去前 `.upper()`，否則 `btc` 和 `BTC` 會被當兩種幣。
- **精度（decimal_places）太小**：`2` 對 BTC 不夠（BTC 常見到小數第 8 位）。基本階段先不管，進階要正視。
- **用浮點數算錢**：絕對不要用 `float`。一律用 `Decimal`（Django 的 `DecimalField` 已經幫你處理），否則會有 `0.1 + 0.2 != 0.3` 的精度災難。
