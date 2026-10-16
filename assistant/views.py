from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from activites import progression
from activites.models import Brief
from activites.views import EstApprenantOrganisme

from .contexte import _assignation
from .models import LONGUEUR_MAX_QUESTION, Conversation, Message
from .serializers import ConversationDetailSerializer, ConversationSerializer
from .tasks import repondre

# Protège le quota du LLM : questions par apprenant et par jour
QUESTIONS_PAR_JOUR = 30
LONGUEUR_TITRE = 80


class ApprenantAssistant(EstApprenantOrganisme):
    message = "L'assistant est réservé aux apprenants."


class ConversationViewSet(viewsets.ModelViewSet):
    """
    Conversations de l'apprenant connecté avec l'assistant (privées : ni le
    formateur ni l'admin ne les voient).

    - POST {brief?} : nouvelle conversation, sur un brief qui lui est assigné ;
    - POST <id>/messages/ {contenu} : question ; la réponse arrive en tâche de
      fond (message « EN_COURS », à relire jusqu'à « PRET » ou « ECHEC »).
    """

    permission_classes = [ApprenantAssistant]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        qs = Conversation.objects.filter(
            apprenant=self.request.user,
            promotion__formation__tenant_id=self.kwargs["tenant_id"],
        ).select_related("brief")
        if self.action != "list":
            qs = qs.prefetch_related("messages")
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return ConversationSerializer
        return ConversationDetailSerializer

    def create(self, request, tenant_id=None):
        inscription = progression.inscription_active(request.user, tenant_id)
        if inscription is None:
            raise ValidationError({"detail": "Vous n'êtes inscrit à aucune promotion ouverte."})

        brief = None
        brief_id = request.data.get("brief")
        if brief_id not in (None, ""):
            brief = Brief.objects.filter(
                pk=brief_id,
                promotion=inscription.promotion,
                statut__in=[Brief.Statut.PUBLIE, Brief.Statut.ARCHIVE],
            ).first()
            if brief is None or _assignation(request.user, brief) is None:
                raise NotFound("Brief introuvable.")

        conversation = Conversation.objects.create(
            apprenant=request.user, promotion=inscription.promotion, brief=brief
        )
        return Response(ConversationDetailSerializer(conversation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def messages(self, request, tenant_id=None, pk=None):
        conversation = self.get_object()
        contenu = str(request.data.get("contenu") or "").strip()
        if not contenu:
            raise ValidationError({"contenu": "Écrivez votre question."})
        if len(contenu) > LONGUEUR_MAX_QUESTION:
            raise ValidationError({"contenu": f"{LONGUEUR_MAX_QUESTION} caractères au plus."})
        if conversation.messages.filter(statut=Message.Statut.EN_COURS).exists():
            raise ValidationError({"detail": "Attendez la réponse de l'assistant avant de poser une autre question."})

        debut_du_jour = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        posees = Message.objects.filter(
            conversation__apprenant=request.user, role=Message.Role.APPRENANT, date_creation__gte=debut_du_jour
        ).count()
        if posees >= QUESTIONS_PAR_JOUR:
            return Response(
                {"detail": f"Vous avez atteint la limite de {QUESTIONS_PAR_JOUR} questions par jour. À demain !"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        with transaction.atomic():
            Message.objects.create(conversation=conversation, role=Message.Role.APPRENANT, contenu=contenu)
            reponse = Message.objects.create(
                conversation=conversation, role=Message.Role.ASSISTANT, statut=Message.Statut.EN_COURS
            )
            if not conversation.titre:
                conversation.titre = contenu[:LONGUEUR_TITRE]
            conversation.save()  # date de modification : remonte en tête de liste
            transaction.on_commit(lambda: repondre.delay(reponse.id))

        conversation = self.get_queryset().get(pk=conversation.pk)
        return Response(ConversationDetailSerializer(conversation).data, status=status.HTTP_201_CREATED)
