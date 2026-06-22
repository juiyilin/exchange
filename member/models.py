from django.contrib.auth.models import User
from django.db import models
from django.db.models import F
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


class WalletQuerySet(models.QuerySet):
    def transfer_asset(self, transaction):
        buyer = transaction.buy_order.user
        seller = transaction.sell_order.user
        trading_pair = transaction.buy_order.trading_pair
        base_currency = trading_pair.base_currency
        quote_currency = trading_pair.quote_currency
        total_amount = transaction.price * transaction.quantity


        # buyer 取得買到的幣，付出本次交易的幣
        self.get_or_create(user=buyer, asset_type=base_currency)
        self.filter(user=buyer, asset_type=base_currency).update(available_balance=F('available_balance') + transaction.quantity)
        self.filter(user=buyer, asset_type=quote_currency).update(frozen_balance=F('frozen_balance') - total_amount)

        # seller 取得本次交易的幣，付出賣出的幣
        self.get_or_create(user=seller, asset_type=quote_currency)
        self.filter(user=seller, asset_type=base_currency).update(frozen_balance=F('frozen_balance') - transaction.quantity)
        self.filter(user=seller, asset_type=quote_currency).update(available_balance=F('available_balance') + total_amount)


class WalletManager(models.Manager.from_queryset(WalletQuerySet)):
    pass


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

    objects = WalletManager()

    class Meta:
        verbose_name = "錢包"
        verbose_name_plural = "錢包"
        unique_together = ("user", "asset_type")
