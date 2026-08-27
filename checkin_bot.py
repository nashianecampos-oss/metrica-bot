import requests
import time
from datetime import datetime, date

BOT_TOKEN = "8713718165:AAEAZErm1E_tDdpHrBJj6T6haQbDC2sQKxc"
CHAT_ID = "-1004373927366"

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def enviar_mensagem(texto):
    payload = {
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown"
    }
    requests.post(f"{BASE_URL}/sendMessage", json=payload)

def is_dia_util():
    return date.today().weekday() < 5

def get_horarios():
    dia = date.today().day
    if dia % 2 == 0:
        return ["10:00", "12:00", "14:00", "16:00"]
    else:
        return ["09:30", "11:30", "13:30", "15:30"]

def main():
    print("Bot iniciado!")
    ja_enviado = set()

    while True:
        agora = datetime.now().strftime("%H:%M")
        hoje = str(date.today())
        chave = f"{hoje}-{agora}"

        if is_dia_util() and agora in get_horarios() and chave not in ja_enviado:
            enviar_mensagem(f"🟢 *Check-in {agora}*\nQuem está presente? Reaja com 👍")
            ja_enviado.add(chave)
            print(f"Check-in enviado: {agora}")

        time.sleep(30)

if __name__ == "__main__":
    main()
