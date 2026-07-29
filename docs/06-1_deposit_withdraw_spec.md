# 細部規格 — 入金 / 出金（deposit / withdraw）〔範圍一：模擬〕

> 上層文件：`00_overall_spec.md`　**範圍二續篇：`06-2_deposit_withdraw_spec.md`**（真的接測試鏈）
> 相關：`07-1_logging_audit_spec.md`（出入金紀錄的正式欄位定義與記帳 wiring）

## 一、需求方規格

### 1. 入金 / 出金是什麼

- **入金（Deposit）**：把幣「搬進」交易所，使用戶在交易所內部的可用餘額增加。
- **出金（Withdraw）**：把幣「搬出」交易所，使用戶在交易所內部的可用餘額減少。

入金與出金構成交易所的**託管邊界**：入金是幣由用戶自己保管轉為交易所保管，出金相反。在中心化交易所中，這兩端是資產真正進出交易所的唯二節點，中間的買賣撮合不涉及資產進出交易所。

用戶在交易所內的餘額本質是一張「交易所欠用戶多少幣」的借據，代表交易所對用戶的負債，並非用戶自持的資產。

### 2. 出金的對外行為與邊界

- 用戶只能對**自己**的餘額提出出金。
- 出金數量必須為**正**；數量為零或負值一律拒絕（否則等同於偷偷增加餘額）。
- 當該幣別的可用餘額不足以支付出金數量時，出金被**拒絕**，且該用戶餘額**完全不變**（不允許只扣一部分）。
- 出金只能動用**可用餘額**；被**凍結**的餘額（掛單佔用中）不可被領走，用戶必須先取消對應訂單，凍結的錢釋放回可用後才能出金。
- 出金成功後，可用餘額按出金數量減少。
- 範圍一為模擬,不真的把幣送往任何地方，僅為內部餘額的數字減少。

### 3. 入金的對外行為與邊界

- 入金會使目標用戶的可用餘額增加。
- 入金**僅限管理員操作**，一般用戶不得自行發起入金；一般用戶嘗試入金一律**被拒絕（無權限）**。
- 入金數量必須為**正**。
- 入金的對象是被指定的目標用戶（由管理員代為入金），而非發起操作者本人；這一點與出金相反（出金只能對操作者自己）。

**為何入金僅限管理員**：真實的中心化交易所沒有「用戶呼叫介面說『幫我入金』」這種行為，用戶是拿到充值地址後在鏈上自行轉帳，交易所靠背景服務監聽鏈上到帳後才增加餘額，入帳永遠由系統偵測觸發而非用戶觸發。入金的本質是「憑空增加餘額」；真實系統中這個「憑空」有鏈上依據（確實有人把幣轉進來），而範圍一沒有任何鏈上依據，若開放給一般用戶，等同任何人都能為自己無限鑄幣。因此範圍一的入金操作僅作為「鏈上監聽服務的替身」，供開發期使用，範圍二將由監聽服務取代並移除。

### 4. 範圍一：驗收標準

- 管理員為某用戶增加一筆餘額（例如 100000 USDT）後，該用戶下單買賣一切正常，下游功能對入金來源無感。
- 一般用戶嘗試入金 → 被拒絕（無權限）。
- 出金 30000 → 可用餘額減少 30000；出金超過可用餘額 → 被拒絕且餘額不變；即使凍結餘額很多，出金超過「可用」部分 → 被拒絕；出金數量為零或負值 → 被拒絕。
- 出金與入金各留下一筆出入金業務紀錄與對應的餘額變動分錄，且**對帳不變量成立**：每個錢包的餘額等於該錢包所有餘額變動分錄的總和。

## 二、開發方規格

> 對應 app：`member`（端點）、`ledger`（紀錄）　主要 model：`WalletModel`、`DepositWithdrawModel`

本模組是範圍一（模擬）通往範圍二（測試鏈）的橋。設計上把入金/出金做成清楚的接口，範圍一以「模擬」實作，範圍二換成「鏈上」實作時其他模組不需改動。撮合、結算、訂單、錢包餘額等核心模組完全不感知「鏈」的存在，它們只看到 `available_balance` 變大（入金）或變小（出金），鏈的複雜度全部收斂在本模組內；這也是 `03-1`／`04-1`／`05-1` 沒有範圍二續篇的原因。

> `WalletModel` 不是區塊鏈錢包，只是資料庫裡的數字，本質是一張借據（IOU）。範圍二時真正的幣會集中放在交易所自己的鏈上錢包。

### 1. 出金端點 `POST /api/user/wallet/withdraw/`

`WalletViewSet` 的 `@action(detail=False, methods=['post'])`。

- Request：`{"asset_type_id": <currency_id>, "quantity": "<數量>"}`（`WithdrawSerializer` 驗證，`quantity` 須 > 0）
- 取用戶：`request.user`（用戶只能領自己的錢）
- 行為（全程 `transaction.atomic()`）：
  1. `select_for_update()` 鎖住「該用戶 + 該幣」的錢包
  2. 錢包不存在 → 400
  3. `quantity <= 0` → 400
  4. `available_balance < quantity` → 400，且餘額完全不變
  5. 通過 → `available_balance -= quantity`
  6. 建一筆 `DepositWithdrawModel(direction=WITHDRAW, status=DONE)` 並寫 `LedgerEntry(reason=WITHDRAW)` 以 `ref` 指回它

### 2. 入金端點 `POST /api/user/wallet/deposit/`（admin-only）

`WalletViewSet` 的 `@action(detail=False, methods=['post'])`，搭配 `permission_classes=[IsAdminUser]`。

- Request：`{"user_id": <目標用戶>, "asset_type_id": <currency_id>, "quantity": "<金額>"}`
- 對象是 body 的 `user_id`，不是 `request.user`
- 行為（atomic）：`get_or_create` 該用戶+幣別錢包 → `available_balance += quantity` → 建 `DepositWithdrawModel(direction=DEPOSIT, status=DONE)` 並寫 `LedgerEntry(reason=DEPOSIT)` 指回它
- 非 admin → 403；`quantity <= 0` → 400

### 3. 入金核心抽成可重用函式（為範圍二鋪路）

「`available_balance += amount` → 建 `DepositWithdrawModel` → 寫 `LedgerEntry(DEPOSIT)`」這段核心在範圍一與範圍二一致：範圍一由 admin 端點呼叫，範圍二由鏈上監聽器呼叫，應為同一個函式。請將它抽成可重用的函式/manager 方法，端點僅為薄殼，範圍二只需替換觸發器而非重寫入金。

相較於直接在 admin 後台手改數字，改用此端點的理由是：手改不會寫帳本，會在對帳上造成破口。經由此路徑，每個錢包餘額才能完整往回追溯每一筆有紀錄的變動。

### 4. 紀錄 model：`DepositWithdrawModel`

正式欄位定義見 `07-1_logging_audit_spec.md` §4（此處不重複）。要點：

- 它是「一次錢進出交易所大門」的業務紀錄；`LedgerEntry` 是它造成的餘額變動分錄，以 `ref` 指回它。
- 不是 append-only：`status` 會轉移（`PENDING → DONE/FAILED`），必須能更新。
- 範圍一：`status` 直接為 `DONE`，`tx_hash`／`address` 留空，此二欄目前不使用但保留，升級測試鏈時直接沿用。

### 5. 技術約束/注意事項

- **出金只動 `available_balance`，絕不碰 `frozen_balance`**：凍結中的錢是掛單佔用的，須先取消訂單釋放後才可出金。
- **入金與出金的錢包取得邏輯相反**：入金用 `get_or_create`（用戶首次入金某幣時該幣錢包尚不存在，入金正是取得第一筆餘額的途徑）；出金用 `.get()`，無錢包即代表無餘額可領，回 400。
- **入金的 delta 為正**：入金是增加餘額，`LedgerEntry` 的 `delta` 必須為正；若寫成負值，會使錢包與帳本方向相反、對帳破功。
- **餘額變動與帳本分錄必須在同一個 `atomic` 內**完成，否則帳本與錢包會對不上。
- **金額一律使用 `Decimal`，不可用 float。**
- **對帳不變量**：錢包餘額須等於該錢包所有 ledger delta 的總和。

> 測試：`ledger/test/test_deposit_withdraw.py`、`member/test/test_withdraw.py`
