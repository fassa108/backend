from django.db import transaction
from django.utils import timezone

from activites.models import Livrable

from .models import GroupeMembre, InscriptionPromotion


class InscriptionService:

    @staticmethod
    def a_depose_dans_promotion(apprenant_id, promotion_id):
        """L'apprenant a-t-il déposé au moins un livrable dans cette promotion ?"""
        return Livrable.objects.filter(
            deposant_id=apprenant_id,
            assignation__brief__promotion_id=promotion_id,
        ).exists()

    @staticmethod
    def retirer_des_groupes(apprenant_id, promotion_id):
        """
        Retire l'apprenant des groupes de la promotion.

        - Aucun livrable déposé : appartenances supprimées.
        - Livrables déposés : appartenances conservées pour l'historique,
          mais inactives.
        """
        appartenances = GroupeMembre.objects.filter(
            apprenant_id=apprenant_id,
            groupe__promotion_id=promotion_id,
            actif=True,
        )

        if InscriptionService.a_depose_dans_promotion(apprenant_id, promotion_id):
            appartenances.update(actif=False)
        else:
            appartenances.delete()

    @staticmethod
    @transaction.atomic
    def desinscrire(inscription):
        inscription.actif = False
        inscription.fermee_par_cloture = False
        inscription.date_desinscription = timezone.now()
        inscription.save(update_fields=["actif", "fermee_par_cloture", "date_desinscription"])

        InscriptionService.retirer_des_groupes(
            inscription.apprenant_id,
            inscription.promotion_id,
        )

    @staticmethod
    @transaction.atomic
    def cloturer(promotion):
        """
        Clôture la promotion et ferme ses inscriptions actives.

        Les apprenants peuvent alors être inscrits dans une autre promotion.
        Les groupes et leurs membres sont conservés (lecture seule).
        Retourne le nombre d'inscriptions fermées.
        """
        nb = promotion.inscriptions.filter(actif=True).update(
            actif=False,
            fermee_par_cloture=True,
            date_desinscription=timezone.now(),
        )
        promotion.actif = False
        promotion.save(update_fields=["actif", "date_modification"])
        return nb

    @staticmethod
    @transaction.atomic
    def rouvrir(promotion):
        """
        Rouvre la promotion et réactive les inscriptions fermées par la clôture.

        Un apprenant inscrit entre-temps dans une autre promotion de
        l'organisme n'est pas réactivé (une seule inscription active par
        organisme) : il est renvoyé dans la liste « non_reactives ».
        """
        promotion.actif = True
        promotion.save(update_fields=["actif", "date_modification"])

        reactivees, non_reactives = 0, []
        fermees = promotion.inscriptions.filter(
            fermee_par_cloture=True,
        ).select_related("apprenant")

        for inscription in fermees:
            deja_inscrit = InscriptionPromotion.objects.filter(
                apprenant_id=inscription.apprenant_id,
                tenant_id=inscription.tenant_id,
                actif=True,
            ).exists()

            if deja_inscrit:
                # Définitivement sortie de cette promotion : ne sera plus
                # réactivée lors d'une prochaine réouverture.
                inscription.fermee_par_cloture = False
                inscription.save(update_fields=["fermee_par_cloture"])
                non_reactives.append({
                    "apprenant": inscription.apprenant_id,
                    "nom": f"{inscription.apprenant.prenom} {inscription.apprenant.nom}",
                })
                continue

            inscription.actif = True
            inscription.fermee_par_cloture = False
            inscription.date_desinscription = None
            inscription.save(update_fields=["actif", "fermee_par_cloture", "date_desinscription"])
            reactivees += 1

        return reactivees, non_reactives
