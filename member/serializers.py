from django.contrib.auth.models import User
import pyotp
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import UserProfileModel, WalletModel
from common.func import verify_totp, generate_encrypted_totp_secret
from exchange.settings import ISSUER


class RegisterSerializer(serializers.ModelSerializer):
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

        secret, encrypted_secret = generate_encrypted_totp_secret()
        UserProfileModel.objects.create(
            user=user, phone_number=phone_number, address=address, encrypted_totp_secret=encrypted_secret
        )
        self.validated_data['secret'] = secret
        self.validated_data['issuer'] = ISSUER
        return user

    def to_representation(self, instance):
        data = {
            'username': instance.username,
            'secret': self.validated_data['secret'],
            'issuer': ISSUER,
            'qrcode_link': pyotp.totp.TOTP(self.validated_data['secret']).provisioning_uri(name=instance.username, issuer_name=ISSUER)
        }
        return data


class TwoFactorEnableSerializer(serializers.Serializer):
    username = serializers.CharField()
    totp = serializers.CharField(max_length=6)

    def validate(self, attrs):
        data = super().validate(attrs)
        try:
            self.instance = UserProfileModel.objects.get(user__username=data['username'], two_factor_enabled=False)
        except UserProfileModel.DoesNotExist:
            raise serializers.ValidationError({'username': '已啟用2FA或不存在此使用者'})

        decrypt_totp_secret = self.instance.decrypt_totp_secret()
        if not verify_totp(decrypt_totp_secret, attrs['totp']):
            raise serializers.ValidationError({'totp': 'TOTP 錯誤'})
        return data

    def update(self, instance, validated_data):
        self.instance.two_factor_enabled = True
        self.instance.save()
        return self.instance


class LoginSerializer(TokenObtainPairSerializer):
    totp = serializers.CharField(max_length=6)

    def validate(self, attrs):
        data = super().validate(attrs)
        if not getattr(self.user, 'profile'):
            UserProfileModel.objects.create(user=self.user)
            self.user.refresh_from_db()
        if not self.user.profile.two_factor_enabled:
            raise serializers.ValidationError({'two_facotr_enabled': '尚未啟用2fa'})
        decrypt_totp_secret = self.user.profile.decrypt_totp_secret()
        if not verify_totp(decrypt_totp_secret, attrs['totp']):
            raise serializers.ValidationError({'totp': 'TOTP 錯誤'})
        return data


class UserListSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(source="profile.phone_number", read_only=True)
    address = serializers.CharField(source="profile.address", read_only=True)

    class Meta:
        model = User
        exclude = ["password"]


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

