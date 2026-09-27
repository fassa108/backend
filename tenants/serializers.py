from rest_framework import serializers

from accounts.models import MembreTenant, Utilisateur

from .models import Tenant


TENANT_FIELDS = [
    "id",
    "nom",
    "code",
    "description",
    "email",
    "telephone",
    "adresse",
    "site_web",
    "statut",
    "date_creation",
    "date_modification",
]


class TenantSerializer(serializers.ModelSerializer):
    """
    Serializer principal du modèle Tenant.

    Utilisé par l'admin d'organisme pour modifier les informations
    de son organisme. Le statut n'est modifiable que par l'admin SaaS
    (voir TenantStatutSerializer).
    """

    class Meta:
        model = Tenant
        fields = TENANT_FIELDS

        read_only_fields = [
            "id",
            "code",
            "statut",
            "date_creation",
            "date_modification",
        ]

    def validate_nom(self, value):
        """
        Vérifie que le nom de l'organisme est unique.
        """

        queryset = Tenant.objects.filter(
            nom__iexact=value.strip()
        )

        # Lors d'une modification, on exclut l'organisme actuel.
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                "Un organisme portant ce nom existe déjà."
            )

        return value.strip()


class TenantStatutSerializer(serializers.ModelSerializer):
    """
    Seule modification autorisée à l'admin SaaS :
    suspendre ou réactiver un organisme.
    """

    class Meta:
        model = Tenant
        fields = ["statut"]
        extra_kwargs = {"statut": {"required": True}}


class TenantCreationSerializer(TenantSerializer):
    """
    Création d'un organisme par l'admin SaaS, avec son premier
    administrateur.

    Si l'email correspond à un compte existant, ce compte devient
    administrateur de l'organisme. Sinon un compte est créé et une
    invitation est envoyée : nom et prénom sont alors obligatoires.
    """

    admin_email = serializers.EmailField(write_only=True)
    admin_nom = serializers.CharField(
        max_length=100,
        required=False,
        write_only=True,
    )
    admin_prenom = serializers.CharField(
        max_length=100,
        required=False,
        write_only=True,
    )

    class Meta(TenantSerializer.Meta):
        fields = TENANT_FIELDS + [
            "admin_email",
            "admin_nom",
            "admin_prenom",
        ]

    def validate_admin_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        attrs = super().validate(attrs)

        existe = Utilisateur.objects.filter(
            email__iexact=attrs["admin_email"]
        ).exists()

        if not existe:
            if not attrs.get("admin_nom"):
                raise serializers.ValidationError({
                    "admin_nom": "Le nom est obligatoire pour un nouvel utilisateur."
                })

            if not attrs.get("admin_prenom"):
                raise serializers.ValidationError({
                    "admin_prenom": "Le prénom est obligatoire pour un nouvel utilisateur."
                })

        return attrs


class IndicateursTenantSerializer(serializers.Serializer):
    nb_administrateurs = serializers.IntegerField()
    nb_formateurs = serializers.IntegerField()
    nb_apprenants = serializers.IntegerField()
    nb_formations = serializers.IntegerField()
    nb_promotions = serializers.IntegerField()


class AdministrateurTenantSerializer(serializers.ModelSerializer):
    nom = serializers.CharField(source="utilisateur.nom")
    prenom = serializers.CharField(source="utilisateur.prenom")
    email = serializers.EmailField(source="utilisateur.email")
    compte_active = serializers.BooleanField(source="utilisateur.actif")

    class Meta:
        model = MembreTenant
        fields = ["nom", "prenom", "email", "compte_active", "actif"]


class TenantDetailSerializer(serializers.ModelSerializer):
    """
    Fiche d'un organisme : informations, indicateurs et administrateurs.

    Les indicateurs sont annotés sur le queryset
    (voir TenantViewSet.get_queryset).
    """

    indicateurs = serializers.SerializerMethodField()
    administrateurs = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = TENANT_FIELDS + ["indicateurs", "administrateurs"]
        read_only_fields = fields

    def get_indicateurs(self, obj):
        return IndicateursTenantSerializer({
            champ: getattr(obj, champ, 0)
            for champ in IndicateursTenantSerializer().fields
        }).data

    def get_administrateurs(self, obj):
        admins = [
            membre
            for membre in obj.membres.all()
            if membre.role == MembreTenant.Role.ADMINISTRATEUR
        ]
        return AdministrateurTenantSerializer(admins, many=True).data


class IndicateursGlobauxSerializer(serializers.Serializer):
    nb_organismes = serializers.IntegerField()
    nb_organismes_actifs = serializers.IntegerField()
    nb_organismes_suspendus = serializers.IntegerField()
    nb_utilisateurs = serializers.IntegerField()
    nb_formations = serializers.IntegerField()
    nb_promotions = serializers.IntegerField()
