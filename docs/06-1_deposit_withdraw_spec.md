# 細部規格 — 入金 / 出金（deposit / withdraw）〔範圍一：模擬〕

> 對應 app：`member`（端點）、`ledger`（紀錄）　主要 model：`WalletModel`、`DepositWithdrawModel`
> 上層文件：`00_overall_spec.md`　**範圍二續篇：`06-2_deposit_withdraw_spec.md`**（真的接測試鏈）
> 相關：`07-1_logging_audit_spec.md`（`DepositWithdrawModel` 正式欄位定義與記帳 wiring）

這個模組是**範圍一（模擬）通往範圍二（測試鏈）的橋**。設計重點：把入金/出金做成一個清楚的接口，
現在用「模擬」實作，未來換成「鏈上」實作時，**其他模組完全不用動**。

> **關鍵設計原則（整個範圍二能否輕鬆落地，全看這條）**：
> **撮合、結算、訂單、錢包餘額這些核心模組，完全不知道「鏈」的存在。**
> 它們只看到 `available_balance` 變大（入金）或變小（出金）。把鏈的複雜度全部關在這個模組裡，核心就能保持乾淨。
> 這就是為什麼 `03-1`／`04-1`／`05-1` 沒有範圍二的檔案——它們在範圍二一行都不用改。

## 1. 入金 / 出金是什麼

- **入金（Deposit）**：把幣「搬進」交易所，讓你的內部錢包可用餘額增加。
- **出金（Withdraw）**：把幣「搬出」交易所，內部錢包可用餘額減少。

進出的邊界是**交易所的託管邊界**：入金是幣從**你自己保管**（私鑰在你手上）進入**交易所保管**；出金相反。
在中心化交易所裡，**這是鏈唯一真正介入的兩端**——中間的買賣撮合都不上鏈。
所以即使現在完全不碰鏈，把這兩端用「模擬」頂住，整個交易所就能完整運轉。

> 提醒：`WalletModel` **不是區塊鏈錢包**，它只是資料庫裡的數字——本質是一張「交易所欠你多少幣」的**借據（IOU）**。
> 真正的幣（範圍二時）會集中放在交易所自己的鏈上錢包裡。

## 2. 範圍一的模擬實作（已完成）

### 2.1 出金 `POST /api/user/wallet/withdraw/`

`WalletViewSet` 的 `@action(detail=False, methods=['post'])`。

- Request：`{"asset_type_id": <currency_id>, "quantity": "<數量>"}`（`WithdrawSerializer` 驗證，`quantity` 須 > 0）
- 取用戶：`request.user`（用戶只能領自己的錢）
- 行為（全程 `transaction.atomic()`）：
  1. `select_for_update()` 鎖住「該用戶 + 該幣」的錢包
  2. 錢包不存在 → 400
  3. `quantity <= 0` → 400（不可用負數/零出金，否則等於偷偷加錢）
  4. `available_balance < quantity` → 400，且**餘額完全不變**（不可扣到一半）
  5. 通過 → `available_balance -= quantity`
  6. 建一筆 `DepositWithdrawModel(direction=WITHDRAW, status=DONE)` + 寫 `LedgerEntry(reason=WITHDRAW)` 指回它

**鐵則**：**只動 `available_balance`，絕不碰 `frozen_balance`**——凍結中的錢是掛單佔用的，必須先取消訂單才領得回來。
金額一律 `Decimal`，不可用 float。

範圍一不真的把幣送到任何地方，純粹數字減少。

### 2.2 入金 `POST /api/user/wallet/deposit/`（**admin-only**）

`WalletViewSet` 的 `@action(detail=False, methods=['post'])` + `permission_classes=[IsAdminUser]`。

- Request：`{"user_id": <目標用戶>, "asset_type_id": <currency_id>, "quantity": "<金額>"}`
- 對象是 **body 的 `user_id`**，不是 `request.user`（admin 替某用戶入金；這點與出金相反）
- 行為（atomic）：`get_or_create` 該用戶+幣別錢包 → `available += quantity` → 建 `DepositWithdrawModel(direction=DEPOSIT, status=DONE)` + 寫 `LedgerEntry(reason=DEPOSIT)` 指回它
- 非 admin → 403；`quantity <= 0` → 400

> **為什麼入金必須是 admin-only？這是最容易搞錯的觀念。**
>
> 真實 CEX **根本沒有「用戶呼叫 API 說『幫我入金』」這種端點**。用戶是拿到充值地址後，
> 用自己的私鑰在**鏈上**發一筆轉帳——這動作**完全不經過後端**。交易所是靠**背景監聽服務**
> 盯著鏈、偵測到到帳、確認數足夠後，才去加餘額。**入帳永遠是系統觸發，不是用戶觸發。**
>
> 而入金的本質是「**憑空增加餘額**」。真實系統裡這個「憑空」有鏈上依據（真的有人轉幣進來）；
> 範圍一沒有那個依據，所以若開放給一般用戶，等於**任何人都能給自己無限鑄錢**。
>
> 所以這個 admin 端點的定位是：**鏈上監聽器的替身（開發期用）**，不是正式操作。
> 範圍二會被監聽服務取代並**移除**。
>
> 為什麼要建它、而不是繼續用 admin 後台手改數字？因為手改數字**不會寫帳本**，對帳就有破口。
> 建了這條路徑後，每個錢包的餘額才能完整往回追到「入金 → 凍結 → 結算 → 退款 → 出金」一連串有記錄的變動。

### 2.3 入金核心要抽成可重用函式（為範圍二鋪路）

「`available += amount` → 建 `DepositWithdrawModel` → 寫 `LedgerEntry(DEPOSIT)`」這段核心，
**範圍一與範圍二一模一樣**。範圍一由 admin 端點呼叫它，範圍二由鏈上監聽器呼叫它——**同一個函式**。

所以請把它抽成可重用的函式/manager 方法，端點只是薄薄一層殼。
這樣範圍二來臨時，你是「**換觸發器**」，不是「重寫入金」。

## 3. 紀錄 model：`DepositWithdrawModel`

正式欄位定義見 **`07-1_logging_audit_spec.md` §4**（此處不重複）。要點：

- 它是「一次錢進出交易所大門」的**業務紀錄**；`LedgerEntry` 則是它造成的**餘額變動分錄**，用 `ref` 指回它。
- **不是 append-only**：`status` 會轉移（`PENDING → DONE/FAILED`），必須能更新。
- 範圍一：`status` 直接 `DONE`，`tx_hash`/`address` 留空——這兩欄現在用不到，但**先留著**，升級測試鏈時直接用。

## 4. 範圍一：驗收標準

- admin 用 `POST /wallet/deposit/` 給用戶加 100000 USDT → 下單買賣一切正常（下游對入金來源無感）
- 一般用戶打 `deposit` → 403
- 出金 30000 → 可用 −30000；出金超過可用 → 400 且餘額不變；想領超過「可用」（即使凍結很多）→ 400；`quantity <= 0` → 400
- 出入金都各留下一筆 `DepositWithdrawModel` 與對應的 `LedgerEntry`，且**對帳不變量成立**
  （錢包餘額 == 該錢包所有 ledger delta 總和）

測試：`ledger/test/test_deposit_withdraw.py`、`member/test/test_withdraw.py`

## 5. 常見坑（範圍一）

- **入金 delta 寫成負的**：入金是**增加**餘額，`delta` 必須為 **+**。從出金複製貼上時最容易寫反，
  而且會讓對帳直接破功（錢包 +100000、帳本卻記 −100000）。
- **入金忘了 `get_or_create` 錢包**：用戶第一次入金某個幣時，本來就還沒有那個幣的錢包——
  入金正是他取得第一筆餘額的途徑。要求「錢包必須先存在」是雞生蛋。
  （對比出金用 `.get()`、沒錢包回 400 是對的：沒錢包就代表沒餘額可領。**兩邊邏輯本來就該相反**。）
- **出金碰到 `frozen_balance`**：凍結的錢是掛單佔用的，不可出金。
- **只改餘額沒寫帳本**（或反之）：兩者必須在**同一個 `atomic`** 內，否則帳本與錢包對不上。
