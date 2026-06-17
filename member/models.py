from django.contrib.auth.models import User
from django.db import models

from common.models import BaseTimeModel
from currency.models import CurrencyModel


class UserProfileModel(BaseTimeModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    phone_number = models.CharField(
        max_length=20, blank=True, default="", verbose_name="電話號碼"
    )
    address = models.CharField(
        max_length=255, blank=True, default="", verbose_name="地址"
    )

    class Meta:
        verbose_name = "用戶其他資料"
        verbose_name_plural = "用戶其他資料"


class WalletModel(BaseTimeModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    asset_type = models.ForeignKey(
        CurrencyModel, on_delete=models.CASCADE, verbose_name="資產類型"
    )
    available_balance = models.DecimalField(
        max_digits=20, decimal_places=2, default=0, verbose_name="可用餘額"
    )
    frozen_balance = models.DecimalField(
        max_digits=20, decimal_places=2, default=0, verbose_name="凍結餘額"
    )

    class Meta:
        verbose_name = "錢包"
        verbose_name_plural = "錢包"
        unique_together = ("user", "asset_type")
