"""
Tests de l'assistant de l'apprenant.

Couvre : conversations privées et réservées aux apprenants, brief assigné
seulement, limites (longueur, une réponse à la fois, questions par jour),
contexte envoyé au service IA (anonymisé), réponse et échec.
"""

from unittest.mock import patch

from rest_framework import status

from activites.models import Assignation, Evaluation
from activites.tests import ActivitesBaseTestCase

from .models import Conversation, Message
from .views import QUESTIONS_PAR_JOUR


@patch("assistant.views.repondre.delay")
class ConversationTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.brief = self.creer_brief()
        self.brief.competence_niveaux.set([self.cn])
        Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        self.base = f"/api/tenants/{self.tenant.id}/assistant/conversations/"
        self.client.force_authenticate(user=self.apprenant)

    def nouvelle(self, **data):
        return self.client.post(self.base, data, format="json")

    def poser(self, conversation_id, contenu="Par quoi commencer ?"):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f"{self.base}{conversation_id}/messages/", {"contenu": contenu}, format="json")

    def test_conversation_sur_un_brief_puis_question(self, repondre):
        res = self.nouvelle(brief=self.brief.id)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["brief_titre"], self.brief.titre)

        res = self.poser(res.data["id"])
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual([(m["role"], m["statut"]) for m in res.data["messages"]],
                         [("APPRENANT", "PRET"), ("ASSISTANT", "EN_COURS")])
        self.assertEqual(res.data["titre"], "Par quoi commencer ?")
        repondre.assert_called_once_with(res.data["messages"][1]["id"])

    def test_brief_non_assigne_refuse(self, repondre):
        autre = self.creer_brief()
        self.assertEqual(self.nouvelle(brief=autre.id).status_code, status.HTTP_404_NOT_FOUND)

    def test_reserve_aux_apprenants(self, repondre):
        for u in (self.formateur, self.admin):
            self.client.force_authenticate(user=u)
            self.assertEqual(self.client.get(self.base).status_code, status.HTTP_403_FORBIDDEN, u.email)

    def test_conversations_privees(self, repondre):
        conversation_id = self.nouvelle().data["id"]
        self.client.force_authenticate(user=self.apprenant_b)
        self.assertEqual(self.client.get(self.base).data, [])
        self.assertEqual(self.client.get(f"{self.base}{conversation_id}/").status_code, status.HTTP_404_NOT_FOUND)

    def test_apprenant_sans_promotion(self, repondre):
        self.client.force_authenticate(user=self.apprenant_hors_promo)
        self.assertEqual(self.nouvelle().status_code, status.HTTP_400_BAD_REQUEST)

    def test_question_vide_ou_trop_longue(self, repondre):
        conversation_id = self.nouvelle().data["id"]
        for contenu in ("   ", "x" * 1001):
            self.assertEqual(self.poser(conversation_id, contenu).status_code, status.HTTP_400_BAD_REQUEST)

    def test_une_reponse_a_la_fois(self, repondre):
        conversation_id = self.nouvelle().data["id"]
        self.poser(conversation_id)
        res = self.poser(conversation_id, "Et ensuite ?")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Attendez la réponse", str(res.data))

    def test_limite_de_questions_par_jour(self, repondre):
        conversation = Conversation.objects.create(apprenant=self.apprenant, promotion=self.promotion)
        Message.objects.bulk_create(
            Message(conversation=conversation, role=Message.Role.APPRENANT, contenu="q")
            for _ in range(QUESTIONS_PAR_JOUR)
        )
        res = self.poser(self.nouvelle().data["id"])
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_suppression_de_sa_conversation(self, repondre):
        conversation_id = self.nouvelle().data["id"]
        self.assertEqual(self.client.delete(f"{self.base}{conversation_id}/").status_code, status.HTTP_204_NO_CONTENT)


@patch("assistant.tasks.requests.post")
class ReponseAssistantTests(ActivitesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.brief = self.creer_brief()
        self.brief.livrables_attendus = "<p>Une <b>fiche</b> protocole</p>"
        self.brief.save()
        self.brief.competence_niveaux.set([self.cn])
        self.assignation = Assignation.objects.create(brief=self.brief, apprenant=self.apprenant)
        Evaluation.objects.create(assignation=self.assignation, evaluateur=self.formateur, commentaire="Soignez la mise en page")
        self.conversation = Conversation.objects.create(apprenant=self.apprenant, promotion=self.promotion, brief=self.brief)
        Message.objects.create(conversation=self.conversation, role=Message.Role.APPRENANT, contenu="Bonjour")
        Message.objects.create(conversation=self.conversation, role=Message.Role.ASSISTANT, contenu="Bonjour !")
        Message.objects.create(conversation=self.conversation, role=Message.Role.APPRENANT, contenu="Par quoi commencer ?")
        self.reponse = Message.objects.create(
            conversation=self.conversation, role=Message.Role.ASSISTANT, statut=Message.Statut.EN_COURS
        )

    def repondre(self):
        from .tasks import repondre
        repondre(self.reponse.id)
        self.reponse.refresh_from_db()

    def test_reponse_enregistree(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"reponse": "Commencez par lister les étapes."}
        self.repondre()
        self.assertEqual((self.reponse.statut, self.reponse.contenu), ("PRET", "Commencez par lister les étapes."))

    def test_contexte_envoye(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"reponse": "ok"}
        self.repondre()
        demande = post.call_args.kwargs["json"]
        self.assertEqual(demande["question"], "Par quoi commencer ?")
        self.assertEqual([m["role"] for m in demande["historique"]], ["apprenant", "assistant"])
        contexte = demande["contexte"]
        self.assertEqual(contexte["formation"], "Formation Web")
        self.assertEqual(contexte["brief"]["livrables_attendus"], "Une fiche protocole")
        self.assertEqual(contexte["brief"]["competences_visees"], ["Développer une API (Imiter)"])
        self.assertEqual(contexte["competences_non_acquises"], ["Développer une API (Imiter)"])
        self.assertEqual(contexte["commentaires_formateur"], ["Soignez la mise en page"])
        self.assertIn("Base de données", [m["nom"] for m in contexte["modules"]])
        self.assertNotIn(self.apprenant.email, str(demande))

    def test_echec_du_service(self, post):
        post.return_value.status_code = 503
        post.return_value.text = "indisponible"
        self.repondre()
        self.assertEqual(self.reponse.statut, "ECHEC")
        self.assertIn("pas disponible", self.reponse.contenu)

    def test_conversation_sans_brief(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"reponse": "ok"}
        Conversation.objects.filter(pk=self.conversation.pk).update(brief=None)
        self.repondre()
        contexte = post.call_args.kwargs["json"]["contexte"]
        self.assertIsNone(contexte["brief"])
        self.assertIn("Modéliser (Imiter)", contexte["competences_non_acquises"])
