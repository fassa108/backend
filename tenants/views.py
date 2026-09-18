from rest_framework import viewsets

from .models import Tenant
from .serializers import TenantSerializer
from .permissions import IsAdminSaaS, IsAdminOrganisme


class TenantViewSet(viewsets.ModelViewSet):
    queryset = Tenant.objects.all().order_by("-date_creation")
    serializer_class = TenantSerializer

    # On garde GET, POST, PATCH et DELETE.
    # PUT n'est pas exposé.
    http_method_names = ["get", "post", "patch", "delete"]

    def get_permissions(self):

        # DELETE → Admin SaaS uniquement
        if self.action == "destroy":
            permission_classes = [IsAdminSaaS]

        # Consultation d'un organisme
        elif self.action == "retrieve":
            permission_classes = [
                IsAdminSaaS | IsAdminOrganisme
            ]

        # Modification partielle
        elif self.action == "partial_update":
            permission_classes = [
                IsAdminSaaS | IsAdminOrganisme
            ]

        # Liste → Admin SaaS uniquement
        elif self.action == "list":
            permission_classes = [IsAdminSaaS]

        # Création via le CRUD → Admin SaaS uniquement.
        # L'inscription publique aura son propre endpoint.
        elif self.action == "create":
            permission_classes = [IsAdminSaaS]

        else:
            permission_classes = [IsAdminSaaS]

        return [permission() for permission in permission_classes]