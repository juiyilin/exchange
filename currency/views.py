from rest_framework.viewsets import ModelViewSet
from .models import CurrencyModel
from .serializers import CurrencySerializer


class CurrencyViewSet(ModelViewSet):
    queryset = CurrencyModel.objects.all()
    serializer_class = CurrencySerializer