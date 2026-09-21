from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import MembreTenant
from pedagogie.models import Competence, Formation, Groupe, InscriptionPromotion, Module, Promotion
from tenants.models import Tenant

from .models import Assignation, Brief, RessourceBrief


User = get_user_model()


class ActivitesAPITests(APITestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            nom="Organisme Test",
        )

        self.admin = User.objects.create_user(
            email="admin@test.com",
            password="Password123!",
            nom="Admin",
            prenom="Test",
            actif=True,
        )

        MembreTenant.objects.create(
            utilisateur=self.admin,
            tenant=self.tenant,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        )

        self.apprenant = User.objects.create_user(
            email="apprenant@test.com",
            password="Password123!",
            nom="Apprenant",
            prenom="Test",
            actif=True,
        )

        MembreTenant.objects.create(
            utilisateur=self.apprenant,
            tenant=self.tenant,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )

        self.formation = Formation.objects.create(
            tenant=self.tenant,
            nom="Formation Web",
        )

        self.promotion = Promotion.objects.create(
            formation=self.formation,
            nom="Promotion 2026",
            date_debut="2026-01-01",
        )

        self.module = Module.objects.create(
            formation=self.formation,
            nom="Développement Web",
            ordre=1,
        )

        self.competence = Competence.objects.create(
            module=self.module,
            nom="Développer une API",
            ordre=1,
        )

        InscriptionPromotion.objects.create(
            promotion=self.promotion,
            apprenant=self.apprenant,
            actif=True,
        )

        self.client.force_authenticate(user=self.admin)

    def creer_brief(self):
        return Brief.objects.create(
            promotion=self.promotion,
            titre="Créer une API REST",
            description="Description du brief",
            consignes="Consignes du brief",
            date_debut="2026-09-01T08:00:00Z",
            date_limite="2026-09-30T18:00:00Z",
        )

    # ------------------------------------------------------------------
    # BRIEF
    # ------------------------------------------------------------------

    def test_creer_brief(self):
        url = reverse(
            "brief-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "promotion": self.promotion.id,
            "titre": "Créer une API REST",
            "description": "Description du brief",
            "consignes": "Consignes du brief",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
            "statut": "BROUILLON",
            "competences": [self.competence.id],
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Brief.objects.count(), 1)

    def test_brief_refuse_si_date_limite_avant_date_debut(self):
        url = reverse(
            "brief-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "promotion": self.promotion.id,
            "titre": "Brief invalide",
            "description": "Description",
            "consignes": "Consignes",
            "date_debut": "2026-09-30T18:00:00Z",
            "date_limite": "2026-09-01T08:00:00Z",
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_brief_refuse_competence_autre_formation(self):
        autre_formation = Formation.objects.create(
            tenant=self.tenant,
            nom="Autre Formation",
        )

        autre_module = Module.objects.create(
            formation=autre_formation,
            nom="Autre Module",
            ordre=1,
        )

        autre_competence = Competence.objects.create(
            module=autre_module,
            nom="Autre Compétence",
            ordre=1,
        )

        url = reverse(
            "brief-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "promotion": self.promotion.id,
            "titre": "Brief test",
            "description": "Description",
            "consignes": "Consignes",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
            "competences": [autre_competence.id],
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------
    # RESSOURCE
    # ------------------------------------------------------------------

    def test_ressource_refuse_url_et_fichier(self):
        brief = self.creer_brief()

        url = reverse(
            "ressource-brief-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        fichier = SimpleUploadedFile(
            "documentation.pdf",
            b"contenu du fichier",
            content_type="application/pdf",
        )

        data = {
            "brief": brief.id,
            "titre": "Documentation",
            "url": "https://example.com",
            "fichier": fichier,
        }

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    # ------------------------------------------------------------------
    # ASSIGNATION
    # ------------------------------------------------------------------

    def test_assignation_apprenant(self):
        brief = self.creer_brief()

        url = reverse(
            "assignation-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "brief": brief.id,
            "apprenant": self.apprenant.id,
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Assignation.objects.count(), 1)

    def test_assignation_refuse_apprenant_non_inscrit(self):
        autre_apprenant = User.objects.create_user(
            email="autre@test.com",
            password="Password123!",
            nom="Autre",
            prenom="Apprenant",
            actif=True,
        )

        MembreTenant.objects.create(
            utilisateur=autre_apprenant,
            tenant=self.tenant,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )

        brief = self.creer_brief()

        url = reverse(
            "assignation-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "brief": brief.id,
            "apprenant": autre_apprenant.id,
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assignation_refuse_groupe_et_apprenant(self):
        groupe = Groupe.objects.create(
            promotion=self.promotion,
            nom="Groupe A",
        )

        brief = self.creer_brief()

        url = reverse(
            "assignation-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "brief": brief.id,
            "groupe": groupe.id,
            "apprenant": self.apprenant.id,
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assignation_refuse_sans_cible(self):
        brief = self.creer_brief()

        url = reverse(
            "assignation-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "brief": brief.id,
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assignation_apprenant_deux_fois_refusee(self):
        brief = self.creer_brief()

        Assignation.objects.create(
            brief=brief,
            apprenant=self.apprenant,
        )

        url = reverse(
            "assignation-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "brief": brief.id,
            "apprenant": self.apprenant.id,
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


    # ------------------------------------------------------------------
    # PERMISSIONS ET ISOLATION MULTI-TENANT
    # ------------------------------------------------------------------

    def test_apprenant_ne_peut_pas_gerer_les_briefs(self):
        self.client.force_authenticate(user=self.apprenant)

        url = reverse(
            "brief-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        data = {
            "promotion": self.promotion.id,
            "titre": "Brief interdit",
            "description": "Description",
            "consignes": "Consignes",
            "date_debut": "2026-09-01T08:00:00Z",
            "date_limite": "2026-09-30T18:00:00Z",
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_autre_tenant_ne_peut_pas_acceder_aux_briefs(self):
        autre_tenant = Tenant.objects.create(
            nom="Autre Organisme",
        )

        autre_admin = User.objects.create_user(
            email="autre-admin@test.com",
            password="Password123!",
            nom="Admin",
            prenom="Autre",
            actif=True,
        )

        MembreTenant.objects.create(
            utilisateur=autre_admin,
            tenant=autre_tenant,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        )

        self.client.force_authenticate(user=autre_admin)

        url = reverse(
            "brief-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_autre_tenant_ne_voit_pas_les_donnees_de_son_tenant(self):
        autre_tenant = Tenant.objects.create(
            nom="Autre Organisme",
        )

        autre_formation = Formation.objects.create(
            tenant=autre_tenant,
            nom="Formation Autre",
        )

        autre_promotion = Promotion.objects.create(
            formation=autre_formation,
            nom="Promotion Autre",
            date_debut="2026-01-01",
        )

        autre_admin = User.objects.create_user(
            email="autre-admin@test.com",
            password="Password123!",
            nom="Admin",
            prenom="Autre",
            actif=True,
        )

        MembreTenant.objects.create(
            utilisateur=autre_admin,
            tenant=autre_tenant,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        )

        Brief.objects.create(
            promotion=autre_promotion,
            titre="Brief Autre Organisme",
            description="Description",
            consignes="Consignes",
            date_debut="2026-09-01T08:00:00Z",
            date_limite="2026-09-30T18:00:00Z",
        )

        self.client.force_authenticate(user=autre_admin)

        url = reverse(
            "brief-list",
            kwargs={"tenant_id": autre_tenant.id},
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        self.assertEqual(
            response.data[0]["titre"],
            "Brief Autre Organisme",
        )

        # L'autre organisme ne doit pas voir les briefs
        # appartenant à notre organisme.
        self.client.force_authenticate(user=autre_admin)

        url = reverse(
            "brief-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_peut_acceder_aux_briefs(self):
        self.admin.est_admin_saas = True
        self.admin.save(update_fields=["est_admin_saas"])

        self.client.force_authenticate(user=self.admin)

        url = reverse(
            "brief-list",
            kwargs={"tenant_id": self.tenant.id},
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
