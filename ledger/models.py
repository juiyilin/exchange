from django.db import models
from django.contrib.auth.models import User
from common.models import BaseTimeModel
from currency.models import CurrencyModel
from .constants import ReasonType, BalanceFieldType, DepositWithdrawType, DepositWithdrawStatus
from transaction.constants import OrderStatus
from django.db.models.functions import Abs, Coalesce


class DepositWithdrawModel(BaseTimeModel):
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, verbose_name='使用者')
    asset_type = models.ForeignKey(CurrencyModel, null=True, on_delete=models.SET_NULL, verbose_name='哪個幣別的錢包')
    amount = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='金額')
    direction = models.CharField(max_length=20, choices=DepositWithdrawType.choices, verbose_name='變動方向')
    status = models.CharField(max_length=20, choices=DepositWithdrawStatus.choices, verbose_name='狀態')
    tx_hash = models.CharField(max_length=100, blank=True, default="", db_index=True)
    address = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        verbose_name = "出入金紀錄"
        verbose_name_plural = "出入金紀錄"


class LedgerEntryQuerySet(models.QuerySet):
    def create_order_ledgers(self, wallet, delta, order):
        """下單時的流水帳"""
        available_ledger_dict = {
            'user': wallet.user,
            'asset_type': wallet.asset_type,
            'reason': ReasonType.FREEZE,
            'balance_field': BalanceFieldType.AVAILABLE,
            'delta': -delta,
            'balance_after': wallet.available_balance,
            'ref_type': order._meta.model_name,
            'ref_id': str(order.id)
        }
        frozen_ledger_dict = {
            'user': wallet.user,
            'asset_type': wallet.asset_type,
            'reason': ReasonType.FREEZE,
            'balance_field': BalanceFieldType.FROZEN,
            'delta': delta,
            'balance_after': wallet.frozen_balance,
            'ref_type': order._meta.model_name,
            'ref_id': str(order.id)
        }
        self.create(**available_ledger_dict)
        self.create(**frozen_ledger_dict)

    def create_transaction_ledgers(self, base_delta, quote_delta, transaction, buyer_base_wallet, buyer_quote_wallet, seller_base_wallet, seller_quote_wallet):
        """成交時的流水帳"""
        # user
        buyer = transaction.buy_order.user
        seller = transaction.sell_order.user

        # asset_type
        trading_pair = transaction.buy_order.trading_pair
        base_currency = trading_pair.base_currency
        quote_currency = trading_pair.quote_currency

        reason = ReasonType.SETTLE

        buyer_base_ledger_dict = {
            'user': buyer,
            'asset_type': base_currency,
            'reason': reason,
            'balance_field': BalanceFieldType.AVAILABLE,
            'delta': base_delta,
            'balance_after': buyer_base_wallet.available_balance,
            'ref_type': transaction._meta.model_name,
            'ref_id': str(transaction.id)
        }
        buyer_quote_ledger_dict = {
            'user': buyer,
            'asset_type': quote_currency,
            'reason': reason,
            'balance_field': BalanceFieldType.FROZEN,
            'delta': -quote_delta,
            'balance_after': buyer_quote_wallet.frozen_balance,
            'ref_type': transaction._meta.model_name,
            'ref_id': str(transaction.id)
        }
        seller_base_ledger_dict = {
            'user': seller,
            'asset_type': base_currency,
            'reason': reason,
            'balance_field': BalanceFieldType.FROZEN,
            'delta': -base_delta,
            'balance_after': seller_base_wallet.frozen_balance,
            'ref_type': transaction._meta.model_name,
            'ref_id': str(transaction.id)
        }
        seller_quote_ledger_dict = {
            'user': seller,
            'asset_type': quote_currency,
            'reason': reason,
            'balance_field': BalanceFieldType.AVAILABLE,
            'delta': quote_delta,
            'balance_after': seller_quote_wallet.available_balance,
            'ref_type': transaction._meta.model_name,
            'ref_id': str(transaction.id)
        }
        self.create(**buyer_base_ledger_dict)
        self.create(**buyer_quote_ledger_dict)
        self.create(**seller_base_ledger_dict)
        self.create(**seller_quote_ledger_dict)

    def create_release_ledgers(self, wallet, delta, order):
        """全部成交、取消時的流水帳"""
        if delta == 0:
            # 差額為 0 不用寫
            return
        if order.status == OrderStatus.CANCELED:
            reason = ReasonType.UNFREEZE
        elif order.status == OrderStatus.FULLY_FILLED:
            reason = ReasonType.REFUND
        else:
            return

        available_ledger_dict = {
            'user': wallet.user,
            'asset_type': wallet.asset_type,
            'reason': reason,
            'balance_field': BalanceFieldType.AVAILABLE,
            'delta': delta,
            'balance_after': wallet.available_balance,
            'ref_type': order._meta.model_name,
            'ref_id': str(order.id)
        }
        frozen_ledger_dict = {
            'user': wallet.user,
            'asset_type': wallet.asset_type,
            'reason': reason,
            'balance_field': BalanceFieldType.FROZEN,
            'delta': -delta,
            'balance_after': wallet.frozen_balance,
            'ref_type': order._meta.model_name,
            'ref_id': str(order.id)
        }
        self.create(**available_ledger_dict)
        self.create(**frozen_ledger_dict)

    def create_deposit_ledgers(self, wallet, delta):
        """入金時的流水帳"""
        deposit = DepositWithdrawModel.objects.create(user=wallet.user, asset_type=wallet.asset_type, amount=delta,
                                            direction=DepositWithdrawType.DEPOSIT, status=DepositWithdrawStatus.DONE)
        ledger_dict = {
            'user': wallet.user,
            'asset_type': wallet.asset_type,
            'reason': ReasonType.DEPOSIT,
            'balance_field': BalanceFieldType.AVAILABLE,
            'delta': delta,
            'balance_after': wallet.available_balance,
            'ref_type': deposit._meta.model_name,
            'ref_id': str(deposit.id)
        }
        self.create(**ledger_dict)

    def create_withdraw_ledgers(self, wallet, delta):
        """出金時的流水帳"""
        withdraw = DepositWithdrawModel.objects.create(user=wallet.user, asset_type=wallet.asset_type, amount=delta,
                                            direction=DepositWithdrawType.WITHDRAW, status=DepositWithdrawStatus.DONE)
        ledger_dict = {
            'user': wallet.user,
            'asset_type': wallet.asset_type,
            'reason': ReasonType.WITHDRAW,
            'balance_field': BalanceFieldType.AVAILABLE,
            'delta': -delta,
            'balance_after': wallet.available_balance,
            'ref_type': withdraw._meta.model_name,
            'ref_id': str(withdraw.id)
        }
        self.create(**ledger_dict)

    def get_user_total_amount(self, user, reason, date_from, date_to=''):
        if not date_to:
            date_filter = {'created_at__date': date_from}
        # TODO:有 date_to 時的filter
        return self.filter(
                user_id=user.id,
                reason=reason,
                **date_filter
            ).select_related('asset_type').select_for_update().aggregate(
                total=Coalesce(
                        models.Sum(models.F('asset_type__fiat_rate') * Abs(models.F('delta'))),
                        models.Value(0),
                        output_field=models.DecimalField()
                    )
            )['total']


class LedgerEntryManager(models.Manager.from_queryset(LedgerEntryQuerySet)):
    pass

class LedgerEntryModel(models.Model):
    """
    餘額變動紀錄
    不可修改與刪除
    """
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, verbose_name='使用者')
    asset_type = models.ForeignKey(CurrencyModel, null=True, on_delete=models.SET_NULL, verbose_name='哪個幣別的錢包')
    reason = models.CharField(max_length=20, choices=ReasonType.choices, verbose_name='變動原因')
    balance_field = models.CharField(max_length=20, choices=BalanceFieldType, verbose_name='變動的balance欄位')
    delta = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='變動量(有+-號)')
    balance_after = models.DecimalField(max_digits=20, decimal_places=2, verbose_name='balance_field變動後的值')
    ref_type = models.CharField(max_length=32, verbose_name='事件來源', help_text='用model名稱，沒有則為manual')
    ref_id =models.CharField(max_length=32, verbose_name='事件來源id', help_text='用model id，沒有則為""，DEX時可存 hash')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LedgerEntryManager()

    class Meta:
        verbose_name = "帳本流水帳"
        verbose_name_plural = "帳本流水帳"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError(f"{self._meta.model_name} is append-only: 不可更新，請寫一筆反向分錄")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(f"{self._meta.model_name} is append-only: 不可刪除")