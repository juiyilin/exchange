# 細部規格 — 入金 / 出金（deposit / withdraw）

> 對應 app：`member`（未來可獨立成 app）　主要 model：`WalletModel`
> 上層文件：`00_overall_spec.md`

這個模組是**範圍 1（模擬）通往範圍 2（測試鏈）的橋**。設計重點是：把入金/出金做成一個清楚的接口，現在用「模擬」實作，未來換成「鏈上」實作時，其他模組完全不用動。

## 1. 入金 / 出金是什麼

- **入金（Deposit）**：把幣「搬進」交易所，讓你的內部錢包可用餘額增加。
- **出金（Withdraw）**：把幣「搬出」交易所，內部錢包可用餘額減少。

在中心化交易所裡，這是鏈唯一真正介入的兩端。中間的買賣撮合都不上鏈。所以即使現在完全不碰鏈，把這兩端用「模擬」頂住，整個交易所就能完整運轉。

## 2. v0.1 模擬實作

### 模擬入金

最簡單：直接在 admin 後台改 `WalletModel.available_balance` 加上去。
進一步：做一個 `POST /api/deposit/` 內部 API（或 admin action），輸入用戶+幣別+金額，原子地把 available_balance 加上去，並（建議）寫一筆入金紀錄。

### 模擬出金（v0.1 基本，已定案的契約）

端點：**`POST /api/user/wallet/withdraw/`**（做成 `WalletViewSet` 的 `@action(detail=False, methods=['post'])`，沿用 member 既有 `/api/user/` 路由）。

Request body：`{"asset_type_id": <currency_id>, "quantity": "<數量>"}`（由 `WithdrawSerializer` 驗證；`quantity` 設 `min_value` 且 `validate_quantity` 擋掉 0）

取用戶：沿用現有暫時做法 `get_random_user()`（認證是 M7 才換成 `request.user`）。

行為（全部包在 `transaction.atomic()`）：

1. `select_for_update()` 鎖住「該用戶 + 該幣」的錢包。
2. 錢包不存在 → 回 400。
3. `quantity <= 0` → 回 400（不可用負數/零出金，否則等於偷偷加錢）。
4. `available_balance < quantity` → 回 400，且**餘額完全不變**（不可扣到一半）。
5. 通過 → `available_balance -= quantity`，存檔，回 **200**。

鐵則：**只動 `available_balance`，絕不碰 `frozen_balance`**——凍結中的錢是掛單佔用的，必須先取消訂單才領得回來。金額一律用 `Decimal` 處理，不可用 float。

v0.1 不真的把幣送到任何地方，純粹數字減少。**基本階段不寫任何出金紀錄/log**（紀錄 model 見下，整個延到後續/範圍 2 再做）。

### 建議的紀錄 model（DepositWithdrawModel，進階）

> 正式欄位定義與整體 log 設計見 `07_logging_audit_spec.md` §4；此處為摘要。

| 欄位        | 說明                                     |
| ----------- | ---------------------------------------- |
| `user`      | 誰                                       |
| `currency`  | 哪種幣                                   |
| `quantity`  | 金額                                     |
| `direction` | 入金 / 出金                              |
| `status`    | 處理中 / 完成 / 失敗                     |
| `tx_hash`   | 鏈上交易雜湊（範圍 1 留空，範圍 2 才填） |
| `address`   | 對方地址（範圍 1 留空）                  |

`tx_hash` 和 `address` 現在用不到，但先留欄位，升級到測試鏈時直接用。

## 3. 基本階段：你要完成的事

1. **模擬入金**：用 admin 手動改 `available_balance` 加數字（基本階段不另做 API）。
2. **模擬出金**：做上面定案的 `POST /api/user/wallet/withdraw/`——鎖錢包、檢查（不存在 / quantity≤0 / 可用不足）各回 400、通過則扣 available、回 200，全程 atomic、只動 available。**不寫紀錄。**
3. 確認入金後的餘額能被下單/撮合正常使用（其實就是 `available_balance` 變大而已，下游無感）。

**驗證方式**：

- admin 給用戶加 100000 USDT → 下單買賣一切正常。
- 出金 30000 → 可用 −30000；出金超過可用 → 400 且餘額不變；想領超過「可用」(即使凍結很多) → 400；quantity≤0 → 400。

## 4. 升級到範圍 2（測試鏈）— 預留路徑

當你想真的碰鏈，把「模擬實作」換成「鏈上實作」，接口不變：

### 入金（鏈上）

1. 為每個用戶+幣別產生一個**充值地址**（HD wallet 派生地址）。
2. 跑一個**監聽服務**（背景任務，輪詢或 webhook）盯著這些地址的鏈上到帳。
3. 偵測到一筆轉入 → 等足夠**確認數（confirmations）**（例如 BTC 等 3~6 個區塊）→ 把對應金額加到該用戶內部錢包可用餘額，並記 `tx_hash`。

### 出金（鏈上）

1. 用戶發起出金到某個外部地址。
2. 先扣內部可用餘額（凍結/標記處理中）。
3. 從交易所**熱錢包**簽署並廣播一筆鏈上交易把幣送出。
4. 等確認 → 標記完成、記 `tx_hash`；失敗 → 退回餘額。

### 會用到的概念與工具（到時候再學）

- 測試網（testnet / Sepolia 等），不用真錢。
- 節點存取：自架節點或用 Infura / Alchemy 之類的 RPC 服務。
- 函式庫：以太系用 `web3.py`，比特系用 `bitcoinlib` 之類。
- 私鑰保管、確認數、Gas 費、nonce 管理——這些都是範圍 2 的新功課。

> 關鍵設計原則：**撮合、結算、訂單、錢包餘額這些核心模組，完全不知道「鏈」的存在**。它們只看到 `available_balance` 變大（入金）或變小（出金）。把鏈的複雜度全部關在這個模組裡，核心就能保持乾淨。

## 5. 常見坑（範圍 2 預警）

- **確認數不夠就入帳**：鏈會分叉，太早入帳可能對方反悔（雙花攻擊）。一定要等足夠確認。
- **私鑰外洩**：熱錢包私鑰若寫死在程式或 commit 進 git，等於把錢包公開。務必用環境變數/金鑰管理服務。
- **出金重放/重複送**：出金任務要冪等（idempotent），同一筆出金不能因重試送兩次。
- **精度換算**：鏈上單位（wei、satoshi）和顯示單位（ETH、BTC）差很多位，換算錯會差好幾個數量級。
