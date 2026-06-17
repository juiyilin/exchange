from rest_framework import serializers

from currency.models import CurrencyModel, TradingPairModel
from member.models import WalletModel
from transaction.constants import OrderType

from .models import OrderModel


class OrderSerializer(serializers.ModelSerializer):
    user = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = OrderModel
        fields = "__all__"


class OrderCreateUpdateSerializer(serializers.ModelSerializer):
    trading_pair = serializers.PrimaryKeyRelatedField(required=True, queryset=TradingPairModel.objects)

    class Meta:
        model = OrderModel
        exclude = ['user']

    def validate(self, data):
        data = super().validate(data)
        user = self.context['user']

        trading_pair = TradingPairModel.objects.get(id=data['trading_pair'].id)
        if not trading_pair.is_active:
            raise serializers.ValidationError('幣對未開放')

        wallet, required_balance = self.check_wallet_balance(user, data, trading_pair)
        self.context.update({'wallet': wallet, 'required_balance': required_balance})
        return data

    def check_wallet_balance(self, user, data, trading_pair):
        order_type = data["order_type"]

        base_currency = trading_pair.base_currency
        quote_currency = trading_pair.quote_currency

        if order_type == OrderType.BUY:
            wallet = WalletModel.objects.select_for_update().filter(user=user, asset_type=quote_currency).first()
        else:
            wallet = WalletModel.objects.select_for_update().filter(user=user, asset_type=base_currency).first()

        if not wallet:
            raise serializers.ValidationError("用戶錢包不存在")

        required_balance = self.required_balance(data)
        if wallet.available_balance < required_balance:
            raise serializers.ValidationError(f"用戶可用餘額不足，目前餘額：{wallet.available_balance}，需要餘額：{required_balance}")

        return wallet, required_balance

    def required_balance(self, data):
        # 計算這筆訂單需要凍結的 currency1（出去的貨幣）數量
        if data["order_type"] == OrderType.BUY:
            # 買單：currency1 是計價幣，需付出 數量 × 價格
            return data["amount"] * data["price"]
        # 賣單：currency1 是標的幣，直接付出 數量
        return data["amount"]

    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        data['user'] = self.context['user']
        return data