from django.contrib.auth.models import User
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework import serializers

from common.func import get_random_user
from .models import WalletModel
from .serializers import UserCreateUpdateSerializer, UserListSerializer, WalletSerializer, WithdrawSerializer


class UserViewSet(ModelViewSet):
    """
    View to list all users in the system.

    * Requires token authentication.
    * Only admin users are able to access this view.
    """

    queryset = User.objects.select_related("userprofilemodel").all()
    serializer_class = UserListSerializer

    def create(self, request, *args, **kwargs):
        serializer = UserCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            self.serializer_class(serializer.instance).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )


class WalletViewSet(ModelViewSet):
    queryset = WalletModel.objects.select_related("user", "asset_type").all()
    serializer_class = WalletSerializer

    def perform_create(self, serializer):
        # 這裡可以添加一些創建錢包前的邏輯，例如檢查用戶是否存在等
        user = get_random_user()
        # user = self.request.user
        serializer.save(user=user)

    @action(methods=['post'], detail=False)
    @transaction.atomic
    def withdraw(self, request):
        """出金"""
        serializer = WithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = get_random_user()
        try:
            wallet = WalletModel.objects.select_for_update().get(user=user, asset_type_id=serializer.validated_data['asset_type_id'])
        except WalletModel.DoesNotExist:
            raise serializers.ValidationError('錢包不存在')
        if wallet.available_balance < serializer.validated_data['quantity']:
            raise serializers.ValidationError('餘額不足')
        wallet.available_balance -= serializer.validated_data['quantity']
        wallet.save()
        return Response(WalletSerializer(wallet).data)