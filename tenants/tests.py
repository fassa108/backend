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

import hashlib
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import AccountActivationToken, MembreTenant, Utilisateur
from notifications.models import Notification
from pedagogie.models import Formation, Promotion
from .models import DemandeInscription, Paiement, Tenant
from . import paydunya
from .paydunya import PayDunyaIndisponible
from .tasks import envoyer_email_lien_paiement, envoyer_email_nouvel_organisme, envoyer_email_paiement_confirme


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


# ─── Inscriptions d'organismes (dépôt + paiement PayDunya) ────────────────────

def facture_payee(status="completed", montant=25000, jeton="test_jeton"):
    """Réponse de PayDunya à la confirmation d'une facture."""
    return {
        "response_code": "00",
        "status": status,
        "invoice": {"token": jeton, "total_amount": str(montant)},
        "customer": {"name": "Fatou Ba", "phone": "+221780001122"},
        "receipt_url": f"https://paydunya.com/receipt/{jeton}",
    }


@override_settings(PRIX_ABONNEMENT_FCFA=25000)
@patch("tenants.views.envoyer_email_lien_paiement.delay")
@patch("tenants.services.envoyer_email_paiement_confirme.delay")
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

    def initier(self, demande, jeton="test_jeton"):
        """POST paiement/ : facture PayDunya simulée."""
        url = f"https://app.paydunya.com/sandbox-checkout/invoice/{jeton}"
        with patch("tenants.services.paydunya.creer_facture", return_value=(jeton, url)) as creer:
            reponse = self.client.post(self.url_paiement(demande))
        self.creer_facture = creer
        return reponse

    def confirmer(self, demande, jeton="test_jeton", facture=None):
        """Retour de PayDunya : la facture est relue (simulée) chez PayDunya."""
        with patch("tenants.services.paydunya.confirmer", return_value=facture or facture_payee()) as lire, \
                self.captureOnCommitCallbacks(execute=True):
            reponse = self.client.post(f"{self.url_paiement(demande)}confirmer/", {"token": jeton})
        self.lire_facture = lire
        return reponse

    def payer(self, demande, jeton="test_jeton", facture=None):
        self.initier(demande, jeton)
        return self.confirmer(demande, jeton, facture)

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
            "statut": "EN_ATTENTE_PAIEMENT", "montant": 25000, "mode_test": True, "paiement": None,
        })

    def test_reference_inconnue(self, *mocks):
        r = self.client.get(f"{self.url_demandes}paiement/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    # Paiement PayDunya

    @override_settings(FRONTEND_URL="https://eduhub.test", BACKEND_URL="https://api.eduhub.test")
    def test_initier_cree_la_facture_et_renvoie_la_page_paydunya(self, *mocks):
        d = self.demande(telephone="+221 77 000 00 00")
        r = self.initier(d)

        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data["url"], "https://app.paydunya.com/sandbox-checkout/invoice/test_jeton")
        paiement = Paiement.objects.get()
        self.assertEqual((paiement.statut, paiement.reference_transaction, paiement.montant), ("EN_ATTENTE", "test_jeton", 25000))
        facture = self.creer_facture.call_args.kwargs
        self.assertEqual(facture["montant"], 25000)
        self.assertEqual(facture["return_url"], f"https://eduhub.test/inscription/paiement/{d.reference}/retour")
        self.assertEqual(facture["cancel_url"], f"https://eduhub.test/inscription/paiement/{d.reference}?annule=1")
        self.assertEqual(facture["callback_url"], "https://api.eduhub.test/api/demandes-inscription/paydunya/ipn/")
        self.assertEqual(facture["client"], {"name": "Fatou Ba", "email": "fatou.ba@gamma.test", "phone": "+221 77 000 00 00"})
        # Rien n'est créé tant que PayDunya n'a pas confirmé
        d.refresh_from_db()
        self.assertEqual(d.statut, DemandeInscription.Statut.EN_ATTENTE_PAIEMENT)

    def test_paiement_cree_organisme_et_invite_le_responsable(self, activation, cree, nouvel, *emails):
        d = self.demande(telephone="+221 33 800 00 00")
        r = self.payer(d)

        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual((r.data["statut"], r.data["moyen_libelle"]), ("REUSSI", "PayDunya"))
        self.lire_facture.assert_called_once_with("test_jeton")
        d.refresh_from_db()
        self.assertEqual(d.statut, DemandeInscription.Statut.PAYEE)
        paiement = d.paiement_reussi
        self.assertEqual((paiement.telephone, paiement.url_recu), ("+221780001122", "https://paydunya.com/receipt/test_jeton"))
        self.assertIsNotNone(paiement.date_confirmation)
        self.assertEqual((d.tenant.nom, d.tenant.telephone), ("Organisme Gamma", "+221 33 800 00 00"))
        membre = MembreTenant.objects.get(tenant=d.tenant)
        self.assertEqual(membre.role, MembreTenant.Role.ADMINISTRATEUR)
        self.assertEqual((membre.utilisateur.prenom, membre.utilisateur.nom), ("Fatou", "Ba"))
        self.assertFalse(membre.utilisateur.actif)
        activation.assert_called_once()
        cree.assert_not_called()
        nouvel.assert_called_once_with(["saas@test.com"], "Organisme Gamma", "Fatou Ba", 25000)
        self.assertEqual(self.client.get(self.url_paiement(d)).data["statut"], "PAYEE")

    def test_paiement_notifie_le_responsable_et_l_admin_saas(self, *mocks):
        d = self.demande()
        self.payer(d)
        d.refresh_from_db()
        bienvenue = Notification.objects.get(utilisateur__email="fatou.ba@gamma.test")
        self.assertEqual((bienvenue.tenant, bienvenue.lien), (d.tenant, "/dashboard/admin"))
        saas = Notification.objects.get(utilisateur=self.admin_saas)
        self.assertEqual(saas.titre, "Nouvel organisme : Organisme Gamma")
        self.assertIsNone(saas.tenant)
        self.assertEqual(saas.lien, f"/admin/organismes/{d.tenant.id}")

    def test_paiement_avec_compte_existant(self, activation, cree, nouvel, *emails):
        d = self.demande(email="admin.beta@test.com")
        self.assertEqual(self.payer(d).status_code, status.HTTP_200_OK)
        d.refresh_from_db()
        self.assertTrue(MembreTenant.objects.filter(
            tenant=d.tenant, utilisateur=self.admin_autre, role=MembreTenant.Role.ADMINISTRATEUR,
        ).exists())
        activation.assert_not_called()
        cree.assert_called_once_with("admin.beta@test.com", "Organisme Gamma")

    def test_paiement_avec_compte_jamais_active(self, activation, cree, nouvel, *emails):
        Utilisateur.objects.create_user(email="inactif@test.com", nom="Sy", prenom="Ali")
        d = self.demande(email="inactif@test.com")
        self.assertEqual(self.payer(d).status_code, status.HTTP_200_OK)
        activation.assert_called_once()
        cree.assert_not_called()

    def test_paiement_annule_puis_nouvelle_tentative(self, *mocks):
        d = self.demande()
        r = self.payer(d, facture=facture_payee(status="cancelled"))
        self.assertEqual(r.data["statut"], "ANNULE")
        d.refresh_from_db()
        self.assertEqual(d.statut, DemandeInscription.Statut.EN_ATTENTE_PAIEMENT)
        self.assertFalse(Tenant.objects.filter(nom="Organisme Gamma").exists())

        self.assertEqual(self.payer(d, jeton="test_jeton_2").data["statut"], "REUSSI")
        self.assertEqual(list(d.paiements.values_list("statut", flat=True)), ["REUSSI", "ANNULE"])

    def test_paiement_encore_en_attente(self, *mocks):
        d = self.demande()
        r = self.payer(d, facture=facture_payee(status="pending"))
        self.assertEqual(r.data["statut"], "EN_ATTENTE")
        self.assertFalse(Tenant.objects.filter(nom="Organisme Gamma").exists())

    def test_montant_different_refuse(self, *mocks):
        d = self.demande()
        r = self.payer(d, facture=facture_payee(montant=100))
        self.assertEqual(r.data["statut"], "ECHEC")
        self.assertFalse(Tenant.objects.filter(nom="Organisme Gamma").exists())

    def test_confirmation_en_double_un_seul_organisme(self, *mocks):
        d = self.demande()
        self.payer(d)
        r = self.confirmer(d)
        self.assertEqual(r.data["statut"], "REUSSI")
        self.lire_facture.assert_not_called()  # déjà confirmé : PayDunya n'est pas relu
        self.assertEqual(Tenant.objects.filter(nom="Organisme Gamma").count(), 1)

    def test_jeton_d_une_autre_inscription(self, *mocks):
        d = self.demande()
        self.initier(d)
        autre = self.demande(nom_organisme="Organisme Delta")
        self.assertEqual(self.confirmer(autre).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.post(f"{self.url_paiement(d)}confirmer/", {}).status_code, status.HTTP_400_BAD_REQUEST)

    def test_paydunya_indisponible(self, *mocks):
        d = self.demande()
        with patch("tenants.services.paydunya.creer_facture", side_effect=PayDunyaIndisponible("panne")):
            r = self.client.post(self.url_paiement(d))
        self.assertEqual(r.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("indisponible", r.data["detail"])
        self.assertFalse(Paiement.objects.exists())

    def test_paiement_une_seule_fois(self, *mocks):
        d = self.demande()
        self.payer(d)
        r = self.initier(d, jeton="test_jeton_2")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Paiement.objects.count(), 1)

    def test_paiement_refuse_si_nom_pris_entre_temps(self, *mocks):
        d = self.demande(nom_organisme="ORGANISME ALPHA")
        r = self.initier(d)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Paiement.objects.exists())

    def test_paiement_limite_en_debit(self, *mocks):
        d = self.demande()
        for i in range(10):
            self.initier(d, jeton=f"test_jeton_{i}")
        self.assertEqual(self.initier(d, jeton="test_jeton_x").status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    # Paiement interrompu : lien par email, page rouverte, reçu

    def test_depot_envoie_le_lien_de_paiement(self, *mocks):
        with patch("tenants.views.envoyer_email_lien_paiement.delay") as lien, \
                self.captureOnCommitCallbacks(execute=True):
            self.deposer()
        d = DemandeInscription.objects.get()
        lien.assert_called_once_with("fatou.ba@gamma.test", "Fatou", "Organisme Gamma", str(d.reference), 25000)

    def test_recu_envoye_au_responsable(self, *mocks):
        d = self.demande()
        with patch("tenants.services.envoyer_email_paiement_confirme.delay") as recu:
            self.payer(d)
        recu.assert_called_once_with(
            "fatou.ba@gamma.test", "Organisme Gamma", 25000, "test_jeton",
            "https://paydunya.com/receipt/test_jeton", str(d.reference),
        )

    def test_page_rouverte_rattrape_un_paiement_interrompu(self, *mocks):
        # Payé chez PayDunya, mais la page a été fermée avant le retour sur EduHub
        d = self.demande()
        self.initier(d)
        with patch("tenants.services.paydunya.confirmer", return_value=facture_payee()), \
                self.captureOnCommitCallbacks(execute=True):
            r = self.client.get(self.url_paiement(d))
        self.assertEqual(r.data["statut"], "PAYEE")
        self.assertEqual(r.data["paiement"]["reference_transaction"], "test_jeton")
        self.assertTrue(Tenant.objects.filter(nom="Organisme Gamma").exists())

    def test_page_rouverte_paydunya_injoignable(self, *mocks):
        d = self.demande()
        self.initier(d)
        with patch("tenants.services.paydunya.confirmer", side_effect=PayDunyaIndisponible("panne")):
            r = self.client.get(self.url_paiement(d))
        self.assertEqual((r.status_code, r.data["statut"]), (status.HTTP_200_OK, "EN_ATTENTE_PAIEMENT"))

    def test_tentative_expiree_non_relue(self, *mocks):
        d = self.demande()
        self.initier(d)
        Paiement.objects.update(date_paiement=timezone.now() - timedelta(hours=25))
        with patch("tenants.services.paydunya.confirmer") as lire:
            self.client.get(self.url_paiement(d))
        lire.assert_not_called()

    def test_tentatives_abandonnees_annulees_apres_le_paiement(self, *mocks):
        d = self.demande()
        self.initier(d, jeton="test_abandon")
        self.payer(d, jeton="test_paye")
        self.assertEqual(
            dict(d.paiements.values_list("reference_transaction", "statut")),
            {"test_abandon": "ANNULE", "test_paye": "REUSSI"},
        )

    # Callback de PayDunya (production)

    @override_settings(PAYDUNYA_MASTER_KEY="cle-principale")
    def test_callback_authentifie_confirme_le_paiement(self, *mocks):
        d = self.demande()
        self.initier(d)
        url = f"{self.url_demandes}paydunya/ipn/"
        with patch("tenants.services.paydunya.confirmer", return_value=facture_payee()), \
                self.captureOnCommitCallbacks(execute=True):
            refus = self.client.post(url, {"data[hash]": "faux", "data[invoice][token]": "test_jeton"})
            r = self.client.post(url, {
                "data[hash]": hashlib.sha512(b"cle-principale").hexdigest(),
                "data[invoice][token]": "test_jeton",
                "data[status]": "completed",
            })
        self.assertEqual(refus.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        d.refresh_from_db()
        self.assertEqual(d.statut, DemandeInscription.Statut.PAYEE)

    # Historique réservé à l'admin SaaS

    def test_historique_reserve_admin_saas(self, *mocks):
        self.payer(self.demande())
        self.assertEqual(self.client.get(self.url_demandes).status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url_demandes).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.admin_saas)
        r = self.client.get(self.url_demandes)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data[0]["paiement"]["moyen_libelle"], "PayDunya")
        self.assertEqual(r.data[0]["paiement"]["reference_transaction"], "test_jeton")

    def test_filtre_par_statut(self, *mocks):
        self.demande()
        self.payer(self.demande(nom_organisme="Organisme Delta"))
        self.client.force_authenticate(self.admin_saas)
        r = self.client.get(self.url_demandes, {"statut": "PAYEE"})
        self.assertEqual([d["nom_organisme"] for d in r.data], ["Organisme Delta"])


@override_settings(PAYDUNYA_MODE="test", PAYDUNYA_MASTER_KEY="m", PAYDUNYA_PRIVATE_KEY="p", PAYDUNYA_TOKEN="t")
@patch("tenants.paydunya.requests.request")
class ClientPayDunyaTests(APITestCase):
    def reponse(self, requete, code=200, **donnees):
        requete.return_value.status_code = code
        requete.return_value.json.return_value = donnees

    def test_creer_facture_en_sandbox(self, requete):
        self.reponse(requete, response_code="00", response_text="https://paydunya.com/checkout/t1", token="t1")
        jeton, url = paydunya.creer_facture(
            montant=25000, description="Abonnement", client={"name": "A"},
            return_url="r", cancel_url="c", callback_url="cb", custom_data={},
        )
        self.assertEqual((jeton, url), ("t1", "https://paydunya.com/checkout/t1"))
        methode, adresse = requete.call_args.args
        self.assertEqual((methode, adresse), ("POST", "https://app.paydunya.com/sandbox-api/v1/checkout-invoice/create"))
        entetes = requete.call_args.kwargs["headers"]
        self.assertEqual(
            (entetes["PAYDUNYA-MASTER-KEY"], entetes["PAYDUNYA-PRIVATE-KEY"], entetes["PAYDUNYA-TOKEN"]), ("m", "p", "t"),
        )
        self.assertEqual(requete.call_args.kwargs["json"]["invoice"]["total_amount"], 25000)

    @override_settings(PAYDUNYA_MODE="live")
    def test_confirmer_en_production(self, requete):
        self.reponse(requete, response_code="00", status="completed")
        self.assertEqual(paydunya.confirmer("t1")["status"], "completed")
        self.assertEqual(requete.call_args.args[1], "https://app.paydunya.com/api/v1/checkout-invoice/confirm/t1")

    def test_refus_de_paydunya(self, requete):
        self.reponse(requete, code=422, response_code="1001", response_text="Clés invalides")
        with self.assertRaisesMessage(PayDunyaIndisponible, "Clés invalides"):
            paydunya.confirmer("t1")

    @override_settings(PAYDUNYA_MASTER_KEY="")
    def test_cles_absentes(self, requete):
        with self.assertRaises(PayDunyaIndisponible):
            paydunya.confirmer("t1")
        requete.assert_not_called()

    def test_hash(self, requete):
        self.assertTrue(paydunya.hash_valide(hashlib.sha512(b"m").hexdigest()))
        self.assertFalse(paydunya.hash_valide("abc"))
        self.assertFalse(paydunya.hash_valide(None))


class EmailsInscriptionTests(TenantsBaseTestCase):
    def test_email_admins_echappe_le_nom(self):
        envoyer_email_nouvel_organisme(["saas@test.com"], "<b>Gamma</b>", "Fatou Ba", 25000)
        self.assertEqual(mail.outbox[0].to, ["saas@test.com"])
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("&lt;b&gt;Gamma&lt;/b&gt;", html)
        self.assertIn("25000 FCFA", mail.outbox[0].body)


class EmailsPaiementTests(TenantsBaseTestCase):
    def test_lien_de_paiement(self):
        envoyer_email_lien_paiement("fatou@test.com", "Fatou", "Gamma", "abc", 25000)
        self.assertIn("/inscription/paiement/abc", mail.outbox[0].body)
        self.assertIn("25000 FCFA", mail.outbox[0].body)

    def test_paiement_confirme_avec_recu(self):
        envoyer_email_paiement_confirme("fatou@test.com", "Gamma", 25000, "test_x", "https://paydunya.com/r/x", "abc")
        self.assertEqual(mail.outbox[0].subject, "Paiement confirmé : abonnement EduHub de Gamma")
        self.assertIn("test_x", mail.outbox[0].body)
        self.assertIn("https://paydunya.com/r/x", mail.outbox[0].body)
