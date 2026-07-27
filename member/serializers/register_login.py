"""註冊與登入相關 serializer"""

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import serializers
from django.contrib.auth.models import User
from currency.models import CurrencyModel
from common.func import verify_totp, generate_encrypted_totp_secret
from exchange.settings import ISSUER
from member.models import UserProfileModel, WalletModel
import pyotp


class RegisterSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(write_only=True)
    address = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)
    wallet_currency_ids = serializers.PrimaryKeyRelatedField(required=False, write_only=True, many=True, queryset=CurrencyModel.objects.all())

    class Meta:
        model = User
        fields = "__all__"

    def create(self, validated_data):
        phone_number = validated_data.pop("phone_number")
        address = validated_data.pop("address")
        asset_types = set(validated_data.pop('wallet_currency_ids', []))

        user = User.objects.create_user(**validated_data)

        secret, encrypted_secret = generate_encrypted_totp_secret()
        UserProfileModel.objects.create(
            user=user, phone_number=phone_number, address=address, encrypted_totp_secret=encrypted_secret
        )
        self.validated_data['secret'] = secret
        self.validated_data['issuer'] = ISSUER
        if asset_types:
            wallets = [WalletModel(user=user, asset_type=asset_type) for asset_type in asset_types]
            WalletModel.objects.bulk_create(wallets)
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
