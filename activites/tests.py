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
- Multi-tenant : isolation
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import MembreTenant
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

from .models import Assignation, Brief, FichierLivrable, Livrable, Ressource

User = get_user_model()

MEDIA_TEST = tempfile.mkdtemp()


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
        return Brief.objects.create(
            promotion=promotion or self.promotion,
            module=self.module,
            titre="Créer une API REST",
            description="Description",
            consignes="Consignes",
            date_debut="2026-09-01T08:00:00Z",
            date_limite="2026-09-30T18:00:00Z",
            statut=statut,
            **extra,
        )

    def deposer(self, assignation, apprenant=None):
        return Livrable.objects.create(
            assignation=assignation, deposant=apprenant or self.apprenant, titre="L",
        )

    def url(self, nom, pk=None):
        kwargs = {"tenant_id": self.tenant.id}
        if pk is not None:
            kwargs["pk"] = pk
            return reverse(f"{nom}-detail", kwargs=kwargs)
        return reverse(f"{nom}-list", kwargs=kwargs)

    def ids(self, res):
        return sorted(x["id"] for x in res.data)

    def payload_brief(self, **extra):
        return {
            "promotion": self.promotion.id,
            "module": self.module.id,
            "titre": "Brief API",
            "description": "Description",
            "consignes": "Consignes",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
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

    def test_ressource_d_un_autre_organisme_refusee(self):
        ressource = Ressource.objects.create(tenant=self.tenant_2, titre="Doc", url="https://example.com")
        res = self.client.post(self.url("brief"), self.payload_brief(ressources=[ressource.id]), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_statut_termine_n_existe_plus(self):
        res = self.client.post(self.url("brief"), self.payload_brief(statut="TERMINE"), format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dates_invalides_refusees(self):
        res = self.client.post(self.url("brief"), self.payload_brief(
            date_debut="2026-09-30T08:00:00Z", date_limite="2026-09-01T18:00:00Z"), format="json")
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
                                {"date_limite": "2026-10-30T18:00:00Z"}, format="json")
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
        fichier = SimpleUploadedFile("doc.pdf", b"PDF", content_type="application/pdf")
        res = self.client.post(self.url("ressource"), {"titre": "Fichier", "fichier": fichier})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_url_et_fichier_simultanes_refuses(self):
        fichier = SimpleUploadedFile("doc.pdf", b"PDF", content_type="application/pdf")
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

    def deposer_api(self, assignation):
        return self.client.post(self.url("livrable"),
                                {"assignation": assignation.id, "titre": "Mon livrable"}, format="json")

    def test_apprenant_depose(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.deposer_api(self.assignation)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["deposant"], self.apprenant.id)

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

    def test_apprenant_ne_voit_pas_les_livrables_des_autres(self):
        # Audit S1
        self.deposer(self.assignation)
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.client.get(self.url("livrable")).data, [])
        self.client.force_authenticate(user=self.apprenant_hors_promo)
        self.assertEqual(self.client.get(self.url("livrable")).data, [])

    def test_apprenant_voit_ses_livrables(self):
        livrable = self.deposer(self.assignation)
        self.client.force_authenticate(user=self.apprenant)
        self.assertEqual(self.ids(self.client.get(self.url("livrable"))), [livrable.id])

    def test_formateur_non_affecte_ne_voit_pas(self):
        self.deposer(self.assignation)
        self.client.force_authenticate(user=self.formateur_2)
        self.assertEqual(self.client.get(self.url("livrable")).data, [])

    def test_suppression_interdite(self):
        livrable = self.deposer(self.assignation)
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.delete(self.url("livrable", livrable.id))
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_admin_saas_exclu(self):
        self.client.force_authenticate(user=self.admin_saas)
        self.assertEqual(self.client.get(self.url("livrable")).status_code, status.HTTP_403_FORBIDDEN)


class FichierLivrableTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        assignation = Assignation.objects.create(brief=self.creer_brief(), apprenant=self.apprenant)
        self.livrable = self.deposer(assignation)
        self.client.force_authenticate(user=self.apprenant)

    def envoyer(self, nom, contenu=b"contenu", content_type="application/octet-stream"):
        fichier = SimpleUploadedFile(nom, contenu, content_type=content_type)
        return self.client.post(self.url("fichier-livrable"),
                                {"livrable": self.livrable.id, "nom": nom, "fichier": fichier})

    def test_types_acceptes(self):
        for nom in ("doc.pdf", "slides.pptx", "rapport.docx", "notes.txt"):
            self.assertEqual(self.envoyer(nom).status_code, status.HTTP_201_CREATED, nom)

    def test_types_refuses(self):
        for nom in ("image.png", "photo.jpg", "video.mp4", "archive.zip"):
            self.assertEqual(self.envoyer(nom).status_code, status.HTTP_400_BAD_REQUEST, nom)

    def test_url_seule_acceptee(self):
        res = self.client.post(self.url("fichier-livrable"),
                               {"livrable": self.livrable.id, "nom": "Dépôt", "url": "https://github.com/x/y"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_url_et_fichier_refuses(self):
        fichier = SimpleUploadedFile("doc.pdf", b"PDF", content_type="application/pdf")
        res = self.client.post(self.url("fichier-livrable"), {
            "livrable": self.livrable.id, "nom": "X", "fichier": fichier, "url": "https://x.test",
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_taille_maximale(self):
        self.assertEqual(self.envoyer("gros.pdf", b"x" * (5 * 1024 * 1024 + 1)).status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_autre_apprenant_ne_voit_pas_les_fichiers(self):
        FichierLivrable.objects.create(livrable=self.livrable, nom="Lien", url="https://x.test")
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.client.get(self.url("fichier-livrable")).data, [])


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

    def test_niveau_de_competence_vise_non_retirable(self):
        res = self.client.delete(f"{self.base}/competence-niveaux/{self.cn.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(CompetenceNiveau.objects.filter(pk=self.cn.pk).exists())

    def test_niveau_de_competence_libre_retirable(self):
        res = self.client.delete(f"{self.base}/competence-niveaux/{self.cn_module_2.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
