from rest_framework import routers
from django.urls import path
from .views import CurrencyViewSet, NoAuthCurrencyListViewSet

router = routers.SimpleRouter()
router.register(r"currency", CurrencyViewSet)

urlpatterns = router.urls
urlpatterns += [
    path('free-list/', NoAuthCurrencyListViewSet.as_view())
]