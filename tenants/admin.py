from django.contrib import admin

from .models import DemandeInscription, Paiement, Tenant


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


class PaiementInline(admin.StackedInline):
    model = Paiement
    extra = 0
    can_delete = False


@admin.register(DemandeInscription)
class DemandeInscriptionAdmin(admin.ModelAdmin):
    inlines = [PaiementInline]

    list_display = (
        "nom_organisme",
        "email",
        "statut",
        "date_creation",
    )

    list_filter = (
        "statut",
    )

    search_fields = (
        "nom_organisme",
        "email",
    )
