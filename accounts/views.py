from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from drf_spectacular.utils import extend_schema
from django.shortcuts import get_object_or_404

from .serializers import ActivationSerializer, ConnexionResponseSerializer, DemandeResetPasswordSerializer, ResetPasswordSerializer
from .serializers import (
    MembreTenantSerializer,
    AjouterMembreSerializer,
)
from .services import AccountService
from .tokens import ActivationTokenService
from .serializers import ConnexionJWTSerializer

from .models import MembreTenant, Utilisateur
from tenants.models import Tenant

from .permissions import CanManageMembers

@extend_schema(request=ActivationSerializer)
class ActivationView(APIView):
    """
    Permet à un utilisateur invité d'activer son compte.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "activation"

    def post(self, request):
        serializer = ActivationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["token"]
        password = serializer.validated_data["password"]

        # Vérifie que le token existe, n'est pas expiré
        # et n'a pas déjà été utilisé.
        activation_token = ActivationTokenService.get_valid_token(
            token
        )

        # Active le compte et définit le mot de passe.
        utilisateur = AccountService.activer_compte(
            activation_token=activation_token,
            password=password,
        )

        return Response(
            {
                "message": "Votre compte a été activé avec succès.",
                "utilisateur": {
                    "id": utilisateur.id,
                    "nom": utilisateur.nom,
                    "prenom": utilisateur.prenom,
                    "email": utilisateur.email,
                },
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    request=ConnexionJWTSerializer,
    responses={200: ConnexionResponseSerializer},
)
class ConnexionView(TokenObtainPairView):
    """
    Endpoint de connexion de Eduhub.

    Retourne un access token, un refresh token,
    les informations de l'utilisateur connecté
    et ses organismes actifs avec leurs rôles.
    """

    serializer_class = ConnexionJWTSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "connexion"




class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")

        if not refresh_token:
            return Response(
                {"detail": "Le refresh token est obligatoire."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return Response(
                {"detail": "Refresh token invalide ou révoqué."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": "Déconnexion réussie."},
            status=status.HTTP_205_RESET_CONTENT,
        )


@extend_schema(request=DemandeResetPasswordSerializer)
class DemandeResetPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = DemandeResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]

        AccountService.demander_reset_password(
            email=email
        )

        return Response(
            {
                "detail": (
                    "Si un compte associé à cette adresse existe, "
                    "un lien de réinitialisation a été envoyé."
                )
            },
            status=status.HTTP_200_OK,
        )

@extend_schema(request=ResetPasswordSerializer)
class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset_confirm"

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            AccountService.reset_password(
                uid=serializer.validated_data["uid"],
                token=serializer.validated_data["token"],
                password=serializer.validated_data["password"],
            )
        except ValueError as error:
            return Response(
                {"detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": "Votre mot de passe a été réinitialisé avec succès."},
            status=status.HTTP_200_OK,
        )



class MembreTenantViewSet(viewsets.GenericViewSet):
    permission_classes = [CanManageMembers]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        tenant_id = self.kwargs.get("tenant_id")

        return MembreTenant.objects.filter(
            tenant_id=tenant_id
        ).select_related(
            "utilisateur",
            "tenant"
        ).order_by("-date_ajout")

    def get_serializer_class(self):
        if self.action == "create":
            return AjouterMembreSerializer

        return MembreTenantSerializer

    def list(self, request, tenant_id):
        membres = self.get_queryset()
        serializer = MembreTenantSerializer(
            membres,
            many=True
        )
        return Response(serializer.data)

    def retrieve(self, request, tenant_id, pk=None):
        membre = get_object_or_404(
            self.get_queryset(),
            pk=pk
        )

        serializer = MembreTenantSerializer(membre)
        return Response(serializer.data)

    def create(self, request, tenant_id):
        serializer = AjouterMembreSerializer(
            data=request.data,
            context={"tenant_id": tenant_id},
        )
        serializer.is_valid(raise_exception=True)

        tenant = get_object_or_404(
            Tenant,
            pk=tenant_id
        )

        data = serializer.validated_data

        utilisateur = Utilisateur.objects.filter(
            email__iexact=data["email"]
        ).first()

        if utilisateur:
            membre = AccountService.ajouter_membre_existant(
                utilisateur=utilisateur,
                tenant=tenant,
                role=data["role"]
            )
        else:
            utilisateur, token = AccountService.inviter_utilisateur(
                nom=data["nom"],
                prenom=data["prenom"],
                email=data["email"],
                tenant=tenant,
                role=data["role"]
            )

            # L'envoi du mail sera déclenché ici
            # par la tâche Celery existante.

            membre = MembreTenant.objects.get(
                utilisateur=utilisateur,
                tenant=tenant
            )

        response_serializer = MembreTenantSerializer(membre)

        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, tenant_id, pk=None):
        membre = get_object_or_404(
            self.get_queryset(),
            pk=pk
        )

        serializer = MembreTenantSerializer(
            membre,
            data=request.data,
            partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(serializer.data)