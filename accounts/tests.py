"""
Tests du module accounts.

Couvre :
- Connexion (message unique, email insensible à la casse, statut des organismes)
- Normalisation et unicité des emails
- Activation et réinitialisation (règles de mot de passe, durée du lien)
- Gestion des membres (doublon, admin SaaS exclu, dernier admin,
  changement de rôle)
- Limitation de débit
- Gabarit des emails (logo, bouton, échappement)
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from config.emails import BLEU, envoyer, mise_en_page
from pedagogie.models import FormateurPromotion, Formation, InscriptionPromotion, Promotion
from tenants.models import Tenant
from .models import AccountActivationToken, MembreTenant, Utilisateur
from .tasks import envoyer_email_activation


MOT_DE_PASSE_SOLIDE = "Tr3s-Solide-Passe!"


class AccountsBaseTestCase(APITestCase):
    def setUp(self):
        # Les compteurs de limitation de débit sont stockés en cache.
        cache.clear()

        self.tenant = Tenant.objects.create(nom="Organisme Alpha")

        self.admin_saas = self.creer_utilisateur("saas@test.com", est_admin_saas=True)
        self.admin = self.creer_membre("admin@test.com", MembreTenant.Role.ADMINISTRATEUR)
        self.formateur = self.creer_membre("formateur@test.com", MembreTenant.Role.FORMATEUR)
        self.apprenant = self.creer_membre("apprenant@test.com", MembreTenant.Role.APPRENANT)

        self.url_membres = f"/api/accounts/tenants/{self.tenant.id}/membres/"

    def creer_utilisateur(self, email, actif=True, **extra):
        return Utilisateur.objects.create_user(
            email=email, password=MOT_DE_PASSE_SOLIDE,
            nom="Nom", prenom="Prenom", actif=actif, **extra,
        )

    def creer_membre(self, email, role, tenant=None, actif=True):
        utilisateur = self.creer_utilisateur(email)
        MembreTenant.objects.create(
            utilisateur=utilisateur, tenant=tenant or self.tenant,
            role=role, actif=actif,
        )
        return utilisateur

    def membre(self, utilisateur):
        return MembreTenant.objects.get(utilisateur=utilisateur, tenant=self.tenant)


# ─── Connexion ────────────────────────────────────────────────────────────────

class ConnexionTests(AccountsBaseTestCase):
    url = "/api/accounts/connexion/"

    def test_connexion_reussie_retourne_tenants_avec_statut(self):
        r = self.client.post(self.url, {"email": "admin@test.com", "password": MOT_DE_PASSE_SOLIDE})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("access", r.data)
        self.assertEqual(r.data["tenants"], [{
            "id": self.tenant.id, "nom": self.tenant.nom, "code": self.tenant.code,
            "role": "ADMINISTRATEUR", "statut": True, "actif": True,
        }])

    def test_connexion_retourne_les_acces_suspendus(self):
        membre = self.membre(self.formateur)
        membre.actif = False
        membre.save()
        r = self.client.post(self.url, {"email": "formateur@test.com", "password": MOT_DE_PASSE_SOLIDE})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data["tenants"]), 1)
        self.assertFalse(r.data["tenants"][0]["actif"])

    def test_connexion_organisme_suspendu_autorisee(self):
        self.tenant.statut = False
        self.tenant.save()
        r = self.client.post(self.url, {"email": "admin@test.com", "password": MOT_DE_PASSE_SOLIDE})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(r.data["tenants"][0]["statut"])

    def test_connexion_email_insensible_a_la_casse(self):
        r = self.client.post(self.url, {"email": "ADMIN@Test.com", "password": MOT_DE_PASSE_SOLIDE})
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_mauvais_mot_de_passe_et_compte_inactif_meme_message(self):
        self.creer_utilisateur("inactif@test.com", actif=False)

        r_faux = self.client.post(self.url, {"email": "admin@test.com", "password": "faux"})
        r_inactif = self.client.post(self.url, {"email": "inactif@test.com", "password": MOT_DE_PASSE_SOLIDE})
        r_inconnu = self.client.post(self.url, {"email": "inconnu@test.com", "password": "faux"})

        for r in (r_faux, r_inactif, r_inconnu):
            self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)
            self.assertEqual(str(r.data["detail"]), "Email ou mot de passe incorrect.")

    def test_limitation_de_debit(self):
        for _ in range(10):
            self.client.post(self.url, {"email": "admin@test.com", "password": "faux"})
        r = self.client.post(self.url, {"email": "admin@test.com", "password": "faux"})
        self.assertEqual(r.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


# ─── Emails ───────────────────────────────────────────────────────────────────

class EmailTests(AccountsBaseTestCase):
    def test_email_stocke_en_minuscules(self):
        u = Utilisateur.objects.create_user(email="Awa.Diop@Exemple.COM", nom="D", prenom="A")
        self.assertEqual(u.email, "awa.diop@exemple.com")

    def test_email_unique_insensible_a_la_casse(self):
        # Contourne la normalisation du manager pour tester la contrainte BDD.
        with self.assertRaises(IntegrityError):
            Utilisateur.objects.create(email="ADMIN@test.com", nom="N", prenom="P")


# ─── Activation ───────────────────────────────────────────────────────────────

class ActivationTests(AccountsBaseTestCase):
    url = "/api/accounts/activation/"

    def setUp(self):
        super().setUp()
        self.invite = Utilisateur.objects.create_user(email="invite@test.com", nom="Diop", prenom="Awa")
        self.token = AccountActivationToken.objects.create(
            utilisateur=self.invite,
            date_expiration=timezone.now() + timedelta(hours=2),
        )

    def activer(self, password):
        return self.client.post(self.url, {
            "token": str(self.token.token), "password": password, "password_confirm": password,
        })

    def test_mot_de_passe_trop_courant_refuse(self):
        r = self.activer("password")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", r.data)
        self.invite.refresh_from_db()
        self.assertFalse(self.invite.actif)

    def test_mot_de_passe_numerique_refuse(self):
        r = self.activer("48291736")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_activation_reussie(self):
        r = self.activer(MOT_DE_PASSE_SOLIDE)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.invite.refresh_from_db()
        self.assertTrue(self.invite.actif)


# ─── Réinitialisation du mot de passe ─────────────────────────────────────────

@patch("accounts.services.envoyer_email_reset_password.delay")
class ResetPasswordTests(AccountsBaseTestCase):
    url_demande = "/api/accounts/password-reset/"
    url_confirm = "/api/accounts/password-reset/confirm/"

    def test_lien_valable_2_heures(self, _delay):
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 2 * 60 * 60)

    def test_demande_email_insensible_a_la_casse(self, delay):
        r = self.client.post(self.url_demande, {"email": "Admin@TEST.com"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        delay.assert_called_once()
        self.assertEqual(delay.call_args.args[0], "admin@test.com")

    def test_demande_email_inconnu_meme_reponse(self, delay):
        r = self.client.post(self.url_demande, {"email": "inconnu@test.com"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        delay.assert_not_called()

    def _lien(self, delay):
        self.client.post(self.url_demande, {"email": "admin@test.com"})
        uid, token = delay.call_args.args[1].rstrip("/").split("/")[-2:]
        return uid, token

    def test_confirmation_mot_de_passe_faible_refuse(self, delay):
        uid, token = self._lien(delay)
        r = self.client.post(self.url_confirm, {
            "uid": uid, "token": token, "password": "password", "password_confirm": "password",
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", r.data)

    def test_confirmation_reussie(self, delay):
        uid, token = self._lien(delay)
        nouveau = "Nouveau-Passe-2026!"
        r = self.client.post(self.url_confirm, {
            "uid": uid, "token": token, "password": nouveau, "password_confirm": nouveau,
        })
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(nouveau))


# ─── Gestion des membres ──────────────────────────────────────────────────────

class AjoutMembreTests(AccountsBaseTestCase):
    def test_admin_saas_ne_gere_pas_les_membres(self):
        self.client.force_authenticate(self.admin_saas)
        self.assertEqual(self.client.get(self.url_membres).status_code, status.HTTP_403_FORBIDDEN)
        r = self.client.post(self.url_membres, {"email": "x@test.com", "nom": "N", "prenom": "P", "role": "FORMATEUR"})
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_formateur_ne_gere_pas_les_membres(self):
        self.client.force_authenticate(self.formateur)
        self.assertEqual(self.client.get(self.url_membres).status_code, status.HTTP_403_FORBIDDEN)

    def test_ajout_membre_deja_present_400(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url_membres, {"email": "Apprenant@Test.com", "role": "FORMATEUR"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", r.data)

    def test_ajout_utilisateur_existant_autre_organisme_casse_differente(self):
        autre = Tenant.objects.create(nom="Organisme Beta")
        self.creer_membre("awa@test.com", MembreTenant.Role.APPRENANT, tenant=autre)

        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url_membres, {"email": "AWA@test.com", "role": "APPRENANT"})

        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Utilisateur.objects.filter(email__iexact="awa@test.com").count(), 1)

    def test_admin_peut_ajouter_un_autre_admin(self):
        self.client.force_authenticate(self.admin)
        with self.captureOnCommitCallbacks(), patch("accounts.services.envoyer_email_activation.delay"):
            r = self.client.post(self.url_membres, {
                "email": "admin2@test.com", "nom": "N", "prenom": "P", "role": "ADMINISTRATEUR",
            })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data["role"], "ADMINISTRATEUR")


class ModificationMembreTests(AccountsBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)
        formation = Formation.objects.create(tenant=self.tenant, nom="Dev Web")
        self.promotion = Promotion.objects.create(formation=formation, nom="P1", date_debut=date(2026, 1, 1))

    def patch_membre(self, utilisateur, data):
        return self.client.patch(f"{self.url_membres}{self.membre(utilisateur).id}/", data)

    def test_dernier_admin_ne_peut_pas_perdre_son_role(self):
        r = self.patch_membre(self.admin, {"role": "FORMATEUR"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dernier_admin_ne_peut_pas_etre_desactive(self):
        r = self.patch_membre(self.admin, {"actif": False})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_peut_etre_retrograde_s_il_en_reste_un_autre(self):
        admin2 = self.creer_membre("admin2@test.com", MembreTenant.Role.ADMINISTRATEUR)
        r = self.patch_membre(admin2, {"role": "FORMATEUR"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(self.membre(admin2).role, "FORMATEUR")

    def test_admin_inactif_ne_compte_pas(self):
        self.creer_membre("admin2@test.com", MembreTenant.Role.ADMINISTRATEUR, actif=False)
        r = self.patch_membre(self.admin, {"actif": False})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apprenant_inscrit_ne_change_pas_de_role(self):
        InscriptionPromotion.objects.create(promotion=self.promotion, apprenant=self.apprenant)
        r = self.patch_membre(self.apprenant, {"role": "FORMATEUR"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("role", r.data)

    def test_apprenant_desinscrit_peut_changer_de_role(self):
        InscriptionPromotion.objects.create(promotion=self.promotion, apprenant=self.apprenant, actif=False)
        r = self.patch_membre(self.apprenant, {"role": "FORMATEUR"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_formateur_affecte_ne_change_pas_de_role(self):
        FormateurPromotion.objects.create(formateur=self.formateur, promotion=self.promotion)
        r = self.patch_membre(self.formateur, {"role": "APPRENANT"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("role", r.data)

    def test_formateur_sans_affectation_peut_changer_de_role(self):
        r = self.patch_membre(self.formateur, {"role": "APPRENANT"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)


class OrganismeSuspenduMembresTests(AccountsBaseTestCase):
    def test_gestion_membres_bloquee(self):
        self.tenant.statut = False
        self.tenant.save()
        self.client.force_authenticate(self.admin)
        r = self.client.get(self.url_membres)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(r.json()["code"], "organisme_suspendu")


# ─── Membre suspendu ──────────────────────────────────────────────────────────

class MembreSuspenduTests(AccountsBaseTestCase):
    """
    Le middleware identifie l'utilisateur par son JWT :
    ces tests utilisent un vrai token, pas force_authenticate.
    """

    def setUp(self):
        super().setUp()
        self.url_formations = f"/api/tenants/{self.tenant.id}/formations/"

    def authentifier(self, utilisateur):
        token = RefreshToken.for_user(utilisateur).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def suspendre(self, utilisateur, tenant=None):
        MembreTenant.objects.filter(
            utilisateur=utilisateur, tenant=tenant or self.tenant,
        ).update(actif=False)

    def test_membre_suspendu_recoit_code_dedie(self):
        admin2 = self.creer_membre("admin2@test.com", MembreTenant.Role.ADMINISTRATEUR)
        self.suspendre(admin2)
        self.authentifier(admin2)
        r = self.client.get(self.url_formations)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(r.json()["code"], "membre_suspendu")

    def test_membre_actif_non_bloque(self):
        self.authentifier(self.admin)
        self.assertEqual(self.client.get(self.url_formations).status_code, status.HTTP_200_OK)

    def test_suspension_limitee_a_l_organisme(self):
        autre = Tenant.objects.create(nom="Organisme Beta")
        MembreTenant.objects.create(
            utilisateur=self.formateur, tenant=autre, role=MembreTenant.Role.ADMINISTRATEUR,
        )
        self.suspendre(self.formateur)
        self.authentifier(self.formateur)
        r = self.client.get(f"/api/tenants/{autre.id}/formations/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_token_invalide_laisse_drf_repondre_401(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalide")
        self.assertEqual(self.client.get(self.url_formations).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_organisme_suspendu_prioritaire(self):
        self.suspendre(self.formateur)
        self.tenant.statut = False
        self.tenant.save()
        self.authentifier(self.formateur)
        r = self.client.get(self.url_formations)
        self.assertEqual(r.json()["code"], "organisme_suspendu")


class PromotionsEnCoursTests(AccountsBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)
        formation = Formation.objects.create(tenant=self.tenant, nom="Dev Web")
        self.p1 = Promotion.objects.create(formation=formation, nom="P1", date_debut=date(2026, 1, 1))
        self.p2 = Promotion.objects.create(formation=formation, nom="P2", date_debut=date(2026, 1, 1))
        InscriptionPromotion.objects.create(promotion=self.p1, apprenant=self.apprenant)
        FormateurPromotion.objects.create(formateur=self.formateur, promotion=self.p1)
        FormateurPromotion.objects.create(formateur=self.formateur, promotion=self.p2)

    def par_email(self, data):
        return {m["utilisateur_email"]: m["promotions_en_cours"] for m in data}

    def test_liste(self):
        r = self.client.get(self.url_membres)
        promos = self.par_email(r.data)
        self.assertEqual(promos["apprenant@test.com"], [{"id": self.p1.id, "nom": "P1"}])
        self.assertCountEqual(
            promos["formateur@test.com"],
            [{"id": self.p1.id, "nom": "P1"}, {"id": self.p2.id, "nom": "P2"}],
        )
        self.assertEqual(promos["admin@test.com"], [])

    def test_liste_nombre_de_requetes_constant(self):
        for i in range(5):
            self.creer_membre(f"apprenant{i}@test.com", MembreTenant.Role.APPRENANT)
        with self.assertNumQueries(5):
            # statut organisme (middleware) + permission + membres
            # + inscriptions + affectations : indépendant du nombre de membres
            self.client.get(self.url_membres)

    def test_inscription_inactive_ignoree(self):
        InscriptionPromotion.objects.filter(apprenant=self.apprenant).update(actif=False)
        r = self.client.get(f"{self.url_membres}{self.membre(self.apprenant).id}/")
        self.assertEqual(r.data["promotions_en_cours"], [])

    def test_suspension_autorisee_malgre_les_promotions(self):
        r = self.client.patch(f"{self.url_membres}{self.membre(self.formateur).id}/", {"actif": False})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(r.data["actif"])
        self.assertEqual(len(r.data["promotions_en_cours"]), 2)


# ─── Gabarit des emails ──────────────────────────────────────────────────────

@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_LOGO_URL="https://exemple.test/logo.png",
)
class GabaritEmailTests(SimpleTestCase):

    def test_logo_et_bouton_bleu(self):
        html = mise_en_page("Titre", ["Ligne"], "Voir", "https://eduhub.test/x")
        self.assertIn('src="https://exemple.test/logo.png"', html)
        self.assertIn('alt="EduHub"', html)
        self.assertIn(f"background:{BLEU}", html)

    def test_textes_echappes(self):
        html = mise_en_page("<b>Titre</b>", ["<script>x</script>"], "Voir", "https://eduhub.test/?a=1&b=2")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;b&gt;Titre&lt;/b&gt;", html)
        self.assertIn('href="https://eduhub.test/?a=1&amp;b=2"', html)

    def test_un_email_par_destinataire(self):
        envoyer(["a@test.com", "b@test.com"], "Sujet", "Titre", ["Ligne"], "Voir", "https://eduhub.test/x")
        self.assertEqual([m.to for m in mail.outbox], [["a@test.com"], ["b@test.com"]])
        self.assertIn("Voir : https://eduhub.test/x", mail.outbox[0].body)

    def test_activation_avec_lien_de_secours(self):
        envoyer_email_activation("awa@test.com", "https://eduhub.test/activate-account/abc")
        html = mail.outbox[0].alternatives[0][0]
        self.assertEqual(mail.outbox[0].subject, "Activez votre compte EduHub")
        self.assertIn("Si le bouton ne fonctionne pas", html)
        self.assertIn("https://eduhub.test/activate-account/abc", html)
