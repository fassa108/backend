from rest_framework import serializers

from .models import Tenant


class TenantSerializer(serializers.ModelSerializer):
    """
    Serializer principal du modèle Tenant.
    """

    class Meta:
        model = Tenant
        fields = [
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

        read_only_fields = [
            "id",
            "code",
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