from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ConversationViewSet

router = DefaultRouter()
router.register("assistant/conversations", ConversationViewSet, basename="conversation-assistant")

urlpatterns = [
    path(
        "tenants/<int:tenant_id>/",
        include(router.urls),
    ),
]
