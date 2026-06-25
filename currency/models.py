from django.db import models

from common.models import BaseTimeModel


class CurrencyModel(BaseTimeModel):
    code = models.CharField(
        max_length=10,
        unique=True,
        verbose_name="貨幣代碼",
        help_text="例如：USDT、BTC、ETH等",
    )
    name = models.CharField(
        max_length=50,
        verbose_name="貨幣名稱",
        help_text="例如：Tether、Bitcoin、Ethereum等",
    )

    class Meta:
        verbose_name = "貨幣"
        verbose_name_plural = "貨幣"

    def __str__(self):
        return f'{self.name}({self.code})'

    def save(self, *args, **kwargs):
        self.code = self.code.upper()
        return super().save(*args, **kwargs)


class TradingPairModel(BaseTimeModel):
    """
    ex: BTC/USDT 表示 1 顆 BTC 值多少 USDT
    """
    base_currency = models.ForeignKey(CurrencyModel, on_delete=models.CASCADE, related_name='base_pairs', verbose_name='基準貨幣', help_text='買進或賣出的貨幣')
    quote_currency = models.ForeignKey(CurrencyModel, on_delete=models.CASCADE, related_name='quote_pairs', verbose_name='報價貨幣', help_text='計算的貨幣')
    symbol = models.CharField(max_length=20, default='', verbose_name='幣對名稱')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "幣對"
        verbose_name_plural = "幣對"
        constraints = [
            models.UniqueConstraint(
                fields=['base_currency', 'quote_currency', 'symbol'],
                name='unique_currency_pair')
        ]

    def __str__(self):
        return self.symbol

    def save(self, *args, **kwargs):
        if self.base_currency == self.quote_currency:
            raise ValueError()
        self.symbol = f'{self.base_currency.code}/{self.quote_currency.code}'
        return super().save(*args, **kwargs)
