from datetime import date, timedelta
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import MembreTenant
from tenants.models import Tenant
from .models import (
    Competence,
    CompetenceNiveau,
    Formation,
    Groupe,
    GroupeMembre,
    InscriptionPromotion,
    Module,
    Niveau,
    Promotion,
)

Utilisateur = get_user_model()


class PedagogieBaseTestCase(APITestCase):
    def setUp(self):
        # Création de deux tenants distincts
        self.tenant_1 = Tenant.objects.create(
            nom="Organisme Alpha",
            description="Tenant 1 pour les tests",
        )
        self.tenant_2 = Tenant.objects.create(
            nom="Organisme Beta",
            description="Tenant 2 pour les tests",
        )

        # Admin SaaS
        self.admin_saas = Utilisateur.objects.create_user(
            email="saas_admin@test.com",
            password="password123",
            nom="SaaS",
            prenom="Admin",
            actif=True,
            est_admin_saas=True,
        )

        # Admin Organisme 1
        self.admin_tenant_1 = Utilisateur.objects.create_user(
            email="admin_org1@test.com",
            password="password123",
            nom="Admin",
            prenom="Org1",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.admin_tenant_1,
            tenant=self.tenant_1,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        )

        # Formateur Organisme 1 (aucun accès prévu pour ce socle)
        self.formateur_tenant_1 = Utilisateur.objects.create_user(
            email="formateur_org1@test.com",
            password="password123",
            nom="Formateur",
            prenom="Org1",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.formateur_tenant_1,
            tenant=self.tenant_1,
            role=MembreTenant.Role.FORMATEUR,
            actif=True,
        )

        # Apprenant Organisme 1 (aucun accès prévu pour ce socle)
        self.apprenant_tenant_1 = Utilisateur.objects.create_user(
            email="apprenant_org1@test.com",
            password="password123",
            nom="Apprenant",
            prenom="Org1",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.apprenant_tenant_1,
            tenant=self.tenant_1,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )

        # Admin Organisme 2
        self.admin_tenant_2 = Utilisateur.objects.create_user(
            email="admin_org2@test.com",
            password="password123",
            nom="Admin",
            prenom="Org2",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.admin_tenant_2,
            tenant=self.tenant_2,
            role=MembreTenant.Role.ADMINISTRATEUR,
            actif=True,
        )


class FormationTests(PedagogieBaseTestCase):
    def test_crud_formation_admin_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)

        # CREATE
        url = f"/api/tenants/{self.tenant_1.id}/formations/"
        payload = {
            "nom": "Développeur Python",
            "description": "Formation complète backend",
            "actif": True,
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        formation_id = res.data["id"]
        self.assertEqual(res.data["tenant"], self.tenant_1.id)

        # LIST
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

        # RETRIEVE
        detail_url = f"/api/tenants/{self.tenant_1.id}/formations/{formation_id}/"
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["nom"], "Développeur Python")

        # PATCH
        res = self.client.patch(detail_url, {"nom": "Développeur Python Django"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["nom"], "Développeur Python Django")

        # DELETE
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Formation.objects.filter(id=formation_id).exists())

    def test_admin_saas_peut_acceder(self):
        self.client.force_authenticate(user=self.admin_saas)
        url = f"/api/tenants/{self.tenant_1.id}/formations/"
        res = self.client.post(url, {"nom": "Formation SaaS"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_formateur_et_apprenant_ont_acces_refuse(self):
        url = f"/api/tenants/{self.tenant_1.id}/formations/"

        # Formateur
        self.client.force_authenticate(user=self.formateur_tenant_1)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Apprenant
        self.client.force_authenticate(user=self.apprenant_tenant_1)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_authentifie_recoit_401(self):
        url = f"/api/tenants/{self.tenant_1.id}/formations/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cross_tenant_bloque_par_permission(self):
        # Admin Tenant 1 tente d'accéder aux formations de Tenant 2
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_2.id}/formations/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_isolation_queryset_retourne_404(self):
        # Une formation créée dans Tenant 2 ne doit pas être visible depuis l'URL de Tenant 1
        formation_t2 = Formation.objects.create(
            tenant=self.tenant_2,
            nom="Formation Beta",
        )
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/formations/{formation_t2.id}/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_methode_put_interdite_retourne_405(self):
        formation = Formation.objects.create(
            tenant=self.tenant_1,
            nom="Formation 1",
        )
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/formations/{formation.id}/"
        res = self.client.put(url, {"nom": "Tentative PUT"})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_unicite_nom_formation_par_tenant(self):
        Formation.objects.create(tenant=self.tenant_1, nom="Data Science")
        self.client.force_authenticate(user=self.admin_tenant_1)

        # Doublon dans le même tenant -> 400
        url = f"/api/tenants/{self.tenant_1.id}/formations/"
        res = self.client.post(url, {"nom": "Data Science"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Même nom dans un autre tenant -> 201
        self.client.force_authenticate(user=self.admin_tenant_2)
        url_t2 = f"/api/tenants/{self.tenant_2.id}/formations/"
        res_t2 = self.client.post(url_t2, {"nom": "Data Science"})
        self.assertEqual(res_t2.status_code, status.HTTP_201_CREATED)


class PromotionTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation_t1 = Formation.objects.create(
            tenant=self.tenant_1,
            nom="Formation Web",
        )
        self.formation_t2 = Formation.objects.create(
            tenant=self.tenant_2,
            nom="Formation Mobile",
        )

    def test_crud_promotion_et_validation_dates(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/promotions/"

        # Erreur si date_fin < date_debut
        res = self.client.post(url, {
            "formation": self.formation_t1.id,
            "nom": "Promo 2026",
            "date_debut": "2026-09-01",
            "date_fin": "2026-06-01",
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_fin", res.data)

        # Succès avec dates valides
        res = self.client.post(url, {
            "formation": self.formation_t1.id,
            "nom": "Promo 2026",
            "date_debut": "2026-09-01",
            "date_fin": "2027-06-30",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_unicite_nom_promotion_par_formation(self):
        Promotion.objects.create(
            formation=self.formation_t1,
            nom="Promo Automne",
            date_debut=date(2026, 9, 1),
        )
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/promotions/"

        res = self.client.post(url, {
            "formation": self.formation_t1.id,
            "nom": "Promo Automne",
            "date_debut": "2026-10-01",
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validation_parent_enfant_promotion_autre_tenant(self):
        # Tenter d'associer une promotion à une formation appartenant à Tenant 2
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/promotions/"

        res = self.client.post(url, {
            "formation": self.formation_t2.id,
            "nom": "Promo Piratage",
            "date_debut": "2026-09-01",
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class ModuleEtCompetenceTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation_t1 = Formation.objects.create(
            tenant=self.tenant_1,
            nom="Formation DevOps",
        )
        self.formation_t2 = Formation.objects.create(
            tenant=self.tenant_2,
            nom="Formation Cloud",
        )

    def test_module_contraintes_nom_et_ordre(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/modules/"

        # Ordre = 0 interdit
        res = self.client.post(url, {
            "formation": self.formation_t1.id,
            "nom": "Module 0",
            "ordre": 0,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Création nominale
        res = self.client.post(url, {
            "formation": self.formation_t1.id,
            "nom": "Docker & Conteneurs",
            "ordre": 1,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Doublon de nom dans la même formation
        res = self.client.post(url, {
            "formation": self.formation_t1.id,
            "nom": "Docker & Conteneurs",
            "ordre": 2,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Doublon d'ordre dans la même formation
        res = self.client.post(url, {
            "formation": self.formation_t1.id,
            "nom": "Kubernetes",
            "ordre": 1,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validation_parent_enfant_module_autre_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/modules/"

        res = self.client.post(url, {
            "formation": self.formation_t2.id,
            "nom": "Module Intrus",
            "ordre": 1,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_competence_contraintes_et_parent_enfant(self):
        module = Module.objects.create(
            formation=self.formation_t1,
            nom="CI/CD",
            ordre=1,
        )
        module_t2 = Module.objects.create(
            formation=self.formation_t2,
            nom="AWS Cloud",
            ordre=1,
        )

        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/competences/"

        # Création nominale
        res = self.client.post(url, {
            "module": module.id,
            "nom": "Configurer GitHub Actions",
            "ordre": 1,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Doublon d'ordre dans le même module
        res = self.client.post(url, {
            "module": module.id,
            "nom": "Créer un pipeline GitLab",
            "ordre": 1,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Tenter d'associer la compétence à un module de Tenant 2
        res = self.client.post(url, {
            "module": module_t2.id,
            "nom": "Compétence Pirate",
            "ordre": 1,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_absence_relation_promotion_module(self):
        # Vérification structurelle : aucune relation directe entre Promotion et Module
        self.assertFalse(hasattr(Promotion, "modules"))
        self.assertFalse(hasattr(Promotion, "module"))
        self.assertFalse(hasattr(Module, "promotions"))
        self.assertFalse(hasattr(Module, "promotion"))


class NiveauTests(PedagogieBaseTestCase):
    def test_crud_niveau_admin_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)

        # CREATE
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"
        payload = {
            "nom": "Débutant",
            "description": "Niveau débutant socle",
            "ordre": 1,
            "actif": True,
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        niveau_id = res.data["id"]
        self.assertEqual(res.data["tenant"], self.tenant_1.id)
        self.assertEqual(res.data["nom"], "Débutant")

        # LIST
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

        # RETRIEVE
        detail_url = f"/api/tenants/{self.tenant_1.id}/niveaux/{niveau_id}/"
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["nom"], "Débutant")

        # PATCH
        res = self.client.patch(detail_url, {"nom": "Initiation", "ordre": 2})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["nom"], "Initiation")
        self.assertEqual(res.data["ordre"], 2)

        # DELETE
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Niveau.objects.filter(id=niveau_id).exists())

    def test_unicite_nom_par_tenant(self):
        Niveau.objects.create(tenant=self.tenant_1, nom="Intermédiaire", ordre=1)
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"

        res = self.client.post(url, {"nom": "Intermédiaire", "ordre": 2})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nom", res.data)

    def test_unicite_nom_insensible_casse(self):
        Niveau.objects.create(tenant=self.tenant_1, nom="Avancé", ordre=1)
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"

        res = self.client.post(url, {"nom": "  avancé  ", "ordre": 2})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nom", res.data)

    def test_nom_identique_autorise_differents_tenants(self):
        Niveau.objects.create(tenant=self.tenant_1, nom="Expert", ordre=1)
        self.client.force_authenticate(user=self.admin_tenant_2)
        url_t2 = f"/api/tenants/{self.tenant_2.id}/niveaux/"

        res = self.client.post(url_t2, {"nom": "Expert", "ordre": 1})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_unicite_ordre_par_tenant(self):
        Niveau.objects.create(tenant=self.tenant_1, nom="Niveau A", ordre=1)
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"

        res = self.client.post(url, {"nom": "Niveau B", "ordre": 1})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ordre", res.data)

    def test_ordre_identique_autorise_differents_tenants(self):
        Niveau.objects.create(tenant=self.tenant_1, nom="Niveau A", ordre=1)
        self.client.force_authenticate(user=self.admin_tenant_2)
        url_t2 = f"/api/tenants/{self.tenant_2.id}/niveaux/"

        res = self.client.post(url_t2, {"nom": "Niveau X", "ordre": 1})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_rejet_ordre_inferieur_a_1(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"

        res = self.client.post(url, {"nom": "Niveau Zéro", "ordre": 0})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ordre", res.data)

    def test_isolation_tenant_niveau(self):
        niveau_t2 = Niveau.objects.create(tenant=self.tenant_2, nom="Niveau Beta", ordre=1)
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/{niveau_t2.id}/"

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_methode_put_interdite_sur_niveau(self):
        niveau = Niveau.objects.create(tenant=self.tenant_1, nom="Niveau PUT", ordre=1)
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/{niveau.id}/"

        res = self.client.put(url, {"nom": "Tentative"})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class CompetenceNiveauTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        # Formations, modules et compétences dans Tenant 1
        self.formation_1 = Formation.objects.create(tenant=self.tenant_1, nom="Formation 1")
        self.module_1 = Module.objects.create(formation=self.formation_1, nom="Module 1", ordre=1)
        self.competence_1 = Competence.objects.create(module=self.module_1, nom="Compétence 1", ordre=1)

        self.formation_2 = Formation.objects.create(tenant=self.tenant_1, nom="Formation 2")
        self.module_2 = Module.objects.create(formation=self.formation_2, nom="Module 2", ordre=1)
        self.competence_2 = Competence.objects.create(module=self.module_2, nom="Compétence 2", ordre=1)

        # Niveaux dans Tenant 1
        self.niveau_debutant = Niveau.objects.create(tenant=self.tenant_1, nom="Débutant", ordre=1)
        self.niveau_avance = Niveau.objects.create(tenant=self.tenant_1, nom="Avancé", ordre=2)

        # Éléments dans Tenant 2
        self.formation_t2 = Formation.objects.create(tenant=self.tenant_2, nom="Formation T2")
        self.module_t2 = Module.objects.create(formation=self.formation_t2, nom="Module T2", ordre=1)
        self.competence_t2 = Competence.objects.create(module=self.module_t2, nom="Compétence T2", ordre=1)
        self.niveau_t2 = Niveau.objects.create(tenant=self.tenant_2, nom="Débutant T2", ordre=1)

    def test_crud_competence_niveau(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/"

        # CREATE
        payload = {
            "competence": self.competence_1.id,
            "niveau": self.niveau_debutant.id,
            "description": "Maîtrise basique des commandes",
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        cn_id = res.data["id"]
        self.assertEqual(res.data["competence"], self.competence_1.id)
        self.assertEqual(res.data["niveau"], self.niveau_debutant.id)
        self.assertEqual(res.data["description"], "Maîtrise basique des commandes")

        # LIST
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

        # RETRIEVE
        detail_url = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/{cn_id}/"
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["description"], "Maîtrise basique des commandes")

        # PATCH
        res = self.client.patch(detail_url, {"description": "Maîtrise révisée"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["description"], "Maîtrise révisée")

        # DELETE
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CompetenceNiveau.objects.filter(id=cn_id).exists())

    def test_reutilisation_niveau_par_plusieurs_formations_meme_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/"

        # Associer Niveau Débutant à Compétence 1 (Formation 1)
        res1 = self.client.post(url, {
            "competence": self.competence_1.id,
            "niveau": self.niveau_debutant.id,
            "description": "Attendu formation 1",
        })
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Associer le MÊME Niveau Débutant à Compétence 2 (Formation 2)
        res2 = self.client.post(url, {
            "competence": self.competence_2.id,
            "niveau": self.niveau_debutant.id,
            "description": "Attendu formation 2",
        })
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)

        # Vérification en base : les 2 associations pointent vers le même niveau
        self.assertEqual(
            CompetenceNiveau.objects.filter(niveau=self.niveau_debutant).count(),
            2,
        )

    def test_unicite_paire_competence_niveau(self):
        CompetenceNiveau.objects.create(
            competence=self.competence_1,
            niveau=self.niveau_debutant,
            description="Initiale",
        )
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/"

        res = self.client.post(url, {
            "competence": self.competence_1.id,
            "niveau": self.niveau_debutant.id,
            "description": "Doublon",
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejet_competence_et_niveau_tenants_differents(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/"

        # Compétence de Tenant 1 avec Niveau de Tenant 2
        res = self.client.post(url, {
            "competence": self.competence_1.id,
            "niveau": self.niveau_t2.id,
            "description": "Croisement illicite",
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Compétence de Tenant 2 avec Niveau de Tenant 1
        res = self.client.post(url, {
            "competence": self.competence_t2.id,
            "niveau": self.niveau_debutant.id,
            "description": "Croisement illicite 2",
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_isolation_tenant_competence_niveau(self):
        cn_t2 = CompetenceNiveau.objects.create(
            competence=self.competence_t2,
            niveau=self.niveau_t2,
            description="Tenant 2 association",
        )
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/{cn_t2.id}/"

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_filtres_query_params_competence_et_niveau(self):
        CompetenceNiveau.objects.create(
            competence=self.competence_1,
            niveau=self.niveau_debutant,
            description="C1 - N1",
        )
        CompetenceNiveau.objects.create(
            competence=self.competence_1,
            niveau=self.niveau_avance,
            description="C1 - N2",
        )
        CompetenceNiveau.objects.create(
            competence=self.competence_2,
            niveau=self.niveau_debutant,
            description="C2 - N1",
        )

        self.client.force_authenticate(user=self.admin_tenant_1)
        base_url = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/"

        # Filtre par compétence
        res = self.client.get(f"{base_url}?competence={self.competence_1.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 2)

        # Filtre par niveau
        res = self.client.get(f"{base_url}?niveau={self.niveau_avance.id}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_methode_put_interdite_sur_competence_niveau(self):
        cn = CompetenceNiveau.objects.create(
            competence=self.competence_1,
            niveau=self.niveau_debutant,
        )
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/{cn.id}/"

        res = self.client.put(url, {"description": "Tentative"})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class PedagogieNiveauxPermissionsTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation = Formation.objects.create(tenant=self.tenant_1, nom="Formation Perm")
        self.module = Module.objects.create(formation=self.formation, nom="Module Perm", ordre=1)
        self.competence = Competence.objects.create(module=self.module, nom="Comp Perm", ordre=1)
        self.niveau = Niveau.objects.create(tenant=self.tenant_1, nom="Niveau Perm", ordre=1)

        # Utilisateur non membre / sans tenant
        self.user_sans_tenant = Utilisateur.objects.create_user(
            email="sans_tenant@test.com",
            password="password123",
            nom="Inconnu",
            prenom="User",
            actif=True,
        )

    def test_admin_saas_autorise(self):
        self.client.force_authenticate(user=self.admin_saas)
        url_niveau = f"/api/tenants/{self.tenant_1.id}/niveaux/"
        res = self.client.post(url_niveau, {"nom": "Niveau SaaS", "ordre": 2})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        url_cn = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/"
        res = self.client.post(url_cn, {
            "competence": self.competence.id,
            "niveau": self.niveau.id,
            "description": "Association SaaS",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_admin_tenant_autorise_sur_son_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        url_cn = f"/api/tenants/{self.tenant_1.id}/competence-niveaux/"
        res = self.client.get(url_cn)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_admin_tenant_refuse_sur_autre_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url_niveau_t2 = f"/api/tenants/{self.tenant_2.id}/niveaux/"
        res = self.client.get(url_niveau_t2)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        url_cn_t2 = f"/api/tenants/{self.tenant_2.id}/competence-niveaux/"
        res = self.client.get(url_cn_t2)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_refuse(self):
        self.client.force_authenticate(user=self.formateur_tenant_1)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/niveaux/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/competence-niveaux/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_apprenant_refuse(self):
        self.client.force_authenticate(user=self.apprenant_tenant_1)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/niveaux/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/competence-niveaux/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_utilisateur_non_membre_refuse(self):
        self.client.force_authenticate(user=self.user_sans_tenant)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/niveaux/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/competence-niveaux/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_authentifie_refuse(self):
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/niveaux/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/competence-niveaux/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class InscriptionPromotionTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation = Formation.objects.create(tenant=self.tenant_1, nom="Formation Python")
        self.promotion_1 = Promotion.objects.create(
            formation=self.formation,
            nom="Promo 2026-A",
            date_debut=date(2026, 9, 1),
        )
        self.promotion_2 = Promotion.objects.create(
            formation=self.formation,
            nom="Promo 2026-B",
            date_debut=date(2026, 10, 1),
        )

        # Deuxième apprenant dans Tenant 1
        self.apprenant_2 = Utilisateur.objects.create_user(
            email="apprenant2_org1@test.com",
            password="password123",
            nom="Deuxieme",
            prenom="Apprenant",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.apprenant_2,
            tenant=self.tenant_1,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )

        # Apprenant dans Tenant 2
        self.apprenant_t2 = Utilisateur.objects.create_user(
            email="apprenant_org2@test.com",
            password="password123",
            nom="Autre",
            prenom="Tenant",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.apprenant_t2,
            tenant=self.tenant_2,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )

    def test_inscription_apprenant_promotion(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/promotions/{self.promotion_1.id}/inscrire-apprenant/"

        res = self.client.post(url, {"apprenant_id": self.apprenant_tenant_1.id})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            InscriptionPromotion.objects.filter(
                promotion=self.promotion_1,
                apprenant=self.apprenant_tenant_1,
                actif=True,
            ).exists()
        )

    def test_rejet_inscription_deux_promotions_actives_simultanees(self):
        # Inscrire l'apprenant dans Promo 1
        InscriptionPromotion.objects.create(
            promotion=self.promotion_1,
            apprenant=self.apprenant_tenant_1,
            actif=True,
        )

        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/promotions/{self.promotion_2.id}/inscrire-apprenant/"

        # Tentative d'inscription dans Promo 2 -> Doit échouer
        res = self.client.post(url, {"apprenant_id": self.apprenant_tenant_1.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("apprenant", res.data)

    def test_rejet_inscription_utilisateur_non_apprenant(self):
        # Tentative d'inscrire le formateur comme apprenant
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/promotions/{self.promotion_1.id}/inscrire-apprenant/"

        res = self.client.post(url, {"apprenant_id": self.formateur_tenant_1.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejet_inscription_apprenant_autre_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/promotions/{self.promotion_1.id}/inscrire-apprenant/"

        res = self.client.post(url, {"apprenant_id": self.apprenant_t2.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_desinscription_apprenant_et_reinscription(self):
        # Inscription
        inscription = InscriptionPromotion.objects.create(
            promotion=self.promotion_1,
            apprenant=self.apprenant_tenant_1,
            actif=True,
        )

        self.client.force_authenticate(user=self.admin_tenant_1)
        url_desinscrire = f"/api/tenants/{self.tenant_1.id}/promotions/{self.promotion_1.id}/desinscrire-apprenant/"

        # Désinscription
        res = self.client.post(url_desinscrire, {"apprenant_id": self.apprenant_tenant_1.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        inscription.refresh_from_db()
        self.assertFalse(inscription.actif)
        self.assertIsNotNone(inscription.date_desinscription)

        # Réinscription dans une autre promotion (Promo 2) désormais permise
        url_inscrire_2 = f"/api/tenants/{self.tenant_1.id}/promotions/{self.promotion_2.id}/inscrire-apprenant/"
        res_2 = self.client.post(url_inscrire_2, {"apprenant_id": self.apprenant_tenant_1.id})
        self.assertEqual(res_2.status_code, status.HTTP_201_CREATED)


class GroupeTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation = Formation.objects.create(tenant=self.tenant_1, nom="Formation Dev")
        self.promotion_1 = Promotion.objects.create(
            formation=self.formation,
            nom="Promo 1",
            date_debut=date(2026, 9, 1),
        )
        self.promotion_2 = Promotion.objects.create(
            formation=self.formation,
            nom="Promo 2",
            date_debut=date(2026, 10, 1),
        )

        # Inscriptions promotions
        self.inscription_1 = InscriptionPromotion.objects.create(
            promotion=self.promotion_1,
            apprenant=self.apprenant_tenant_1,
            actif=True,
        )

        # Apprenant 2 dans Promo 1
        self.apprenant_2 = Utilisateur.objects.create_user(
            email="apprenant2_test@test.com",
            password="password123",
            nom="Deux",
            prenom="User",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.apprenant_2,
            tenant=self.tenant_1,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )
        self.inscription_2 = InscriptionPromotion.objects.create(
            promotion=self.promotion_1,
            apprenant=self.apprenant_2,
            actif=True,
        )

        # Apprenant 3 dans Promo 2
        self.apprenant_3 = Utilisateur.objects.create_user(
            email="apprenant3_test@test.com",
            password="password123",
            nom="Trois",
            prenom="User",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.apprenant_3,
            tenant=self.tenant_1,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )
        self.inscription_3 = InscriptionPromotion.objects.create(
            promotion=self.promotion_2,
            apprenant=self.apprenant_3,
            actif=True,
        )

        # Apprenant sans promotion
        self.apprenant_sans_promo = Utilisateur.objects.create_user(
            email="sans_promo@test.com",
            password="password123",
            nom="Sans",
            prenom="Promo",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.apprenant_sans_promo,
            tenant=self.tenant_1,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )

        # Éléments Tenant 2
        self.formation_t2 = Formation.objects.create(tenant=self.tenant_2, nom="Formation T2")
        self.promotion_t2 = Promotion.objects.create(
            formation=self.formation_t2,
            nom="Promo T2",
            date_debut=date(2026, 9, 1),
        )
        self.apprenant_t2 = Utilisateur.objects.create_user(
            email="apprenant_t2@test.com",
            password="password123",
            nom="T2",
            prenom="User",
            actif=True,
        )
        MembreTenant.objects.create(
            utilisateur=self.apprenant_t2,
            tenant=self.tenant_2,
            role=MembreTenant.Role.APPRENANT,
            actif=True,
        )
        self.inscription_t2 = InscriptionPromotion.objects.create(
            promotion=self.promotion_t2,
            apprenant=self.apprenant_t2,
            actif=True,
        )

    def test_crud_groupe(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/"

        # CREATE
        payload = {
            "promotion": self.promotion_1.id,
            "nom": "Groupe Alpha",
            "description": "Premier groupe projet",
            "actif": True,
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        groupe_id = res.data["id"]
        self.assertEqual(res.data["nom"], "Groupe Alpha")
        self.assertEqual(res.data["nb_membres"], 0)

        # LIST
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

        # RETRIEVE
        detail_url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_id}/"
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["nom"], "Groupe Alpha")

        # PATCH
        res = self.client.patch(detail_url, {"nom": "Groupe Alpha Renommé"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["nom"], "Groupe Alpha Renommé")

        # DELETE
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Groupe.objects.filter(id=groupe_id).exists())

    def test_unicite_nom_groupe_par_promotion(self):
        Groupe.objects.create(promotion=self.promotion_1, nom="Groupe Un")
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/"

        # Doublon exact
        res = self.client.post(url, {"promotion": self.promotion_1.id, "nom": "Groupe Un"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Doublon insensible à la casse
        res = self.client.post(url, {"promotion": self.promotion_1.id, "nom": "  groupe un  "})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Même nom dans une autre promotion -> OK
        res = self.client.post(url, {"promotion": self.promotion_2.id, "nom": "Groupe Un"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_groupe_vide_autorise(self):
        groupe = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe Sans Membre")
        self.client.force_authenticate(user=self.admin_tenant_1)
        detail_url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/"
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["nb_membres"], 0)
        self.assertEqual(len(res.data["membres"]), 0)

    def test_appartenance_multiple_groupes_meme_promotion(self):
        groupe_a = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe A")
        groupe_b = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe B")

        self.client.force_authenticate(user=self.admin_tenant_1)

        # Ajout apprenant 1 dans Groupe A
        url_a = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_a.id}/ajouter-apprenant/"
        res_a = self.client.post(url_a, {"apprenant_id": self.apprenant_tenant_1.id})
        self.assertEqual(res_a.status_code, status.HTTP_201_CREATED)

        # Ajout du MÊME apprenant 1 dans Groupe B
        url_b = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_b.id}/ajouter-apprenant/"
        res_b = self.client.post(url_b, {"apprenant_id": self.apprenant_tenant_1.id})
        self.assertEqual(res_b.status_code, status.HTTP_201_CREATED)

        # Vérification des deux appartenances
        self.assertEqual(GroupeMembre.objects.filter(apprenant=self.apprenant_tenant_1).count(), 2)

    def test_rejet_ajout_apprenant_autre_promotion(self):
        groupe_promo_1 = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe Promo 1")
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_promo_1.id}/ajouter-apprenant/"

        # apprenant_3 est dans Promo 2 -> tentative de l'ajouter dans un groupe de Promo 1
        res = self.client.post(url, {"apprenant_id": self.apprenant_3.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("apprenant", res.data)

    def test_rejet_ajout_apprenant_sans_promotion(self):
        groupe_promo_1 = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe Sans")
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_promo_1.id}/ajouter-apprenant/"

        res = self.client.post(url, {"apprenant_id": self.apprenant_sans_promo.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("apprenant", res.data)

    def test_rejet_doublon_apprenant_meme_groupe(self):
        groupe = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe Doublon")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_tenant_1)

        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/ajouter-apprenant/"

        res = self.client.post(url, {"apprenant_id": self.apprenant_tenant_1.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retirer_apprenant_groupe(self):
        groupe = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe Retrait")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_tenant_1)

        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/retirer-apprenant/"

        res = self.client.post(url, {"apprenant_id": self.apprenant_tenant_1.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(GroupeMembre.objects.filter(groupe=groupe, apprenant=self.apprenant_tenant_1).exists())

    def test_suppression_groupe_ne_supprime_pas_utilisateur(self):
        groupe = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe A Supprimer")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_tenant_1)

        self.client.force_authenticate(user=self.admin_tenant_1)
        detail_url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/"
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        # Le groupe et son association sont supprimés
        self.assertFalse(Groupe.objects.filter(id=groupe.id).exists())
        self.assertFalse(GroupeMembre.objects.filter(groupe_id=groupe.id).exists())
        # L'utilisateur apprenant existe TOUJOURS
        self.assertTrue(Utilisateur.objects.filter(id=self.apprenant_tenant_1.id).exists())

    def test_rejet_apprenant_autre_tenant(self):
        groupe = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe Tenant 1")
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/ajouter-apprenant/"

        res = self.client.post(url, {"apprenant_id": self.apprenant_t2.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_isolation_tenant_groupe(self):
        groupe_t2 = Groupe.objects.create(promotion=self.promotion_t2, nom="Groupe T2")
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_t2.id}/"

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_methode_put_interdite_sur_groupe(self):
        groupe = Groupe.objects.create(promotion=self.promotion_1, nom="Groupe PUT")
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/"

        res = self.client.put(url, {"nom": "Tentative"})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class GroupePermissionsTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation = Formation.objects.create(tenant=self.tenant_1, nom="Formation Perm")
        self.promotion = Promotion.objects.create(
            formation=self.formation,
            nom="Promo Perm",
            date_debut=date(2026, 9, 1),
        )
        self.groupe = Groupe.objects.create(promotion=self.promotion, nom="Groupe Perm")

        # Utilisateur sans tenant
        self.user_sans_tenant = Utilisateur.objects.create_user(
            email="sans_tenant_groupe@test.com",
            password="password123",
            nom="Inconnu",
            prenom="User",
            actif=True,
        )

    def test_admin_saas_autorise(self):
        self.client.force_authenticate(user=self.admin_saas)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/"
        res = self.client.post(url, {"promotion": self.promotion.id, "nom": "Groupe SaaS"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_admin_tenant_autorise_sur_son_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_admin_tenant_refuse_sur_autre_tenant(self):
        self.client.force_authenticate(user=self.admin_tenant_1)
        url = f"/api/tenants/{self.tenant_2.id}/groupes/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_refuse(self):
        self.client.force_authenticate(user=self.formateur_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_apprenant_refuse(self):
        self.client.force_authenticate(user=self.apprenant_tenant_1)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_utilisateur_non_membre_refuse(self):
        self.client.force_authenticate(user=self.user_sans_tenant)
        url = f"/api/tenants/{self.tenant_1.id}/groupes/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_authentifie_refuse(self):
        url = f"/api/tenants/{self.tenant_1.id}/groupes/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


