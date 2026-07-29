# 細部規格 — 身份驗證（KYC）〔範圍一〕

> 上層文件：`00_overall_spec.md`　相鄰規格：`02-1_member_wallet_spec.md`（用戶/錢包）、`06-1_deposit_withdraw_spec.md`（出金）、`07-1_logging_audit_spec.md`（帳本）
>
> 本檔只涵蓋範圍一（純內部帳本，不碰鏈）。KYC 為託管型 CEX 的合規層；DEX 階段身分改以鏈上錢包地址代表，屆時另立規格，本檔不重寫。
>
> 交付切分：
> - **第一步（KYC-A，可實作）**：身分欄位 + 審核狀態機 + 審核端點 + 出金閘門；證件文件先以文字欄位／佔位帶過，不碰物件儲存。
> - **第二步（設計預留，本輪不實作）**：物件儲存 + 證件照上傳（正反面 + 自拍）。
> - **KYC-B（進階）**：下單閘門（B1）+ 分級額度（B2）。

---

## 一、需求方規格

### 1. 目的與合規背景

身分驗證（KYC，Know Your Customer）的目的是：**在資金離開系統之前，能對監管機關交代帳戶持有人是誰**。

這是法規義務，而非產品選項。它隸屬反洗錢／打擊資恐（AML/CFT）框架，源於防制洗錢金融行動工作組織（FATF）的建議，並由各國立法落地（台灣為《洗錢防制法》，虛擬資產服務業者須完成相關法令遵循聲明）。設計判準是「資金要離開系統時，能否交代此人身分」，而非用戶便利。

### 2. 申報的身分資料

用戶申報、系統保存下列身分資料，作為審核依據：

- 法定姓名
- 證件號碼
- 出生日期
- 國籍

證件號碼屬個人敏感資料，須妥善保護（保存與回傳時應加密或遮罩，見開發方規格）。第二步將加入證件照上傳（見第 8 節）。

### 3. 審核狀態流程

每個帳戶都有一個當前身分驗證狀態，隨審核進展轉移：

- **未驗證**：尚未申報或申報已被打回。
- **審核中**：用戶已申報身分資料，等待人工審核。
- **已通過**：審核通過，取得敏感操作（如出金）的資格。
- **已拒絕**：審核未通過。

流程：用戶申報後進入「審核中」；審核人員可將其判為「已通過」或「已拒絕」。

### 4. 重新驗證

身分驗證不是一次定終身，三種情況需要重新走流程：

- **被拒後重送**：被判「已拒絕」的用戶可補正資料重新申報，再次進入「審核中」。被拒不是死路（例如打錯一個字不應使帳戶永久無法出金）。
- **撤銷**（懲罰性作廢）：對已通過的帳戶，因詐欺、盜用或制裁等風控理由，由審核人員將其打回「未驗證」。
- **要求重驗**（例行覆審）：對已通過的帳戶，因證件過期或定期覆審，由審核人員要求其重新驗證，打回「未驗證」。

撤銷與要求重驗**只能由審核人員／系統發起，用戶不能自行觸發**（避免用戶自我降級亂搞）。兩者都須填寫理由以供稽核；被打回「未驗證」後，出金資格自動失效，用戶須重新申報。系統須保留每一次申報與審核事件的完整歷史，作為稽核軌跡，且覆寫當前資料時不得遺失先前申報內容。

### 5. 出金閘門

帳戶的身分驗證狀態**未達「已通過」時，拒絕出金**，且拒絕時帳戶餘額完全不變。入金不設此限制（資金進入系統無洗錢風險）。

### 6. 下單閘門〔KYC-B〕

在下單環節加入與出金相同的准入條件：帳戶身分驗證**未達「已通過」時，拒絕下單**，不建立訂單、不凍結資金。取消訂單不設此限制（取消是把凍結資金退回可用，資金留在系統內）。

早期交易所下單不設身分限制，但監管收緊後，「入金 → 交易 → 提幣」整條被視為洗錢管道，下單普遍納管，本專案跟進。

### 7. 分級額度〔KYC-B〕

**`kyc_tier` 是交易者的等級**，決定其額度；等級越高、額度越高。等級與「是否通過驗證」是**兩件正交的事**：是否通過看驗證狀態（未通過就是被拒 `REJECTED`，不是等級低）；通過之後能用多少額度，才看等級。等級由審核人員在通過時核給。

等級影響**交易額度與出金額度**兩者（本輪先實作出金的每日上限；交易額度是同一等級的延伸）。典型分層（額度數字為政策設定）：

- **等級 0**：最低級、額度最小（例如出金上限為 0，須升級才可出金）。核給等級 0 只是「給最低額度」，不代表驗證無效。
- **等級 1**：可出金，設每日上限。
- **等級 2**：上限更高或無上限。

每日出金上限以**法幣計價**：不同幣別的出金各自換算成同一種法幣後加總，再與該等級的上限比較。換算匯率在真實系統採「有防護的指數價」——不採單一市場的最近成交價，因其可被少量對敲操縱以撐大自己的可提額度；範圍一沒有行情來源，先以管理者維護的匯率當佔位、標明為指數價的替身，日後接行情再替換。

額度以**自然日**計（每日固定時刻歸零，看「今天」累計）。出金時，除須通過身分驗證外，還須「今日累計出金（法幣）＋ 本次（法幣）≤ 該等級上限」才放行，超限則拒絕、餘額不變。下單維持單一（是否通過）門檻，暫不套每日額度。

### 8. 證件上傳〔第二步，設計預留〕

第二步將把「填寫文字欄位送審」擴充為「填寫欄位 + 上傳證件正反面 + 自拍」，審核狀態流程本身不變。一位用戶可對應多張文件。證件照是全系統最敏感的資料，儲存與存取須嚴格保護（見開發方規格）。此步仍屬範圍一（不碰鏈）。

---

## 二、開發方規格

> 對應 app：`member`（KYC 當前狀態欄位掛在 `UserProfileModel`）。新增歷史表 `KycRecordModel`（`member` app）。狀態值集中於 `member/constants.py`。

### 1. 兩層資料模型

沿用錢包／帳本已建立的「當前層 + append-only 歷史層」拆分：

| | 當前層（會覆寫） | 歷史層（append-only） |
|---|---|---|
| 錢包／帳本 | `WalletModel.balance` | `LedgerEntryModel`（每筆異動）|
| KYC | `UserProfileModel.latest_kyc_status` + 當前身分欄位 | `KycRecordModel`（每次事件）|

當前層回答「現在如何」（出金／下單閘門查它，要快、一次查到）；歷史層回答「怎麼走到現在」（稽核軌跡與重新驗證來源）。

#### 1.1 狀態與事件 constants（`member/constants.py`）

比照 `transaction/constants.py`（`OrderStatus`）、`ledger/constants.py`，以 `TextChoices` 集中狀態值（資料契約，serializer／view／閘門／admin／測試共用一份）。狀態（現在停在哪個節點，存 profile、會覆寫）與事件（發生過哪次轉移，存 record、永不改）是兩組不同 choices，`APPROVED` 字面在兩邊語意不同，不可合併：

- 當前狀態 `KycStatus`：`UNVERIFIED` / `VERIFYING` / `APPROVED` / `REJECTED`。
- 歷史事件 `KycEvent`：`SUBMITTED` / `APPROVED` / `REJECTED` / `REVOKED` / `REVERIFY_REQUIRED`。

`REVOKED`（懲罰性作廢）與 `REVERIFY_REQUIRED`（例行重驗）狀態轉移相同（皆 `APPROVED → UNVERIFIED`），但意圖不同，以 `event_status` 本身區分（而非塞進自由文字），使稽核可直接依事件類型篩選。

#### 1.2 當前狀態層（`UserProfileModel`）

KYC 當前資料掛在既有的 `UserProfileModel`（與 `User` 為 `OneToOne`，一人一份；2FA 密鑰亦在此表）。欄位：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `latest_kyc_status` | `CharField(choices=KycStatus)` | `UNVERIFIED` | 當前狀態（閘門查此欄）|
| `legal_name` | `CharField(100, blank, default="")` | `""` | 目前存檔法定姓名 |
| `id_number` | `CharField(100, blank, default="")` | `""` | 目前存檔證件號碼（PII）|
| `birth_date` | `DateField(null=True, blank=True)` | `NULL` | 生日 |
| `nationality` | `CharField(50, blank, default="")` | `""` | 國籍（ISO 國碼或名稱，範圍一不細究）|

「某一次事件」的屬性（審核者、審核時間、理由、送審時間）**不放這層**，一律移到歷史層；profile 只回答「現在」。

#### 1.3 歷史層（`KycRecordModel`，append-only）

新增 model（`member` app），每發生一次 KYC 事件寫一列，永不改、永不刪：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | `FK → User (related_name="kyc_records")` | 事件屬於誰 |
| `event_status` | `CharField(choices=KycEvent)` | SUBMITTED / APPROVED / REJECTED / REVOKED / REVERIFY_REQUIRED |
| `operator` | `FK → User (null=True, on_delete=SET_NULL)` | 觸發者：送審＝本人；審核／撤銷／重驗＝staff |
| `legal_name` | `CharField(100, blank, default="")` | 當次送審快照（SUBMITTED 才填）|
| `id_number` | `CharField(100, blank, default="")` | 當次送審快照（PII）|
| `birth_date` | `DateField(null=True, blank=True)` | 當次送審快照 |
| `nationality` | `CharField(50, blank, default="")` | 當次送審快照 |
| `reason` | `TextField(blank=True, default="")` | 事件理由（自由文字）。`REJECTED`/`REVOKED`/`REVERIFY_REQUIRED` 有；`SUBMITTED`/`APPROVED` 留空 |
| `created_at` | 繼承 `BaseTimeModel` | 事件時間（取代 profile 的 submitted_at/reviewed_at）|

送審欄位須快照：profile 當前身分欄位會被下一次送審覆寫，若 record 不記當時申報內容，稽核就會斷。

#### 1.4 Migration

`makemigrations member && migrate`。profile 新欄位皆有預設值（`UNVERIFIED` / `""` / `NULL`），既有用戶不需搬遷；`KycRecordModel` 為新表，直接建。

### 2. 狀態機與允許轉移

```
          用戶送審                    staff 通過
UNVERIFIED ───────▶ VERIFYING ─────────────────────▶ APPROVED
    ▲   ▲              │                                │
    │   │             │ staff 拒絕      staff 撤銷／要求重驗 │
    │   │  用戶修正後重送 ▼                                │
    │   └────────── REJECTED                            │
    └───────────────────────────────────────────────────┘
```

允許的轉移（每次轉移都在同一 atomic 內寫一筆 `KycRecordModel`）：

| 從 | 到 | 由誰 | 觸發 | 寫入的 record 事件 |
|---|---|---|---|---|
| `UNVERIFIED` / `REJECTED` | `VERIFYING` | 用戶本人 | 送審（填 KYC 欄位）| `SUBMITTED`（含欄位快照）|
| `VERIFYING` | `APPROVED` | staff | approve 端點 | `APPROVED` |
| `VERIFYING` | `REJECTED` | staff | reject 端點 | `REJECTED`（含 reason）|
| `APPROVED` | `UNVERIFIED` | staff／系統 | revoke 端點（懲罰性作廢）| `REVOKED`（含 reason）|
| `APPROVED` | `UNVERIFIED` | staff／系統 | reverify 端點（例行重驗）| `REVERIFY_REQUIRED`（含 reason）|

最後兩列狀態轉移相同、事件不同，故拆成兩個端點／兩個事件。`reject`/`revoke`/`reverify` 的 `reason` 皆必填（在端點／serializer 驗，DB 欄位維持可空）。

不允許的轉移（後端須擋，回 400）：

- 從 `VERIFYING` 再送審（已在審核中）。
- 從 `APPROVED` 直接再送審或再被 approve/reject（須先 revoke／reverify 打回 `UNVERIFIED`）。
- 對非 `VERIFYING` 者 approve/reject。
- 對非 `APPROVED` 者 revoke／reverify。

每個轉移都要通過兩道檢查：**狀態守衛**（現在狀態允不允許此轉移）與**權限**（觸發者是否有資格），兩者皆過才改狀態並寫 record。

**打回一律 `APPROVED → UNVERIFIED`，不跳 `VERIFYING`**：撤銷／重驗當下用戶尚未交新資料，沒有東西可審，`UNVERIFIED` 語意才正確。若日後要區分「從未驗證」與「曾通過但過期」，可另加狀態（範圍一先複用 `UNVERIFIED`，完整歷史在 `KycRecordModel` 可查）。

> 設計預留（非本輪）：自動定期覆審需 Celery beat 排程掃描 + 覆審週期政策，屬 KYC-B。若週期固定，「下次覆審時間」多半可由「最近一筆 `APPROVED` record 的 `created_at` + 週期」推算，不必存欄位；僅當週期因風險分級而異時，才值得於 profile 存覆審到期欄位，配 beat 掃描過期打回 `UNVERIFIED` 並寫 record。

### 3. API 端點（第一步）

新增獨立 `KycViewSet`（建議 `GenericViewSet`），註冊於 `member/urls.py` router：`router.register(r"kyc", KycViewSet)`，掛在 `/api/user/kyc/...`。權限分角色層與擁有權層兩層。不併入 `UserViewSet`（後者已鎖 `IsAdminUser`，一般用戶無法用它送審或查自己）。

| 端點 | 方法 | 說明 | 權限 | 狀態轉移 |
|---|---|---|---|---|
| `/api/user/kyc/` | POST | 送審：body `{legal_name, id_number, birth_date, nationality}`，綁 `request.user` | IsAuthenticated | `UNVERIFIED`/`REJECTED` → `VERIFYING` |
| `/api/user/kyc/me/` | GET | 查自己的 KYC 狀態（`@action(detail=False)`）| IsAuthenticated | — |
| `/api/user/kyc/{user_id}/approve/` | POST | 通過（`@action(detail=True)`；KYC-B 起 body 帶 `kyc_tier`，見 §6.3）| IsAdminUser | `VERIFYING` → `APPROVED` |
| `/api/user/kyc/{user_id}/reject/` | POST | 拒絕：body `{reason}`（必填）| IsAdminUser | `VERIFYING` → `REJECTED` |
| `/api/user/kyc/{user_id}/revoke/` | POST | 撤銷：body `{reason}`（必填）| IsAdminUser | `APPROVED` → `UNVERIFIED`（記 `REVOKED`）|
| `/api/user/kyc/{user_id}/reverify/` | POST | 要求重驗：body `{reason}`（必填）| IsAdminUser | `APPROVED` → `UNVERIFIED`（記 `REVERIFY_REQUIRED`）|

技術約束：

- **`detail=True` 以 user id 定位**：建議 `queryset = UserProfileModel.objects.all()` + `lookup_field = "user_id"`，URL 直接吃 `user_id`。
- **送審綁 `request.user`，不信任 body 身分**：誰送審由 JWT 決定（`request.user.profile`），body 只帶 KYC 資料欄位，不得指定「幫誰送審」。
- **`me/` 端點**：一般用戶在 `UserViewSet` 鎖 admin 後查不到自己資料，`me/` 至少讓其查得到自己的 KYC 狀態。
- **狀態守衛位置**：範圍一端點單純，守衛寫在 view/serializer 的 `validate`/action 內即可；非法轉移回 400 帶清楚訊息。
- **`revoke` 與 `reverify` 共用轉移邏輯**：兩者守衛（須 `APPROVED`）、轉移（`→ UNVERIFIED`）、reason 必填完全相同，僅差寫入的 `event_status`。抽一個內部 helper 收 `event_status` 與 `reason` 兩參數，兩個 action 各帶 `REVOKED` / `REVERIFY_REQUIRED` 呼叫。
- **回應格式**：`me/` 與送審回當前狀態與身分欄位，`id_number` 不回全碼（至少遮罩）；approve/reject/revoke/reverify 回更新後狀態；`me/` 可選附 record 歷史。
- **who/when 由 record 承載**：由 `operator` 與 `created_at` 記錄，不往 profile 塞 reviewed_by/at。

### 4. 出金閘門（位置與狀態碼）

在 `WalletViewSet.withdraw`（`member/views.py`）**扣款之前**加閘門：KYC 未 `APPROVED` → 擋下、回 **403**（`PermissionDenied`），餘額完全不變。

- **位置**：在 `select_for_update` 鎖錢包／改餘額**之前**——閘門是准入條件，資格不符不該進到動錢段。若日後出金抽成 service 函式，閘門隨之搬到該函式開頭。
- **狀態碼**：403（權限／資格語意），非 400（400 是「資料有問題」，此處資料無誤而是「此人尚無資格」）。既有「餘額不足／無錢包」仍回 400，兩者並存。
- **無 profile 的用戶**：以 `getattr(request.user, "profile", None)` 取；取不到或 `latest_kyc_status != APPROVED`，一律視為未通過 → 403。
- 入金（`deposit`）不設閘門。

概念（實作由使用者完成）：

```
# withdraw 一進來、動錢之前：
profile = getattr(request.user, "profile", None)
if profile is None or profile.latest_kyc_status != KycStatus.APPROVED:
    raise PermissionDenied("KYC 未通過，無法出金")
# ...（原本的 select_for_update / 餘額檢查 / 扣款 / 寫 ledger 不變）
```

### 5. 下單閘門〔KYC-B / B1〕

在 `OrderViewSet.create`（下單入口）動錢之前，加與出金閘門完全同型的檢查：KYC 未 `APPROVED` → 擋下、回 **403**，不建單、不凍結、不寫帳本。

- **位置**：貼在 `serializer.is_valid()` / `transfer_to_frozen` / 建單 / 寫 ledger **之前**。
- **狀態碼**：403（`PermissionDenied`）；既有「餘額不足／幣對未開放」仍回 400，並存。
- **無 profile 的用戶**：比照出金閘門處理。
- **與出金閘門唯一差別是門的位置**：出金在 `WalletViewSet.withdraw`，下單在 `OrderViewSet.create`；檢查邏輯相同（`latest_kyc_status != APPROVED → 403`）。
- **`cancel` 不設閘門**：取消是把凍結退回可用，資金留在系統內。

閘門置於 view 層，僅影響經 API POST 下單的路徑；以 ORM/service 直接建單或撮合的路徑（如直接呼叫 `match_order`/`cancel_order`）不經過 view 的 create，繞過此閘門。這是「閘門放 view 而非 service」的既定取捨。

### 6. 分級額度〔KYC-B / B2〕

把出金的單一 KYC 門檻升級為 RBA 分級每日上限。三個定案的設計決策：**法幣計價（固定匯率表佔位）＋ 自然日重置 ＋ 帳本流水計量**。

#### 6.1 等級、法幣估值與每日上限

- **等級欄位**：`UserProfileModel.kyc_tier`（整數，預設 `0`）＝交易者等級，決定額度（交易與出金）；與驗證狀態 `latest_kyc_status` **正交**（未通過是被拒，不是等級 0）。`makemigrations member && migrate`。
- **法幣估值（由管理者維護，兩件事）**：
  - **參考法幣**：系統以哪一種法幣計價（如 TWD、USD）。以 `LegalTenderModel`（`currency` app）維護一份法幣清單，欄位 `code`（法幣代碼，**唯一、不可重複**）與 `enable`（是否啟用，預設否）；**最多只能有一筆 `enable=True`**（以 DB 條件式唯一約束保證）。目前參考法幣＝唯一啟用的那筆；admin 在後台維護清單並啟用其一。所有匯率與每日上限的數字都以此法幣為單位。
  - **各幣別匯率**：在 `CurrencyModel` 加 `fiat_rate` 欄位（`Decimal`）＝ 1 單位該幣別值多少參考法幣，由管理者在 admin 維護（`CurrencyModel` 已註冊 admin，新欄位即可編輯）。真實系統此值由有防護的指數價／行情自動更新；範圍一手動維護當佔位。
  - `makemigrations currency && migrate`。
- **每日上限 `KYC_TIER_DAILY_LIMIT`（`member/constants.py`）**：等級 → 每日上限（以參考法幣為單位）；`None` 表無上限。此為政策數字，仍以常數維護（日後若也要讓管理者調，比照匯率改成資料即可）。
- 範圍一契約值（與 `member/tests/test_kyc_tier.py` 一致）：`LegalTenderModel` 的 `code` 唯一、最多一筆 `enable=True`（測試驗證）；`CurrencyModel.fiat_rate` 建立幣別時設定（USDT=1、BTC=30000）；`KYC_TIER_DAILY_LIMIT = {0: 0, 1: 100000, 2: None}`。

#### 6.2 閘門位置與計量

閘門加在 `WalletViewSet.withdraw`，位於**現有 KYC-APPROVED 閘門之後、動錢（鎖錢包／改餘額）之前**：

1. 取 `request.user.profile.kyc_tier` 對應的 `KYC_TIER_DAILY_LIMIT[tier]`；為 `None` 則無上限，跳過整個檢查。
2. （有限額度時）若本次出金幣別的 `fiat_rate <= 0`（未設匯率）→ 直接拒絕（回 **403**）：無法估值就不能放行，否則未設匯率的幣別會以 0 值繞過額度。無上限等級已在上一步跳過，不受此限。
3. 今日已用（法幣）＝ 該用戶今日（自然日起點至今）所有 `reason=WITHDRAW` 的 `LedgerEntryModel`，各取 `abs(delta) × 該幣別的 fiat_rate` 加總。
4. 本次出金（法幣）＝ `quantity × 本次幣別的 fiat_rate`。
5. 「今日已用 ＋ 本次 ≤ 上限」→ 放行（續走原扣款流程）；否則 → 回 **403**（`PermissionDenied`），餘額不變、不寫帳本。

今日已用、本次出金、每日上限三者皆以參考法幣為單位，加總與比較才有意義；`LegalTenderModel`（參考法幣）只是宣告這個單位，不改變上述算式。

**自然日起點**：以 `timezone.now()` 當天 `00:00`（時區依 `settings.TIME_ZONE`；範圍一用 UTC）為窗起點，`created_at >= 起點` 即「今天」。

#### 6.3 審核通過時由審核人員決定等級

`kyc_tier` 是**交易者的等級**，決定其額度（交易與出金），與「是否通過驗證」正交——未通過是被拒（`REJECTED`），不是等級低。核給的等級由審核人員在核准當下決定。`approve` 端點 body 帶 `kyc_tier`（核給的等級），在把狀態改為 `APPROVED` 的同一 atomic 內把 `kyc_tier` 設為該值；`APPROVED` 事件仍照常寫入歷史層。

- `kyc_tier` 可為任一已定義等級，**含 `0`**（等級 0 是最低額度，不代表驗證無效；核給 0 仍是 `APPROVED`）。
- 契約：`test_kyc_tier.py::ApproveAssignsTierTest`。

#### 6.4 技術約束／注意事項

- **換算取絕對值**：`WITHDRAW` 的 `delta` 為負，累計時取 `abs(delta)` 再乘匯率。
- **窗起點用時間、且 ledger 為 append-only**：跨日邊界測試無法直接改 `created_at`，須以凍結時間（`mock`/freezegun 蓋掉 `timezone.now`）造「昨天」的出金列。
- **參考法幣（`LegalTenderModel`）與各幣匯率（`fiat_rate`）皆由管理者維護**：哪種法幣（`code` 唯一、最多啟用一種）與各幣匯率都在 admin 設定；`fiat_rate` 為真實指數價的佔位（真實系統由行情自動更新），切勿改用單一市場最近成交價（可被少量對敲操縱以撐大可提額度）。
- **「最多啟用一種」以 DB 條件式唯一約束強制**：`UniqueConstraint(condition=Q(enable=True), ...)` 讓 `enable=True` 在啟用者間唯一，而非只靠應用層檢查（併發下才不會出現兩筆同時啟用）；`code` 用 `unique=True`。
- **下單（B1）不套額度**：分級額度只加在出金這道「錢離開」的門上。
- **既有走出金 API 的測試都會被額度閘門波及**：用戶預設 `kyc_tier=0`（上限 0），加閘門後既有出金測試會全變 403。已把這些測試 setUp 的出金用戶改為 `kyc_tier=2`（無上限），讓分級閘門對聚焦其他邏輯的測試無影響（同 KYC-A 當初給 APPROVED 的處理）：`member/tests/test_withdraw.py`、`member/tests/test_kyc.py`（`WithdrawKycGateTest`）、`ledger/tests/test_deposit_withdraw.py`（`DWBaseTestCase`）、`ledger/tests/test_ledger.py`（`LedgerBaseTestCase`）。
- 測試：`member/tests/test_kyc_tier.py`。

### 7. 第二步：MinIO 物件儲存 + 證件上傳（設計預留，本輪不實作）

- **新 model `KycDocumentModel`**（一位用戶多張文件）：`user` FK、`doc_type`（FRONT/BACK/SELFIE）、`object_key`（物件在 bucket 的鍵）、`uploaded_at`。DB 只存 key，不存二進位。
- **MinIO**：新增一個 Docker 容器（同 `exchange-redis` 模式），S3 相容；Django 端 `django-storages` + `boto3`，設 S3 endpoint。
- **證件照為全系統最敏感資料**：bucket 必須私有、不可走 public URL；存取一律用預簽名 URL（presigned URL，有時效的臨時連結）。
- **送審流程**：第一步的「填文字欄位送審」擴充為「填欄位 + 上傳正反面 + 自拍 → VERIFYING」，狀態機本身不變。
- 此步仍屬範圍一（不碰鏈），排在第一步之後，勿與範圍二（測試鏈，`*-2` 檔）混淆。

### 8. 技術約束／注意事項

- **每個轉移＝兩筆寫入，須包在同一 `transaction.atomic()`**：更新 profile 當前狀態 ＋ 新增一列 `KycRecordModel`，兩者同生同死；若狀態改了卻沒寫 record（或反之），current 與 history 對不上，稽核軌跡斷點。與帳本「改餘額 ＋ 寫分錄」同原則。KYC 不寫 `ledger`（ledger 記資金移動，KYC 未動錢）。
- **append-only 契約**（照 `LedgerEntryModel`）：`save()` 於 `self.pk` 已存在時 `raise ValueError`（擋更新）；`delete()` 一律 `raise ValueError`；只能 `create`。approve 是新增一筆 `APPROVED`，不是 update 那筆 `SUBMITTED`——這是它與可變狀態表的最大差異，實作時勿手滑改成可更新。
- **PII 加密為待辦**：`id_number` 範圍一先明文存，為刻意過渡。正解比照 2FA 的 `encrypted_totp_secret` 用 `Fernet` 加密，或查詢時遮罩。注意 profile 與 record 兩邊都有 `id_number`，加密須一起處理。
- **`00_overall_spec.md`**：KYC 未新增 app（欄位掛在 member），依賴圖不變；功能總表若要標記出金／下單閘門完成，可順手補一行。
