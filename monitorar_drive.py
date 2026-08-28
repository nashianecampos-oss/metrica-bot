import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from google.oauth2 import service_account

# ─── CONFIGURAÇÕES ───────────────────────────────────────────
BOT_TOKEN = "8713718165:AAEAZErm1E_tDdpHrBJj6T6haQbDC2sQKxc"
CHAT_ID = "-1004404379489"
PASTA_RAIZ_ID = "1vdj8uJ1-M-XJjim7tNlBRKGFWovxhUJP"
INTERVALO_SEGUNDOS = 60
# ─────────────────────────────────────────────────────────────

MANAUS = timezone(timedelta(hours=-4))
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def autenticar():
    # Lê credenciais da variável de ambiente GOOGLE_CREDENTIALS
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        info = json.loads(creds_json)
    else:
        # Fallback para arquivo local (desenvolvimento)
        with open(r"C:\DCE\credenciais.json", "r") as f:
            info = json.load(f)
    
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds)

def enviar_telegram(texto):
    requests.post(f"{BASE_URL}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown"
    })

def buscar_modificados(service, desde):
    desde_str = desde.strftime("%Y-%m-%dT%H:%M:%S")
    resultado = service.files().list(
        q=f"modifiedTime > '{desde_str}Z' and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
        spaces="drive",
        fields="files(id, name, modifiedTime, lastModifyingUser, parents)",
        orderBy="modifiedTime desc",
        corpora="allDrives",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True
    ).execute()
    return resultado.get("files", [])

def get_caminho(service, parents):
    partes = []
    atual_parents = parents
    for _ in range(5):
        if not atual_parents:
            break
        pasta_id = atual_parents[0]
        if pasta_id == PASTA_RAIZ_ID:
            break
        try:
            pasta = service.files().get(
                fileId=pasta_id,
                fields="name, parents",
                supportsAllDrives=True
            ).execute()
            partes.insert(0, pasta["name"])
            atual_parents = pasta.get("parents", [])
        except:
            break
    return " / ".join(partes) if partes else "raiz"

def main():
    print("🔍 Monitoramento do Google Drive iniciado!")
    service = autenticar()
    ultimo_check = datetime.now(MANAUS) - timedelta(minutes=2)
    arquivos_vistos = set()

    while True:
        try:
            agora = datetime.now(MANAUS)
            arquivos = buscar_modificados(service, ultimo_check)

            for arquivo in arquivos:
                file_id = arquivo["id"]
                if file_id in arquivos_vistos:
                    continue

                arquivos_vistos.add(file_id)
                nome = arquivo["name"]
                modificado_por = arquivo.get("lastModifyingUser", {}).get("displayName", "Alguém")
                hora = datetime.fromisoformat(
                    arquivo["modifiedTime"].replace("Z", "+00:00")
                ).astimezone(MANAUS).strftime("%H:%M")
                caminho = get_caminho(service, arquivo.get("parents", []))

                mensagem = (
                    f"📁 *{modificado_por}* salvou um arquivo\n"
                    f"📂 {caminho}\n"
                    f"📄 {nome}\n"
                    f"🕐 {hora}"
                )
                enviar_telegram(mensagem)
                print(f"Notificado: {nome} por {modificado_por}")

            ultimo_check = agora
            if len(arquivos_vistos) > 1000:
                arquivos_vistos.clear()

        except Exception as e:
            print(f"Erro: {e}")

        time.sleep(INTERVALO_SEGUNDOS)

if __name__ == "__main__":
    main()
