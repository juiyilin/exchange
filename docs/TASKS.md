# 可接手任務清單（TASKS）

> 這份是「進度書籤」。下次開新對話時，把這份貼給我（或叫我讀 `docs/TASKS.md`），我就能直接從中途接手。
> 規則：引導你做、不直接幫你寫程式碼。每完成一項就把 `[ ]` 改成 `[x]`，並更新「目前進度」。

## 給接手的 AI 的提示（重要）

- 專案是 Django 5.2 + DRF + Celery + Redis + PostgreSQL 的中心化交易所後端。
- 範圍：**純內部帳本（模擬）**，文件已預留通往「測試鏈入金/出金」的路。
- App：`common`（基底）、`currency`（幣別）、`member`（用戶/錢包）、`transaction`（訂單/撮合/成交）、`ledger`（帳本/出入金紀錄）。
- 完整設計在 `docs/00_overall_spec.md` 及各細部規格。**接手前先讀對應的規格文件。**
- **規格檔名 `NN-S_模組_spec.md`：`-1` = 範圍一（純內部帳本，不碰鏈，目前在做）、`-2` = 範圍二（接測試鏈，設計完成、未實作）。**
  `03`/`04`/`05`（訂單/撮合/結算）**只有 `-1`**——它們在範圍二完全不變，因為核心模組不知道「鏈」的存在。
- 引導風格：解釋概念、給驗證標準、指出該改哪個檔案/該注意什麼坑，**但不替使用者寫實作程式碼**。
- 使用者背景：對區塊鏈/交易所不熟，跟著 Gemini 教學起步，需要由淺入深。
- 語言：繁體中文。

---

## 目前進度（最後更新：**M-日誌與帳本 + M6 收尾 全部完成，全套 81 測試綠**。技術債已清空。下一步建議：M-KYC（順帶補「註冊時建初始錢包」），再來 M-RBAC）

**節奏原則：先把「基本」功能全部做完，再進入「進階」。**（基本/進階的分類見 `00_overall_spec.md` 第 5 節功能總表）

已完成：

- M1 幣別與錢包、M2 下單與凍結、M3 撮合與結算（同步）全部完成並有測試。
- `OrderViewSet.create` 已串接 `match_order(order.id)`，下單即撮合（端到端）。
- 重構：TradingPairModel（base/quote）、欄位 quantity、狀態 FULLY_FILLED、Transaction 用 buy_order/sell_order。
- 結算 `WalletModel.objects.transfer_asset`（F() + 收款錢包 get_or_create）。
- 測試在 `transaction/test/`（test_matching、test_orders、test_order_create_matching）。

**✅ v0.1「基本」+ M4 生命週期 + M5 非同步化 + STP + M7 認證 + M6 併發安全皆完成。下一步建議 M-RBAC、M-KYC、或 M-日誌與帳本。**

已完成（M6）：交易對序列化鎖（擋 deadlock）、冪等撮合（擋重複投遞超賣）、`release_frozen` 改 F()、錢包 CheckConstraint、併發壓力測試。
已完成（M6 收尾）：cancel 也走交易對閘門，鎖順序全系統統一為 `pair → order → wallet`。

**技術債：目前已清空。**（M7 的 `get_random_user()` 已移除、改綁 `request.user`；M6 的 cancel 鎖已補。）

已完成（M4）：取消訂單、多凍結退款、終態不可變、擋改單（PUT/PATCH→405）。
已完成（M5）：下單 `.delay()` 非同步撮合、Redis+worker、commit 後送任務、測試 eager。
已完成（補強）：自我成交防護 STP。

---

## M1 — 幣別與錢包就緒　〔規格：01-1, 02-1〕

- [x] admin 後台註冊 `CurrencyModel`（已完成）
- [x] admin 後台註冊 `UserProfileModel`、`WalletModel`（已完成，還加了 UserProfileInline）
- [x] 建立幣別 USDT、BTC（已完成，CurrencyModel 還加了 **str** 與 save() 自動大寫）
- [x] 建立測試用戶（root / user），併建 USDT / BTC 錢包（4 個錢包齊全）
- [x] 模擬入金：USDT/BTC 錢包都有初始可用餘額
- [x] （選做）幣別查詢 API（`/api/currency/` 已掛 URL）
- [x] 驗證：API/admin 查得到餘額

## M2 — 下單與凍結　〔規格：03-1〕

- [x] 確認下單 API 能建單且正確凍結餘額（買單凍 USDT=quantity×price、賣單凍 BTC=quantity，雙向驗證通過）
- [x] 確認餘額不足會被擋下並回 400（已驗證：回 400「用戶可用餘額不足」）
- [x] 補訂單查詢 API：列表 ✅、單筆 ✅（待成交量尚未在 serializer 暴露，見下方備註）
- [x] 驗證凍結：買 1 BTC@30000 → USDT 可用 −30000、凍結 +30000（已通過）
- [x] 驗證超額：下超額單回 400 且餘額完全不變（已驗證，root USDT 餘額前後一致）

> 備註：測試期間 `get_random_user_id()` 暫時固定回傳 1（root），方便驗證；正解在 M7 改認證。
> 備註：訂單查詢若要顯示「待成交量」，需在 OrderSerializer 加一個 SerializerMethodField 對應 `waiting_transaction_quantity()`（目前 fields="**all**" 不含 model 方法）。
> 備註：OrderModel 未在 admin 註冊，訂單只能用 API 查；若想在後台看，之後可補 transaction/admin.py。

## M3 — 撮合與結算（同步版）★核心★　〔規格：04-1, 05-1〕　✅ 完成（13 測試全綠）

- [x] 把撮合寫成獨立函式（`transaction/tasks.py` 的 `match_order(order_id)`，同步）
- [x] 實作價格時間優先的對手查詢（`OrderQuerySet.get_waiting_match_orders`，排序 price → ordered_at → id）
- [x] 逐筆配對、算成交量(min)、建 `TransactionModel`、更新訂單狀態
- [x] 實作結算函式：原子搬動四個錢包餘額（`WalletModel.objects.transfer_asset`，用 F() + get_or_create 收款錢包）
- [x] 處理部分成交（PARTIALLY_FILLED）
- [x] 驗證：同價完全成交、部分成交雙方向、價格/時間優先、maker 定價、四錢包餘額（test/test_matching.py 9 條）
- [x] 修正 `executed_transaction_quantity`（已改用 `OrderType` 與 buy_transactions/sell_transactions）

> 結構變更紀錄（重要，給接手者）：已重構為 TradingPairModel（base/quote），訂單改用 trading_pair，
> 數量欄位 amount→quantity，狀態 FILLED→FULLY_FILLED，Transaction 用 buy_order/sell_order。
> 測試在 transaction/test/ 底下（test_matching.py、test_orders.py）。
> 已知簡化：買單低於掛價成交的「多凍結退款」尚未做（M4）；撮合仍同步（M5 改 Celery）。

## M-基本收尾 — 模擬出金　〔規格：06-1〕　✅ 完成

- [x] `POST /api/user/wallet/withdraw/`：`WalletViewSet` 的 `@action(detail=False, methods=['post'])`
- [x] body `{asset_type_id, quantity}`；用戶 `get_random_user()`；全程 `@transaction.atomic`
- [x] `select_for_update` 鎖錢包；錢包不存在 / quantity≤0 / 可用不足 各回 400（餘額不變）
- [x] 通過 → `available_balance -= quantity`，回 200。只動 available、不碰 frozen；Decimal
- [x] 不寫紀錄/log（延到後續/範圍 2）
- [x] 驗證：`member/test/test_withdraw.py`（出金成功、領光、超額擋、凍結不可領、≤0 擋、無錢包擋）

> ✅ v0.1 的「基本」功能全部完成（M1 幣別錢包、M2 下單凍結、M3 撮合結算、模擬入金/出金）。
> 測試現都在各 app 的 `test/` 資料夾下。接下來進入「進階」階段（M4 以後）。

---

# ===== 以下為「進階」階段（基本全部完成後才做）=====

## M4 — 訂單生命週期【進階】　〔規格：03-1, 05-1〕　✅ 完成（測試全綠，25 條）

- [x] 取消訂單：解凍剩餘凍結餘額、狀態改 CANCELED（POST /order/{id}/cancel/，@action detail=True）
- [x] 多凍結退款（買單終態時退「原凍結 − 實際花費」差額；與取消共用 release_frozen）
- [x] 訂單狀態機完整、終態不可再變（cancel 內 select_for_update 鎖訂單 + 重檢狀態擋終態）
- [x] 堵掉改單：OrderViewSet 設 `http_method_names = ['get', 'post']`，PUT/PATCH 回 405
- [x] 決定：保留 ordered_at 欄位（不收斂到 created_at）
- [x] 驗證：member/transaction 測試全綠（取消全退/只退剩餘、多凍退款、終態擋取消、防重複退、擋改單）

> 實作重點（給接手者）：
>
> - `release_frozen(order)` 在 `member/models.py` WalletQuerySet，公式：買 `quantity*price − Σ(t.qty*t.price)`、賣 `quantity − Σ(t.qty)`，只在 order 進終態(FULLY_FILLED/CANCELED)時把差額 frozen→available。
> - 心智模型：**任何訂單一進終態就 release_frozen，不分 maker/taker**。撮合 tasks.py 裡 maker 與 taker 各在 `mark_*_status` 之後呼叫一次。
> - 踩過的坑：`release_frozen(maker)` 必須擺在 `mark_maker_status` 之後，否則 maker 狀態還沒變終態 → no-op，「先 taker 部分成交、後當 maker 成交完」的多凍會卡住不退（已加迴歸測試 OverFreezeTakerThenMakerTest）。
> - 新測試檔：`transaction/test/test_cancel_refund.py`（9 條）。
> - 併發互斥僅做到 cancel 的 select_for_update + 重檢；完整序列化留 M6。

## M5 — 非同步化　〔規格：04-1〕　✅ 完成

- [x] 啟動 Redis（Docker：`docker run -d --name exchange-redis -p 6373:6379 redis:7`）+ Celery worker（`uv run celery -A exchange worker -l info`）
- [x] 下單改成 `send_to_match_market.delay()`，API 秒回
- [x] 撮合在 Celery worker 跑（worker log 可見 `send_to_match_market` received/succeeded）
- [x] 驗證：下單立即回應、背景完成撮合、四錢包正確結算

> 實作重點（給接手者）：
>
> - `exchange/__init__.py` 要有 `from .celery import app as celery_app`，否則 web 進程 `.delay()` 找不到 app、task 也不註冊。
> - `send_to_match_market(order_id)` 是 `@shared_task` 薄包裝，內部呼叫純函式 `match_order(order_id)`；**`match_order` 維持純函式**，單元測試直接呼叫它、不碰 Celery。
> - **Celery + DB 競態**：`.delay()` 是「立刻送訊息到 broker」，與 DB commit 無關。若在 `transaction.atomic()` 內送，worker 可能在 web 交易 commit 前就撈任務 → `get(order_id)` 抓不到。解法：把 `.delay()` 放在 `with transaction.atomic():` 區塊**之外**（commit 後才送）;此寫法只在「該 atomic 是最外層」時等價於 commit 後（ATOMIC_REQUESTS 目前關閉、create 未被巢狀，成立）。更防呆的寫法是 `transaction.on_commit(...)`。
> - 注意 `with` 要包住 `serializer.is_valid()`，因為 serializer 內用 `select_for_update()` 鎖錢包查餘額，必須在交易內。
> - 測試：凡是會 POST 下單（觸發 `.delay()`）的測試 class 要掛 `@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)`，讓 task 在進程內同步跑、不漏送到真 broker（已套：test_orders、test_order_create_matching）。**正式環境絕不可開 eager。**
> - 坑：`test_orders.py`（M2 寫的）改非同步後會漏送 task，因為當時沒有 eager override;已補上。

## M-撮合補強 — 自我成交防護（STP）　✅ 完成

- [x] `get_waiting_match_orders` 加 `.exclude(user=order.user)`，taker 不配到自己的單
- [x] 驗證：同一人掛賣+買且價格交叉 → 不成交、兩單留 PENDING、凍結不動（test_matching.py `SelfTradePreventionTest`）

> 為什麼：自我成交（wash trading）會製造假成交量/假行情，是被禁止的市場操縱，正規交易所一律在撮合層擋掉。本專案採最單純策略：跳過自己的單、留在簿上。

## M6 — 併發安全　〔規格：04-1, 02-1〕　✅ 完成（3 支併發測試綠）

- [x] 撮合對訂單/錢包加 `select_for_update`（taker、maker 查詢已鎖；`release_frozen` 改 F() 相對運算免掉更新）
- [x] 同一交易對撮合序列化：`match_order` 開頭先 `select_for_update` 鎖 `TradingPairModel` 當閘門
- [x] 餘額不變量：`WalletModel` 兩條 `CheckConstraint`（available/frozen >= 0），DB 層擋負值
- [x] 併發壓力測試：`transaction/test/test_concurrency.py`（no-oversell、cross-fire 雙向、match/cancel 退款 race）

> 實作重點 / 踩過的坑（給接手者）：
>
> - **deadlock 是真的**：只鎖訂單時，買賣兩邊同時撮合會「先鎖自己的單、再搶對手單」互鎖成環，PostgreSQL 報 `deadlock detected`。解法是**進撮合前先鎖交易對列**（序列化閘門），且**必須在鎖任何訂單之前**，順序反了就無效。不同交易對仍平行。
> - **冪等性**：`match_order` 要能被重複呼叫不出錯（Celery at-least-once 會重送；一張單可能在自己任務跑前就被別張當 maker 吃掉成終態）。作法：`.get(id=..., status__in=[PENDING, PARTIALLY_FILLED])` 撈不到即 return；`taker_remaining` 用 `waiting_transaction_quantity()` 不用 `quantity`。
> - **`release_frozen` 從讀改寫改成 F()**：原本 `wallet.x -= n; wallet.save()` 在併發退款（撮合與 cancel 同時碰同一錢包）會掉更新；`F()` 是 DB 端相對運算，免鎖也不掉。`transfer_asset` 本來就用 F()，安全。
> - **CheckConstraint 立大功**：cross-fire 把「重複撮合超賣」逼出來時，是 `frozen_non_negative` 在 DB 層當場攔下（凍結變 -1），沒讓髒資料寫進去。三層（鎖／原子性／不變量）協作的範例。
> - **測試限制**：併發測試需 `TransactionTestCase` + **PostgreSQL**（SQLite 測不到列鎖）；每執行緒自帶連線、結束 `connection.close()`；用 `Barrier` 對齊起跑、收集 thread 例外。建議加 `@skipUnless(connection.vendor=='postgresql')` 免得 SQLite 假綠。
> - 引擎是**逐筆連續撮合**（非集合競價）；Celery 只是搬到背景跑，仍一張一張即時撮。
> - ~~待加固（選做）：cancel 目前未取交易對鎖~~ → **已排入「M6 收尾」，見下方區塊。**

## M6 收尾 — cancel 的交易對鎖【進階】　〔規格：04-1 §6.1〕　✅ 完成（全套 81 測試綠）

**目標**：把 M6 留下的最後一個坑補掉——讓 cancel 與 match 遵守**同一個鎖順序**，
從架構上消滅繞環的可能。順便修掉一個測試品質問題。

- [x] `04-1` §6.1 新增**鎖順序鐵則**：`TradingPair（閘門） → Order → Wallet`，所有訂單簿寫入者都要遵守;
      §6.2 補述「錢包為何不靠鎖而靠 `F()`」（錢包不受 pair 閘門保護——跨交易對、出入金都會碰它）
- [x] 測試 `transaction/test/test_concurrency.py`：改用真正的 `cancel_order`（刪掉原本複製 view 邏輯的
      `_cancel_order`）、新增 `test_cancel_match_stress_no_deadlock`、新增 `CancelOrderContractTest`
- [x] `transaction/exceptions.py`：`OrderNotCancelable(order)`（純 `Exception`，帶著訂單、訊息在業務層產生）
- [x] `transaction/tasks.py`：抽出 `cancel_order(order_id, user)`，鎖順序 **pair → order → wallet**
- [x] `OrderViewSet.cancel` 改成薄殼：呼叫 `cancel_order`，`DoesNotExist` 與 `OrderNotCancelable` 都轉 400

> **實作重點 / 踩過的坑（給接手者）**：
>
> - **鎖 pair 的先有雞後有蛋**：要鎖 pair 得先知道是哪個 pair，但又不能先鎖訂單（順序就反了）。
>   解法：**先「不鎖」讀出 `trading_pair_id`**（`trading_pair` 建立後永不改變，這個預讀安全——
>   它只決定「要鎖哪一列」，真正的授權與狀態判斷都在鎖到之後才做），再鎖 pair、再 `select_for_update` 鎖訂單重檢。
> - **取消絕不可非同步**：曾誤寫成 `cancel_order.delay(...)`。`.delay()` 是射後不理、立刻回傳，
>   `OrderNotCancelable` 永遠不會被 view 接到 → 終態的單也會回 200。而且 `cancel_order` 根本不是
>   `@shared_task`，沒有 `.delay` → `AttributeError` → 500。撮合非同步是因為它慢（要掃簿、連續成交）；
>   **取消只鎖一列、改狀態、退款，快且必須同步回報成敗**。
> - **例外要繼承純 `Exception`，不要繼承 DRF 的 `APIException`**：`cancel_order` 是業務層純函式，
>   會被併發測試/Celery/management command 呼叫，不該挾帶 HTTP 的 `status_code`。
>   **業務層說「不能取消」，view 層才決定「那對外回 400」**。同理 `tasks.py` 不該 import DRF。
> - **單一真相來源**：`test_concurrency.py` 原本自己複製了一份 view 的取消邏輯，
>   等於「測試在測複製品、不是測線上真正跑的程式碼」。抽成函式後 view 與測試呼叫同一個 `cancel_order`。
> - **不要為了 `balance_after` 把 `transfer_asset`/`release_frozen` 的 `F()` 改回「讀-改-寫」**：
>   錢包列**不受交易對閘門保護**（BTC/USDT 與 ETH/USDT 共用同一個 USDT 錢包;出入金也會碰），
>   改回去會在那些路徑掉更新。`F()` 是對的，要 `balance_after` 就 update 後重讀（`07-1` §6.1）。
> - **誠實話**：deadlock 是時序相依的，壓力測試不保證每次重現。那條測試是**回歸網**，
>   不是「沒紅就證明鎖對了」。鎖順序的正確性主要靠架構保證（§6.1）。

> **為什麼要抽成函式**：原本 `test_concurrency.py` 自己複製了一份 view 的取消邏輯，
> 等於「測試在測複製品、不是測線上真正跑的程式碼」——實作改了測試也不會紅。抽成單一真相來源後，
> view 與測試呼叫同一個 `cancel_order`。（`match_order` 本來就是這種純函式，兩者作伴。）
>
> **不要順手把 `transfer_asset`/`release_frozen` 的 `F()` 改回「讀-改-寫」**：錢包列**不受交易對閘門保護**
> （BTC/USDT 與 ETH/USDT 共用同一個 USDT 錢包;出入金也會碰），改回讀-改-寫會在那些路徑掉更新。
> `F()` 是對的，要 `balance_after` 就 update 後重讀（`07-1` §6.1）。

## M7 — 認證（補技術債）　〔規格：02-1〕　✅ 完成（JWT + 強制 TOTP 2FA + 註冊;54 測試綠）

### A 階段：基礎認證（JWT, simplejwt）

- [x] 設定 JWT：`djangorestframework-simplejwt`、REST_FRAMEWORK 設 JWTAuthentication + 全域 IsAuthenticated
- [x] 登入端點：`POST /api/user/login/`（TokenObtainPairView）、`/token/refresh/`（TokenRefreshView）
- [x] 移除 `get_random_user`，三處（下單、建錢包、出金）改綁 `request.user`
- [x] 權限：`get_queryset` 過濾 `request.user`（staff 看全部）;cancel 綁 `user=request.user`（別人取消→400）
- [x] 測試改帶認證：所有 API 測試改 `force_authenticate`（移除 get_random_user 的 mock）;新增「未登入→401」「別人不能取消你的單」
- [~] 註冊豁免：延到 M-KYC 一起做（屆時 `UserViewSet.create` 設 `AllowAny`、順帶建初始錢包）。在那之前新用戶用 admin/shell 建立。

### B 階段：2FA（TOTP）— ✅ 完成（強制全員 2FA）

- [x] 啟用 2FA 端點：`PUT /api/user/register/`，輸入一次正確 TOTP 碼才把 two_factor_enabled 設 True
- [x] 註冊端點：`POST /api/user/register/`（免登入），建 User+Profile、產生密鑰、回 secret + otpauth QR 連結
- [x] 登入加第二因素：`LoginSerializer`(subclass TokenObtainPairSerializer)，帳密過後驗 TOTP 才發 JWT
- [x] 密鑰加密儲存：`encrypted_totp_secret`(BinaryField) 用 Fernet 加密;設定 FERNET_KEY、ISSUER
- [x] 測試：`member/test/test_2fa.py`（8 條，pyotp 自算碼）;全套 54 條綠

> 實作重點 / 踩過的坑：
>
> - **強制 2FA**：LoginSerializer 採「未啟用 2FA 就不准登入」。代價是 superuser（無 profile）走 JWT login 會炸 → 已用 `getattr(user, 'profile', None)` 擋。
> - **valid_window=1**：verify_totp 一定要設，容忍 ±30 秒時鐘誤差;不設（預設 0）會讓 2FA 測試跨 30 秒邊界時間歇性失敗。
> - 既有 `force_authenticate` 測試不受強制 2FA 影響（直接設 request.user、不走 login serializer）。
> - 註冊豁免在這裡一併做掉了（RegisterView authentication_classes/permission_classes=[]）;M-KYC 只剩「KYC 欄位/審核/風險閘門」。

## M-身份組與權限（RBAC）【進階】　〔規格：02-1 §6.5〕　待做

- [ ] 用 Django Group 分角色（如 trader / support / admin）
- [ ] 對需要的 ViewSet 掛 `DjangoModelPermissions`（HTTP 方法↔model 權限）或自訂 permission 檢查 `request.user.groups`
- [ ] 全域維持 IsAuthenticated 當地板，個別 view 用 permission_classes 覆寫疊角色判斷
- [ ] 釐清「角色層（能做哪種 CRUD）」與「擁有權層（只能碰自己的）」兩維度一起生效
- [ ] 驗證：不同角色帳號對同一端點的 CRUD 權限符合預期

## M-KYC（身份驗證 + 註冊上線流程）【進階】　〔規格：02-1〕　待做

> 註冊流程與 KYC 一起做，避免改兩次。M7-A 暫時把註冊豁免延到這裡。

- [x] 註冊 API（免登入 RegisterView，建 User + Profile + TOTP 密鑰）— M7-B 已做
- [ ] 註冊時順帶建初始錢包（USDT/BTC）;list/retrieve 限 IsAdminUser
- [ ] KYC 欄位/狀態：身分證件、法定姓名、地址證明等;狀態機 unverified → pending → approved / rejected
- [ ] 文件上傳與審核流程（送審、人工/自動審核、結果回寫）
- [ ] 風險閘門：未通過 KYC 限制敏感操作（如出金、額度上限）
- [ ] 驗證：未驗證用戶被擋在受限操作之外;通過後解鎖

## M-日誌與帳本【進階】　〔規格：07-1〕　✅ 全部完成（LedgerEntry + DepositWithdrawModel，測試全綠）

**架構決策**：`LedgerEntryModel` 與 `DepositWithdrawModel` 獨立成新的 **`ledger` app**（原規劃在 member）。
唯一前提：`ledger` 只向下依賴 `currency`，`asset_type` FK 到 `CurrencyModel`（不 FK wallet）、
`ref_type/ref_id` 用軟參照字串（不 FK Order/Transaction），否則循環依賴。層次：
`common ← currency ← ledger ← member ← transaction`。詳見 `07` §3.1 與 `00` 依賴圖。

已由 Claude 完成（規格/文件/測試）：

- [x] 更新 `07-1_logging_audit_spec.md`（ledger app、軟參照、套用點對照現有函式、F() 取 balance_after 的坑、實作 checklist §6.2）
- [x] 更新 `00_overall_spec.md`（模組表 + 依賴圖加入 ledger）
- [x] 建立 `ledger/` 骨架（apps.py / migrations / test / models.py 規格 docstring）
- [x] 寫測試 `ledger/test/test_ledger.py`：model append-only 契約、各套用點（FREEZE/SETTLE/UNFREEZE/REFUND/WITHDRAW）、**對帳不變量端到端**

使用者實作（已完成，測試全綠）：

- [x] `ledger/models.py` 寫 `LedgerEntryModel`（append-only：save() 擋更新、delete() raise）
- [x] `INSTALLED_APPS` 加 `"ledger"`，`makemigrations ledger && migrate`
- [x] 在四個業務函式內補寫 `LedgerEntryModel`（同 atomic）：
      `transfer_to_frozen`(FREEZE)、`transfer_asset`(SETTLE，四筆靠 balance_field+正負分收付)、`release_frozen`(UNFREEZE/REFUND 依 order.status)、`withdraw`(WITHDRAW)
- [x] `ledger/test/test_ledger.py` 全綠（model 契約、各套用點、對帳不變量）

> 踩過的坑（給接手者）：`transfer_asset` 的 `balance_after` 要用「update 後重新讀到的值直接用」，
> 不可再 ± delta（會重複扣）；且抓付款腿錢包時別把 `user=seller` 寫成 `user=buyer`（曾導致
> SETTLE 的 balance_after 抓到錯人的錢包而對帳失敗）。

### DepositWithdrawModel（出入金紀錄）　✅ 完成（test_deposit_withdraw.py 6 條全綠）

決策：範圍＝出金+入金都記帳（見 07 §4 / §4.1）。

- [x] `07` §4 / §4.1：DW 非 append-only（status 會轉，範圍1 直接 DONE）、入金端點設計 + 出入金記帳 wiring；§5 套用點表更新
- [x] 測試 `ledger/test/test_deposit_withdraw.py`（DW model 契約、出金記 DW+ledger、admin-only 入金端點、對帳不變量）
- [x] `DepositWithdrawModel`（ledger app）：`tx_hash`/`address` 各 100 字元、`tx_hash` 加 `db_index`、繼承 `BaseTimeModel`；status 可更新（不擋 save/delete）
- [x] 出金 `withdraw`：建 DW(WITHDRAW, DONE)，LedgerEntry 的 ref 指回該 DW 列
- [x] 入金 `WalletViewSet.deposit`：`@action` + `permission_classes=[IsAdminUser]`；body `{user_id, asset_type_id, quantity}`；atomic 內 get_or_create 錢包、available += quantity、建 DW(DEPOSIT, DONE) + LedgerEntry(DEPOSIT)

> **為什麼入金是 admin-only**：真實 CEX 沒有「用戶呼叫 API 說幫我入金」這種端點——用戶是在**鏈上**自己轉帳，
> 交易所靠**監聽服務偵測到帳**才入帳。入金的本質是「憑空增加餘額」，範圍 1 沒有鏈上依據，
> 若開放給一般用戶等於任何人都能鑄錢。這個 admin 端點是**鏈上監聽器的替身**，範圍 2 會被取代並移除。
>
> **踩過的坑**：`create_deposit_ledgers` 的 `delta` 從 withdraw 複製過來忘了改號（入金應為 **+delta**），
> 會讓對帳直接破功；入金必須 `get_or_create` 錢包（用戶第一次入金某幣時本來就還沒有錢包，
> 與 withdraw 用 `.get()`、沒錢包回 400 的邏輯剛好相反）。

> **範圍 2 的接續點**：入金核心（available += / 建 DW / 寫 ledger）保持可重用；屆時把觸發器從 admin 端點
> 換成鏈上監聽器，並新增用戶端「取得充值地址」API、confirmations 邏輯、`tx_hash` 唯一（防同筆鏈上交易重複入帳）。

剩餘（更後面）：

- [ ] 範圍二相關的一切 → 見下方 M8（規格已寫齊：`01-2` / `02-2` / `06-2` / `07-2`）
- [ ] （選做進階）`TRADING_FEE` 手續費、manual 細分 reason（ADMIN_ADJUST/COMPENSATION/CORRECTION）+ `memo`/`operator` 欄位，見 `07-1` §3.2

## M8（升級到範圍二）— 測試鏈入金/出金　〔規格：01-2, 02-2, 06-2, 07-2〕　📄 設計已完成，未實作

> 範圍二的設計文件已寫齊，動工前**先讀那四份**。核心心法：**只換觸發器與憑據，入金/出金的核心邏輯不變**；
> 訂單/撮合/結算（`03`/`04`/`05`）**一行都不用改**。

- [ ] **⚠️ 先處理精度**（`01-2` §3）：現在金額全是 `Decimal(20,2)`，只有 2 位小數 → **BTC 的 1 satoshi 會被存成 0.00，直接弄丟用戶的錢**。這是跨全系統的 migration（錢包/訂單/成交/帳本），**必須在碰真錢之前做完**。
- [ ] 幣別加鏈上屬性（`01-2` §4）：`chain`、`is_native`、`contract_address`、`decimals`、`min_confirmations`、入出金開關;唯一鍵從 `code` 改成 `(code, chain)`
- [ ] 充值地址（`02-2`）：`DepositAddressModel` + HD wallet 派生（只存 `derivation_index`，私鑰不進 DB）;`GET /api/deposit/address/`
- [ ] 鏈上入金（`06-2` §2）：監聽服務 → 建 DW(PENDING) → 等 `min_confirmations` → 呼叫**範圍一同一個入金核心函式**入帳 → DONE
- [ ] 鏈上出金（`06-2` §3）：先扣餘額建 DW(PENDING) → 熱錢包簽署廣播 → 確認 DONE;**失敗要寫反向分錄退款**（`07-2` §3）
- [ ] 冪等性（`07-2` §2）：`tx_hash` 部分唯一索引（範圍一有空字串，不能直接 `unique=True`）;出金廣播前鎖 DW 檢查 status
- [ ] 移除範圍一的 admin 入金端點（它是監聽器的替身，任務結束）
- [ ] 私鑰/主種子用環境變數或 KMS，**勿進 git**
- [ ] 鏈上餘額對帳（`07-2` §5）：交易所鏈上持有 ≥ 用戶內部餘額總和（Proof of Reserves 雛形）

## M-撮合公平性與嚴格定序【進階／正式引擎，DEX 階段再做】　〔規格：04-1〕

> 背景（M6 延伸）：目前撮合用「一單一 Celery 任務、丟 worker 池非同步跑」。M6 的交易對鎖 +
> 冪等防護已保證**正確性**（不超賣、不雙重撮合、不 deadlock），但**沒保證公平性**：
> 任務可能亂序搶到交易對鎖，於是「晚到的單先撮」會發生 —— 成交價與餘額仍正確，但先後不完全等於到達順序，
> 偏離真實交易所的 price-time priority。真實引擎是「**同一交易對單一寫入者（single writer per symbol）、
> 嚴格按到達序、單一序列處理**」，從架構上天生保證公平，而非靠事後加鎖。

- [ ] 釐清需求：本專案要不要嚴格 price-time 公平？學習階段可不做，DEX/正式撮合才必要。
- [ ] 進單定序：在撮合前給訂單一個全域單調遞增序號（gateway / sequencer），撮合嚴格按序號處理。
- [ ] 單一寫入者模型：同一交易對的撮合收斂到「一條序列流」（專屬 worker / 單一消費佇列 per symbol），
      取代「多 worker 亂序搶鎖」；交易對鎖退化為保險絲而非主要序列化手段。
- [ ] 與 DEX 的銜接：鏈上訂單簿 / AMM 的成交順序由區塊內交易順序決定（含 MEV/排序問題），
      屆時「公平性」的定義與防護要重新規格化（不是現在這套 CEX 內部序）。
- [ ] 驗證：大量亂序進單下，成交順序符合 price-time priority；與「正確性」測試（test_concurrency）分開。

> 備註：這是 CEX 基本/進階都穩固後、真正 DEX 化的獨立大階段才處理的題目（見 CLAUDE.md「最終目標」）。
> 現階段刻意只做到「正確但不嚴格公平」，先把概念吃透；屆時另立規格，不重寫現有 `match_order`。

---

## 給前端的備忘（之後由 AI 協助）

使用者負責後端 API，前端畫面與串接由 AI 製作。需要前端時，先確認這些 API 已穩定：登入、查錢包、下單、查訂單、查成交/訂單簿。屆時做：登入頁、錢包頁、下單頁、訂單簿/成交歷史頁。
