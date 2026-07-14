# 細部規格 — 幣別（currency）〔範圍二：測試鏈〕

> 對應 app：`currency`　主要 model：`CurrencyModel`（擴充）
> 上層文件：`00_overall_spec.md`　**範圍一前篇：`01-1_currency_spec.md`**
> 相關：`06-2_deposit_withdraw_spec.md`（鏈上入出金會用到這裡所有欄位）
>
> **狀態：設計文件，尚未實作。**

## 1. 為什麼幣別需要「鏈上屬性」

範圍一的 `CurrencyModel` 只有 `code` 和 `name`——因為不碰鏈，系統只需要知道「有這種幣」。

一旦要真的收發幣，光有代碼完全不夠。監聽服務得知道：**要去哪條鏈上盯？盯哪個合約？
鏈上那串整數要除以多少才是人看的數字？要等幾個確認才敢入帳？** 這些全是幣別的屬性。

## 2. 一個關鍵設計問題：`USDT` 到底是幾種幣？

**同一個代號在不同鏈上是完全不同的東西。** USDT 在以太坊、Tron、BSC 上都有發行，
它們**互不相通**——把 Tron 的 USDT 轉到以太坊的地址，錢會永久消失（`06-2` §5 那個真實災難）。

所以「幣別」的正確身分不是 `code`，而是 **`(code, chain)` 的組合**。兩種做法：

- **做法 A（推薦，改動小）**：在 `CurrencyModel` 加 `chain` 欄位，
  唯一約束從 `code` 改成 `unique_together = (code, chain)`。
  於是「以太坊上的 USDT」和「Tron 上的 USDT」是**兩筆幣別資料**，各有自己的合約地址與充值地址。
- **做法 B**：拆成「資產（Asset）」與「該資產在某鏈上的發行（Token）」兩層。更正規，但改動大。

> 範圍一的 `unique=True` 在 `code` 上——升級時**必須改掉**，否則你只能支援單一條鏈。

## 3. ⚠️ 精度：現在的 `Decimal(20, 2)` 在範圍二一定不夠

這是升級範圍二**最容易被忽略、又最致命**的一件事，現在就要知道。

鏈上的金額是**整數的最小單位**：

| 幣 | 最小單位 | `decimals` | 1 顆 = |
|---|---|---|---|
| BTC | satoshi | 8 | 100,000,000 satoshi |
| ETH | wei | 18 | 1,000,000,000,000,000,000 wei |
| USDT (ERC-20) | — | 6 | 1,000,000 |

而我們現在 `WalletModel`、`LedgerEntryModel`、`OrderModel` 的金額欄位全是
**`DecimalField(max_digits=20, decimal_places=2)`——只有 2 位小數**。

後果：**0.00000001 BTC（1 satoshi）在我們的系統裡會被存成 0.00**，直接消失。
用戶入金 0.005 BTC → 變成 0.00。這是會**弄丟用戶的錢**的等級的 bug。

**升級時必須處理**（三選一，屆時再定案）：

1. **擴大 `decimal_places`**（例如 8 或 18）。簡單，但 18 位對 `max_digits=20` 來說只剩 2 位整數位，
   要一起把 `max_digits` 加大（例如 `max_digits=36, decimal_places=18`）。
2. **每個幣別各自的精度**：`CurrencyModel.decimals` 決定顯示與驗證的精度，
   DB 統一用一個夠大的精度存。
3. **改用整數存最小單位**（像鏈上一樣存 wei/satoshi 的 `BigInteger`），顯示時才換算。
   最不會出錯，但整個系統的金額運算都要改。

無論選哪個，這都是一次**跨全系統的 migration**（錢包、訂單、成交、帳本全部要動），
而且要**在有真錢之前做完**。

## 4. 欄位設計（`CurrencyModel` 擴充）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `chain` | CharField / choices | 在哪條鏈（`ETH_SEPOLIA`、`BTC_TESTNET`…）。與 `code` 合起來唯一 |
| `is_native` | Boolean | 是鏈的原生幣（ETH、BTC）還是合約發行的代幣（ERC-20 的 USDT） |
| `contract_address` | CharField(100) | **代幣才有**（ERC-20 合約地址）；原生幣留空 |
| `decimals` | PositiveSmallInteger | 鏈上精度（BTC 8、ETH 18、USDT 6）。**換算的關鍵** |
| `min_confirmations` | PositiveSmallInteger | 入金要等幾個確認才入帳（BTC 3~6、ETH 12+） |
| `min_withdraw` | Decimal | 最小提幣量（低於鏈上手續費就沒意義） |
| `withdraw_fee` | Decimal | 提幣手續費（轉嫁 gas 給用戶；記帳用獨立 reason，見 `06-2` §3.4） |
| `is_deposit_enabled` | Boolean | 是否開放入金（該鏈維護、擁塞時可關閉） |
| `is_withdraw_enabled` | Boolean | 是否開放出金 |

> `is_active`（是否開放交易）在 `01-1` §6.3 已規劃，與這裡的入出金開關是**三個獨立的閘門**：
> 可以「暫停入金但仍可交易」，也可以「下架交易但仍讓人提幣出去」。真實交易所常這樣操作。

## 5. 常見坑

- **只用 `code` 當唯一鍵** → 無法同時支援多鏈的同名幣，且極易讓用戶送錯鏈丟幣。
- **`decimals` 搞錯** → 金額差 10^n 倍。ERC-20 的 USDT 是 6 不是 18，這種細節錯了就是大事故。
- **原生幣去查合約地址** → ETH / BTC 沒有合約地址，程式要分開處理原生幣與代幣兩條路徑。
- **`min_confirmations` 設太小** → 見 `06-2` §2.2，會被雙花。
