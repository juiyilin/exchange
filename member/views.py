from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.mixins import CreateModelMixin, UpdateModelMixin
from rest_framework.viewsets import ModelViewSet, GenericViewSet
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from common.permissions import CustomDjangoModelPermissions, DepositPermission, KYCApprovedPermission, ReviewKYCPermission
from member.serializers.user_kyc import KYCApproveSerializer, KYCRetrieveSerializer
from .models import WalletModel, UserProfileModel, KycRecordModel
from member.serializers import KYCCreateSerializer, KYCListSerializer, KYCReasonSerializer, UserListSerializer, WalletSerializer, WithdrawSerializer, DepositSerializer, LoginSerializer, RegisterSerializer, TwoFactorEnableSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from ledger.models import LedgerEntryModel, ReasonType
from .constants import KYC_TIER_DAILY_LIMIT, KycStatus, KycEvent


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
    permission_classes = [CustomDjangoModelPermissions]
    queryset = User.objects.select_related("profile").all()
    serializer_class = UserListSerializer


class WalletViewSet(ModelViewSet):
    queryset = WalletModel.objects.select_related("user", "asset_type").all()
    serializer_class = WalletSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.has_perm('member.view_walletmodel'):
            return queryset
        return queryset.filter(user=self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(user=user)

    @action(methods=['post'], detail=False, permission_classes=[DepositPermission])
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

    def check_daily_withdraw_limit(self, withdraw_amount):
        """檢查是否達到出金上限"""
        daily_withdraw_limit = KYC_TIER_DAILY_LIMIT[self.request.user.profile.kyc_tier]
        if daily_withdraw_limit is None:
            return
        if withdraw_amount <= 0:
            raise PermissionDenied(f'尚未設定匯率')
        today = timezone.localdate()
        current_withdraw = LedgerEntryModel.objects.get_user_total_amount(user=self.request.user, reason=ReasonType.WITHDRAW, date_from=today)
        if (current_withdraw + withdraw_amount) > daily_withdraw_limit:
            raise PermissionDenied(f'本次出金超過每日出金額度 {daily_withdraw_limit}')


    @action(methods=['post'], detail=False, permission_classes=[KYCApprovedPermission])
    @transaction.atomic
    def withdraw(self, request):
        """
        出金
        出金前需檢查
        1. 錢包是否存在
        2. 是否已設定法幣兌換匯率
        3. 餘額是否足夠
        4. 是否達到出金上限
        """
        serializer = WithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        try:
            wallet = WalletModel.objects.select_for_update().get(user=user, asset_type_id=serializer.validated_data['asset_type_id'])
        except WalletModel.DoesNotExist:
            raise serializers.ValidationError('錢包不存在')
        if wallet.available_balance < serializer.validated_data['quantity']:
            raise serializers.ValidationError('餘額不足')

        self.check_daily_withdraw_limit(wallet.asset_type.fiat_rate * serializer.validated_data['quantity'])
        
        wallet.available_balance -= serializer.validated_data['quantity']
        wallet.save()
        LedgerEntryModel.objects.create_withdraw_ledgers(wallet, serializer.validated_data['quantity'])
        return Response(WalletSerializer(wallet).data)


class KYCViewSet(ModelViewSet):
    queryset = UserProfileModel.objects.select_related('user').all()
    lookup_field = 'user_id'  # for self.get_object
    http_method_names = ['get', 'post']

    def get_permissions(self):
        if self.action in ['create', 'me']:
            return [IsAuthenticated()]
        if self.action in ['approve', 'reject', 'revoke', 'reverify']:
            return [ReviewKYCPermission()]
        return [CustomDjangoModelPermissions()]

    def get_serializer_class(self):
        if self.action in ['list']:
            return KYCListSerializer
        if self.action in ['me', 'retrieve']:
            return KYCRetrieveSerializer
        if self.action == 'create':
            return KYCCreateSerializer
        if self.action in ['reject', 'revoke', 'reverify']:
            return KYCReasonSerializer
        return KYCApproveSerializer

    @action(detail=False)
    def me(self, request):
        """自己的kyc資料"""
        try:
            instance = self.queryset.get(user=request.user)
        except UserProfileModel.DoesNotExist as e:
            raise serializers.ValidationError(e)
        return Response(self.get_serializer(instance).data)

    def check_latest_kyc_status(self, status, profile):
        if self.kyc_status_type(status)['can_do_status'] != profile.latest_kyc_status:
            raise serializers.ValidationError(f'使用者當前kyc狀態為{profile.get_latest_kyc_status_display()}無法執行')

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """提交kyc申請"""
        try:
            instance = self.queryset.get(user=request.user)
        except UserProfileModel.DoesNotExist as e:
            raise serializers.ValidationError(e)

        if instance.latest_kyc_status != KycStatus.UNVERIFIED and instance.latest_kyc_status != KycStatus.REJECTED:
            raise serializers.ValidationError(f'使用者當前kyc狀態為{instance.get_latest_kyc_status_display()}無法執行')

        partial = kwargs.pop('partial', False)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        serializer.save(latest_kyc_status=KycStatus.VERIFYING)
        KycRecordModel.objects.create(user=request.user, operator=request.user, event_status=KycEvent.SUBMITTED, **serializer.validated_data)

        headers = self.get_success_headers(serializer.data)
        return Response(status=status.HTTP_201_CREATED, headers=headers)

    def get_base_kyc_record_data(self, profile, status):
        kyc_record_data = {
            'user': profile.user,
            'operator': self.request.user,
            'event_status': status,
            'legal_name': profile.legal_name,
            'id_number': profile.id_number,
            'birth_date': profile.birth_date,
            'nationality': profile.nationality,
        }
        return kyc_record_data

    def kyc_status_type(self, status):
        kyc_type = {
            'approve': {'can_do_status': KycStatus.VERIFYING ,'status': KycStatus.APPROVED, 'event': KycEvent.APPROVED},
            'reject': {'can_do_status': KycStatus.VERIFYING ,'status': KycStatus.REJECTED, 'event': KycEvent.REJECTED},
            'revoke': {'can_do_status': KycStatus.APPROVED ,'status': KycStatus.UNVERIFIED, 'event': KycEvent.REVOKED},
            'reverify': {'can_do_status': KycStatus.APPROVED ,'status': KycStatus.UNVERIFIED, 'event': KycEvent.REVERIFY_REQUIRED},
        }
        return kyc_type[status]

    def do_kyc(self, status, validated_data={}):
        instance = self.get_object()
        self.check_latest_kyc_status(self.action, instance)

        instance.latest_kyc_status = self.kyc_status_type(status)['status']
        if status == 'approve' and 'kyc_tier' in validated_data:
            instance.kyc_tier = validated_data.pop('kyc_tier')
        instance.save()

        kyc_record_data = self.get_base_kyc_record_data(instance, self.kyc_status_type(status)['event'])
        if validated_data:
            kyc_record_data.update(validated_data)
        KycRecordModel.objects.create(**kyc_record_data)

    @action(methods=['POST'], detail=True)
    @transaction.atomic
    def approve(self, request, *args, **kwargs):
        """核可"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.do_kyc(self.action, serializer.validated_data)
        return Response(status=status.HTTP_200_OK)

    @action(methods=['POST'], detail=True)
    @transaction.atomic
    def reject(self, request, *args, **kwargs):
        """
        拒絕
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.do_kyc(self.action, serializer.validated_data)
        return Response(status=status.HTTP_200_OK)

    @action(methods=['POST'], detail=True)
    @transaction.atomic
    def revoke(self, request, *args, **kwargs):
        """
        撤銷: 風控/懲罰性作廢(詐欺/盜用/制裁)
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.do_kyc(self.action, serializer.validated_data)
        return Response(status=status.HTTP_200_OK)

    @action(methods=['POST'], detail=True)
    @transaction.atomic
    def reverify(self, request, *args, **kwargs):
        """
        要求重驗（例行覆審)
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.do_kyc(self.action, serializer.validated_data)
        return Response(status=status.HTTP_200_OK)
