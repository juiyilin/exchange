# 細部規格 — 用戶與錢包（member）

> 對應 app：`member`　主要 model：`UserProfileModel`、`WalletModel`
> 上層文件：`00_overall_spec.md`

## 1. 這個模組負責什麼

兩件事：

1. **用戶**：誰在用這個交易所。沿用 Django 內建的 `User`，額外資料放 `UserProfileModel`。
2. **錢包（內部帳本）**：記錄「每個用戶持有多少哪種幣」。這是整個交易所最敏感、最不能算錯的資料——錢就在這裡。

## 2. 核心概念：內部帳本與可用/凍結餘額

每個用戶、每種幣，有一個錢包（`WalletModel`）。錢包裡的餘額分兩部分：

- **可用餘額（available_balance）**：可以自由使用的錢，能拿去下單。
- **凍結餘額（frozen_balance）**：已經被某張掛單佔住、暫時不能動的錢。

**為什麼要分兩個？**
假設你有 30000 USDT，掛了一張「用 30000 買 1 BTC」的單。這 30000 還沒花掉（單還沒成交），但也不能讓你拿去掛另一張單——不然你會用同一筆錢掛兩張單，成交時錢不夠。所以下單當下就把 30000 從「可用」搬到「凍結」。等成交，從凍結扣掉、把買到的 BTC 加進 BTC 錢包的可用；等取消，把凍結退回可用。

### 餘額不變量（invariant）— 最重要的一條規則

> **任何時刻，對任何一個錢包：`available_balance >= 0` 且 `frozen_balance >= 0`。**
> **搬動只在「可用」「凍結」之間進行，總量在「凍結+結算」之外不會憑空增減。**

只要有任何操作可能讓餘額變負，就是 bug。所有下單、撮合、結算、取消的邏輯，都要圍著「維持這條不變量」設計。

## 3. 資料模型（現況）

### UserProfileModel（繼承 BaseTimeModel）
| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | OneToOne → User | 對應的 Django 用戶 |
| `phone_number` | CharField(20) | 電話 |
| `address` | CharField(255) | 地址 |

### WalletModel（繼承 BaseTimeModel）
| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | FK → User | 擁有者 |
| `asset_type` | FK → CurrencyModel | 哪種幣 |
| `available_balance` | Decimal(20,2) | 可用餘額 |
| `frozen_balance` | Decimal(20,2) | 凍結餘額 |

約束：`unique_together = (user, asset_type)` — 每人每幣只能有一個錢包。這個約束很關鍵，避免同一人同一幣出現兩個錢包導致餘額分裂。

## 4. API（基本階段）

現況 `member/views.py` 有 `UserViewSet` 和 `WalletViewSet`，URL 在 `/api/user/...`：

- `GET/POST /api/user/user/` — 用戶
- `GET/POST /api/user/wallet/` — 錢包

> **注意現況的臨時做法**：`WalletViewSet.perform_create` 用 `get_random_user_id()` 隨機指派用戶，這只是還沒做認證前的 placeholder。進階階段要換成「綁定當前登入用戶」。文件先記著這個技術債。

基本階段 API 目標：

- 能建立用戶
- 能為用戶建立各幣別的錢包
- 能查詢某用戶的所有錢包餘額

## 5. 基本階段：你要完成的事

1. admin 後台註冊 `UserProfileModel`、`WalletModel`，能手動檢視/修改餘額（這同時是你「模擬入金」的方法——直接在 admin 改 available_balance）。
2. 確認建立用戶時能順帶建 profile（或允許 profile 為空）。
3. 為測試用戶各建一個 USDT 錢包和一個 BTC 錢包，給 USDT 錢包一些初始可用餘額（模擬入金）。
4. 能透過 API 或 admin 查到餘額。

**驗證方式**：建一個用戶、給他 100000 USDT 可用餘額，API 查得到 `available_balance=100000, frozen_balance=0`。

## 6. 進階階段：逐步加深

### 6.1 真實認證（取代隨機指派用戶）— 重要技術債
目前所有「誰在操作」都靠 `get_random_user_id()` 亂猜，這是 demo 用的。進階要做：

- 註冊 API（建 User + Profile + 初始錢包）
- 登入 API，發 Token（DRF 的 TokenAuthentication 或 JWT）
- 所有錢包/訂單 API 改成「只能操作 `request.user` 自己的資料」
- 移除 `get_random_user_id` 的用法

### 6.2 帳本流水（Ledger Entry）
目前餘額只存「現在多少」，改了就沒了，無法稽核。進階加一張 `LedgerEntryModel`：每次餘額變動（下單凍結、成交、取消、入金、出金）都寫一筆不可刪的紀錄（誰、哪個錢包、變動類型、金額、變動前後餘額、關聯訂單/成交）。這樣任何餘額都能對帳，出問題能追。真實交易所這是鐵律。

### 6.3 自動建錢包
用戶第一次碰到某幣別時自動建立該幣錢包（`get_or_create`），不用預先建好所有幣的錢包。

### 6.4 精度
`decimal_places=2` 對 BTC 不夠，配合 `01_currency_spec.md` 的精度設計一起調整。

## 7. 常見坑

- **餘額更新沒有原子性**：「讀餘額→改→存」這三步，如果兩個請求同時跑，會互相覆蓋（race condition），導致超賣。下單那段已經用了 `select_for_update()`，所有改餘額的地方都要這樣鎖。詳見 `04_matching_engine_spec.md` 的併發章節。
- **直接改可用餘額卻忘了凍結**：扣可用一定要對應加凍結（或對外轉出），兩邊要一起改、包在同一個 `transaction.atomic()` 裡，不能只改一半。
- **忘記 unique_together**：同一人同一幣建出兩個錢包，餘額就分裂了。
