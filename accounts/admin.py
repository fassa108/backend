from django.contrib import admin

from .models import Utilisateur, MembreTenant


@admin.register(Utilisateur)
class UtilisateurAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "nom",
        "prenom",
        "actif",
        "est_admin_saas",
        "is_staff",
        "is_superuser",
        "date_creation",
    )

    list_filter = (
        "actif",
        "est_admin_saas",
        "is_staff",
        "is_superuser",
    )

    search_fields = (
        "email",
        "nom",
        "prenom",
    )


@admin.register(MembreTenant)
class MembreTenantAdmin(admin.ModelAdmin):
    list_display = (
        "utilisateur",
        "tenant",
        "role",
        "actif",
        "date_ajout",
    )

    list_filter = (
        "role",
        "actif",
    )

    search_fields = (
        "utilisateur__email",
        "utilisateur__nom",
        "utilisateur__prenom",
        "tenant__nom",
    )