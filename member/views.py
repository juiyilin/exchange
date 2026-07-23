from django.contrib.auth.models import User
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.mixins import CreateModelMixin, UpdateModelMixin
from rest_framework.viewsets import ModelViewSet, GenericViewSet
from rest_framework import serializers
from rest_framework.permissions import IsAdminUser
from .models import WalletModel, UserProfileModel
from .serializers import LoginSerializer, RegisterSerializer, TwoFactorEnableSerializer, UserListSerializer, WalletSerializer, WithdrawSerializer, DepositSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from ledger.models import LedgerEntryModel


class RegisterView(CreateModelMixin, UpdateModelMixin, GenericViewSet):
    """
    註冊流程
    1. 先輸入基本資料註冊，回傳2FA的資訊(create)
    2. 使用者在驗證app新增2FA資訊後，將app中顯示的6位數數字與使用者帳號傳到後端啟用(update)
    """
    queryset = UserProfileModel.objects.all()
    serializer_class = RegisterSerializer
    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """輸入基本資料註冊，回傳2FA的資訊，預先建立勾選錢包"""
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """使用者在驗證app新增2FA資訊後，將app中顯示的6位數數字與使用者帳號傳到後端啟用"""
        serializer = TwoFactorEnableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response({"message": "2FA enabled"})


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer


class UserViewSet(ModelViewSet):
    permission_classes = [IsAdminUser]
    queryset = User.objects.select_related("profile").all()
    serializer_class = UserListSerializer


class WalletViewSet(ModelViewSet):
    queryset = WalletModel.objects.select_related("user", "asset_type").all()
    serializer_class = WalletSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)

    def perform_create(self, serializer):
        # 這裡可以添加一些創建錢包前的邏輯，例如檢查用戶是否存在等
        user = self.request.user
        serializer.save(user=user)

    @action(methods=['post'], detail=False, permission_classes=[IsAdminUser])
    @transaction.atomic
    def deposit(self, request):
        """admin user幫user入金，用於範圍一(沒有串鏈)測試"""
        serializer = DepositSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        wallet, _ = WalletModel.objects.select_for_update().get_or_create(user_id=serializer.validated_data['user_id'], asset_type_id=serializer.validated_data['asset_type_id'])

        wallet.available_balance += serializer.validated_data['quantity']
        wallet.save()
        LedgerEntryModel.objects.create_deposit_ledgers(wallet, serializer.validated_data['quantity'])
        return Response(WalletSerializer(wallet).data)

    @action(methods=['post'], detail=False)
    @transaction.atomic
    def withdraw(self, request):
        """出金"""
        serializer = WithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        try:
            wallet = WalletModel.objects.select_for_update().get(user=user, asset_type_id=serializer.validated_data['asset_type_id'])
        except WalletModel.DoesNotExist:
            raise serializers.ValidationError('錢包不存在')
        if wallet.available_balance < serializer.validated_data['quantity']:
            raise serializers.ValidationError('餘額不足')
        wallet.available_balance -= serializer.validated_data['quantity']
        wallet.save()
        LedgerEntryModel.objects.create_withdraw_ledgers(wallet, serializer.validated_data['quantity'])
        return Response(WalletSerializer(wallet).data)