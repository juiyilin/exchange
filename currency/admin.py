from django.contrib import admin

from .models import CurrencyModel, TradingPairModel


@admin.register(CurrencyModel)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "created_at", "updated_at")
    search_fields = ("code", "name")
    ordering = ("code",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(TradingPairModel)
class TradingPairAdmin(admin.ModelAdmin):
    list_display = ("symbol", "is_active")
    readonly_fields = ("created_at", "updated_at", "symbol")