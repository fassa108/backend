"""
Client PayDunya (paiement en ligne, API « checkout-invoice »).

- creer_facture : facture à payer sur la page PayDunya (mobile money,
  carte…), renvoie son jeton et l'adresse de la page de paiement ;
- confirmer : état de la facture, lu chez PayDunya (seule source fiable :
  le retour du navigateur et le callback ne font que la déclencher) ;
- hash_valide : le « hash » renvoyé par PayDunya est le SHA-512 de la
  clé principale (MASTER KEY), il prouve que la réponse vient d'eux.

PAYDUNYA_MODE : « test » (sandbox) ou « live » (production).
"""

import hashlib
import hmac
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

URLS = {
    "test": "https://app.paydunya.com/sandbox-api/v1/",
    "live": "https://app.paydunya.com/api/v1/",
}
DELAI = (5, 30)  # connexion, réponse (secondes)


class PayDunyaIndisponible(Exception):
    """Clés absentes, service injoignable ou requête refusée."""


def configure():
    return all((settings.PAYDUNYA_MASTER_KEY, settings.PAYDUNYA_PRIVATE_KEY, settings.PAYDUNYA_TOKEN))


def _url(chemin):
    return URLS["live" if settings.PAYDUNYA_MODE == "live" else "test"] + chemin


def _entetes():
    if not configure():
        raise PayDunyaIndisponible("Clés PayDunya non configurées")
    return {
        "Content-Type": "application/json",
        "PAYDUNYA-MASTER-KEY": settings.PAYDUNYA_MASTER_KEY,
        "PAYDUNYA-PRIVATE-KEY": settings.PAYDUNYA_PRIVATE_KEY,
        "PAYDUNYA-TOKEN": settings.PAYDUNYA_TOKEN,
    }


def _appeler(methode, chemin, **kwargs):
    try:
        reponse = requests.request(methode, _url(chemin), headers=_entetes(), timeout=DELAI, **kwargs)
        donnees = reponse.json()
    except (requests.RequestException, ValueError) as erreur:
        logger.warning("PayDunya injoignable (%s) : %s", chemin, erreur)
        raise PayDunyaIndisponible("PayDunya ne répond pas") from erreur
    if reponse.status_code != 200 or donnees.get("response_code") != "00":
        logger.warning("PayDunya a refusé %s : %s %s", chemin, reponse.status_code, str(donnees)[:300])
        raise PayDunyaIndisponible(donnees.get("response_text") or "Requête refusée par PayDunya")
    return donnees


def creer_facture(*, montant, description, client, return_url, cancel_url, callback_url, custom_data):
    """Renvoie (jeton, adresse de la page de paiement)."""
    donnees = _appeler("POST", "checkout-invoice/create", json={
        "invoice": {
            "total_amount": montant,
            "description": description,
            "customer": client,
        },
        "store": {"name": "EduHub", "tagline": "Formation par compétences", "logo_url": settings.EMAIL_LOGO_URL},
        "actions": {"return_url": return_url, "cancel_url": cancel_url, "callback_url": callback_url},
        "custom_data": custom_data,
    })
    return donnees["token"], donnees["response_text"]


def confirmer(jeton):
    """État de la facture : {status, invoice, customer, receipt_url, hash…}."""
    return _appeler("GET", f"checkout-invoice/confirm/{jeton}")


def hash_valide(valeur):
    attendu = hashlib.sha512(settings.PAYDUNYA_MASTER_KEY.encode()).hexdigest()
    return bool(valeur) and hmac.compare_digest(str(valeur).lower(), attendu)
