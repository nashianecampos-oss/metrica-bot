import requests
import time
from datetime import datetime, timezone, timedelta, date

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
    ja_enviado = set()

    while True:
        agora = datetime.now(MANAUS)
        hora_atual = agora.strftime("%H:%M")
        hoje = str(agora.date())
        dia_semana = agora.weekday()  # 0=seg, 4=sex
        dia = agora.day
        chave = f"{hoje}-{hora_atual}"

        if dia_semana < 5:
            if dia % 2 == 0:
                horarios = ["10:00", "12:00", "14:00", "16:00"]
            else:
                horarios = ["09:30", "11:30", "13:30", "15:30"]

            if hora_atual in horarios and chave not in ja_enviado:
                enviar_mensagem(f"🟢 *Check-in {hora_atual}*\nQuem está presente? Reaja com 👍")
                ja_enviado.add(chave)
                print(f"Check-in enviado: {hora_atual}")

        time.sleep(30)

if __name__ == "__main__":
    main()
