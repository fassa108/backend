from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives


@shared_task
def envoyer_email_reset_password(email, reset_url):
    message = EmailMultiAlternatives(
        subject="Réinitialisation de votre mot de passe",
        body="Cliquez sur le lien pour réinitialiser votre mot de passe.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )

    message.attach_alternative(
        f"""
        <html>
            <body style="
                margin: 0;
                padding: 0;
                background-color: #f5f7fa;
                font-family: Arial, Helvetica, sans-serif;
                color: #242424;
            ">
                <div style="
                    max-width: 600px;
                    margin: 40px auto;
                    background-color: #ffffff;
                    border-radius: 12px;
                    overflow: hidden;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
                ">

                <!-- En-tête -->
                <div style="
                    padding: 28px;
                    text-align: center;
                    background-color: #242424;
                ">
                    <h1 style="
                        margin: 0;
                        color: #ffffff;
                        font-size: 28px;
                    ">
                        EduHub
                    </h1>
                </div>

                <!-- Contenu -->
                <div style="padding: 40px 35px;">

                    <h2 style="
                        margin-top: 0;
                        font-size: 22px;
                        color: #242424;
                    ">
                        Réinitialisation de votre mot de passe
                    </h2>

                    <p style="
                        font-size: 15px;
                        line-height: 1.6;
                    ">
                        Vous avez demandé la réinitialisation de votre mot de passe
                        pour votre compte EduHub.
                    </p>

                    <p style="
                        font-size: 15px;
                        line-height: 1.6;
                    ">
                        Cliquez sur le bouton ci-dessous pour définir un nouveau
                        mot de passe.
                    </p>

                    <!-- Bouton -->
                    <div style="
                        text-align: center;
                        margin: 32px 0;
                    ">
                        <a href="{reset_url}"
                        style="
                            display: inline-block;
                            padding: 14px 28px;
                            background-color: #242424;
                            color: #ffffff;
                            text-decoration: none;
                            border-radius: 7px;
                            font-size: 15px;
                            font-weight: bold;
                        ">
                            Réinitialiser mon mot de passe
                        </a>
                    </div>

                    <p style="
                        font-size: 14px;
                        line-height: 1.6;
                        color: #666666;
                    ">
                        Ce lien est valable pendant <strong>2 heures</strong>.
                        Si vous n'êtes pas à l'origine de cette demande,
                        vous pouvez simplement ignorer cet email.
                    </p>

                    <p style="
                        margin-top: 30px;
                        font-size: 13px;
                        line-height: 1.5;
                        color: #888888;
                    ">
                        Si le bouton ne fonctionne pas, copiez et collez le lien
                        suivant dans votre navigateur :
                    </p>

                    <p style="
                        font-size: 12px;
                        word-break: break-all;
                        color: #666666;
                    ">
                        {reset_url}
                    </p>

                </div>

                <!-- Pied de page -->
                <div style="
                    padding: 20px 35px;
                    background-color: #f5f7fa;
                    text-align: center;
                ">
                    <p style="
                        margin: 0;
                        font-size: 12px;
                        color: #888888;
                    ">
                        Cet email a été envoyé automatiquement par EduHub.
                    </p>
                </div>

            </div>
        </body>
        

        </html>

        """,
        "text/html",
    )

    message.send()


@shared_task
def envoyer_email_activation(email, activation_url):
    message = EmailMultiAlternatives(
        subject="Activez votre compte EduHub",
        body="Cliquez sur le lien pour activer votre compte EduHub.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )

    message.attach_alternative(
        f"""
        <html>
            <body style="
                margin: 0;
                padding: 0;
                background-color: #f5f7fa;
                font-family: Arial, Helvetica, sans-serif;
                color: #242424;
            ">
                <div style="
                    max-width: 600px;
                    margin: 40px auto;
                    background-color: #ffffff;
                    border-radius: 12px;
                    overflow: hidden;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
                ">

                    <div style="
                        padding: 28px;
                        text-align: center;
                        background-color: #242424;
                    ">
                        <h1 style="
                            margin: 0;
                            color: #ffffff;
                            font-size: 28px;
                        ">
                            EduHub
                        </h1>
                    </div>

                    <div style="padding: 40px 35px;">

                        <h2 style="
                            margin-top: 0;
                            font-size: 22px;
                            color: #242424;
                        ">
                            Bienvenue sur EduHub
                        </h2>

                        <p style="
                            font-size: 15px;
                            line-height: 1.6;
                        ">
                            Vous avez été invité à rejoindre EduHub.
                        </p>

                        <p style="
                            font-size: 15px;
                            line-height: 1.6;
                        ">
                            Cliquez sur le bouton ci-dessous pour activer
                            votre compte et définir votre mot de passe.
                        </p>

                        <div style="
                            text-align: center;
                            margin: 32px 0;
                        ">
                            <a href="{activation_url}"
                            style="
                                display: inline-block;
                                padding: 14px 28px;
                                background-color: #242424;
                                color: #ffffff;
                                text-decoration: none;
                                border-radius: 7px;
                                font-size: 15px;
                                font-weight: bold;
                            ">
                                Activer mon compte
                            </a>
                        </div>

                        <p style="
                            font-size: 14px;
                            line-height: 1.6;
                            color: #666666;
                        ">
                            Ce lien est valable pendant <strong>2 heures</strong>.
                        </p>

                        <p style="
                            margin-top: 30px;
                            font-size: 13px;
                            line-height: 1.5;
                            color: #888888;
                        ">
                            Si le bouton ne fonctionne pas, copiez et collez
                            le lien suivant dans votre navigateur :
                        </p>

                        <p style="
                            font-size: 12px;
                            word-break: break-all;
                            color: #666666;
                        ">
                            {activation_url}
                        </p>

                    </div>

                    <div style="
                        padding: 20px 35px;
                        background-color: #f5f7fa;
                        text-align: center;
                    ">
                        <p style="
                            margin: 0;
                            font-size: 12px;
                            color: #888888;
                        ">
                            Cet email a été envoyé automatiquement par EduHub.
                        </p>
                    </div>

                </div>
            </body>
        </html>
        """,
        "text/html",
    )

    message.send()