"""
Tests du module activités (étape 1 : briefs, assignations, ressources).

Couvre :
- Briefs : module obligatoire, compétences visées (CompetenceNiveau),
  ressources, créé par, brief figé après le premier livrable,
  promotion clôturée, permissions
- Visibilité par rôle (audit S1) : l'apprenant ne voit que les briefs
  publiés / archivés de sa promotion et ses propres assignations et livrables
- Ressources : bibliothèque, modification réservée au créateur et à l'admin
  (audit S11), visibilité apprenant
- Assignations : cibles, doublons, groupe inactif, suppression interdite
  avec livrables (audit S12)
- Livrables : dépôt par l'apprenant ou un membre actif du groupe (audit B5)
- Fichiers : types acceptés / refusés
- Consultation : PDF / TXT tels quels, aperçu PDF des fichiers Office
  (Gotenberg simulé), mêmes droits que le téléchargement
- Évaluations : évaluateur (créateur du brief), compétences visées,
  rien d'acquis sans dépôt, validation définitive, groupes, emails
- Progression : compétences-niveaux validées, briefs validés, visibilité
- Feedback entre pairs : après son propre dépôt, pas sur son rendu, une
  réponse, auteur seul, masquage par le formateur, lecture seule, emails
- Multi-tenant : isolation
"""

import io
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import MembreTenant
from notifications.models import Notification
from pedagogie.models import (
    Competence,
    CompetenceNiveau,
    FormateurPromotion,
    Formation,
    Groupe,
    GroupeMembre,
    InscriptionPromotion,
    Module,
    Niveau,
    Promotion,
)
from pedagogie.services import InscriptionService
from tenants.models import Tenant

from .models import (
    AnalyseAccompagnement,
    Assignation,
    Brief,
    CategorieBrief,
    CompetenceValidee,
    Evaluation,
    FichierLivrable,
    Livrable,
    Ressource,
)

User = get_user_model()

MEDIA_TEST = tempfile.mkdtemp()


def _zip(fichiers):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        for nom in fichiers:
            archive.writestr(nom, "<xml/>")
    return tampon.getvalue()


# Contenus réels minimaux par format
CONTENU_VALIDE = {
    "pdf": b"%PDF-1.4\n% test\n",
    "docx": _zip(["[Content_Types].xml", "word/document.xml"]),
    "pptx": _zip(["[Content_Types].xml", "ppt/presentation.xml"]),
    "txt": "Notes de cours accentuées".encode("utf-8"),
}


def fichier_test(nom, contenu=None):
    ext = nom.rsplit(".", 1)[-1]
    return SimpleUploadedFile(nom, CONTENU_VALIDE.get(ext, b"contenu") if contenu is None else contenu)


# ─── Base ─────────────────────────────────────────────────────────────────────

@override_settings(MEDIA_ROOT=MEDIA_TEST)
class ActivitesBaseTestCase(APITestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.tenant = Tenant.objects.create(nom="Organisme Test")
        self.tenant_2 = Tenant.objects.create(nom="Organisme B")

        self.admin_saas = User.objects.create_user(
            email="saas@test.com", password="pw", nom="SaaS", prenom="A",
            actif=True, est_admin_saas=True,
        )
        self.admin = self.membre("admin@test.com", MembreTenant.Role.ADMINISTRATEUR)
        self.formateur = self.membre("formateur@test.com", MembreTenant.Role.FORMATEUR)
        self.formateur_2 = self.membre("formateur2@test.com", MembreTenant.Role.FORMATEUR)
        self.apprenant = self.membre("apprenant@test.com", MembreTenant.Role.APPRENANT)
        self.apprenant_b = self.membre("apprenant_b@test.com", MembreTenant.Role.APPRENANT)
        self.apprenant_hors_promo = self.membre("hors@test.com", MembreTenant.Role.APPRENANT)

        self.formation = Formation.objects.create(tenant=self.tenant, nom="Formation Web")
        self.autre_formation = Formation.objects.create(tenant=self.tenant, nom="Formation Data")
        self.promotion = Promotion.objects.create(
            formation=self.formation, nom="Promo 2026", date_debut="2026-01-01"
        )
        self.promotion_2 = Promotion.objects.create(
            formation=self.formation, nom="Promo 2026 B", date_debut="2026-06-01"
        )

        self.module = Module.objects.create(formation=self.formation, nom="Développement Web", ordre=1)
        self.module_2 = Module.objects.create(formation=self.formation, nom="Base de données", ordre=2)
        self.module_autre = Module.objects.create(formation=self.autre_formation, nom="Statistiques", ordre=1)

        self.niveau = Niveau.objects.create(tenant=self.tenant, nom="Imiter", ordre=1)
        self.cn = CompetenceNiveau.objects.create(
            competence=Competence.objects.create(module=self.module, nom="Développer une API", ordre=1),
            niveau=self.niveau, description="Reproduire une API existante",
        )
        self.cn_module_2 = CompetenceNiveau.objects.create(
            competence=Competence.objects.create(module=self.module_2, nom="Modéliser", ordre=1),
            niveau=self.niveau,
        )
        self.cn_autre_formation = CompetenceNiveau.objects.create(
            competence=Competence.objects.create(module=self.module_autre, nom="Analyser", ordre=1),
            niveau=self.niveau,
        )

        InscriptionPromotion.objects.create(promotion=self.promotion, apprenant=self.apprenant)
        InscriptionPromotion.objects.create(promotion=self.promotion, apprenant=self.apprenant_b)

        # Formateur affecté à promotion uniquement ; formateur_2 à aucune
        FormateurPromotion.objects.create(formateur=self.formateur, promotion=self.promotion)

        self.client.force_authenticate(user=self.formateur)

    def membre(self, email, role, tenant=None):
        utilisateur = User.objects.create_user(
            email=email, password="pw", nom="Nom", prenom="Prenom", actif=True,
        )
        MembreTenant.objects.create(utilisateur=utilisateur, tenant=tenant or self.tenant, role=role)
        return utilisateur

    def creer_brief(self, promotion=None, statut=Brief.Statut.PUBLIE, **extra):
        # Dates relatives : brief ouvert depuis hier, à rendre dans une semaine
        maintenant = timezone.now()
        extra.setdefault("date_debut", maintenant - timedelta(days=1))
        extra.setdefault("date_limite", maintenant + timedelta(days=7))
        return Brief.objects.create(
            promotion=promotion or self.promotion,
            module=self.module,
            titre="Créer une API REST",
            description="Description",
            modalites_evaluation="<p>Revue de code</p>",
            livrables_attendus="<p>Lien du dépôt</p>",
            statut=statut,
            **extra,
        )

    def deposer(self, assignation, apprenant=None):
        """Dépôt créé directement en base (sans passer par l'API)."""
        return Livrable.objects.create(
            assignation=assignation,
            deposant=apprenant or self.apprenant,
            numero=assignation.livrables.count() + 1,
        )

    def url(self, nom, pk=None):
        kwargs = {"tenant_id": self.tenant.id}
        if pk is not None:
            kwargs["pk"] = pk
            return reverse(f"{nom}-detail", kwargs=kwargs)
        return reverse(f"{nom}-list", kwargs=kwargs)

    def ids(self, res):
        return sorted(x["id"] for x in res.data)

    def dans(self, jours):
        """Date ISO relative à maintenant (les briefs ne commencent pas dans le passé)."""
        return (timezone.now() + timedelta(days=jours)).isoformat()

    def payload_brief(self, **extra):
        return {
            "promotion": self.promotion.id,
            "module": self.module.id,
            "titre": "Brief API",
            "description": "Description",
            "contexte": "<p>Une boulangerie veut un site.</p>",
            "modalites_evaluation": "<p>Revue de code</p>",
            "livrables_attendus": "<ul><li>Lien du dépôt</li></ul>",
            "date_debut": self.dans(1),
            "date_limite": self.dans(30),
            "competence_niveaux": [self.cn.id],
            **extra,
        }


# ─── Briefs ───────────────────────────────────────────────────────────────────

class BriefCreationTests(ActivitesBaseTestCase):
    def test_formateur_cree_un_brief_complet(self):
        ressource = Ressource.objects.create(tenant=self.tenant, formateur=self.formateur,
                                             titre="Doc", url="https://example.com")
        res = self.client.post(self.url("brief"), self.payload_brief(
            competence_niveaux=[self.cn.id, self.cn_module_2.id],
            ressources=[ressource.id],
        ), format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        brief = Brief.objects.get(pk=res.data["id"])
        self.assertEqual(brief.cree_par, self.formateur)
        self.assertEqual(brief.module, self.module)
        # Compétences d'un autre module de la même formation acceptées
        self.assertCountEqual(brief.competence_niveaux.all(), [self.cn, self.cn_module_2])
        self.assertEqual(list(brief.ressources.all()), [ressource])
        self.assertEqual(res.data["statut"], Brief.Statut.BROUILLON)
        self.assertTrue(res.data["modifiable"])

    def test_description_obligatoire(self):
        res = self.client.post(self.url("brief"), self.payload_brief(description="  "), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("description", res.data)

    def test_sections_obligatoires(self):
        # Un éditeur vide envoie « <p></p> » : il compte comme vide
        payload = self.payload_brief(modalites_evaluation="<p></p>", livrables_attendus="<p>&nbsp;</p>")
        res = self.client.post(self.url("brief"), payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        for champ in ("modalites_evaluation", "livrables_attendus"):
            self.assertIn(champ, res.data)

    def test_sections_facultatives_vides_acceptees(self):
        res = self.client.post(self.url("brief"), self.payload_brief(
            contexte="", modalites_pedagogiques="<p></p>", criteres_performance=""), format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_texte_riche_nettoye(self):
        html = (
            '<h2>Contexte</h2><p><strong>Gras</strong> <em>italique</em> <u>souligné</u></p>'
            '<ul><li>puce</li></ul><ol><li>un</li></ol>'
            '<p onclick="alert(1)" style="color:red">texte</p>'
            '<script>alert("xss")</script><iframe src="https://x.test"></iframe>'
            '<a href="javascript:alert(1)">piège</a> <a href="https://doc.test">doc</a>'
            '<img src=x onerror=alert(1)>'
        )
        res = self.client.post(self.url("brief"), self.payload_brief(contexte=html), format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        contexte = res.data["contexte"]
        for autorise in ("<h2>", "<strong>", "<em>", "<u>", "<ul>", "<ol>", "<li>", 'href="https://doc.test"'):
            self.assertIn(autorise, contexte)
        for interdit in ("<script", "onclick", "style=", "<iframe", "javascript:", "<img", "onerror"):
            self.assertNotIn(interdit, contexte)
        self.assertIn('rel="noopener noreferrer nofollow"', contexte)

    def test_sections_obligatoires_en_modification(self):
        brief = self.creer_brief(statut=Brief.Statut.BROUILLON)
        res = self.client.patch(self.url("brief", brief.id), {"livrables_attendus": "<p></p>"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("livrables_attendus", res.data)

    def test_module_obligatoire(self):
        payload = self.payload_brief()
        del payload["module"]
        res = self.client.post(self.url("brief"), payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("module", res.data)

    def test_module_d_une_autre_formation_refuse(self):
        res = self.client.post(self.url("brief"), self.payload_brief(module=self.module_autre.id), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("module", res.data)

    def test_competence_d_une_autre_formation_refusee(self):
        res = self.client.post(self.url("brief"), self.payload_brief(
            competence_niveaux=[self.cn_autre_formation.id]), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("competence_niveaux", res.data)

    def test_competence_deja_visee_par_un_autre_brief_de_la_promotion(self):
        self.creer_brief().competence_niveaux.add(self.cn)
        res = self.client.post(self.url("brief"), self.payload_brief(), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Créer une API REST", str(res.data["competence_niveaux"]))

    def test_un_seul_niveau_par_competence(self):
        niveau_2 = Niveau.objects.create(tenant=self.tenant, nom="Adapter", ordre=2)
        cn_2 = CompetenceNiveau.objects.create(competence=self.cn.competence, niveau=niveau_2)
        res = self.client.post(self.url("brief"), self.payload_brief(competence_niveaux=[self.cn.id, cn_2.id]), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Imiter, Adapter", str(res.data["competence_niveaux"]))

    def test_meme_competence_dans_une_autre_promotion(self):
        self.creer_brief().competence_niveaux.add(self.cn)
        FormateurPromotion.objects.create(formateur=self.formateur, promotion=self.promotion_2)
        res = self.client.post(self.url("brief"), self.payload_brief(promotion=self.promotion_2.id), format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_modifier_un_brief_en_gardant_ses_competences(self):
        brief_id = self.client.post(self.url("brief"), self.payload_brief(), format="json").data["id"]
        res = self.client.patch(self.url("brief", brief_id),
                                {"titre": "Nouveau titre", "competence_niveaux": [self.cn.id]}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

    def test_ressource_d_un_autre_organisme_refusee(self):
        ressource = Ressource.objects.create(tenant=self.tenant_2, titre="Doc", url="https://example.com")
        res = self.client.post(self.url("brief"), self.payload_brief(ressources=[ressource.id]), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_statut_termine_n_existe_plus(self):
        res = self.client.post(self.url("brief"), self.payload_brief(statut="TERMINE"), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dates_invalides_refusees(self):
        res = self.client.post(self.url("brief"), self.payload_brief(
            date_debut=self.dans(30), date_limite=self.dans(1)), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_promotion_non_affectee_refusee(self):
        res = self.client.post(self.url("brief"), self.payload_brief(promotion=self.promotion_2.id), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_promotion_cloturee_refusee(self):
        InscriptionService.cloturer(self.promotion)
        res = self.client.post(self.url("brief"), self.payload_brief(), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("clôturée", str(res.data["promotion"]))

    def test_seul_le_formateur_cree(self):
        for utilisateur in (self.admin, self.apprenant, self.admin_saas):
            self.client.force_authenticate(user=utilisateur)
            res = self.client.post(self.url("brief"), self.payload_brief(), format="json")
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, utilisateur.email)


class BriefModificationTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.brief = self.creer_brief()
        self.assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)

    def test_modifiable_sans_livrable(self):
        res = self.client.patch(self.url("brief", self.brief.id), {"titre": "Nouveau titre"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_fige_apres_le_premier_livrable(self):
        self.deposer(self.assignation)
        res = self.client.patch(self.url("brief", self.brief.id),
                                {"date_limite": self.dans(40)}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self.client.get(self.url("brief", self.brief.id)).data["modifiable"])

    def test_statut_modifiable_apres_livrable(self):
        self.deposer(self.assignation)
        res = self.client.patch(self.url("brief", self.brief.id), {"statut": "ARCHIVE"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_non_supprimable_apres_livrable(self):
        self.deposer(self.assignation)
        res = self.client.delete(self.url("brief", self.brief.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_supprimable_sans_livrable(self):
        res = self.client.delete(self.url("brief", self.brief.id))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_ne_change_pas_de_promotion(self):
        FormateurPromotion.objects.create(formateur=self.formateur, promotion=self.promotion_2)
        res = self.client.patch(self.url("brief", self.brief.id), {"promotion": self.promotion_2.id}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_promotion_cloturee_lecture_seule(self):
        InscriptionService.cloturer(self.promotion)
        self.assertEqual(
            self.client.patch(self.url("brief", self.brief.id), {"titre": "X"}, format="json").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(self.client.delete(self.url("brief", self.brief.id)).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_formateur_non_affecte_ne_modifie_pas(self):
        self.client.force_authenticate(user=self.formateur_2)
        res = self.client.patch(self.url("brief", self.brief.id), {"titre": "X"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_pas_de_retour_en_brouillon_apres_livrable(self):
        self.deposer(self.assignation)
        res = self.client.patch(self.url("brief", self.brief.id), {"statut": "BROUILLON"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("statut", res.data)

    def test_retour_en_brouillon_sans_livrable(self):
        res = self.client.patch(self.url("brief", self.brief.id), {"statut": "BROUILLON"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_publication_refusee_si_date_limite_passee(self):
        brouillon = self.creer_brief(
            statut=Brief.Statut.BROUILLON,
            date_debut=timezone.now() - timedelta(days=10),
            date_limite=timezone.now() - timedelta(days=1),
        )
        res = self.client.patch(self.url("brief", brouillon.id), {"statut": "PUBLIE"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_limite", res.data)

    def test_date_limite_strictement_apres_le_debut(self):
        meme = self.dans(1)
        res = self.client.post(self.url("brief"), self.payload_brief(date_debut=meme, date_limite=meme), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_limite", res.data)

    def test_dates_dans_la_promotion(self):
        self.promotion.date_debut = timezone.localdate() + timedelta(days=10)
        self.promotion.date_fin = timezone.localdate() + timedelta(days=20)
        self.promotion.save()
        avant = self.client.post(self.url("brief"), self.payload_brief(date_debut=self.dans(2)), format="json")
        self.assertEqual(avant.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_debut", avant.data)
        apres = self.client.post(self.url("brief"), self.payload_brief(date_debut=self.dans(11), date_limite=self.dans(30)), format="json")
        self.assertEqual(apres.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_limite", apres.data)

    def test_date_de_debut_pas_dans_le_passe(self):
        res = self.client.post(self.url("brief"), self.payload_brief(date_debut=self.dans(-1)), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_debut", res.data)

        # Brief déjà commencé (hier) : modifiable tant que sa date de début ne change pas
        res = self.client.patch(self.url("brief", self.brief.id), {
            "titre": "Nouveau titre", "date_debut": self.brief.date_debut.isoformat(),
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        res = self.client.patch(self.url("brief", self.brief.id), {"date_debut": self.dans(-3)}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_action_non_prevue_refusee(self):
        # PUT n'est pas exposé ; toute action non listée est refusée
        res = self.client.put(self.url("brief", self.brief.id), self.payload_brief(), format="json")
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class BriefVisibiliteTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.publie = self.creer_brief(statut=Brief.Statut.PUBLIE)
        self.archive = self.creer_brief(statut=Brief.Statut.ARCHIVE)
        self.brouillon = self.creer_brief(statut=Brief.Statut.BROUILLON)
        self.autre_promo = self.creer_brief(promotion=self.promotion_2)

    def lister(self, utilisateur):
        self.client.force_authenticate(user=utilisateur)
        return self.client.get(self.url("brief"))

    def test_apprenant_voit_publies_et_archives_de_sa_promotion(self):
        self.assertEqual(self.ids(self.lister(self.apprenant)), sorted([self.publie.id, self.archive.id]))

    def test_apprenant_ne_voit_pas_un_brouillon_en_detail(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.get(self.url("brief", self.brouillon.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_apprenant_sans_promotion_ne_voit_rien(self):
        self.assertEqual(self.ids(self.lister(self.apprenant_hors_promo)), [])

    def test_apprenant_promotion_cloturee_voit_encore(self):
        InscriptionService.cloturer(self.promotion)
        self.assertEqual(self.ids(self.lister(self.apprenant)), sorted([self.publie.id, self.archive.id]))

    def test_formateur_voit_ses_promotions_brouillons_compris(self):
        self.assertEqual(self.ids(self.lister(self.formateur)),
                         sorted([self.publie.id, self.archive.id, self.brouillon.id]))

    def test_admin_voit_tout(self):
        self.assertEqual(len(self.lister(self.admin).data), 4)

    def test_admin_saas_exclu(self):
        self.assertEqual(self.lister(self.admin_saas).status_code, status.HTTP_403_FORBIDDEN)

    def test_isolation_multi_tenant(self):
        admin_b = self.membre("admin_b@test.com", MembreTenant.Role.ADMINISTRATEUR, tenant=self.tenant_2)
        self.client.force_authenticate(user=admin_b)
        res = self.client.get(self.url("brief"))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


# ─── Catégories de brief ─────────────────────────────────────────────────────

class CategorieBriefTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.tp = CategorieBrief.objects.create(tenant=self.tenant, nom="TP")

    def test_admin_gere_les_categories(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.url("categorie-brief"), {"nom": "Veille"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.client.patch(self.url("categorie-brief", self.tp.id), {"nom": "Travaux pratiques"}).status_code,
                         status.HTTP_200_OK)

    def test_nom_unique_insensible_a_la_casse(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.url("categorie-brief"), {"nom": "tp"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_et_apprenant_lisent_seulement(self):
        for utilisateur in (self.formateur, self.apprenant):
            self.client.force_authenticate(user=utilisateur)
            self.assertEqual(self.ids(self.client.get(self.url("categorie-brief"))), [self.tp.id])
            self.assertEqual(self.client.post(self.url("categorie-brief"), {"nom": "X"}).status_code,
                             status.HTTP_403_FORBIDDEN)

    def test_isolation_tenant(self):
        CategorieBrief.objects.create(tenant=self.tenant_2, nom="Autre")
        self.assertEqual(self.ids(self.client.get(self.url("categorie-brief"))), [self.tp.id])

    def test_brief_avec_categorie(self):
        res = self.client.post(self.url("brief"), self.payload_brief(categorie=self.tp.id), format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["categorie"], self.tp.id)
        self.assertEqual([b["id"] for b in self.client.get(self.url("brief") + f"?categorie={self.tp.id}").data],
                         [res.data["id"]])

    def test_brief_sans_categorie(self):
        res = self.client.post(self.url("brief"), self.payload_brief(), format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(res.data["categorie"])

    def test_categorie_d_un_autre_organisme_refusee(self):
        autre = CategorieBrief.objects.create(tenant=self.tenant_2, nom="Autre")
        res = self.client.post(self.url("brief"), self.payload_brief(categorie=autre.id), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_categorie_desactivee_refusee_pour_un_nouveau_brief(self):
        self.tp.actif = False
        self.tp.save()
        res = self.client.post(self.url("brief"), self.payload_brief(categorie=self.tp.id), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_categorie_utilisee_non_supprimable(self):
        self.creer_brief(categorie=self.tp)
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete(self.url("categorie-brief", self.tp.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("Désactivez", str(res.data["detail"]))

    def test_categorie_libre_supprimable(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.delete(self.url("categorie-brief", self.tp.id)).status_code,
                         status.HTTP_204_NO_CONTENT)


# ─── Ressources ───────────────────────────────────────────────────────────────

class RessourceTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.ressource = Ressource.objects.create(
            tenant=self.tenant, formateur=self.formateur, titre="Doc", url="https://example.com",
        )

    def test_formateur_cree_avec_url(self):
        res = self.client.post(self.url("ressource"), {"titre": "Lien", "url": "https://docs.test"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["formateur"], self.formateur.id)

    def test_formateur_cree_avec_fichier(self):
        res = self.client.post(self.url("ressource"), {"titre": "Fichier", "fichier": fichier_test("doc.pdf")})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_remplacer_un_lien_par_un_fichier_et_inversement(self):
        url = self.url("ressource", self.ressource.id)
        res = self.client.patch(url, {"fichier": fichier_test("doc.pdf"), "url": ""})
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.ressource.refresh_from_db()
        self.assertIsNone(self.ressource.url)
        self.assertTrue(self.ressource.fichier)

        res = self.client.patch(url, {"url": "https://nouveau.test", "fichier": None}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.ressource.refresh_from_db()
        self.assertEqual(self.ressource.url, "https://nouveau.test")
        self.assertFalse(self.ressource.fichier)

    def test_faux_pdf_refuse(self):
        res = self.client.post(self.url("ressource"),
                               {"titre": "Faux", "fichier": fichier_test("virus.pdf", b"MZ\x90\x00binaire")})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ne correspond pas", str(res.data["fichier"]))

    def test_ressource_de_plus_de_10_mo_refusee(self):
        gros = CONTENU_VALIDE["pdf"] + b"x" * (10 * 1024 * 1024)
        res = self.client.post(self.url("ressource"), {"titre": "Gros", "fichier": fichier_test("gros.pdf", gros)})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("10 Mo", str(res.data["fichier"]))

    @override_settings(TAILLE_MAX_FICHIER_MO=1)
    def test_taille_maximale_reglable(self):
        gros = CONTENU_VALIDE["pdf"] + b"x" * (1024 * 1024)
        res = self.client.post(self.url("ressource"), {"titre": "Gros", "fichier": fichier_test("gros.pdf", gros)})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("1 Mo", str(res.data["fichier"]))

    @override_settings(TAILLE_MAX_FICHIER_MO=25)
    def test_limites_fichiers_exposees(self):
        res = self.client.get("/api/limites-fichiers/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, {"taille_max_fichier_mo": 25})

        self.client.force_authenticate(user=None)
        res = self.client.get("/api/limites-fichiers/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_url_et_fichier_simultanes_refuses(self):
        fichier = fichier_test("doc.pdf")
        res = self.client.post(self.url("ressource"),
                               {"titre": "X", "url": "https://docs.test", "fichier": fichier})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sans_source_refusee(self):
        res = self.client.post(self.url("ressource"), {"titre": "X"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_cree_une_ressource(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.url("ressource"), {"titre": "Lien", "url": "https://docs.test"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_autre_formateur_ne_modifie_ni_ne_supprime(self):
        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(
            self.client.patch(self.url("ressource", self.ressource.id), {"titre": "X"}).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self.client.delete(self.url("ressource", self.ressource.id)).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_createur_et_admin_modifient(self):
        self.assertEqual(
            self.client.patch(self.url("ressource", self.ressource.id), {"titre": "Par le créateur"}).status_code,
            status.HTTP_200_OK,
        )
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(
            self.client.patch(self.url("ressource", self.ressource.id), {"titre": "Par l'admin"}).status_code,
            status.HTTP_200_OK,
        )

    def test_ressource_jointe_a_un_brief_non_supprimable(self):
        self.creer_brief().ressources.add(self.ressource)
        res = self.client.delete(self.url("ressource", self.ressource.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_ressource_libre_supprimable(self):
        res = self.client.delete(self.url("ressource", self.ressource.id))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_apprenant_ne_voit_que_les_ressources_de_ses_briefs(self):
        jointe = Ressource.objects.create(tenant=self.tenant, titre="Jointe", url="https://a.test")
        brouillon = Ressource.objects.create(tenant=self.tenant, titre="Brouillon", url="https://b.test")
        self.creer_brief(statut=Brief.Statut.PUBLIE).ressources.add(jointe)
        self.creer_brief(statut=Brief.Statut.BROUILLON).ressources.add(brouillon)

        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.ids(self.client.get(self.url("ressource"))), [jointe.id])

    def test_telechargement_reserve_a_ceux_qui_voient_la_ressource(self):
        self.client.post(self.url("ressource"), {"titre": "Support", "fichier": fichier_test("support.pdf")})
        ressource = Ressource.objects.get(titre="Support")
        url = f"{self.url('ressource', ressource.id)}telecharger/"

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(res.streaming_content), CONTENU_VALIDE["pdf"])
        self.assertIn("attachment", res["Content-Disposition"])

        # Apprenant : seulement si la ressource est jointe à l'un de ses briefs
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.creer_brief().ressources.add(ressource)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_telechargement_d_un_lien_404(self):
        res = self.client.get(f"{self.url('ressource', self.ressource.id)}telecharger/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_apprenant_ne_cree_pas(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.post(self.url("ressource"), {"titre": "X", "url": "https://docs.test"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_exclu(self):
        self.client.force_authenticate(user=self.admin_saas)
        self.assertEqual(self.client.get(self.url("ressource")).status_code, status.HTTP_403_FORBIDDEN)

    def test_isolation_tenant(self):
        Ressource.objects.create(tenant=self.tenant_2, titre="Autre", url="https://c.test")
        self.assertEqual(self.ids(self.client.get(self.url("ressource"))), [self.ressource.id])


# ─── Assignations ─────────────────────────────────────────────────────────────

class AssignationTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.brief = self.creer_brief()
        self.groupe = Groupe.objects.create(promotion=self.promotion, nom="Groupe A")
        GroupeMembre.objects.create(groupe=self.groupe, apprenant=self.apprenant)

    def assigner(self, **cible):
        return self.client.post(self.url("assignation"), {"brief": self.brief.id, **cible}, format="json")

    def test_assignation_apprenant(self):
        self.assertEqual(self.assigner(apprenant=self.apprenant.id).status_code, status.HTTP_201_CREATED)

    def test_assignation_groupe(self):
        self.assertEqual(self.assigner(groupe=self.groupe.id).status_code, status.HTTP_201_CREATED)

    def test_assignation_apprenant_et_groupe(self):
        res = self.assigner(apprenant=self.apprenant_b.id, groupe=self.groupe.id)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_membre_d_un_groupe_assigne_non_assignable_individuellement(self):
        self.assigner(groupe=self.groupe.id)
        res = self.assigner(apprenant=self.apprenant.id)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Groupe A", str(res.data["apprenant"]))
        # Un apprenant hors du groupe reste assignable
        self.assertEqual(self.assigner(apprenant=self.apprenant_b.id).status_code, status.HTTP_201_CREATED)

    def test_groupe_avec_un_membre_deja_assigne_refuse(self):
        self.assigner(apprenant=self.apprenant.id)
        res = self.assigner(groupe=self.groupe.id)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("déjà assignés individuellement", str(res.data["groupe"]))

    def test_assignation_mixte_avec_un_membre_du_groupe_refusee(self):
        res = self.assigner(apprenant=self.apprenant.id, groupe=self.groupe.id)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_membre_inactif_du_groupe_assignable_individuellement(self):
        self.assigner(groupe=self.groupe.id)
        GroupeMembre.objects.filter(apprenant=self.apprenant).update(actif=False)
        self.assertEqual(self.assigner(apprenant=self.apprenant.id).status_code, status.HTTP_201_CREATED)

    def test_deux_groupes_avec_un_membre_commun(self):
        groupe_b = Groupe.objects.create(promotion=self.promotion, nom="Groupe B")
        GroupeMembre.objects.create(groupe=groupe_b, apprenant=self.apprenant)
        self.assigner(groupe=self.groupe.id)
        res = self.assigner(groupe=groupe_b.id)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("via un autre groupe", str(res.data["groupe"]))

    def multiple(self, **cibles):
        return self.client.post(f"{self.url('assignation')}multiple/", {"brief": self.brief.id, **cibles}, format="json")

    def test_assignation_multiple(self):
        groupe_b = Groupe.objects.create(promotion=self.promotion, nom="Groupe B")
        res = self.multiple(groupes=[self.groupe.id, groupe_b.id], apprenants=[self.apprenant_b.id])
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(len(res.data), 3)
        self.assertEqual(Assignation.objects.filter(brief=self.brief).count(), 3)

    def test_assignation_multiple_tout_ou_rien(self):
        # L'apprenant est aussi dans le groupe choisi : rien n'est créé
        res = self.multiple(groupes=[self.groupe.id], apprenants=[self.apprenant_b.id, self.apprenant.id])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual([(e["type"], e["id"]) for e in res.data["erreurs"]], [("apprenant", self.apprenant.id)])
        self.assertFalse(Assignation.objects.filter(brief=self.brief).exists())

    def test_assignation_multiple_vide_refusee(self):
        self.assertEqual(self.multiple().status_code, status.HTTP_400_BAD_REQUEST)

    def test_assignation_multiple_reservee_au_formateur(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.multiple(apprenants=[self.apprenant.id]).status_code, status.HTTP_403_FORBIDDEN)

    def test_sans_cible_refusee(self):
        self.assertEqual(self.assigner().status_code, status.HTTP_400_BAD_REQUEST)

    def test_doublons_refuses(self):
        self.assigner(apprenant=self.apprenant.id)
        self.assigner(groupe=self.groupe.id)
        self.assertEqual(self.assigner(apprenant=self.apprenant.id).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.assigner(groupe=self.groupe.id).status_code, status.HTTP_400_BAD_REQUEST)

    def test_apprenant_non_inscrit_refuse(self):
        self.assertEqual(self.assigner(apprenant=self.apprenant_hors_promo.id).status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_groupe_d_une_autre_promotion_refuse(self):
        groupe_p2 = Groupe.objects.create(promotion=self.promotion_2, nom="Groupe P2")
        self.assertEqual(self.assigner(groupe=groupe_p2.id).status_code, status.HTTP_400_BAD_REQUEST)

    def test_groupe_desactive_refuse(self):
        self.groupe.actif = False
        self.groupe.save()
        self.assertEqual(self.assigner(groupe=self.groupe.id).status_code, status.HTTP_400_BAD_REQUEST)

    def test_brief_archive_refuse(self):
        self.brief.statut = Brief.Statut.ARCHIVE
        self.brief.save()
        self.assertEqual(self.assigner(apprenant=self.apprenant.id).status_code, status.HTTP_400_BAD_REQUEST)

    def test_promotion_cloturee_refusee(self):
        InscriptionService.cloturer(self.promotion)
        self.assertEqual(self.assigner(groupe=self.groupe.id).status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_non_affecte_refuse(self):
        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(self.assigner(apprenant=self.apprenant.id).status_code, status.HTTP_400_BAD_REQUEST)

    def test_seul_le_formateur_assigne(self):
        for utilisateur in (self.admin, self.apprenant, self.admin_saas):
            self.client.force_authenticate(user=utilisateur)
            self.assertEqual(self.assigner(apprenant=self.apprenant.id).status_code,
                             status.HTTP_403_FORBIDDEN, utilisateur.email)

    def test_pas_de_modification(self):
        assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        res = self.client.patch(self.url("assignation", assignation.id), {"apprenant": self.apprenant_b.id})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_suppression_interdite_avec_livrables(self):
        assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        self.deposer(assignation)
        res = self.client.delete(self.url("assignation", assignation.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Livrable.objects.filter(assignation=assignation).exists())

    def test_suppression_sans_livrable(self):
        assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        res = self.client.delete(self.url("assignation", assignation.id))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_apprenant_voit_ses_assignations_directes_et_de_groupe(self):
        directe = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        via_groupe = Assignation.objects.create(brief=self.brief, groupe=self.groupe)
        Assignation.objects.create(brief=self.brief, apprenant=self.apprenant_b)
        brouillon = self.creer_brief(statut=Brief.Statut.BROUILLON)
        Assignation.objects.create(brief=brouillon, apprenant=self.apprenant)

        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.ids(self.client.get(self.url("assignation"))),
                         sorted([directe.id, via_groupe.id]))

    def test_membre_inactif_ne_voit_plus_l_assignation_de_groupe(self):
        Assignation.objects.create(brief=self.brief, groupe=self.groupe)
        GroupeMembre.objects.filter(apprenant=self.apprenant).update(actif=False)
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.ids(self.client.get(self.url("assignation"))), [])


# ─── Livrables ────────────────────────────────────────────────────────────────

class LivrableTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.brief = self.creer_brief()
        self.assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)

    def deposer_api(self, assignation, fichiers=(), liens=("https://github.com/x/y",), commentaire=""):
        return self.client.post(self.url("livrable"), {
            "assignation": assignation.id,
            "commentaire": commentaire,
            "fichiers": list(fichiers),
            "liens": list(liens),
        })

    def test_apprenant_depose_fichier_et_lien(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.deposer_api(self.assignation, fichiers=[fichier_test("maquette.pdf")],
                               commentaire="Première version")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["deposant"], self.apprenant.id)
        self.assertEqual(res.data["numero"], 1)
        self.assertEqual(res.data["commentaire"], "Première version")
        self.assertFalse(res.data["en_retard"])
        self.assertEqual(sorted(f["type"] for f in res.data["fichiers"]), ["fichier", "lien"])
        # Le chemin sur le disque n'est jamais exposé
        self.assertNotIn("fichier", res.data["fichiers"][0])
        element = FichierLivrable.objects.get(livrable_id=res.data["id"], url__isnull=True)
        self.assertEqual(element.nom, "maquette.pdf")
        self.assertTrue(element.fichier.name.startswith(f"livrables/{self.tenant.id}/{self.brief.id}/"))
        self.assertNotIn("maquette", element.fichier.name)

    def test_numeros_successifs(self):
        self.client.force_authenticate(user=self.apprenant)
        self.deposer_api(self.assignation)
        res = self.deposer_api(self.assignation)
        self.assertEqual(res.data["numero"], 2)

    def test_au_moins_un_element(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.deposer_api(self.assignation, liens=())
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_au_plus_10_elements(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.deposer_api(self.assignation, liens=[f"https://x.test/{i}" for i in range(11)])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_un_fichier_refuse_annule_tout_le_depot(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.deposer_api(self.assignation, fichiers=[fichier_test("ok.pdf"), fichier_test("image.png")])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("image.png", str(res.data["fichiers"]))
        self.assertFalse(Livrable.objects.exists())

    def test_types_et_contenus(self):
        self.client.force_authenticate(user=self.apprenant)
        for nom in ("doc.pdf", "slides.pptx", "rapport.docx", "notes.txt"):
            self.assertEqual(self.deposer_api(self.assignation, fichiers=[fichier_test(nom)]).status_code,
                             status.HTTP_201_CREATED, nom)
        faux = {
            "faux.pdf": b"MZ\x90\x00 executable",
            "slides.docx": CONTENU_VALIDE["pptx"],
            "binaire.txt": b"texte\x00\x01 binaire",
            "image.png": b"png",
        }
        for nom, contenu in faux.items():
            res = self.deposer_api(self.assignation, fichiers=[fichier_test(nom, contenu)])
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, nom)

    def test_taille_maximale_10_mo(self):
        self.client.force_authenticate(user=self.apprenant)
        gros = fichier_test("gros.pdf", CONTENU_VALIDE["pdf"] + b"x" * (10 * 1024 * 1024))
        res = self.deposer_api(self.assignation, fichiers=[gros])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("10 Mo", str(res.data["fichiers"]))

    def test_refuse_avant_la_date_de_debut(self):
        self.brief.date_debut = timezone.now() + timedelta(days=1)
        self.brief.date_limite = timezone.now() + timedelta(days=5)
        self.brief.save()
        self.client.force_authenticate(user=self.apprenant)
        res = self.deposer_api(self.assignation)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ouvrent le", str(res.data["assignation"]))

    def test_accepte_apres_la_date_limite_marque_en_retard(self):
        self.brief.date_debut = timezone.now() - timedelta(days=5)
        self.brief.date_limite = timezone.now() - timedelta(days=1)
        self.brief.save()
        self.client.force_authenticate(user=self.apprenant)
        res = self.deposer_api(self.assignation)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data["en_retard"])

    def test_refuse_sur_brief_archive_brouillon_ou_promotion_cloturee(self):
        self.client.force_authenticate(user=self.apprenant)
        for statut in (Brief.Statut.ARCHIVE, Brief.Statut.BROUILLON):
            self.brief.statut = statut
            self.brief.save()
            self.assertEqual(self.deposer_api(self.assignation).status_code, status.HTTP_400_BAD_REQUEST, statut)
        self.brief.statut = Brief.Statut.PUBLIE
        self.brief.save()
        self.promotion.actif = False
        self.promotion.save()
        self.assertEqual(self.deposer_api(self.assignation).status_code, status.HTTP_400_BAD_REQUEST)

    def test_ni_modification_ni_suppression(self):
        livrable = self.deposer(self.assignation)
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.client.patch(self.url("livrable", livrable.id), {"commentaire": "x"}).status_code,
                         status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.delete(self.url("livrable", livrable.id)).status_code,
                         status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_membre_d_un_groupe_depose_pour_le_groupe(self):
        groupe = Groupe.objects.create(promotion=self.promotion, nom="Groupe B")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant)
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_b)
        assignation = Assignation.objects.create(brief=self.creer_brief(), groupe=groupe)
        self.client.force_authenticate(user=self.apprenant_b)
        res = self.deposer_api(assignation)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["cible"], {"type": "groupe", "id": groupe.id, "nom": "Groupe B"})
        # L'autre membre voit le dépôt du groupe
        self.client.force_authenticate(user=self.apprenant)
        self.assertIn(res.data["id"], self.ids(self.client.get(self.url("livrable"))))

    def test_apprenant_ne_depose_pas_pour_un_autre(self):
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.deposer_api(self.assignation).status_code, status.HTTP_400_BAD_REQUEST)

    def test_membre_du_groupe_depose_sur_assignation_apprenant_et_groupe(self):
        # Audit B5 : l'assignation vise un apprenant ET un groupe
        groupe = Groupe.objects.create(promotion=self.promotion, nom="Groupe B")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_b)
        mixte = Assignation.objects.create(brief=self.creer_brief(), apprenant=self.apprenant, groupe=groupe)
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.deposer_api(mixte).status_code, status.HTTP_201_CREATED)

    def test_membre_inactif_du_groupe_ne_depose_pas(self):
        groupe = Groupe.objects.create(promotion=self.promotion, nom="Groupe B")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_b, actif=False)
        assignation = Assignation.objects.create(brief=self.brief, groupe=groupe)
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.deposer_api(assignation).status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_ne_depose_pas(self):
        self.assertEqual(self.deposer_api(self.assignation).status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_exclu(self):
        self.client.force_authenticate(user=self.admin_saas)
        self.assertEqual(self.client.get(self.url("livrable")).status_code, status.HTTP_403_FORBIDDEN)


class VisibiliteLivrableTests(ActivitesBaseTestCase):
    """Audit S1 et travaux des pairs (visibles après son propre dépôt)."""

    def setUp(self):
        super().setUp()
        self.brief = self.creer_brief()
        self.a_apprenant = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        self.a_b = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant_b)
        self.v1_b = self.deposer(self.a_b, self.apprenant_b)
        self.v2_b = self.deposer(self.a_b, self.apprenant_b)

    def liste(self, utilisateur, params=""):
        self.client.force_authenticate(user=utilisateur)
        return self.ids(self.client.get(self.url("livrable") + params))

    def test_sans_depot_ne_voit_pas_les_pairs(self):
        self.assertEqual(self.liste(self.apprenant), [])
        self.assertEqual(self.liste(self.apprenant_hors_promo), [])

    def test_apres_son_depot_voit_le_dernier_depot_des_pairs(self):
        mien = self.deposer(self.a_apprenant)
        self.assertEqual(self.liste(self.apprenant), sorted([mien.id, self.v2_b.id]))

    def test_voit_tous_ses_depots(self):
        self.assertEqual(self.liste(self.apprenant_b), sorted([self.v1_b.id, self.v2_b.id]))

    def test_pairs_d_un_autre_brief_invisibles(self):
        autre = self.creer_brief()
        a_autre = Assignation.objects.create(brief=autre, apprenant=self.apprenant_b)
        pair_autre_brief = self.deposer(a_autre, self.apprenant_b)
        self.deposer(self.a_apprenant)
        self.assertNotIn(pair_autre_brief.id, self.liste(self.apprenant))

    def test_formateurs_et_admin(self):
        self.assertEqual(self.liste(self.formateur), sorted([self.v1_b.id, self.v2_b.id]))
        self.assertEqual(self.liste(self.admin), sorted([self.v1_b.id, self.v2_b.id]))
        self.assertEqual(self.liste(self.formateur_2), [])

    def test_filtres(self):
        mien = self.deposer(self.a_apprenant)
        self.assertEqual(self.liste(self.formateur, f"?assignation={self.a_apprenant.id}"), [mien.id])
        self.assertEqual(len(self.liste(self.formateur, f"?brief={self.brief.id}")), 3)


class MessageDuDepotTests(ActivitesBaseTestCase):
    """Le message d'un dépôt : visible de ses auteurs et des formateurs, pas des pairs."""

    def setUp(self):
        super().setUp()
        brief = self.creer_brief()
        self.a_a = Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        self.a_b = Assignation.objects.create(brief=brief, apprenant=self.apprenant_b)
        Livrable.objects.create(assignation=self.a_a, deposant=self.apprenant, numero=1, commentaire="Mon message")
        Livrable.objects.create(assignation=self.a_b, deposant=self.apprenant_b, numero=1, commentaire="Message de B")

    def messages(self, utilisateur):
        self.client.force_authenticate(user=utilisateur)
        return {l["assignation"]: l["commentaire"] for l in self.client.get(self.url("livrable")).data}

    def test_pair_ne_voit_pas_le_message(self):
        self.assertEqual(self.messages(self.apprenant), {self.a_a.id: "Mon message", self.a_b.id: ""})

    def test_formateur_et_admin_le_voient(self):
        attendu = {self.a_a.id: "Mon message", self.a_b.id: "Message de B"}
        self.assertEqual(self.messages(self.formateur), attendu)
        self.assertEqual(self.messages(self.admin), attendu)

    def test_membre_du_groupe_voit_le_message_du_groupe(self):
        groupe = Groupe.objects.create(promotion=self.promotion, nom="G")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant)
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_b)
        a_g = Assignation.objects.create(brief=self.creer_brief(), groupe=groupe)
        Livrable.objects.create(assignation=a_g, deposant=self.apprenant_b, numero=1, commentaire="Pour le groupe")
        self.assertEqual(self.messages(self.apprenant)[a_g.id], "Pour le groupe")


class FichierLivrableTests(ActivitesBaseTestCase):
    """Éléments des dépôts : lecture seule, téléchargement contrôlé."""

    def setUp(self):
        super().setUp()
        brief = self.creer_brief()
        self.a_apprenant = Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        self.a_b = Assignation.objects.create(brief=brief, apprenant=self.apprenant_b)
        self.client.force_authenticate(user=self.apprenant_b)
        res = self.client.post(self.url("livrable"), {
            "assignation": self.a_b.id,
            "fichiers": [fichier_test("rapport.pdf")],
            "liens": ["https://github.com/x/y"],
        })
        self.depot_b = Livrable.objects.get(pk=res.data["id"])
        self.fichier_b = self.depot_b.fichiers.get(url__isnull=True)
        self.lien_b = self.depot_b.fichiers.get(url__isnull=False)

    def telecharger(self, utilisateur, element):
        self.client.force_authenticate(user=utilisateur)
        return self.client.get(f"{self.url('fichier-livrable', element.id)}telecharger/")

    def test_deposant_telecharge_son_fichier(self):
        res = self.telecharger(self.apprenant_b, self.fichier_b)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(res.streaming_content), CONTENU_VALIDE["pdf"])
        self.assertIn('attachment; filename="rapport.pdf"', res["Content-Disposition"])
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")

    def test_pair_telecharge_seulement_apres_son_propre_depot(self):
        self.assertEqual(self.telecharger(self.apprenant, self.fichier_b).status_code, status.HTTP_404_NOT_FOUND)
        self.deposer(self.a_apprenant)
        self.assertEqual(self.telecharger(self.apprenant, self.fichier_b).status_code, status.HTTP_200_OK)

    def test_hors_promotion_et_formateur_non_affecte(self):
        self.assertEqual(self.telecharger(self.apprenant_hors_promo, self.fichier_b).status_code,
                         status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.telecharger(self.formateur_2, self.fichier_b).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.telecharger(self.formateur, self.fichier_b).status_code, status.HTTP_200_OK)

    def test_un_lien_ne_se_telecharge_pas(self):
        self.assertEqual(self.telecharger(self.apprenant_b, self.lien_b).status_code, status.HTTP_404_NOT_FOUND)

    def test_elements_en_lecture_seule(self):
        self.client.force_authenticate(user=self.apprenant_b)
        res = self.client.post(self.url("fichier-livrable"),
                               {"livrable": self.depot_b.id, "nom": "X", "url": "https://x.test"})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


# ─── Consultation et aperçus ──────────────────────────────────────────────────

class ReponseGotenberg:
    def __init__(self, status_code=200, content=b"%PDF-1.7\n% apercu\n"):
        self.status_code = status_code
        self.content = content


@override_settings(APERCU_OFFICE_ACTIF=True)
@patch("activites.tasks.generer_apercu.delay")
class ConsultationTests(ActivitesBaseTestCase):
    """
    Consultation dans la plateforme : PDF et TXT tels quels, fichiers Office
    via un aperçu PDF produit par Gotenberg (appel simulé).
    """

    def setUp(self):
        super().setUp()
        brief = self.creer_brief()
        self.a_apprenant = Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        self.a_b = Assignation.objects.create(brief=brief, apprenant=self.apprenant_b)

    def deposer_fichier(self, fichier, apprenant=None, assignation=None):
        self.client.force_authenticate(user=apprenant or self.apprenant_b)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(self.url("livrable"), {
                "assignation": (assignation or self.a_b).id, "fichiers": [fichier],
            })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return FichierLivrable.objects.get(livrable_id=res.data["id"])

    def consulter(self, element, utilisateur=None):
        self.client.force_authenticate(user=utilisateur or self.apprenant_b)
        return self.client.get(f"{self.url('fichier-livrable', element.id)}consulter/")

    def generer(self, element, reponse=None, retries=0):
        from .tasks import generer_apercu
        with patch("activites.tasks.requests.post", return_value=reponse or ReponseGotenberg()) as post:
            generer_apercu.apply(args=["activites.fichierlivrable", element.id], retries=retries)
        element.refresh_from_db()
        return post

    def test_pdf_affiche_tel_quel(self, delay):
        element = self.deposer_fichier(fichier_test("rapport.pdf"))
        res = self.consulter(element)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn("inline", res["Content-Disposition"])
        self.assertEqual(b"".join(res.streaming_content), CONTENU_VALIDE["pdf"])
        self.assertEqual(element.apercu_statut, "")
        delay.assert_not_called()

    def test_txt_renvoye_en_utf8(self, delay):
        element = self.deposer_fichier(fichier_test("notes.txt", "Élève à l'œuvre".encode("cp1252")))
        res = self.consulter(element)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(res.content.decode("utf-8"), "Élève à l'œuvre")

    def test_un_lien_ne_se_consulte_pas(self, delay):
        self.client.force_authenticate(user=self.apprenant_b)
        res = self.client.post(self.url("livrable"), {"assignation": self.a_b.id, "liens": ["https://x.test"]})
        lien = FichierLivrable.objects.get(livrable_id=res.data["id"])
        self.assertEqual(self.consulter(lien).status_code, status.HTTP_404_NOT_FOUND)

    def test_office_converti_au_depot(self, delay):
        element = self.deposer_fichier(fichier_test("slides.pptx"))
        self.assertEqual(element.apercu_statut, "EN_COURS")
        delay.assert_called_once_with("activites.fichierlivrable", element.id)

        # Pas encore prêt : 202
        res = self.consulter(element)
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["apercu"], "EN_COURS")

        post = self.generer(element)
        self.assertEqual(post.call_args.args[0], "http://gotenberg:3000/forms/libreoffice/convert")
        self.assertEqual(post.call_args.kwargs["data"], {"metadata": '{"Title": "slides.pptx"}'})
        self.assertEqual(element.apercu_statut, "PRET")
        self.assertTrue(element.apercu.name.startswith(f"apercus/{self.tenant.id}/"))

        res = self.consulter(element)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn('inline; filename="slides.pptx.pdf"', res["Content-Disposition"])
        self.assertTrue(b"".join(res.streaming_content).startswith(b"%PDF-"))

        # L'original reste téléchargeable
        res = self.client.get(f"{self.url('fichier-livrable', element.id)}telecharger/")
        self.assertEqual(b"".join(res.streaming_content), CONTENU_VALIDE["pptx"])

    def test_conversion_refusee_par_gotenberg(self, delay):
        element = self.deposer_fichier(fichier_test("rapport.docx"))
        self.generer(element, ReponseGotenberg(status_code=500, content=b"erreur"))
        self.assertEqual(element.apercu_statut, "ECHEC")
        res = self.consulter(element)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res.data["apercu"], "ECHEC")

    def test_reponse_qui_n_est_pas_un_pdf(self, delay):
        element = self.deposer_fichier(fichier_test("rapport.docx"))
        self.generer(element, ReponseGotenberg(content=b"<html>pas un pdf</html>"))
        self.assertEqual(element.apercu_statut, "ECHEC")
        self.assertFalse(element.apercu)

    def test_gotenberg_injoignable_echec_apres_les_nouvelles_tentatives(self, delay):
        import requests
        from .tasks import generer_apercu
        element = self.deposer_fichier(fichier_test("rapport.docx"))
        with patch("activites.tasks.requests.post", side_effect=requests.ConnectionError("injoignable")):
            generer_apercu.apply(args=["activites.fichierlivrable", element.id], retries=3)
        element.refresh_from_db()
        self.assertEqual(element.apercu_statut, "ECHEC")

    def test_fichier_depose_avant_les_apercus_converti_a_la_demande(self, delay):
        with override_settings(APERCU_OFFICE_ACTIF=False):
            element = self.deposer_fichier(fichier_test("slides.pptx"))
            self.assertEqual(element.apercu_statut, "")
            # Conversion coupée : aperçu indisponible, rien n'est lancé
            res = self.consulter(element)
            self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
            self.assertEqual(res.data["apercu"], "INDISPONIBLE")
        delay.assert_not_called()

        with self.captureOnCommitCallbacks(execute=True):
            res = self.consulter(element)
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        delay.assert_called_once_with("activites.fichierlivrable", element.id)

    def test_memes_droits_que_le_telechargement(self, delay):
        element = self.deposer_fichier(fichier_test("rapport.pdf"))
        # Pair : seulement après son propre dépôt
        self.assertEqual(self.consulter(element, self.apprenant).status_code, status.HTTP_404_NOT_FOUND)
        self.deposer(self.a_apprenant)
        self.assertEqual(self.consulter(element, self.apprenant).status_code, status.HTTP_200_OK)
        self.assertEqual(self.consulter(element, self.formateur_2).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.consulter(element, self.formateur).status_code, status.HTTP_200_OK)

    def test_extension_exposee(self, delay):
        element = self.deposer_fichier(fichier_test("slides.pptx"))
        self.client.force_authenticate(user=self.apprenant_b)
        res = self.client.get(self.url("fichier-livrable", element.id))
        self.assertEqual(res.data["extension"], "pptx")


@override_settings(APERCU_OFFICE_ACTIF=True)
@patch("activites.tasks.generer_apercu.delay")
class ConsultationRessourceTests(ActivitesBaseTestCase):
    def creer(self, fichier):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(self.url("ressource"), {"titre": "Support", "fichier": fichier})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return Ressource.objects.get(pk=res.data["id"])

    def test_ressource_office_convertie_puis_consultee(self, delay):
        from .tasks import generer_apercu
        ressource = self.creer(fichier_test("cours.pptx"))
        self.assertEqual(ressource.apercu_statut, "EN_COURS")
        delay.assert_called_once_with("activites.ressource", ressource.id)

        with patch("activites.tasks.requests.post", return_value=ReponseGotenberg()):
            generer_apercu.apply(args=["activites.ressource", ressource.id])
        res = self.client.get(f"{self.url('ressource', ressource.id)}consulter/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('inline; filename="Support.pptx.pdf"', res["Content-Disposition"])

        # Apprenant : seulement si la ressource est jointe à l'un de ses briefs
        self.client.force_authenticate(user=self.apprenant)
        url = f"{self.url('ressource', ressource.id)}consulter/"
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.creer_brief().ressources.add(ressource)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_remplacement_et_suppression_nettoient_le_disque(self, delay):
        from .tasks import generer_apercu
        ressource = self.creer(fichier_test("cours.docx"))
        with patch("activites.tasks.requests.post", return_value=ReponseGotenberg()):
            generer_apercu.apply(args=["activites.ressource", ressource.id])
        ressource.refresh_from_db()
        ancien, ancien_apercu = ressource.fichier.path, ressource.apercu.path

        # Nouveau fichier : l'ancien et son aperçu disparaissent, nouvel aperçu demandé
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.patch(self.url("ressource", ressource.id), {"fichier": fichier_test("cours2.pptx")})
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        ressource.refresh_from_db()
        self.assertFalse(os.path.exists(ancien))
        self.assertFalse(os.path.exists(ancien_apercu))
        self.assertFalse(ressource.apercu)
        self.assertEqual(ressource.apercu_statut, "EN_COURS")

        # Passage à un lien : plus de fichier ni d'aperçu
        fichier = ressource.fichier.path
        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch(self.url("ressource", ressource.id), {"url": "https://x.test", "fichier": None}, format="json")
        ressource.refresh_from_db()
        self.assertFalse(os.path.exists(fichier))
        self.assertEqual(ressource.apercu_statut, "")

        # Suppression : fichier effacé du disque
        ressource = self.creer(fichier_test("support.pdf"))
        fichier = ressource.fichier.path
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(self.url("ressource", ressource.id))
        self.assertFalse(os.path.exists(fichier))


# ─── Évaluations ──────────────────────────────────────────────────────────────

@patch("activites.notifications.envoyer_email_evaluation.delay")
class EvaluationTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.niveau_2 = Niveau.objects.create(tenant=self.tenant, nom="Adapter", ordre=2)
        self.cn2 = CompetenceNiveau.objects.create(competence=self.cn.competence, niveau=self.niveau_2)
        self.brief = self.creer_brief(cree_par=self.formateur)
        self.brief.competence_niveaux.set([self.cn, self.cn2])
        self.assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        self.deposer(self.assignation)
        # Autre formateur de la même promotion
        self.formateur_3 = self.membre("formateur3@test.com", MembreTenant.Role.FORMATEUR)
        FormateurPromotion.objects.create(formateur=self.formateur_3, promotion=self.promotion)

    def evaluer(self, assignation=None, acquis=(True, False), utilisateur=None, commentaire="Bon travail"):
        self.client.force_authenticate(user=utilisateur or self.formateur)
        lignes = [
            {"competence_niveau": cn.id, "acquis": a}
            for cn, a in zip((self.cn, self.cn2), acquis)
        ]
        return self.client.post(self.url("evaluation"), {
            "assignation": (assignation or self.assignation).id,
            "commentaire": commentaire,
            "competences": lignes,
        }, format="json")

    def test_createur_du_brief_evalue(self, email):
        res = self.evaluer()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["evaluateur"], self.formateur.id)
        self.assertEqual(res.data["commentaire"], "Bon travail")
        self.assertEqual(res.data["cible"]["type"], "apprenant")
        lignes = {l["competence_niveau"]: l for l in res.data["competences"]}
        self.assertTrue(lignes[self.cn.id]["acquis"])
        self.assertEqual(lignes[self.cn2.id]["niveau_nom"], "Adapter")
        self.assertEqual(
            list(CompetenceValidee.objects.filter(apprenant=self.apprenant).values_list("competence_niveau", flat=True)),
            [self.cn.id],
        )

    def test_evaluation_et_competence_validee_notifiees(self, email):
        with self.captureOnCommitCallbacks(execute=True):
            self.evaluer()
        evaluation = Notification.objects.get(utilisateur=self.apprenant, type=Notification.Type.EVALUATION)
        self.assertIn("1 compétence(s) acquise(s) sur 2", evaluation.message)
        competence = Notification.objects.get(utilisateur=self.apprenant, type=Notification.Type.COMPETENCE)
        self.assertEqual(competence.titre, "Compétence validée")
        self.assertEqual(competence.message, "Développer une API (Imiter)")
        self.assertEqual((competence.tenant, competence.lien), (self.tenant, "/ma-progression"))

    def test_competence_deja_validee_pas_renotifiee(self, email):
        CompetenceValidee.objects.create(apprenant=self.apprenant, competence_niveau=self.cn)
        self.evaluer()
        self.assertFalse(Notification.objects.filter(type=Notification.Type.COMPETENCE).exists())

    def test_autre_formateur_de_la_promotion_refuse(self, email):
        self.assertEqual(self.evaluer(utilisateur=self.formateur_3).status_code, status.HTTP_403_FORBIDDEN)

    def test_createur_desaffecte_un_autre_formateur_evalue(self, email):
        FormateurPromotion.objects.filter(formateur=self.formateur, promotion=self.promotion).delete()
        self.assertEqual(self.evaluer().status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.evaluer(utilisateur=self.formateur_3).status_code, status.HTTP_201_CREATED)

    def test_hors_promotion_admin_et_apprenant_refuses(self, email):
        for u in (self.formateur_2, self.admin, self.apprenant):
            self.assertEqual(self.evaluer(utilisateur=u).status_code, status.HTTP_403_FORBIDDEN, u.email)

    def test_toutes_les_competences_visees_une_fois(self, email):
        self.client.force_authenticate(user=self.formateur)
        for lignes in (
            [{"competence_niveau": self.cn.id, "acquis": True}],
            [{"competence_niveau": self.cn.id, "acquis": True}, {"competence_niveau": self.cn.id, "acquis": True}],
            [{"competence_niveau": self.cn.id, "acquis": True}, {"competence_niveau": self.cn2.id, "acquis": True},
             {"competence_niveau": self.cn_module_2.id, "acquis": True}],
        ):
            res = self.client.post(self.url("evaluation"),
                                   {"assignation": self.assignation.id, "competences": lignes}, format="json")
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("competences", res.data)

    def test_sans_depot_rien_ne_peut_etre_acquis(self, email):
        sans_depot = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant_b)
        res = self.evaluer(assignation=sans_depot)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.evaluer(assignation=sans_depot, acquis=(False, False)).status_code,
                         status.HTTP_201_CREATED)

    def test_validation_definitive(self, email):
        self.evaluer(acquis=(True, False))
        self.deposer(self.assignation)
        res = self.evaluer(acquis=(False, True))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("définitif", str(res.data["competences"]))
        # Réévaluation : ce qui manquait est désormais acquis ; historique gardé
        self.assertEqual(self.evaluer(acquis=(True, True)).status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.assignation.evaluations.count(), 2)
        self.assertEqual(CompetenceValidee.objects.filter(apprenant=self.apprenant).count(), 2)

    def test_reevaluation_seulement_apres_un_nouveau_depot(self, email):
        self.assertEqual(self.evaluer(acquis=(True, False)).status_code, status.HTTP_201_CREATED)
        res = self.evaluer(acquis=(True, True))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nouveau dépôt", str(res.data["assignation"]))

        # Nouveau dépôt : seule la compétence non acquise est à évaluer,
        # la compétence acquise est reprise automatiquement
        self.deposer(self.assignation)
        self.client.force_authenticate(user=self.formateur)
        res = self.client.post(self.url("evaluation"), {
            "assignation": self.assignation.id,
            "competences": [{"competence_niveau": self.cn2.id, "acquis": True}],
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual({l["competence_niveau"]: l["acquis"] for l in res.data["competences"]},
                         {self.cn.id: True, self.cn2.id: True})

    def test_competence_restante_obligatoire(self, email):
        self.evaluer(acquis=(True, False))
        self.deposer(self.assignation)
        self.client.force_authenticate(user=self.formateur)
        res = self.client.post(self.url("evaluation"), {
            "assignation": self.assignation.id,
            "competences": [{"competence_niveau": self.cn.id, "acquis": True}],
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("competences", res.data)

    def test_rendu_valide_ferme(self, email):
        self.evaluer(acquis=(True, True))
        self.deposer(self.assignation)  # en base, hors API
        res = self.evaluer(acquis=(True, True))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("validé", str(res.data["assignation"]))

        # Dépôt par l'API refusé
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.post(self.url("livrable"), {"assignation": self.assignation.id, "liens": ["https://x.test"]})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("validé", str(res.data["assignation"]))

    def test_competence_validee_ailleurs_le_reste(self, email):
        self.evaluer(acquis=(True, False))
        autre = self.creer_brief(cree_par=self.formateur)
        autre.competence_niveaux.set([self.cn, self.cn2])
        a = Assignation.objects.create(brief=autre, apprenant=self.apprenant)
        self.deposer(a)
        self.assertEqual(self.evaluer(assignation=a, acquis=(False, False)).status_code, status.HTTP_201_CREATED)
        self.assertTrue(CompetenceValidee.objects.filter(apprenant=self.apprenant, competence_niveau=self.cn).exists())

    def test_groupe_valide_pour_ses_membres_actifs(self, email):
        groupe = Groupe.objects.create(promotion=self.promotion, nom="G1")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant)
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_b, actif=False)
        brief = self.creer_brief(cree_par=self.formateur)
        brief.competence_niveaux.set([self.cn, self.cn2])
        a = Assignation.objects.create(brief=brief, groupe=groupe)
        self.deposer(a)
        self.assertEqual(self.evaluer(assignation=a, acquis=(True, True)).status_code, status.HTTP_201_CREATED)
        self.assertEqual(CompetenceValidee.objects.filter(apprenant=self.apprenant).count(), 2)
        self.assertFalse(CompetenceValidee.objects.filter(apprenant=self.apprenant_b).exists())

    def test_brouillon_et_promotion_cloturee_refuses(self, email):
        Brief.objects.filter(pk=self.brief.pk).update(statut=Brief.Statut.BROUILLON)
        self.assertEqual(self.evaluer().status_code, status.HTTP_400_BAD_REQUEST)
        Brief.objects.filter(pk=self.brief.pk).update(statut=Brief.Statut.ARCHIVE)
        Promotion.objects.filter(pk=self.promotion.pk).update(actif=False)
        self.assertEqual(self.evaluer().status_code, status.HTTP_400_BAD_REQUEST)

    def test_brief_sans_competence_commentaire_seul(self, email):
        brief = self.creer_brief(cree_par=self.formateur)
        a = Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        self.client.force_authenticate(user=self.formateur)
        res = self.client.post(self.url("evaluation"), {"assignation": a.id, "commentaire": "Vu"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_visibilite(self, email):
        self.evaluer()
        a_b = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant_b)
        self.evaluer(assignation=a_b, acquis=(False, False))

        self.client.force_authenticate(user=self.apprenant)
        res = self.client.get(self.url("evaluation"))
        self.assertEqual([e["assignation"] for e in res.data], [self.assignation.id])
        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(self.client.get(self.url("evaluation")).data, [])
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(len(self.client.get(self.url("evaluation")).data), 2)
        res = self.client.get(self.url("evaluation"), {"assignation": a_b.id})
        self.assertEqual(len(res.data), 1)

    def test_ni_modifiee_ni_supprimee(self, email):
        evaluation_id = self.evaluer().data["id"]
        url = self.url("evaluation", evaluation_id)
        self.assertEqual(self.client.patch(url, {"commentaire": "x"}).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_brief_indique_qui_peut_evaluer(self, email):
        self.client.force_authenticate(user=self.formateur)
        data = self.client.get(self.url("brief", self.brief.id)).data
        self.assertTrue(data["peut_evaluer"])
        self.assertEqual(
            [(c["competence"], c["niveau"]) for c in data["competences_visees"]],
            [("Développer une API", "Imiter"), ("Développer une API", "Adapter")],
        )
        self.client.force_authenticate(user=self.formateur_3)
        self.assertFalse(self.client.get(self.url("brief", self.brief.id)).data["peut_evaluer"])

    def test_email_a_l_apprenant(self, email):
        with self.captureOnCommitCallbacks(execute=True):
            self.evaluer(acquis=(True, False))
        email.assert_called_once()
        destinataires, titre, evaluateur, nb_acquises, nb_visees, url = email.call_args.args
        self.assertEqual(destinataires, [self.apprenant.email])
        self.assertEqual((nb_acquises, nb_visees), (1, 2))
        self.assertTrue(url.endswith(f"/activites/{self.brief.id}"))

    def test_suppression_en_cascade_d_un_organisme_evalue(self, email):
        self.evaluer(acquis=(True, True))
        self.tenant.delete()
        self.assertFalse(Evaluation.objects.exists())
        self.assertFalse(CompetenceValidee.objects.exists())

    def test_niveau_de_competence_evalue_non_retirable(self, email):
        self.evaluer()
        self.brief.competence_niveaux.remove(self.cn2)
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete(f"/api/tenants/{self.tenant.id}/competence-niveaux/{self.cn2.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


# ─── Progression ──────────────────────────────────────────────────────────────

@patch("activites.notifications.envoyer_email_evaluation.delay")
class ProgressionTests(ActivitesBaseTestCase):
    """Référentiel de la formation : cn et cn_module_2 (module_2), plus cn2."""

    def setUp(self):
        super().setUp()
        self.cn2 = CompetenceNiveau.objects.create(
            competence=self.cn.competence, niveau=Niveau.objects.create(tenant=self.tenant, nom="Adapter", ordre=2)
        )
        self.brief = self.creer_brief(cree_par=self.formateur)
        self.brief.competence_niveaux.set([self.cn, self.cn2])
        self.assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        # Brief sans compétence : hors compteur des briefs
        Assignation.objects.create(brief=self.creer_brief(cree_par=self.formateur), apprenant=self.apprenant)

    def evaluer(self, acquis):
        self.client.force_authenticate(user=self.formateur)
        res = self.client.post(self.url("evaluation"), {
            "assignation": self.assignation.id,
            "competences": [{"competence_niveau": cn.id, "acquis": a} for cn, a in zip((self.cn, self.cn2), acquis)],
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def moi(self):
        self.client.force_authenticate(user=self.apprenant)
        return self.client.get(f"/api/tenants/{self.tenant.id}/progression/moi/")

    def test_compteurs(self, email):
        res = self.moi()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["resume"], {
            "competences_validees": 0, "competences_total": 3, "briefs_valides": 0, "briefs_total": 1,
        })
        self.assertEqual(res.data["briefs"][0]["statut"], "NON_RENDU")

        self.deposer(self.assignation)
        self.assertEqual({b["id"]: b["statut"] for b in self.moi().data["briefs"]}[self.brief.id], "A_EVALUER")

        self.evaluer((True, False))
        data = self.moi().data
        self.assertEqual(data["resume"]["competences_validees"], 1)
        self.assertEqual(data["resume"]["briefs_valides"], 0)
        etat = {b["id"]: b for b in data["briefs"]}[self.brief.id]
        self.assertEqual((etat["statut"], etat["nb_acquises"], etat["nb_visees"]), ("NON_VALIDE", 1, 2))

        # Nouveau dépôt : à réévaluer
        self.deposer(self.assignation)
        self.assertEqual({b["id"]: b["statut"] for b in self.moi().data["briefs"]}[self.brief.id], "A_EVALUER")

        self.evaluer((True, True))
        data = self.moi().data
        self.assertEqual(data["resume"], {
            "competences_validees": 2, "competences_total": 3, "briefs_valides": 1, "briefs_total": 1,
        })

    def test_referentiel_detaille(self, email):
        self.deposer(self.assignation)
        self.evaluer((True, False))
        modules = self.moi().data["modules"]
        self.assertEqual([m["nom"] for m in modules], ["Développement Web", "Base de données"])
        niveaux = modules[0]["competences"][0]["niveaux"]
        self.assertEqual([(n["niveau"], n["valide"]) for n in niveaux], [("Imiter", True), ("Adapter", False)])

    def test_liste_par_promotion(self, email):
        self.client.force_authenticate(user=self.formateur)
        res = self.client.get(self.url("progression"), {"promotion": self.promotion.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(sorted(r["apprenant"] for r in res.data), sorted([self.apprenant.id, self.apprenant_b.id]))
        self.assertEqual(res.data[0]["competences_total"], 3)

        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get(self.url("progression"), {"promotion": self.promotion.id}).status_code,
                         status.HTTP_200_OK)
        for u in (self.formateur_2, self.apprenant):
            self.client.force_authenticate(user=u)
            self.assertEqual(self.client.get(self.url("progression"), {"promotion": self.promotion.id}).status_code,
                             status.HTTP_404_NOT_FOUND)

    def test_detail_d_un_apprenant(self, email):
        url = self.url("progression", self.apprenant.id)
        self.client.force_authenticate(user=self.formateur)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.client.get(url).data["apprenant"]["id"], self.apprenant.id)

    def test_apprenant_sans_promotion(self, email):
        self.client.force_authenticate(user=self.apprenant_hors_promo)
        res = self.client.get(f"/api/tenants/{self.tenant.id}/progression/moi/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


# ─── Feedback entre pairs ─────────────────────────────────────────────────────

@patch("activites.notifications.envoyer_email_commentaire.delay")
class CommentairePairTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.apprenant_c = self.membre("apprenant_c@test.com", MembreTenant.Role.APPRENANT)
        InscriptionPromotion.objects.create(promotion=self.promotion, apprenant=self.apprenant_c)
        self.brief = self.creer_brief(cree_par=self.formateur)
        self.a_a = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        self.a_b = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant_b)
        self.a_c = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant_c)
        self.deposer(self.a_a)
        self.deposer(self.a_b, self.apprenant_b)

    def commenter(self, auteur, assignation, texte="Beau travail !", parent=None):
        self.client.force_authenticate(user=auteur)
        data = {"assignation": assignation.id, "texte": texte}
        if parent:
            data["parent"] = parent
        return self.client.post(self.url("commentaire"), data, format="json")

    def lister(self, utilisateur, **params):
        self.client.force_authenticate(user=utilisateur)
        return self.client.get(self.url("commentaire"), params).data

    def test_commenter_le_rendu_d_un_pair(self, email):
        res = self.commenter(self.apprenant, self.a_b, "  Bonne idée la navigation  ")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["texte"], "Bonne idée la navigation")
        self.assertEqual(res.data["auteur_nom"], "Prenom Nom")
        self.assertTrue(res.data["peut_modifier"])

    def test_reponse_notifie_l_auteur_d_origine_pas_le_repondant(self, email):
        c = self.commenter(self.apprenant, self.a_b).data["id"]
        self.assertEqual(
            list(Notification.objects.values_list("utilisateur", "titre")),
            [(self.apprenant_b.id, "Nouveau commentaire")],
        )
        Notification.objects.all().delete()
        self.commenter(self.apprenant_b, self.a_b, "Merci !", parent=c)
        self.assertEqual(
            list(Notification.objects.values_list("utilisateur", "titre")),
            [(self.apprenant.id, "Réponse à un commentaire")],
        )

    def test_sans_depot_pas_de_commentaire(self, email):
        res = self.commenter(self.apprenant_c, self.a_b)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Déposez d'abord", str(res.data))

    def test_pas_sur_son_propre_rendu_ni_sur_un_rendu_sans_depot(self, email):
        self.assertEqual(self.commenter(self.apprenant, self.a_a).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.commenter(self.apprenant, self.a_c).status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_et_admin_ne_commentent_pas(self, email):
        for u in (self.formateur, self.admin):
            self.assertEqual(self.commenter(u, self.a_b).status_code, status.HTTP_403_FORBIDDEN)

    def test_reponse_un_seul_niveau(self, email):
        c = self.commenter(self.apprenant, self.a_b).data["id"]
        # L'apprenant commenté répond sur son propre rendu
        r = self.commenter(self.apprenant_b, self.a_b, "Merci !", parent=c)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertEqual(self.commenter(self.apprenant, self.a_b, "Et ?", parent=r.data["id"]).status_code,
                         status.HTTP_400_BAD_REQUEST)
        # Parent d'un autre rendu
        self.assertEqual(self.commenter(self.apprenant_b, self.a_a, "x", parent=c).status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_texte_vide_ou_trop_long(self, email):
        self.assertEqual(self.commenter(self.apprenant, self.a_b, "   ").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.commenter(self.apprenant, self.a_b, "x" * 2001).status_code, status.HTTP_400_BAD_REQUEST)

    def test_auteur_seul_modifie_et_supprime(self, email):
        c = self.commenter(self.apprenant, self.a_b).data["id"]
        url = self.url("commentaire", c)
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.client.patch(url, {"texte": "Pirate"}, format="json").status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.apprenant)
        res = self.client.patch(url, {"texte": "Corrigé", "assignation": self.a_a.id}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual((res.data["texte"], res.data["assignation"]), ("Corrigé", self.a_b.id))
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)

    def test_visibilite(self, email):
        c = self.commenter(self.apprenant, self.a_b).data["id"]
        self.assertEqual([x["id"] for x in self.lister(self.apprenant_b)], [c])  # commenté
        self.assertEqual(self.lister(self.apprenant_c), [])  # n'a pas déposé
        self.deposer(self.a_c, self.apprenant_c)
        self.assertEqual([x["id"] for x in self.lister(self.apprenant_c)], [c])
        self.assertEqual([x["id"] for x in self.lister(self.formateur)], [c])
        self.assertEqual(self.lister(self.formateur_2), [])
        self.assertEqual(len(self.lister(self.admin)), 1)
        self.assertEqual(self.lister(self.apprenant_hors_promo), [])

    def test_masquage_par_un_formateur_de_la_promotion(self, email):
        c = self.commenter(self.apprenant, self.a_b).data["id"]
        r = self.commenter(self.apprenant_b, self.a_b, "Merci", parent=c).data["id"]
        url = f"{self.url('commentaire', c)}masquer/"

        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(self.client.post(url).status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.client.post(url).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(user=self.formateur)
        res = self.client.post(url)
        self.assertTrue(res.data["masque"])

        # Masqué (et ses réponses) : invisible des autres apprenants, sauf de leur auteur
        self.assertEqual([x["id"] for x in self.lister(self.apprenant_b)], [r])
        vu = self.lister(self.apprenant)
        self.assertEqual([x["id"] for x in vu], [c])
        self.assertTrue(vu[0]["masque"])
        self.assertFalse(vu[0]["peut_modifier"])
        self.assertEqual(len(self.lister(self.formateur)), 2)
        # Plus modifiable, plus de réponse
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.client.patch(self.url("commentaire", c), {"texte": "x"}, format="json").status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.commenter(self.apprenant_b, self.a_b, "?", parent=c).status_code,
                         status.HTTP_400_BAD_REQUEST)

        self.client.force_authenticate(user=self.formateur)
        self.assertFalse(self.client.post(f"{self.url('commentaire', c)}demasquer/").data["masque"])
        self.assertEqual(len(self.lister(self.apprenant_b)), 2)

    def test_brief_archive_lecture_seule(self, email):
        c = self.commenter(self.apprenant, self.a_b).data["id"]
        Brief.objects.filter(pk=self.brief.pk).update(statut=Brief.Statut.ARCHIVE)
        self.assertEqual(self.commenter(self.apprenant, self.a_b).status_code, status.HTTP_400_BAD_REQUEST)
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.client.patch(self.url("commentaire", c), {"texte": "x"}, format="json").status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.delete(self.url("commentaire", c)).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(len(self.lister(self.apprenant_b)), 1)

    def test_emails(self, email):
        with self.captureOnCommitCallbacks(execute=True):
            c = self.commenter(self.apprenant, self.a_b).data["id"]
        destinataires, auteur, titre, reponse, url = email.call_args.args
        self.assertEqual((destinataires, reponse), ([self.apprenant_b.email], False))
        self.assertTrue(url.endswith(f"/activites/{self.brief.id}"))

        self.deposer(self.a_c, self.apprenant_c)
        with self.captureOnCommitCallbacks(execute=True):
            self.commenter(self.apprenant_c, self.a_b, "Moi aussi", parent=c)
        destinataires, _, _, reponse, _ = email.call_args.args
        self.assertEqual(sorted(destinataires), sorted([self.apprenant.email, self.apprenant_b.email]))
        self.assertTrue(reponse)

        # Réponse du commenté : seul l'auteur d'origine est prévenu
        with self.captureOnCommitCallbacks(execute=True):
            self.commenter(self.apprenant_b, self.a_b, "Merci", parent=c)
        self.assertEqual(email.call_args.args[0], [self.apprenant.email])


# ─── Emails ───────────────────────────────────────────────────────────────────

@patch("activites.notifications.envoyer_email_soumission.delay")
@patch("activites.notifications.envoyer_email_assignation.delay")
class NotificationTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.groupe = Groupe.objects.create(promotion=self.promotion, nom="Groupe A")
        GroupeMembre.objects.create(groupe=self.groupe, apprenant=self.apprenant)
        GroupeMembre.objects.create(groupe=self.groupe, apprenant=self.apprenant_b)

    def assigner(self, brief, **cible):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url("assignation"), {"brief": brief.id, **cible}, format="json")

    def test_assignation_d_un_brief_publie(self, email_assignation, _soumission):
        brief = self.creer_brief()
        self.assigner(brief, groupe=self.groupe.id)
        email_assignation.assert_called_once()
        self.assertEqual(email_assignation.call_args.args[0], sorted([self.apprenant.email, self.apprenant_b.email]))
        self.assertEqual(email_assignation.call_args.args[1], brief.titre)

    def test_brouillon_puis_publication(self, email_assignation, _soumission):
        brief = self.creer_brief(statut=Brief.Statut.BROUILLON)
        self.assigner(brief, apprenant=self.apprenant.id)
        email_assignation.assert_not_called()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch(self.url("brief", brief.id), {"statut": "PUBLIE"}, format="json")
        email_assignation.assert_called_once()
        self.assertEqual(email_assignation.call_args.args[0], [self.apprenant.email])

    def test_republication_d_un_archive_sans_email(self, email_assignation, _soumission):
        brief = self.creer_brief(statut=Brief.Statut.ARCHIVE)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch(self.url("brief", brief.id), {"statut": "PUBLIE"}, format="json")
        email_assignation.assert_not_called()

    def test_assignation_multiple_un_seul_envoi(self, email_assignation, _soumission):
        brief = self.creer_brief()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"{self.url('assignation')}multiple/", {
                "brief": brief.id, "apprenants": [self.apprenant.id, self.apprenant_b.id],
            }, format="json")
        email_assignation.assert_called_once()
        self.assertEqual(len(email_assignation.call_args.args[0]), 2)

    def test_depot_previent_les_formateurs(self, _assignation, email_soumission):
        FormateurPromotion.objects.create(formateur=self.formateur_2, promotion=self.promotion)
        assignation = Assignation.objects.create(brief=self.creer_brief(), apprenant=self.apprenant)
        self.client.force_authenticate(user=self.apprenant)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url("livrable"), {"assignation": assignation.id, "liens": ["https://x.test"]})
        email_soumission.assert_called_once()
        args = email_soumission.call_args.args
        self.assertCountEqual(args[0], [self.formateur.email, self.formateur_2.email])
        self.assertEqual(args[4], 1)      # numéro du dépôt
        self.assertFalse(args[6])         # pas en retard

    def test_notifications_de_l_application(self, _assignation, _soumission):
        brief = self.creer_brief()
        self.assigner(brief, groupe=self.groupe.id)
        notifs = Notification.objects.filter(type=Notification.Type.ASSIGNATION)
        self.assertCountEqual([n.utilisateur for n in notifs], [self.apprenant, self.apprenant_b])
        self.assertEqual((notifs[0].tenant, notifs[0].lien), (self.tenant, f"/activites/{brief.id}"))

        assignation = Assignation.objects.get(brief=brief)
        self.client.force_authenticate(user=self.apprenant)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url("livrable"), {"assignation": assignation.id, "liens": ["https://x.test"]})
        depot = Notification.objects.get(type=Notification.Type.DEPOT)
        self.assertEqual((depot.utilisateur, depot.lien), (self.formateur, f"/briefs/{brief.id}"))
        self.assertIn("pour le groupe Groupe A", depot.message)

    def test_depot_refuse_sans_email(self, _assignation, email_soumission):
        assignation = Assignation.objects.create(brief=self.creer_brief(), apprenant=self.apprenant)
        self.client.force_authenticate(user=self.apprenant)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url("livrable"), {"assignation": assignation.id})
        email_soumission.assert_not_called()


# ─── Protections côté pédagogie ───────────────────────────────────────────────

class ReferentielUtiliseParUnBriefTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.creer_brief().competence_niveaux.add(self.cn)
        self.client.force_authenticate(user=self.admin)
        self.base = f"/api/tenants/{self.tenant.id}"

    def test_module_utilise_non_supprimable(self):
        vide = Module.objects.create(formation=self.formation, nom="Vide", ordre=9)
        Brief.objects.filter(module=self.module).update(module=vide)
        res = self.client.delete(f"{self.base}/modules/{vide.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_suppression_en_cascade_d_un_organisme_avec_des_briefs(self):
        # Le module est protégé seul, pas quand toute la formation disparaît
        self.tenant.delete()
        self.assertFalse(Brief.objects.exists())

    def test_niveau_de_competence_vise_non_retirable(self):
        res = self.client.delete(f"{self.base}/competence-niveaux/{self.cn.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(CompetenceNiveau.objects.filter(pk=self.cn.pk).exists())

    def test_niveau_de_competence_libre_retirable(self):
        res = self.client.delete(f"{self.base}/competence-niveaux/{self.cn_module_2.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)


class FiltreLivrablesParModuleTests(ActivitesBaseTestCase):
    def test_filtre_par_module_et_titre_du_brief(self):
        brief = self.creer_brief()
        autre = self.creer_brief()
        autre.module = self.module_2
        autre.save()
        livrable = self.deposer(Assignation.objects.create(brief=brief, apprenant=self.apprenant))
        self.deposer(Assignation.objects.create(brief=autre, apprenant=self.apprenant))

        res = self.client.get(self.url("livrable"), {"module": self.module.id})
        self.assertEqual(self.ids(res), [livrable.id])
        self.assertEqual(res.data[0]["brief_titre"], brief.titre)


# ─── Tableaux de bord ─────────────────────────────────────────────────────────

class TableauDeBordTests(ActivitesBaseTestCase):
    """
    Brief échu (rendu par « apprenant », pas par « apprenant_b ») et brief
    à venir (pas encore rendu), dans la promotion du formateur.
    """

    def setUp(self):
        super().setUp()
        maintenant = timezone.now()
        self.echu = self.brief_nomme(
            "Brief échu", cree_par=self.formateur,
            date_debut=maintenant - timedelta(days=10), date_limite=maintenant - timedelta(days=2),
        )
        self.a_venir = self.brief_nomme("Brief à venir", cree_par=self.formateur)
        for brief in (self.echu, self.a_venir):
            brief.competence_niveaux.set([self.cn])
        self.rendu = Assignation.objects.create(brief=self.echu, apprenant=self.apprenant)
        self.manquant = Assignation.objects.create(brief=self.echu, apprenant=self.apprenant_b)
        Assignation.objects.create(brief=self.a_venir, apprenant=self.apprenant)
        self.deposer(self.rendu)

    def brief_nomme(self, titre, **extra):
        brief = self.creer_brief(**extra)
        brief.titre = titre
        brief.save(update_fields=["titre"])
        return brief

    def get(self, role, utilisateur):
        self.client.force_authenticate(user=utilisateur)
        return self.client.get(
            reverse(f"tableau-de-bord-{role}", kwargs={"tenant_id": self.tenant.id})
        )

    def evaluer(self, assignation, acquis=True):
        evaluation = Evaluation.objects.create(assignation=assignation, evaluateur=self.formateur)
        evaluation.competences.create(competence_niveau=self.cn, acquis=acquis)
        if acquis:
            CompetenceValidee.objects.create(
                apprenant=assignation.apprenant, competence_niveau=self.cn, evaluation=evaluation
            )

    # Formateur

    def test_formateur_rendus_a_evaluer_et_retards(self):
        res = self.get("formateur", self.formateur)
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["indicateurs"]["a_evaluer"], 1)
        self.assertEqual(res.data["indicateurs"]["apprenants"], 2)
        self.assertEqual(res.data["indicateurs"]["taux_rendu"], 50)
        self.assertEqual(res.data["a_evaluer"][0]["assignation"], self.rendu.id)
        # Un seul retard, inscription récente : pas encore signalé (voir DecrochageTests)
        self.assertEqual(res.data["apprenants_a_suivre"], [])
        echeances = {e["titre"]: (e["rendus"], e["attendus"]) for e in res.data["echeances"]}
        self.assertEqual(echeances, {"Brief échu": (1, 2), "Brief à venir": (0, 1)})

    def test_formateur_evaluation_puis_nouveau_depot(self):
        self.evaluer(self.rendu)
        res = self.get("formateur", self.formateur)
        self.assertEqual(res.data["indicateurs"]["a_evaluer"], 0)
        suivi = {s["titre"]: s for s in res.data["suivi_briefs"]}
        self.assertEqual(suivi["Brief échu"]["VALIDE"], 1)
        self.assertEqual(suivi["Brief échu"]["NON_RENDU"], 1)
        # Référentiel de 2 compétences : 50 % pour « apprenant », 0 % pour « apprenant_b »
        self.assertEqual(res.data["indicateurs"]["competences_pct"], 25)

        # Nouveau dépôt après l'évaluation : à réévaluer
        self.deposer(self.rendu)
        res = self.get("formateur", self.formateur)
        self.assertEqual(res.data["indicateurs"]["a_evaluer"], 1)

    def test_formateur_ne_voit_que_ses_promotions(self):
        res = self.get("formateur", self.formateur_2)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["indicateurs"]["a_evaluer"], 0)
        self.assertEqual(res.data["promotions"], [])
        self.assertEqual(res.data["suivi_briefs"], [])

    def test_brouillon_ignore(self):
        brouillon = self.brief_nomme("Brouillon", statut=Brief.Statut.BROUILLON)
        Assignation.objects.create(brief=brouillon, apprenant=self.apprenant)
        res = self.get("formateur", self.formateur)
        self.assertNotIn("Brouillon", [s["titre"] for s in res.data["suivi_briefs"]])

    # Admin organisme

    def test_admin_indicateurs_et_points_d_attention(self):
        invite = self.membre("invite@test.com", MembreTenant.Role.APPRENANT)
        invite.actif = False
        invite.save(update_fields=["actif"])

        res = self.get("admin", self.admin)
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["indicateurs"]["promotions_actives"], 2)
        self.assertEqual(res.data["indicateurs"]["apprenants"], 4)
        self.assertEqual(res.data["indicateurs"]["depots_7_jours"], 1)
        self.assertEqual(res.data["attention"]["invitations_en_attente"], 1)
        self.assertEqual(
            res.data["attention"]["promotions_sans_formateur"],
            [{"id": self.promotion_2.id, "nom": self.promotion_2.nom}],
        )
        promotion = next(p for p in res.data["promotions"] if p["id"] == self.promotion.id)
        self.assertEqual((promotion["nb_apprenants"], promotion["taux_rendu"], promotion["a_evaluer"]), (2, 50, 1))
        self.assertEqual(len(res.data["activite"]), 14)
        self.assertEqual(res.data["activite"][-1]["depots"], 1)

    # Apprenant

    def test_apprenant_a_rendre_et_attente(self):
        res = self.get("apprenant", self.apprenant)
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data["promotion"]["id"], self.promotion.id)
        self.assertEqual([b["titre"] for b in res.data["a_rendre"]], ["Brief à venir"])
        self.assertEqual(res.data["indicateurs"]["a_rendre"], 1)
        self.assertEqual(res.data["indicateurs"]["en_retard"], 0)
        self.assertEqual(res.data["indicateurs"]["en_attente_evaluation"], 1)

    def test_apprenant_en_retard_et_evaluation_recente(self):
        res = self.get("apprenant", self.apprenant_b)
        self.assertEqual(res.data["indicateurs"]["en_retard"], 1)
        self.assertTrue(res.data["a_rendre"][0]["en_retard"])

        self.evaluer(self.rendu)
        res = self.get("apprenant", self.apprenant)
        self.assertEqual(res.data["indicateurs"]["en_attente_evaluation"], 0)
        self.assertEqual(res.data["progression"]["competences_validees"], 1)
        self.assertTrue(res.data["evaluations_recentes"][0]["valide"])

    def test_apprenant_sans_promotion(self):
        res = self.get("apprenant", self.apprenant_hors_promo)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data["promotion"])

    def test_chaque_route_reservee_a_son_role(self):
        refus = [
            ("formateur", self.apprenant), ("formateur", self.admin),
            ("admin", self.formateur), ("admin", self.apprenant),
            ("apprenant", self.formateur), ("apprenant", self.admin),
            ("admin", self.admin_saas),
        ]
        for role, utilisateur in refus:
            self.assertEqual(self.get(role, utilisateur).status_code, status.HTTP_403_FORBIDDEN, (role, utilisateur))

    def test_autre_organisme_refuse(self):
        admin_b = self.membre("admin_b@test.com", MembreTenant.Role.ADMINISTRATEUR, tenant=self.tenant_2)
        self.assertEqual(self.get("admin", admin_b).status_code, status.HTTP_403_FORBIDDEN)


# ─── Décrochage ───────────────────────────────────────────────────────────────

class DecrochageBaseTestCase(ActivitesBaseTestCase):
    """
    Deux briefs échus, ouverts il y a 30 jours : « apprenant » a tout rendu,
    « apprenant_b » rien. Inscriptions anciennes, connexions récentes.
    """

    def setUp(self):
        super().setUp()
        maintenant = timezone.now()
        InscriptionPromotion.objects.filter(promotion=self.promotion).update(
            date_inscription=maintenant - timedelta(days=30)
        )
        User.objects.filter(pk__in=[self.apprenant.pk, self.apprenant_b.pk]).update(last_login=maintenant)
        self.briefs = []
        for jours in (20, 18):
            brief = self.creer_brief(
                cree_par=self.formateur,
                date_debut=maintenant - timedelta(days=30),
                date_limite=maintenant - timedelta(days=jours),
            )
            brief.competence_niveaux.set([self.cn])
            self.briefs.append(brief)
            self.deposer(Assignation.objects.create(brief=brief, apprenant=self.apprenant))
            Assignation.objects.create(brief=brief, apprenant=self.apprenant_b)


class DecrochageTests(DecrochageBaseTestCase):
    def detecter(self):
        from .decrochage import detecter
        return {s["apprenant"].id: s for s in detecter([Promotion.objects.get(pk=self.promotion.pk)])}

    def codes(self, signale):
        return [s["code"] for s in signale["signaux"]]

    def test_retards_et_inactivite_a_risque(self):
        signales = self.detecter()
        self.assertEqual(list(signales), [self.apprenant_b.id])
        b = signales[self.apprenant_b.id]
        self.assertEqual(b["niveau"], "A_RISQUE")
        self.assertEqual(self.codes(b), ["RETARDS", "INACTIVITE"])
        self.assertEqual(b["signaux"][0]["court"], "2 retards")
        self.assertEqual(b["signaux"][1]["valeur"], 30)

    def test_un_seul_signal_a_surveiller(self):
        User.objects.filter(pk=self.apprenant.pk).update(last_login=timezone.now() - timedelta(days=12))
        a = self.detecter()[self.apprenant.id]
        self.assertEqual((a["niveau"], self.codes(a)), ("A_SURVEILLER", ["ABSENCE"]))
        self.assertIn("12 jours", a["signaux"][0]["libelle"])

    def test_jamais_connecte(self):
        User.objects.filter(pk=self.apprenant.pk).update(last_login=None)
        signal = self.detecter()[self.apprenant.id]["signaux"][0]
        self.assertIn("Jamais connecté", signal["libelle"])

    def test_inscription_recente_jamais_signalee(self):
        InscriptionPromotion.objects.filter(apprenant=self.apprenant_b).update(date_inscription=timezone.now())
        self.assertEqual(self.detecter(), {})

    def test_promotion_cloturee_ignoree(self):
        Promotion.objects.filter(pk=self.promotion.pk).update(actif=False)
        self.assertEqual(self.detecter(), {})

    def test_progression_faible_par_rapport_a_la_promotion(self):
        # « apprenant » a tout validé (100 %), « apprenant_b » rien : moyenne 50 %
        for cn in (self.cn, self.cn_module_2):
            CompetenceValidee.objects.create(apprenant=self.apprenant, competence_niveau=cn)
        self.assertIn("PROGRESSION", self.codes(self.detecter()[self.apprenant_b.id]))

    def test_alertes_aux_formateurs_une_fois_par_semaine(self):
        from .decrochage import alerter_formateurs

        self.assertEqual(alerter_formateurs(), 1)
        alerte = Notification.objects.get(type=Notification.Type.ALERTE)
        self.assertEqual(alerte.utilisateur, self.formateur)
        self.assertEqual(alerte.titre, "Prenom Nom : à risque de décrochage")
        self.assertEqual((alerte.lien, alerte.tenant), (f"/progression/{self.apprenant_b.id}", self.tenant))
        self.assertIn("2 briefs échus non rendus", alerte.message)
        self.assertEqual(alerter_formateurs(), 0)

    def test_passage_a_risque_realerte_mais_pas_l_inverse(self):
        from .decrochage import alerter_formateurs

        rendu_b = Assignation.objects.get(brief=self.briefs[0], apprenant=self.apprenant_b)
        # Un seul retard, dépôt récent : à surveiller
        self.deposer(rendu_b, self.apprenant_b)
        User.objects.filter(pk=self.apprenant_b.pk).update(last_login=timezone.now() - timedelta(days=12))
        self.assertEqual(alerter_formateurs(), 1)
        self.assertIn("à surveiller", Notification.objects.get().titre)

        # Plus de dépôt : à risque, nouvelle alerte
        Livrable.objects.filter(deposant=self.apprenant_b).delete()
        self.assertEqual(alerter_formateurs(), 1)

        # De nouveau à surveiller la même semaine : pas de nouvelle alerte
        self.deposer(rendu_b, self.apprenant_b)
        self.assertEqual(alerter_formateurs(), 0)

    def test_tableaux_de_bord(self):
        self.client.force_authenticate(user=self.formateur)
        res = self.client.get(reverse("tableau-de-bord-formateur", kwargs={"tenant_id": self.tenant.id}))
        a_suivre = res.data["apprenants_a_suivre"]
        self.assertEqual([(a["id"], a["niveau"], a["briefs_non_rendus"]) for a in a_suivre],
                         [(self.apprenant_b.id, "A_RISQUE", 2)])
        self.assertEqual(len(a_suivre[0]["signaux"]), 2)

        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse("tableau-de-bord-admin", kwargs={"tenant_id": self.tenant.id}))
        promo = next(p for p in res.data["promotions"] if p["id"] == self.promotion.id)
        self.assertEqual(promo["a_risque"], 1)
        self.assertEqual(res.data["attention"]["apprenants_a_risque"], 1)

    def test_situation_envoyee_a_l_ia(self):
        from .decrochage import situation

        Evaluation.objects.create(
            assignation=Assignation.objects.get(brief=self.briefs[0], apprenant=self.apprenant),
            evaluateur=self.formateur, commentaire="Commentaire pour un autre apprenant",
        )
        self.briefs[0].description = "<p>Créer une <b>API</b> &amp; la documenter</p>"
        self.briefs[0].save()
        demande, signale = situation(self.apprenant_b, self.promotion)
        self.assertEqual(signale["niveau"], "A_RISQUE")
        self.assertEqual(demande["signaux"], ["2 briefs échus non rendus", "Aucun dépôt depuis 30 jours"])
        self.assertEqual([b["statut"] for b in demande["briefs"]], ["en_retard", "en_retard"])
        self.assertEqual(demande["briefs"][0]["description"], "Créer une API & la documenter")
        self.assertEqual(demande["competences_non_acquises"], [{
            "competence": "Développer une API", "niveau": "Imiter",
            "description": "Reproduire une API existante", "brief": "Créer une API REST",
        }])
        self.assertEqual(demande["commentaires_formateur"], [])
        # Anonymisée : ni nom ni email
        self.assertNotIn(self.apprenant_b.email, str(demande))

    def test_competence_d_un_brief_non_echu_pas_envoyee(self):
        from .decrochage import situation

        a_venir = self.creer_brief(cree_par=self.formateur)
        a_venir.competence_niveaux.set([self.cn_module_2])
        Assignation.objects.create(brief=a_venir, apprenant=self.apprenant_b)
        demande, _ = situation(self.apprenant_b, self.promotion)
        self.assertNotIn("Modéliser", str(demande["competences_non_acquises"]))
        self.assertIn("en_cours", [b["statut"] for b in demande["briefs"]])


class DerniereConnexionTests(ActivitesBaseTestCase):
    def test_rafraichissement_met_a_jour_la_derniere_connexion(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        ancienne = timezone.now() - timedelta(days=5)
        User.objects.filter(pk=self.apprenant.pk).update(last_login=ancienne)
        self.client.force_authenticate(user=None)
        res = self.client.post("/api/accounts/refresh/", {"refresh": str(RefreshToken.for_user(self.apprenant))})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.apprenant.refresh_from_db()
        self.assertGreater(self.apprenant.last_login, ancienne)

    def test_jeton_invalide_sans_effet(self):
        res = self.client.post("/api/accounts/refresh/", {"refresh": "invalide"})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


REPONSE_IA = {
    "diagnostic": "Deux briefs en retard.",
    "suggestions_formateur": [{"action": "Entretien", "detail": "Faire le point."}],
    "plan_apprenant": {"message": "Vous pouvez y arriver.", "etapes": [{"titre": "Commencer", "detail": "Le HTML."}]},
}


@patch("activites.views.generer_analyse_accompagnement.delay")
class AnalyseAccompagnementTests(DecrochageBaseTestCase):
    def setUp(self):
        super().setUp()
        self.base = f"/api/tenants/{self.tenant.id}/analyses-accompagnement/"

    def demander(self, utilisateur=None, apprenant=None):
        self.client.force_authenticate(user=utilisateur or self.formateur)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.base, {
                "apprenant": (apprenant or self.apprenant_b).id,
                "promotion": self.promotion.id,
            }, format="json")

    def prete(self, analyse_id):
        AnalyseAccompagnement.objects.filter(pk=analyse_id).update(statut="PRETE", contenu=REPONSE_IA)

    def test_formateur_demande_une_analyse(self, generer):
        res = self.demander()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual((res.data["statut"], res.data["apprenant_nom"]), ("EN_COURS", "Prenom Nom"))
        generer.assert_called_once_with(res.data["id"])

    def test_admin_peut_demander(self, generer):
        self.assertEqual(self.demander(self.admin).status_code, status.HTTP_201_CREATED)

    def test_formateur_hors_promotion_et_apprenant_refuses(self, generer):
        for u in (self.formateur_2, self.apprenant):
            self.assertEqual(self.demander(u).status_code, status.HTTP_403_FORBIDDEN, u.email)
        generer.assert_not_called()

    def test_apprenant_hors_promotion(self, generer):
        res = self.demander(apprenant=self.apprenant_hors_promo)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_promotion_cloturee(self, generer):
        Promotion.objects.filter(pk=self.promotion.pk).update(actif=False)
        self.assertEqual(self.demander().status_code, status.HTTP_400_BAD_REQUEST)

    def test_une_seule_analyse_en_cours(self, generer):
        self.demander()
        res = self.demander()
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("déjà en cours", str(res.data))

    def test_partage_du_plan_a_l_apprenant(self, generer):
        analyse_id = self.demander().data["id"]

        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.client.get(self.base).data, [])

        self.client.force_authenticate(user=self.formateur)
        res = self.client.post(f"{self.base}{analyse_id}/partager/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)  # pas encore prête

        self.prete(analyse_id)
        res = self.client.post(f"{self.base}{analyse_id}/partager/")
        self.assertTrue(res.data["partagee"])
        notif = Notification.objects.get(utilisateur=self.apprenant_b)
        self.assertEqual((notif.type, notif.lien), (Notification.Type.IA, "/ma-progression"))

        self.client.force_authenticate(user=self.apprenant_b)
        plans = self.client.get(self.base).data
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["plan"], REPONSE_IA["plan_apprenant"])
        for cache in ("contenu", "signaux", "niveau", "diagnostic"):
            self.assertNotIn(cache, plans[0])
        # L'apprenant ne voit pas les plans des autres
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.client.get(self.base).data, [])

    def test_apprenant_ne_partage_pas(self, generer):
        analyse_id = self.demander().data["id"]
        self.prete(analyse_id)
        self.client.force_authenticate(user=self.apprenant_b)
        res = self.client.post(f"{self.base}{analyse_id}/partager/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(AnalyseAccompagnement.objects.get(pk=analyse_id).partagee)

    def test_formateur_voit_les_analyses_de_ses_promotions(self, generer):
        self.demander()
        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(self.client.get(self.base).data, [])
        self.client.force_authenticate(user=self.formateur)
        res = self.client.get(self.base, {"apprenant": self.apprenant_b.id})
        self.assertEqual(len(res.data), 1)


@patch("activites.tasks.requests.post")
class GenerationAnalyseTests(DecrochageBaseTestCase):
    def setUp(self):
        super().setUp()
        self.analyse = AnalyseAccompagnement.objects.create(
            apprenant=self.apprenant_b, promotion=self.promotion, demandee_par=self.formateur
        )

    def test_analyse_prete_et_formateur_prevenu(self, post):
        from .tasks import generer_analyse_accompagnement

        post.return_value.status_code = 200
        post.return_value.json.return_value = REPONSE_IA
        generer_analyse_accompagnement(self.analyse.pk)

        self.analyse.refresh_from_db()
        self.assertEqual((self.analyse.statut, self.analyse.contenu), ("PRETE", REPONSE_IA))
        self.assertEqual(self.analyse.niveau, "A_RISQUE")
        self.assertEqual(len(post.call_args.kwargs["json"]["signaux"]), 2)
        notif = Notification.objects.get(utilisateur=self.formateur)
        self.assertEqual((notif.type, notif.lien), (Notification.Type.IA, f"/progression/{self.apprenant_b.id}"))

    def test_echec_du_service(self, post):
        from .tasks import generer_analyse_accompagnement

        post.return_value.status_code = 503
        post.return_value.text = "LLM indisponible"
        generer_analyse_accompagnement(self.analyse.pk)
        self.analyse.refresh_from_db()
        self.assertEqual(self.analyse.statut, "ECHEC")
        self.assertIn("échoué", self.analyse.erreur)


class ProgressionDecrochageTests(DecrochageBaseTestCase):
    def test_signalement_visible_du_formateur_seulement(self):
        res = self.client.get(self.url("progression", self.apprenant_b.id))
        self.assertEqual(res.data["decrochage"]["niveau"], "A_RISQUE")
        self.assertTrue(res.data["promotion"]["actif"])
        self.assertIsNone(self.client.get(self.url("progression", self.apprenant.id)).data["decrochage"])

        self.client.force_authenticate(user=self.apprenant_b)
        res = self.client.get(f"/api/tenants/{self.tenant.id}/progression/moi/")
        self.assertNotIn("decrochage", res.data)


# ─── Rappels d'échéance ───────────────────────────────────────────────────────

@patch("activites.tasks.envoyer_email_rappel.apply_async")
class RappelsTests(ActivitesBaseTestCase):
    def brief_ouvert(self, ecoule_h, restant_h, statut=Brief.Statut.PUBLIE):
        """Brief ouvert depuis « ecoule_h » heures, échéance dans « restant_h » heures."""
        maintenant = timezone.now()
        return self.creer_brief(
            statut=statut,
            date_debut=maintenant - timedelta(hours=ecoule_h),
            date_limite=maintenant + timedelta(hours=restant_h),
        )

    def rappels(self):
        from .rappels import envoyer_rappels
        return envoyer_rappels()

    def test_rappel_a_70_pourcent_du_delai(self, email):
        avant = self.brief_ouvert(60, 40)  # 60 % écoulé
        apres = self.brief_ouvert(72, 28)  # 72 % écoulé
        for brief in (avant, apres):
            Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        self.assertEqual(self.rappels(), 1)
        n = Notification.objects.get()
        self.assertEqual((n.type, n.lien), (Notification.Type.RAPPEL, f"/activites/{apres.id}"))
        self.assertEqual(n.titre, f"Rappel : {apres.titre}")
        self.assertEqual(email.call_count, 1)
        # Une seule fois
        self.assertEqual(self.rappels(), 0)

    def test_brief_court(self, email):
        # Brief de 8 h : rappel dès 5 h 36 écoulées
        Assignation.objects.create(brief=self.brief_ouvert(6, 2), apprenant=self.apprenant)
        self.assertEqual(self.rappels(), 1)

    def test_rappel_de_retard(self, email):
        echu = self.brief_ouvert(50, -2)
        ancien = self.brief_ouvert(100, -30)  # hors de la fenêtre de 24 h
        for brief in (echu, ancien):
            Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        self.assertEqual(self.rappels(), 1)
        self.assertEqual(Notification.objects.get().titre, f"Échéance dépassée : {echu.titre}")
        self.assertEqual(email.call_args.args[0][2], "RETARD")

    def test_pas_de_rappel_si_rendu_brouillon_ou_promotion_cloturee(self, email):
        rendu = Assignation.objects.create(brief=self.brief_ouvert(80, 20), apprenant=self.apprenant)
        self.deposer(rendu)
        Assignation.objects.create(
            brief=self.brief_ouvert(80, 20, statut=Brief.Statut.BROUILLON), apprenant=self.apprenant
        )
        cloturee = self.brief_ouvert(80, 20)
        cloturee.promotion = self.promotion_2
        cloturee.save()
        Promotion.objects.filter(pk=self.promotion_2.pk).update(actif=False)
        InscriptionPromotion.objects.create(promotion=self.promotion_2, apprenant=self.apprenant_hors_promo)
        Assignation.objects.create(brief=cloturee, apprenant=self.apprenant_hors_promo)
        self.assertEqual(self.rappels(), 0)

    def test_groupe_membres_actifs_seulement(self, email):
        groupe = Groupe.objects.create(promotion=self.promotion, nom="Groupe A")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant)
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_b, actif=False)
        Assignation.objects.create(brief=self.brief_ouvert(80, 20), groupe=groupe)
        self.assertEqual(self.rappels(), 1)
        self.assertEqual(Notification.objects.get().utilisateur, self.apprenant)

    def test_apprenant_desinscrit_pas_relance(self, email):
        Assignation.objects.create(brief=self.brief_ouvert(80, 20), apprenant=self.apprenant)
        InscriptionPromotion.objects.filter(apprenant=self.apprenant).update(actif=False)
        self.assertEqual(self.rappels(), 0)


class MomentEmailTests(ActivitesBaseTestCase):
    def a(self, jour, heure):
        return timezone.make_aware(datetime(2026, 10, jour, heure, 0))

    def test_journee_tout_de_suite(self):
        from .rappels import RAPPEL, moment_email
        self.assertIsNone(moment_email(self.a(15, 10), self.a(15, 18), RAPPEL))

    def test_nuit_decale_au_matin(self):
        from .rappels import RAPPEL, RETARD, moment_email
        self.assertEqual(moment_email(self.a(15, 3), self.a(15, 18), RAPPEL), self.a(15, 7))
        self.assertEqual(moment_email(self.a(15, 23), self.a(17, 18), RAPPEL), self.a(16, 7))
        self.assertEqual(moment_email(self.a(15, 3), self.a(15, 1), RETARD), self.a(15, 7))

    def test_rappel_urgent_part_la_nuit(self):
        from .rappels import RAPPEL, moment_email
        # Échéance à 8 h : avant 7 h + 2 h de marge
        self.assertIsNone(moment_email(self.a(15, 3), self.a(15, 8), RAPPEL))


class EmailRappelTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        maintenant = timezone.now()
        self.brief = self.creer_brief(
            date_debut=maintenant - timedelta(hours=80), date_limite=maintenant + timedelta(hours=20)
        )
        self.assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)

    def envoyer(self, type_rappel="RAPPEL"):
        from .tasks import envoyer_email_rappel
        envoyer_email_rappel(self.assignation.id, self.apprenant.id, type_rappel)

    def test_email_envoye(self):
        self.envoyer()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.apprenant.email])
        self.assertEqual(mail.outbox[0].subject, f"Rappel : {self.brief.titre}")

    def test_depot_entre_temps_pas_d_email(self):
        self.deposer(self.assignation)
        self.envoyer()
        self.assertEqual(mail.outbox, [])

    def test_echeance_passee_entre_temps_pas_de_rappel(self):
        Brief.objects.filter(pk=self.brief.pk).update(date_limite=timezone.now() - timedelta(hours=1))
        self.envoyer()
        self.assertEqual(mail.outbox, [])
        self.envoyer("RETARD")
        self.assertEqual(len(mail.outbox), 1)

    def test_brief_repasse_en_brouillon_pas_d_email(self):
        Brief.objects.filter(pk=self.brief.pk).update(statut=Brief.Statut.BROUILLON)
        self.envoyer()
        self.assertEqual(mail.outbox, [])


# ─── Activité du formateur ────────────────────────────────────────────────────

class DatePublicationBriefTests(ActivitesBaseTestCase):
    def test_remplie_a_la_publication_puis_conservee(self):
        brief = self.creer_brief(statut=Brief.Statut.BROUILLON)
        self.assertIsNone(brief.date_publication)
        brief.statut = Brief.Statut.PUBLIE
        brief.save(update_fields=["statut"])
        brief.refresh_from_db()
        premiere = brief.date_publication
        self.assertIsNotNone(premiere)
        brief.statut = Brief.Statut.ARCHIVE
        brief.save()
        brief.statut = Brief.Statut.PUBLIE
        brief.save()
        brief.refresh_from_db()
        self.assertEqual(brief.date_publication, premiere)

    def test_publication_par_l_api(self):
        brief = self.creer_brief(statut=Brief.Statut.BROUILLON, cree_par=self.formateur)
        self.client.patch(self.url("brief", brief.id), {"statut": "PUBLIE"}, format="json")
        brief.refresh_from_db()
        self.assertIsNotNone(brief.date_publication)


class ActiviteFormateurTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.maintenant = timezone.now()
        self.brief = self.creer_brief(cree_par=self.formateur)
        self.assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)

    def il_y_a(self, **delta):
        return self.maintenant - timedelta(**delta)

    def evaluation(self, depot_il_y_a_h, evaluee_il_y_a_h, commentaire="", assignation=None):
        if assignation is None:
            self.nb_eleves = getattr(self, "nb_eleves", 0) + 1
            eleve = self.membre(f"eleve{self.nb_eleves}@test.com", MembreTenant.Role.APPRENANT)
            assignation = Assignation.objects.create(brief=self.brief, apprenant=eleve)
        depot = self.deposer(assignation)
        Livrable.objects.filter(pk=depot.pk).update(date_depot=self.il_y_a(hours=depot_il_y_a_h))
        e = Evaluation.objects.create(assignation=assignation, evaluateur=self.formateur, commentaire=commentaire)
        Evaluation.objects.filter(pk=e.pk).update(date_creation=self.il_y_a(hours=evaluee_il_y_a_h))
        return e

    def activite(self, utilisateur=None):
        self.client.force_authenticate(user=utilisateur or self.formateur)
        return self.client.get(reverse("activite-formateur", kwargs={"tenant_id": self.tenant.id}))

    def test_actions_comptees_et_fil(self):
        Ressource.objects.create(tenant=self.tenant, formateur=self.formateur, titre="Guide CSS", url="https://x.test")
        self.evaluation(30, 6, commentaire="Bien")
        res = self.activite()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["periode"]["actions"], {"BRIEF": 1, "EVALUATION": 1, "RESSOURCE": 1, "REVISION": 0})
        self.assertEqual(res.data["total_annee"], 3)
        self.assertEqual(len(res.data["grille"]), 365)
        self.assertEqual(sum(j["actions"] for j in res.data["grille"]), 3)
        self.assertIn("Rendu évalué : Prenom Nom, Créer une API REST", [a["libelle"] for a in res.data["fil"]])

    def test_delai_de_correction_et_commentaires(self):
        self.evaluation(30, 6, commentaire="Bien")         # 24 h
        self.evaluation(60, 48, commentaire="   ")          # 12 h, sans commentaire
        periode = self.activite().data["periode"]
        self.assertEqual(periode["delai_moyen_correction_h"], 18.0)
        self.assertEqual(periode["feedback_pct"], 50)

    def test_periode_precedente(self):
        self.evaluation(24 * 40 + 5, 24 * 40)
        res = self.activite().data
        self.assertEqual(res["periode"]["evaluations"], 0)
        self.assertEqual(res["periode_precedente"]["evaluations"], 1)
        self.assertIsNone(res["periode"]["delai_moyen_correction_h"])

    def test_brouillon_et_autre_formateur_non_comptes(self):
        self.creer_brief(statut=Brief.Statut.BROUILLON, cree_par=self.formateur)
        FormateurPromotion.objects.create(formateur=self.formateur_2, promotion=self.promotion)
        self.creer_brief(cree_par=self.formateur_2)
        self.assertEqual(self.activite().data["periode"]["actions"]["BRIEF"], 1)

    def test_rendus_en_attente_depuis_plus_de_3_jours(self):
        ancien = self.deposer(self.assignation)
        Livrable.objects.filter(pk=ancien.pk).update(date_depot=self.il_y_a(days=4))
        recent = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant_b)
        self.deposer(recent, self.apprenant_b)
        self.assertEqual(self.activite().data["rendus_en_attente"], 1)

    def test_semaines_actives(self):
        Brief.objects.filter(pk=self.brief.pk).update(date_publication=self.il_y_a(weeks=3))
        self.evaluation(30, 6)
        self.assertEqual(self.activite().data["semaines_actives"], 2)

    def test_reserve_au_formateur(self):
        for u in (self.admin, self.apprenant):
            self.assertEqual(self.activite(u).status_code, status.HTTP_403_FORBIDDEN, u.email)


# ─── Évolution et vue d'ensemble ──────────────────────────────────────────────

class EvolutionTests(ActivitesBaseTestCase):
    """Promotion commencée il y a 3 semaines ; référentiel de 2 compétences."""

    def setUp(self):
        super().setUp()
        aujourd_hui = timezone.localdate()
        Promotion.objects.filter(pk=self.promotion.pk).update(date_debut=aujourd_hui - timedelta(weeks=3))
        self.promo = Promotion.objects.get(pk=self.promotion.pk)

    def valider(self, apprenant, cn, il_y_a_jours):
        v = CompetenceValidee.objects.create(apprenant=apprenant, competence_niveau=cn)
        CompetenceValidee.objects.filter(pk=v.pk).update(date_validation=timezone.now() - timedelta(days=il_y_a_jours))

    def test_courbe_d_un_apprenant(self):
        from .progression import evolution

        self.valider(self.apprenant, self.cn, 10)
        self.valider(self.apprenant, self.cn_module_2, 0)
        points = evolution(self.apprenant, self.promo)
        self.assertIn(len(points), (4, 5))  # semaines entamées depuis le début
        self.assertEqual(points[0]["semaine"].weekday(), 0)  # chaque point est un lundi
        self.assertEqual(points[0]["pct"], 0)
        self.assertEqual((points[-1]["validees"], points[-1]["pct"]), (2, 100))
        self.assertEqual([p["validees"] for p in points], sorted(p["validees"] for p in points))

    def test_promotion_pas_commencee(self):
        from .progression import evolution

        Promotion.objects.filter(pk=self.promotion.pk).update(date_debut=timezone.localdate() + timedelta(days=10))
        self.assertEqual(evolution(self.apprenant, Promotion.objects.get(pk=self.promotion.pk)), [])

    def test_moyenne_de_la_promotion(self):
        from .progression import evolution_promotion

        self.valider(self.apprenant, self.cn, 0)  # 50 % pour l'un, 0 % pour l'autre
        points = evolution_promotion(self.promo, [self.apprenant.id, self.apprenant_b.id])
        self.assertEqual(points[-1]["pct"], 25)

    def test_dans_les_api(self):
        self.valider(self.apprenant, self.cn, 0)
        res = self.client.get(self.url("progression", self.apprenant.id))
        self.assertEqual(res.data["evolution"][-1]["pct"], 50)
        self.assertEqual(res.data["evolution_promotion"][-1]["pct"], 25)

        res = self.client.get(reverse("tableau-de-bord-formateur", kwargs={"tenant_id": self.tenant.id}))
        promo = next(p for p in res.data["promotions"] if p["id"] == self.promotion.id)
        self.assertEqual(promo["evolution"][-1]["pct"], 25)

        self.client.force_authenticate(user=self.apprenant)
        res = self.client.get(f"/api/tenants/{self.tenant.id}/progression/moi/")
        self.assertEqual(res.data["evolution"][-1]["pct"], 50)
        self.assertNotIn("evolution_promotion", res.data)


class VueEnsembleAdminTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        maintenant = timezone.now()
        echu = self.creer_brief(date_debut=maintenant - timedelta(days=10), date_limite=maintenant - timedelta(days=2))
        echu.competence_niveaux.set([self.cn, self.cn_module_2])
        a_venir = self.creer_brief()
        a_venir.competence_niveaux.set([self.cn_autre_formation])
        for apprenant in (self.apprenant, self.apprenant_b):
            Assignation.objects.create(brief=echu, apprenant=apprenant)
            Assignation.objects.create(brief=a_venir, apprenant=apprenant)
        # « Développer une API » acquise par les deux, « Modéliser » par un seul
        for apprenant in (self.apprenant, self.apprenant_b):
            CompetenceValidee.objects.create(apprenant=apprenant, competence_niveau=self.cn)
        CompetenceValidee.objects.create(apprenant=self.apprenant, competence_niveau=self.cn_module_2)
        self.client.force_authenticate(user=self.admin)
        self.donnees = self.client.get(reverse("tableau-de-bord-admin", kwargs={"tenant_id": self.tenant.id})).data

    def test_competences_les_moins_acquises(self):
        faibles = self.donnees["competences_moins_acquises"]
        self.assertEqual([(c["competence"], c["pct"], c["apprenants"]) for c in faibles],
                         [("Modéliser", 50, 2), ("Développer une API", 100, 2)])
        self.assertEqual(faibles[0]["module"], "Base de données")
        # Compétence d'un brief pas encore échu : absente

    def test_bilan_par_formation(self):
        web = next(f for f in self.donnees["formations"] if f["nom"] == "Formation Web")
        self.assertEqual((web["promotions"], web["apprenants"], web["competences_pct"]), (2, 2, 75))
        self.assertEqual(web["taux_rendu"], 0)

    def test_courbe_par_promotion(self):
        promo = next(p for p in self.donnees["promotions"] if p["id"] == self.promotion.id)
        self.assertIn("evolution", promo)
