import requests
import time
from datetime import datetime, timezone, timedelta

BOT_TOKEN = "8713718165:AAEAZErm1E_tDdpHrBJj6T6haQbDC2sQKxc"
CHAT_ID = "-1004373927366"

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
MANAUS = timezone(timedelta(hours=-4))

def enviar_mensagem(texto):
    requests.post(f"{BASE_URL}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown"
    })

def main():
    print("Bot iniciado!")
    ultimo_envio = None

    while True:
        agora = datetime.now(MANAUS)
        hora_atual = agora.strftime("%H:%M")
        dia_semana = agora.weekday()
        dia = agora.day
        segundo = agora.second

        if dia_semana < 5 and segundo < 30:
            if dia % 2 == 0:
                horarios = ["10:00", "12:00", "14:00", "16:00"]
            else:
                horarios = ["09:30", "11:30", "13:30", "15:30"]

            if hora_atual in horarios and hora_atual != ultimo_envio:
                enviar_mensagem(f"🟢 *Check-in {hora_atual}*\nQuem está presente? Reaja com 👍")
                ultimo_envio = hora_atual
                print(f"Check-in enviado: {hora_atual}")

        time.sleep(20)

if __name__ == "__main__":
    main()
