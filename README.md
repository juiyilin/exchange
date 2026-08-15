# 虛擬貨幣交易所 side project(進行中)

本專案規格為我與 Claude 討論後制定，Claude 撰寫單元測試，本人進行開發

## 套件管理: uv

- 安裝套件: `uv add xxx`
- 同步 pyproject.toml 與 uv.lock: `uv sync`

## 測試後台

http://127.0.0.1:8000/admin/，測試帳號root，測試密碼admin12345

## 單元測試

```
uv run python manage.py test
uv run manage.py test transaction.tests.test_orders
```
