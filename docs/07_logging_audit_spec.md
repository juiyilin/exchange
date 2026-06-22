# 細部規格 — 日誌與帳本（logging / audit）

> 對應 app：`member`（帳本）、`transaction`（成交/訂單事件）、入金出金
> 上層文件：`00_overall_spec.md`　相關：`02_member_wallet_spec.md`、`05_settlement_spec.md`、`06_deposit_withdraw_spec.md`
>
> **狀態：設計先定案，實作全部延後（屬進階）。** 基本階段(v0.1)不寫任何 log；此文件是之後實作的依據。

## 1. 為什麼需要 log

錢的系統必須可稽核：任何餘額變動都要能回答五件事——**誰、何時、因為什麼、變動多少、變動前後是多少**。出問題能追根、能對帳、能重建。目前系統「改了 `WalletModel` 的數字就沒了」，無法回答這些，所以需要一套紀錄。

## 2. 三層紀錄（各司其職）

| 紀錄 | 是什麼 | 現況 |
|---|---|---|
| **A. 帳本流水 `LedgerEntry`** | 每一次 `available`/`frozen` 的變動都寫一筆不可變紀錄（核心） | 未做 |
| **B. 入金/出金紀錄 `DepositWithdrawModel`** | 一次「錢進出交易所大門」的業務紀錄，含鏈上欄位 | 未做（見 `06`） |
| **C. 訂單 / 成交事件** | `OrderModel`（下單意圖）、`TransactionModel`（成交） | **已存在**，配 `created_at`/`ordered_at` 已是事件紀錄 |

C 已經有了；要補的是 **A（帳本流水）** 和 **B（入出金紀錄）**。A 是地基——所有餘額變動的單一真相來源。

## 3. 帳本流水 `LedgerEntry`（核心設計）

放在 `member`（跟 `WalletModel` 同 app）。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | FK → User | 誰 |
| `asset_type` | FK → CurrencyModel | 哪種幣（或直接 FK 到 wallet） |
| `entry_type` | choices | 變動原因：`FREEZE`(下單凍結)、`UNFREEZE`(取消解凍)、`SETTLE_IN`(結算收到)、`SETTLE_OUT`(結算付出)、`REFUND`(多凍結退款)、`DEPOSIT`(入金)、`WITHDRAW`(出金)、`FEE`(手續費) |
| `balance_field` | choices | 動到哪個欄位：`AVAILABLE` / `FROZEN` |
| `delta` | Decimal(20,2) | 變動量，帶正負號（+ 增、− 減） |
| `balance_after` | Decimal(20,2) | 該欄位變動後的值（存當下快照，方便稽核/重建） |
| `ref_type` | CharField | 來源事件類型：`order` / `transaction` / `deposit_withdraw` / `manual` |
| `ref_id` | Integer/Char | 來源事件的 id（哪張訂單、哪筆成交） |
| `created_at` | DateTime(auto_now_add) | 何時 |

**鐵則：**

1. **append-only**：只新增，**絕不 update、絕不 delete**。寫錯就再寫一筆反向修正。
2. **與餘額變動同一個 `atomic`**：每次改 `WalletModel` 的 `available`/`frozen`，就在**同一個交易**內寫對應的 `LedgerEntry`。不能餘額改了、log 沒寫（或反之），否則帳本和錢包就對不上。
3. **可對帳的不變量**：任一錢包當前 `available`（或 `frozen`）== 該錢包所有對應 `balance_field` 的 `delta` 總和。對帳工作就是定期掃這條等式。

## 4. 入金/出金紀錄 `DepositWithdrawModel`

（與 `06` 對齊；此處為正式欄位定義，`06` 引用本節。）

| 欄位 | 說明 |
|---|---|
| `user` | 誰 |
| `asset_type` | 哪種幣 |
| `amount` | 金額 |
| `direction` | `DEPOSIT` / `WITHDRAW` |
| `status` | `PENDING`(處理中) / `DONE`(完成) / `FAILED`(失敗) |
| `tx_hash` | 鏈上交易雜湊（範圍 1 留空，範圍 2 才填） |
| `address` | 對方地址（範圍 1 留空，範圍 2 才填） |
| `created_at` / `updated_at` | 時間 |

範圍 1（模擬）：`status` 直接 `DONE`、`tx_hash`/`address` 留空。範圍 2（測試鏈）：依鏈上確認流程更新 `status` 與 `tx_hash`。

## 5. 哪些操作要寫 log（套用點）

每個會動餘額的地方，在同一個 `atomic` 內補寫紀錄：

| 操作 | 寫什麼 |
|---|---|
| 下單凍結（`03`） | `LedgerEntry`：FREEZE（frozen +）、AVAILABLE（−） |
| 撮合結算（`05`） | 雙方各 `LedgerEntry`：SETTLE_OUT（frozen −）、SETTLE_IN（available +） |
| 取消訂單（`04`） | `LedgerEntry`：UNFREEZE（frozen −、available +） |
| 多凍結退款（`04`） | `LedgerEntry`：REFUND |
| 入金 | `DepositWithdrawModel` + `LedgerEntry`：DEPOSIT（available +） |
| 出金（`06`） | `DepositWithdrawModel` + `LedgerEntry`：WITHDRAW（available −） |
| 手續費（進階） | `LedgerEntry`：FEE |

## 6. 實作建議（延後，屬進階）

- **顯式寫，不要用 signal**：在各業務函式（結算、取消、出金…）內**明確地**寫 `LedgerEntry`，放進同一個 `atomic`。不要用 Django signal 自動寫——signal 難追蹤、也難保證和餘額變動在同一交易裡。
- **建議順序**：先做 `LedgerEntry`（它能對帳整個系統、價值最高），再做 `DepositWithdrawModel`。
- **回填**：上線前的測試資料沒有 log 是正常的；正式啟用 log 後，餘額對帳的基準從那刻起算。

## 7. 常見坑

- **log 與餘額不同步**：沒包在同一個 `atomic` → 帳本和錢包對不上。這是最常見也最致命的問題。
- **改舊紀錄**：append-only 被破壞，稽核就失去意義。要修正用「反向分錄」，不要改舊的。
- **漏掉 `balance_after`**：只記 delta、不記變動後餘額，重建時雖能算，但少了交叉驗證的能力。建議兩個都存。
- **精度**：一律 `Decimal`，與錢包欄位同精度。
