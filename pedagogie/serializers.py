from rest_framework import serializers

from .models import (
    Competence,
    CompetenceNiveau,
    Formation,
    Module,
    Niveau,
    Promotion,
)


class FormationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Formation
        fields = [
            "id",
            "tenant",
            "nom",
            "description",
            "actif",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "tenant",
            "date_creation",
            "date_modification",
        ]

    def validate_nom(self, value):
        nom = value.strip()
        tenant_id = self.context.get("tenant_id")

        qs = Formation.objects.filter(tenant_id=tenant_id, nom__iexact=nom)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError(
                "Une formation avec ce nom existe déjà pour cet organisme."
            )
        return nom


class PromotionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Promotion
        fields = [
            "id",
            "formation",
            "nom",
            "description",
            "date_debut",
            "date_fin",
            "actif",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "date_creation",
            "date_modification",
        ]

    def validate(self, attrs):
        tenant_id = self.context.get("tenant_id")
        formation = attrs.get("formation") or (
            self.instance.formation if self.instance else None
        )

        # Validation parent-enfant : la formation doit appartenir au tenant actif
        if formation and tenant_id and str(formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError(
                {"formation": "La formation sélectionnée n'appartient pas à cet organisme."}
            )

        # Unicité du nom par formation
        nom = attrs.get("nom")
        if nom is not None:
            nom = nom.strip()
            attrs["nom"] = nom
        elif self.instance:
            nom = self.instance.nom

        if formation and nom:
            qs = Promotion.objects.filter(formation=formation, nom__iexact=nom)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"nom": "Une promotion avec ce nom existe déjà pour cette formation."}
                )

        # Validation chronologique des dates
        date_debut = attrs.get("date_debut") or (
            self.instance.date_debut if self.instance else None
        )
        date_fin = (
            attrs.get("date_fin")
            if "date_fin" in attrs
            else (self.instance.date_fin if self.instance else None)
        )

        if date_debut and date_fin and date_fin < date_debut:
            raise serializers.ValidationError(
                {"date_fin": "La date de fin doit être postérieure ou égale à la date de début."}
            )

        return attrs


class ModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Module
        fields = [
            "id",
            "formation",
            "nom",
            "description",
            "ordre",
            "actif",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "date_creation",
            "date_modification",
        ]

    def validate(self, attrs):
        tenant_id = self.context.get("tenant_id")
        formation = attrs.get("formation") or (
            self.instance.formation if self.instance else None
        )

        # Validation parent-enfant : la formation doit appartenir au tenant actif
        if formation and tenant_id and str(formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError(
                {"formation": "La formation sélectionnée n'appartient pas à cet organisme."}
            )

        # Validation ordre >= 1
        ordre = (
            attrs.get("ordre")
            if "ordre" in attrs
            else (self.instance.ordre if self.instance else None)
        )
        if ordre is not None and ordre < 1:
            raise serializers.ValidationError(
                {"ordre": "L'ordre doit être supérieur ou égal à 1."}
            )

        # Unicité du nom par formation
        nom = attrs.get("nom")
        if nom is not None:
            nom = nom.strip()
            attrs["nom"] = nom
        elif self.instance:
            nom = self.instance.nom

        if formation and nom:
            qs_nom = Module.objects.filter(formation=formation, nom__iexact=nom)
            if self.instance:
                qs_nom = qs_nom.exclude(pk=self.instance.pk)
            if qs_nom.exists():
                raise serializers.ValidationError(
                    {"nom": "Un module avec ce nom existe déjà pour cette formation."}
                )

        # Unicité de l'ordre par formation
        if formation and ordre is not None:
            qs_ordre = Module.objects.filter(formation=formation, ordre=ordre)
            if self.instance:
                qs_ordre = qs_ordre.exclude(pk=self.instance.pk)
            if qs_ordre.exists():
                raise serializers.ValidationError(
                    {"ordre": "Un module avec cet ordre existe déjà pour cette formation."}
                )

        return attrs


class CompetenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Competence
        fields = [
            "id",
            "module",
            "nom",
            "description",
            "ordre",
            "actif",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "date_creation",
            "date_modification",
        ]

    def validate(self, attrs):
        tenant_id = self.context.get("tenant_id")
        module = attrs.get("module") or (
            self.instance.module if self.instance else None
        )

        # Validation parent-enfant : le module doit appartenir au tenant actif
        if module and tenant_id and str(module.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError(
                {"module": "Le module sélectionné n'appartient pas à cet organisme."}
            )

        # Validation ordre >= 1
        ordre = (
            attrs.get("ordre")
            if "ordre" in attrs
            else (self.instance.ordre if self.instance else None)
        )
        if ordre is not None and ordre < 1:
            raise serializers.ValidationError(
                {"ordre": "L'ordre doit être supérieur ou égal à 1."}
            )

        # Unicité du nom par module
        nom = attrs.get("nom")
        if nom is not None:
            nom = nom.strip()
            attrs["nom"] = nom
        elif self.instance:
            nom = self.instance.nom

        if module and nom:
            qs_nom = Competence.objects.filter(module=module, nom__iexact=nom)
            if self.instance:
                qs_nom = qs_nom.exclude(pk=self.instance.pk)
            if qs_nom.exists():
                raise serializers.ValidationError(
                    {"nom": "Une compétence avec ce nom existe déjà pour ce module."}
                )

        # Unicité de l'ordre par module
        if module and ordre is not None:
            qs_ordre = Competence.objects.filter(module=module, ordre=ordre)
            if self.instance:
                qs_ordre = qs_ordre.exclude(pk=self.instance.pk)
            if qs_ordre.exists():
                raise serializers.ValidationError(
                    {"ordre": "Une compétence avec cet ordre existe déjà pour ce module."}
                )

        return attrs


class NiveauSerializer(serializers.ModelSerializer):
    class Meta:
        model = Niveau
        fields = [
            "id",
            "tenant",
            "nom",
            "description",
            "ordre",
            "actif",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "tenant",
            "date_creation",
            "date_modification",
        ]

    def validate(self, attrs):
        tenant_id = self.context.get("tenant_id")

        # Validation ordre >= 1
        ordre = (
            attrs.get("ordre")
            if "ordre" in attrs
            else (self.instance.ordre if self.instance else None)
        )
        if ordre is not None and ordre < 1:
            raise serializers.ValidationError(
                {"ordre": "L'ordre doit être supérieur ou égal à 1."}
            )

        # Nettoyage et validation du nom
        nom = attrs.get("nom")
        if nom is not None:
            nom = nom.strip()
            attrs["nom"] = nom
        elif self.instance:
            nom = self.instance.nom

        # Unicité du nom insensible à la casse au sein du tenant
        if tenant_id and nom:
            qs_nom = Niveau.objects.filter(tenant_id=tenant_id, nom__iexact=nom)
            if self.instance:
                qs_nom = qs_nom.exclude(pk=self.instance.pk)
            if qs_nom.exists():
                raise serializers.ValidationError(
                    {"nom": "Un niveau avec ce nom existe déjà pour cet organisme."}
                )

        # Unicité de l'ordre au sein du tenant
        if tenant_id and ordre is not None:
            qs_ordre = Niveau.objects.filter(tenant_id=tenant_id, ordre=ordre)
            if self.instance:
                qs_ordre = qs_ordre.exclude(pk=self.instance.pk)
            if qs_ordre.exists():
                raise serializers.ValidationError(
                    {"ordre": "Un niveau avec cet ordre existe déjà pour cet organisme."}
                )

        return attrs


class CompetenceNiveauSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompetenceNiveau
        fields = [
            "id",
            "competence",
            "niveau",
            "description",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "date_creation",
            "date_modification",
        ]

    def validate(self, attrs):
        tenant_id = self.context.get("tenant_id")
        competence = attrs.get("competence") or (
            self.instance.competence if self.instance else None
        )
        niveau = attrs.get("niveau") or (
            self.instance.niveau if self.instance else None
        )

        # Validation parent-enfant : la compétence doit appartenir au tenant actif
        if competence and tenant_id and str(competence.module.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError(
                {"competence": "La compétence sélectionnée n'appartient pas à cet organisme."}
            )

        # Validation parent-enfant : le niveau doit appartenir au tenant actif
        if niveau and tenant_id and str(niveau.tenant_id) != str(tenant_id):
            raise serializers.ValidationError(
                {"niveau": "Le niveau sélectionné n'appartient pas à cet organisme."}
            )

        # Règle métier critique : appartenance au même tenant
        if competence and niveau:
            if str(competence.module.formation.tenant_id) != str(niveau.tenant_id):
                raise serializers.ValidationError(
                    "La formation de la compétence et le niveau doivent appartenir au même organisme."
                )

            # Unicité de la paire (competence, niveau)
            qs = CompetenceNiveau.objects.filter(competence=competence, niveau=niveau)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    "Cette compétence est déjà associée à ce niveau."
                )

        return attrs
