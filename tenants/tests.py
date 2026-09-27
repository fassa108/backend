"""
Tests du module tenants.

Couvre :
- Création d'un organisme avec son premier administrateur (Admin SaaS)
- Liste, fiche et indicateurs (Admin SaaS)
- Statut modifiable uniquement par l'Admin SaaS
- Blocage des organismes suspendus
- Suppression limitée aux organismes vides
- Génération du code
"""

from datetime import date
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import MembreTenant, Utilisateur
from pedagogie.models import Formation, Promotion
from .models import Tenant


class TenantsBaseTestCase(APITestCase):
    url = "/api/tenants/"

    def setUp(self):
        self.tenant = Tenant.objects.create(nom="Organisme Alpha", email="contact@alpha.test")
        self.autre_tenant = Tenant.objects.create(nom="Organisme Beta")

        self.admin_saas = self.creer_utilisateur("saas@test.com", est_admin_saas=True)
        self.admin = self.creer_membre("admin@test.com", MembreTenant.Role.ADMINISTRATEUR, self.tenant)
        self.apprenant = self.creer_membre("apprenant@test.com", MembreTenant.Role.APPRENANT, self.tenant)
        self.admin_autre = self.creer_membre("admin.beta@test.com", MembreTenant.Role.ADMINISTRATEUR, self.autre_tenant)

        formation = Formation.objects.create(tenant=self.tenant, nom="Dev Web")
        Promotion.objects.create(formation=formation, nom="P1", date_debut=date(2026, 1, 1))

    def creer_utilisateur(self, email, **extra):
        return Utilisateur.objects.create_user(
            email=email, password="pw", nom="Diop", prenom="Awa", actif=True, **extra,
        )

    def creer_membre(self, email, role, tenant):
        utilisateur = self.creer_utilisateur(email)
        MembreTenant.objects.create(utilisateur=utilisateur, tenant=tenant, role=role)
        return utilisateur

    def detail(self, tenant):
        return f"{self.url}{tenant.id}/"

    def suspendre(self, tenant):
        tenant.statut = False
        tenant.save()


# ─── Création ─────────────────────────────────────────────────────────────────

@patch("accounts.services.envoyer_email_activation.delay")
class CreationTenantTests(TenantsBaseTestCase):
    def test_creation_avec_nouvel_admin(self, delay):
        self.client.force_authenticate(self.admin_saas)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(self.url, {
                "nom": "Organisme Gamma",
                "admin_email": "Nouvel.Admin@Gamma.test",
                "admin_nom": "Ba",
                "admin_prenom": "Fatou",
            })

        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        tenant = Tenant.objects.get(nom="Organisme Gamma")
        membre = MembreTenant.objects.get(tenant=tenant)
        self.assertEqual(membre.role, MembreTenant.Role.ADMINISTRATEUR)
        self.assertEqual(membre.utilisateur.email, "nouvel.admin@gamma.test")
        self.assertFalse(membre.utilisateur.actif)
        delay.assert_called_once()
        self.assertEqual(r.data["administrateurs"][0]["email"], "nouvel.admin@gamma.test")

    def test_creation_avec_utilisateur_existant(self, delay):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {"nom": "Organisme Gamma", "admin_email": "ADMIN.BETA@test.com"})

        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        tenant = Tenant.objects.get(nom="Organisme Gamma")
        self.assertTrue(MembreTenant.objects.filter(
            tenant=tenant, utilisateur=self.admin_autre, role=MembreTenant.Role.ADMINISTRATEUR,
        ).exists())
        delay.assert_not_called()

    def test_nouvel_admin_sans_nom_refuse_et_rien_n_est_cree(self, delay):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {"nom": "Organisme Gamma", "admin_email": "x@gamma.test"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Tenant.objects.filter(nom="Organisme Gamma").exists())

    def test_admin_email_obligatoire(self, delay):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {"nom": "Organisme Gamma"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("admin_email", r.data)

    def test_admin_organisme_ne_cree_pas(self, delay):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url, {"nom": "Organisme Gamma", "admin_email": "admin@test.com"})
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)


class CodeTenantTests(TenantsBaseTestCase):
    def test_code_translittere(self):
        self.assertEqual(Tenant.objects.create(nom="École Été").code, "ECOLE-ETE")

    def test_code_unique(self):
        self.assertEqual(Tenant.objects.create(nom="Ecole  Ete").code, "ECOLE-ETE")
        self.assertEqual(Tenant.objects.create(nom="École-Été").code, "ECOLE-ETE-1")


# ─── Consultation et indicateurs ──────────────────────────────────────────────

class ConsultationTenantTests(TenantsBaseTestCase):
    def test_admin_saas_liste_avec_indicateurs(self):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)

        alpha = next(t for t in r.data if t["id"] == self.tenant.id)
        self.assertEqual(alpha["indicateurs"], {
            "nb_administrateurs": 1, "nb_formateurs": 0, "nb_apprenants": 1,
            "nb_formations": 1, "nb_promotions": 1,
        })
        self.assertEqual(alpha["administrateurs"], [{
            "nom": "Diop", "prenom": "Awa", "email": "admin@test.com",
            "compte_active": True, "actif": True,
        }])

    def test_admin_organisme_ne_liste_pas(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_organisme_voit_sa_fiche_uniquement(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.detail(self.tenant)).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(self.detail(self.autre_tenant)).status_code, status.HTTP_403_FORBIDDEN)

    def test_apprenant_ne_voit_pas_la_fiche(self):
        self.client.force_authenticate(self.apprenant)
        self.assertEqual(self.client.get(self.detail(self.tenant)).status_code, status.HTTP_403_FORBIDDEN)

    def test_indicateurs_globaux(self):
        self.suspendre(self.autre_tenant)
        self.client.force_authenticate(self.admin_saas)
        r = self.client.get(f"{self.url}indicateurs/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data, {
            "nb_organismes": 2, "nb_organismes_actifs": 1, "nb_organismes_suspendus": 1,
            "nb_utilisateurs": 3, "nb_formations": 1, "nb_promotions": 1,
        })

    def test_indicateurs_globaux_reserves_admin_saas(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(f"{self.url}indicateurs/").status_code, status.HTTP_403_FORBIDDEN)


# ─── Modification et statut ───────────────────────────────────────────────────

class ModificationTenantTests(TenantsBaseTestCase):
    def test_admin_saas_suspend_et_reactive(self):
        self.client.force_authenticate(self.admin_saas)

        r = self.client.patch(self.detail(self.tenant), {"statut": False})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertFalse(self.tenant.statut)

        r = self.client.patch(self.detail(self.tenant), {"statut": True})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.statut)

    def test_admin_saas_ne_modifie_que_le_statut(self):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.patch(self.detail(self.tenant), {"nom": "Piraté", "statut": True})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.nom, "Organisme Alpha")

    def test_admin_organisme_modifie_les_informations(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.detail(self.tenant), {"telephone": "+221 33 000 00 00"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.telephone, "+221 33 000 00 00")

    def test_admin_organisme_ne_modifie_pas_le_statut(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.detail(self.tenant), {"statut": False})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.statut)

    def test_admin_organisme_suspendu_ne_se_reactive_pas(self):
        self.suspendre(self.tenant)
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.detail(self.tenant), {"statut": True})
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(r.json()["code"], "organisme_suspendu")
        self.tenant.refresh_from_db()
        self.assertFalse(self.tenant.statut)


# ─── Organisme suspendu ───────────────────────────────────────────────────────

class OrganismeSuspenduTests(TenantsBaseTestCase):
    def test_routes_metier_bloquees(self):
        self.suspendre(self.tenant)
        self.client.force_authenticate(self.admin)
        for route in ("formations/", "promotions/", "briefs/", "livrables/"):
            r = self.client.get(f"/api/tenants/{self.tenant.id}/{route}")
            self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN, route)
            self.assertEqual(r.json()["code"], "organisme_suspendu", route)

    def test_fiche_bloquee_pour_admin_organisme(self):
        self.suspendre(self.tenant)
        self.client.force_authenticate(self.admin)
        r = self.client.get(self.detail(self.tenant))
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(r.json()["code"], "organisme_suspendu")

    def test_fiche_accessible_admin_saas(self):
        self.suspendre(self.tenant)
        self.client.force_authenticate(self.admin_saas)
        self.assertEqual(self.client.get(self.detail(self.tenant)).status_code, status.HTTP_200_OK)

    def test_autres_organismes_non_affectes(self):
        # Un utilisateur membre de deux organismes continue d'utiliser l'autre.
        MembreTenant.objects.create(
            utilisateur=self.admin, tenant=self.autre_tenant, role=MembreTenant.Role.ADMINISTRATEUR,
        )
        self.suspendre(self.tenant)
        self.client.force_authenticate(self.admin)
        r = self.client.get(f"/api/tenants/{self.autre_tenant.id}/formations/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)


# ─── Suppression ──────────────────────────────────────────────────────────────

class SuppressionTenantTests(TenantsBaseTestCase):
    def test_organisme_non_vide_non_supprimable(self):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.delete(self.detail(self.tenant))
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Tenant.objects.filter(pk=self.tenant.pk).exists())

    def test_organisme_avec_seulement_des_admins_supprimable(self):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.delete(self.detail(self.autre_tenant))
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Tenant.objects.filter(pk=self.autre_tenant.pk).exists())
        # Le compte utilisateur est conservé.
        self.assertTrue(Utilisateur.objects.filter(pk=self.admin_autre.pk).exists())

    def test_admin_organisme_ne_supprime_pas(self):
        self.client.force_authenticate(self.admin_autre)
        r = self.client.delete(self.detail(self.autre_tenant))
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
