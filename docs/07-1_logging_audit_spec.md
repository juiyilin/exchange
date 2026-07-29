# 細部規格 — 日誌與帳本（logging / audit）〔範圍一〕

> 上層文件：`00_overall_spec.md`　範圍二續篇：`07-2_logging_audit_spec.md`（狀態機、鏈上冪等、鏈上對帳）
> 相關：`02-1_member_wallet_spec.md`、`05-1_settlement_spec.md`、`06-1_deposit_withdraw_spec.md`

## 一、需求方規格

### 1. 稽核目標

錢的系統必須可稽核:任何一次餘額變動都要能回答五件事——**誰、何時、因為什麼、變動多少、變動前後各是多少**。系統必須能追根、能對帳、能重建。只保留「當前餘額數字」而不留變動歷程是不夠的,因此每一筆餘額變動都要落成一筆紀錄。

### 2. 三類紀錄

| 紀錄 | 是什麼 |
|---|---|
| **A. 帳本流水** | 每一次可動用餘額 / 凍結餘額的變動,都要寫一筆不可竄改、不可刪除的紀錄。這是所有餘額變動的單一真相來源。 |
| **B. 入金 / 出金紀錄** | 一次「錢進出交易所大門」的業務紀錄。 |
| **C. 訂單 / 成交事件** | 下單意圖與成交本身的事件紀錄(已具備)。 |

要補齊的是 A（帳本流水）與 B（入出金紀錄）;A 是地基。

### 3. 帳本流水的需求

每一次餘額變動都要留下一筆紀錄,內容包含:

- **誰**:發生變動的用戶。
- **何時**:變動發生的時間。
- **哪種幣**:變動的幣別。
- **變動原因**:業務上為什麼發生這次變動——下單凍結、取消解凍、撮合成交結算、多凍結退款、入金、出金、交易手續費等。原因是「業務上為什麼」這個無法從金額推導出來的資訊,必須獨立記錄。
- **變動多少**:帶正負號的變動量(增為正、減為負)。
- **變動後餘額**:該次變動後的餘額快照,方便稽核與重建。

紀錄的顆粒度原則:**一個操作若同時牽動多個餘額項目,每動一個項目就寫一筆**。例如下單凍結同時使可動用餘額減少、凍結餘額增加,就寫兩筆;一次成交牽動買賣雙方共四個餘額項目,就寫四筆。

原因的分類原則——判準是「這是同一個業務事件的多條腿,還是不同的業務事件」:

- **同一事件的多條腿 → 同一個原因**,靠「動到哪個餘額項目」與「正負號」區分各腿。例如凍結、解凍、退款、結算,都是「一個原因、兩條腿」。
- **不同的業務事件 → 各自一個原因**。例如入金與出金雖然都只動可動用餘額、只差正負,但它們是各自獨立發生的事件、報表上分別統計,故不合併。

**關鍵反例**:凍結餘額減少且變動量為負,可能是「付給對手成交」也可能是「退回自己」——同一項目、同一方向、意義相反,只能靠變動原因區分。因此變動原因不可被「變動量 + 餘額項目」取代。

### 4. 入金 / 出金紀錄的需求

每一次入金或出金,都要留下一筆業務紀錄,內容包含:誰、哪種幣、金額、方向(入金 / 出金)、處理狀態(處理中 / 完成 / 失敗)、時間。範圍一為模擬內部帳本,狀態直接為「完成」,鏈上相關資訊(交易雜湊、對方地址)暫時留空。

入金屬內部 / 管理操作(真實交易所由鏈上偵測觸發,此處以管理者操作模擬):由管理者替某位指定用戶記入一筆幣。出金則是用戶對自己錢包發起。金額必須為正,否則拒絕。

入出金紀錄是「這件事」的業務列,帳本流水是它造成的餘額變動分錄,後者指回前者。

### 5. 對帳不變量

所有紀錄合在一起要能對帳:**任一錢包的當前餘額,等於該錢包所有對應變動量的總和**。對帳工作就是定期檢查這條等式是否成立。具備完整入金到出金的紀錄鏈(入金 → 凍結 → 結算 → 退款 → 出金)後,每個錢包的餘額都能往回追到一連串有紀錄的變動,不再有「手動改數字、帳本卻沒這筆」的破口。

## 二、開發方規格

本層承接需求方規格,補充技術約束。相關 model:帳本流水 `LedgerEntryModel`、入出金紀錄 `DepositWithdrawModel`,兩者收在獨立的 `ledger` app;訂單 / 成交事件由既有的 `OrderModel` / `TransactionModel`(`transaction` app)承擔。

### 1. 帳本流水 model（`LedgerEntryModel`）

放在 `ledger` app,欄位:

| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | FK → User | 誰 |
| `asset_type` | FK → CurrencyModel | 哪種幣。只 FK 到 User + CurrencyModel,不要 FK 到 `WalletModel`(見 §3) |
| `reason` | choices | 變動原因:`FREEZE`(下單凍結)、`UNFREEZE`(取消解凍)、`SETTLE`(撮合成交結算)、`REFUND`(多凍結退款)、`DEPOSIT`(入金)、`WITHDRAW`(出金)、`TRADING_FEE`(交易手續費) |
| `balance_field` | choices | 動到哪個欄位:`AVAILABLE` / `FROZEN` |
| `delta` | Decimal(20,2) | 變動量,帶正負號(+ 增、− 減) |
| `balance_after` | Decimal(20,2) | 該欄位變動後的值(當下快照) |
| `ref_type` | CharField | 來源事件類型:`order` / `transaction` / `deposit_withdraw` / `manual`。軟參照(純字串),不要 FK(見 §3) |
| `ref_id` | CharField | 來源事件的 id;存 `str(pk)` 即可,不要 FK |
| `created_at` | DateTime(auto_now_add) | 何時 |

顆粒度規則:**每動一個 `balance_field` 就寫一筆**。下單凍結同時動 `AVAILABLE`(−) 與 `FROZEN`(+),寫兩筆 `FREEZE`;一筆成交動到買賣雙方四個餘額欄位,寫四筆 `SETTLE`。

`reason` 顆粒度依「同一業務事件的多條腿 vs 不同業務事件」判定:`FREEZE`、`UNFREEZE`、`REFUND`、`SETTLE`(收 = `AVAILABLE` + / 付 = `FROZEN` −)各為「一個 reason、兩條腿」;`DEPOSIT` 與 `WITHDRAW` 為獨立事件,各有一筆 `DepositWithdrawModel`,不合併。`ref_type="manual"` 只表示「找不到上游業務 FK」;要區分手動動帳的種類(如後台調整 / 補償 / 修正錯帳),把在意的情境升格成獨立 `reason`,並可搭配 `memo`(緣由) 與 `operator`(FK→User) 兩欄以利問責,修正錯帳的反向分錄用 `ref` 指回被修正的原 entry。`reason` 是 `choices`,擴充只需改 code + 一次 migration,等對應功能真的出現再加即可。

### 2. append-only 技術約束

`LedgerEntryModel` 為 append-only:只新增,絕不 update、絕不 delete。寫錯就再寫一筆反向修正分錄。建議在 model 層強制:覆寫 `save()` 擋「更新既有列」、覆寫 `delete()` 直接 raise。

- **與餘額變動同一個 `atomic`**:每次改 `WalletModel` 的 `available` / `frozen`,就在同一個交易內寫對應的 `LedgerEntryModel`。不能餘額改了、log 沒寫(或反之),否則帳本與錢包對不上。
- **可對帳的不變量**:任一錢包當前 `available`(或 `frozen`)== 該錢包所有對應 `balance_field` 的 `delta` 總和。
- **精度**:一律 `Decimal`,與錢包欄位同精度。

### 3. 獨立 app 與依賴方向

`ledger` 被 `member`、`transaction` 依賴(它們在業務函式內 import `LedgerEntryModel` 寫紀錄)。為避免循環依賴,`ledger` 只能向下依賴 `currency` / `common`,不得反向 import 任何上層 model。兩條鐵律:

1. `LedgerEntryModel.asset_type` FK 到 `CurrencyModel`,不 FK 到 `WalletModel`。否則形成 `ledger → member` 且 `member → ledger` 的循環。
2. `ref_type` / `ref_id` 用軟參照(字串),不做成指向 `OrderModel` / `TransactionModel` 的真 FK。否則形成 `ledger → transaction` 且 `transaction → ledger` 的循環。

守住後依賴層次為單向鏈:`common ← currency ← ledger ← member ← transaction`。

### 4. 入金 / 出金紀錄 model（`DepositWithdrawModel`）

與 `06` 對齊,此處為正式欄位定義:

| 欄位 | 說明 |
|---|---|
| `user` | 誰 |
| `asset_type` | 哪種幣 |
| `amount` | 金額 |
| `direction` | `DEPOSIT` / `WITHDRAW` |
| `status` | `PENDING`(處理中) / `DONE`(完成) / `FAILED`(失敗) |
| `tx_hash` | 鏈上交易雜湊(範圍一留空,範圍二才填) |
| `address` | 對方地址(範圍一留空,範圍二才填) |
| `created_at` / `updated_at` | 時間 |

範圍一(模擬):`status` 直接 `DONE`,`tx_hash` / `address` 留空。欄位長度:`tx_hash` 100(以太 66 = `0x` + 64 hex;比特幣 64)、`address` 100(以太 42;比特幣 bech32 上限 90);`varchar(n)` 長度只是約束,不影響儲存空間。

**`DepositWithdrawModel` 不是 append-only**:它有 `status` 會轉移(`PENDING → DONE/FAILED`),是會被更新的業務列,不要像 `LedgerEntryModel` 那樣覆寫 `save()` / `delete()` 去擋更新。`LedgerEntryModel` 用 `ref_type="deposit_withdraw"` + `ref_id=str(該列.id)` 指回對應的 `DepositWithdrawModel`。範圍二(狀態機真的轉移、`tx_hash` 唯一性與冪等、出金失敗的反向分錄)見 `07-2_logging_audit_spec.md`。

### 5. 各業務函式套用點

每個會動餘額的地方,在同一個 `atomic` 內補寫紀錄:

| 操作 | 現有實作位置 | 寫什麼 |
|---|---|---|
| 下單凍結 | `OrderViewSet.transfer_to_frozen`(`transaction/views.py`,已在 `create` 的 atomic 內) | 兩筆 `FREEZE`:`AVAILABLE`(−total)、`FROZEN`(+total) |
| 撮合結算 | `WalletQuerySet.transfer_asset`(`member/models.py`,在 `match_order` 的 atomic 內) | 四筆 `SETTLE`:買方 base `AVAILABLE`(+)、quote `FROZEN`(−);賣方 base `FROZEN`(−)、quote `AVAILABLE`(+)。收 / 付靠 `balance_field` + 正負區分 |
| 取消 / 多凍退款 | `WalletQuerySet.release_frozen`(`member/models.py`) | 兩筆:`FROZEN`(−)、`AVAILABLE`(+)。reason 依 `order.status` 決定:CANCELED → `UNFREEZE`、FULLY_FILLED → `REFUND` |
| 出金 | `WalletViewSet.withdraw`(`member/views.py`,已在 atomic 內) | `DepositWithdrawModel`(direction=WITHDRAW, DONE) + `LedgerEntry`:`WITHDRAW`(`AVAILABLE` −),`ref_type="deposit_withdraw"` |
| 入金 | `WalletViewSet.deposit`(新增 admin-only action) | `DepositWithdrawModel`(direction=DEPOSIT, DONE) + `LedgerEntry`:`DEPOSIT`(`AVAILABLE` +),`ref_type="deposit_withdraw"` |
| 交易手續費(進階) | 尚未實作 | `LedgerEntry`:`TRADING_FEE`(見 §7) |

取消與退款共用同一個 `release_frozen`,「這次是 `UNFREEZE` 還是 `REFUND`」依 `order.status` 區分(進到此函式時訂單已是終態:CANCELED 或 FULLY_FILLED)。此行為由測試 `LedgerOnCancelTest` 與 `LedgerOnOverFreezeRefundTest` 分別釘住。

### 6. 入金 / 出金端點與記帳 wiring（範圍一）

兩端都在 `WalletViewSet`(`member/views.py`),各自全程 `@transaction.atomic`,在同一個 atomic 內完成「動餘額 + 建 DW 列 + 寫 LedgerEntry」三件事。

**出金** `POST /api/user/wallet/withdraw/`:行為照 `06` §2(鎖錢包、檢查、扣 `available`);通過後建 `DepositWithdrawModel(user=request.user, asset_type, amount=quantity, direction=WITHDRAW, status=DONE)`,再寫 `LedgerEntry(reason=WITHDRAW, balance_field=AVAILABLE, delta=-quantity, balance_after, ref_type="deposit_withdraw", ref_id=str(dw.id))`。

**入金(admin-only)** `POST /api/user/wallet/deposit/`:做成 `WalletViewSet` 的 `@action(detail=False, methods=['post'])`,`permission_classes=[IsAdminUser]`。body:`{"user_id": <目標用戶>, "asset_type_id": <幣別>, "quantity": "<金額>"}`——入金對象取 body 的 `user_id`,不是 `request.user`。驗證:`quantity <= 0` → 400;非 admin → 403。行為(atomic):`get_or_create` 該用戶 + 幣別錢包;`available += quantity`;建 `DepositWithdrawModel(direction=DEPOSIT, status=DONE)`;寫 `LedgerEntry(reason=DEPOSIT, balance_field=AVAILABLE, delta=+quantity, balance_after, ref_type="deposit_withdraw", ref_id=str(dw.id))`;回 200/201。

### 7. `TRADING_FEE` 與 gas fee 的界線

`TRADING_FEE` 指交易所的抽成(commission):每筆成交按成交額抽一個百分比(可分 maker/taker 費率),於結算時從用戶該收 / 該付的那側扣掉、進交易所收入錢包。它純粹是內部帳本的數字搬移,與區塊鏈無關。本專案目前結算不抽成(`transfer_asset` 為 1:1 搬),`TRADING_FEE` 為預留值,本階段不實作;日後在結算同一個 atomic 內多寫一筆 `TRADING_FEE` 的 `LedgerEntry`(用戶 −、交易所收入錢包 +)即可。

gas fee 是付給區塊鏈網路的費用,不在現在範圍。DEX 階段資產不託管,gas 由用戶自己的鏈上錢包直接付給網路,不經過內部帳本,一般不需為 gas 新增 `reason`。唯一可能需要記 gas 的情況是範圍二(CEX 接測試鏈出金,交易所代為廣播並墊付網路費),屆時再考慮加獨立 `reason`(如 `WITHDRAW_FEE` / `NETWORK_FEE`),與 `TRADING_FEE` 分開。原則:`LedgerEntry` 只記真的有動到內部餘額的事。

### 8. 技術約束 / 注意事項

- **顯式寫,不用 signal**:在各業務函式(結算、取消、出金…)內明確寫 `LedgerEntryModel`,放進同一個 `atomic`。不使用 Django signal 自動寫,以確保與餘額變動落在同一交易。
- **log 與餘額必須同步**:未包在同一個 `atomic` 內,帳本與錢包會對不上。
- **不改舊紀錄**:append-only 一旦被破壞,稽核即失去意義;修正一律用反向分錄。
- **`balance_after` 必存**:只記 `delta` 而不記變動後餘額,雖能重建但缺少交叉驗證能力。
- **精度**:一律 `Decimal`,與錢包欄位同精度。

#### 8.1 `F()` 更新取 `balance_after`

`transfer_asset` 與 `release_frozen` 以 `.update(balance=F('balance') + x)` 做 DB 端相對運算(為併發安全,勿改回讀-改-寫)。`F()` 更新不會把新值回填到 Python 物件,因此取不到 `balance_after`。兩種取法:

1. `.update(...)` 後對該錢包 `refresh_from_db()`(或重新 `get`),讀回新值再寫 `LedgerEntryModel`(推薦)。
2. 在同一 atomic 內,更新前已 `select_for_update` 鎖住該列時,自行算 `舊值 ± delta` 當 `balance_after`。

無論哪種,寫 `LedgerEntryModel` 必須與那筆 `.update()` 在同一個 `atomic`(本就在 `match_order` / `withdraw` 的 atomic 內)。

#### 8.2 app 建立步驟

1. 在 `ledger/models.py` 寫 `LedgerEntryModel`(欄位見 §1、契約見 §2 與 §3)。append-only 建議在 model 層以覆寫 `save()` / `delete()` 強制。
2. `exchange/settings.py` 的 `INSTALLED_APPS` 加入 `"ledger"`。
3. `python manage.py makemigrations ledger && migrate`。
4. 在 §5 表列的函式內補寫 `LedgerEntryModel`(同 atomic)。
5. 再做 `DepositWithdrawModel`。

建議先做 `LedgerEntryModel`(能對帳整個系統、價值最高),再做 `DepositWithdrawModel`。正式啟用 log 後,餘額對帳的基準從那刻起算。
