"""
註冊到 apps.py 中，只要下 migrate 指令就會更新 group 中的 permission
################################
# 但是 group 改名稱需要另外處理    #
################################
"""

from .constants import Role


ROLES_PERMISSIONS = {
    # view_xxx為可以看所有資料的權限
    Role.TRADER: [],
    Role.SUPPORT: [
        ("auth",   "view_user"),
        ("member", "view_userprofilemodel"),
        ("member", "view_walletmodel"),
    ],
    Role.COMPLIANCE: [
        ("auth",   "view_user"),
        ("member", "view_userprofilemodel"),
        ("member", "review_kyc"),
    ],
    Role.ADMIN: [
        ("auth", "view_user"),
        ("auth", "add_user"),
        ("auth", "change_user"),
        ("auth", "delete_user"),
        ("member", "view_userprofilemodel"),
        ("member", "view_walletmodel"),
        ("member", "can_deposit"),
    ],
}