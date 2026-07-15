"""
gmail_auth.py — genera token.json para el envío de mail por Gmail (OAuth2).

Se corre una sola vez, a mano, en la máquina de quien configura el auditor
(no en CI). Requiere credentials.json (credenciales OAuth "Desktop app"
bajadas de Google Cloud Console) en la raíz del repo.

Uso:
    pip install -e ".[mail]"
    python scripts/gmail_auth.py

Si UTN ya tiene un token.json vigente para el mismo mecanismo en
agente-reporte-diario-devs (mismo scope, https://mail.google.com/), se puede
reutilizar directamente en vez de generar uno nuevo.
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://mail.google.com/"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(ROOT, "credentials.json")
TOKEN_FILE = os.path.join(ROOT, "token.json")


def main():
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"ERROR: no se encuentra {CREDENTIALS_FILE}")
        print("Bajá las credenciales OAuth (tipo 'Desktop app') desde Google Cloud Console "
              "y copiá el JSON aquí.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"Token guardado en: {TOKEN_FILE}")
    print("No commitees ese archivo — contiene tu refresh token. Para CI, subí su "
          "contenido como el secret GMAIL_TOKEN_JSON.")


if __name__ == "__main__":
    main()
