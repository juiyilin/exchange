import random

from django.db import transaction
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from transaction.tasks import send_to_match_market
from .constants import OrderStatus
from .models import OrderModel
from .serializers import OrderCreateUpdateSerializer, OrderSerializer
from member.models import WalletModel
from ledger.models import LedgerEntryModel


class OrderViewSet(ModelViewSet):
    queryset = OrderModel.objects.select_related("user", "trading_pair").all()
    serializer_class = OrderSerializer
    filterset_fields = ['status']
    http_method_names = ['get', 'post']

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)

    def transfer_to_frozen(self, wallet, total):
        wallet.available_balance -= total
        wallet.frozen_balance += total
        wallet.save()

    def create(self, request, *args, **kwargs):
        user = request.user

        with transaction.atomic():
            serializer = OrderCreateUpdateSerializer(data=request.data, context={'user': user})
            serializer.is_valid(raise_exception=True)

            wallet, required_balance = serializer.context['wallet'], serializer.context['required_balance']

            self.transfer_to_frozen(wallet, required_balance)
            order = serializer.save()
            LedgerEntryModel.objects.create_order_ledgers(wallet, required_balance, order)

            headers = self.get_success_headers(serializer.data)
        # commit 後 celery 才送到撮合市場。如果 with 報錯，後續都不會執行
        send_to_match_market.delay(order.id, order.trading_pair.id)

        return Response(
            {"message": "Order created successfully"}, status=status.HTTP_201_CREATED, headers=headers
        )

    @action(methods=['post'], detail=True)
    @transaction.atomic
    def cancel(self, request, pk=None):
        """
        取消
        1. 全部成交、已取消狀態的單子不能取消
        2. 把單子改成已取消後，處理多餘的凍結餘額
        """
        try:
            instance = OrderModel.objects.select_for_update().get(id=pk, user=request.user)
        except OrderModel.DoesNotExist:
            raise serializers.ValidationError('該單不存在')
        if instance.status in [OrderStatus.FULLY_FILLED, OrderStatus.CANCELED]:
            raise serializers.ValidationError(f'本單{instance.get_status_display()}，無法取消')

        instance.status = OrderStatus.CANCELED
        instance.save()

        WalletModel.objects.release_frozen(instance)
        return Response({"message": "Order canceled successfully"})
