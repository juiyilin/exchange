# 細部規格 — 權限與身份組（RBAC／授權層）

> **跨模組主題：授權（authorization）**——回答「你能做什麼」。與**認證**（authentication，登入/JWT/2FA，見 `02-1` §6.1）是兩件不同的事,分層看。
> 上層文件：`00_overall_spec.md`。相關主題：KYC 審核見 `08-1`。

## 一、需求方規格

### 1. 授權的兩個維度

系統對「你能不能做這件事」的判斷有兩個彼此獨立、要一起生效才完整的維度:

- **擁有權維度(對哪一筆)**:限定「只能操作**自己的**資源」。回答「這一筆是不是你的」。例如:一般用戶只能取消自己的訂單、只能查自己的錢包。
- **角色維度(能做哪種動作)**:依身分決定「能不能做**某一類**操作」。回答「你這種身分能不能做這件事」。例如:能不能檢視全站所有用戶、能不能審核 KYC、能不能入金。

兩者正交:「你能不能看全站用戶」屬角色維度;「你只能取消自己的單」屬擁有權維度。

### 2. 四個角色與職責

- **交易者**:一般交易用戶,新註冊者自動歸入此身分。只能操作自己的訂單與錢包(靠擁有權維度),不具任何跨用戶或管理能力。此身分的角色維度**完全不給任何跨用戶或管理權限**,它存在的意義是「預設身分」與日後下單分級閘門的掛鉤。
- **客服**:交易所內部人員。可**唯讀**檢視跨用戶資料(用戶、KYC 資料/紀錄、錢包)以協助客戶,但**不能修改任何資料、不能審核、不能動錢**。
- **合規**:交易所內部人員,**專責 KYC 審核**(核准/拒絕/撤銷/重新審核),並可唯讀檢視用戶與 KYC 資料。**不能動錢、不能管理用戶**。
- **管理員**:交易所內部人員,負責**系統與金流管理**——跨用戶的建立/修改/刪除、入金。**刻意不含 KYC 審核權**(見 §4 職責分離)。

### 3. 各角色能做什麼(業務動作對照)

| 業務動作 | 交易者 | 客服 | 合規 | 管理員 |
|---|:-:|:-:|:-:|:-:|
| 檢視全部用戶 | | ✓ | ✓ | ✓ |
| 建立/修改/刪除用戶 | | | | ✓ |
| 檢視 KYC 資料/紀錄 | | ✓ | ✓ | ✓ |
| 檢視全站錢包 | | ✓ | | ✓ |
| 審核 KYC | | | ✓ | |
| 入金 | | | | ✓ |

交易者那列全空是刻意的:其權力全在擁有權維度(操作自己的訂單/錢包),角色維度不賦予任何跨用戶或管理能力。

### 4. 職責分離:審核與動錢分家

管理員**刻意不含 KYC 審核權**。這是內控原則「職責分離」:讓「放行帳號」(合規)與「動錢/管系統」(管理員)落在不同角色,使**沒有任何單一角色能又核准一個帳號、又幫它入金**,壓縮監守自盜的空間。這是日後 KYC「四眼原則」的雛形(見 `08-1` §5)。

附帶控制(本階段不做,後續議):審核者不得審自己的 KYC、雙人覆核。

### 5. 哪些資源連自己都不該看(管理型 vs 擁有權型)

資源分兩類,決定「讀取」要不要受角色維度把關:

- **管理型資源**(用戶清單、跨用戶 KYC 查詢):一般交易者**根本沒資格檢視**。這類資源的讀取本身即需角色授權。
- **擁有權型資源**(錢包、訂單):使用者**一定要能檢視自己的**。這類資源的讀取不因角色而封鎖 owner 檢視自己;角色維度在此只決定「能不能**額外**看到別人的」。

原則:**限制讀取只用在「連自己都不該看」的管理型資源上;擁有權型資源永遠讓 owner 檢視自己的。**

## 二、開發方規格

> 影響範圍:`member`(角色定義、User/Wallet/KYC 端點權限)、`ledger`(入金 `can_deposit`)、`transaction`(`OrderViewSet` 目前刻意不套,見 §5)。契約見 `member/tests/test_rbac.py`。

### 1. 三層權限模型(認證/擁有權/角色)

需求方規格的兩個維度,在實作上與認證合為**三層正交**,要一起生效:

- **認證層**:JWT 認出 `request.user`,全域 `IsAuthenticated` 當地板(最低門檻:要登入)。
- **擁有權層**:`get_queryset` 過濾 `request.user`、cancel 綁 `user=request.user`,保證只能碰自己的資料(對應需求方「擁有權維度」)。
- **角色層 RBAC(本檔)**:依角色決定能否執行某一類操作(對應需求方「角色維度」)。

### 2. 以 Django Group 實作角色

用 Django 內建 **Group** 分出四個角色,取代舊的「`is_staff` 有/無」二分。

- **`Group.name` 存英文穩定碼**(`trader`/`support`/`compliance`/`admin`),中文只作顯示標籤。兩者放在 `member.constants.Role`(`TextChoices`,value=英文碼、label=中文):`TRADER = "trader", "交易者"`、`SUPPORT = "support", "客服"`、`COMPLIANCE = "compliance", "合規"`、`ADMIN = "admin", "管理員"`。程式、group 查詢、`ROLES_PERMISSIONS` 的 key 一律以 `Role.XXX` 引用;要顯示中文用 `Role.X.label`。
- **權限 codename 維持英文,不中文化。**

各角色持有的權限(codename 級別,此表即契約,`sync_roles` 照此指派):

| 權限 codename | 對應業務動作 | 交易者 | 客服 | 合規 | 管理員 |
|---|---|:-:|:-:|:-:|:-:|
| `auth.view_user` | 查用戶 | | ✓ | ✓ | ✓ |
| `auth.add_user` / `change_user` / `delete_user` | 建/改/刪用戶 | | | | ✓ |
| `member.view_userprofilemodel` | 查 KYC 資料/紀錄 | | ✓ | ✓ | ✓ |
| `member.view_walletmodel` | 看全站錢包 | | ✓ | | ✓ |
| `member.review_kyc`(自訂) | 審核 KYC | | | ✓ | |
| `member.can_deposit`(自訂) | 入金 | | | | ✓ |

`member.view_userprofilemodel`、`member.view_walletmodel` 是 Django 為 model 自動產生的權限;`member.review_kyc`、`member.can_deposit` 是**自訂權限**——因「審核 KYC」「入金」不是標準 CRUD 動詞,須在對應 model 的 `Meta.permissions` 宣告(`UserProfileModel` 宣告 `review_kyc`、`WalletModel` 宣告 `can_deposit`),再指派給 group。

> **為何 `Group.name` 用英文碼、不用中文**:Django 的 Group 沒有獨立主鍵,身分即 `name`;`sync_roles` 靠 `get_or_create(name=...)` 對齊。若把中文存進 `name`,改中文名後 `get_or_create` 會建一個新 group、舊的連同成員變孤兒。把 `name` 固定成英文穩定碼、中文只當 `label`,改中文只動 label、永不碰 `name`。僅極少數要改英文碼本身時,才需一筆 `Group.objects.filter(name=舊).update(name=新)` 的 data migration。

### 3. read-gating(讀取閘門)

**read-gating** 指把讀取(GET)也納入權限管制:須先有 `view_xxx` 權限才讀得到。之所以要特別處理,是因為 DRF 的 `DjangoModelPermissions` **預設不擋讀**——其方法對應表只有 `POST→add`、`PUT/PATCH→change`、`DELETE→delete`,而 `GET/HEAD/OPTIONS` 對到空清單(讀取不檢查任何 model 權限)。

要擋讀,須**自訂一個 `DjangoModelPermissions` 子類**,把 `view` 權限塞進 `GET`(連同 `HEAD`/`OPTIONS`)的對應。套用範圍對應需求方 §5 的資源分類:

- **管理型資源**(用戶清單、跨用戶 KYC 查詢):套 read-gating(GET 要 `view` 權限)。
- **擁有權型資源**(錢包、訂單):**不套** read-gating——否則連 owner 自己的資源都被擋掉。owner 讀自己的靠 `get_queryset` 過濾;角色層在此只決定能否額外看到別人的,做法是把 `get_queryset` 裡舊的 `is_staff` 分支換成「有沒有 `view_xxx` 權限」。

### 4. 角色資料建置:宣告式單一真相 + post_migrate 自動套用

角色與權限屬「系統初始資料」,採**宣告式**建法,以取得「單一真相」與「測試 DB 也自動套用」兩者:

- **單一真相**:`member/rbac.py` 放一份 `ROLES_PERMISSIONS` dict(角色 → 權限 codename 清單,照 §2 那張表)。變更史看 `git log member/rbac.py`。
- **`sync_roles()`**:一個冪等函式,讀 `ROLES_PERMISSIONS`,以 `Group.objects.get_or_create` + `group.permissions.set([...])` 對齊 DB。
- **兩個觸發點共用同一函式**:`AppConfig.ready()` 裡 `post_migrate.connect(sync_roles, sender=self)`(自動,顧測試/部署);外加一個選配的 `sync_roles` 管理指令(手動,改完想立刻重套)。
- **成員關係不寫進 `ROLES_PERMISSIONS`**:該 dict 只定義「group 有哪些權限」,不定義「誰屬於哪個 group」。新用戶自動進交易者是在註冊流程裡 `user.groups.add(Role.TRADER 群組)`;內部帳號由 admin 後台或 shell 手動指派。契約:`test_rbac.py::RegisterAutoJoinsTraderTest`。

> 相較於 data migration(記的是「變更」不是「現狀」,查現狀須疊加歷史)與管理指令(測試建臨時 DB 只跑 `migrate`、不跑自訂指令,group 會不存在),宣告式兼得可查與自動套用。

### 5. 各端點套用點 + 自訂 permission vs `DjangoModelPermissions` 邊界

#### 5.1 兩種把關工具

- **標準 CRUD 端點**(list/retrieve/create/update/delete)→ 交給 `DjangoModelPermissions`(+ read-gating 子類)。它把 HTTP 方法對應到 `add/change/delete/view` 四個標準權限。
- **自訂動作**(`@action`,像審核、入金這種非 CRUD 動詞)→ 用**自訂 permission class** 直接查 `has_perm`。

自訂動作不能靠 `DjangoModelPermissions`:它只認得 `add/change/delete/view`,沒有 `review_kyc`、`can_deposit` 的概念;且審核、入金都是 POST,硬套會拿 `POST→add` 查錯權限。故非 CRUD 動作另寫小 permission:

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

`has_perm("member.review_kyc")` 的運作:使用者透過 group 拿到權限即回 `True`(合規在 `ROLES_PERMISSIONS` 裡有 `review_kyc`,其他人 403);superuser 天生回 `True`;未登入時 `has_perm` 回 `False`,DRF 因有 JWT 認證器而回 401。

#### 5.2 各 ViewSet 對照

- **`UserViewSet`**(管理型):`permission_classes` 換成 read-gating 子類。→ 客服/合規/管理員可讀(有 `view_user`);管理員可寫;交易者讀寫皆 403;未登入 401。queryset 維持 `.all()`,由 read-gating 控制誰進得來。
- **`KYCViewSet`**(`get_permissions` 依 action 分流):`create`(送審)/`me` 維持 `IsAuthenticated`(本人);`list`/`retrieve` 套 read-gating(要 `view_userprofilemodel`);`approve`/`reject`/`revoke`/`reverify` 掛 `CanReviewKyc`(合規專屬,管理員 403,superuser 例外)。
- **`WalletViewSet`**(擁有權型):`get_queryset` 的「看全站」分支從 `is_staff` 改成 `has_perm('member.view_walletmodel')`(客服/管理員看全站,合規/交易者看自己);`deposit` 掛 `CanDeposit`(管理員專屬);`withdraw` 維持本人 + KYC 閘門不變;owner 讀自己錢包不加 read-gating。
- **`RegisterView`**:`create` 建完新用戶後 `user.groups.add(Role.TRADER 群組)`(自動歸交易者,同一 atomic)。契約:`test_rbac.py::RegisterAutoJoinsTraderTest`。`RegisterView` 自帶 `authentication_classes = []`,使註冊端點在全域 `IsAuthenticated` 下仍開放。回歸網:`test_user_permissions.py::test_register_still_open`。
- **`OrderViewSet`(本階段不套角色層)**:維持擁有權層 + `IsAuthenticated`。理由:每個註冊用戶都是交易者,對訂單再套角色層閘門收益低。下單的角色/分級閘門留給後續 KYC 分級。其 `get_queryset` 的 `is_staff` 分支暫留。

### 6. 授權判斷從 `is_staff` 搬到 Group/權限

`DjangoModelPermissions` 查的是 `has_perm`,而 `is_staff` 本身不給任何 model 權限(只有 superuser 無條件全過)。故「管理員」角色必須是帶著權限的 Group,不能只靠 `is_staff`。授權判斷一律改成看 Group/權限;`is_staff` 只保留一個用途——能否登入 Django admin 後台(與「管理員」角色是兩回事)。

對應調整:admin 帳號改綁「管理員」群組、KYC 審核者改綁「合規」群組、入金者改綁「管理員」群組(見 `test_user_permissions`、`test_kyc`、`ledger` 的 `test_deposit_withdraw`)。

### 7. 技術約束/注意事項

- **`has_perm` 有快取**:`user.has_perm(...)` 第一次呼叫會把權限快取在 user 物件上。測試若沿用同一個 user,須在「加入 group」之後才發第一個請求,否則快取住空權限。判斷用的狀態,要在改完之後才讀。
- **`post_migrate` 建權限時序**:fresh DB 上 receiver 可能比 Django 建權限那步先跑,`Permission.objects.get(...)` 撈不到會拋錯。應在 `sync_roles` 開頭先對每個 app_config 呼叫一次 `create_permissions`(放迴圈外),或用 `content_type__app_label + codename` 精準抓(codename 跨 app 會撞名)。不要依賴 `INSTALLED_APPS` 的排序碰巧成立。
- **superuser 繞過權限檢查**:Django 的 superuser 天生繞過所有權限檢查(`has_perm` 對 superuser 永遠回 `True`),故 superuser 仍能審核 KYC。RBAC 擋的是一般 staff 帳號的越權,不是 superuser;正式營運須靠「superuser 帳號極少、嚴管」的作業紀律補上。`test_rbac.py` 有一條釘住此事實(superuser 能審核 → 200)。
- **`UserViewSet` 必須鎖**:若只吃全域 `IsAuthenticated`,任何登入用戶都能撈出全站所有人的資料(用戶名單外洩在真實交易所是重大事故)。故收進 read-gating,只有客服/合規/管理員讀得到。
- **職責分離邊界**:管理員不得持有 `review_kyc`;審核與動錢須分屬不同角色(見 §一.4)。
- **自訂 permission 邊界**:自訂 permission 只查 `has_perm`,不處理擁有權過濾;擁有權仍由 `get_queryset` 與 action 內的 `user=request.user` 綁定負責。
