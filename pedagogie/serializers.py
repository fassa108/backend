from rest_framework import serializers

from .models import (
    Competence,
    CompetenceNiveau,
    Formation,
    FormateurPromotion,
    Groupe,
    GroupeMembre,
    InscriptionPromotion,
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
    # Formateurs à affecter dès la création (facultatif).
    formateurs = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
    )

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
            "formateurs",
            "date_creation",
            "date_modification",
        ]
        # « actif » (promotion ouverte / clôturée) ne change que via les
        # actions cloturer / rouvrir, qui gèrent aussi les inscriptions.
        read_only_fields = [
            "id",
            "actif",
            "date_creation",
            "date_modification",
        ]

    def validate_formateurs(self, value):
        from accounts.models import MembreTenant

        if self.instance is not None:
            raise serializers.ValidationError(
                "Les formateurs se gèrent depuis la fiche de la promotion."
            )

        ids = list(dict.fromkeys(value))
        valides = set(
            MembreTenant.objects.filter(
                utilisateur_id__in=ids,
                tenant_id=self.context.get("tenant_id"),
                role=MembreTenant.Role.FORMATEUR,
                actif=True,
            ).values_list("utilisateur_id", flat=True)
        )
        invalides = [i for i in ids if i not in valides]
        if invalides:
            raise serializers.ValidationError(
                "Ces utilisateurs ne sont pas des formateurs actifs de cet organisme : "
                + ", ".join(str(i) for i in invalides)
            )
        return ids

    def create(self, validated_data):
        formateurs = validated_data.pop("formateurs", [])
        promotion = super().create(validated_data)
        FormateurPromotion.objects.bulk_create([
            FormateurPromotion(formateur_id=formateur_id, promotion=promotion)
            for formateur_id in formateurs
        ])
        return promotion

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


class FormateurPromotionSerializer(serializers.ModelSerializer):
    formateur_nom = serializers.CharField(source="formateur.nom", read_only=True)
    formateur_prenom = serializers.CharField(source="formateur.prenom", read_only=True)
    formateur_email = serializers.CharField(source="formateur.email", read_only=True)

    class Meta:
        model = FormateurPromotion
        fields = [
            "id",
            "formateur",
            "formateur_nom",
            "formateur_prenom",
            "formateur_email",
            "promotion",
            "date_ajout",
        ]
        read_only_fields = [
            "id",
            "formateur_nom",
            "formateur_prenom",
            "formateur_email",
            "date_ajout",
        ]

    def validate(self, attrs):
        from accounts.models import MembreTenant

        tenant_id = self.context.get("tenant_id")
        formateur = attrs.get("formateur") or (
            self.instance.formateur if self.instance else None
        )
        promotion = attrs.get("promotion") or (
            self.instance.promotion if self.instance else None
        )

        if formateur and tenant_id:
            # Le formateur doit être membre actif du tenant avec le rôle FORMATEUR
            if not MembreTenant.objects.filter(
                utilisateur=formateur,
                tenant_id=tenant_id,
                role=MembreTenant.Role.FORMATEUR,
                actif=True,
            ).exists():
                raise serializers.ValidationError(
                    {
                        "formateur": (
                            "L'utilisateur sélectionné doit être un formateur actif "
                            "de cet organisme."
                        )
                    }
                )

        if promotion and tenant_id:
            # La promotion doit appartenir au tenant
            if str(promotion.formation.tenant_id) != str(tenant_id):
                raise serializers.ValidationError(
                    {
                        "promotion": (
                            "La promotion sélectionnée n'appartient pas à cet organisme."
                        )
                    }
                )

        if formateur and promotion and self.instance is None:
            # Anti-doublon
            if FormateurPromotion.objects.filter(
                formateur=formateur,
                promotion=promotion,
            ).exists():
                raise serializers.ValidationError(
                    "Ce formateur est déjà affecté à cette promotion."
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


class InscriptionPromotionSerializer(serializers.ModelSerializer):
    apprenant_nom = serializers.CharField(source="apprenant.nom", read_only=True)
    apprenant_prenom = serializers.CharField(source="apprenant.prenom", read_only=True)
    apprenant_email = serializers.CharField(source="apprenant.email", read_only=True)

    class Meta:
        model = InscriptionPromotion
        fields = [
            "id",
            "promotion",
            "apprenant",
            "apprenant_nom",
            "apprenant_prenom",
            "apprenant_email",
            "actif",
            "fermee_par_cloture",
            "date_inscription",
            "date_desinscription",
        ]
        read_only_fields = [
            "id",
            "fermee_par_cloture",
            "date_inscription",
            "date_desinscription",
        ]

    def validate(self, attrs):
        tenant_id = self.context.get("tenant_id")
        promotion = attrs.get("promotion") or (
            self.instance.promotion if self.instance else None
        )
        apprenant = attrs.get("apprenant") or (
            self.instance.apprenant if self.instance else None
        )
        actif = attrs.get("actif", True if not self.instance else self.instance.actif)

        # 1. Vérification que la promotion appartient au tenant actif
        if promotion and tenant_id and str(promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError(
                {"promotion": "La promotion sélectionnée n'appartient pas à cet organisme."}
            )

        # 2. Vérification de l'apprenant : utilisateur actif + MembreTenant rôle APPRENANT actif dans ce tenant
        if apprenant and tenant_id:
            from accounts.models import MembreTenant
            if not MembreTenant.objects.filter(
                utilisateur=apprenant,
                tenant_id=tenant_id,
                role=MembreTenant.Role.APPRENANT,
                actif=True,
            ).exists():
                raise serializers.ValidationError(
                    {"apprenant": "L'utilisateur doit être un apprenant actif de cet organisme."}
                )

        # 3. Une seule inscription active par organisme
        if apprenant and actif and promotion:
            qs = InscriptionPromotion.objects.filter(
                apprenant=apprenant,
                tenant_id=promotion.formation.tenant_id,
                actif=True,
            )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                active_promo = qs.first().promotion.nom
                raise serializers.ValidationError(
                    {"apprenant": f"L'apprenant a déjà une inscription active dans la promotion '{active_promo}'."}
                )

        # 4. Unicité du couple (promotion, apprenant) si on est en création
        if not self.instance and promotion and apprenant:
            if InscriptionPromotion.objects.filter(promotion=promotion, apprenant=apprenant).exists():
                raise serializers.ValidationError(
                    "Une inscription existe déjà pour cet apprenant dans cette promotion."
                )

        return attrs


class GroupeMembreSerializer(serializers.ModelSerializer):
    apprenant_nom = serializers.CharField(source="apprenant.nom", read_only=True)
    apprenant_prenom = serializers.CharField(source="apprenant.prenom", read_only=True)
    apprenant_email = serializers.CharField(source="apprenant.email", read_only=True)

    class Meta:
        model = GroupeMembre
        fields = [
            "id",
            "groupe",
            "apprenant",
            "apprenant_nom",
            "apprenant_prenom",
            "apprenant_email",
            "actif",
            "date_ajout",
        ]
        read_only_fields = [
            "id",
            "actif",
            "date_ajout",
        ]

    def validate(self, attrs):
        tenant_id = self.context.get("tenant_id")
        groupe = attrs.get("groupe") or (
            self.instance.groupe if self.instance else None
        )
        apprenant = attrs.get("apprenant") or (
            self.instance.apprenant if self.instance else None
        )

        # 1. Vérification tenant du groupe
        if groupe and tenant_id and str(groupe.promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError(
                {"groupe": "Le groupe sélectionné n'appartient pas à cet organisme."}
            )

        # 2. Vérification apprenant actif et rôle APPRENANT dans le tenant
        if apprenant and tenant_id:
            from accounts.models import MembreTenant
            if not MembreTenant.objects.filter(
                utilisateur=apprenant,
                tenant_id=tenant_id,
                role=MembreTenant.Role.APPRENANT,
                actif=True,
            ).exists():
                raise serializers.ValidationError(
                    {"apprenant": "L'utilisateur doit être un apprenant actif de cet organisme."}
                )

        # 3. Vérification que l'apprenant a une inscription active dans LA PROMOTION DU GROUPE
        if groupe and apprenant:
            if not InscriptionPromotion.objects.filter(
                apprenant=apprenant,
                promotion_id=groupe.promotion_id,
                actif=True,
            ).exists():
                raise serializers.ValidationError(
                    {"apprenant": f"L'apprenant n'a pas d'inscription active dans la promotion '{groupe.promotion.nom}'."}
                )

            # 4. Unicité dans ce groupe
            qs = GroupeMembre.objects.filter(groupe=groupe, apprenant=apprenant)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"apprenant": "Cet apprenant est déjà membre de ce groupe."}
                )

        return attrs


class GroupeSerializer(serializers.ModelSerializer):
    membres = GroupeMembreSerializer(many=True, read_only=True)
    nb_membres = serializers.SerializerMethodField()

    class Meta:
        model = Groupe
        fields = [
            "id",
            "promotion",
            "nom",
            "description",
            "actif",
            "membres",
            "nb_membres",
            "date_creation",
            "date_modification",
        ]
        read_only_fields = [
            "id",
            "date_creation",
            "date_modification",
        ]

    def get_nb_membres(self, groupe):
        # Membres actifs (les membres inactifs sont gardés pour l'historique).
        return sum(1 for m in groupe.membres.all() if m.actif)

    def validate(self, attrs):
        tenant_id = self.context.get("tenant_id")

        # Un groupe ne change pas de promotion.
        if (
            self.instance
            and "promotion" in attrs
            and attrs["promotion"].pk != self.instance.promotion_id
        ):
            raise serializers.ValidationError(
                {"promotion": "Un groupe ne peut pas changer de promotion."}
            )

        promotion = attrs.get("promotion") or (
            self.instance.promotion if self.instance else None
        )

        # Validation parent-enfant : promotion doit appartenir au tenant actif
        if promotion and tenant_id and str(promotion.formation.tenant_id) != str(tenant_id):
            raise serializers.ValidationError(
                {"promotion": "La promotion sélectionnée n'appartient pas à cet organisme."}
            )

        # Nettoyage du nom
        nom = attrs.get("nom")
        if nom is not None:
            nom = nom.strip()
            attrs["nom"] = nom
        elif self.instance:
            nom = self.instance.nom

        # Unicité insensible à la casse dans la promotion
        if promotion and nom:
            qs_nom = Groupe.objects.filter(promotion=promotion, nom__iexact=nom)
            if self.instance:
                qs_nom = qs_nom.exclude(pk=self.instance.pk)
            if qs_nom.exists():
                raise serializers.ValidationError(
                    {"nom": "Un groupe avec ce nom existe déjà pour cette promotion."}
                )

        return attrs

