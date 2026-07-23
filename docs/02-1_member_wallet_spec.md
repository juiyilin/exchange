# 細部規格 — 用戶與錢包（member）〔範圍一〕

> 對應 app：`member`　主要 model：`UserProfileModel`、`WalletModel`
> 上層文件：`00_overall_spec.md`　**範圍二續篇：`02-2_member_wallet_spec.md`**（充值地址、熱/冷錢包）
>
> 本檔只談**範圍一（純內部帳本，不碰鏈）**。
> 注意：`WalletModel` 是**內部帳本（IOU）**，不是區塊鏈錢包——它在範圍二**依然不變**。
> 範圍二新增的是「充值地址管理」這種**鏈上**的東西，收在 `02-2`。
> （`UserProfileModel.address` 是**住址**，與鏈上地址無關，別搞混。）
>
> **狀態（2026-07-15 同步至實作）**：§4 API、§6.1 認證、§6.2 帳本、§6.3 自動建錢包
> 皆已落地；§6.4 精度未做（範圍二阻擋項）；§6.5 RBAC 僅完成粗粒度（is_staff），
> Group 分角色待 M-RBAC。KYC 規格另立（見 TASKS.md「M-KYC」）。

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
| `user` | OneToOne → User | 對應的 Django 用戶（`related_name='profile'`） |
| `phone_number` | CharField(20) | 電話 |
| `address` | CharField(255) | **住址**（與鏈上地址無關） |
| `two_factor_enabled` | BooleanField | 是否已啟用 2FA（M7-B） |
| `encrypted_totp_secret` | BinaryField | Fernet 加密後的 TOTP 密鑰（M7-B） |

> **TOTP 密鑰為什麼要加密存**：它等同於用戶的第二把鑰匙——DB 若被拖走，明文密鑰讓攻擊者
> 可以自行產生任何用戶的 2FA 碼，2FA 直接失效。用 `Fernet` 對稱加密，金鑰（`FERNET_KEY`）
> 放設定／環境變數，**不與 DB 存在一起**。相關方法：`decrypt_totp_secret()`、
> `get_secret_qrcode_link()`、`get_current_totp()`。

### WalletModel（繼承 BaseTimeModel）
| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | FK → User | 擁有者 |
| `asset_type` | FK → CurrencyModel | 哪種幣 |
| `available_balance` | Decimal(20,2) | 可用餘額 |
| `frozen_balance` | Decimal(20,2) | 凍結餘額 |

約束：`unique_together = (user, asset_type)` — 每人每幣只能有一個錢包。這個約束很關鍵，避免同一人同一幣出現兩個錢包導致餘額分裂。

## 4. API（現況）

`member/urls.py` 掛在 `/api/user/...` 底下。**粗體**為免登入端點：

| 端點 | 方法 | 說明 | 權限 |
|---|---|---|---|
| **`/api/user/register/`** | POST | 註冊：建 User + Profile + TOTP 密鑰 + 初始錢包，回 `{username, secret, issuer, qrcode_link}` | AllowAny |
| **`/api/user/register/`** | PUT | 啟用 2FA，body `{username, totp}` | AllowAny |
| **`/api/user/login/`** | POST | 登入，body `{username, password, totp}` → JWT `{access, refresh}` | AllowAny |
| **`/api/user/token/refresh/`** | POST | 換新 access token | AllowAny |
| `/api/user/user/` | GET | 用戶列表／單筆 | **IsAdminUser** |
| `/api/user/wallet/` | GET/POST | 錢包（`get_queryset` 過濾 `request.user`；staff 看全部） | IsAuthenticated |
| `/api/user/wallet/deposit/` | POST | **入金（admin 專用）**，body `{user_id, asset_type_id, quantity}` | IsAdminUser |
| `/api/user/wallet/withdraw/` | POST | 出金，body `{asset_type_id, quantity}` | IsAuthenticated |

全域預設 `IsAuthenticated` + `JWTAuthentication`（見 `REST_FRAMEWORK` 設定），個別 view 用
`permission_classes` 覆寫。

> **技術債已清除**：舊版本此處記載的 `get_random_user_id()` 隨機指派用戶（demo 用 placeholder）
> 已於 M7 全數移除，三處（下單、建錢包、出金）皆改綁 `request.user`。
>
> **註冊為什麼是獨立的 `RegisterView`、不是 `UserViewSet.create`**：註冊必須免登入，
> 但全域地板是 `IsAuthenticated`。若把註冊放在 `UserViewSet` 裡，就得對單一 action 做
> 權限豁免（`get_permissions` 依 `self.action` 分歧），而同一個 ViewSet 又要
> 鎖成 admin-only，等於一個類別同時承擔兩種相反的權限語意，容易改錯。
> 拆成 `RegisterView`（自帶 `authentication_classes = []` / `permission_classes = []`）後，
> 「鎖 `UserViewSet`」與「開放註冊」互不干擾。
>
> **入金為什麼是 admin-only**：真實 CEX 沒有「用戶呼叫 API 說幫我入金」——用戶在**鏈上**
> 自己轉帳，交易所靠**監聽服務偵測到帳**才入帳。入金的本質是「憑空增加餘額」，
> 範圍一沒有鏈上依據，開放給一般用戶等於任何人都能鑄錢。這個端點是**鏈上監聽器的替身**，
> 範圍二會被取代並移除。詳見 `06-1` / `07-1` §4.1。

### 4.1 註冊時建立初始錢包

註冊 body 可選帶 `wallet_currency_ids`（一組 `CurrencyModel.id`）：

```json
{
  "username": "alice",
  "password": "...",
  "phone_number": "0900000000",
  "address": "Taipei",
  "wallet_currency_ids": [1, 2]
}
```

規則：

- **選填**：不帶或帶空陣列 → 不建任何錢包，註冊照樣成功。
- **新錢包餘額必為 0**：`available_balance = frozen_balance = 0`。**註冊不等於入金**——
  白送餘額等同憑空鑄錢，與上面「入金 admin-only」擋的是同一件事。
- **重複 id 要去重**：`unique_together = (user, asset_type)` 是 DB 層防線（見 §7），
  前端多送一次同樣的 id 很常見，應被安靜正規化，不該回 500。
- **非法幣別 id → 400，且整筆註冊回滾**：見 §4.2。
- 回應格式不變（仍是 `{username, secret, issuer, qrcode_link}`）。錢包建了沒，
  用 `GET /api/user/wallet/` 查。

> **為什麼是「勾選」而不是寫死 USDT/BTC**：**幣別是資料，不是常數。**
> 若在 `create()` 裡寫死 `code="USDT"`，上架第三種幣時就得回頭改註冊邏輯；
> 而且測試環境沒建該幣別時註冊會直接炸。讓呼叫端指定，註冊邏輯就與
> 「目前上架哪些幣」解耦。
>
> 欄位型別用 id 而非 code，與 `WithdrawSerializer` / `DepositSerializer` 的
> `asset_type_id` 風格一致。

### 4.2 註冊的原子性

`RegisterView.create` 包 `@transaction.atomic`。**要嘛全部成功，要嘛什麼都沒發生。**

沒有它會怎樣：User 建好、Profile 建好，然後建錢包時幣別 id 非法炸掉 → DB 留下一個
「有帳號、沒錢包、但也沒拿到 TOTP secret 回應」的殭屍用戶。使用者重試註冊會撞
`username 已存在`，**帳號等於被自己卡死**。

這與 M3 結算的「四個錢包要嘛一起動、要嘛都不動」（`05-1`）是同一條原則，換個場景而已。

### 4.3 已知缺口：一般用戶無法查自己的資料

`UserViewSet` 鎖成 `IsAdminUser` 後，一般用戶沒有任何端點能查自己的 profile。
慣例解法是 `GET /api/user/user/me/`（`@action(detail=False)`，回 `request.user` 自己）。

**刻意延後**：KYC 階段會需要「查自己的 KYC 狀態」，屆時一起設計，免得改兩次。

> 為什麼不把 `retrieve` 放寬成「可以查自己」？因為 `IsAdminUser` 是**角色層**判斷
> （見 §6.5），它只問「你是不是 staff」，不問「你要查的是不是自己」。
> 混進擁有權判斷會讓一個端點同時承擔兩種權限語意，日後難以維護。

### 4.4 基本階段的原始目標（已達成，留存備查）

- 能建立用戶
- 能為用戶建立各幣別的錢包
- 能查詢某用戶的所有錢包餘額

## 5. 基本階段：你要完成的事 — ✅ 全部完成（M1）

1. admin 後台註冊 `UserProfileModel`、`WalletModel`，能手動檢視/修改餘額（這同時是你「模擬入金」的方法——直接在 admin 改 available_balance）。
2. 確認建立用戶時能順帶建 profile（或允許 profile 為空）。
3. 為測試用戶各建一個 USDT 錢包和一個 BTC 錢包，給 USDT 錢包一些初始可用餘額（模擬入金）。
4. 能透過 API 或 admin 查到餘額。

**驗證方式**：建一個用戶、給他 100000 USDT 可用餘額，API 查得到 `available_balance=100000, frozen_balance=0`。

## 6. 進階階段：逐步加深

### 6.1 真實認證 — ✅ 已完成（M7）

原本的技術債（所有「誰在操作」都靠 `get_random_user_id()` 亂猜）已清除。落地成果：

- **JWT**（`djangorestframework-simplejwt`）：`JWTAuthentication` + 全域 `IsAuthenticated`。
- **註冊**：`RegisterView`（免登入），建 User + Profile + TOTP 密鑰（+ 初始錢包，見 §4.1）。
- **登入**：`LoginView` / `LoginSerializer`（subclass `TokenObtainPairSerializer`），
  帳密過後**再驗 TOTP** 才發 JWT。
- **強制 2FA**：未啟用 2FA 者不准登入。
- **擁有權**：錢包/訂單 `get_queryset` 過濾 `request.user`（staff 看全部）；
  cancel 綁 `user=request.user`。
- `get_random_user_id()` 三處用法（下單、建錢包、出金）全數移除。

> **踩過的坑**：強制 2FA 導致 superuser（無 profile）走 JWT login 會炸 →
> 用 `getattr(user, 'profile', None)` 擋。另外 `verify_totp` 必須設 `valid_window=1`
> 容忍 ±30 秒時鐘誤差，不設會讓 2FA 測試跨 30 秒邊界時間歇性失敗。
> 測試：`member/test/test_2fa.py`。

### 6.2 帳本流水（Ledger Entry）— ✅ 已完成，但**不在 member**

**架構決策**：`LedgerEntryModel` 與 `DepositWithdrawModel` 獨立成新的 **`ledger` app**
（原規劃在 member）。

理由：`ledger` 只能**向下依賴 `currency`**——`asset_type` FK 到 `CurrencyModel`（**不 FK wallet**）、
`ref_type`/`ref_id` 用軟參照字串（**不 FK Order/Transaction**），否則會循環依賴。
層次：`common ← currency ← ledger ← member ← transaction`。

**完整規格見 `07-1_logging_audit_spec.md`**，本檔不重複。member 這邊只需知道：
`transfer_to_frozen`(FREEZE)、`transfer_asset`(SETTLE)、`release_frozen`(UNFREEZE/REFUND)、
`withdraw`(WITHDRAW)、`deposit`(DEPOSIT) 五個套用點都要在**同一個 atomic 內**補寫帳本分錄。

### 6.3 自動建錢包 — ✅ 已完成

`transfer_asset`（收款腿）與 `deposit` 都用 `get_or_create`，用戶第一次碰到某幣別時
自動建該幣錢包，不必預先建好所有幣的錢包。

**注意 `withdraw` 刻意相反**：用 `.get()`，錢包不存在直接回 400。
語意不同——「收錢」時沒錢包是正常的（該建），「出金」時沒錢包代表你根本沒有這種幣（該擋）。

§4.1 的「註冊時建初始錢包」與本節不衝突：那是為了讓新用戶的錢包列表不是空的（UX），
`get_or_create` 則是不論有沒有預建都不會炸的底層保險。

### 6.4 精度 — ⚠️ 未做，且是**範圍二的阻擋項**

`decimal_places=2` 對 BTC 遠遠不夠：**1 satoshi（0.00000001 BTC）會被存成 0.00，直接弄丟用戶的錢。**

範圍一是模擬帳本、金額都是整數級，暫時無害。但這是跨全系統的 migration
（錢包／訂單／成交／帳本全都要改），**必須在碰真錢之前做完**。詳見 `01-2` §3 與 TASKS.md M8 第一項。

### 6.5 身份組與權限（RBAC）

認證解決「你是誰」，授權（權限）解決「你能做什麼」。這兩件事是分層的，不要混在一起：

- **認證層（M7 已做）**：JWT 認出 `request.user`，全域 `IsAuthenticated` 當地板（最低門檻：要登入）。
- **物件層擁有權（M7 已做）**：用 `get_queryset` 過濾 `request.user`、cancel 綁 `user=request.user`，保證「只能碰自己的資料」。這是「對哪一筆」的維度。
- **角色層 RBAC（本節，部分完成）**：依角色決定「能不能做某類 CRUD」。這是「能做哪種操作」的維度。
  - **已做（粗粒度）**：直接用 `is_staff` 當唯一角色軸——`UserViewSet` 與 `wallet/deposit/` 掛
    `IsAdminUser`；`WalletViewSet.get_queryset` 的 staff 分支看全站。
  - **待做（M-RBAC）**：用 Django 內建的 **Group（身份組）** 分出多個角色，取代
    「staff / 非 staff」這種二分法。

兩個維度是正交的、要一起用才完整：RBAC 管「能不能下單/能不能看全站訂單」，擁有權管「只能取消自己的單」。

實作方向（建議用 Django 既有機制，不要自己造）：

- Django 每個 model 自動有 `view / add / change / delete` 四種權限，可指派給不同 Group。
- DRF 的 `DjangoModelPermissions` 會把 HTTP 方法對應到這些權限（GET→view、POST→add、PUT/PATCH→change、DELETE→delete）。把它掛在特定 ViewSet 的 `permission_classes`，就是「依身份組決定誰能 CRUD」。
- 全域預設仍維持 `IsAuthenticated`;個別 view 用 `permission_classes` 覆寫，疊上角色判斷（`DjangoModelPermissions` 或自訂 `permission`，例如檢查 `request.user.groups`）。
- 需要更細的「同一 model、不同角色看不同欄位／不同物件」時，再寫自訂 `BasePermission` 或物件層 `has_object_permission`。

設想的角色舉例（依專案需要調整）：一般交易者（trader）只能 CRUD 自己的訂單/錢包;客服（support）可唯讀查詢用戶資料;風控/管理（admin/staff）可跨用戶查詢與凍結。

> **註冊死鎖（已解決，記錄備查）**：在全域 `IsAuthenticated` 下，若註冊端點沒有豁免，
> 就會產生經典死鎖——新用戶無法註冊 → 永遠沒有帳號可以登入。
> 本專案在 M7-B 把註冊拆成獨立的 `RegisterView`（自帶 `authentication_classes = []`）後
> **天生免疫**，不需要對 `UserViewSet` 做 action 層級的權限分歧。
> 回歸網：`member/test/test_user_permissions.py::test_register_still_open`。
>
> **`UserViewSet` 為什麼非鎖不可**：它原本沒有 `get_queryset` 過濾也沒有 `permission_classes`
> 覆寫，只吃到全域的 `IsAuthenticated` ——意思是**任何一個登入的普通用戶，都能撈出全站
> 所有人的 username、email、is_staff、電話、地址**（`UserListSerializer` 是
> `exclude = ["password"]`，除了密碼什麼都給）。
>
> 對照：`WalletViewSet` 與訂單在 M7 都做了擁有權過濾，**只有 User 這張表漏掉**。
> 用戶名單外洩在真實交易所是重大事故：它給攻擊者完整的目標清單（可拿去撞庫、釣魚），
> 而 `is_staff` 欄位等於直接標示「先打哪幾個帳號最有價值」。KYC 階段的資料
> （身分證號、證件照）敏感度只會更高，本階段等於先把地基打好。

## 7. 常見坑

- **餘額更新沒有原子性**：「讀餘額→改→存」這三步，如果兩個請求同時跑，會互相覆蓋（race condition），導致超賣。下單那段已經用了 `select_for_update()`，所有改餘額的地方都要這樣鎖。詳見 `04-1_matching_engine_spec.md` 的併發章節。
- **直接改可用餘額卻忘了凍結**：扣可用一定要對應加凍結（或對外轉出），兩邊要一起改、包在同一個 `transaction.atomic()` 裡，不能只改一半。
- **忘記 unique_together**：同一人同一幣建出兩個錢包，餘額就分裂了。
