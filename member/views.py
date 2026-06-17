from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from common.func import get_random_user_id
from .models import WalletModel
from .serializers import UserCreateUpdateSerializer, UserListSerializer, WalletSerializer


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
        user_id = get_random_user_id()
        serializer.save(user_id=user_id)