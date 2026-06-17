import random

from django.db import transaction
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from common.func import get_random_user_id

from .constants import OrderType
from .models import OrderModel
from .serializers import OrderCreateUpdateSerializer, OrderSerializer
from member.models import WalletModel


class OrderViewSet(ModelViewSet):
    queryset = OrderModel.objects.select_related("user", "currency1", "currency2").all()
    serializer_class = OrderSerializer
    filterset_fields = ['status']

    def required_currency1_amount(self, data):
        # 計算這筆訂單需要凍結的 currency1（出去的貨幣）數量
        if data["order_type"] == OrderType.BUY:
            # 買單：currency1 是計價幣，需付出 數量 × 價格
            return data["amount"] * data["price"]
        # 賣單：currency1 是標的幣，直接付出 數量
        return data["amount"]

    def check_wallet_balance(self, user_id, data):
        wallet = WalletModel.objects.select_for_update().filter(user_id=user_id, asset_type_id=data['currency1']).first()
        if not wallet:
            raise serializers.ValidationError("用戶錢包不存在")
        total = self.required_currency1_amount(data)
        if wallet.available_balance < total:
            raise serializers.ValidationError("用戶可用餘額不足")
        return wallet, total
    
    def transfer_to_frozen(self, wallet, total):
        wallet.available_balance -= total
        wallet.frozen_balance += total
        wallet.save()

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        user_id = get_random_user_id()
        request.data["user"] = user_id  # 隨機分配用戶ID
        serializer = OrderCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        wallet, total = self.check_wallet_balance(
            user_id,
            serializer.validated_data,
        )
        self.transfer_to_frozen(wallet, total)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        # celery 送到撮合市場
        # send_to_match_market.delay()

        return Response(
            {"message": "Order created successfully"}, status=status.HTTP_201_CREATED, headers=headers
        )
