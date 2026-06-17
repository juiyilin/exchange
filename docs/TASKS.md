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

## 目前進度（最後更新：建立文件當下）

已存在的程式碼雛形：
- 四個 app 結構、`BaseTimeModel`、`CurrencyModel`、`UserProfileModel`、`WalletModel`、`OrderModel`、`TransactionModel` 都已建好。
- 下單 API（`OrderViewSet.create`）已能驗證餘額並凍結。
- Celery/Redis/Postgres 相依已裝、`celery.py` 已設定。

已知技術債/未完成：
- 撮合引擎 `send_to_match_market` 是空函式，下單裡的 `.delay()` 被註解。
- 用 `get_random_user_id()` 隨機指派用戶，**還沒有真正的認證**。
- `currency/views.py` 是空的；`TransactionViewSet` 在 urls 被註解。
- 結算邏輯尚未實作。
- 還沒做取消訂單、部分成交退款。

**下一步建議從 M1 / M2 的驗證開始，再進入 M3（撮合結算）這個核心。**

---

## M1 — 幣別與錢包就緒　〔規格：01, 02〕
- [x] admin 後台註冊 `CurrencyModel`（已完成）
- [x] admin 後台註冊 `UserProfileModel`、`WalletModel`（已完成，還加了 UserProfileInline）
- [x] 建立幣別 USDT、BTC（已完成，CurrencyModel 還加了 __str__ 與 save() 自動大寫）
- [x] 建立測試用戶（root / user），併建 USDT / BTC 錢包（4 個錢包齊全）
- [x] 模擬入金：USDT/BTC 錢包都有初始可用餘額
- [ ] （選做）`CurrencyViewSet` 查詢 API + URL（尚未做，currency 未掛 URL）
- [x] 驗證：API/admin 查得到餘額

## M2 — 下單與凍結　〔規格：03〕
- [x] 確認下單 API 能建單且正確凍結餘額（買單凍 USDT=amount×price、賣單凍 BTC=amount，雙向驗證通過）
- [x] 確認餘額不足會被擋下並回 400（已驗證：回 400「用戶可用餘額不足」）
- [x] 補訂單查詢 API：列表 ✅、單筆 ✅（待成交量尚未在 serializer 暴露，見下方備註）
- [x] 驗證凍結：買 1 BTC@30000 → USDT 可用 −30000、凍結 +30000（已通過）
- [x] 驗證超額：下超額單回 400 且餘額完全不變（已驗證，root USDT 餘額前後一致）

> 備註：測試期間 `get_random_user_id()` 暫時固定回傳 1（root），方便驗證；正解在 M7 改認證。
> 備註：訂單查詢若要顯示「待成交量」，需在 OrderSerializer 加一個 SerializerMethodField 對應 `waiting_transaction_amount()`（目前 fields="__all__" 不含 model 方法）。
> 備註：OrderModel 未在 admin 註冊，訂單只能用 API 查；若想在後台看，之後可補 transaction/admin.py。

## M3 — 撮合與結算（同步版）★核心★　〔規格：04, 05〕
- [ ] 把撮合寫成獨立函式（先同步）
- [ ] 實作價格時間優先的對手查詢
- [ ] 逐筆配對、算成交量(min)、建 `TransactionModel`、更新訂單狀態
- [ ] 實作結算函式：原子搬動四個錢包餘額（含 `select_for_update`）
- [ ] 處理部分成交（PARTIALLY_FILLED）
- [ ] 驗證：同價買賣單完全成交、雙方餘額符合手算；部分成交狀態正確
- [ ] 修正 `OrderModel.executed_transaction_amount` 的 related_name 對應（buy_order/sell_order）

## M4 — 訂單生命週期　〔規格：03, 05〕
- [ ] 取消訂單：解凍剩餘凍結餘額、狀態改 CANCELED
- [ ] 多凍結退款（買單以低於掛價成交時退差額）
- [ ] 訂單狀態機完整、終態不可再變
- [ ] 驗證：掛單後取消，凍結正確退回可用

## M5 — 非同步化　〔規格：04〕
- [ ] 啟動 Redis + Celery worker
- [ ] 下單改成 `send_to_match_market.delay()`，API 秒回
- [ ] 撮合在 Celery worker 跑
- [ ] 驗證：下單立即回應，背景完成撮合

## M6 — 併發安全　〔規格：04, 02〕
- [ ] 撮合對訂單/錢包加 `select_for_update`
- [ ] 同一交易對撮合序列化（避免雙重撮合）
- [ ] 餘額不變量檢查（不可變負）
- [ ] 併發壓力測試：大量對手單不超賣、不重複成交

## M7 — 認證（補技術債）　〔規格：02〕
- [ ] 註冊 / 登入 / Token 驗證
- [ ] 移除 `get_random_user_id`，所有操作綁 `request.user`
- [ ] 權限：只能操作自己的錢包/訂單

## M8（升級到範圍 2）— 測試鏈入金/出金　〔規格：06〕
- [ ] 加 `DepositWithdrawModel`（含 tx_hash / address 欄位）
- [ ] 產生充值地址
- [ ] 監聽鏈上到帳 + 確認數 → 入帳
- [ ] 出金：扣餘額 → 簽署廣播 → 確認 → 完成
- [ ] 私鑰用環境變數/金鑰管理，勿進 git

---

## 給前端的備忘（之後由 AI 協助）
使用者負責後端 API，前端畫面與串接由 AI 製作。需要前端時，先確認這些 API 已穩定：登入、查錢包、下單、查訂單、查成交/訂單簿。屆時做：登入頁、錢包頁、下單頁、訂單簿/成交歷史頁。
