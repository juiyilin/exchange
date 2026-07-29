from rest_framework.permissions import DjangoModelPermissions, BasePermission
from member.constants import KycStatus


class CustomDjangoModelPermissions(DjangoModelPermissions):
    """
    用於 django model 的權限
    """
    perms_map = {
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'OPTIONS': ['%(app_label)s.view_%(model_name)s'],
        'HEAD': [],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }


class DepositPermission(BasePermission):
    """
    可以入金的權限
    """
    def has_permission(self, request, view):
        """
        Return `True` if permission is granted, `False` otherwise.
        """
        return request.user.has_perm('member.can_deposit')


class ReviewKYCPermission(BasePermission):
    """
    可以審核kyc的權限
    """
    def has_permission(self, request, view):
        """
        Return `True` if permission is granted, `False` otherwise.
        """
        return request.user.has_perm('member.review_kyc')


class KYCApprovedPermission(BasePermission):
    """
    檢查kyc是否已通過
    """
    def has_permission(self, request, view):
        return hasattr(request.user, 'profile') and request.user.profile.latest_kyc_status == KycStatus.APPROVED
    