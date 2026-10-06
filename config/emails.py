"""
Gabarit commun des emails d'EduHub : logo en en-tête, bouton bleu,
lien de secours et pied de page. Les textes sont échappés.

Le logo est une image hébergée (EMAIL_LOGO_URL) : les messageries
bloquent les images intégrées au message.
"""

from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

BLEU = "#0769DF"


def mise_en_page(titre, lignes, libelle_bouton, url, note="", lien_secours=False):
    # « note » est du HTML écrit dans le code (jamais une saisie) : non échappé.
    paragraphes = "".join(
        f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:#3f3f46;">{escape(l)}</p>'
        for l in lignes
    )
    url_attr = escape(url, quote=True)
    note_html = (
        f'<p style="margin:24px 0 0;font-size:14px;line-height:1.6;color:#666666;">{note}</p>'
        if note else ""
    )
    secours_html = (
        '<p style="margin:28px 0 6px;font-size:13px;line-height:1.5;color:#888888;">'
        "Si le bouton ne fonctionne pas, copiez et collez le lien suivant dans votre navigateur :</p>"
        f'<p style="margin:0;font-size:12px;word-break:break-all;color:#666666;">{escape(url)}</p>'
        if lien_secours else ""
    )
    return f"""
    <html>
      <body style="margin:0;padding:0;background:#f5f7fa;font-family:Arial,Helvetica,sans-serif;">
        <div style="max-width:600px;margin:40px auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);">
          <div style="padding:24px;text-align:center;background:#ffffff;border-bottom:4px solid {BLEU};">
            <img src="{escape(settings.EMAIL_LOGO_URL, quote=True)}" alt="EduHub" width="160"
                 style="display:inline-block;width:160px;max-width:60%;height:auto;border:0;color:{BLEU};font-size:24px;font-weight:bold;">
          </div>
          <div style="padding:32px;">
            <h2 style="margin:0 0 20px;font-size:20px;color:#111827;">{escape(titre)}</h2>
            {paragraphes}
            <div style="margin-top:28px;text-align:center;">
              <a href="{url_attr}" style="display:inline-block;padding:12px 24px;background:{BLEU};color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold;">
                {escape(libelle_bouton)}
              </a>
            </div>
            {note_html}
            {secours_html}
          </div>
          <div style="padding:16px;text-align:center;border-top:1px solid #eeeeee;">
            <p style="margin:0;font-size:12px;color:#888888;">Cet email a été envoyé automatiquement par EduHub.</p>
          </div>
        </div>
      </body>
    </html>
    """


def envoyer(destinataires, sujet, titre, lignes, libelle_bouton, url, note="", lien_secours=False):
    """Un email par destinataire : les adresses des autres ne sont jamais visibles."""
    html = mise_en_page(titre, lignes, libelle_bouton, url, note, lien_secours)
    texte = "\n\n".join([titre, *lignes, f"{libelle_bouton} : {url}"])
    for email in destinataires:
        message = EmailMultiAlternatives(
            subject=sujet,
            body=texte,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        )
        message.attach_alternative(html, "text/html")
        message.send()
