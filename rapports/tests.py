"""
Tests des rapports et des exports.

Couvre : chiffres calculés par Django, demande à l'IA pseudonymisée,
texte rédigé (noms remis, HTML sûr), droits (formateur de la promotion,
admin, apprenant), cycle brouillon → validé → partagé, PDF, exports Excel.
"""

from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.utils import timezone
from openpyxl import load_workbook
from rest_framework import status

from activites.models import Assignation, CompetenceValidee, Evaluation, EvaluationCompetence, Livrable
from activites.tests import ActivitesBaseTestCase
from notifications.models import Notification

from .donnees import demande_ia, donnees_apprenant, donnees_promotion
from .models import Rapport
from .texte import en_html, markdown

REPONSE_IA = {
    "synthese": "Apprenant 2 progresse, l'Apprenant 1 doit rattraper son retard.",
    "points_forts": ["Rendus réguliers"],
    "points_vigilance": ["Développer une API"],
    "apprenants_a_suivre": [
        {"apprenant": "Apprenant 1", "constat": "Un brief non rendu.", "action": "Entretien."},
        {"apprenant": "Apprenant 9", "constat": "Inconnu.", "action": "Rien."},
    ],
    "recommandations": ["Séance de rattrapage", "Découper le brief"],
}


class RapportsBaseTestCase(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.promotion.refresh_from_db()  # dates en chaînes dans le setUp commun
        # Noms distincts : « Apprenant 1 » = Awa Ndiaye, « Apprenant 2 » = Moussa Sow (ordre alphabétique)
        self.apprenant.prenom, self.apprenant.nom = "Awa", "Ndiaye"
        self.apprenant.save()
        self.apprenant_b.prenom, self.apprenant_b.nom = "Moussa", "Sow"
        self.apprenant_b.save()

        maintenant = timezone.now()
        self.brief = self.creer_brief(
            date_debut=maintenant - timedelta(days=10), date_limite=maintenant - timedelta(days=2)
        )
        self.brief.competence_niveaux.set([self.cn])
        self.a = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        self.a_b = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant_b)
        # Moussa a rendu en retard, et a validé la compétence
        depot = self.deposer(self.a_b, self.apprenant_b)
        Livrable.objects.filter(pk=depot.pk).update(date_depot=maintenant - timedelta(days=1))
        evaluation = Evaluation.objects.create(
            assignation=self.a_b, evaluateur=self.formateur, commentaire="<p>Bon travail</p>"
        )
        EvaluationCompetence.objects.create(evaluation=evaluation, competence_niveau=self.cn, acquis=True)
        CompetenceValidee.objects.create(apprenant=self.apprenant_b, competence_niveau=self.cn, evaluation=evaluation)

        self.base = f"/api/tenants/{self.tenant.id}/rapports/"

    def rapport(self, type_=Rapport.Type.PROMOTION, apprenant=None, **champs):
        donnees = (
            donnees_apprenant(apprenant, self.promotion) if apprenant else donnees_promotion(self.promotion)
        )
        champs.setdefault("statut", Rapport.Statut.BROUILLON)
        champs.setdefault("contenu", "## Synthèse\nTexte.")
        return Rapport.objects.create(
            type=type_, promotion=self.promotion, apprenant=apprenant, auteur=self.formateur,
            donnees=donnees, **champs,
        )


class DonneesTests(RapportsBaseTestCase):
    def test_chiffres_de_la_promotion(self):
        d = donnees_promotion(self.promotion)
        indicateurs = {i["libelle"]: i["valeur"] for i in d["indicateurs"]}
        self.assertEqual(indicateurs["Apprenants"], "2")
        self.assertEqual(indicateurs["Rendus en retard"], "1")
        self.assertEqual(indicateurs["Rendus manquants"], "1")
        self.assertEqual(indicateurs["Taux de rendu (briefs échus)"], "50 %")
        self.assertEqual(d["formateurs"], ["Prenom Nom"])

        awa, moussa = d["apprenants"]
        self.assertEqual((awa["reference"], awa["nom"], awa["email"]), ("Apprenant 1", "Awa Ndiaye", "apprenant@test.com"))
        self.assertEqual((awa["retards"], awa["rendus"], awa["attendus"]), (1, 0, 1))
        self.assertEqual(moussa["progression_pct"], 50)
        self.assertEqual(d["competences"][0]["pct"], 50)

    def test_validation_genre_et_graphiques(self):
        self.apprenant.genre, self.apprenant_b.genre = "F", "H"
        self.apprenant.save()
        self.apprenant_b.save()
        d = donnees_promotion(self.promotion)
        indicateurs = {i["libelle"]: i["valeur"] for i in d["indicateurs"]}
        self.assertEqual(
            [indicateurs[k] for k in ("Rendus validés", "Rendus non validés", "Taux de validation")], ["1", "0", "100 %"]
        )
        # Moussa a déposé il y a un jour, évalué à l'instant
        self.assertEqual(indicateurs["Délai moyen de correction"], "1,0 j")
        self.assertEqual((d["etats"]["VALIDE"], d["etats"]["NON_RENDU"], d["etats"]["A_RENDRE"]), (1, 1, 0))
        self.assertEqual([t["nb"] for t in d["tranches"]], [1, 0, 1, 0])

        femmes, hommes = d["genre"]
        self.assertEqual((femmes["libelle"], femmes["nb"], femmes["pct"], femmes["taux_rendu"]), ("Femmes", 1, 50, 0))
        self.assertEqual((hommes["progression_pct"], hommes["taux_rendu"], hommes["taux_validation"]), (50, 100, 100))

        brief, = d["bilan_briefs"]
        self.assertEqual(
            [brief[k] for k in ("attendus", "rendus", "valides", "non_valides", "non_rendus", "en_retard")],
            [2, 1, 1, 0, 1, 1],
        )
        self.assertEqual(d["apprenants"][1]["valides"], 1)

    def test_genre_envoye_a_l_ia_seulement_en_groupes_suffisants(self):
        self.apprenant.genre, self.apprenant_b.genre = "F", "H"
        self.apprenant.save()
        self.apprenant_b.save()
        rapport = self.rapport()
        libelles = lambda: [i["libelle"] for i in demande_ia(rapport)["indicateurs"]]
        # Un apprenant par groupe : sa moyenne serait la sienne
        self.assertFalse(any(l.startswith("Femmes") for l in libelles()))
        with patch("rapports.donnees.MIN_GROUPE_IA", 1):
            self.assertIn("Femmes : effectif, progression, taux de rendu", libelles())
        # Jamais le genre de chaque apprenant
        self.assertTrue(all("genre" not in a for a in demande_ia(rapport)["apprenants"]))

    def test_bilan_individuel(self):
        d = donnees_apprenant(self.apprenant_b, self.promotion)
        self.assertEqual(d["etats"]["VALIDE"], 1)
        self.assertEqual(d["par_module"][0]["acquises"], 1)
        self.assertIn({"libelle": "Briefs non validés", "valeur": "0"}, d["indicateurs"])
        self.assertEqual(d["apprenant"], {"nom": "Moussa Sow", "email": "apprenant_b@test.com"})
        self.assertEqual(d["briefs"][0]["statut"], "Validé")
        self.assertTrue(d["briefs"][0]["en_retard"])
        self.assertEqual(d["commentaires"], ["Bon travail"])
        acquises = [c["valide"] for m in d["modules"] for c in m["competences"]]
        self.assertEqual(acquises, [True, False])

    def test_demande_a_l_ia_pseudonymisee(self):
        for rapport in (self.rapport(), self.rapport(Rapport.Type.APPRENANT, self.apprenant)):
            demande = str(demande_ia(rapport))
            for donnee_personnelle in ("Awa", "Ndiaye", "Moussa", "Sow", "@test.com", "Prenom Nom"):
                self.assertNotIn(donnee_personnelle, demande)
        self.assertEqual(
            [a["reference"] for a in demande_ia(self.rapport())["apprenants"]], ["Apprenant 1", "Apprenant 2"]
        )


class TexteTests(RapportsBaseTestCase):
    def test_noms_remis_et_references_inconnues_ecartees(self):
        texte = markdown(self.rapport(), REPONSE_IA)
        self.assertIn("Moussa Sow progresse, Awa Ndiaye doit rattraper", texte)
        self.assertIn("- **Awa Ndiaye** : Un brief non rendu. Action : Entretien.", texte)
        self.assertNotIn("Apprenant 9", texte)
        self.assertIn("## Points forts", texte)
        self.assertIn("2. Découper le brief", texte)

    def test_bilan_titres_et_sans_apprenants_a_suivre(self):
        texte = markdown(self.rapport(Rapport.Type.APPRENANT, self.apprenant), REPONSE_IA)
        self.assertIn("## Acquis", texte)
        self.assertIn("## Axes de progrès", texte)
        self.assertNotIn("Apprenants à suivre", texte)

    def test_html_echappe(self):
        html = en_html("## Titre\n<script>alert(1)</script> **gras**\n\n1. un\n- sous\n2. deux")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<h2>Titre</h2>", html)
        self.assertIn("<strong>gras</strong>", html)
        self.assertIn('<ol start="2"><li>deux</li></ol>', html)


@patch("rapports.views.generer_rapport.delay")
class DemandeTests(RapportsBaseTestCase):
    def demander(self, **data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.base, {"type": "PROMOTION", "promotion": self.promotion.id, **data}, format="json")

    def test_formateur_demande_un_rapport(self, generer):
        res = self.demander()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["statut"], "EN_COURS")
        self.assertEqual(res.data["donnees"]["promotion"], "Promo 2026")
        generer.assert_called_once_with(res.data["id"])

    def test_un_seul_rapport_en_cours(self, generer):
        self.demander()
        self.assertEqual(self.demander().status_code, status.HTTP_400_BAD_REQUEST)

    def test_bilan_d_un_apprenant_de_la_promotion(self, generer):
        res = self.demander(type="APPRENANT", apprenant=self.apprenant.id)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["apprenant_nom"], "Awa Ndiaye")
        res = self.demander(type="APPRENANT", apprenant=self.apprenant_hors_promo.id)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_reserve_aux_formateurs_de_la_promotion_et_a_l_admin(self, generer):
        for utilisateur, attendu in (
            (self.formateur_2, status.HTTP_403_FORBIDDEN),
            (self.apprenant, status.HTTP_403_FORBIDDEN),
            (self.admin, status.HTTP_201_CREATED),
        ):
            self.client.force_authenticate(user=utilisateur)
            self.assertEqual(self.demander().status_code, attendu, utilisateur.email)

    def test_type_inconnu(self, generer):
        self.assertEqual(self.demander(type="AUTRE").status_code, status.HTTP_400_BAD_REQUEST)


@patch("rapports.tasks.requests.post")
class GenerationTests(RapportsBaseTestCase):
    def generer(self, rapport):
        from .tasks import generer_rapport

        generer_rapport(rapport.id)
        rapport.refresh_from_db()
        return rapport

    def test_brouillon_et_notification(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = REPONSE_IA
        rapport = self.generer(self.rapport(statut=Rapport.Statut.EN_COURS, contenu=""))
        self.assertEqual(rapport.statut, Rapport.Statut.BROUILLON)
        self.assertIn("Awa Ndiaye", rapport.contenu)
        self.assertNotIn("@test.com", str(post.call_args.kwargs["json"]))
        notification = Notification.objects.get(utilisateur=self.formateur)
        self.assertEqual(notification.lien, f"/rapports/{rapport.id}")

    def test_echec_du_service(self, post):
        post.return_value.status_code = 503
        post.return_value.text = "indisponible"
        rapport = self.generer(self.rapport(statut=Rapport.Statut.EN_COURS, contenu=""))
        self.assertEqual(rapport.statut, Rapport.Statut.ECHEC)
        self.assertIn("échoué", rapport.erreur)


class CycleTests(RapportsBaseTestCase):
    def url(self, rapport, action=""):
        return f"{self.base}{rapport.id}/{action + '/' if action else ''}"

    def test_modifier_valider_puis_plus_modifiable(self):
        rapport = self.rapport()
        res = self.client.patch(self.url(rapport), {"contenu": "## Synthèse\nRelu."}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["contenu"], "## Synthèse\nRelu.")

        res = self.client.post(self.url(rapport, "valider"))
        self.assertEqual(res.data["statut"], "VALIDE")
        self.assertEqual(res.data["valide_par_nom"], "Prenom Nom")
        res = self.client.patch(self.url(rapport), {"contenu": "Autre"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_suppression_d_un_rapport_valide_par_l_admin_seulement(self):
        rapport = self.rapport(statut=Rapport.Statut.VALIDE)
        self.assertEqual(self.client.delete(self.url(rapport)).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.delete(self.url(rapport)).status_code, status.HTTP_204_NO_CONTENT)

    def test_bilan_partage_visible_par_l_apprenant(self):
        bilan = self.rapport(Rapport.Type.APPRENANT, self.apprenant)
        self.rapport(statut=Rapport.Statut.VALIDE)  # rapport de promotion : jamais visible
        self.assertEqual(self.client.post(self.url(bilan, "partager")).status_code, status.HTTP_400_BAD_REQUEST)

        self.client.post(self.url(bilan, "valider"))
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.client.get(self.base).data, [])

        self.client.force_authenticate(user=self.formateur)
        self.assertTrue(self.client.post(self.url(bilan, "partager")).data["partage"])
        self.assertTrue(Notification.objects.filter(utilisateur=self.apprenant, lien=f"/rapports/{bilan.id}").exists())

        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.ids(self.client.get(self.base)), [bilan.id])
        self.assertEqual(self.client.post(self.url(bilan, "valider")).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.client.get(self.base).data, [])

    def test_formateur_d_une_autre_promotion(self):
        rapport = self.rapport()
        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(self.client.get(self.url(rapport)).status_code, status.HTTP_404_NOT_FOUND)

    @patch("rapports.views.generer_rapport.delay")
    def test_relancer_un_rapport_en_echec(self, generer):
        rapport = self.rapport(statut=Rapport.Statut.ECHEC, contenu="")
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(self.url(rapport, "relancer"))
        self.assertEqual(res.data["statut"], "EN_COURS")
        generer.assert_called_once_with(rapport.id)

    def test_pdf(self):
        rapport = self.rapport(contenu="## Synthèse\n**Bien**")
        res = self.client.get(self.url(rapport, "pdf"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn('filename="rapport-promotion-promo-2026.pdf"', res["Content-Disposition"])
        self.assertTrue(res.content.startswith(b"%PDF"))

        en_cours = self.rapport(statut=Rapport.Statut.EN_COURS)
        self.assertEqual(self.client.get(self.url(en_cours, "pdf")).status_code, status.HTTP_400_BAD_REQUEST)


class ExportsTests(RapportsBaseTestCase):
    def classeur(self, res):
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("spreadsheetml", res["Content-Type"])
        return load_workbook(BytesIO(res.content))

    def valeurs(self, feuille):
        return [v for ligne in feuille.iter_rows(values_only=True) for v in ligne if v is not None]

    def export(self, nom):
        return self.client.get(f"/api/tenants/{self.tenant.id}/exports/promotions/{self.promotion.id}/{nom}/")

    def test_progression(self):
        classeur = self.classeur(self.export("progression"))
        self.assertEqual(classeur.sheetnames, ["Progression", "Compétences"])
        self.assertIn("apprenant@test.com", self.valeurs(classeur["Progression"]))
        self.assertIn("1 / 2", self.valeurs(classeur["Compétences"]))

    def test_suivi(self):
        valeurs = self.valeurs(self.classeur(self.export("suivi"))["Suivi"])
        self.assertIn("Awa Ndiaye", valeurs)
        self.assertIn("Non rendu", valeurs)
        self.assertIn("Bon travail", valeurs)

    def test_promotion_d_un_autre_formateur(self):
        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(self.export("progression").status_code, status.HTTP_403_FORBIDDEN)

    def test_membres_reserve_a_l_admin(self):
        url = f"/api/tenants/{self.tenant.id}/exports/membres/"
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.admin)
        valeurs = self.valeurs(self.classeur(self.client.get(url))["Membres"])
        self.assertIn("formateur@test.com", valeurs)
        self.assertIn("Promo 2026", valeurs)
