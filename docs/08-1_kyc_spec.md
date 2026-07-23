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
> **狀態**：第一步——規格與測試（`member/test/test_kyc.py`）由 Claude 撰寫完成，實作由使用者完成。

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
    UNVERIFIED = "UNVERIFIED", "未驗證"
    PENDING    = "PENDING",    "審核中"
    APPROVED   = "APPROVED",   "已通過"
    REJECTED   = "REJECTED",   "已拒絕"
```

> **為什麼獨立一個 constants.py**：`member` 目前沒有 constants.py（狀態字串散在各處）。KYC 引入第一組
> 明確的狀態機，趁機比照 `transaction`/`ledger` 收進 `member/constants.py`。狀態值是資料契約，
> serializer、view、閘門、admin、測試都會引用同一份，集中一處才不會出現「PENDING」打成「pending」這種對不上的坑。

### 3.2 UserProfileModel 新增欄位

KYC 欄位**掛在既有的 `UserProfileModel`**（它與 `User` 已是 `OneToOne`，KYC 也是一人一份，天生契合；且 2FA 密鑰已經在這張表，沿用同一處管理用戶敏感資料）：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `kyc_status` | `CharField(choices=KycStatus)` | `UNVERIFIED` | 狀態機當前狀態 |
| `legal_name` | `CharField(100, blank, default="")` | `""` | 法定姓名 |
| `id_number` | `CharField(100, blank, default="")` | `""` | 證件號碼（**PII，見下方警告**） |
| `birth_date` | `DateField(null=True, blank=True)` | `NULL` | 生日 |
| `nationality` | `CharField(50, blank, default="")` | `""` | 國籍（ISO 國碼或名稱，範圍一不細究） |
| `kyc_submitted_at` | `DateTimeField(null=True, blank=True)` | `NULL` | 最近一次送審時間 |
| `kyc_reviewed_at` | `DateTimeField(null=True, blank=True)` | `NULL` | 最近一次審核時間 |
| `kyc_reviewed_by` | `FK(User, null=True, blank=True, related_name="kyc_reviews", on_delete=SET_NULL)` | `NULL` | 哪位 staff 審的（稽核用） |
| `kyc_reject_reason` | `CharField(255, blank, default="")` | `""` | 被拒原因（回寫給用戶看） |

> ⚠️ **`id_number` 是 PII（個人可識別資訊），範圍一先明文存，但這是刻意的技術債**：
> 正解與 2FA 密鑰同款——用 `Fernet` 加密後存（`02-1` §3 的 `encrypted_totp_secret` 就是範本），
> 或至少查詢時遮罩。**證件照（第二步）敏感度更高**，那時 bucket 必須私有、走預簽名 URL（§8）。
> 本階段先把流程跑通，把加密列為 §7 的待辦，不要在明文欄位上疊功能後才回頭補。

> **要不要另開一張 `KycProfileModel`？** 也可以，好處是把最敏感的 PII 與一般 profile 隔離、日後容易做欄位級存取控制。
> 但範圍一為了「由淺入深、少一個 model 少一層 join」，**建議先掛在 `UserProfileModel`**；
> 第二步的**證件文件**才獨立成新 model（一人可有正面/反面/自拍多張，是一對多，天生該分出去，見 §8）。

### 3.3 Migration

新增欄位後 `makemigrations member && migrate`。全部有預設值（`UNVERIFIED` / `""` / `NULL`），既有用戶不需資料搬遷，安全。

---

## 4. 狀態機（第一步的核心）

```
          用戶送審                    staff 通過
UNVERIFIED ───────▶ PENDING ─────────────────────▶ APPROVED   （終態，範圍一不 downgrade）
    ▲                  │
    │                  │ staff 拒絕
    │   用戶修正後重送    ▼
    └────────────── REJECTED
```

**允許的轉移，與「誰能觸發」：**

| 從 | 到 | 由誰 | 觸發 |
|---|---|---|---|
| `UNVERIFIED` / `REJECTED` | `PENDING` | **用戶本人** | 送審（填 KYC 欄位）|
| `PENDING` | `APPROVED` | **staff** | approve 端點 |
| `PENDING` | `REJECTED` | **staff** | reject 端點（帶原因）|

**不允許（要在後端擋，回 400）：**

- 從 `PENDING` 再送審（已在審核中，重複送沒意義）。
- 從 `APPROVED` 再送審或再被 approve/reject（已是終態）。
- 對**非 `PENDING`** 的人 approve/reject（沒有在等審的東西可審）。

> **心智模型**：狀態機的每個「轉移」都要問兩件事——**現在的狀態允不允許這個轉移**（狀態守衛）、
> **這個人有沒有資格觸發這個轉移**（權限）。兩者都過，才改狀態。這和 M4 訂單狀態機
> 「終態不可再變」（`03-1`/`05-1`）是同一套思路，換個場景。
>
> **`REJECTED → PENDING` 為什麼要留**：被拒不能是死路，否則用戶打錯一個字就永遠出不了金。
> 真實所都允許補件重送。實作上「送審」這個動作對 `UNVERIFIED` 和 `REJECTED` 一視同仁。

---

## 5. API 端點（第一步）

新增一個 **`KycViewSet`**（建議 `GenericViewSet`），註冊在 `member/urls.py` 的 router：`router.register(r"kyc", KycViewSet)` → 掛在 `/api/user/kyc/...`。權限**分兩層**（呼應 `02-1` §6.5：角色層 vs 擁有權層）：

| 端點 | 方法 | 說明 | 權限 | 狀態轉移 |
|---|---|---|---|---|
| `/api/user/kyc/` | POST | 送審：body `{legal_name, id_number, birth_date, nationality}`，綁 `request.user` | IsAuthenticated | `UNVERIFIED`/`REJECTED` → `PENDING` |
| `/api/user/kyc/me/` | GET | 查**自己**的 KYC 狀態（`@action(detail=False)`）| IsAuthenticated | — |
| `/api/user/kyc/{user_id}/approve/` | POST | 通過（`@action(detail=True)`）| **IsAdminUser** | `PENDING` → `APPROVED` |
| `/api/user/kyc/{user_id}/reject/` | POST | 拒絕：body `{reason}`（`@action(detail=True)`）| **IsAdminUser** | `PENDING` → `REJECTED` |

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
  `validate`/action 內即可；轉移非法 → 回 **400** 帶清楚訊息（如「目前狀態為 PENDING，無法重複送審」）。
- **審核要記 who/when**：approve/reject 時寫入 `kyc_reviewed_by = request.user`、`kyc_reviewed_at = now()`；
  reject 另寫 `kyc_reject_reason`。這是稽核軌跡，別省。
- **回應格式**：`me/` 與送審回 `{kyc_status, legal_name?, nationality?, kyc_reject_reason?, ...}`（**別回 id_number 全碼**，最少要遮罩）；approve/reject 回更新後的狀態。

> **要不要包 `transaction.atomic`**：送審只改一列、approve/reject 只改一列，單筆更新本身原子。
> 但若你在同一動作裡**又寫稽核／帳本**，就要包在一起（同 `07-1` 的套用點原則）。範圍一 KYC 不寫 ledger
> （ledger 記的是「錢的移動」，KYC 沒有動錢），所以先不用，但知道這條界線。

---

## 6. 出金閘門（第一步的重點）

在 `WalletViewSet.withdraw`（`member/views.py`）**扣款之前**加一道檢查：**KYC 未 `APPROVED` → 擋下、回 403，餘額完全不變。**

**擺放位置**：要在 `select_for_update` 鎖錢包 / 改餘額**之前**——閘門是「有沒有資格出金」，資格不符就根本不該進到動錢那段。

**回哪個狀態碼**：建議 **403 Forbidden**（用 `rest_framework.exceptions.PermissionDenied`），不是 400。
理由：400 是「你送的資料有問題」，但這裡資料沒問題，是**你這個人還沒有資格**做這件事——那是權限/資格語意，403 才對。（現有 withdraw 的「餘額不足／無錢包」仍是 400，兩者語意不同，並存。）

**沒有 profile 的用戶怎麼辦**：用 `getattr(request.user, "profile", None)` 取（同 `LoginSerializer` 擋 superuser 無 profile 的手法，見 `02-1` §6.1）。取不到 profile，或 `profile.kyc_status != APPROVED`，一律視為未通過 → 403。

概念上（**實作由你寫**）：

```
# withdraw 一進來、動錢之前：
profile = getattr(request.user, "profile", None)
if profile is None or profile.kyc_status != KycStatus.APPROVED:
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
  `setUp` 幫 `self.user` 建一份 `kyc_status=APPROVED` 的 profile，讓「有資格者出金」的既有行為維持綠。
  （這正是 KYC-B「下單閘門會牽動 25+ 條交易測試」的小型預演——先在這一支感受一次。）
- **只有 `test_withdraw` 受影響**：`test_2fa` / `test_register_wallets` / `test_user_permissions` 不碰 withdraw，不受閘門影響。
- **註冊/登入/2FA 流程不動**：KYC 是**登入後**的獨立流程；新用戶註冊完 `kyc_status` 預設 `UNVERIFIED`，能登入、能看行情，但不能出金。
- **PII 加密是待辦**：`id_number` 明文存是刻意的過渡（§3.2）。列入待辦：比照 `encrypted_totp_secret` 用 Fernet，或查詢遮罩。
- **`00_overall_spec.md`**：KYC 沒有新增 app（欄位掛在 member），依賴圖不變；若 §5 功能總表要標記「KYC-A 出金閘門完成」，可順手補一行（非必要）。

---

## 8. 第二步：MinIO 物件儲存 + 證件上傳（設計預留，本輪不實作）

> 本節是**下一輪**才做的設計預留，讓第一步的欄位/狀態機留好接點。**本輪不寫這段的測試與實作。**

- **新 model `KycDocumentModel`**（獨立成一對多：一位用戶多張文件）：`user` FK、`doc_type`（FRONT/BACK/SELFIE）、`object_key`（物件在 bucket 的鍵）、`uploaded_at`。**DB 只存 key，不存二進位。**
- **MinIO**：多一個 Docker 容器（同 `exchange-redis` 模式），S3 相容。Django 端 `django-storages` + `boto3`，設 S3 endpoint。
- **證件照是全系統最敏感的資料**：bucket **必須私有**、**絕不可走 public URL**；存取一律用**預簽名 URL（presigned URL）**（有時效的臨時連結）。
- **送審流程改版**：第一步的「填文字欄位送審」擴充為「填欄位 + 上傳正反面 + 自拍 → PENDING」。狀態機本身不變。
- **與範圍二的關係**：這仍屬**範圍一**（不碰鏈），所以留在 `08-1`，只是排在第一步之後；不要跟範圍二（測試鏈，`*-2` 檔）搞混。

---

## 9. 實作 checklist（第一步，給使用者；對應 TASKS.md）

- [ ] `member/constants.py`：新增 `KycStatus`（UNVERIFIED/PENDING/APPROVED/REJECTED）。
- [ ] `UserProfileModel`：加 §3.2 的九個欄位；`makemigrations member && migrate`。
- [ ] `member/serializers.py`：`KycSubmitSerializer`（送審欄位、綁 request.user）、`KycStatusSerializer`（回狀態、遮罩 id_number）、reject 的 `{reason}`。
- [ ] `member/views.py`：`KycViewSet`——`create`(送審，狀態守衛)、`me`(detail=False GET)、`approve`/`reject`(detail=True, IsAdminUser, 記 who/when)。
- [ ] `member/urls.py`：`router.register(r"kyc", KycViewSet)`。
- [ ] `WalletViewSet.withdraw`：開頭加出金閘門（未 APPROVED → 403，動錢前）。
- [ ] （選做）`member/admin.py`：把 KYC 欄位加進 profile admin，方便手動查/改。
- [ ] 跑測試：`member/test/test_kyc.py` 全綠、且 `test_withdraw` / `test_2fa` / `test_register_wallets` / `test_user_permissions` 不退步。

> 測試（`member/test/test_kyc.py`）已由 Claude 寫好，是本步「正確行為」的定義——照著讓它變綠即可。
