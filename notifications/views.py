from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from pedagogie.views import _param_entier

from .models import Notification
from .serializers import NotificationSerializer


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "taille"
    max_page_size = 50


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    Notifications de l'utilisateur connecté, les plus récentes d'abord.

    - ?tenant= : celles de cet organisme, plus celles sans organisme ;
    - ?non_lues=1 : seulement les non lues.

    Chacun ne voit que les siennes : celle d'un autre renvoie 404.
    """

    serializer_class = NotificationSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        return Notification.objects.filter(utilisateur=self.request.user)

    def _filtrees(self):
        qs = self.get_queryset()
        tenant_id = _param_entier(self.request, "tenant")
        if tenant_id is not None:
            qs = qs.filter(Q(tenant_id=tenant_id) | Q(tenant__isnull=True))
        if self.request.query_params.get("non_lues") in ("1", "true"):
            qs = qs.filter(lue=False)
        return qs

    def filter_queryset(self, queryset):
        if self.action == "list":
            return self._filtrees()
        return queryset

    @action(detail=False, methods=["get"])
    def compteur(self, request):
        return Response({"non_lues": self._filtrees().filter(lue=False).count()})

    @action(detail=True, methods=["post"])
    def lire(self, request, pk=None):
        notification = self.get_object()
        if not notification.lue:
            notification.lue = True
            notification.date_lecture = timezone.now()
            notification.save(update_fields=["lue", "date_lecture"])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"], url_path="tout-lire")
    def tout_lire(self, request):
        marquees = self._filtrees().filter(lue=False).update(lue=True, date_lecture=timezone.now())
        return Response({"marquees": marquees})
