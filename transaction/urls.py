from rest_framework import routers

from .views import OrderViewSet

router = routers.SimpleRouter()
router.register(r"order", OrderViewSet)
# router.register(r'transaction', TransactionViewSet)

urlpatterns = router.urls
