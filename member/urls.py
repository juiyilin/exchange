from rest_framework import routers

from .views import UserViewSet, WalletViewSet

router = routers.SimpleRouter()
router.register(r"user", UserViewSet)
router.register(r"wallet", WalletViewSet)

urlpatterns = router.urls
