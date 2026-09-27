"""
Tests complets pour le flux pédagogique EduHub.

Couvre :
- Formations, Promotions, Modules, Compétences, Niveaux, CompétenceNiveaux (existants)
- FormateurPromotion : affectation, retrait, doublon, multi-formateur, multi-promotion
- Accès Formateur aux promotions (ses promotions seulement)
- Accès Formateur aux apprenants
- Groupes : Admin organisme + Formateur (ses promotions)
- Utilisateur.actif vs MembreTenant.actif
- Isolation multi-tenant
- Exclusion Admin SaaS des opérations métier
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import MembreTenant
from activites.models import Assignation, Brief, Livrable
from tenants.models import Tenant
from .models import (
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

Utilisateur = get_user_model()


# ─── Base ─────────────────────────────────────────────────────────────────────

class PedagogieBaseTestCase(APITestCase):
    def setUp(self):
        self.tenant_1 = Tenant.objects.create(nom="Organisme Alpha")
        self.tenant_2 = Tenant.objects.create(nom="Organisme Beta")

        self.admin_saas = Utilisateur.objects.create_user(
            email="saas@test.com", password="pw", nom="SaaS", prenom="Admin",
            actif=True, est_admin_saas=True,
        )

        self.admin_t1 = Utilisateur.objects.create_user(
            email="admin_t1@test.com", password="pw", nom="Admin", prenom="T1", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.admin_t1, tenant=self.tenant_1,
                                    role=MembreTenant.Role.ADMINISTRATEUR, actif=True)

        self.admin_t2 = Utilisateur.objects.create_user(
            email="admin_t2@test.com", password="pw", nom="Admin", prenom="T2", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.admin_t2, tenant=self.tenant_2,
                                    role=MembreTenant.Role.ADMINISTRATEUR, actif=True)

        self.formateur_t1 = Utilisateur.objects.create_user(
            email="formateur_t1@test.com", password="pw", nom="Formateur", prenom="T1", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.formateur_t1, tenant=self.tenant_1,
                                    role=MembreTenant.Role.FORMATEUR, actif=True)

        self.formateur2_t1 = Utilisateur.objects.create_user(
            email="formateur2_t1@test.com", password="pw", nom="Formateur2", prenom="T1", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.formateur2_t1, tenant=self.tenant_1,
                                    role=MembreTenant.Role.FORMATEUR, actif=True)

        self.apprenant_t1 = Utilisateur.objects.create_user(
            email="apprenant_t1@test.com", password="pw", nom="Apprenant", prenom="T1", actif=True,
        )
        MembreTenant.objects.create(utilisateur=self.apprenant_t1, tenant=self.tenant_1,
                                    role=MembreTenant.Role.APPRENANT, actif=True)

        # Apprenant avec compte NON activé mais membre actif du tenant
        self.apprenant_inactif = Utilisateur.objects.create_user(
            email="apprenant_inactif@test.com", password="pw", nom="Inactif", prenom="App",
            actif=False,  # compte non activé
        )
        MembreTenant.objects.create(utilisateur=self.apprenant_inactif, tenant=self.tenant_1,
                                    role=MembreTenant.Role.APPRENANT, actif=True)  # membre actif


# ─── Formations ───────────────────────────────────────────────────────────────

class FormationTests(PedagogieBaseTestCase):
    def test_admin_organisme_peut_creer_formation(self):
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/formations/",
            {"nom": "Dev Web", "actif": True},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_admin_saas_ne_peut_pas_acceder_formations(self):
        """Admin SaaS n'a aucun droit CRUD sur les formations (opération métier organisme)."""
        self.client.force_authenticate(user=self.admin_saas)
        url = f"/api/tenants/{self.tenant_1.id}/formations/"

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.post(url, {"nom": "Formation SaaS"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_modifier_formation(self):
        f = Formation.objects.create(tenant=self.tenant_1, nom="Formation Existante")
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.patch(
            f"/api/tenants/{self.tenant_1.id}/formations/{f.id}/",
            {"nom": "Modifiée"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_supprimer_formation(self):
        f = Formation.objects.create(tenant=self.tenant_1, nom="Formation À Supprimer")
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.delete(
            f"/api/tenants/{self.tenant_1.id}/formations/{f.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_ne_peut_pas_creer_formation(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/formations/",
            {"nom": "Formation Formateur"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_apprenant_sans_promotion_ne_voit_aucune_formation(self):
        Formation.objects.create(tenant=self.tenant_1, nom="Formation invisible")
        self.client.force_authenticate(user=self.apprenant_t1)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/formations/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])

    def test_cross_tenant_bloque(self):
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.get(f"/api/tenants/{self.tenant_2.id}/formations/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_isolation_queryset(self):
        f2 = Formation.objects.create(tenant=self.tenant_2, nom="Formation T2")
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/formations/{f2.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_crud_complet(self):
        self.client.force_authenticate(user=self.admin_t1)
        url = f"/api/tenants/{self.tenant_1.id}/formations/"
        res = self.client.post(url, {"nom": "Python", "description": "Desc", "actif": True})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        fid = res.data["id"]

        res = self.client.patch(f"{url}{fid}/", {"nom": "Python Avancé"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.delete(f"{url}{fid}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_unicite_nom_par_tenant(self):
        Formation.objects.create(tenant=self.tenant_1, nom="Data Science")
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/formations/", {"nom": "Data Science"}
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_interdit(self):
        f = Formation.objects.create(tenant=self.tenant_1, nom="PUT Test")
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.put(f"/api/tenants/{self.tenant_1.id}/formations/{f.id}/", {"nom": "X"})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


# ─── FormateurPromotion ───────────────────────────────────────────────────────

class FormateurPromotionTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation = Formation.objects.create(tenant=self.tenant_1, nom="Formation FP")
        self.promo_1 = Promotion.objects.create(
            formation=self.formation, nom="Promo 1", date_debut=date(2026, 9, 1)
        )
        self.promo_2 = Promotion.objects.create(
            formation=self.formation, nom="Promo 2", date_debut=date(2026, 10, 1)
        )

    # ── Affectation ───────────────────────────────────────────────────────────

    def test_admin_organisme_peut_affecter_formateur(self):
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/affecter-formateur/",
            {"formateur": self.formateur_t1.id, "promotion": self.promo_1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(FormateurPromotion.objects.filter(
            formateur=self.formateur_t1, promotion=self.promo_1
        ).exists())

    def test_admin_saas_ne_peut_pas_affecter_formateur(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/affecter-formateur/",
            {"formateur": self.formateur_t1.id, "promotion": self.promo_1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_ne_peut_pas_s_auto_affecter(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/affecter-formateur/",
            {"formateur": self.formateur_t1.id, "promotion": self.promo_1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_doublon_affectation_refuse(self):
        FormateurPromotion.objects.create(formateur=self.formateur_t1, promotion=self.promo_1)
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/affecter-formateur/",
            {"formateur": self.formateur_t1.id, "promotion": self.promo_1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_peut_etre_affecte_plusieurs_promotions(self):
        self.client.force_authenticate(user=self.admin_t1)
        res1 = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/affecter-formateur/",
            {"formateur": self.formateur_t1.id, "promotion": self.promo_1.id},
        )
        res2 = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_2.id}/affecter-formateur/",
            {"formateur": self.formateur_t1.id, "promotion": self.promo_2.id},
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            FormateurPromotion.objects.filter(formateur=self.formateur_t1).count(), 2
        )

    def test_promotion_peut_avoir_plusieurs_formateurs(self):
        self.client.force_authenticate(user=self.admin_t1)
        res1 = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/affecter-formateur/",
            {"formateur": self.formateur_t1.id, "promotion": self.promo_1.id},
        )
        res2 = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/affecter-formateur/",
            {"formateur": self.formateur2_t1.id, "promotion": self.promo_1.id},
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            FormateurPromotion.objects.filter(promotion=self.promo_1).count(), 2
        )

    def test_affecter_utilisateur_non_formateur_refuse(self):
        """Un apprenant ne peut pas être affecté comme formateur."""
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/affecter-formateur/",
            {"formateur": self.apprenant_t1.id, "promotion": self.promo_1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Retrait ───────────────────────────────────────────────────────────────

    def test_admin_organisme_peut_retirer_formateur(self):
        FormateurPromotion.objects.create(formateur=self.formateur_t1, promotion=self.promo_1)
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/retirer-formateur/",
            {"formateur_id": self.formateur_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(FormateurPromotion.objects.filter(
            formateur=self.formateur_t1, promotion=self.promo_1
        ).exists())

    def test_admin_saas_ne_peut_pas_retirer_formateur(self):
        FormateurPromotion.objects.create(formateur=self.formateur_t1, promotion=self.promo_1)
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/retirer-formateur/",
            {"formateur_id": self.formateur_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_retirer_formateur_non_affecte_refuse(self):
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/retirer-formateur/",
            {"formateur_id": self.formateur_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Liste des formateurs ──────────────────────────────────────────────────

    def test_lister_formateurs_d_une_promotion(self):
        FormateurPromotion.objects.create(formateur=self.formateur_t1, promotion=self.promo_1)
        FormateurPromotion.objects.create(formateur=self.formateur2_t1, promotion=self.promo_1)
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.get(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_1.id}/formateurs/"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 2)


# ─── Accès Formateur aux promotions ───────────────────────────────────────────

class PromotionAccesFormateurTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation = Formation.objects.create(tenant=self.tenant_1, nom="Formation Accès")
        self.promo_affectee = Promotion.objects.create(
            formation=self.formation, nom="Promo Affectée", date_debut=date(2026, 9, 1)
        )
        self.promo_non_affectee = Promotion.objects.create(
            formation=self.formation, nom="Promo Non Affectée", date_debut=date(2026, 10, 1)
        )
        FormateurPromotion.objects.create(
            formateur=self.formateur_t1, promotion=self.promo_affectee
        )

    def test_formateur_voit_uniquement_ses_promotions(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/promotions/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [p["id"] for p in res.data]
        self.assertIn(self.promo_affectee.id, ids)
        self.assertNotIn(self.promo_non_affectee.id, ids)

    def test_formateur_ne_peut_pas_voir_promo_non_affectee(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.get(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_non_affectee.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_formateur_ne_peut_pas_creer_promotion(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/",
            {"formation": self.formation.id, "nom": "Promo Interdite", "date_debut": "2026-11-01"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_ne_peut_pas_modifier_promotion(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.patch(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_affectee.id}/",
            {"nom": "Modifiée"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_ne_peut_pas_supprimer_promotion(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.delete(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_affectee.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_acceder_promotions_metier(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/promotions/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_creer_promotion(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/",
            {"formation": self.formation.id, "nom": "Promo SaaS", "date_debut": "2026-11-01"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_organisme_voit_toutes_ses_promotions(self):
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/promotions/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [p["id"] for p in res.data]
        self.assertIn(self.promo_affectee.id, ids)
        self.assertIn(self.promo_non_affectee.id, ids)

    def test_formateur_ne_peut_pas_inscrire_apprenant(self):
        """Seul l'admin organisme peut inscrire un apprenant."""
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/"
            f"{self.promo_affectee.id}/inscrire-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_peut_lister_inscrits_de_sa_promotion(self):
        InscriptionPromotion.objects.create(
            promotion=self.promo_affectee, apprenant=self.apprenant_t1, actif=True
        )
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.get(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo_affectee.id}/inscriptions/"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_cross_tenant_formateur_bloque(self):
        formation_t2 = Formation.objects.create(tenant=self.tenant_2, nom="Forma T2")
        promo_t2 = Promotion.objects.create(
            formation=formation_t2, nom="Promo T2", date_debut=date(2026, 9, 1)
        )
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.get(
            f"/api/tenants/{self.tenant_2.id}/promotions/{promo_t2.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


# ─── Inscription promotion — Utilisateur.actif non bloquant ───────────────────

class InscriptionPromotionTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation = Formation.objects.create(tenant=self.tenant_1, nom="Formation Inscription")
        self.promo = Promotion.objects.create(
            formation=self.formation, nom="Promo Inscription", date_debut=date(2026, 9, 1)
        )

    def test_inscription_apprenant_compte_actif(self):
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo.id}/inscrire-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_inscription_apprenant_compte_inactif_autorisee(self):
        """
        Utilisateur.actif=False ne bloque PAS l'inscription si MembreTenant.actif=True.
        Règle métier validée.
        """
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo.id}/inscrire-apprenant/",
            {"apprenant_id": self.apprenant_inactif.id},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            InscriptionPromotion.objects.filter(
                promotion=self.promo, apprenant=self.apprenant_inactif, actif=True
            ).exists()
        )

    def test_inscription_double_promo_active_refusee(self):
        InscriptionPromotion.objects.create(
            promotion=self.promo, apprenant=self.apprenant_t1, actif=True
        )
        promo_2 = Promotion.objects.create(
            formation=self.formation, nom="Promo 2", date_debut=date(2026, 10, 1)
        )
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{promo_2.id}/inscrire-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inscription_non_apprenant_refuse(self):
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo.id}/inscrire-apprenant/",
            {"apprenant_id": self.formateur_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_desinscription_et_reinscription(self):
        inscription = InscriptionPromotion.objects.create(
            promotion=self.promo, apprenant=self.apprenant_t1, actif=True
        )
        self.client.force_authenticate(user=self.admin_t1)

        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{self.promo.id}/desinscrire-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        inscription.refresh_from_db()
        self.assertFalse(inscription.actif)

        promo_2 = Promotion.objects.create(
            formation=self.formation, nom="Promo 2", date_debut=date(2026, 11, 1)
        )
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/promotions/{promo_2.id}/inscrire-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)


# ─── Groupes ──────────────────────────────────────────────────────────────────

class GroupeTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation = Formation.objects.create(tenant=self.tenant_1, nom="Formation Groupe")
        self.promo_1 = Promotion.objects.create(
            formation=self.formation, nom="Promo G1", date_debut=date(2026, 9, 1)
        )
        self.promo_2 = Promotion.objects.create(
            formation=self.formation, nom="Promo G2", date_debut=date(2026, 10, 1)
        )

        # Formateur affecté à promo_1 uniquement
        FormateurPromotion.objects.create(formateur=self.formateur_t1, promotion=self.promo_1)

        # Inscription apprenants
        InscriptionPromotion.objects.create(
            promotion=self.promo_1, apprenant=self.apprenant_t1, actif=True
        )
        InscriptionPromotion.objects.create(
            promotion=self.promo_1, apprenant=self.apprenant_inactif, actif=True
        )

    # ── Création ──────────────────────────────────────────────────────────────

    def test_admin_organisme_ne_peut_pas_creer_groupe(self):
        # Les groupes sont gérés par les formateurs ; l'admin les consulte.
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/",
            {"promotion": self.promo_1.id, "nom": "Groupe Admin"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_peut_creer_groupe_dans_sa_promotion(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/",
            {"promotion": self.promo_1.id, "nom": "Groupe Formateur"},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_formateur_ne_peut_pas_creer_groupe_promo_non_affectee(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/",
            {"promotion": self.promo_2.id, "nom": "Groupe Interdit"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_creer_groupe(self):
        self.client.force_authenticate(user=self.admin_saas)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/",
            {"promotion": self.promo_1.id, "nom": "Groupe SaaS"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_apprenant_ne_peut_pas_creer_groupe(self):
        self.client.force_authenticate(user=self.apprenant_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/",
            {"promotion": self.promo_1.id, "nom": "Groupe App"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # ── Visibilité ────────────────────────────────────────────────────────────

    def test_formateur_voit_groupes_de_ses_promotions_seulement(self):
        g1 = Groupe.objects.create(promotion=self.promo_1, nom="Groupe Promo 1")
        g2 = Groupe.objects.create(promotion=self.promo_2, nom="Groupe Promo 2")
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/groupes/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [g["id"] for g in res.data]
        self.assertIn(g1.id, ids)
        self.assertNotIn(g2.id, ids)

    def test_formateur_ne_peut_pas_modifier_groupe_promo_non_affectee(self):
        groupe = Groupe.objects.create(promotion=self.promo_2, nom="Groupe Interdit")
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.patch(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/",
            {"nom": "Modifié"},
        )
        # Le formateur ne voit pas ce groupe (promo non affectée) → 404
        self.assertIn(res.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])

    def test_formateur_peut_modifier_groupe_sa_promotion(self):
        groupe = Groupe.objects.create(promotion=self.promo_1, nom="Groupe A")
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.patch(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/",
            {"nom": "Groupe A Renommé"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_formateur_peut_supprimer_groupe_sa_promotion(self):
        groupe = Groupe.objects.create(promotion=self.promo_1, nom="Groupe À Supprimer")
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.delete(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    # ── Membres ───────────────────────────────────────────────────────────────

    def test_ajouter_apprenant_compte_actif(self):
        groupe = Groupe.objects.create(promotion=self.promo_1, nom="Groupe Membre")
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/ajouter-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_ajouter_apprenant_compte_inactif_autorise(self):
        """
        Utilisateur.actif=False ne bloque PAS l'ajout si MembreTenant.actif=True
        et que l'apprenant est inscrit dans la promotion.
        """
        groupe = Groupe.objects.create(promotion=self.promo_1, nom="Groupe Inactif")
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/ajouter-apprenant/",
            {"apprenant_id": self.apprenant_inactif.id},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            GroupeMembre.objects.filter(groupe=groupe, apprenant=self.apprenant_inactif).exists()
        )

    def test_appartenance_multiple_groupes_meme_promotion(self):
        groupe_a = Groupe.objects.create(promotion=self.promo_1, nom="Groupe A")
        groupe_b = Groupe.objects.create(promotion=self.promo_1, nom="Groupe B")
        self.client.force_authenticate(user=self.formateur_t1)

        res_a = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_a.id}/ajouter-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        res_b = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_b.id}/ajouter-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res_a.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_b.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            GroupeMembre.objects.filter(apprenant=self.apprenant_t1).count(), 2
        )

    def test_doublon_membre_refuse(self):
        groupe = Groupe.objects.create(promotion=self.promo_1, nom="Groupe Doublon")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_t1)
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/ajouter-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apprenant_autre_promotion_refuse(self):
        """Un apprenant inscrit en promo_2 ne peut pas rejoindre un groupe de promo_1."""
        apprenant_p2 = Utilisateur.objects.create_user(
            email="apprenant_p2@test.com", password="pw", nom="P2", prenom="App", actif=True,
        )
        MembreTenant.objects.create(utilisateur=apprenant_p2, tenant=self.tenant_1,
                                    role=MembreTenant.Role.APPRENANT, actif=True)
        InscriptionPromotion.objects.create(
            promotion=self.promo_2, apprenant=apprenant_p2, actif=True
        )
        groupe = Groupe.objects.create(promotion=self.promo_1, nom="Groupe Promo 1")
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/ajouter-apprenant/",
            {"apprenant_id": apprenant_p2.id},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retirer_apprenant_groupe(self):
        groupe = Groupe.objects.create(promotion=self.promo_1, nom="Groupe Retrait")
        GroupeMembre.objects.create(groupe=groupe, apprenant=self.apprenant_t1)
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe.id}/retirer-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(
            GroupeMembre.objects.filter(groupe=groupe, apprenant=self.apprenant_t1).exists()
        )

    def test_isolation_tenant_groupe(self):
        formation_t2 = Formation.objects.create(tenant=self.tenant_2, nom="F T2")
        promo_t2 = Promotion.objects.create(
            formation=formation_t2, nom="P T2", date_debut=date(2026, 9, 1)
        )
        groupe_t2 = Groupe.objects.create(promotion=promo_t2, nom="Groupe T2")
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.get(
            f"/api/tenants/{self.tenant_1.id}/groupes/{groupe_t2.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


# ─── Modules & Compétences ────────────────────────────────────────────────────

class ModuleEtCompetenceTests(PedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.formation_t1 = Formation.objects.create(tenant=self.tenant_1, nom="Formation DevOps")
        self.formation_t2 = Formation.objects.create(tenant=self.tenant_2, nom="Formation Cloud")

    def test_admin_saas_ne_peut_pas_acceder_modules(self):
        """Admin SaaS n'a aucun droit CRUD sur les modules (opération métier organisme)."""
        self.client.force_authenticate(user=self.admin_saas)
        url = f"/api/tenants/{self.tenant_1.id}/modules/"

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.post(url, {
            "formation": self.formation_t1.id, "nom": "Module SaaS", "ordre": 1
        })
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_acceder_competences(self):
        """Admin SaaS n'a aucun droit CRUD sur les compétences."""
        module = Module.objects.create(
            formation=self.formation_t1, nom="Module Test", ordre=1
        )
        self.client.force_authenticate(user=self.admin_saas)
        url = f"/api/tenants/{self.tenant_1.id}/competences/"

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.post(url, {"module": module.id, "nom": "Comp SaaS", "ordre": 1})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_ne_peut_pas_creer_module(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/modules/",
            {"formation": self.formation_t1.id, "nom": "Module F", "ordre": 1},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_validation_parent_enfant_module_autre_tenant(self):
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/modules/",
            {"formation": self.formation_t2.id, "nom": "Module Pirate", "ordre": 1},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_contraintes_module(self):
        self.client.force_authenticate(user=self.admin_t1)
        url = f"/api/tenants/{self.tenant_1.id}/modules/"
        res = self.client.post(url, {"formation": self.formation_t1.id, "nom": "M1", "ordre": 0})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        res = self.client.post(url, {"formation": self.formation_t1.id, "nom": "M1", "ordre": 1})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        res = self.client.post(url, {"formation": self.formation_t1.id, "nom": "M1", "ordre": 2})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


# ─── Niveaux ──────────────────────────────────────────────────────────────────

class NiveauTests(PedagogieBaseTestCase):
    def test_crud_niveau_admin_tenant(self):
        self.client.force_authenticate(user=self.admin_t1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"
        res = self.client.post(url, {"nom": "Débutant", "ordre": 1})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        nid = res.data["id"]

        res = self.client.patch(
            f"/api/tenants/{self.tenant_1.id}/niveaux/{nid}/", {"nom": "Initiation"}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.delete(f"/api/tenants/{self.tenant_1.id}/niveaux/{nid}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_formateur_ne_peut_pas_creer_niveau(self):
        self.client.force_authenticate(user=self.formateur_t1)
        res = self.client.post(
            f"/api/tenants/{self.tenant_1.id}/niveaux/", {"nom": "Niveau F", "ordre": 1}
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_saas_ne_peut_pas_creer_niveau(self):
        """Admin SaaS n'a aucun droit CRUD sur les niveaux (opération métier organisme)."""
        self.client.force_authenticate(user=self.admin_saas)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"

        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.post(url, {"nom": "Niveau SaaS", "ordre": 1})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_unicite_nom_et_ordre_par_tenant(self):
        Niveau.objects.create(tenant=self.tenant_1, nom="Expert", ordre=1)
        self.client.force_authenticate(user=self.admin_t1)
        url = f"/api/tenants/{self.tenant_1.id}/niveaux/"
        res = self.client.post(url, {"nom": "Expert", "ordre": 2})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self.client.post(url, {"nom": "Avancé", "ordre": 1})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_isolation_tenant_niveau(self):
        n_t2 = Niveau.objects.create(tenant=self.tenant_2, nom="N T2", ordre=1)
        self.client.force_authenticate(user=self.admin_t1)
        res = self.client.get(f"/api/tenants/{self.tenant_1.id}/niveaux/{n_t2.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


# ═══ Règles métier validées (module pédagogie) ════════════════════════════════

class ReglesPedagogieBaseTestCase(PedagogieBaseTestCase):
    """
    Formation F1 : modules M1 (compétence C1 décrite au niveau N1) ;
    promotions P1 (formateur_t1, apprenant_t1) et P2 (vide).
    Formation F2 : promotion P3, module M2 — hors du périmètre du formateur.
    """

    def setUp(self):
        super().setUp()
        self.base = f"/api/tenants/{self.tenant_1.id}"
        self.f1 = Formation.objects.create(tenant=self.tenant_1, nom="F1")
        self.f2 = Formation.objects.create(tenant=self.tenant_1, nom="F2")
        self.p1 = Promotion.objects.create(formation=self.f1, nom="P1", date_debut=date(2026, 1, 1))
        self.p2 = Promotion.objects.create(formation=self.f1, nom="P2", date_debut=date(2026, 1, 1))
        self.p3 = Promotion.objects.create(formation=self.f2, nom="P3", date_debut=date(2026, 1, 1))
        self.m1 = Module.objects.create(formation=self.f1, nom="M1")
        self.m2 = Module.objects.create(formation=self.f2, nom="M2")
        self.c1 = Competence.objects.create(module=self.m1, nom="C1")
        self.c2 = Competence.objects.create(module=self.m2, nom="C2")
        self.n1 = Niveau.objects.create(tenant=self.tenant_1, nom="N1")
        self.cn1 = CompetenceNiveau.objects.create(competence=self.c1, niveau=self.n1)
        CompetenceNiveau.objects.create(competence=self.c2, niveau=self.n1)

        FormateurPromotion.objects.create(formateur=self.formateur_t1, promotion=self.p1)
        self.inscription = InscriptionPromotion.objects.create(promotion=self.p1, apprenant=self.apprenant_t1)
        self.groupe = Groupe.objects.create(promotion=self.p1, nom="G1")
        GroupeMembre.objects.create(groupe=self.groupe, apprenant=self.apprenant_t1)

    def deposer_livrable(self, apprenant, promotion):
        now = timezone.now()
        brief = Brief.objects.create(
            promotion=promotion, titre="B", description="d", consignes="c",
            date_debut=now, date_limite=now + timedelta(days=1),
        )
        assignation = Assignation.objects.create(brief=brief, apprenant=apprenant)
        return Livrable.objects.create(assignation=assignation, deposant=apprenant, titre="L")

    def ids(self, res):
        return sorted(x["id"] for x in res.data)


class InscriptionParOrganismeTests(ReglesPedagogieBaseTestCase):
    def test_tenant_renseigne_automatiquement(self):
        self.assertEqual(self.inscription.tenant_id, self.tenant_1.id)

    def test_inscription_active_dans_deux_organismes(self):
        MembreTenant.objects.create(utilisateur=self.apprenant_t1, tenant=self.tenant_2,
                                    role=MembreTenant.Role.APPRENANT)
        formation_t2 = Formation.objects.create(tenant=self.tenant_2, nom="F T2")
        promo_t2 = Promotion.objects.create(formation=formation_t2, nom="P T2", date_debut=date(2026, 1, 1))

        self.client.force_authenticate(self.admin_t2)
        res = self.client.post(
            f"/api/tenants/{self.tenant_2.id}/promotions/{promo_t2.id}/inscrire-apprenant/",
            {"apprenant_id": self.apprenant_t1.id},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(InscriptionPromotion.objects.filter(apprenant=self.apprenant_t1, actif=True).count(), 2)

    def test_deux_inscriptions_actives_meme_organisme_refusees_en_base(self):
        with self.assertRaises(IntegrityError):
            InscriptionPromotion.objects.create(promotion=self.p2, apprenant=self.apprenant_t1)

    def test_deuxieme_inscription_meme_organisme_refusee_par_api(self):
        self.client.force_authenticate(self.admin_t1)
        res = self.client.post(f"{self.base}/promotions/{self.p2.id}/inscrire-apprenant/",
                               {"apprenant_id": self.apprenant_t1.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class ClotureTests(ReglesPedagogieBaseTestCase):
    def cloturer(self, promotion=None):
        self.client.force_authenticate(self.admin_t1)
        return self.client.post(f"{self.base}/promotions/{(promotion or self.p1).id}/cloturer/")

    def rouvrir(self, promotion=None):
        self.client.force_authenticate(self.admin_t1)
        return self.client.post(f"{self.base}/promotions/{(promotion or self.p1).id}/rouvrir/")

    def test_cloture_ferme_les_inscriptions(self):
        res = self.cloturer()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["inscriptions_fermees"], 1)
        self.p1.refresh_from_db()
        self.inscription.refresh_from_db()
        self.assertFalse(self.p1.actif)
        self.assertFalse(self.inscription.actif)
        self.assertTrue(self.inscription.fermee_par_cloture)
        # Les groupes sont conservés
        self.assertTrue(GroupeMembre.objects.filter(groupe=self.groupe, actif=True).exists())

    def test_apres_cloture_inscription_possible_ailleurs(self):
        self.cloturer()
        res = self.client.post(f"{self.base}/promotions/{self.p2.id}/inscrire-apprenant/",
                               {"apprenant_id": self.apprenant_t1.id})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_cloture_deja_cloturee_400(self):
        self.cloturer()
        self.assertEqual(self.cloturer().status_code, status.HTTP_400_BAD_REQUEST)

    def test_formateur_ne_cloture_pas(self):
        self.client.force_authenticate(self.formateur_t1)
        res = self.client.post(f"{self.base}/promotions/{self.p1.id}/cloturer/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_actif_non_modifiable_par_patch(self):
        self.client.force_authenticate(self.admin_t1)
        self.client.patch(f"{self.base}/promotions/{self.p1.id}/", {"actif": False})
        self.p1.refresh_from_db()
        self.assertTrue(self.p1.actif)

    def test_promotion_cloturee_en_lecture_seule(self):
        self.cloturer()
        refus = [
            self.client.patch(f"{self.base}/promotions/{self.p1.id}/", {"nom": "X"}),
            self.client.post(f"{self.base}/promotions/{self.p1.id}/inscrire-apprenant/",
                             {"apprenant_id": self.apprenant_inactif.id}),
            self.client.post(f"{self.base}/promotions/{self.p1.id}/affecter-formateur/",
                             {"formateur": self.formateur2_t1.id}),
        ]
        self.client.force_authenticate(self.formateur_t1)
        refus += [
            self.client.post(f"{self.base}/groupes/", {"promotion": self.p1.id, "nom": "G2"}),
            self.client.patch(f"{self.base}/groupes/{self.groupe.id}/", {"nom": "X"}),
            self.client.post(f"{self.base}/groupes/{self.groupe.id}/retirer-apprenant/",
                             {"apprenant_id": self.apprenant_t1.id}),
        ]
        for res in refus:
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, res.data)

    def test_promotion_cloturee_reste_consultable(self):
        self.cloturer()
        self.client.force_authenticate(self.formateur_t1)
        self.assertEqual(self.client.get(f"{self.base}/groupes/{self.groupe.id}/").status_code, 200)
        self.client.force_authenticate(self.apprenant_t1)
        self.assertEqual(self.client.get(f"{self.base}/promotions/{self.p1.id}/").status_code, 200)
        self.assertEqual(self.ids(self.client.get(f"{self.base}/groupes/")), [self.groupe.id])

    def test_reouverture_reactive_les_inscriptions_fermees_par_cloture(self):
        self.cloturer()
        res = self.rouvrir()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["inscriptions_reactivees"], 1)
        self.inscription.refresh_from_db()
        self.assertTrue(self.inscription.actif)
        self.assertFalse(self.inscription.fermee_par_cloture)

    def test_reouverture_ne_reactive_pas_une_desinscription(self):
        self.client.force_authenticate(self.admin_t1)
        self.client.post(f"{self.base}/promotions/{self.p1.id}/desinscrire-apprenant/",
                         {"apprenant_id": self.apprenant_t1.id})
        self.cloturer()
        res = self.rouvrir()
        self.assertEqual(res.data["inscriptions_reactivees"], 0)
        self.inscription.refresh_from_db()
        self.assertFalse(self.inscription.actif)

    def test_reouverture_apprenant_inscrit_ailleurs(self):
        self.cloturer()
        InscriptionPromotion.objects.create(promotion=self.p2, apprenant=self.apprenant_t1)
        res = self.rouvrir()
        self.assertEqual(res.data["inscriptions_reactivees"], 0)
        self.assertEqual([a["apprenant"] for a in res.data["non_reactives"]], [self.apprenant_t1.id])
        self.inscription.refresh_from_db()
        self.assertFalse(self.inscription.actif)
        self.assertFalse(self.inscription.fermee_par_cloture)


class DesinscriptionTests(ReglesPedagogieBaseTestCase):
    def desinscrire(self):
        self.client.force_authenticate(self.admin_t1)
        return self.client.post(f"{self.base}/promotions/{self.p1.id}/desinscrire-apprenant/",
                                {"apprenant_id": self.apprenant_t1.id})

    def test_sans_livrable_retire_des_groupes(self):
        self.assertEqual(self.desinscrire().status_code, status.HTTP_200_OK)
        self.assertFalse(GroupeMembre.objects.filter(apprenant=self.apprenant_t1).exists())

    def test_avec_livrable_appartenance_inactive(self):
        self.deposer_livrable(self.apprenant_t1, self.p1)
        self.desinscrire()
        membre = GroupeMembre.objects.get(apprenant=self.apprenant_t1)
        self.assertFalse(membre.actif)

        self.client.force_authenticate(self.formateur_t1)
        groupe = self.client.get(f"{self.base}/groupes/{self.groupe.id}/").data
        self.assertEqual(groupe["nb_membres"], 0)
        self.assertEqual([m["actif"] for m in groupe["membres"]], [False])

    def test_reinscription_puis_reactivation_dans_le_groupe(self):
        self.deposer_livrable(self.apprenant_t1, self.p1)
        self.desinscrire()
        self.client.post(f"{self.base}/promotions/{self.p1.id}/inscrire-apprenant/",
                         {"apprenant_id": self.apprenant_t1.id})
        self.client.force_authenticate(self.formateur_t1)
        res = self.client.post(f"{self.base}/groupes/{self.groupe.id}/ajouter-apprenant/",
                               {"apprenant_id": self.apprenant_t1.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(GroupeMembre.objects.get(apprenant=self.apprenant_t1).actif)


class GroupeReglesTests(ReglesPedagogieBaseTestCase):
    def test_groupe_ne_change_pas_de_promotion(self):
        FormateurPromotion.objects.create(formateur=self.formateur_t1, promotion=self.p2)
        self.client.force_authenticate(self.formateur_t1)
        res = self.client.patch(f"{self.base}/groupes/{self.groupe.id}/", {"promotion": self.p2.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.groupe.refresh_from_db()
        self.assertEqual(self.groupe.promotion_id, self.p1.id)

    def test_admin_consulte_mais_ne_modifie_pas(self):
        self.client.force_authenticate(self.admin_t1)
        self.assertEqual(self.client.get(f"{self.base}/groupes/").status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.patch(f"{self.base}/groupes/{self.groupe.id}/", {"nom": "X"}).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(f"{self.base}/groupes/{self.groupe.id}/ajouter-apprenant/",
                             {"apprenant_id": self.apprenant_inactif.id}).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_ajout_refuse_dans_un_groupe_desactive(self):
        self.groupe.actif = False
        self.groupe.save()
        self.client.force_authenticate(self.formateur_t1)
        res = self.client.post(f"{self.base}/groupes/{self.groupe.id}/ajouter-apprenant/",
                               {"apprenant_id": self.apprenant_inactif.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("désactivé", str(res.data["detail"]))

    def test_apprenant_voit_ses_groupes_et_leurs_membres(self):
        Groupe.objects.create(promotion=self.p1, nom="Autre groupe")
        self.client.force_authenticate(self.apprenant_t1)
        res = self.client.get(f"{self.base}/groupes/")
        self.assertEqual(self.ids(res), [self.groupe.id])
        self.assertEqual(res.data[0]["membres"][0]["apprenant"], self.apprenant_t1.id)

    def test_apprenant_ne_modifie_pas_un_groupe(self):
        self.client.force_authenticate(self.apprenant_t1)
        res = self.client.patch(f"{self.base}/groupes/{self.groupe.id}/", {"nom": "X"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_suppression_groupe_avec_membres_refusee(self):
        self.client.force_authenticate(self.formateur_t1)
        res = self.client.delete(f"{self.base}/groupes/{self.groupe.id}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_suppression_groupe_vide(self):
        vide = Groupe.objects.create(promotion=self.p1, nom="Vide")
        self.client.force_authenticate(self.formateur_t1)
        res = self.client.delete(f"{self.base}/groupes/{vide.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)


class SuppressionTests(ReglesPedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin_t1)

    def supprimer(self, ressource, objet):
        return self.client.delete(f"{self.base}/{ressource}/{objet.id}/")

    def test_elements_lies_non_supprimables(self):
        for ressource, objet in [
            ("formations", self.f1),
            ("promotions", self.p1),
            ("modules", self.m1),
            ("competences", self.c1),
            ("niveaux", self.n1),
        ]:
            res = self.supprimer(ressource, objet)
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, ressource)
            self.assertIn("Désactivez", str(res.data["detail"]))

    def test_elements_vides_supprimables(self):
        formation = Formation.objects.create(tenant=self.tenant_1, nom="Vide")
        module = Module.objects.create(formation=self.f1, nom="Vide", ordre=9)
        niveau = Niveau.objects.create(tenant=self.tenant_1, nom="Vide", ordre=9)
        for ressource, objet in [
            ("formations", formation), ("promotions", self.p2),
            ("modules", module), ("niveaux", niveau),
        ]:
            self.assertEqual(self.supprimer(ressource, objet).status_code,
                             status.HTTP_204_NO_CONTENT, ressource)


class LectureReferentielTests(ReglesPedagogieBaseTestCase):
    def get(self, ressource):
        return self.client.get(f"{self.base}/{ressource}/")

    def test_formateur_lit_les_formations_de_ses_promotions(self):
        self.client.force_authenticate(self.formateur_t1)
        self.assertEqual(self.ids(self.get("formations")), [self.f1.id])
        self.assertEqual(self.ids(self.get("modules")), [self.m1.id])
        self.assertEqual(self.ids(self.get("competences")), [self.c1.id])
        self.assertEqual(self.ids(self.get("competence-niveaux")), [self.cn1.id])
        self.assertEqual(self.ids(self.get("niveaux")), [self.n1.id])
        self.assertEqual(
            self.client.get(f"{self.base}/modules/{self.m2.id}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_formateur_ne_modifie_pas_le_referentiel(self):
        self.client.force_authenticate(self.formateur_t1)
        res = self.client.patch(f"{self.base}/modules/{self.m1.id}/", {"nom": "X"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_apprenant_lit_sa_promotion_et_sa_formation(self):
        self.client.force_authenticate(self.apprenant_t1)
        self.assertEqual(self.ids(self.get("promotions")), [self.p1.id])
        self.assertEqual(self.ids(self.get("formations")), [self.f1.id])
        self.assertEqual(self.ids(self.get("modules")), [self.m1.id])
        self.assertEqual(self.ids(self.get("competences")), [self.c1.id])
        self.assertEqual(self.ids(self.get("competence-niveaux")), [self.cn1.id])

    def test_apprenant_ne_voit_pas_inscrits_ni_formateurs(self):
        self.client.force_authenticate(self.apprenant_t1)
        for action in ("inscriptions", "formateurs"):
            res = self.client.get(f"{self.base}/promotions/{self.p1.id}/{action}/")
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, action)

    def test_filtre_non_numerique_400(self):
        self.client.force_authenticate(self.admin_t1)
        for url in ("promotions/?formation=abc", "modules/?formation=abc",
                    "competences/?module=abc", "groupes/?promotion=abc"):
            res = self.client.get(f"{self.base}/{url}")
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, url)


class CreationPromotionAvecFormateursTests(ReglesPedagogieBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin_t1)

    def creer(self, **extra):
        return self.client.post(f"{self.base}/promotions/", {
            "formation": self.f1.id, "nom": "P4", "date_debut": "2026-09-01", **extra,
        }, format="json")

    def test_creation_sans_formateur(self):
        res = self.creer()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertFalse(FormateurPromotion.objects.filter(promotion_id=res.data["id"]).exists())

    def test_creation_avec_plusieurs_formateurs(self):
        res = self.creer(formateurs=[self.formateur_t1.id, self.formateur2_t1.id])
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertCountEqual(
            FormateurPromotion.objects.filter(promotion_id=res.data["id"]).values_list("formateur_id", flat=True),
            [self.formateur_t1.id, self.formateur2_t1.id],
        )

    def test_formateur_invalide_refuse_et_rien_n_est_cree(self):
        res = self.creer(formateurs=[self.apprenant_t1.id])
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Promotion.objects.filter(nom="P4").exists())

    def test_formateurs_non_modifiables_par_patch(self):
        res = self.client.patch(f"{self.base}/promotions/{self.p2.id}/",
                                {"formateurs": [self.formateur_t1.id]}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
