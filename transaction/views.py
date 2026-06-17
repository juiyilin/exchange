import random

from django.db import transaction
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from common.func import get_random_user

from .constants import OrderType
from .models import OrderModel
from .serializers import OrderCreateUpdateSerializer, OrderSerializer
from member.models import WalletModel


class OrderViewSet(ModelViewSet):
    queryset = OrderModel.objects.select_related("user", "trading_pair").all()
    serializer_class = OrderSerializer
    filterset_fields = ['status']

    def transfer_to_frozen(self, wallet, total):
        wallet.available_balance -= total
        wallet.frozen_balance += total
        wallet.save()

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        user = get_random_user()
        # user = request.user
        serializer = OrderCreateUpdateSerializer(data=request.data, context={'user': user})
        serializer.is_valid(raise_exception=True)

        wallet, required_balance = serializer.context['wallet'], serializer.context['required_balance']
        self.transfer_to_frozen(wallet, required_balance)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        # celery 送到撮合市場
        # send_to_match_market.delay()

        return Response(
            {"message": "Order created successfully"}, status=status.HTTP_201_CREATED, headers=headers
        )
