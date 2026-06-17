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
