from .models import UserProfileModel, WalletModel
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib import admin


class UserProfileInline(admin.StackedInline):
    model = UserProfileModel
    can_delete = False
    verbose_name_plural = "用戶其他資料"
    fields = ["phone_number", "address"]


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]
    list_display = ["username", "email", "phone_number", "address"]

    @admin.display(description="電話號碼")
    def phone_number(self, obj):
        profile = getattr(obj, "userprofilemodel", None)
        return profile.phone_number if profile else ""

    @admin.display(description="地址")
    def address(self, obj):
        profile = getattr(obj, "userprofilemodel", None)
        return profile.address if profile else ""

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("userprofilemodel")


@admin.register(WalletModel)
class WalletAdmin(admin.ModelAdmin):
    list_display = ["username", "asset_type_code", "available_balance", "frozen_balance"]

    @admin.display(description="使用者名稱")
    def username(self, obj):
        return obj.user.username
    
    @admin.display(description="資產類型")
    def asset_type_code(self, obj):
        return obj.asset_type.code