from django.contrib.auth.models import User
from django.db import models
from django.db.models import F
import pyotp
from common.models import BaseTimeModel
from currency.models import CurrencyModel
from transaction.constants import OrderStatus
from cryptography.fernet import Fernet
from exchange.settings import FERNET_KEY, ISSUER


class UserProfileModel(BaseTimeModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone_number = models.CharField(max_length=20, blank=True, default="", verbose_name="電話號碼")
    address = models.CharField(max_length=255, blank=True, default="", verbose_name="地址")

    # 2fa
    two_factor_enabled = models.BooleanField(default=False, verbose_name='是否啟用2fa')
    encrypted_totp_secret = models.BinaryField(default=b'', verbose_name="加密後的TOTP金鑰")

    class Meta:
        verbose_name = "用戶其他資料"
        verbose_name_plural = "用戶其他資料"

    def decrypt_totp_secret(self):
        """回傳原本的TOTP金鑰"""
        fernet = Fernet(FERNET_KEY)
        totp_secret = fernet.decrypt(self.encrypted_totp_secret).decode()
        return totp_secret

    def get_secret_qrcode_link(self):
        if self.encrypted_totp_secret:
            secret = self.decrypt_totp_secret()
        else: 
            secret = pyotp.random_base32()
        return pyotp.totp.TOTP(secret).provisioning_uri(name=self.user.username, issuer_name=ISSUER)

    def get_current_totp(self):
        self.decrypt_totp_secret()
        return pyotp.TOTP(self.decrypt_totp_secret()).now()


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

    def release_frozen(self, order):
        """取消 或 全部成交 後要處理多餘的凍結"""
        if order.status in [OrderStatus.FULLY_FILLED, OrderStatus.CANCELED]:
            current_frozen = order.get_current_frozen()
            asset_type = order.get_asset_type()
            self.filter(
                    user=order.user, asset_type=asset_type
                ).update(
                    frozen_balance=F('frozen_balance') - current_frozen,
                    available_balance=F('available_balance') + current_frozen
                )


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
        constraints = [
            models.CheckConstraint(condition=models.Q(available_balance__gte=0), name="available_non_negative"),
            models.CheckConstraint(condition=models.Q(frozen_balance__gte=0), name="frozen_non_negative"),
        ]