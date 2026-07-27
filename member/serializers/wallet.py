"""錢包與出入金相關 serializer"""

from django.contrib.auth.models import User
from rest_framework import serializers
from member.models import WalletModel


class WalletSerializer(serializers.ModelSerializer):

    class Meta:
        model = WalletModel
        fields = ['asset_type', 'available_balance']


class WithdrawSerializer(serializers.Serializer):
    asset_type_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=20, decimal_places=2, min_value=0)

    def validate_quantity(self, value):
        if value == 0:
            raise serializers.ValidationError('需大於 0')
        return value


class DepositSerializer(WithdrawSerializer):
    user_id = serializers.IntegerField()

    def validate_user_id(self, value):
        if User.objects.filter(id=value).exists():
            return User.objects.filter(id=value).first().id
        raise serializers.ValidationError('無此使用者')
