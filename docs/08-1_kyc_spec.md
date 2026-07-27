# 細部規格 — 身份驗證（KYC）〔範圍一〕

> 對應 app：`member`（KYC 欄位掛在 `UserProfileModel`）　相關 model：`UserProfileModel`
> 上層文件：`00_overall_spec.md`　相鄰規格：`02-1_member_wallet_spec.md`（用戶/錢包）、`06-1_deposit_withdraw_spec.md`（出金）、`07-1_logging_audit_spec.md`（帳本）
>
> 本檔只談**範圍一（純內部帳本，不碰鏈）**。KYC 是 CEX（託管型）才需要的合規層；
> 到了 DEX 階段身份改以**鏈上錢包地址**代表（見 `CLAUDE.md`「最終目標」），屆時另立規格，本檔不重寫。
>
> **切分（本輪定案，見 TASKS.md「M-KYC / KYC-A」）**：
> - **第一步（本檔 §3–§7，可立即實作）**：KYC 欄位 + 狀態機 + admin 審核端點 + 出金閘門。
>   證件文件先用**文字欄位／佔位**帶過，不碰物件儲存。
> - **第二步（本檔 §8，設計預留、本輪不實作）**：MinIO 物件儲存 + 證件照上傳（正反面 + 自拍）。
>
> **狀態**：第一步——規格與測試（`member/tests/test_kyc.py`）由 Claude 撰寫完成，實作由使用者完成。

---

## 1. 這個模組負責什麼

KYC（Know Your Customer，認識你的客戶）＝**在錢要離開系統之前，能對監管機關交代「這個人是誰」**。

技術上它就三塊拼圖：

1. **一組身份欄位**（法定姓名、證件號碼、生日、國籍）——用戶申報、系統保存。
2. **一台狀態機**（`unverified → pending → approved / rejected`）——記錄「這個人驗到哪個階段」。
3. **一道閘門**——在敏感操作前檢查狀態，未通過就擋。KYC-A 只在**出金**設閘門。

第一步刻意不做「上傳證件照」——那需要一整套新的物件儲存基礎設施（MinIO），與「狀態流程」是正交的兩件事，切開學比較不會亂（見 §8）。

## 2. 背景與心法（為什麼要做、哪裡要擋）

### 2.1 KYC 是法規逼的，不是產品選擇

KYC 隸屬 **AML/CFT**（反洗錢／打擊資恐）框架，源頭是 FATF（防制洗錢金融行動工作組織）的建議，各國立法落地（台灣：《洗錢防制法》，VASP 虛擬資產服務業者需完成洗錢防制法令遵循聲明）。設計邏輯不是「對用戶方便」，而是**「錢要離開系統時，能不能對監管機關交代這個人是誰」**。

### 2.2 心法：看「錢的流向」，不是看「操作的重要性」

判斷「哪裡該擋 KYC」，看的是錢往哪流，不是操作看起來多重要：

| 操作 | 擋不擋 | 為什麼 |
|---|---|---|
| **出金** | **一定擋** | 錢**離開**系統，是洗錢的最後一哩路，法規盯最緊。 |
| 入金（幣圈） | 通常不擋 | 錢**進來**沒有洗錢風險。（但**法幣入金**要串銀行，一定擋。） |
| 下單 | 早期不擋、2021 後主流所多半擋 | 監管收緊後「入金→交易→提幣」整條都算管道。本專案留到 **KYC-B**。 |

> **為什麼 KYC-A 只擋出金**：法規底線、非做不可，且 `withdraw` 函式已經在（`02-1` §4 / `06-1`），加閘門只要幾行。
> 下單閘門技術上不難，但會牽動現有 25+ 條交易測試（每個測試用戶都得先過 KYC），切開做比較不會亂（見 KYC-B）。
> 入金現在是 **admin-only 的假端點**（鏈上監聽器的替身，見 `02-1` §4 / `07-1` §4.1），擋它沒意義。

### 2.3 進階背景（KYC-B 以後才需要，先建立直覺）

真實世界的 KYC 是**分級**的，不是布林值——這叫 **RBA（風險基礎方法，Risk-Based Approach）**，FATF 核心原則：風險越高查越嚴。典型分層：Tier 0（僅 email）可入金看行情、不能提幣；Tier 1（身分證＋自拍）可交易、有每日提幣上限；Tier 2（地址證明＋財力來源）高額或無上限。另有 **EDD**（加強盡職審查，對政治人物 PEP／高風險國家／大額異常）與 **KYT**（持續監控交易行為）。**本專案 KYC-A 只做最粗的布林式門檻（approved 才能出金），分級額度留到 KYC-B。**

---

## 3. 資料模型（第一步）

### 3.1 KycStatus（新增 `member/constants.py`）

與 `transaction/constants.py`（`OrderStatus`）、`ledger/constants.py` 同風格，用 `TextChoices`：

```python
class KycStatus(models.TextChoices):
    """UserProfileModel.latest_kyc_status —— 用戶「當前」驗到哪。"""
    UNVERIFIED = "UNVERIFIED", "未驗證"
    VERIFYING  = "VERIFYING",  "審核中"
    APPROVED   = "APPROVED",   "已通過"
    REJECTED   = "REJECTED",   "已拒絕"


class KycEvent(models.TextChoices):
    """KycRecordModel.event_status —— append-only 歷史裡「發生了什麼事」（一次一列）。"""
    SUBMITTED         = "SUBMITTED",         "送審"
    APPROVED          = "APPROVED",          "通過"
    REJECTED          = "REJECTED",          "拒絕"
    REVOKED           = "REVOKED",           "撤銷"       # 風控/懲罰性作廢
    REVERIFY_REQUIRED = "REVERIFY_REQUIRED", "要求重驗"   # 例行重新驗證
```

> **`REVOKED` 與 `REVERIFY_REQUIRED` 為什麼分兩個事件**：它們的狀態轉移相同（都 `APPROVED → UNVERIFIED`），
> 但**意圖不同**——一個是懲罰性作廢（詐欺/盜用/制裁），一個是例行重驗（證件過期/定期覆審）。歷史層記的是
> 「發生了什麼事**以及為什麼**」，稽核時要能直接 filter「所有詐欺撤銷」，所以用 `event_status` 本身區分，
> 而不是塞進自由文字。（延伸：日後「撤銷」若要導向 `BLOCKED` 擋住重送，就會與「要求重驗」導向 `UNVERIFIED` 分道，
> 範圍一先都 → `UNVERIFIED`。）

> **為什麼獨立一個 constants.py**：`member` 目前沒有 constants.py（狀態字串散在各處）。KYC 引入第一組
> 明確的狀態機，趁機比照 `transaction`/`ledger` 收進 `member/constants.py`。狀態值是資料契約，
> serializer、view、閘門、admin、測試都會引用同一份，集中一處才不會出現「VERIFYING」打成「VERIFING」（就像這次差點發生的漏字）這種對不上的坑。
>
> **`KycStatus`（狀態）與 `KycEvent`（事件）是兩件事，別合併**：狀態是「現在停在哪個節點」（存在 profile，會被覆寫）；
> 事件是「發生過哪些轉移」（存在 record，永不改）。同一個 `APPROVED` 字面在兩邊語意不同：一個是「目前已通過」，
> 一個是「剛剛發生了一次通過」。分兩組 choices 才不會混。

### 3.2 UserProfileModel —— 當前狀態層（一人一份，會被覆寫）

KYC 的「**當前**」資料掛在既有的 `UserProfileModel`（與 `User` 已是 `OneToOne`，一人一份；且 2FA 密鑰已在此表，沿用同一處管理用戶敏感資料）。這層是**「現在的真相」**——出金閘門查的就是這張表，要快、一次查到。

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `latest_kyc_status` | `CharField(choices=KycStatus)` | `UNVERIFIED` | 當前狀態（出金閘門看這個）|
| `legal_name` | `CharField(100, blank, default="")` | `""` | 目前存檔的法定姓名 |
| `id_number` | `CharField(100, blank, default="")` | `""` | 目前存檔的證件號碼（**PII，見警告**）|
| `birth_date` | `DateField(null=True, blank=True)` | `NULL` | 生日 |
| `nationality` | `CharField(50, blank, default="")` | `""` | 國籍（ISO 國碼或名稱，範圍一不細究）|

> **注意**：`reviewed_by` / `reviewed_at` / `reason` / `submitted_at` **不放這裡**——它們是「某一次事件」的屬性，
> 屬於下面 §3.3 的歷史層。profile 只回答「現在」，不回答「歷史上第幾次、誰審的」。

> ⚠️ **`id_number` 是 PII，範圍一先明文存，是刻意的技術債**：正解與 2FA 密鑰同款——用 `Fernet` 加密後存
> （`02-1` §3 的 `encrypted_totp_secret` 是範本），或至少查詢時遮罩。證件照（第二步）敏感度更高，那時 bucket
> 必須私有、走預簽名 URL（§8）。本階段先把流程跑通，把加密列為 §7 待辦。

### 3.3 KycRecordModel —— 歷史層（一人多筆，append-only）

新增一張 model（放 `member` app），**每發生一次 KYC 事件就寫一列，永不改、永不刪**。這是稽核軌跡與「重新 KYC」的歷史來源。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | `FK → User (related_name="kyc_records")` | 這筆事件屬於誰 |
| `event_status` | `CharField(choices=KycEvent)` | SUBMITTED / APPROVED / REJECTED / REVOKED / REVERIFY_REQUIRED |
| `operator` | `FK → User (null=True, on_delete=SET_NULL)` | 誰觸發：送審＝本人；審核／撤銷＝staff |
| `legal_name` | `CharField(100, blank, default="")` | **當次送審的快照**（SUBMITTED 才填）|
| `id_number` | `CharField(100, blank, default="")` | 當次送審的快照（PII，同上警告）|
| `birth_date` | `DateField(null=True, blank=True)` | 當次送審的快照 |
| `nationality` | `CharField(50, blank, default="")` | 當次送審的快照 |
| `reason` | `TextField(blank=True, default="")` | 為何發生此事件（自由文字，admin 用 textarea）。`REJECTED`/`REVOKED`/`REVERIFY_REQUIRED` 才有；`SUBMITTED`/`APPROVED` 留空 |
| `created_at` | 繼承 `BaseTimeModel` | 事件發生時間（取代 profile 的 submitted_at/reviewed_at）|

**append-only 契約（照抄 `LedgerEntryModel` 的做法，見 `ledger/models.py` / `07-1`）**：

- `save()`：`if self.pk: raise ValueError(...)`（pk 已存在＝更新 → 擋），不允許改既有列。
- `delete()`：一律 `raise ValueError(...)`，不允許刪。
- 只能 `create`。approve 是**新增一筆 `APPROVED`**，不是去 update 那筆 `SUBMITTED`。

> **為什麼要快照送審欄位**：如果 record 只記「事件」不記「當時交了什麼」，那 profile 被下一次送審覆寫後，
> 「上一次到底交了什麼名字/證件」就永遠查不回來了——稽核就斷了。快照讓每一次申請的內容都留底。

### 3.4 兩層設計：current vs append-only（你已經做過一次）

這跟你在 M-日誌與帳本做的**一模一樣**，只是換成 KYC：

| | 當前層（會覆寫） | 歷史層（append-only） |
|---|---|---|
| 錢包／帳本 | `WalletModel.balance` | `LedgerEntryModel`（每筆異動）|
| **KYC** | `UserProfileModel.latest_kyc_status` + 當前身分欄位 | `KycRecordModel`（每次事件）|

心法一致：**當前層回答「現在如何」（查得快），歷史層回答「怎麼走到現在的」（查得全）**。每次狀態轉移，都在**同一個 `transaction.atomic()`** 裡「更新 profile 當前狀態 **＋** 寫一筆 record」——兩者要嘛一起成功、要嘛都不動（同 `07-1` 的套用點原則）。

### 3.5 Migration

`makemigrations member && migrate`。profile 新欄位都有預設值（`UNVERIFIED` / `""` / `NULL`），既有用戶不需搬遷；`KycRecordModel` 是新表，直接建。安全。

---

## 4. 狀態機（第一步的核心）

```
          用戶送審                    staff 通過
UNVERIFIED ───────▶ VERIFYING ─────────────────────▶ APPROVED
    ▲   ▲              │                                │
    │   │             │ staff 拒絕      staff 撤銷／要求重驗 │
    │   │  用戶修正後重送 ▼                                │
    │   └────────── REJECTED                            │
    └───────────────────────────────────────────────────┘
```

**允許的轉移，與「誰能觸發」（每次轉移都在同一 atomic 內寫一筆 `KycRecordModel`）：**

| 從 | 到 | 由誰 | 觸發 | 寫入的 record 事件 |
|---|---|---|---|---|
| `UNVERIFIED` / `REJECTED` | `VERIFYING` | **用戶本人** | 送審（填 KYC 欄位）| `SUBMITTED`（含欄位快照）|
| `VERIFYING` | `APPROVED` | **staff** | approve 端點 | `APPROVED` |
| `VERIFYING` | `REJECTED` | **staff** | reject 端點 | `REJECTED`（含 reason）|
| `APPROVED` | `UNVERIFIED` | **staff／系統** | **revoke** 端點（懲罰性作廢）| `REVOKED`（含 reason）|
| `APPROVED` | `UNVERIFIED` | **staff／系統** | **reverify** 端點（例行重驗）| `REVERIFY_REQUIRED`（含 reason）|

> 最後兩列**狀態轉移相同、事件不同**：這正是把它們拆成兩個端點/兩個事件的原因（見 §3.1、§4.1）。
> `reject`/`revoke`/`reverify` 三者的 `reason` **都必填**（稽核要交代「為什麼」）；必填規則在端點/serializer 驗，DB 欄位維持可空（見 §5）。

**不允許（要在後端擋，回 400）：**

- 從 `VERIFYING` 再送審（已在審核中，重複送沒意義）。
- 從 `APPROVED` 直接再送審或再被 approve/reject（要先 `revoke`／`reverify` 打回 `UNVERIFIED`，才能重跑流程）。
- 對**非 `VERIFYING`** 的人 approve/reject（沒有在等審的東西可審）。
- 對**非 `APPROVED`** 的人 revoke／reverify（沒有通過過，談不上撤銷或重驗）。

> **心智模型**：狀態機的每個「轉移」都要問兩件事——**現在的狀態允不允許這個轉移**（狀態守衛）、
> **這個人有沒有資格觸發這個轉移**（權限）。兩者都過，才改狀態並寫 record。這和 M4 訂單狀態機
> 「終態不可再變」（`03-1`/`05-1`）是同一套思路。

### 4.1 重新 KYC 的兩種情況（本輪加入）

**情況一：被拒後重送**（`REJECTED → VERIFYING`）。用戶補件重送，走的是同一個「送審」動作——因為是一人一份，
profile 的當前身分欄位被新值覆寫、狀態回 `VERIFYING`；同時 `KycRecordModel` 多一筆 `SUBMITTED` 快照。
被拒不能是死路（打錯一個字不該永遠出不了金），所以「送審」對 `UNVERIFIED` 與 `REJECTED` 一視同仁。

**情況二：已通過後要打回**（re-KYC）。真實所會因兩種原因讓**已通過**的人回到未驗證，`APPROVED` 因此**不是永久終態**；
兩者都**只能由 staff／系統觸發，用戶不能自己發起**（否則用戶可自我降級亂搞），也都 `APPROVED → UNVERIFIED`
（出金閘門自動關上，因為只有 `APPROVED` 放行）→ 用戶重新送審 → 回到正常流程。差別只在**記哪個事件**：

- **`revoke` 端點**（懲罰性作廢：詐欺/盜用/制裁）→ 寫 `REVOKED`，**reason 必填**。
- **`reverify` 端點**（例行重驗：證件過期/定期覆審）→ 寫 `REVERIFY_REQUIRED`，**reason 必填**。

> **備忘（之後評估，非本輪）——自動定期覆審**：以上 `reverify` 是**手動**觸發（staff 按）。
> 若要做**時間到自動觸發**的定期覆審，需要 Celery beat 排程掃描 + 一條覆審週期政策，屬 KYC-B 範圍。
> 屆時「下次覆審時間」多半**不必存欄位**——若週期固定，直接由「最近一筆 `APPROVED` record 的 `created_at` + 週期」推算即可。
> 只有當週期**因風險分級而異**（EDD/RBA）才值得在 profile 存 `kyc_next_review_at`，配 beat 掃描過期 → 打回 `UNVERIFIED`
> 並寫一筆 record（複用 `REVERIFY_REQUIRED` 或另立 `EXPIRED`）。詳見 TASKS.md「KYC-B」。

> **為什麼是 `APPROVED → UNVERIFIED` 而不是 `→ VERIFYING`**：撤銷的當下用戶**還沒交新資料**，
> 沒有東西可審，所以不能直接跳 `VERIFYING`（那代表「等審中」）。打回 `UNVERIFIED` 語意才對：
> 「你現在不算已驗證，請重新送審」。若想區分「從未驗證」與「曾通過但過期」，可另加 `EXPIRED` 狀態——
> 但範圍一為求簡單先複用 `UNVERIFIED`，反正完整歷史都在 `KycRecordModel` 裡查得到。

---

## 5. API 端點（第一步）

新增一個 **`KycViewSet`**（建議 `GenericViewSet`），註冊在 `member/urls.py` 的 router：`router.register(r"kyc", KycViewSet)` → 掛在 `/api/user/kyc/...`。權限**分兩層**（呼應 `02-1` §6.5：角色層 vs 擁有權層）：

| 端點 | 方法 | 說明 | 權限 | 狀態轉移 |
|---|---|---|---|---|
| `/api/user/kyc/` | POST | 送審：body `{legal_name, id_number, birth_date, nationality}`，綁 `request.user` | IsAuthenticated | `UNVERIFIED`/`REJECTED` → `VERIFYING` |
| `/api/user/kyc/me/` | GET | 查**自己**的 KYC 狀態（`@action(detail=False)`）| IsAuthenticated | — |
| `/api/user/kyc/{user_id}/approve/` | POST | 通過（`@action(detail=True)`）| **IsAdminUser** | `VERIFYING` → `APPROVED` |
| `/api/user/kyc/{user_id}/reject/` | POST | 拒絕：body `{reason}`（**必填**，`@action(detail=True)`）| **IsAdminUser** | `VERIFYING` → `REJECTED` |
| `/api/user/kyc/{user_id}/revoke/` | POST | 撤銷（懲罰性作廢）：body `{reason}`（**必填**）| **IsAdminUser** | `APPROVED` → `UNVERIFIED`（記 `REVOKED`）|
| `/api/user/kyc/{user_id}/reverify/` | POST | 要求重驗（例行覆審）：body `{reason}`（**必填**）| **IsAdminUser** | `APPROVED` → `UNVERIFIED`（記 `REVERIFY_REQUIRED`）|

**實作要點 / 決策：**

- **不塞進 `UserViewSet`**：`UserViewSet` 已鎖成 `IsAdminUser`（M-KYC 暖身），一般用戶碰不到，
  沒辦法用它送審或查自己。KYC 需要「用戶自己能用」＋「staff 能審」兩種語意，**獨立 ViewSet** 最乾淨
  （同 `02-1` §4 把註冊拆成獨立 `RegisterView` 的理由）。
- **`detail=True` 的 id 用 user id**：建議 `queryset = UserProfileModel.objects.all()` + `lookup_field = "user_id"`，
  URL 直接吃 `user_id`（admin 是拿著「哪個用戶」在審，不是「哪個 profile 列」）。
- **送審綁 `request.user`，不信任 body 的身分**：誰送審由 JWT 決定（`request.user.profile`），
  body 只帶 KYC 資料欄位。**絕不可讓 body 指定「幫誰送審」**——那等於任何人替別人送 KYC。
- **`me/` 端點順便補掉 `02-1` §4.3 的缺口**：一般用戶在 `UserViewSet` 鎖 admin 後本來查不到自己資料，
  這裡的 `me/` 至少讓他查得到自己的 KYC 狀態（完整的 `/me/` profile 端點仍可後續再補）。
- **狀態守衛放哪**：守衛（現在狀態允不允許轉移）屬於**業務規則**。範圍一端點單純，寫在 view/serializer 的
  `validate`/action 內即可；轉移非法 → 回 **400** 帶清楚訊息（如「目前狀態為 VERIFYING，無法重複送審」）。
- **每個轉移＝「改 profile 當前狀態 ＋ 寫一筆 record」**：送審寫 `SUBMITTED`（含欄位快照，operator=本人）；
  approve/reject/revoke/reverify 寫對應事件（operator=staff；reject/revoke/reverify 的 `reason` **必填**，approve 不收）。who/when 由 record 的 `operator` 與
  `created_at` 承載——**不要再往 profile 塞 reviewed_by/at**（§3.2 已把這些移到歷史層）。
- **`revoke` 與 `reverify` 共用同一段轉移邏輯**：兩個 action 的守衛（必須 `APPROVED`）、轉移（`→ UNVERIFIED`）、reason 必填完全一樣，
  只差寫入的 `event_status`。建議抽一個內部 helper 收下 `event_status` 與 `reason` 兩個參數，兩個 action 各自帶 `REVOKED` / `REVERIFY_REQUIRED` 呼叫它，
  避免複製兩份幾乎相同的程式碼（同 `03`/`04` 把共用邏輯抽成函式的精神）。
- **回應格式**：`me/` 與送審回 `{latest_kyc_status, legal_name?, nationality?, ...}`（**別回 id_number 全碼**，最少要遮罩）；
  approve/reject/revoke/reverify 回更新後的狀態。`me/` 可選擇附上該用戶的 record 歷史（一人多筆）。

> **這裡一定要包 `transaction.atomic`**：因為每個動作現在都是**兩筆寫入**——更新 profile 的當前狀態
> ＋ 新增一列 `KycRecordModel`。兩者必須同生同死：若狀態改了卻沒寫 record（或反過來），
> current 與 history 就對不上，稽核軌跡出現斷點。這與 `07-1` 帳本「改餘額 ＋ 寫分錄」包在同一 atomic 是同一條原則。
> （KYC 仍不寫 `ledger`——ledger 記的是「錢的移動」，KYC 沒動錢；`KycRecordModel` 是 KYC 自己的歷史表。）

> **備忘（之後評估，非本輪）——雙人覆核（四眼原則 / maker-checker / 職責分離）**：目前單一 staff 一步 `approve`
> 就從 `VERIFYING → APPROVED`。若要防單點內部舞弊，可改成「第一人**提出**核准/拒絕 → `PENDING_APPROVAL`（待覆核）→
> 第二個**不同的人確認** → `APPROVED`/`REJECTED`」，硬規則 `確認者 ≠ 提出者`。做法＝插一個中間狀態 + 一個確認端點 +
> 一條守衛（比對最近一筆提出 record 的 `operator`），不必重寫現有流程——**`KycRecordModel.operator` 已是它的稽核基礎**。
> 偏內控/RBAC 成熟度，歸 M-RBAC 或另立里程碑；另一個相關小控制：staff 不得審自己的 KYC。詳見 TASKS.md。

在 `WalletViewSet.withdraw`（`member/views.py`）**扣款之前**加一道檢查：**KYC 未 `APPROVED` → 擋下、回 403，餘額完全不變。**

**擺放位置**：要在 `select_for_update` 鎖錢包 / 改餘額**之前**——閘門是「有沒有資格出金」，資格不符就根本不該進到動錢那段。

**回哪個狀態碼**：建議 **403 Forbidden**（用 `rest_framework.exceptions.PermissionDenied`），不是 400。
理由：400 是「你送的資料有問題」，但這裡資料沒問題，是**你這個人還沒有資格**做這件事——那是權限/資格語意，403 才對。（現有 withdraw 的「餘額不足／無錢包」仍是 400，兩者語意不同，並存。）

**沒有 profile 的用戶怎麼辦**：用 `getattr(request.user, "profile", None)` 取（同 `LoginSerializer` 擋 superuser 無 profile 的手法，見 `02-1` §6.1）。取不到 profile，或 `profile.latest_kyc_status != APPROVED`，一律視為未通過 → 403。

概念上（**實作由你寫**）：

```
# withdraw 一進來、動錢之前：
profile = getattr(request.user, "profile", None)
if profile is None or profile.latest_kyc_status != KycStatus.APPROVED:
    raise PermissionDenied("KYC 未通過，無法出金")
# ...（原本的 select_for_update / 餘額檢查 / 扣款 / 寫 ledger 不變）
```

> **為什麼閘門放 view 而不放更底層**：出金的業務進入點目前就是這個 action（`06-1`）。閘門是「出金這個入口的准入條件」，
> 放在入口最直觀、也最好測。若日後出金抽成 service 函式，閘門就跟著搬到那個函式的開頭——
> 原則不變：**閘門貼在「錢要離開」的那道門上**。
> 入金（`deposit`）**不設** KYC 閘門（§2.2：錢進來沒有洗錢風險，且它本來就是 admin-only 假端點）。

---

## 7. 與現有系統的互動 / 坑

- **既有 `test_withdraw.py` 會被閘門打紅——這是預期的，且已一起處理**：現在 `WithdrawTest.setUp`
  建的 `trader` 沒有 profile，加閘門後每條出金測試都會變 403。**Claude 已同步更新 `test_withdraw.py`**：
  `setUp` 幫 `self.user` 建一份 `latest_kyc_status=APPROVED` 的 profile，讓「有資格者出金」的既有行為維持綠。
  （這正是 KYC-B「下單閘門會牽動 25+ 條交易測試」的小型預演——先在這一支感受一次。）
- **只有 `test_withdraw` 受影響**：`test_2fa` / `test_register_wallets` / `test_user_permissions` 不碰 withdraw，不受閘門影響。
- **註冊/登入/2FA 流程不動**：KYC 是**登入後**的獨立流程；新用戶註冊完 `latest_kyc_status` 預設 `UNVERIFIED`，能登入、能看行情，但不能出金。
- **重新 KYC 走 `KycRecordModel` 的歷史**（§3.3/§4.1）：被拒重送＝覆寫 profile 當前值 ＋ 多一筆 `SUBMITTED`；
  已通過後 staff `revoke`（撤銷）或 `reverify`（要求重驗）打回 `UNVERIFIED`（閘門自動關）再重送。
  **profile 只留當前、歷史全在 record**，所以覆寫當前值不會弄丟「上一次交了什麼」。
- **append-only 別手滑改成可更新**：`KycRecordModel` 的 `save()`/`delete()` 要擋（同 `LedgerEntryModel`）。
  approve 時是**新增一筆 `APPROVED` record**，不是去 update 那筆 `SUBMITTED`——這是它與「可變狀態表」最大的差異。
- **PII 加密是待辦**：`id_number` 明文存是刻意的過渡（§3.2）。注意 profile **與** record 兩邊都有 `id_number`，
  日後加密要一起處理。列入待辦：比照 `encrypted_totp_secret` 用 Fernet，或查詢遮罩。
- **`00_overall_spec.md`**：KYC 沒有新增 app（欄位掛在 member），依賴圖不變；若 §5 功能總表要標記「KYC-A 出金閘門完成」，可順手補一行（非必要）。

---

## 8. 第二步：MinIO 物件儲存 + 證件上傳（設計預留，本輪不實作）

> 本節是**下一輪**才做的設計預留，讓第一步的欄位/狀態機留好接點。**本輪不寫這段的測試與實作。**

- **新 model `KycDocumentModel`**（獨立成一對多：一位用戶多張文件）：`user` FK、`doc_type`（FRONT/BACK/SELFIE）、`object_key`（物件在 bucket 的鍵）、`uploaded_at`。**DB 只存 key，不存二進位。**
- **MinIO**：多一個 Docker 容器（同 `exchange-redis` 模式），S3 相容。Django 端 `django-storages` + `boto3`，設 S3 endpoint。
- **證件照是全系統最敏感的資料**：bucket **必須私有**、**絕不可走 public URL**；存取一律用**預簽名 URL（presigned URL）**（有時效的臨時連結）。
- **送審流程改版**：第一步的「填文字欄位送審」擴充為「填欄位 + 上傳正反面 + 自拍 → VERIFYING」。狀態機本身不變。
- **與範圍二的關係**：這仍屬**範圍一**（不碰鏈），所以留在 `08-1`，只是排在第一步之後；不要跟範圍二（測試鏈，`*-2` 檔）搞混。

---

## 9. 實作 checklist（第一步，給使用者；對應 TASKS.md）

- [ ] `member/constants.py`：新增 `KycStatus`（狀態）與 `KycEvent`（事件）兩組 TextChoices。
- [ ] `UserProfileModel`：加 §3.2 的**當前狀態層**欄位（`latest_kyc_status` + 身分欄位）；`makemigrations member && migrate`。
- [ ] `KycRecordModel`（member app，§3.3）：append-only（`save()` 擋更新、`delete()` raise，照抄 `LedgerEntryModel`）。
- [ ] `member/serializers.py`：`KycSubmitSerializer`（送審欄位、綁 request.user）、`KycStatusSerializer`（回狀態、遮罩 id_number）、reject/revoke/reverify 的 `{reason}`（三者皆必填）。
- [ ] `member/views.py`：`KycViewSet`——`create`(送審)、`me`(detail=False GET)、`approve`/`reject`/`revoke`/`reverify`(detail=True, IsAdminUser)。
      `revoke`/`reverify` 共用內部 helper，只差帶入的 `event_status`（`REVOKED` / `REVERIFY_REQUIRED`）。
      **每個動作在同一 `transaction.atomic()` 內：改 profile 當前狀態 ＋ 寫一筆 `KycRecordModel`**（§3.4）。狀態守衛非法轉移回 400。
- [ ] `member/urls.py`：`router.register(r"kyc", KycViewSet)`。
- [ ] `WalletViewSet.withdraw`：開頭加出金閘門（未 APPROVED → 403，動錢前）。
- [ ] （選做）`member/admin.py`：把 KYC 欄位加進 profile admin、註冊 `KycRecordModel`（唯讀）方便查歷史。
- [ ] 跑測試：`member/tests/test_kyc.py` 全綠、且 `test_withdraw` / `test_2fa` / `test_register_wallets` / `test_user_permissions` 不退步。

> 測試（`member/tests/test_kyc.py`）已由 Claude 寫好，是本步「正確行為」的定義——照著讓它變綠即可。
