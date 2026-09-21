from rest_framework import status, viewsets
from rest_framework.response import Response

from tenants.models import Tenant

from .models import Assignation, Brief, RessourceBrief
from .serializers import (
    AssignationSerializer,
    BriefSerializer,
    RessourceBriefSerializer,
)
from pedagogie.permissions import IsTenantAdminOrSaaSAdmin


class BriefViewSet(viewsets.ModelViewSet):
    serializer_class = BriefSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Brief.objects.filter(
            promotion__formation__tenant_id=self.kwargs["tenant_id"]
        ).select_related(
            "promotion",
            "promotion__formation",
        ).prefetch_related(
            "competences",
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        return context


class RessourceBriefViewSet(viewsets.ModelViewSet):
    serializer_class = RessourceBriefSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return RessourceBrief.objects.filter(
            brief__promotion__formation__tenant_id=self.kwargs["tenant_id"]
        ).select_related(
            "brief",
            "brief__promotion",
            "brief__promotion__formation",
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        return context

    def perform_create(self, serializer):
        tenant_id = self.kwargs["tenant_id"]

        brief = serializer.validated_data["brief"]

        if brief.promotion.formation.tenant_id != tenant_id:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Ce brief n'appartient pas à cet organisme."
            )

        serializer.save()


class AssignationViewSet(viewsets.ModelViewSet):
    serializer_class = AssignationSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Assignation.objects.filter(
            brief__promotion__formation__tenant_id=self.kwargs["tenant_id"]
        ).select_related(
            "brief",
            "brief__promotion",
            "brief__promotion__formation",
            "groupe",
            "apprenant",
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs["tenant_id"]
        return context