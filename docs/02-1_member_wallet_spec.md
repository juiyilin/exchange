# 細部規格 — 用戶與錢包（member）〔範圍一〕

> 上層文件：`00_overall_spec.md`　範圍二續篇：`02-2_member_wallet_spec.md`（充值地址、熱/冷錢包）
> 相關規格：KYC 見 `08-1_kyc_spec.md`；身份組與授權層見 `09-1_permission_spec.md`；帳本流水見 `07-1_logging_audit_spec.md`；結算原子性見 `05-1`；精度 migration 見 `01-2`。
>
> 本檔只談範圍一（純內部帳本，不碰鏈）。此處的錢包是內部帳本（IOU），不是區塊鏈錢包；範圍二新增的「充值地址管理」屬鏈上主題，收在 `02-2`。用戶的住址資料與鏈上地址無關。

## 一、需求方規格

### 1. 模組職責

本模組負責兩件事：

1. **用戶**：管理誰在使用這個交易所，以及每位用戶的附加基本資料（電話、住址等）。
2. **錢包（內部帳本）**：記錄每位用戶持有多少哪一種幣。這是整個交易所最敏感、最不容出錯的資料——用戶的錢就記在這裡。

### 2. 核心概念：內部帳本與可用/凍結餘額

每位用戶、每一種幣，各有一個錢包。錢包餘額分為兩部分：

- **可用餘額**：可以自由使用的錢，能拿去下單。
- **凍結餘額**：已被某張掛單佔住、暫時不能動用的錢。

分成兩部分是為了避免用同一筆錢重複下單。例如持有 30000 USDT 並掛出一張「用 30000 買 1 BTC」的單：這 30000 尚未花掉（單還沒成交），但也不能再拿去掛另一張單，否則成交時會餘額不足。因此下單當下即把該筆金額從可用移到凍結；成交時從凍結扣除、把買到的幣加進對應錢包的可用；取消時把凍結退回可用。

### 3. 餘額不變量（最重要的一條規則）

> 任何時刻、對任何一個錢包：可用餘額與凍結餘額皆不得為負。
> 資金的搬動只在「可用」與「凍結」之間進行；除了凍結與結算的正常流程外，總量不會憑空增減。

任何可能讓餘額變負的操作都是錯誤。所有下單、撮合、結算、取消的邏輯，都必須圍繞「維持這條不變量」來設計。

### 4. 對外行為與邊界

**註冊**

- 任何人皆可免登入註冊，成功後系統為該用戶建立帳號、附加資料，以及一次性驗證碼的密鑰。
- 註冊時可選擇性指定要一併開立哪些幣別的錢包；不指定或指定空集合時，不建任何錢包，註冊仍應成功。
- 新建錢包的可用餘額與凍結餘額一律為 0。註冊不等於入金，不得藉註冊白送任何餘額。
- 指定重複的幣別應被安靜地正規化（去重），不得因此失敗。
- 指定不存在的幣別時，整筆註冊必須全部撤回（原子性）：要嘛全部成功、要嘛什麼都沒發生，不得留下「有帳號、沒錢包」且無法重試的殘缺用戶。

**雙因素驗證（一次性驗證碼）**

- 系統為每位用戶保管一組一次性驗證碼密鑰，並以加密方式儲存,不與資料庫明文並存。
- 用戶須先啟用雙因素驗證,方可登入。
- 密鑰須以加密方式儲存：若儲存資料外洩,攻擊者不得藉明文密鑰自行產生任何用戶的驗證碼。

**登入**

- 用戶以帳號、密碼加上當下的一次性驗證碼登入；三者皆通過才算登入成功。
- 未啟用雙因素驗證者不准登入。
- 登入後可換發新的通行憑證以延續工作階段。

**錢包餘額查詢與資金操作**

- 用戶可查詢自己所有錢包的餘額。
- 入金（憑空增加餘額）僅限管理員操作。真實交易所中用戶是在鏈上自行轉帳、由交易所偵測到帳後入帳；範圍一沒有鏈上依據,故此能力不對一般用戶開放,此入金端點是鏈上監聽器的替身,範圍二會被取代並移除。
- 出金由用戶對自己的錢包發起。

**擁有權（只能操作自己的資料）**

- 一般用戶僅能查詢與操作屬於自己的錢包與訂單;取消訂單只能取消自己的。
- 具管理職權者可檢視全部資料。
- 未登入者一律拒絕。

### 5. 驗收條件

- 能建立用戶,並為用戶開立各幣別的錢包。
- 能查詢某用戶的所有錢包餘額。
- 建立一個用戶並給予 100000 單位某幣的可用餘額後,查詢應得到該幣可用餘額為 100000、凍結餘額為 0。
- 每位用戶、每一種幣至多只有一個錢包。
- 未啟用雙因素驗證者無法登入;帳密正確但驗證碼錯誤者無法登入。
- 一般用戶查詢錢包時只看得到自己的錢包。

## 二、開發方規格

> 對應 app：`member`　主要 model：`UserProfileModel`、`WalletModel`

### 1. 資料模型

#### UserProfileModel（繼承 BaseTimeModel）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | OneToOne → User | 對應的 Django 用戶（`related_name='profile'`） |
| `phone_number` | CharField(20) | 電話 |
| `address` | CharField(255) | 住址（與鏈上地址無關） |
| `two_factor_enabled` | BooleanField | 是否已啟用 2FA |
| `encrypted_totp_secret` | BinaryField | Fernet 加密後的 TOTP 密鑰 |

TOTP 密鑰以 `Fernet` 對稱加密儲存,金鑰（`FERNET_KEY`）放設定／環境變數,不與 DB 存在一起。相關方法：`decrypt_totp_secret()`、`get_secret_qrcode_link()`、`get_current_totp()`。

#### WalletModel（繼承 BaseTimeModel）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `user` | FK → User | 擁有者 |
| `asset_type` | FK → CurrencyModel | 哪種幣 |
| `available_balance` | Decimal(20,2) | 可用餘額 |
| `frozen_balance` | Decimal(20,2) | 凍結餘額 |

約束：`unique_together = (user, asset_type)` — 每人每幣只能有一個錢包,為 DB 層防線,避免同一人同一幣出現兩個錢包導致餘額分裂。

### 2. API

`member/urls.py` 掛在 `/api/user/...` 底下。全域預設 `IsAuthenticated` + `JWTAuthentication`（見 `REST_FRAMEWORK` 設定）,個別 view 以 `permission_classes` 覆寫。免登入端點以粗體標示。

| 端點 | 方法 | 說明 | 權限 |
|---|---|---|---|
| **`/api/user/register/`** | POST | 註冊：建 User + Profile + TOTP 密鑰 + 初始錢包,回 `{username, secret, issuer, qrcode_link}` | AllowAny |
| **`/api/user/register/`** | PUT | 啟用 2FA,body `{username, totp}` | AllowAny |
| **`/api/user/login/`** | POST | 登入,body `{username, password, totp}` → JWT `{access, refresh}` | AllowAny |
| **`/api/user/token/refresh/`** | POST | 換新 access token | AllowAny |
| `/api/user/user/` | GET | 用戶列表／單筆 | IsAdminUser |
| `/api/user/wallet/` | GET/POST | 錢包（`get_queryset` 過濾 `request.user`;staff 看全部） | IsAuthenticated |
| `/api/user/wallet/deposit/` | POST | 入金（admin 專用）,body `{user_id, asset_type_id, quantity}` | IsAdminUser |
| `/api/user/wallet/withdraw/` | POST | 出金,body `{asset_type_id, quantity}` | IsAuthenticated |

註冊獨立成 `RegisterView`（自帶 `authentication_classes = []` / `permission_classes = []`）,而非放進 `UserViewSet`：因全域地板為 `IsAuthenticated` 且 `UserViewSet` 鎖為 admin-only,將免登入的註冊與 admin-only 的用戶管理混在同一類別會使其同時承擔兩種相反的權限語意。

### 3. 註冊時建立初始錢包

註冊 body 可選帶 `wallet_currency_ids`（一組 `CurrencyModel.id`）:

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

- 選填:不帶或帶空陣列 → 不建任何錢包,註冊仍成功。
- 新錢包 `available_balance = frozen_balance = 0`。
- 重複 id 須去重（`unique_together = (user, asset_type)` 為 DB 層防線,前端重送同一 id 應被安靜正規化,不得回 500）。
- 非法幣別 id → 400,且整筆註冊回滾（見下一節）。
- 回應格式固定為 `{username, secret, issuer, qrcode_link}`。錢包是否建立,以 `GET /api/user/wallet/` 查詢。

幣別以 id（而非寫死 code）指定,與 `WithdrawSerializer` / `DepositSerializer` 的 `asset_type_id` 風格一致,使註冊邏輯與「目前上架哪些幣」解耦。

### 4. 註冊的原子性

`RegisterView.create` 包 `@transaction.atomic`:要嘛全部成功,要嘛什麼都沒發生。避免出現「User／Profile 已建、建錢包時因非法幣別 id 失敗」而留下無法重試的殘缺用戶。此與 `05-1` 結算的「四個錢包要嘛一起動、要嘛都不動」為同一條原則。

### 5. 認證機制

- **JWT**（`djangorestframework-simplejwt`）:`JWTAuthentication` + 全域 `IsAuthenticated`。
- **註冊**:`RegisterView`（免登入）,建 User + Profile + TOTP 密鑰（+ 初始錢包,見上節）。
- **登入**:`LoginView` / `LoginSerializer`（subclass `TokenObtainPairSerializer`）,帳密通過後再驗 TOTP 才發 JWT。
- **強制 2FA**:未啟用 2FA 者不准登入。
- **擁有權**:錢包/訂單 `get_queryset` 過濾 `request.user`（staff 看全部）;cancel 綁 `user=request.user`。所有「誰在操作」一律綁 `request.user`。

### 6. 帳本流水套用點

`LedgerEntryModel` 與 `DepositWithdrawModel` 位於獨立的 `ledger` app（非 member）。`ledger` 只向下依賴 `currency`:`asset_type` FK 到 `CurrencyModel`（不 FK wallet）、`ref_type`/`ref_id` 用軟參照字串(不 FK Order/Transaction),以避免循環依賴。層次:`common ← currency ← ledger ← member ← transaction`。完整規格見 `07-1_logging_audit_spec.md`。

member 這邊須知:`transfer_to_frozen`(FREEZE)、`transfer_asset`(SETTLE)、`release_frozen`(UNFREEZE/REFUND)、`withdraw`(WITHDRAW)、`deposit`(DEPOSIT) 五個套用點都要在同一個 atomic 內補寫帳本分錄。

### 7. 自動建錢包

`transfer_asset`（收款腿）與 `deposit` 都用 `get_or_create`,用戶第一次碰到某幣別時自動建該幣錢包,不必預先建好所有幣的錢包。

`withdraw` 刻意相反:用 `.get()`,錢包不存在直接回 400。語意不同——收錢時沒錢包是正常的（該建）,出金時沒錢包代表根本沒有這種幣（該擋）。第 3 節「註冊時建初始錢包」與本節不衝突:前者是為讓新用戶錢包列表非空（UX）,`get_or_create` 則是不論有無預建都不會出錯的底層保險。

### 8. 精度（未做,範圍二阻擋項）

`decimal_places=2` 對 BTC 不足:1 satoshi（0.00000001 BTC）會被存成 0.00,直接弄丟用戶的錢。範圍一為模擬帳本、金額皆整數級,暫時無害,但這是跨全系統的 migration（錢包／訂單／成交／帳本全都要改）,必須在碰真錢之前完成。詳見 `01-2` §3。

### 9. 身份組與授權

授權看 Group／權限,不再看 `is_staff`。`Role`／`ROLES_PERMISSIONS`／`sync_roles` 放在 `member`（`constants.py`／`rbac.py`／`apps.py`）,因角色資料落在 member。四角色、read-gating、宣告式 `sync_roles`、職責分離、自訂 permission 邊界、各端點套用點等細節,一律以 `09-1_permission_spec.md` 為準。

`UserViewSet` 鎖為 `IsAdminUser` 屬角色層判斷(只問「是不是具管理職權」,不問「查的是不是自己」),不應在其中混入擁有權判斷。若要提供一般用戶查詢自己的資料,慣例解法是 `GET /api/user/user/me/`（`@action(detail=False)`,回 `request.user` 自己）,規劃與 KYC 狀態查詢一併設計。

### 10. 技術約束/注意事項

- **餘額更新的原子性**:「讀餘額→改→存」若兩個請求同時執行會互相覆蓋（race condition）導致超賣。所有改餘額處都須以 `select_for_update()` 上鎖。詳見 `04-1_matching_engine_spec.md` 併發章節。
- **可用與凍結成對變動**:扣可用必對應加凍結（或對外轉出）,兩邊一起改並包在同一個 `transaction.atomic()` 內,不得只改一半。
- **唯一性約束**:務必維持 `unique_together = (user, asset_type)`,否則同一人同一幣建出兩個錢包會使餘額分裂。
- **強制 2FA**:未啟用 2FA 者不得登入。
- **一次性驗證碼的時鐘容忍**:驗證 TOTP 時須容許用戶與伺服器之間合理的時鐘偏移。
- **擁有權過濾**:一般用戶的錢包/訂單查詢與操作一律以 `request.user` 過濾;staff 方可看全部。
