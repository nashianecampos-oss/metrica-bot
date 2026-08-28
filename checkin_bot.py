import requests
import time
from datetime import datetime, date
import pytz

BOT_TOKEN = "8713718165:AAEAZErm1E_tDdpHrBJj6T6haQbDC2sQKxc"
CHAT_ID = "-1004373927366"

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
MANAUS_TZ = pytz.timezone("America/Manaus")

def enviar_mensagem(texto):
    payload = {
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown"
    }
    requests.post(f"{BASE_URL}/sendMessage", json=payload)

def is_dia_util():
    agora = datetime.now(MANAUS_TZ)
    return agora.weekday() < 5

def get_horarios():
    agora = datetime.now(MANAUS_TZ)
    dia = agora.day
    if dia % 2 == 0:
        return ["10:00", "12:00", "14:00", "16:00"]
    else:
        return ["09:30", "11:30", "13:30", "15:30"]

def main():
    print("Bot iniciado!")
    ja_enviado = set()

    while True:
        agora = datetime.now(MANAUS_TZ)
        hora_atual = agora.strftime("%H:%M")
        hoje = str(agora.date())
        chave = f"{hoje}-{hora_atual}"

        if is_dia_util() and hora_atual in get_horarios() and chave not in ja_enviado:
            enviar_mensagem(f"🟢 *Check-in {hora_atual}*\nQuem está presente? Reaja com 👍")
            ja_enviado.add(chave)
            print(f"Check-in enviado: {hora_atual}")

        time.sleep(30)

if __name__ == "__main__":
    main()
