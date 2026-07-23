from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from .models import CurrencyModel
from .serializers import CurrencySerializer
from rest_framework.views import APIView


class NoAuthCurrencyListViewSet(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response(CurrencySerializer(CurrencyModel.objects.all(), many=True).data)

class CurrencyViewSet(ModelViewSet):
    queryset = CurrencyModel.objects.all()
    serializer_class = CurrencySerializer
