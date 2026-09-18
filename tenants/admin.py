from django.contrib import admin

from .models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "code",
        "email",
        "telephone",
        "statut",
        "date_creation",
    )

    list_filter = (
        "statut",
    )

    search_fields = (
        "nom",
        "code",
        "email",
    )