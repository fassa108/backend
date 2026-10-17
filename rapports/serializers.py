from rest_framework import serializers

from .models import Rapport


def _nom(utilisateur):
    return f"{utilisateur.prenom} {utilisateur.nom}" if utilisateur else ""


class RapportSerializer(serializers.ModelSerializer):
    type_libelle = serializers.CharField(source="get_type_display", read_only=True)
    promotion_nom = serializers.CharField(source="promotion.nom", read_only=True)
    formation_nom = serializers.CharField(source="promotion.formation.nom", read_only=True)
    apprenant_nom = serializers.SerializerMethodField()
    auteur_nom = serializers.SerializerMethodField()
    valide_par_nom = serializers.SerializerMethodField()

    class Meta:
        model = Rapport
        fields = [
            "id", "type", "type_libelle", "promotion", "promotion_nom", "formation_nom",
            "apprenant", "apprenant_nom", "auteur_nom", "statut", "erreur",
            "valide_par_nom", "date_validation", "partage", "date_creation", "date_modification",
        ]
        read_only_fields = fields

    def get_apprenant_nom(self, obj):
        return _nom(obj.apprenant)

    def get_auteur_nom(self, obj):
        return _nom(obj.auteur)

    def get_valide_par_nom(self, obj):
        return _nom(obj.valide_par)


class RapportDetailSerializer(RapportSerializer):
    class Meta(RapportSerializer.Meta):
        fields = [*RapportSerializer.Meta.fields, "contenu", "donnees"]
        read_only_fields = fields
