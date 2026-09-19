from django.shortcuts import get_object_or_404
from rest_framework import viewsets

from tenants.models import Tenant
from .models import (
    Competence,
    CompetenceNiveau,
    Formation,
    Module,
    Niveau,
    Promotion,
)
from .permissions import IsTenantAdminOrSaaSAdmin
from .serializers import (
    CompetenceNiveauSerializer,
    CompetenceSerializer,
    FormationSerializer,
    ModuleSerializer,
    NiveauSerializer,
    PromotionSerializer,
)


class FormationViewSet(viewsets.ModelViewSet):
    serializer_class = FormationSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        return Formation.objects.filter(tenant_id=tenant_id).order_by("nom")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    def perform_create(self, serializer):
        tenant_id = self.kwargs.get("tenant_id")
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer.save(tenant=tenant)


class PromotionViewSet(viewsets.ModelViewSet):
    serializer_class = PromotionSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Promotion.objects.filter(
            formation__tenant_id=tenant_id
        ).select_related("formation").order_by("-date_debut")

        formation_id = self.request.query_params.get("formation")
        if formation_id:
            qs = qs.filter(formation_id=formation_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context


class ModuleViewSet(viewsets.ModelViewSet):
    serializer_class = ModuleSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Module.objects.filter(
            formation__tenant_id=tenant_id
        ).select_related("formation").order_by("ordre", "nom")

        formation_id = self.request.query_params.get("formation")
        if formation_id:
            qs = qs.filter(formation_id=formation_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context


class CompetenceViewSet(viewsets.ModelViewSet):
    serializer_class = CompetenceSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = Competence.objects.filter(
            module__formation__tenant_id=tenant_id
        ).select_related("module", "module__formation").order_by("ordre", "nom")

        module_id = self.request.query_params.get("module")
        if module_id:
            qs = qs.filter(module_id=module_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context


class NiveauViewSet(viewsets.ModelViewSet):
    serializer_class = NiveauSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        return Niveau.objects.filter(tenant_id=tenant_id).order_by("ordre", "nom")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context

    def perform_create(self, serializer):
        tenant_id = self.kwargs.get("tenant_id")
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer.save(tenant=tenant)


class CompetenceNiveauViewSet(viewsets.ModelViewSet):
    serializer_class = CompetenceNiveauSerializer
    permission_classes = [IsTenantAdminOrSaaSAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")
        qs = CompetenceNiveau.objects.filter(
            competence__module__formation__tenant_id=tenant_id
        ).select_related(
            "competence",
            "competence__module",
            "competence__module__formation",
            "niveau",
        ).order_by("niveau__ordre", "competence__ordre")

        competence_id = self.request.query_params.get("competence")
        if competence_id:
            qs = qs.filter(competence_id=competence_id)
        niveau_id = self.request.query_params.get("niveau")
        if niveau_id:
            qs = qs.filter(niveau_id=niveau_id)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["tenant_id"] = self.kwargs.get("tenant_id")
        return context
