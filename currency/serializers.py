from rest_framework import serializers
from .models import CurrencyModel


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = CurrencyModel
        fields = ['id', 'code', 'name']