from django.urls import path

from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ActivationView,
    ConnexionView,
    LogoutView,
    DemandeResetPasswordView,
    ResetPasswordView,
    MembreTenantViewSet,
)


urlpatterns = [

    # AUTHENTIFICATION

    path(
        "activation/",
        ActivationView.as_view(),
        name="activation",
    ),

    path(
        "connexion/",
        ConnexionView.as_view(),
        name="connexion",
    ),

    path(
        "refresh/",
        TokenRefreshView.as_view(),
        name="refresh",
    ),

    path(
        "logout/",
        LogoutView.as_view(),
        name="logout",
    ),

    path(
        "password-reset/",
        DemandeResetPasswordView.as_view(),
        name="password-reset",
    ),

    path(
        "password-reset/confirm/",
        ResetPasswordView.as_view(),
        name="password-reset-confirm",
    ),


    # MEMBRES D'UN ORGANISME

    path(
        "tenants/<int:tenant_id>/membres/",
        MembreTenantViewSet.as_view({
            "get": "list",
            "post": "create",
        }),
        name="membres-list-create",
    ),

    path(
        "tenants/<int:tenant_id>/membres/<int:pk>/",
        MembreTenantViewSet.as_view({
            "get": "retrieve",
            "patch": "partial_update",
        }),
        name="membre-detail",
    ),

    path(
        "tenants/<int:tenant_id>/membres/<int:pk>/renvoyer-invitation/",
        MembreTenantViewSet.as_view({
            "post": "renvoyer_invitation",
        }),
        name="membre-renvoyer-invitation",
    ),
]