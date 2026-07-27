from rest_framework import routers
from rest_framework_simplejwt.views import TokenRefreshView
from django.urls import path
from .views import UserViewSet, WalletViewSet, LoginView, RegisterView, KYCViewSet


router = routers.SimpleRouter()
router.register(r"user", UserViewSet)
router.register(r"wallet", WalletViewSet)
router.register(r'kyc', KYCViewSet)


urlpatterns = [
    path('register/', RegisterView.as_view({
        'post': 'create',
        'put': 'update'
    })),
    path('login/', LoginView.as_view()),
    path('token/refresh/', TokenRefreshView.as_view()),
] + router.urls
