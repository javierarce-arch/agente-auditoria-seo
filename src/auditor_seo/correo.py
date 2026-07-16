"""
correo.py — envío de mail por Gmail (OAuth2/XOAUTH2, sin contraseña de
aplicación ni Action de terceros).

Mismo mecanismo que ya corre en producción en el proyecto hermano
`agente-reporte-diario-devs` (ver src/deliver_email.py ahí), portado acá con
soporte de adjuntos.

Requiere el extra opcional 'mail' (`pip install ".[mail]"`) y un token OAuth
generado una sola vez, a mano, con `python scripts/gmail_auth.py`.
"""

import base64
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    Request = None
    Credentials = None

_GMAIL_SCOPES = ["https://mail.google.com/"]


def credenciales_disponibles(ruta_token=None):
    """True si existe el archivo de token OAuth (explícito o por GMAIL_TOKEN_FILE)."""
    ruta_token = ruta_token or os.environ.get("GMAIL_TOKEN_FILE", "token.json")
    return os.path.exists(ruta_token)


def _token_acceso(ruta_token):
    if Credentials is None:
        raise SystemExit(
            "Hay un token de Gmail pero faltan las dependencias de mail. "
            "Instalá el extra: pip install -e '.[mail]'"
        )
    creds = Credentials.from_authorized_user_file(ruta_token, _GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(ruta_token, "w") as f:
            f.write(creds.to_json())
    return creds.token


def _xoauth2(usuario, token_acceso):
    payload = f"user={usuario}\x01auth=Bearer {token_acceso}\x01\x01"
    return base64.b64encode(payload.encode()).decode()


def _conectar_smtp(host, port, usuario, ruta_token):
    server = smtplib.SMTP(host, port, timeout=60)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.docmd("AUTH", "XOAUTH2 " + _xoauth2(usuario, _token_acceso(ruta_token)))
    return server


def enviar_mail(asunto, cuerpo, destinatarios, adjuntos=None):
    """
    Manda un mail por Gmail SMTP con OAuth2.

    `destinatarios`: lista de direcciones.
    `adjuntos`: lista de rutas de archivo a adjuntar tal cual.

    Variables de entorno usadas: SMTP_HOST (default smtp.gmail.com),
    SMTP_PORT (default 587), SMTP_USER (obligatoria), SMTP_FROM (default:
    SMTP_USER), GMAIL_TOKEN_FILE (default: token.json).
    """
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    usuario = os.environ["SMTP_USER"]
    remitente = os.environ.get("SMTP_FROM", usuario)
    ruta_token = os.environ.get("GMAIL_TOKEN_FILE", "token.json")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

    for ruta in adjuntos or []:
        with open(ruta, "rb") as f:
            parte = MIMEApplication(f.read(), Name=os.path.basename(ruta))
        parte["Content-Disposition"] = f'attachment; filename="{os.path.basename(ruta)}"'
        msg.attach(parte)

    server = _conectar_smtp(host, port, usuario, ruta_token)
    try:
        server.sendmail(remitente, destinatarios, msg.as_string())
    finally:
        server.quit()
