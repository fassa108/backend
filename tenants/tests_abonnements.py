"""
Tests des abonnements mensuels et des finances.

Couvre : calcul des échéances, création à l'organisme, rappels et
suspension pour impayé (tâche quotidienne), renouvellement via PayDunya
(simulé), réactivation, accès pendant la suspension, finances de l'admin SaaS.
"""

import io
from datetime import date, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework import status

from notifications.models import Notification

from .abonnements import abonnement_de, ajouter_un_mois, verifier_abonnements
from .models import Abonnement, Paiement, Tenant
from .tests import TenantsBaseTestCase, facture_payee


class EcheancesTests(TenantsBaseTestCase):
    def test_un_mois_de_plus(self):
        self.assertEqual(ajouter_un_mois(date(2027, 1, 31)), date(2027, 2, 28))
        self.assertEqual(ajouter_un_mois(date(2026, 12, 15)), date(2027, 1, 15))
        self.assertEqual(ajouter_un_mois(date(2028, 1, 30)), date(2028, 2, 29))

    def test_statut_selon_la_date(self):
        a = abonnement_de(self.tenant)
        a.date_fin = date(2026, 10, 10)
        self.assertEqual(a.statut_au(date(2026, 10, 10)), "ACTIF")
        self.assertEqual(a.statut_au(date(2026, 10, 17)), "EN_GRACE")
        self.assertEqual(a.statut_au(date(2026, 10, 18)), "EXPIRE")

    @patch("accounts.services.envoyer_email_activation.delay")
    def test_organisme_cree_avec_un_mois(self, *mocks):
        self.client.force_authenticate(self.admin_saas)
        r = self.client.post(self.url, {
            "nom": "Organisme Gamma", "admin_email": "g@test.com", "admin_prenom": "Ama", "admin_nom": "Sy",
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        a = Tenant.objects.get(nom="Organisme Gamma").abonnement
        self.assertEqual(a.date_fin, ajouter_un_mois(timezone.localdate()))


@patch("tenants.tasks.envoyer_email_abonnement.delay")
class VerificationQuotidienneTests(TenantsBaseTestCase):
    def setUp(self):
        super().setUp()
        self.abonnement = abonnement_de(self.tenant)
        self.aujourd_hui = timezone.localdate()

    def echeance_dans(self, jours):
        self.abonnement.date_fin = self.aujourd_hui + timedelta(days=jours)
        self.abonnement.save()

    def verifier(self):
        with self.captureOnCommitCallbacks(execute=True):
            return verifier_abonnements(self.aujourd_hui)

    def test_rappel_sept_jours_avant_une_seule_fois(self, email, *mocks):
        self.echeance_dans(7)
        self.verifier()
        self.verifier()
        rappel = Notification.objects.get(utilisateur=self.admin)
        self.assertEqual((rappel.titre, rappel.lien), ("Votre abonnement expire dans 7 jours", "/abonnement"))
        email.assert_called_once()
        self.assertEqual(email.call_args.args[0], ["admin@test.com"])
        self.assertFalse(Notification.objects.filter(utilisateur=self.apprenant).exists())

    def test_rien_hors_des_jalons(self, email, *mocks):
        self.echeance_dans(5)
        self.assertEqual(self.verifier(), {"rappels": 0, "suspendus": 0})

    def test_grace_puis_suspension_pour_impaye(self, email, *mocks):
        self.echeance_dans(-Abonnement.JOURS_GRACE)  # dernier jour de grâce
        self.assertEqual(self.verifier()["suspendus"], 0)

        self.echeance_dans(-Abonnement.JOURS_GRACE - 1)
        self.assertEqual(self.verifier()["suspendus"], 1)
        self.tenant.refresh_from_db()
        self.abonnement.refresh_from_db()
        self.assertFalse(self.tenant.statut)
        self.assertTrue(self.abonnement.suspendu_pour_impaye)
        self.assertTrue(Notification.objects.filter(utilisateur=self.admin_saas, titre__contains="impayé").exists())

        # Les routes de l'organisme répondent « abonnement expiré »
        self.client.force_authenticate(self.admin)
        r = self.client.get(f"/api/tenants/{self.tenant.id}/formations/")
        self.assertEqual((r.status_code, r.json()["code"]), (status.HTTP_403_FORBIDDEN, "abonnement_expire"))
        # Pas une deuxième fois
        self.assertEqual(self.verifier()["suspendus"], 0)

    def test_reactivation_par_l_admin_saas_sans_nouvelle_suspension(self, email, *mocks):
        self.echeance_dans(-30)
        self.verifier()
        self.client.force_authenticate(self.admin_saas)
        self.client.patch(self.detail(self.tenant), {"statut": True}, format="json")
        self.verifier()
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.statut)

    def test_organisme_suspendu_par_l_admin_saas_reste_un_organisme_suspendu(self, email, *mocks):
        self.suspendre(self.tenant)
        self.client.force_authenticate(self.admin)
        r = self.client.get(f"/api/tenants/{self.tenant.id}/formations/")
        self.assertEqual(r.json()["code"], "organisme_suspendu")


@override_settings(PRIX_ABONNEMENT_FCFA=25000)
@patch("tenants.services.envoyer_email_renouvellement_confirme.delay")
class RenouvellementTests(TenantsBaseTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.abonnement = abonnement_de(self.tenant)
        self.url_abonnement = f"/api/abonnements/{self.tenant.id}/"
        self.client.force_authenticate(self.admin)

    def renouveler(self, jeton="test_renouv"):
        url = f"https://app.paydunya.com/sandbox-checkout/invoice/{jeton}"
        with patch("tenants.services.paydunya.creer_facture", return_value=(jeton, url)) as creer:
            r = self.client.post(f"{self.url_abonnement}renouveler/")
        self.creer_facture = creer
        return r

    def confirmer(self, jeton="test_renouv", facture=None):
        with patch("tenants.services.paydunya.confirmer", return_value=facture or facture_payee(jeton=jeton)), \
                self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f"{self.url_abonnement}confirmer/", {"token": jeton})

    def test_page_mon_abonnement(self, *mocks):
        r = self.client.get(self.url_abonnement)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["organisme"], "Organisme Alpha")
        self.assertEqual(r.data["abonnement"]["statut"], "ACTIF")
        self.assertEqual(r.data["abonnement"]["montant_mensuel"], 25000)

    def test_reserve_aux_administrateurs_de_l_organisme(self, *mocks):
        for utilisateur in (self.apprenant, self.admin_autre):
            self.client.force_authenticate(utilisateur)
            self.assertEqual(self.client.get(self.url_abonnement).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.renouveler().status_code, status.HTTP_403_FORBIDDEN)
        # L'admin SaaS consulte, mais ne paie pas à la place de l'organisme
        self.client.force_authenticate(self.admin_saas)
        self.assertEqual(self.client.get(self.url_abonnement).status_code, status.HTTP_200_OK)
        self.assertEqual(self.renouveler().status_code, status.HTTP_403_FORBIDDEN)

    def test_renouvellement_en_avance_prolonge_depuis_l_echeance(self, recu):
        echeance = self.abonnement.date_fin
        r = self.renouveler()
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertIn("sandbox-checkout", r.data["url"])
        self.assertEqual(self.creer_facture.call_args.kwargs["montant"], 25000)
        self.assertTrue(self.creer_facture.call_args.kwargs["return_url"].endswith("/abonnement/retour"))

        r = self.confirmer()
        self.assertEqual(r.data["paiement"]["statut"], "REUSSI")
        self.abonnement.refresh_from_db()
        self.assertEqual(self.abonnement.date_fin, ajouter_un_mois(echeance))
        paiement = Paiement.objects.get(reference_transaction="test_renouv")
        self.assertEqual((paiement.type, paiement.payeur, paiement.periode_debut), ("RENOUVELLEMENT", self.admin, echeance))
        self.assertEqual(r.data["paiements"][0]["reference_transaction"], "test_renouv")
        recu.assert_called_once()
        self.assertTrue(Notification.objects.filter(utilisateur=self.admin, titre="Abonnement renouvelé").exists())

    def test_renouvellement_d_un_organisme_suspendu_le_reactive(self, *mocks):
        self.abonnement.date_fin = timezone.localdate() - timedelta(days=20)
        self.abonnement.suspendu_pour_impaye = True
        self.abonnement.save()
        self.suspendre(self.tenant)

        # Les routes d'abonnement restent accessibles pendant la suspension
        self.assertEqual(self.client.get(self.url_abonnement).data["abonnement"]["suspendu_pour_impaye"], True)
        self.renouveler()
        self.confirmer()
        self.tenant.refresh_from_db()
        self.abonnement.refresh_from_db()
        self.assertTrue(self.tenant.statut)
        self.assertFalse(self.abonnement.suspendu_pour_impaye)
        self.assertEqual(self.abonnement.date_fin, ajouter_un_mois(timezone.localdate()))

    def test_confirmation_en_double_un_seul_mois(self, *mocks):
        echeance = self.abonnement.date_fin
        self.renouveler()
        self.confirmer()
        self.confirmer()
        self.abonnement.refresh_from_db()
        self.assertEqual(self.abonnement.date_fin, ajouter_un_mois(echeance))

    def test_paiement_annule(self, *mocks):
        echeance = self.abonnement.date_fin
        self.renouveler()
        r = self.confirmer(facture=facture_payee(status="cancelled", jeton="test_renouv"))
        self.assertEqual(r.data["paiement"]["statut"], "ANNULE")
        self.abonnement.refresh_from_db()
        self.assertEqual(self.abonnement.date_fin, echeance)

    def test_page_rouverte_rattrape_un_renouvellement_interrompu(self, *mocks):
        echeance = self.abonnement.date_fin
        self.renouveler()
        with patch("tenants.services.paydunya.confirmer", return_value=facture_payee(jeton="test_renouv")), \
                self.captureOnCommitCallbacks(execute=True):
            r = self.client.get(self.url_abonnement)
        self.assertEqual(r.data["abonnement"]["date_fin"], ajouter_un_mois(echeance))

    def test_jeton_d_un_autre_organisme(self, *mocks):
        self.renouveler()
        self.client.force_authenticate(self.admin_autre)
        r = self.client.post(f"/api/abonnements/{self.autre_tenant.id}/confirmer/", {"token": "test_renouv"})
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)


class FinancesTests(TenantsBaseTestCase):
    url_finances = "/api/finances/"

    def paiement(self, montant, mode="test", statut="REUSSI", type_="RENOUVELLEMENT", jours=0, tenant=None):
        p = Paiement.objects.create(
            type=type_, tenant=tenant or self.tenant, mode=mode, statut=statut, montant=montant,
            reference_transaction=f"t{Paiement.objects.count()}",
        )
        quand = timezone.now() - timedelta(days=jours)
        Paiement.objects.filter(pk=p.pk).update(date_confirmation=quand, date_paiement=quand)
        return p

    def test_reserve_a_l_admin_saas(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url_finances).status_code, status.HTTP_403_FORBIDDEN)

    def test_tableau(self):
        self.paiement(25000)
        self.paiement(25000, type_="INSCRIPTION")
        self.paiement(25000, statut="ANNULE")
        self.paiement(99999, mode="live")  # production : pas comptée en mode test
        abonnement_de(self.autre_tenant)
        Abonnement.objects.filter(tenant=self.autre_tenant).update(date_fin=timezone.localdate() + timedelta(days=3))

        self.client.force_authenticate(self.admin_saas)
        d = self.client.get(self.url_finances, {"mode": "test"}).data
        self.assertEqual(d["indicateurs"]["ce_mois"], 50000)
        self.assertEqual(d["indicateurs"]["total"], 50000)
        self.assertEqual(d["indicateurs"]["montant_moyen"], 25000)
        self.assertEqual(d["repartition"]["ANNULE"], 1)
        self.assertEqual(len(d["par_mois"]), 12)
        self.assertEqual(d["par_mois"][-1]["renouvellements"], 25000)
        self.assertEqual([e["organisme"] for e in d["echeances"]], ["Organisme Beta"])
        self.assertEqual(len(d["transactions"]), 3)
        self.assertEqual(self.client.get(self.url_finances, {"mode": "live"}).data["indicateurs"]["total"], 99999)

    def test_filtres_et_export(self):
        self.paiement(25000)
        self.paiement(25000, statut="ANNULE")
        self.client.force_authenticate(self.admin_saas)
        d = self.client.get(self.url_finances, {"statut": "ANNULE"}).data
        self.assertEqual([t["statut"] for t in d["transactions"]], ["ANNULE"])
        r = self.client.get(f"{self.url_finances}export/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        classeur = load_workbook(io.BytesIO(r.content))
        lignes = list(classeur["Transactions"].iter_rows(values_only=True))
        self.assertIn(("Organisme Alpha", "Renouvellement", "Réussi", 25000), [l[1:5] for l in lignes[1:3]])
        # Le paiement annulé n'entre pas dans le total
        self.assertEqual(lignes[-1][4], 25000)
        self.assertIn("Synthèse", classeur.sheetnames)
        self.assertEqual(self.client.get(self.url_finances, {"mode": "autre"}).status_code, status.HTTP_400_BAD_REQUEST)
