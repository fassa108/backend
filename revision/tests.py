"""
Tests des quiz et fiches de révision.

Couvre :
- Génération : formateur des promotions de la formation, sources (ressources,
  livrables de ses promotions, fichiers ajoutés), 1 à 5 fichiers, un quiz
  par difficulté et deux fiches par module, tâche lancée après validation
- Tâche Celery : appel du service IA (simulé), enregistrement du quiz ou de
  la fiche, erreurs, fichiers ajoutés effacés
- Relecture, publication, suppression : créateur seul, brouillon seulement
- Visibilité : apprenants ayant déposé sur un brief du module, supports
  publiés, sans les bonnes réponses ; isolation des organismes
- Tentatives : score, correction, apprenant seul
"""

from unittest.mock import patch

import requests
from django.test import override_settings
from django.urls import reverse
from rest_framework import status

from accounts.models import MembreTenant
from activites.models import Assignation, FichierLivrable, Ressource
from activites.tests import CONTENU_VALIDE, ActivitesBaseTestCase, fichier_test
from pedagogie.models import FormateurPromotion, InscriptionPromotion, Promotion

from .models import FichierSource, Option, Question, SupportRevision, Tentative
from .tasks import MESSAGE_GENERAL, generer_support

QUIZ_IA = {
    "questions": [
        {
            "intitule": "Quel motif suit Django ?",
            "type": "choix_unique",
            "options": [
                {"texte": "MVT", "est_correcte": True},
                {"texte": "MVVM", "est_correcte": False},
            ],
            "explication": "Django suit le motif MVT.",
        },
        {
            "intitule": "Frameworks Python ?",
            "type": "choix_multiple",
            "options": [
                {"texte": "Django", "est_correcte": True},
                {"texte": "Flask", "est_correcte": True},
                {"texte": "React", "est_correcte": False},
            ],
            "explication": None,
        },
    ]
}

FICHE_IA = {
    "titre": "Django en bref",
    "resume": "Un framework web Python.",
    "sections": [{"titre": "Architecture", "points": ["Motif MVT"]}],
    "a_retenir": ["Django suit le motif MVT"],
}


class ReponseIA:
    def __init__(self, status_code, donnees):
        self.status_code = status_code
        self._donnees = donnees

    def json(self):
        return self._donnees


class RevisionBaseTestCase(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.ressource = Ressource.objects.create(
            tenant=self.tenant, formateur=self.formateur, titre="Cours Django",
            fichier=fichier_test("cours.pdf"),
        )

    def url(self, nom="support-revision", pk=None, action=None):
        kwargs = {"tenant_id": self.tenant.id}
        if pk is None:
            return reverse(f"{nom}-list", kwargs=kwargs)
        kwargs["pk"] = pk
        return reverse(f"{nom}-{action or 'detail'}", kwargs=kwargs)

    def generer(self, **extra):
        donnees = {
            "module": self.module.id,
            "type": "QUIZ",
            "difficulte": "FACILE",
            "nb_questions": 5,
            "ressources": [self.ressource.id],
            **extra,
        }
        with patch("revision.views.generer_support.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                res = self.client.post(self.url(), donnees, format="multipart")
        self.delay = delay
        return res

    def creer_support(self, type_="QUIZ", difficulte="FACILE", statut="BROUILLON", cree_par=None, module=None):
        support = SupportRevision.objects.create(
            module=module or self.module,
            type=type_,
            difficulte=difficulte if type_ == "QUIZ" else "",
            statut=statut,
            cree_par=cree_par or self.formateur,
            titre="Support",
            contenu=FICHE_IA if type_ == "FICHE" else None,
        )
        if type_ == "QUIZ":
            question = Question.objects.create(support=support, ordre=1, intitule="Motif ?", type="CHOIX_UNIQUE")
            self.bonne = Option.objects.create(question=question, ordre=1, texte="MVT", est_correcte=True)
            self.mauvaise = Option.objects.create(question=question, ordre=2, texte="MVVM")
            self.question = question
        return support

    def deposer_sur_le_module(self, apprenant=None):
        brief = self.creer_brief()
        assignation = Assignation.objects.create(brief=brief, apprenant=apprenant or self.apprenant)
        return self.deposer(assignation, apprenant)


# ─── Génération ───────────────────────────────────────────────────────────────

class GenerationTests(RevisionBaseTestCase):
    def test_formateur_genere_un_quiz(self):
        res = self.generer()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        support = SupportRevision.objects.get(pk=res.data["id"])
        self.assertEqual(support.statut, SupportRevision.Statut.EN_COURS)
        self.assertEqual(support.cree_par, self.formateur)
        self.assertEqual(list(support.ressources.all()), [self.ressource])
        self.delay.assert_called_once_with(support.pk)

    def test_fiche_sans_difficulte(self):
        res = self.generer(type="FICHE", difficulte="DIFFICILE")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        support = SupportRevision.objects.get(pk=res.data["id"])
        self.assertEqual(support.difficulte, "")
        self.assertIsNone(support.nb_questions)

    def test_quiz_sans_difficulte_refuse(self):
        res = self.generer(difficulte="")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("difficulte", res.data)

    def test_tout_formateur_de_la_formation(self):
        # formateur_2 affecté à l'autre promotion de la même formation
        FormateurPromotion.objects.create(formateur=self.formateur_2, promotion=self.promotion_2)
        self.client.force_authenticate(self.formateur_2)
        self.assertEqual(self.generer().status_code, status.HTTP_201_CREATED)

    def test_formateur_hors_formation_refuse(self):
        self.client.force_authenticate(self.formateur_2)
        res = self.generer()
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("module", res.data)

    def test_module_desactive_refuse(self):
        self.module.actif = False
        self.module.save()
        self.assertEqual(self.generer().status_code, status.HTTP_400_BAD_REQUEST)

    def test_apprenant_et_admin_ne_generent_pas(self):
        for utilisateur in (self.apprenant, self.admin):
            self.client.force_authenticate(utilisateur)
            self.assertEqual(self.generer().status_code, status.HTTP_403_FORBIDDEN)

    def test_un_quiz_par_difficulte(self):
        self.assertEqual(self.generer().status_code, status.HTTP_201_CREATED)
        res = self.generer()
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("déjà un quiz facile", str(res.data))
        self.assertEqual(self.generer(difficulte="MOYEN").status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.generer(difficulte="DIFFICILE").status_code, status.HTTP_201_CREATED)

    def test_un_brouillon_en_echec_compte_dans_la_limite(self):
        self.creer_support(statut="ECHEC")
        self.assertEqual(self.generer().status_code, status.HTTP_400_BAD_REQUEST)

    def test_deux_fiches_par_module(self):
        for _ in range(2):
            self.assertEqual(self.generer(type="FICHE").status_code, status.HTTP_201_CREATED)
        res = self.generer(type="FICHE")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("déjà 2 fiches", str(res.data))
        # L'autre module n'est pas concerné
        self.assertEqual(self.generer(type="FICHE", module=self.module_2.id).status_code, status.HTTP_201_CREATED)

    def test_lien_refuse_comme_source(self):
        lien = Ressource.objects.create(tenant=self.tenant, titre="Doc", url="https://example.com")
        res = self.generer(ressources=[lien.id])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ressources", res.data)

    def test_ressource_d_un_autre_organisme_refusee(self):
        autre = Ressource.objects.create(tenant=self.tenant_2, titre="Doc", fichier=fichier_test("autre.pdf"))
        self.assertEqual(self.generer(ressources=[autre.id]).status_code, status.HTTP_400_BAD_REQUEST)

    def test_livrable_de_ses_promotions(self):
        livrable = self.deposer_sur_le_module()
        fichier = FichierLivrable.objects.create(livrable=livrable, nom="rendu.pdf", fichier=fichier_test("rendu.pdf"))
        res = self.generer(ressources=[], fichiers_livrables=[fichier.id])
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_livrable_d_une_autre_promotion_refuse(self):
        brief = self.creer_brief(promotion=self.promotion_2)
        InscriptionPromotion.objects.create(promotion=self.promotion_2, apprenant=self.apprenant_hors_promo)
        assignation = Assignation.objects.create(brief=brief, apprenant=self.apprenant_hors_promo)
        livrable = self.deposer(assignation, self.apprenant_hors_promo)
        fichier = FichierLivrable.objects.create(livrable=livrable, nom="rendu.pdf", fichier=fichier_test("rendu.pdf"))
        res = self.generer(ressources=[], fichiers_livrables=[fichier.id])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("fichiers_livrables", res.data)

    def test_fichier_ajoute(self):
        res = self.generer(ressources=[], fichiers=[fichier_test("notes.docx")])
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(FichierSource.objects.get().nom, "notes.docx")

    def test_fichier_ajoute_invalide(self):
        res = self.generer(ressources=[], fichiers=[fichier_test("faux.pdf", b"pas un pdf")])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("fichiers", res.data)

    def test_au_moins_une_source(self):
        self.assertEqual(self.generer(ressources=[]).status_code, status.HTTP_400_BAD_REQUEST)

    def test_au_plus_cinq_fichiers(self):
        fichiers = [fichier_test(f"notes{i}.txt") for i in range(5)]
        res = self.generer(fichiers=fichiers)  # + la ressource = 6
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Au plus 5", str(res.data))

    def test_suppression_du_module_refusee(self):
        self.creer_support()
        self.client.force_authenticate(self.admin)
        url = reverse("module-detail", kwargs={"tenant_id": self.tenant.id, "pk": self.module.id})
        self.module.competences.all().delete()
        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


# ─── Tâche Celery ─────────────────────────────────────────────────────────────

@override_settings(IA_URL="http://ia:8000", IA_SERVICE_TOKEN="jeton")
class TacheGenerationTests(RevisionBaseTestCase):
    def lancer(self, support, reponse=None, erreur=None):
        with patch("revision.tasks.requests.post", return_value=reponse, side_effect=erreur) as post:
            generer_support.apply(args=[support.pk])
        support.refresh_from_db()
        return post

    def support_en_cours(self, type_="QUIZ"):
        res = self.generer(type=type_, fichiers=[fichier_test("notes.txt")])
        return SupportRevision.objects.get(pk=res.data["id"])

    def test_quiz_genere(self):
        support = self.support_en_cours()
        post = self.lancer(support, ReponseIA(200, QUIZ_IA))

        self.assertEqual(support.statut, SupportRevision.Statut.BROUILLON)
        self.assertEqual(support.questions.count(), 2)
        q1, q2 = support.questions.all()
        self.assertEqual(q1.type, "CHOIX_UNIQUE")
        self.assertEqual(q2.explication, "")
        self.assertEqual([o.est_correcte for o in q2.options.all()], [True, True, False])
        self.assertIn("facile", support.titre)

        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://ia:8000/generate/quiz")
        self.assertEqual(kwargs["headers"], {"X-Service-Token": "jeton"})
        self.assertEqual(kwargs["data"], {"nb_questions": 5, "difficulte": "facile"})
        noms = sorted(f[1][0] for f in kwargs["files"])
        self.assertEqual(noms, ["Cours Django.pdf", "notes.txt"])
        # Fichier ajouté effacé une fois la génération terminée
        self.assertFalse(support.fichiers_ajoutes.exists())

    def test_fiche_generee(self):
        support = self.support_en_cours("FICHE")
        post = self.lancer(support, ReponseIA(200, FICHE_IA))
        self.assertEqual(support.statut, SupportRevision.Statut.BROUILLON)
        self.assertEqual(support.titre, "Django en bref")
        self.assertEqual(support.contenu["sections"][0]["points"], ["Motif MVT"])
        self.assertEqual(post.call_args.args[0], "http://ia:8000/generate/fiche")

    def test_erreur_utile_transmise(self):
        support = self.support_en_cours()
        self.lancer(support, ReponseIA(413, {"detail": "Texte trop long. Sélectionnez moins de ressources."}))
        self.assertEqual(support.statut, SupportRevision.Statut.ECHEC)
        self.assertIn("Texte trop long", support.erreur)
        self.assertFalse(support.fichiers_ajoutes.exists())

    def test_erreur_interne_masquee(self):
        support = self.support_en_cours()
        self.lancer(support, ReponseIA(503, {"detail": "Le LLM a renvoyé une erreur (503)"}))
        self.assertEqual(support.statut, SupportRevision.Statut.ECHEC)
        self.assertEqual(support.erreur, MESSAGE_GENERAL)

    def test_service_injoignable_apres_nouvelles_tentatives(self):
        support = self.support_en_cours()
        post = self.lancer(support, erreur=requests.ConnectionError("injoignable"))
        self.assertEqual(post.call_count, 4)
        self.assertEqual(support.statut, SupportRevision.Statut.ECHEC)
        self.assertEqual(support.erreur, MESSAGE_GENERAL)

    def test_support_supprime_entre_temps(self):
        support = self.support_en_cours()
        support.delete()
        with patch("revision.tasks.requests.post") as post:
            generer_support.apply(args=[support.pk])
        post.assert_not_called()


# ─── Relecture, publication, suppression ──────────────────────────────────────

class RelectureTests(RevisionBaseTestCase):
    QUESTIONS = [
        {
            "intitule": "Langage de Django ?",
            "type": "CHOIX_UNIQUE",
            "explication": "Django est écrit en Python.",
            "options": [{"texte": "Python", "est_correcte": True}, {"texte": "Ruby", "est_correcte": False}],
        }
    ]

    def test_createur_corrige_les_questions(self):
        support = self.creer_support()
        res = self.client.patch(self.url(pk=support.id), {"titre": "Mon quiz", "questions": self.QUESTIONS}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["titre"], "Mon quiz")
        self.assertEqual([q["intitule"] for q in res.data["questions"]], ["Langage de Django ?"])
        self.assertEqual(support.questions.count(), 1)

    def test_choix_unique_avec_deux_bonnes_reponses_refuse(self):
        support = self.creer_support()
        questions = [{**self.QUESTIONS[0], "options": [
            {"texte": "Python", "est_correcte": True}, {"texte": "Ruby", "est_correcte": True},
        ]}]
        res = self.client.patch(self.url(pk=support.id), {"questions": questions}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_createur_corrige_la_fiche(self):
        support = self.creer_support(type_="FICHE")
        contenu = {**FICHE_IA, "titre": "Fiche corrigée"}
        res = self.client.patch(self.url(pk=support.id), {"contenu": contenu}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["titre"], "Fiche corrigée")

    def test_autre_formateur_ne_corrige_pas(self):
        FormateurPromotion.objects.create(formateur=self.formateur_2, promotion=self.promotion)
        support = self.creer_support()
        self.client.force_authenticate(self.formateur_2)
        res = self.client.patch(self.url(pk=support.id), {"titre": "x"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_support_publie_non_modifiable(self):
        support = self.creer_support(statut="PUBLIE")
        res = self.client.patch(self.url(pk=support.id), {"titre": "x"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_publication_par_le_createur(self):
        support = self.creer_support()
        res = self.client.post(self.url(pk=support.id, action="publier"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        support.refresh_from_db()
        self.assertEqual(support.statut, SupportRevision.Statut.PUBLIE)
        self.assertIsNotNone(support.date_publication)

    def test_publication_refusee_hors_brouillon(self):
        for statut in ("EN_COURS", "ECHEC", "PUBLIE"):
            SupportRevision.objects.all().delete()
            support = self.creer_support(statut=statut)
            res = self.client.post(self.url(pk=support.id, action="publier"))
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, statut)

    def test_publication_par_un_autre_formateur_refusee(self):
        FormateurPromotion.objects.create(formateur=self.formateur_2, promotion=self.promotion)
        support = self.creer_support()
        self.client.force_authenticate(self.formateur_2)
        self.assertEqual(self.client.post(self.url(pk=support.id, action="publier")).status_code, status.HTTP_403_FORBIDDEN)

    def test_suppression_par_le_createur_avec_les_tentatives(self):
        support = self.creer_support(statut="PUBLIE")
        Tentative.objects.create(support=support, apprenant=self.apprenant, score=1, total=1)
        self.assertEqual(self.client.delete(self.url(pk=support.id)).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Tentative.objects.exists())

    def test_suppression_par_un_autre_formateur_refusee(self):
        FormateurPromotion.objects.create(formateur=self.formateur_2, promotion=self.promotion)
        support = self.creer_support()
        self.client.force_authenticate(self.formateur_2)
        self.assertEqual(self.client.delete(self.url(pk=support.id)).status_code, status.HTTP_403_FORBIDDEN)


# ─── Visibilité ───────────────────────────────────────────────────────────────

class VisibiliteTests(RevisionBaseTestCase):
    def setUp(self):
        super().setUp()
        self.publie = self.creer_support(statut="PUBLIE")
        self.brouillon = self.creer_support(difficulte="MOYEN")

    def test_apprenant_ayant_depose_voit_les_supports_publies(self):
        self.deposer_sur_le_module()
        self.client.force_authenticate(self.apprenant)
        res = self.client.get(self.url())
        self.assertEqual(self.ids(res), [self.publie.id])

    def test_apprenant_ne_voit_pas_les_bonnes_reponses(self):
        self.deposer_sur_le_module()
        self.client.force_authenticate(self.apprenant)
        res = self.client.get(self.url(pk=self.publie.id))
        question = res.data["questions"][0]
        self.assertNotIn("explication", question)
        self.assertNotIn("est_correcte", question["options"][0])
        self.assertNotIn("sources", res.data)
        self.assertEqual(res.data["nb_tentatives"], 0)

    def test_apprenant_sans_depot_ne_voit_rien(self):
        self.client.force_authenticate(self.apprenant_b)
        self.assertEqual(self.client.get(self.url()).data, [])

    def test_depot_sur_un_autre_module_ne_suffit_pas(self):
        brief = self.creer_brief()
        brief.module = self.module_2
        brief.save()
        self.deposer(Assignation.objects.create(brief=brief, apprenant=self.apprenant))
        self.client.force_authenticate(self.apprenant)
        self.assertEqual(self.client.get(self.url()).data, [])

    def test_formateur_de_la_formation_voit_tout(self):
        res = self.client.get(self.url())
        self.assertEqual(self.ids(res), sorted([self.publie.id, self.brouillon.id]))
        self.assertTrue(res.data[0]["est_proprietaire"])
        self.assertIn("est_correcte", res.data[0]["questions"][0]["options"][0])

    def test_formateur_hors_formation_ne_voit_rien(self):
        self.client.force_authenticate(self.formateur_2)
        self.assertEqual(self.client.get(self.url()).data, [])

    def test_admin_voit_tout(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(len(self.client.get(self.url()).data), 2)

    def test_filtre_par_module(self):
        res = self.client.get(self.url(), {"module": self.module_2.id})
        self.assertEqual(res.data, [])

    def test_isolation_des_organismes(self):
        autre_admin = self.membre("admin_b@test.com", MembreTenant.Role.ADMINISTRATEUR, tenant=self.tenant_2)
        self.client.force_authenticate(autre_admin)
        url = reverse("support-revision-list", kwargs={"tenant_id": self.tenant_2.id})
        self.assertEqual(self.client.get(url).data, [])


# ─── Tentatives ───────────────────────────────────────────────────────────────

class TentativeTests(RevisionBaseTestCase):
    def setUp(self):
        super().setUp()
        self.quiz = self.creer_support(statut="PUBLIE")
        self.deposer_sur_le_module()
        self.client.force_authenticate(self.apprenant)

    def repondre(self, options):
        return self.client.post(
            self.url(pk=self.quiz.id, action="tentatives"),
            {"reponses": [{"question": self.question.id, "options": options}]},
            format="json",
        )

    def test_bonne_reponse(self):
        res = self.repondre([self.bonne.id])
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual((res.data["score"], res.data["total"]), (1, 1))
        self.assertEqual(res.data["correction"][0]["bonnes_options"], [self.bonne.id])

    def test_mauvaise_reponse_ou_reponse_partielle(self):
        self.assertEqual(self.repondre([self.mauvaise.id]).data["score"], 0)
        self.assertEqual(self.repondre([self.bonne.id, self.mauvaise.id]).data["score"], 0)
        self.assertEqual(self.repondre([]).data["score"], 0)

    def test_historique_et_meilleur_score(self):
        self.repondre([self.mauvaise.id])
        self.repondre([self.bonne.id])
        res = self.client.get(self.url(pk=self.quiz.id, action="tentatives"))
        self.assertEqual([t["score"] for t in res.data], [1, 0])
        detail = self.client.get(self.url(pk=self.quiz.id))
        self.assertEqual((detail.data["nb_tentatives"], detail.data["meilleur_score"]), (2, 1))

    def test_option_inconnue_refusee(self):
        autre = self.creer_support(difficulte="MOYEN", statut="PUBLIE")
        res = self.repondre([self.bonne.id + 100])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(autre)

    def test_apprenant_sans_depot_refuse(self):
        self.client.force_authenticate(self.apprenant_b)
        self.assertEqual(self.repondre([self.bonne.id]).status_code, status.HTTP_404_NOT_FOUND)

    def test_brouillon_inaccessible(self):
        self.quiz.statut = SupportRevision.Statut.BROUILLON
        self.quiz.save()
        self.assertEqual(self.repondre([self.bonne.id]).status_code, status.HTTP_404_NOT_FOUND)

    def test_formateur_ne_passe_pas_le_quiz(self):
        self.client.force_authenticate(self.formateur)
        self.assertEqual(self.repondre([self.bonne.id]).status_code, status.HTTP_403_FORBIDDEN)

    def test_pas_de_tentative_sur_une_fiche(self):
        fiche = self.creer_support(type_="FICHE", statut="PUBLIE")
        res = self.client.post(self.url(pk=fiche.id, action="tentatives"), {"reponses": []}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
