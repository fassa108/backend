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

import uuid
from datetime import date
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import AccountActivationToken, MembreTenant, Utilisateur
from pedagogie.models import Formation, Promotion
from .models import DemandeInscription, Paiement, Tenant
from .serializers import PaiementSerializer
from .tasks import envoyer_email_nouvel_organisme


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

    def test_prenom_et_nom_manquants_signales_ensemble(self, delay):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {"nom": "Organisme Gamma", "admin_email": "x@gamma.test"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("admin_prenom", r.data)
        self.assertIn("admin_nom", r.data)

    def test_adresse_admin_saas_refusee(self, delay):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {"nom": "Organisme Gamma", "admin_email": "SAAS@test.com"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("admin_email", r.data)
        self.assertFalse(Tenant.objects.filter(nom="Organisme Gamma").exists())

    def test_compte_jamais_active_recoit_un_nouveau_lien(self, delay):
        inactif = Utilisateur.objects.create_user(email="inactif@test.com", nom="Sy", prenom="Ali")
        AccountActivationToken.objects.create(utilisateur=inactif, date_expiration=timezone.now())

        self.client.force_authenticate(self.admin_saas)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(self.url, {"nom": "Organisme Gamma", "admin_email": "inactif@test.com"})

        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        delay.assert_called_once()
        self.assertEqual(delay.call_args.args[0], "inactif@test.com")
        tokens = AccountActivationToken.objects.filter(utilisateur=inactif)
        self.assertEqual(tokens.count(), 2)
        self.assertEqual(tokens.filter(utilise=False).count(), 1)

    def test_site_web_sans_schema_complete(self, delay):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {
            "nom": "Organisme Gamma", "admin_email": "admin.beta@test.com", "site_web": " www.gamma.sn ",
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data["site_web"], "https://www.gamma.sn")

    def test_site_web_invalide(self, delay):
        self.client.force_authenticate(self.admin_saas)
        for valeur in ("pas une adresse", "ftp://gamma.sn", "gamma"):
            r = self.client.post(self.url, {
                "nom": "Organisme Gamma", "admin_email": "admin.beta@test.com", "site_web": valeur,
            })
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, valeur)
            self.assertIn("site_web", r.data)

    def test_telephone_verifie(self, delay):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {
            "nom": "Organisme Gamma", "admin_email": "admin.beta@test.com", "telephone": "abc123",
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("telephone", r.data)

    def test_nom_trop_court(self, delay):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {"nom": " G ", "admin_email": "admin.beta@test.com"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nom", r.data)


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

    def test_admin_organisme_modification_verifiee(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.detail(self.tenant), {"nom": "organisme beta", "telephone": "12"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nom", r.data)
        self.assertIn("telephone", r.data)

        r = self.client.patch(self.detail(self.tenant), {"nom": "Organisme Alpha Plus", "site_web": "alpha.sn"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.tenant.refresh_from_db()
        self.assertEqual((self.tenant.nom, self.tenant.site_web), ("Organisme Alpha Plus", "https://alpha.sn"))

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


# ─── Inscriptions d'organismes (dépôt + paiement simulé) ──────────────────────

@override_settings(PRIX_ABONNEMENT_FCFA=25000)
@patch("tenants.services.envoyer_email_nouvel_organisme.delay")
@patch("tenants.services.envoyer_email_organisme_cree.delay")
@patch("accounts.services.envoyer_email_activation.delay")
class InscriptionOrganismeTests(TenantsBaseTestCase):
    url_demandes = "/api/demandes-inscription/"

    def setUp(self):
        super().setUp()
        cache.clear()  # limitation de débit : compteurs remis à zéro

    def donnees(self, **extra):
        return {
            "nom_organisme": "Organisme Gamma",
            "responsable_prenom": "Fatou",
            "responsable_nom": "Ba",
            "email": "Fatou.Ba@Gamma.test",
            "telephone": "+221 77 123 45 67",
            "message": "Nous formons 40 apprenants par an.",
            **extra,
        }

    def deposer(self, **extra):
        return self.client.post(self.url_demandes, self.donnees(**extra))

    def demande(self, **extra):
        return DemandeInscription.objects.create(**{
            "nom_organisme": "Organisme Gamma",
            "responsable_prenom": "Fatou",
            "responsable_nom": "Ba",
            "email": "fatou.ba@gamma.test",
            **extra,
        })

    def url_paiement(self, demande):
        return f"{self.url_demandes}paiement/{demande.reference}/"

    def payer(self, demande, moyen="WAVE", telephone="77 123 45 67"):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url_paiement(demande), {"moyen": moyen, "telephone": telephone})

    # Dépôt public

    def test_depot_renvoie_seulement_la_reference(self, *mocks):
        r = self.deposer()

        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        d = DemandeInscription.objects.get()
        self.assertEqual(r.data, {"reference": d.reference, "montant": 25000})
        self.assertEqual(d.statut, DemandeInscription.Statut.EN_ATTENTE_PAIEMENT)
        self.assertEqual(d.email, "fatou.ba@gamma.test")
        self.assertFalse(Tenant.objects.filter(nom="Organisme Gamma").exists())

    def test_champs_obligatoires(self, *mocks):
        r = self.client.post(self.url_demandes, {})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        for champ in ("nom_organisme", "responsable_prenom", "responsable_nom", "email"):
            self.assertIn(champ, r.data)
        self.assertNotIn("telephone", r.data)

    def test_nom_deja_pris_par_un_organisme(self, *mocks):
        r = self.deposer(nom_organisme="organisme alpha")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("existe déjà", str(r.data["nom_organisme"]))

    def test_nom_trop_court(self, *mocks):
        r = self.deposer(nom_organisme=" A ")
        self.assertIn("nom_organisme", r.data)

    def test_adresse_admin_saas_refusee(self, *mocks):
        r = self.deposer(email="SAAS@test.com")
        self.assertIn("email", r.data)

    def test_demande_non_payee_ne_bloque_pas_une_nouvelle(self, *mocks):
        self.demande()
        self.assertEqual(self.deposer().status_code, status.HTTP_201_CREATED)

    def test_telephone_valide_ou_vide(self, *mocks):
        for i, numero in enumerate(("abc", "12 34", "+221 77 12a 45 67")):
            r = self.deposer(telephone=numero, nom_organisme=f"Orga {i}")
            self.assertIn("telephone", r.data, numero)
        self.assertEqual(self.deposer(telephone="").status_code, status.HTTP_201_CREATED)

    def test_message_limite_a_1000_caracteres(self, *mocks):
        self.assertIn("message", self.deposer(message="x" * 1001).data)

    def test_depot_limite_en_debit(self, *mocks):
        for i in range(5):
            self.deposer(nom_organisme=f"Orga {i}")
        r = self.deposer(nom_organisme="Orga 6")
        self.assertEqual(r.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    # Page de paiement

    def test_page_de_paiement(self, *mocks):
        d = self.demande()
        r = self.client.get(self.url_paiement(d))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data, {
            "nom_organisme": "Organisme Gamma", "email": "fatou.ba@gamma.test",
            "statut": "EN_ATTENTE_PAIEMENT", "montant": 25000,
        })

    def test_reference_inconnue(self, *mocks):
        r = self.client.get(f"{self.url_demandes}paiement/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_paiement_cree_organisme_et_invite_le_responsable(self, activation, cree, nouvel):
        d = self.demande(telephone="+221 33 800 00 00")
        r = self.payer(d, moyen="ORANGE_MONEY", telephone="+221 78 000 11 22")

        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(r.data["reference_transaction"].startswith("OM-"))
        d.refresh_from_db()
        self.assertEqual(d.statut, DemandeInscription.Statut.PAYEE)
        self.assertEqual((d.paiement.montant, d.paiement.moyen, d.paiement.telephone), (25000, "ORANGE_MONEY", "+221780001122"))
        self.assertEqual((d.tenant.nom, d.tenant.telephone), ("Organisme Gamma", "+221 33 800 00 00"))
        membre = MembreTenant.objects.get(tenant=d.tenant)
        self.assertEqual(membre.role, MembreTenant.Role.ADMINISTRATEUR)
        self.assertEqual((membre.utilisateur.prenom, membre.utilisateur.nom), ("Fatou", "Ba"))
        self.assertFalse(membre.utilisateur.actif)
        activation.assert_called_once()
        cree.assert_not_called()
        nouvel.assert_called_once_with(["saas@test.com"], "Organisme Gamma", "Fatou Ba", 25000)

    def test_paiement_avec_compte_existant(self, activation, cree, nouvel):
        d = self.demande(email="admin.beta@test.com")
        self.assertEqual(self.payer(d).status_code, status.HTTP_201_CREATED)
        d.refresh_from_db()
        self.assertTrue(MembreTenant.objects.filter(
            tenant=d.tenant, utilisateur=self.admin_autre, role=MembreTenant.Role.ADMINISTRATEUR,
        ).exists())
        activation.assert_not_called()
        cree.assert_called_once_with("admin.beta@test.com", "Organisme Gamma")

    def test_paiement_avec_compte_jamais_active(self, activation, cree, nouvel):
        Utilisateur.objects.create_user(email="inactif@test.com", nom="Sy", prenom="Ali")
        d = self.demande(email="inactif@test.com")
        self.assertEqual(self.payer(d).status_code, status.HTTP_201_CREATED)
        activation.assert_called_once()
        cree.assert_not_called()

    def test_numero_mobile_senegalais_obligatoire(self, *mocks):
        d = self.demande()
        for numero in ("", "33 800 00 00", "77 123 45", "+33 6 12 34 56 78"):
            r = self.payer(d, telephone=numero)
            self.assertIn("telephone", r.data, numero)
        for numero in ("771234567", "+221 76 123 45 67", "00221 70.123.45.67"):
            self.assertEqual(
                PaiementSerializer(data={"moyen": "WAVE", "telephone": numero}).is_valid(), True, numero,
            )
        self.assertFalse(Paiement.objects.exists())

    def test_moyen_de_paiement_inconnu(self, *mocks):
        r = self.payer(self.demande(), moyen="CARTE")
        self.assertIn("moyen", r.data)

    def test_paiement_une_seule_fois(self, *mocks):
        d = self.demande()
        self.assertEqual(self.payer(d).status_code, status.HTTP_201_CREATED)
        r = self.payer(d)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Paiement.objects.count(), 1)
        self.assertEqual(self.client.get(self.url_paiement(d)).data["statut"], "PAYEE")

    def test_paiement_refuse_si_nom_pris_entre_temps(self, *mocks):
        d = self.demande(nom_organisme="ORGANISME ALPHA")
        r = self.payer(d)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Paiement.objects.exists())
        d.refresh_from_db()
        self.assertEqual(d.statut, DemandeInscription.Statut.EN_ATTENTE_PAIEMENT)

    def test_paiement_limite_en_debit(self, *mocks):
        d = self.demande()
        for _ in range(10):
            self.payer(d, telephone="12")
        self.assertEqual(self.payer(d).status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    # Historique réservé à l'admin SaaS

    def test_historique_reserve_admin_saas(self, *mocks):
        self.payer(self.demande())
        self.assertEqual(self.client.get(self.url_demandes).status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url_demandes).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.admin_saas)
        r = self.client.get(self.url_demandes)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data[0]["paiement"]["moyen_libelle"], "Wave")

    def test_filtre_par_statut(self, *mocks):
        self.demande()
        self.payer(self.demande(nom_organisme="Organisme Delta"))
        self.client.force_authenticate(self.admin_saas)
        r = self.client.get(self.url_demandes, {"statut": "PAYEE"})
        self.assertEqual([d["nom_organisme"] for d in r.data], ["Organisme Delta"])


class EmailsInscriptionTests(TenantsBaseTestCase):
    def test_email_admins_echappe_le_nom(self):
        envoyer_email_nouvel_organisme(["saas@test.com"], "<b>Gamma</b>", "Fatou Ba", 25000)
        self.assertEqual(mail.outbox[0].to, ["saas@test.com"])
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("&lt;b&gt;Gamma&lt;/b&gt;", html)
        self.assertIn("25000 FCFA", mail.outbox[0].body)
