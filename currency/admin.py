from django.contrib import admin

from .models import CurrencyModel


@admin.register(CurrencyModel)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "created_at", "updated_at")
    search_fields = ("code", "name")
    ordering = ("code",)
    readonly_fields = ("created_at", "updated_at")
