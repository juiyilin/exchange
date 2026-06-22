from django.contrib.auth.models import User
from rest_framework import serializers

from .models import UserProfileModel, WalletModel


class UserListSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(
        source="userprofilemodel.phone_number", read_only=True
    )
    address = serializers.CharField(source="userprofilemodel.address", read_only=True)

    class Meta:
        model = User
        exclude = ["password"]


class UserCreateUpdateSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(write_only=True)
    address = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = "__all__"

    def create(self, validated_data):
        phone_number = validated_data.pop("phone_number")
        address = validated_data.pop("address")
        user = User.objects.create_user(**validated_data)
        UserProfileModel.objects.create(
            user=user, phone_number=phone_number, address=address
        )
        return user


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

