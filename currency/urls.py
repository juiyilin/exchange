from rest_framework import routers

from .views import CurrencyViewSet

router = routers.SimpleRouter()
router.register(r"currency", CurrencyViewSet)

urlpatterns = router.urls