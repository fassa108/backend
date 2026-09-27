"""
Tests complets pour le flux activités EduHub.

Couvre :
- Briefs : création, modification après soumission, permissions
- Ressources indépendantes : CRUD, isolation tenant, indépendance du Brief
- RessourceBrief (déprécié) : compatibilité
- Assignations : apprenant, groupe, apprenant+groupe, doublons, Formateur limité à ses promotions,
  Utilisateur.actif non bloquant, Admin SaaS exclu
- Livrables : dépôt, types de fichiers, statut, permissions
- FichierLivrable : PDF/PPTX/DOCX/TXT acceptés, autres refusés
- Multi-tenant : isolation complète
"""

from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import MembreTenant
from pedagogie.models import (
    Competence,
    FormateurPromotion,
    Formation,
    Groupe,
    GroupeMembre,
    InscriptionPromotion,
    Module,
    Promotion,
)
from tenants.models import Tenant

from .models import (
    Assignation,
    Brief,
    FichierLivrable,
    Livrable,
    Ressource,
    RessourceBrief,
)

User = get_user_model()


# ─── Base ─────────────────────────────────────────────────────────────────────

class ActivitesBaseTestCase(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(nom="Organisme Test")
        self.tenant_2 = Tenant.objects.create(nom="Organisme B")

        self.admin_saas = User.objects.create_user(
            email="saas@test.com", password="pw", nom="SaaS", prenom="A",
            actif=True, est_admin_saas=True,
        )

        self.admin = User.objects.create_user(
            email="admin@test.com", password="pw", nom="Admin", prenom="T", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.admin, tenant=self.tenant,
                                    role=MembreTenant.Role.ADMINISTRATEUR, actif=True)

        self.formateur = User.objects.create_user(
            email="formateur@test.com", password="pw", nom="Form", prenom="T", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.formateur, tenant=self.tenant,
                                    role=MembreTenant.Role.FORMATEUR, actif=True)

        self.formateur_2 = User.objects.create_user(
            email="formateur2@test.com", password="pw", nom="Form2", prenom="T", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.formateur_2, tenant=self.tenant,
                                    role=MembreTenant.Role.FORMATEUR, actif=True)

        self.apprenant = User.objects.create_user(
            email="apprenant@test.com", password="pw", nom="App", prenom="T", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.apprenant, tenant=self.tenant,
                                    role=MembreTenant.Role.APPRENANT, actif=True)

        # Apprenant avec compte non activé — MembreTenant.actif=True
        self.apprenant_inactif = User.objects.create_user(
            email="apprenant_inactif@test.com", password="pw", nom="Inactif", prenom="App",
            actif=False,
        )
        MembreTenant.objects.create(utilisateur=self.apprenant_inactif, tenant=self.tenant,
                                    role=MembreTenant.Role.APPRENANT, actif=True)

        self.formation = Formation.objects.create(tenant=self.tenant, nom="Formation Web")
        self.promotion = Promotion.objects.create(
            formation=self.formation, nom="Promo 2026", date_debut="2026-01-01"
        )
        self.promotion_2 = Promotion.objects.create(
            formation=self.formation, nom="Promo 2026 B", date_debut="2026-06-01"
        )

        self.module = Module.objects.create(
            formation=self.formation, nom="Développement Web", ordre=1
        )
        self.competence = Competence.objects.create(
            module=self.module, nom="Développer une API", ordre=1
        )

        InscriptionPromotion.objects.create(
            promotion=self.promotion, apprenant=self.apprenant, actif=True
        )
        InscriptionPromotion.objects.create(
            promotion=self.promotion, apprenant=self.apprenant_inactif, actif=True
        )

        # Formateur affecté à promotion uniquement
        FormateurPromotion.objects.create(formateur=self.formateur, promotion=self.promotion)
        # formateur_2 n'est PAS affecté

        self.client.force_authenticate(user=self.formateur)

    def creer_brief(self, promotion=None):
        p = promotion or self.promotion
        return Brief.objects.create(
            promotion=p,
            titre="Créer une API REST",
            description="Description",
            consignes="Consignes",
            date_debut="2026-09-01T08:00:00Z",
            date_limite="2026-09-30T18:00:00Z",
        )

    def brief_url(self):
        return reverse("brief-list", kwargs={"tenant_id": self.tenant.id})

    def brief_detail_url(self, brief_id):
        return reverse("brief-detail", kwargs={"tenant_id": self.tenant.id, "pk": brief_id})


# ─── Briefs ───────────────────────────────────────────────────────────────────

class BriefTests(ActivitesBaseTestCase):
    def test_formateur_peut_creer_brief_dans_sa_promotion(self):
        res = self.client.post(self.brief_url(), {
            "promotion": self.promotion.id,
            "titre": "Brief API",
            "description": "Desc",
            "consignes": "Cons",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_formateur_ne_peut_pas_creer_brief_promo_non_affectee(self):
        res = self.client.post(self.brief_url(), {
            "promotion": self.promotion_2.id,
            "titre": "Brief Interdit",
            "description": "Desc",
            "consignes": "Cons",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_non_affecte_ne_peut_pas_creer_brief(self):
        self.client.force_authenticate(user=self.formateur_2)
        res = self.client.post(self.brief_url(), {
            "promotion": self.promotion.id,
            "titre": "Brief Formateur 2",
            "description": "Desc",
            "consignes": "Cons",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_organisme_peut_consulter_briefs(self):
        self.creer_brief()
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(self.brief_url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_admin_organisme_ne_peut_pas_creer_brief(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.brief_url(), {
            "promotion": self.promotion.id,
            "titre": "Brief Admin",
            "description": "Desc",
            "consignes": "Cons",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_apprenant_ne_peut_pas_creer_brief(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.post(self.brief_url(), {
            "promotion": self.promotion.id,
            "titre": "Brief App",
            "description": "Desc",
            "consignes": "Cons",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_consulter_briefs(self):
        self.creer_brief()
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.get(self.brief_url())
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_creer_brief(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.post(self.brief_url(), {
            "promotion": self.promotion.id,
            "titre": "Brief SaaS",
            "description": "Desc",
            "consignes": "Cons",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_brief_non_modifiable_apres_soumission(self):
        brief = self.creer_brief()
        assignation = Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        Livrable.objects.create(
            assignation=assignation, deposant=self.apprenant,
            titre="Livrable", description="Desc",
        )
        res = self.client.patch(
            self.brief_detail_url(brief.id), {"titre": "Modifié"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_brief_non_supprimable_apres_soumission(self):
        brief = self.creer_brief()
        assignation = Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        Livrable.objects.create(
            assignation=assignation, deposant=self.apprenant, titre="L", description="D"
        )
        res = self.client.delete(self.brief_detail_url(brief.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_dates_invalides_refusees(self):
        res = self.client.post(self.brief_url(), {
            "promotion": self.promotion.id,
            "titre": "Brief Date",
            "description": "Desc",
            "consignes": "Cons",
            "date_debut": "2026-09-30T08:00:00Z",
            "date_limite": "2026-09-01T08:00:00Z",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_isolation_multi_tenant_brief(self):
        autre_tenant = Tenant.objects.create(nom="Autre Org")
        autre_admin = User.objects.create_user(
            email="autre_admin@test.com", password="pw", nom="A", prenom="B", actif=True
        )
        MembreTenant.objects.create(utilisateur=autre_admin, tenant=autre_tenant,
                                    role=MembreTenant.Role.ADMINISTRATEUR, actif=True)
        self.client.force_authenticate(user=autre_admin)
        res = self.client.get(self.brief_url())
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_ne_voit_pas_briefs_promo_non_affectee(self):
        brief_p2 = self.creer_brief(promotion=self.promotion_2)
        brief_p1 = self.creer_brief(promotion=self.promotion)
        # formateur_2 n'est pas affecté à promotion
        self.client.force_authenticate(user=self.formateur_2)
        res = self.client.get(self.brief_url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [b["id"] for b in res.data]
        self.assertNotIn(brief_p1.id, ids)


# ─── Ressources indépendantes ─────────────────────────────────────────────────

class RessourceTests(ActivitesBaseTestCase):
    def ressource_url(self):
        return reverse("ressource-list", kwargs={"tenant_id": self.tenant.id})

    def test_formateur_peut_creer_ressource_avec_url(self):
        res = self.client.post(self.ressource_url(), {
            "titre": "Documentation",
            "url": "https://example.com/doc",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        r = Ressource.objects.get(id=res.data["id"])
        self.assertEqual(r.tenant, self.tenant)
        self.assertEqual(r.formateur, self.formateur)

    def test_formateur_peut_creer_ressource_avec_fichier(self):
        fichier = SimpleUploadedFile("doc.pdf", b"contenu", content_type="application/pdf")
        res = self.client.post(self.ressource_url(), {
            "titre": "Mon PDF",
            "fichier": fichier,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_url_et_fichier_simultanes_refuse(self):
        fichier = SimpleUploadedFile("doc.pdf", b"contenu", content_type="application/pdf")
        res = self.client.post(self.ressource_url(), {
            "titre": "Invalide",
            "url": "https://example.com",
            "fichier": fichier,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sans_source_refuse(self):
        res = self.client.post(self.ressource_url(), {"titre": "Vide"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ressource_independante_du_brief(self):
        """Une Ressource ne doit pas avoir de lien vers un Brief."""
        res = self.client.post(self.ressource_url(), {
            "titre": "Indépendante",
            "url": "https://example.com",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("brief", res.data)

    def test_apprenant_peut_consulter_ressource(self):
        Ressource.objects.create(
            tenant=self.tenant, formateur=self.formateur,
            titre="Res", url="https://example.com"
        )
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.get(self.ressource_url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_admin_saas_ne_peut_pas_creer_ressource(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.post(self.ressource_url(), {
            "titre": "Ressource SaaS",
            "url": "https://example.com",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_consulter_ressource(self):
        Ressource.objects.create(
            tenant=self.tenant, formateur=self.formateur,
            titre="Res", url="https://example.com"
        )
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.get(self.ressource_url())
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_isolation_tenant_ressource(self):
        """Une ressource d'un autre tenant n'est pas visible."""
        autre_formateur = User.objects.create_user(
            email="f_b@test.com", password="pw", nom="F", prenom="B", actif=True
        )
        MembreTenant.objects.create(utilisateur=autre_formateur, tenant=self.tenant_2,
                                    role=MembreTenant.Role.FORMATEUR, actif=True)
        Ressource.objects.create(
            tenant=self.tenant_2, formateur=autre_formateur,
            titre="Res T2", url="https://example.com"
        )
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(self.ressource_url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 0)

    def test_admin_organisme_peut_gerer_ressource(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.ressource_url(), {
            "titre": "Res Admin",
            "url": "https://example.com",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)


# ─── Assignations ─────────────────────────────────────────────────────────────

class AssignationTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.brief = self.creer_brief()
        self.groupe = Groupe.objects.create(promotion=self.promotion, nom="Groupe A")

    def assignation_url(self):
        return reverse("assignation-list", kwargs={"tenant_id": self.tenant.id})

    def test_assignation_apprenant(self):
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": self.apprenant.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_assignation_groupe(self):
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "groupe": self.groupe.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_assignation_apprenant_inactif_autorisee(self):
        """
        Utilisateur.actif=False ne bloque pas l'assignation si MembreTenant.actif=True
        et inscription active dans la promotion.
        """
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": self.apprenant_inactif.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_assignation_apprenant_et_groupe_simultanément_acceptee(self):
        """
        Une assignation peut cibler un apprenant ET un groupe en même temps.
        Nouvelle règle : OR (pas XOR).
        """
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": self.apprenant.id,
            "groupe": self.groupe.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_assignation_sans_cible_refusee(self):
        """
        Une assignation sans apprenant ni groupe doit être refusée.
        C'est le seul cas invalide avec la règle OR.
        """
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_doublon_apprenant_refuse(self):
        Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": self.apprenant.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_doublon_groupe_refuse(self):
        Assignation.objects.create(brief=self.brief, groupe=self.groupe)
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "groupe": self.groupe.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apprenant_non_inscrit_refuse(self):
        autre_app = User.objects.create_user(
            email="autre_app@test.com", password="pw", nom="A", prenom="B", actif=True
        )
        MembreTenant.objects.create(utilisateur=autre_app, tenant=self.tenant,
                                    role=MembreTenant.Role.APPRENANT, actif=True)
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": autre_app.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_non_affecte_ne_peut_pas_assigner(self):
        """formateur_2 n'est pas affecté à promotion."""
        self.client.force_authenticate(user=self.formateur_2)
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": self.apprenant.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_organisme_ne_peut_pas_creer_assignation(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": self.apprenant.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_apprenant_ne_peut_pas_creer_assignation(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": self.apprenant.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_creer_assignation(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "apprenant": self.apprenant.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_consulter_assignations(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.get(self.assignation_url())
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_groupe_autre_promotion_refuse(self):
        groupe_p2 = Groupe.objects.create(promotion=self.promotion_2, nom="Groupe P2")
        res = self.client.post(self.assignation_url(), {
            "brief": self.brief.id,
            "groupe": groupe_p2.id,
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


# ─── Livrables ────────────────────────────────────────────────────────────────

class LivrableTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.brief = self.creer_brief()
        self.assignation = Assignation.objects.create(
            brief=self.brief, apprenant=self.apprenant
        )

    def livrable_url(self):
        return reverse("livrable-list", kwargs={"tenant_id": self.tenant.id})

    def livrable_detail_url(self, livrable_id):
        return reverse("livrable-detail", kwargs={"tenant_id": self.tenant.id, "pk": livrable_id})

    def test_apprenant_peut_deposer_livrable(self):
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.post(self.livrable_url(), {
            "assignation": self.assignation.id,
            "titre": "Mon livrable",
            "description": "Desc",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Livrable.objects.get(id=res.data["id"]).deposant, self.apprenant)

    def test_apprenant_ne_peut_pas_deposer_pour_autre(self):
        autre_app = User.objects.create_user(
            email="autre2@test.com", password="pw", nom="A", prenom="B", actif=True
        )
        MembreTenant.objects.create(utilisateur=autre_app, tenant=self.tenant,
                                    role=MembreTenant.Role.APPRENANT, actif=True)
        InscriptionPromotion.objects.create(
            promotion=self.promotion, apprenant=autre_app, actif=True
        )
        autre_ass = Assignation.objects.create(brief=self.brief, apprenant=autre_app)
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.post(self.livrable_url(), {
            "assignation": autre_ass.id,
            "titre": "Interdit",
            "description": "Desc",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_ne_peut_pas_deposer_livrable(self):
        res = self.client.post(self.livrable_url(), {
            "assignation": self.assignation.id,
            "titre": "Livrable Formateur",
            "description": "Desc",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_peut_modifier_statut(self):
        livrable = Livrable.objects.create(
            assignation=self.assignation, deposant=self.apprenant, titre="L", description="D"
        )
        res = self.client.patch(
            self.livrable_detail_url(livrable.id),
            {"statut": Livrable.Statut.RETENU},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        livrable.refresh_from_db()
        self.assertEqual(livrable.statut, Livrable.Statut.RETENU)

    def test_apprenant_ne_peut_pas_modifier_statut(self):
        livrable = Livrable.objects.create(
            assignation=self.assignation, deposant=self.apprenant, titre="L", description="D"
        )
        self.client.force_authenticate(user=self.apprenant)
        res = self.client.patch(
            self.livrable_detail_url(livrable.id),
            {"statut": Livrable.Statut.RETENU},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_consulter_livrables(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.get(self.livrable_url())
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_modifier_statut_livrable(self):
        livrable = Livrable.objects.create(
            assignation=self.assignation, deposant=self.apprenant, titre="L", description="D"
        )
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.patch(
            self.livrable_detail_url(livrable.id),
            {"statut": Livrable.Statut.RETENU},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_suppression_livrable_interdite(self):
        livrable = Livrable.objects.create(
            assignation=self.assignation, deposant=self.apprenant, titre="L", description="D"
        )
        res = self.client.delete(self.livrable_detail_url(livrable.id))
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_formateur_ne_voit_pas_livrables_promo_non_affectee(self):
        """formateur_2 n'est pas affecté à promotion."""
        livrable = Livrable.objects.create(
            assignation=self.assignation, deposant=self.apprenant, titre="L", description="D"
        )
        self.client.force_authenticate(user=self.formateur_2)
        res = self.client.get(self.livrable_url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [l["id"] for l in res.data]
        self.assertNotIn(livrable.id, ids)


# ─── FichierLivrable — types de fichiers ─────────────────────────────────────

class FichierLivrableTypeTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        brief = self.creer_brief()
        assignation = Assignation.objects.create(brief=brief, apprenant=self.apprenant)
        self.livrable = Livrable.objects.create(
            assignation=assignation, deposant=self.apprenant, titre="L", description="D"
        )
        self.client.force_authenticate(user=self.apprenant)

    def fichier_url(self):
        return reverse("fichier-livrable-list", kwargs={"tenant_id": self.tenant.id})

    def _post_fichier(self, nom, contenu, content_type="application/pdf"):
        fichier = SimpleUploadedFile(nom, contenu, content_type=content_type)
        return self.client.post(self.fichier_url(), {
            "livrable": self.livrable.id,
            "nom": nom,
            "fichier": fichier,
        })

    def test_pdf_accepte(self):
        res = self._post_fichier("doc.pdf", b"PDF content")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_pptx_accepte(self):
        res = self._post_fichier("slides.pptx", b"PPTX content",
                                 "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_docx_accepte(self):
        res = self._post_fichier("rapport.docx", b"DOCX content",
                                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_txt_accepte(self):
        res = self._post_fichier("notes.txt", b"Texte", "text/plain")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_png_refuse(self):
        res = self._post_fichier("image.png", b"PNG data", "image/png")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_jpg_refuse(self):
        res = self._post_fichier("photo.jpg", b"JPG data", "image/jpeg")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mp4_refuse(self):
        res = self._post_fichier("video.mp4", b"MP4 data", "video/mp4")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_zip_refuse(self):
        res = self._post_fichier("archive.zip", b"ZIP data", "application/zip")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_url_seule_acceptee(self):
        res = self.client.post(self.fichier_url(), {
            "livrable": self.livrable.id,
            "nom": "Lien externe",
            "url": "https://example.com/doc",
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_url_et_fichier_refuses(self):
        fichier = SimpleUploadedFile("doc.pdf", b"PDF", content_type="application/pdf")
        res = self.client.post(self.fichier_url(), {
            "livrable": self.livrable.id,
            "nom": "Invalide",
            "url": "https://example.com",
            "fichier": fichier,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_taille_maximale_respectee(self):
        gros_fichier = SimpleUploadedFile("gros.pdf", b"0" * (5 * 1024 * 1024 + 1),
                                          content_type="application/pdf")
        res = self.client.post(self.fichier_url(), {
            "livrable": self.livrable.id,
            "nom": "gros.pdf",
            "fichier": gros_fichier,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
