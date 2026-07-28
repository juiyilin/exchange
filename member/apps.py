from django.apps import AppConfig
from django.db.models.signals import post_migrate
from .rbac import ROLES_PERMISSIONS
from django.contrib.auth.management import create_permissions


class MemberConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "member"

    def ready(self):
        # 只要下migrate就會跑sync_roles
        post_migrate.connect(sync_roles, sender=self)

def sync_roles(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission
    from django.apps import apps as global_apps

    # 先確保所有 app 的權限都建好（fresh DB 上 receiver 順序不保證）
    for app_config in global_apps.get_app_configs():
        create_permissions(app_config, verbosity=0)

    for name, perms in ROLES_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=name)
        permissions = [
            Permission.objects.get(content_type__app_label=app, codename=code)
            for app, code in perms
        ]
        group.permissions.set(permissions)
