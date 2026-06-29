# 細部規格 — 日誌與帳本（logging / audit）

> 對應 app：**`ledger`（新增；帳本流水 + 入出金紀錄）**、`transaction`（成交/訂單事件，已存在）
> 上層文件：`00_overall_spec.md`　相關：`02_member_wallet_spec.md`、`05_settlement_spec.md`、`06_deposit_withdraw_spec.md`
>
> **狀態：設計定案、實作進行中（M-日誌與帳本，屬進階）。** 基本階段(v0.1)不寫任何 log；此文件是實作的依據。
>
> **架構決策（2026-06，與原規格的差異）**：原本把 `LedgerEntryModel` 規劃在 `member`（跟 `WalletModel` 同 app）。
> 改為**獨立成新的 `ledger` app**，把帳本流水 `LedgerEntryModel` 與入出金紀錄 `DepositWithdrawModel` 收在一起（兩者都偏稽核性質）。
> 這樣拆只有一個前提必須守住，否則會循環依賴 → 見 §3.1。

## 1. 為什麼需要 log

錢的系統必須可稽核：任何餘額變動都要能回答五件事——**誰、何時、因為什麼、變動多少、變動前後是多少**。出問題能追根、能對帳、能重建。目前系統「改了 `WalletModel` 的數字就沒了」，無法回答這些，所以需要一套紀錄。

## 2. 三層紀錄（各司其職）

| 紀錄 | 是什麼 | 現況 |
|---|---|---|
| **A. 帳本流水 `LedgerEntryModel`** | 每一次 `available`/`frozen` 的變動都寫一筆不可變紀錄（核心） | 未做 |
| **B. 入金/出金紀錄 `DepositWithdrawModel`** | 一次「錢進出交易所大門」的業務紀錄，含鏈上欄位 | 未做（見 `06`） |
| **C. 訂單 / 成交事件** | `OrderModel`（下單意圖）、`TransactionModel`（成交） | **已存在**，配 `created_at`/`ordered_at` 已是事件紀錄 |

C 已經有了；要補的是 **A（帳本流水）** 和 **B（入出金紀錄）**。A 是地基——所有餘額變動的單一真相來源。

## 3. 帳本流水 `LedgerEntryModel`（核心設計）

放在新的 `ledger` app。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | FK → User | 誰 |
| `asset_type` | FK → CurrencyModel | 哪種幣。**只 FK 到 User + CurrencyModel，不要 FK 到 `WalletModel`**（見 §3.1） |
| `reason` | choices | 變動原因：`FREEZE`(下單凍結)、`UNFREEZE`(取消解凍)、`SETTLE`(撮合成交結算)、`REFUND`(多凍結退款)、`DEPOSIT`(入金)、`WITHDRAW`(出金)、`TRADING_FEE`(交易手續費)。顆粒度判準見 §3.2 |
| `balance_field` | choices | 動到哪個欄位：`AVAILABLE` / `FROZEN` |
| `delta` | Decimal(20,2) | 變動量，帶正負號（+ 增、− 減） |
| `balance_after` | Decimal(20,2) | 該欄位變動後的值（存當下快照，方便稽核/重建） |
| `ref_type` | CharField | 來源事件類型：`order` / `transaction` / `deposit_withdraw` / `manual`。**軟參照(純字串)，不要 FK**（見 §3.1） |
| `ref_id` | CharField | 來源事件的 id（哪張訂單、哪筆成交）。同上，存 `str(pk)` 即可，不要 FK |
| `created_at` | DateTime(auto_now_add) | 何時 |

> **一個操作可能寫多筆**：規則是「**每動一個 `balance_field` 就寫一筆**」。例如下單凍結同時動 available(−) 與 frozen(+)，
> 就寫兩筆 `FREEZE`（一筆 `AVAILABLE` delta 負、一筆 `FROZEN` delta 正）。一筆成交動到買賣雙方共四個餘額欄位 → 四筆。

### 3.1 拆成獨立 app 的唯一前提：保持單向依賴

`ledger` 被 `member`、`transaction` 依賴（它們在自己的業務函式裡 import `LedgerEntryModel` 來寫紀錄）。
為了不形成循環依賴，`ledger` 自己**只能向下依賴 `currency`/`common`，不准反向 import 任何上層 model**。具體兩條鐵律：

1. `LedgerEntryModel.asset_type` **FK 到 `CurrencyModel`**，不要「直接 FK 到 wallet」。若 FK 到 `WalletModel`，就變成 `ledger → member` 且 `member → ledger` = 循環依賴。
2. `ref_type` / `ref_id` 用**軟參照（字串）**，不要做成真 FK 指向 `OrderModel` / `TransactionModel`。否則 `ledger → transaction` 且 `transaction → ledger` = 循環依賴。

守住這兩條，依賴層次就是乾淨的單向鏈：`common ← currency ← ledger ← member ← transaction`。

**鐵則：**

1. **append-only**：只新增，**絕不 update、絕不 delete**。寫錯就再寫一筆反向修正。
2. **與餘額變動同一個 `atomic`**：每次改 `WalletModel` 的 `available`/`frozen`，就在**同一個交易**內寫對應的 `LedgerEntryModel`。不能餘額改了、log 沒寫（或反之），否則帳本和錢包就對不上。
3. **可對帳的不變量**：任一錢包當前 `available`（或 `frozen`）== 該錢包所有對應 `balance_field` 的 `delta` 總和。對帳工作就是定期掃這條等式。

### 3.2 `reason` 的顆粒度原則（要合併還是細分）

判準**不是**「正負號能不能還原」，而是「**這是同一個業務事件的多條腿，還是不同的業務事件**」：

- **同一事件的多條腿 → 同一個 `reason`**，靠 `balance_field` 與 `delta` 正負區分各腿。
  例：`FREEZE`(available − / frozen +)、`UNFREEZE`、`REFUND`、以及 `SETTLE`(收 = available + / 付 = frozen −) 都是「一個 reason、兩條腿」。
  （`SETTLE` 即原 `SETTLE_IN`/`SETTLE_OUT` 合併而來：它們是同一次成交的兩條腿，分成兩個 reason 是多餘的。）
- **不同的業務事件 → 各自一個 `reason`**。
  例：`DEPOSIT` 與 `WITHDRAW` 雖都只動 available、只差正負，但它們是不同時間各自發生的獨立事件（各有一筆 `DepositWithdrawModel`、`direction` 已區分），報表上也分別統計，故**不合併**。

`reason` 不可被「`delta` + `balance_field`」取代，因為它記的是「業務上為什麼」這個推不出來的資訊。
**關鍵反例**：FROZEN 減少、delta 為負，可能是 `SETTLE`(付給對手成交) 也可能是 `UNFREEZE`/`REFUND`(退回自己)——同欄位、同方向、意義相反，只能靠 `reason` 區分。

**manual 的細分同理**：`ref_type="manual"` 只表示「找不到上游業務 FK」，要區分種類就把在意的情境**升格成獨立 `reason`**
（如 `ADMIN_ADJUST` 後台調整 / `COMPENSATION` 補償 / `CORRECTION` 修正錯帳），別全擠在一個值看不出來。
搭配建議：加 `memo`(自由文字緣由) 與 `operator`(FK→User，誰做的) 兩欄，手動動帳才能問責；修正錯帳的反向分錄則用 `ref` 指回被修正的原 entry。
這些都**等對應功能真的出現再加**即可——`reason` 是 `choices`，擴充只是改 code + 一次 migration，很輕量。

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

每個會動餘額的地方，在同一個 `atomic` 內補寫紀錄。下表「現有實作位置」是這個專案目前真正動餘額的函式：

| 操作 | 現有實作位置 | 寫什麼 |
|---|---|---|
| 下單凍結（`03`） | `OrderViewSet.transfer_to_frozen`（`transaction/views.py`，已在 `create` 的 atomic 內） | 兩筆 `FREEZE`：AVAILABLE（−total）、FROZEN（+total） |
| 撮合結算（`05`） | `WalletQuerySet.transfer_asset`（`member/models.py`，在 `match_order` 的 atomic 內） | 四筆 `SETTLE`：買方 base AVAILABLE(+)、quote FROZEN(−)；賣方 base FROZEN(−)、quote AVAILABLE(+)。收/付靠 `balance_field`+正負區分（見 §3.2） |
| 取消／多凍退款（`04`） | `WalletQuerySet.release_frozen`（`member/models.py`） | 兩筆：FROZEN（−）、AVAILABLE（+）。**reason 依 `order.status` 決定**：CANCELED→`UNFREEZE`、FULLY_FILLED→`REFUND` |
| 出金（`06`） | `WalletViewSet.withdraw`（`member/views.py`，已在 atomic 內） | `DepositWithdrawModel` + `LedgerEntryModel`：WITHDRAW（AVAILABLE −） |
| 入金 | 目前是 admin 手動加數字（無函式） | 啟用 log 後若要納入對帳，需走一個會寫 `DepositWithdrawModel` + `LedgerEntryModel`：DEPOSIT（AVAILABLE +）的入金路徑 |
| 交易手續費（進階） | 尚未實作 | `LedgerEntryModel`：TRADING_FEE（交易所抽成，非 gas；見下方 §5.1） |

> 取消與退款共用同一個 `release_frozen`，所以「這次是 UNFREEZE 還是 REFUND」要靠 `order.status` 區分
> （進到此函式時訂單已是終態：CANCELED 或 FULLY_FILLED）。這也是測試 `LedgerOnCancelTest` 與
> `LedgerOnOverFreezeRefundTest` 分別釘住的行為。

### 5.1 `TRADING_FEE`（交易手續費）vs gas fee — 別搞混

`TRADING_FEE` 指**交易所的抽成**（commission）：每筆成交按成交額抽一個百分比（常分 maker/taker 不同費率），
在**結算時**從用戶該收/該付的那側扣掉、進交易所的收入錢包。它純粹是內部帳本的數字搬移，**與區塊鏈無關**。
本專案目前結算不抽成（`transfer_asset` 是 1:1 搬），所以 `TRADING_FEE` 現在只是預留值、本里程碑不實作；
日後要做時，在結算同一個 atomic 內多寫一筆 `TRADING_FEE` 的 `LedgerEntryModel`（用戶 −、交易所收入錢包 +）。

**gas fee 是另一回事，且不在現在的範圍**：gas 是付給區塊鏈網路的費用，只在「碰到鏈」時才出現。

- **DEX 階段**：資產不託管，gas 由**用戶自己的鏈上錢包**直接付給網路，**不經過我們的內部帳本**——
  所以 DEX 一般**不需要**為 gas 新增 `reason`；後端角色是索引鏈上事件，不是搬內部餘額。
- **唯一可能需要記 gas 的中間情況**是範圍 2（CEX 接測試鏈出金）：交易所代為廣播出金、自己墊付網路費，
  可能再轉嫁給用戶當「提幣手續費」。屆時才考慮加一個獨立的 `reason`（例如 `WITHDRAW_FEE` / `NETWORK_FEE`），
  與 `TRADING_FEE` 分開。

**原則**：`LedgerEntryModel` 只記「真的有動到內部餘額」的事。離開用戶自己鏈上錢包的 gas 永遠不碰我們的帳本。
`reason` 是 `choices`，之後要加新值只是改 code + 一次 migration，很輕量——所以**現在不用預先把 gas 加進去**，
等對應功能真的出現再加。

## 6. 實作建議

- **顯式寫，不要用 signal**：在各業務函式（結算、取消、出金…）內**明確地**寫 `LedgerEntryModel`，放進同一個 `atomic`。不要用 Django signal 自動寫——signal 難追蹤、也難保證和餘額變動在同一交易裡。
- **建議順序**：先做 `LedgerEntryModel`（它能對帳整個系統、價值最高），再做 `DepositWithdrawModel`。
- **回填**：上線前的測試資料沒有 log 是正常的；正式啟用 log 後，餘額對帳的基準從那刻起算。

### 6.1 `F()` 更新拿不到 `balance_after` 的坑（這專案會遇到）

`transfer_asset` 與 `release_frozen` 目前用 `.update(balance=F('balance') + x)` 做 DB 端相對運算（M6 為了併發安全特意改的，別改回讀-改-寫）。
但 `F()` 更新**不會把新值回填到 Python 物件**，所以你拿不到 `balance_after`。兩個解法：

1. `.update(...)` 之後對該錢包 `refresh_from_db()`（或重新 `get`），讀回新值再寫 `LedgerEntryModel`。簡單、推薦。
2. 在同一個 atomic 內，更新前已 `select_for_update` 鎖住該列時，可自行算 `舊值 ± delta` 當 `balance_after`。

無論哪種，**寫 `LedgerEntryModel` 必須和那筆 `.update()` 在同一個 `atomic`**（它們本來就在 `match_order` / `withdraw` 的 atomic 內，照著放即可）。

### 6.2 app 建立步驟（給使用者的實作 checklist）

骨架已建好（`ledger/`），但 model 與業務函式的補寫由你完成：

1. 在 `ledger/models.py` 寫 `LedgerEntryModel`（欄位見 §3、契約見 §3.1 與下方鐵則）。append-only 建議在 model 層強制：覆寫 `save()` 擋「更新既有列」、覆寫 `delete()` 直接 raise。
2. `exchange/settings.py` 的 `INSTALLED_APPS` 加入 `"ledger"`。
3. `python manage.py makemigrations ledger && migrate`。
4. 在 §5 表列的四個函式內補寫 `LedgerEntryModel`（同 atomic）。
5. 跑 `ledger/test/test_ledger.py`，逐條變綠。
6. （之後）再做 `DepositWithdrawModel`。

## 7. 常見坑

- **log 與餘額不同步**：沒包在同一個 `atomic` → 帳本和錢包對不上。這是最常見也最致命的問題。
- **改舊紀錄**：append-only 被破壞，稽核就失去意義。要修正用「反向分錄」，不要改舊的。
- **漏掉 `balance_after`**：只記 delta、不記變動後餘額，重建時雖能算，但少了交叉驗證的能力。建議兩個都存。
- **精度**：一律 `Decimal`，與錢包欄位同精度。
