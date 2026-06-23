# 可接手任務清單（TASKS）

> 這份是「進度書籤」。下次開新對話時，把這份貼給我（或叫我讀 `docs/TASKS.md`），我就能直接從中途接手。
> 規則：引導你做、不直接幫你寫程式碼。每完成一項就把 `[ ]` 改成 `[x]`，並更新「目前進度」。

## 給接手的 AI 的提示（重要）

- 專案是 Django 4.2 + DRF + Celery + Redis + PostgreSQL 的中心化交易所後端。
- 範圍：**純內部帳本（模擬）**，文件已預留通往「測試鏈入金/出金」的路。
- App：`common`（基底）、`currency`（幣別）、`member`（用戶/錢包）、`transaction`（訂單/撮合/成交）。
- 完整設計在 `docs/00_overall_spec.md` 及各細部規格。**接手前先讀對應的規格文件。**
- 引導風格：解釋概念、給驗證標準、指出該改哪個檔案/該注意什麼坑，**但不替使用者寫實作程式碼**。
- 使用者背景：對區塊鏈/交易所不熟，跟著 Gemini 教學起步，需要由淺入深。
- 語言：繁體中文。

---

## 目前進度（最後更新：M5 非同步化 + 自我成交防護完成）

**節奏原則：先把「基本」功能全部做完，再進入「進階」。**（基本/進階的分類見 `00_overall_spec.md` 第 5 節功能總表）

已完成：
- M1 幣別與錢包、M2 下單與凍結、M3 撮合與結算（同步）全部完成並有測試。
- `OrderViewSet.create` 已串接 `match_order(order.id)`，下單即撮合（端到端）。
- 重構：TradingPairModel（base/quote）、欄位 quantity、狀態 FULLY_FILLED、Transaction 用 buy_order/sell_order。
- 結算 `WalletModel.objects.transfer_asset`（F() + 收款錢包 get_or_create）。
- 測試在 `transaction/test/`（test_matching、test_orders、test_order_create_matching）。

**✅ v0.1「基本」+ M4 訂單生命週期 + M5 非同步化 + 自我成交防護已完成。下一步建議 M6（併發安全）或 M7（認證）。**

已知技術債（屬進階，之後處理）：
- `get_random_user()` 隨機指派用戶，**還沒有真正的認證**（M7）；cancel 目前用 pk、未綁擁有者。
- 撮合的完整併發序列化（同交易對互斥、撮合內對訂單/錢包 select_for_update）尚未做，目前只有 cancel 的 select_for_update + 重檢（M6）。

已完成（M4）：取消訂單、多凍結退款、終態不可變、擋改單（PUT/PATCH→405）。
已完成（M5）：下單 `.delay()` 非同步撮合、Redis+worker、commit 後送任務、測試 eager。
已完成（補強）：自我成交防護 STP。

---

## M1 — 幣別與錢包就緒　〔規格：01, 02〕
- [x] admin 後台註冊 `CurrencyModel`（已完成）
- [x] admin 後台註冊 `UserProfileModel`、`WalletModel`（已完成，還加了 UserProfileInline）
- [x] 建立幣別 USDT、BTC（已完成，CurrencyModel 還加了 __str__ 與 save() 自動大寫）
- [x] 建立測試用戶（root / user），併建 USDT / BTC 錢包（4 個錢包齊全）
- [x] 模擬入金：USDT/BTC 錢包都有初始可用餘額
- [x] （選做）幣別查詢 API（`/api/currency/` 已掛 URL）
- [x] 驗證：API/admin 查得到餘額

## M2 — 下單與凍結　〔規格：03〕
- [x] 確認下單 API 能建單且正確凍結餘額（買單凍 USDT=quantity×price、賣單凍 BTC=quantity，雙向驗證通過）
- [x] 確認餘額不足會被擋下並回 400（已驗證：回 400「用戶可用餘額不足」）
- [x] 補訂單查詢 API：列表 ✅、單筆 ✅（待成交量尚未在 serializer 暴露，見下方備註）
- [x] 驗證凍結：買 1 BTC@30000 → USDT 可用 −30000、凍結 +30000（已通過）
- [x] 驗證超額：下超額單回 400 且餘額完全不變（已驗證，root USDT 餘額前後一致）

> 備註：測試期間 `get_random_user_id()` 暫時固定回傳 1（root），方便驗證；正解在 M7 改認證。
> 備註：訂單查詢若要顯示「待成交量」，需在 OrderSerializer 加一個 SerializerMethodField 對應 `waiting_transaction_quantity()`（目前 fields="__all__" 不含 model 方法）。
> 備註：OrderModel 未在 admin 註冊，訂單只能用 API 查；若想在後台看，之後可補 transaction/admin.py。

## M3 — 撮合與結算（同步版）★核心★　〔規格：04, 05〕　✅ 完成（13 測試全綠）
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

## M-基本收尾 — 模擬出金　〔規格：06 §2/§3〕　✅ 完成
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

## M4 — 訂單生命週期【進階】　〔規格：03, 05〕　✅ 完成（測試全綠，25 條）
- [x] 取消訂單：解凍剩餘凍結餘額、狀態改 CANCELED（POST /order/{id}/cancel/，@action detail=True）
- [x] 多凍結退款（買單終態時退「原凍結 − 實際花費」差額；與取消共用 release_frozen）
- [x] 訂單狀態機完整、終態不可再變（cancel 內 select_for_update 鎖訂單 + 重檢狀態擋終態）
- [x] 堵掉改單：OrderViewSet 設 `http_method_names = ['get', 'post']`，PUT/PATCH 回 405
- [x] 決定：保留 ordered_at 欄位（不收斂到 created_at）
- [x] 驗證：member/transaction 測試全綠（取消全退/只退剩餘、多凍退款、終態擋取消、防重複退、擋改單）

> 實作重點（給接手者）：
> - `release_frozen(order)` 在 `member/models.py` WalletQuerySet，公式：買 `quantity*price − Σ(t.qty*t.price)`、賣 `quantity − Σ(t.qty)`，只在 order 進終態(FULLY_FILLED/CANCELED)時把差額 frozen→available。
> - 心智模型：**任何訂單一進終態就 release_frozen，不分 maker/taker**。撮合 tasks.py 裡 maker 與 taker 各在 `mark_*_status` 之後呼叫一次。
> - 踩過的坑：`release_frozen(maker)` 必須擺在 `mark_maker_status` 之後，否則 maker 狀態還沒變終態 → no-op，「先 taker 部分成交、後當 maker 成交完」的多凍會卡住不退（已加迴歸測試 OverFreezeTakerThenMakerTest）。
> - 新測試檔：`transaction/test/test_cancel_refund.py`（9 條）。
> - 併發互斥僅做到 cancel 的 select_for_update + 重檢；完整序列化留 M6。

## M5 — 非同步化　〔規格：04〕　✅ 完成
- [x] 啟動 Redis（Docker：`docker run -d --name exchange-redis -p 6373:6379 redis:7`）+ Celery worker（`uv run celery -A exchange worker -l info`）
- [x] 下單改成 `send_to_match_market.delay()`，API 秒回
- [x] 撮合在 Celery worker 跑（worker log 可見 `send_to_match_market` received/succeeded）
- [x] 驗證：下單立即回應、背景完成撮合、四錢包正確結算

> 實作重點（給接手者）：
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

## M6 — 併發安全　〔規格：04, 02〕
- [ ] 撮合對訂單/錢包加 `select_for_update`
- [ ] 同一交易對撮合序列化（避免雙重撮合）
- [ ] 餘額不變量檢查（不可變負）
- [ ] 併發壓力測試：大量對手單不超賣、不重複成交

## M7 — 認證（補技術債）　〔規格：02〕
- [ ] 註冊 / 登入 / Token 驗證
- [ ] 移除 `get_random_user_id`，所有操作綁 `request.user`
- [ ] 權限：只能操作自己的錢包/訂單

## M-日誌與帳本【進階】　〔規格：07〕
- [ ] `LedgerEntry`：每次餘額變動（凍結/解凍/結算/退款/入出金/手續費）寫一筆 append-only 紀錄，與餘額變動同一 atomic
- [ ] 在各業務函式顯式寫入（不要用 signal）：下單凍結、撮合結算、取消/退款、入金、出金
- [ ] `DepositWithdrawModel`：入出金業務紀錄（status、tx_hash/address，範圍 1 留空）
- [ ] 對帳：錢包餘額 == 該錢包所有 ledger delta 總和
- [ ] 注意：設計已定案於 `07_logging_audit_spec.md`；基本階段不做，這裡是延後實作的依據

## M8（升級到範圍 2）— 測試鏈入金/出金　〔規格：06〕
- [ ] 加 `DepositWithdrawModel`（含 tx_hash / address 欄位）
- [ ] 產生充值地址
- [ ] 監聽鏈上到帳 + 確認數 → 入帳
- [ ] 出金：扣餘額 → 簽署廣播 → 確認 → 完成
- [ ] 私鑰用環境變數/金鑰管理，勿進 git

---

## 給前端的備忘（之後由 AI 協助）
使用者負責後端 API，前端畫面與串接由 AI 製作。需要前端時，先確認這些 API 已穩定：登入、查錢包、下單、查訂單、查成交/訂單簿。屆時做：登入頁、錢包頁、下單頁、訂單簿/成交歷史頁。
