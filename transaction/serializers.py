from rest_framework import serializers

from .models import OrderModel


class OrderSerializer(serializers.ModelSerializer):
    user = serializers.CharField(source="user.username", read_only=True)
    currency1 = serializers.CharField(source="currency1.code", read_only=True)
    currency2 = serializers.CharField(source="currency2.code", read_only=True)

    class Meta:
        model = OrderModel
        fields = "__all__"


class OrderCreateUpdateSerializer(serializers.ModelSerializer):

    class Meta:
        model = OrderModel
        fields = "__all__"

    def validate(self, data):
        data = super().validate(data)
        if data["currency1"] == data["currency2"]:
            raise serializers.ValidationError("貨幣1和貨幣2不能相同")
        return data
