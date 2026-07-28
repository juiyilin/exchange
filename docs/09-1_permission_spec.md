# 細部規格 — 權限與身份組（RBAC／授權層）

> **跨模組主題：授權（authorization）**——回答「你能做什麼」。與**認證**（authentication，登入/JWT/2FA，見 `02-1` §6.1）是**兩件不同的事**，分層看。
> 影響範圍：`member`（角色定義 `Role`／`rbac.py`／`sync_roles`、User/Wallet/KYC 端點權限）、`ledger`（入金 `can_deposit`）、`transaction`（`OrderViewSet` 目前刻意不套，見 §7）。
> 上層文件：`00_overall_spec.md`。
>
> **狀態（2026-07）**：本檔由 `02-1` §6.5 獨立而來——RBAC 是**跨模組**主題（不只屬於 member），比照 KYC 獨立成 `08-1` 的判斷。**M-RBAC 已實作完成**：四角色 + read-gating + 宣告式 `sync_roles`，全套測試綠。契約見 `member/tests/test_rbac.py`。

## 1. 三層權限模型（認證／擁有權／角色）

認證解決「你是誰」，授權（權限）解決「你能做什麼」。全系統的權限是**三層正交**、要一起生效才完整，別混在一起：

- **認證層（M7 已做）**：JWT 認出 `request.user`，全域 `IsAuthenticated` 當地板（最低門檻：要登入）。
- **擁有權層（M7 已做）**：`get_queryset` 過濾 `request.user`、cancel 綁 `user=request.user`，保證「只能碰**自己的**資料」。這是「對哪一筆」的維度。
- **角色層 RBAC（本檔）**：依角色決定「能不能做**某一類**操作」。這是「能做哪種動作」的維度。

心智模型：擁有權層回答「這一筆是不是你的」，角色層回答「你這種人能不能做這件事」。例如——「你能不能看全站用戶」是角色層；「你只能取消自己的單」是擁有權層。

## 2. 四個角色與權限對照表

用 Django 內建 **Group（身份組）** 分出四個角色，取代舊的「`is_staff` 有/無」二分。**`Group.name` 存英文穩定碼**（`trader`/`support`/`compliance`/`admin`），**中文只是顯示標籤**；兩者都放在 `member.constants.Role`（`TextChoices`，value=英文碼、label=中文），程式一律以 `Role.XXX` 常數引用（見 §4，那裡解釋為何 name 不用中文）。角色語意（括號內為 `Role` 常數 = value「label」）：

- **交易者（`Role.TRADER` = `"trader"`「交易者」）**：一般交易用戶，**新註冊自動歸入**。只碰自己的訂單/錢包（靠擁有權層），沒有任何跨用戶或管理權。
- **客服（`Role.SUPPORT` = `"support"`「客服」）**：交易所內部人員，**唯讀**跨用戶資料（用戶、KYC、錢包）以協助客戶。**一個字都不能改**、不能審核、不能動錢。
- **合規（`Role.COMPLIANCE` = `"compliance"`「合規」）**：交易所內部人員，**專責 KYC 審核**（approve/reject/revoke/reverify），可唯讀用戶與 KYC。不能動錢、不能管理用戶。
- **管理員（`Role.ADMIN` = `"admin"`「管理員」）**：交易所內部人員，管**系統與金流**——跨用戶 CRUD、入金。**刻意不含 KYC 審核權**（見 §5 職責分離）。

每個角色持有的權限（codename 級別，這張表即契約，`sync_roles` 要照著指派；權限 codename 一律維持英文、不中文化）：

| 權限 codename | 說明 | 交易者 | 客服 | 合規 | 管理員 |
|---|---|:-:|:-:|:-:|:-:|
| `auth.view_user` | 查用戶 | | ✓ | ✓ | ✓ |
| `auth.add_user` / `change_user` / `delete_user` | 建/改/刪用戶 | | | | ✓ |
| `member.view_userprofilemodel` | 查 KYC 資料/紀錄 | | ✓ | ✓ | ✓ |
| `member.view_walletmodel` | 看全站錢包 | | ✓ | | ✓ |
| `member.review_kyc`（自訂）| 審核 KYC | | | ✓ | |
| `member.can_deposit`（自訂）| 入金 | | | | ✓ |

交易者（`Role.TRADER`）那列**全空**是刻意的：它的權力全在擁有權層（自己的訂單/錢包），角色層不給它任何跨用戶或管理能力。這個 group 的存在意義是「預設身分 + 日後 KYC-B 下單閘門的掛鉤」，不是靠它拿權限。

> `member.view_userprofilemodel`、`member.view_walletmodel` 是 Django 為 model 自動產生的權限；
> `member.review_kyc`、`member.can_deposit` 是**自訂權限**——因為「審核 KYC」「入金」不是標準 CRUD 動詞，
> 得在對應 model 的 `Meta.permissions` 自己宣告（`UserProfileModel` 宣告 `review_kyc`、
> `WalletModel` 宣告 `can_deposit`），再指派給 group。這類「非 CRUD 動作」怎麼把關，見 §7。

## 3. read-gating（讀取閘門）：擋讀只用在「連自己都不該看」的資源

> **名詞解釋 — read-gating（讀取閘門／讀取把關）**：指「把**讀取（GET）**這個動作也納入權限管制」的做法。
> 一般權限控制多半只管「寫入」——新增、修改、刪除要有權限，而**讀取預設放行**（登入就能看）。
> read-gating 就是在**讀取**這道門也裝上把關：**你得先有 `view_xxx` 權限，才讀得到**。
> 之所以要特別談它，是因為 DRF 的 `DjangoModelPermissions` **天生不擋讀**（見下），
> 想做出「客服能唯讀查全站、一般用戶查不到」就得自己把讀取這道閘門補上——但也**不是每個端點都該裝**（見後）。

一個關鍵坑：DRF 的 `DjangoModelPermissions` **預設不擋讀**——它的方法對應表只有
`POST→add`、`PUT/PATCH→change`、`DELETE→delete`，而 `GET/HEAD/OPTIONS` 對到**空清單**（讀取不檢查任何 model 權限，登入即可讀）。所以「客服唯讀查全站、交易者查不到」這種需求，**光掛 `DjangoModelPermissions` 做不到**。

要擋讀，得**自訂一個 `DjangoModelPermissions` 子類**，把 `view` 權限塞進 `GET`（連同 `HEAD`/`OPTIONS`）的對應。但——**不是每個端點都套 read-gating**，要分兩種資源：

- **純管理型資源**（用戶清單、跨用戶 KYC 查詢）：一般交易者 **根本沒資格讀** → 這裡才套 read-gating（GET 要 `view` 權限）。
- **擁有權型資源**（錢包、訂單）：交易者 **一定要讀得到自己的** → 這裡**不能**用 `view` 權限擋 GET（否則連他自己的錢包都擋掉）。owner 讀自己的靠 `get_queryset` 過濾；角色層在這裡只決定「能不能**額外**看到別人的」——把 `get_queryset` 裡舊的 `is_staff` 分支換成「有沒有 `view_xxx` 權限」。

一句話：**擋讀只用在「連自己都不該看」的資源上；擁有權型資源永遠讓 owner 讀自己的。**

## 4. 角色資料怎麼建：宣告式單一真相 + post_migrate 自動套用

角色與權限是「系統該有的初始資料」。有三種建法，比較後採**宣告式**：

| | data migration | 管理指令 | **post_migrate 宣告式（採用）** |
|---|---|---|---|
| 現狀可查（哪知道寫在哪） | 差，要疊加歷史 | 好，單一命名檔 | **好，單一 dict** |
| 自動套用（含測試 DB） | ✓ | ✗ 要手動跑 | **✓ migrate 自動** |
| 改角色成本 | 每次加 migration | 改檔重跑 | **改 dict，migrate 自動** |

migration 記的是「變更」不是「現狀」，時間久了要查「現在各角色有哪些權限」得把所有相關 migration 疊加著讀；管理指令可讀但**不會自己跑**——測試建臨時 DB 只跑 `migrate`、不跑自訂指令，group 會不存在。宣告式兩者兼得：

- **單一真相**：`member/rbac.py` 放一份 `ROLES_PERMISSIONS` dict（角色 → 權限 codename 清單，照 §2 那張表）。要看/要改永遠只來這一個檔；變更史看 `git log member/rbac.py`。
- **`sync_roles()`**：一個冪等函式，讀 `ROLES_PERMISSIONS`，`Group.objects.get_or_create` + `group.permissions.set([...])` 對齊 DB。
- **兩個觸發點，共用同一函式**：`AppConfig.ready()` 裡 `post_migrate.connect(sync_roles, sender=self)`（自動，顧測試/部署）；外加一個選配的 `sync_roles` 管理指令（手動，改完想立刻重套）。
- **`Role`（constants，`TextChoices`）**：與現有 `KycStatus`/`KycEvent` 同款，value=英文穩定碼、label=中文——`TRADER = "trader", "交易者"`、`SUPPORT = "support", "客服"`、`COMPLIANCE = "compliance", "合規"`、`ADMIN = "admin", "管理員"`。`Group.name` 存的是 **value（英文）**，要顯示中文用 `Role.X.label`。程式、`ROLES_PERMISSIONS` 的 key、各處查 group 一律用 `Role.XXX`（字串情境即英文碼）。**權限 codename（`review_kyc` 等）也維持英文，不中文化。**

> **為何 `Group.name` 用英文碼、不用中文**：Django 的 Group **沒有獨立主鍵，身分就是 `name`**；`sync_roles` 靠 `get_or_create(name=...)` 對齊。若把中文存進 `name`，哪天改中文名（改了 `Role` 的值再 re-sync），`get_or_create` 會建一個**新** group、舊的連同已加入的成員變孤兒——那不是「改名」，是「多一個 group」。把 `name` 固定成英文穩定碼、中文只當 `label`，改中文只動 label、永不碰 `name`，**天生沒有這個問題**（Django 後台 Groups 頁會顯示英文碼；要中文顯示可另加 Group proxy，本階段不做）。只有極少數要改**英文碼本身**時，才需要一筆 `Group.objects.filter(name=舊).update(name=新)` 的 data migration——但那是內部識別，幾乎不會動。

> **成員關係不寫進 `ROLES_PERMISSIONS`**：`ROLES_PERMISSIONS` 只定義「group 有哪些權限」，不定義「誰屬於哪個 group」。
> 新用戶自動進「交易者」是在 `RegisterView.create`（或 `RegisterSerializer.create`）裡 `user.groups.add(Group.objects.get(name=Role.TRADER))`；
> 客服/合規/管理員這種內部帳號由 admin 後台或 shell 手動指派。回歸網：`test_rbac.py::RegisterAutoJoinsTraderTest`。

## 5. 職責分離：審核與動錢分家（合規專屬，管理員不兜底）

管理員（`Role.ADMIN`）**刻意不含 `review_kyc`**。理由是內控原則**職責分離**：讓「放行帳號」（合規）與「動錢/管系統」（管理員）落在不同角色，**沒有任何單一角色能又核准一個帳號、又幫它入金**，壓縮監守自盜的空間。這是 KYC-B「四眼原則」的雛形（見 `08-1` §5 備忘）。

- **誠實標註（擋不住的例外）**：Django 的 **superuser 天生繞過所有權限檢查**（`has_perm` 對 superuser 永遠回 `True`），所以 superuser 帳號仍能審核。RBAC 擋的是「一般 staff 帳號」的越權，不是 superuser；正式營運要靠「superuser 帳號極少、嚴管」的作業紀律補上。測試 `test_rbac.py` 有一條把這個事實釘住（superuser 能審核 → 200）。
- 附帶小控制（本階段不做，KYC-B 再議）：staff 不得審自己的 KYC、雙人覆核。

## 6. 授權從 `is_staff` 搬到 Group/權限

`DjangoModelPermissions` 查的是 `has_perm`，而 `is_staff` **本身不給任何 model 權限**（只有 superuser 無條件全過）。所以要讓「管理員」角色在 `DjangoModelPermissions` 下算數，它必須是**帶著權限的 Group**，不能只靠 `is_staff`。本里程碑因此把授權判斷從「看 `is_staff`」改成「看 Group/權限」，`is_staff` 之後只剩一個用途——**能不能登入 Django admin 後台**（這裡的「admin 後台」指 Django 內建管理站，與「管理員」角色是兩回事）。

副作用：既有測試的語意隨之調整——`test_user_permissions` 的 admin 帳號改綁「管理員」群組、`test_kyc` 的審核者改綁「合規」群組、`ledger` 的入金測試（`test_deposit_withdraw`）入金者也改綁「管理員」群組。

## 7. 各端點套用點 + 自訂 permission vs `DjangoModelPermissions` 邊界

### 7.1 兩種把關工具，各司其職

- **標準 CRUD 端點**（list/retrieve/create/update/delete）→ 交給 `DjangoModelPermissions`（+ read-gating 子類）。它把 HTTP 方法對應到 `add/change/delete/view` 四個標準權限。
- **自訂動作**（`@action`，像審核、入金這種**不是 CRUD 動詞**的）→ 用**自訂 permission class** 直接查 `has_perm`。

**為什麼自訂動作不能靠 `DjangoModelPermissions`**：它心裡只有 `add/change/delete/view` 那張對應表，**沒有 `review_kyc`、`can_deposit` 的概念**。而且審核、入金都是 POST，硬套的話它會拿 `POST→add` 去查 `add_xxx`——查錯權限、語意也不對。所以「非 CRUD 動作」要另寫一個小 permission：

```python
from rest_framework.permissions import BasePermission

class HasModelPermission(BasePermission):
    required_perm = None
    def has_permission(self, request, view):
        return bool(request.user and request.user.has_perm(self.required_perm))

class CanReviewKyc(HasModelPermission):
    required_perm = "member.review_kyc"

class CanDeposit(HasModelPermission):
    required_perm = "member.can_deposit"
```

`has_perm("member.review_kyc")` 的運作：使用者透過 group 拿到權限就回 `True`（合規在 `ROLES_PERMISSIONS` 裡有 `review_kyc` → 過，其他人 403）；superuser 天生回 `True`；未登入時 `has_perm` 回 `False`，DRF 因有 JWT 認證器而回 401。

> 一句話：**`DjangoModelPermissions` 管「標準動詞」，自訂 permission 管「自訂動作」，兩者是不同東西在擋。**

### 7.2 各 ViewSet 對照

- **`UserViewSet`**（純管理型）：`permission_classes` 換成 **read-gating 子類**。→ 客服/合規/管理員 可讀（有 `view_user`）；管理員可寫（add/change/delete_user）；交易者讀寫皆 403；未登入 401。queryset 維持 `.all()`（用戶清單無「自己的」概念，不做擁有權過濾，由 read-gating 控制誰進得來）。
- **`KYCViewSet`**（`get_permissions` 依 action 分流）：`create`（送審）/`me` 維持 `IsAuthenticated`（本人）；`list`/`retrieve` 套 read-gating（要 `view_userprofilemodel`）；`approve`/`reject`/`revoke`/`reverify` 掛 `CanReviewKyc`（→ 合規專屬，管理員 403，superuser 例外）。
- **`WalletViewSet`**（擁有權型）：`get_queryset` 的「看全站」分支從 `is_staff` 改成 `has_perm('member.view_walletmodel')`（→ 客服/管理員 看全站，合規/交易者 看自己）；`deposit` 掛 `CanDeposit`（→ 管理員專屬）；`withdraw` 維持本人 + KYC 閘門不變；owner 讀自己錢包不加 read-gating。
- **`RegisterView`**：`create` 建完新用戶後 `user.groups.add(Role.TRADER 群組)`（自動歸交易者，同一 atomic）。回歸網：`test_rbac.py::RegisterAutoJoinsTraderTest`。
- **`OrderViewSet`（本里程碑不動）**：維持擁有權層 + `IsAuthenticated`。理由：每個註冊用戶都是交易者，「已登入」≈「是交易者」，對訂單再套角色層閘門收益低、卻要動到 25+ 條既有交易測試（那批用戶都得先取得權限）。下單的角色/分級閘門留給 KYC-B。其 `get_queryset` 的 `is_staff` 分支暫留（唯一殘留，已註記，不影響 RBAC 正確性）。

## 8. 常見坑

- **`has_perm` 有快取**：`user.has_perm(...)` 第一次呼叫會把權限**快取**在 user 物件上。測試裡若沿用 `force_authenticate` 重用的同一個 user，要在「加入 group」**之後**才發第一個請求，否則快取住空權限、角色測試會假紅。這與 KYC 送審守衛踩過的「反向 OneToOne 被快取」是同一類坑：**判斷用的狀態，要在改完之後才讀。**
- **`post_migrate` 建權限時序**：fresh DB 上你的 receiver 有可能比 Django 建權限那步先跑，`Permission.objects.get(...)` 撈不到會炸 → 在 `sync_roles` 開頭先對每個 app_config 呼叫一次 `create_permissions`（真的呼叫、放迴圈外），或用 `content_type__app_label + codename` 精準抓（codename 跨 app 會撞名）。本專案因 `auth` 在 `INSTALLED_APPS` 排在 `member` 前而「碰巧能過」，但別依賴這個順序。
- **註冊死鎖（已解決，記錄備查）**：全域 `IsAuthenticated` 下，若註冊端點沒豁免會死鎖——新用戶無法註冊 → 永遠沒帳號可登入。M7-B 把註冊拆成獨立 `RegisterView`（自帶 `authentication_classes = []`）後**天生免疫**。回歸網：`test_user_permissions.py::test_register_still_open`。
- **`UserViewSet` 為什麼非鎖不可**：它原本只吃全域 `IsAuthenticated`，等於**任何登入用戶都能撈出全站所有人的資料**（`UserListSerializer` 只 `exclude=["password"]`）。用戶名單外洩在真實交易所是重大事故（撞庫、釣魚的完整目標清單）。M-RBAC 把它收進 read-gating，只有客服/合規/管理員讀得到。
