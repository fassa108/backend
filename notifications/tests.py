"""
Tests des notifications de l'application.

Couvre :
- Service notifier() : création, anti-doublon par clé, rattachement à l'organisme
- API : chacun ne voit que les siennes, filtres, compteur, marquage
- Tâche quotidienne : planification et ménage des notifications lues
(les événements qui notifient sont testés dans leurs apps).
"""

from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Utilisateur
from tenants.models import Tenant

from .models import Notification
from .services import notifier, purger_notifications_lues
from .tasks import verifier_alertes

URL = "/api/notifications/"


class NotificationsBaseTestCase(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(nom="Organisme A")
        self.tenant_b = Tenant.objects.create(nom="Organisme B")
        self.awa = self.utilisateur("awa@test.com")
        self.moussa = self.utilisateur("moussa@test.com")

    def utilisateur(self, email):
        return Utilisateur.objects.create_user(email=email, password="pw", nom="Nom", prenom="Prenom", actif=True)

    def notif(self, utilisateur, titre="Titre", **extra):
        notifier([utilisateur], Notification.Type.ALERTE, titre, **extra)
        return Notification.objects.filter(utilisateur=utilisateur).first()


class NotifierTests(NotificationsBaseTestCase):
    def test_une_notification_par_destinataire(self):
        ids = notifier(
            [self.awa, self.moussa, self.awa], Notification.Type.ASSIGNATION, "Nouveau brief",
            "À rendre", "/activites/1", tenant=self.tenant,
        )
        self.assertEqual(ids, sorted([self.awa.id, self.moussa.id]))
        n = Notification.objects.get(utilisateur=self.awa)
        self.assertEqual((n.tenant, n.lien, n.lue), (self.tenant, "/activites/1", False))

    def test_identifiants_acceptes(self):
        notifier([self.awa.id], Notification.Type.ALERTE, "Titre", tenant=self.tenant.id)
        self.assertEqual(Notification.objects.get().tenant, self.tenant)

    def test_cle_anti_doublon(self):
        notifier([self.awa], Notification.Type.RAPPEL, "Rappel", cle="rappel-1")
        ids = notifier([self.awa, self.moussa], Notification.Type.RAPPEL, "Rappel", cle="rappel-1")
        self.assertEqual(ids, [self.moussa.id])
        self.assertEqual(Notification.objects.filter(utilisateur=self.awa).count(), 1)

    def test_sans_cle_pas_de_dedoublonnage(self):
        notifier([self.awa], Notification.Type.DEPOT, "Dépôt")
        notifier([self.awa], Notification.Type.DEPOT, "Dépôt")
        self.assertEqual(Notification.objects.count(), 2)

    def test_textes_trop_longs_tronques(self):
        n = self.notif(self.awa, "T" * 300, message="M" * 900)
        self.assertEqual((len(n.titre), len(n.message)), (150, 500))

    def test_aucun_destinataire(self):
        self.assertEqual(notifier([], Notification.Type.ALERTE, "Titre"), [])


class NotificationApiTests(NotificationsBaseTestCase):
    def setUp(self):
        super().setUp()
        self.n_a = self.notif(self.awa, "A", tenant=self.tenant)
        self.n_b = self.notif(self.awa, "B", tenant=self.tenant_b)
        self.n_saas = self.notif(self.awa, "Sans organisme")
        self.n_moussa = self.notif(self.moussa, "Moussa", tenant=self.tenant)
        self.client.force_authenticate(user=self.awa)

    def titres(self, res):
        return sorted(n["titre"] for n in res.data["results"])

    def test_authentification_requise(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_chacun_ne_voit_que_les_siennes(self):
        res = self.client.get(URL)
        self.assertEqual(res.data["count"], 3)
        self.assertNotIn("Moussa", self.titres(res))

    def test_filtre_par_organisme(self):
        res = self.client.get(URL, {"tenant": self.tenant.id})
        self.assertEqual(self.titres(res), ["A", "Sans organisme"])

    def test_organisme_non_numerique(self):
        self.assertEqual(self.client.get(URL, {"tenant": "x"}).status_code, status.HTTP_400_BAD_REQUEST)

    def test_plus_recentes_d_abord_et_pagination(self):
        res = self.client.get(URL, {"taille": 2})
        self.assertEqual([n["titre"] for n in res.data["results"]], ["Sans organisme", "B"])
        self.assertIsNotNone(res.data["next"])

    def test_compteur_et_lecture(self):
        compteur = lambda: self.client.get(f"{URL}compteur/", {"tenant": self.tenant.id}).data["non_lues"]
        self.assertEqual(compteur(), 2)

        res = self.client.post(f"{URL}{self.n_a.id}/lire/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["lue"])
        self.assertIsNotNone(res.data["date_lecture"])
        self.assertEqual(compteur(), 1)

        res = self.client.get(URL, {"non_lues": 1})
        self.assertEqual(self.titres(res), ["B", "Sans organisme"])

    def test_relire_garde_la_premiere_date(self):
        self.client.post(f"{URL}{self.n_a.id}/lire/")
        premiere = Notification.objects.get(pk=self.n_a.pk).date_lecture
        self.client.post(f"{URL}{self.n_a.id}/lire/")
        self.assertEqual(Notification.objects.get(pk=self.n_a.pk).date_lecture, premiere)

    def test_notification_d_un_autre_introuvable(self):
        res = self.client.post(f"{URL}{self.n_moussa.id}/lire/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Notification.objects.get(pk=self.n_moussa.pk).lue)

    def test_tout_lire_dans_un_organisme(self):
        res = self.client.post(f"{URL}tout-lire/?tenant={self.tenant.id}")
        self.assertEqual(res.data["marquees"], 2)
        self.assertFalse(Notification.objects.get(pk=self.n_b.pk).lue)
        self.assertFalse(Notification.objects.get(pk=self.n_moussa.pk).lue)

    def test_pas_de_creation_ni_de_suppression(self):
        self.assertEqual(self.client.post(URL, {"titre": "x"}).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.delete(f"{URL}{self.n_a.id}/").status_code, status.HTTP_404_NOT_FOUND)


class TacheQuotidienneTests(NotificationsBaseTestCase):
    def lue_il_y_a(self, jours):
        n = self.notif(self.awa, f"Lue il y a {jours} j")
        Notification.objects.filter(pk=n.pk).update(lue=True, date_lecture=timezone.now() - timedelta(days=jours))
        return n

    def test_planifiee_chaque_jour(self):
        taches = [t["task"] for t in settings.CELERY_BEAT_SCHEDULE.values()]
        self.assertIn("notifications.tasks.verifier_alertes", taches)

    def test_menage_des_notifications_lues_anciennes(self):
        ancienne = self.lue_il_y_a(61)
        recente = self.lue_il_y_a(10)
        non_lue = self.notif(self.moussa, "Non lue")
        Notification.objects.filter(pk=non_lue.pk).update(date_creation=timezone.now() - timedelta(days=200))

        self.assertEqual(purger_notifications_lues(), 1)
        self.assertFalse(Notification.objects.filter(pk=ancienne.pk).exists())
        self.assertTrue(Notification.objects.filter(pk__in=[recente.pk, non_lue.pk]).count() == 2)

    def test_tache_et_commande(self):
        self.lue_il_y_a(90)
        self.assertEqual(verifier_alertes(), {"purgees": 1})
        sortie = StringIO()
        call_command("verifier_alertes", stdout=sortie)
        self.assertIn("Vérification terminée", sortie.getvalue())
