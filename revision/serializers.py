from django.db import transaction
from rest_framework import serializers

from activites.models import EXTENSIONS_RESSOURCES, FichierLivrable, Ressource
from activites.validators import valider_fichier
from pedagogie.models import Module

from .models import (
    NB_FICHES_MAX,
    NB_QUESTIONS_MAX,
    NB_SOURCES_MAX,
    FichierSource,
    Option,
    Question,
    SupportRevision,
    Tentative,
)

LONGUEUR_MAX_TEXTE = 2000


# ─── Lecture ──────────────────────────────────────────────────────────────────

class OptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Option
        fields = ["id", "ordre", "texte", "est_correcte"]


class QuestionSerializer(serializers.ModelSerializer):
    options = OptionSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = ["id", "ordre", "intitule", "type", "explication", "options"]


class SupportRevisionSerializer(serializers.ModelSerializer):
    """
    Lecture d'un quiz ou d'une fiche. Pour l'apprenant, les bonnes réponses
    et les explications ne sont pas renvoyées (elles arrivent avec la
    correction d'une tentative), ni les sources ni l'erreur.
    """

    module_nom = serializers.CharField(source="module.nom", read_only=True)
    cree_par_nom = serializers.SerializerMethodField()
    est_proprietaire = serializers.SerializerMethodField()
    questions = QuestionSerializer(many=True, read_only=True)
    sources = serializers.SerializerMethodField()

    class Meta:
        model = SupportRevision
        fields = [
            "id", "module", "module_nom", "type", "difficulte", "nb_questions",
            "titre", "contenu", "statut", "erreur", "cree_par", "cree_par_nom",
            "est_proprietaire", "questions", "sources", "date_creation",
            "date_publication",
        ]
        read_only_fields = fields

    def get_cree_par_nom(self, obj):
        u = obj.cree_par
        return f"{u.prenom} {u.nom}" if u else None

    def get_est_proprietaire(self, obj):
        return obj.cree_par_id == self.context["request"].user.id

    def get_sources(self, obj):
        return {
            "ressources": [{"id": r.id, "nom": r.nom_affiche} for r in obj.ressources.all()],
            "fichiers_livrables": [{"id": f.id, "nom": f.nom} for f in obj.fichiers_livrables.all()],
            "fichiers_ajoutes": [f.nom for f in obj.fichiers_ajoutes.all()],
        }

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if self.context.get("role") == "APPRENANT":
            for cle in ("sources", "erreur", "cree_par", "est_proprietaire"):
                data.pop(cle, None)
            for question in data["questions"]:
                question.pop("explication", None)
                for option in question["options"]:
                    option.pop("est_correcte", None)
            tentatives = [t for t in obj.tentatives.all() if t.apprenant_id == self.context["request"].user.id]
            data["nb_tentatives"] = len(tentatives)
            data["meilleur_score"] = max((t.score for t in tentatives), default=None)
        return data


# ─── Génération ───────────────────────────────────────────────────────────────

class GenerationSerializer(serializers.Serializer):
    """
    Demande de génération (multipart) : module, type, difficulté et nombre
    de questions (quiz), sources : ressources, fichiers de livrables,
    fichiers ajoutés. Entre 1 et NB_SOURCES_MAX fichiers au total.
    """

    module = serializers.PrimaryKeyRelatedField(queryset=Module.objects.select_related("formation"))
    type = serializers.ChoiceField(choices=SupportRevision.Type.choices)
    difficulte = serializers.ChoiceField(
        choices=SupportRevision.Difficulte.choices, required=False, allow_blank=True, default=""
    )
    nb_questions = serializers.IntegerField(min_value=1, max_value=NB_QUESTIONS_MAX, default=10)
    ressources = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    fichiers_livrables = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    fichiers = serializers.ListField(child=serializers.FileField(), required=False, default=list)

    def validate(self, attrs):
        tenant_id = self.context["tenant_id"]
        formations = self.context["formations_du_formateur"]
        promotions = self.context["promotions_du_formateur"]
        module = attrs["module"]

        if str(module.formation.tenant_id) != str(tenant_id) or module.formation_id not in formations:
            raise serializers.ValidationError({"module": "Ce module n'est pas dans une formation de vos promotions."})
        if not module.actif:
            raise serializers.ValidationError({"module": "Ce module est désactivé."})

        if attrs["type"] == SupportRevision.Type.QUIZ:
            if not attrs["difficulte"]:
                raise serializers.ValidationError({"difficulte": "Choisissez la difficulté du quiz."})
        else:
            attrs["difficulte"] = ""

        ressources = list(Ressource.objects.filter(id__in=attrs["ressources"], tenant_id=tenant_id))
        if len(ressources) != len(set(attrs["ressources"])):
            raise serializers.ValidationError({"ressources": "Ressource introuvable."})
        if any(not r.fichier for r in ressources):
            raise serializers.ValidationError({"ressources": "Un lien ne peut pas servir de source : choisissez des fichiers."})

        livrables = list(
            FichierLivrable.objects.filter(
                id__in=attrs["fichiers_livrables"],
                livrable__assignation__brief__promotion_id__in=promotions,
            )
        )
        if len(livrables) != len(set(attrs["fichiers_livrables"])):
            raise serializers.ValidationError({"fichiers_livrables": "Fichier de livrable introuvable dans vos promotions."})
        if any(not f.fichier for f in livrables):
            raise serializers.ValidationError({"fichiers_livrables": "Un lien ne peut pas servir de source : choisissez des fichiers."})

        erreurs = []
        for fichier in attrs["fichiers"]:
            ext = fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else ""
            try:
                if ext not in EXTENSIONS_RESSOURCES:
                    raise serializers.ValidationError("Format non accepté (pdf, docx, pptx ou txt).")
                valider_fichier(fichier)
            except serializers.ValidationError as erreur:
                erreurs.append(f"{fichier.name} : {erreur.detail[0]}")
        if erreurs:
            raise serializers.ValidationError({"fichiers": erreurs})

        total = len(ressources) + len(livrables) + len(attrs["fichiers"])
        if total == 0:
            raise serializers.ValidationError("Choisissez au moins une source.")
        if total > NB_SOURCES_MAX:
            raise serializers.ValidationError(f"Au plus {NB_SOURCES_MAX} fichiers par génération.")

        attrs["ressources"] = ressources
        attrs["fichiers_livrables"] = livrables
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        module = validated_data["module"]
        # Verrou sur le module : deux demandes simultanées ne dépassent pas la limite
        Module.objects.select_for_update().get(pk=module.pk)
        supports = SupportRevision.objects.filter(module=module)

        if validated_data["type"] == SupportRevision.Type.QUIZ:
            difficulte = validated_data["difficulte"]
            if supports.filter(type=SupportRevision.Type.QUIZ, difficulte=difficulte).exists():
                libelle = SupportRevision.Difficulte(difficulte).label.lower()
                raise serializers.ValidationError(
                    {"difficulte": f"Ce module a déjà un quiz {libelle} : supprimez-le pour en générer un autre."}
                )
        elif supports.filter(type=SupportRevision.Type.FICHE).count() >= NB_FICHES_MAX:
            raise serializers.ValidationError(
                f"Ce module a déjà {NB_FICHES_MAX} fiches : supprimez-en une pour en générer une autre."
            )

        est_quiz = validated_data["type"] == SupportRevision.Type.QUIZ
        support = SupportRevision.objects.create(
            module=module,
            type=validated_data["type"],
            difficulte=validated_data["difficulte"],
            nb_questions=validated_data["nb_questions"] if est_quiz else None,
            cree_par=self.context["request"].user,
        )
        support.ressources.set(validated_data["ressources"])
        support.fichiers_livrables.set(validated_data["fichiers_livrables"])
        for fichier in validated_data["fichiers"]:
            FichierSource.objects.create(support=support, nom=fichier.name[:255], fichier=fichier)
        return support


# ─── Relecture ────────────────────────────────────────────────────────────────

class OptionEcritureSerializer(serializers.Serializer):
    texte = serializers.CharField(max_length=LONGUEUR_MAX_TEXTE)
    est_correcte = serializers.BooleanField()


class QuestionEcritureSerializer(serializers.Serializer):
    intitule = serializers.CharField(max_length=LONGUEUR_MAX_TEXTE)
    type = serializers.ChoiceField(choices=Question.Type.choices)
    explication = serializers.CharField(max_length=LONGUEUR_MAX_TEXTE, required=False, allow_blank=True, default="")
    options = OptionEcritureSerializer(many=True)

    def validate(self, attrs):
        options = attrs["options"]
        if not 2 <= len(options) <= 6:
            raise serializers.ValidationError("Une question a entre 2 et 6 options.")
        nb_correctes = sum(o["est_correcte"] for o in options)
        if nb_correctes == 0:
            raise serializers.ValidationError("Au moins une option doit être correcte.")
        if attrs["type"] == Question.Type.CHOIX_UNIQUE and nb_correctes != 1:
            raise serializers.ValidationError("Un choix unique a exactement une bonne option.")
        return attrs


class SectionFicheSerializer(serializers.Serializer):
    titre = serializers.CharField(max_length=255)
    points = serializers.ListField(child=serializers.CharField(max_length=LONGUEUR_MAX_TEXTE), min_length=1)


class ContenuFicheSerializer(serializers.Serializer):
    titre = serializers.CharField(max_length=255)
    resume = serializers.CharField(max_length=LONGUEUR_MAX_TEXTE)
    sections = SectionFicheSerializer(many=True, min_length=1)
    a_retenir = serializers.ListField(child=serializers.CharField(max_length=LONGUEUR_MAX_TEXTE), min_length=1)


class RelectureSerializer(serializers.Serializer):
    """
    Correction d'un brouillon par son créateur. Quiz : titre et liste
    complète des questions (remplace l'existante). Fiche : contenu complet.
    """

    titre = serializers.CharField(max_length=255, required=False)
    questions = QuestionEcritureSerializer(many=True, required=False)
    contenu = ContenuFicheSerializer(required=False)

    def validate(self, attrs):
        support = self.instance
        if support.est_quiz:
            if "contenu" in attrs:
                raise serializers.ValidationError({"contenu": "Un quiz n'a pas de contenu de fiche."})
            if "questions" in attrs and not 1 <= len(attrs["questions"]) <= NB_QUESTIONS_MAX:
                raise serializers.ValidationError(
                    {"questions": f"Un quiz a entre 1 et {NB_QUESTIONS_MAX} questions."}
                )
        elif "questions" in attrs:
            raise serializers.ValidationError({"questions": "Une fiche n'a pas de questions."})
        return attrs

    @transaction.atomic
    def update(self, support, validated_data):
        if "questions" in validated_data:
            support.questions.all().delete()
            for i, q in enumerate(validated_data["questions"], start=1):
                question = Question.objects.create(
                    support=support, ordre=i, intitule=q["intitule"],
                    type=q["type"], explication=q["explication"],
                )
                Option.objects.bulk_create(
                    Option(question=question, ordre=j, texte=o["texte"], est_correcte=o["est_correcte"])
                    for j, o in enumerate(q["options"], start=1)
                )
            support.nb_questions = len(validated_data["questions"])
        if "contenu" in validated_data:
            support.contenu = validated_data["contenu"]
            support.titre = validated_data["contenu"]["titre"]
        if "titre" in validated_data:
            support.titre = validated_data["titre"]
            if support.contenu:
                support.contenu = {**support.contenu, "titre": validated_data["titre"]}
        support.save()
        return support


# ─── Tentatives ───────────────────────────────────────────────────────────────

class ReponseSerializer(serializers.Serializer):
    question = serializers.IntegerField()
    options = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)


class TentativeSerializer(serializers.ModelSerializer):
    """
    Envoi : {"reponses": [{"question": id, "options": [id, …]}, …]}.
    Une question est juste si les options cochées sont exactement les bonnes.
    Réponse : score et correction (bonnes options et explication).
    """

    reponses = ReponseSerializer(many=True, write_only=True)
    correction = serializers.SerializerMethodField()

    class Meta:
        model = Tentative
        fields = ["id", "reponses", "score", "total", "date", "correction"]
        read_only_fields = ["id", "score", "total", "date", "correction"]

    def validate_reponses(self, reponses):
        support = self.context["support"]
        questions = {q.id: {o.id for o in q.options.all()} for q in support.questions.all()}
        vues = set()
        for r in reponses:
            if r["question"] not in questions or r["question"] in vues:
                raise serializers.ValidationError("Question inconnue ou en double.")
            if not set(r["options"]) <= questions[r["question"]]:
                raise serializers.ValidationError("Option inconnue.")
            vues.add(r["question"])
        return reponses

    def create(self, validated_data):
        support = self.context["support"]
        cochees = {r["question"]: set(r["options"]) for r in validated_data["reponses"]}
        score = 0
        for question in support.questions.all():
            bonnes = {o.id for o in question.options.all() if o.est_correcte}
            score += cochees.get(question.id, set()) == bonnes
        return Tentative.objects.create(
            support=support,
            apprenant=self.context["request"].user,
            reponses={str(q): sorted(o) for q, o in cochees.items()},
            score=score,
            total=support.questions.count(),
        )

    def get_correction(self, tentative):
        cochees = tentative.reponses
        return [
            {
                "question": q.id,
                "options_cochees": cochees.get(str(q.id), []),
                "bonnes_options": [o.id for o in q.options.all() if o.est_correcte],
                "explication": q.explication,
            }
            for q in tentative.support.questions.all()
        ]
