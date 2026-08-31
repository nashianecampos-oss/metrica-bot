import os
import json
import time
import requests
import io
import threading
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from google.oauth2 import service_account

# ─── CONFIGURAÇÕES ───────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GRUPO_EQUIPE = "-1004373927366"
GRUPO_PRIVADO = "-1004404379489"
THREAD_RELATORIOS = 19
PLANILHA_ID = "1yeJdT45QwqN9HvyvxEGdRxK6DQwEIJK_TRFqd9tI2-A"
PASTA_DRIVE_ID = "1vdj8uJ1-M-XJjim7tNlBRKGFWovxhUJP"
WORKSPACE_CLICKUP = "9011144418"
# ─────────────────────────────────────────────────────────────

MANAUS = timezone(timedelta(hours=-4))
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Mapeamento Telegram ID → dados da pessoa
EQUIPE = {
    7615289681: {
        "nome": "Helena",
        "mention": "[Helena](tg://user?id=7615289681)",
        "intervalo_h": 1,
        "carga_diaria_h": 6,
        "clickup_id": 75487185,
        "drive_name": None,  # preencher quando trabalhar
    },
    5777049521: {
        "nome": "Micheli",
        "mention": "[Micheli](tg://user?id=5777049521)",
        "intervalo_h": 0,
        "carga_diaria_h": 6,
        "clickup_id": 194434554,
        "drive_name": "michelemaria792",
    },
    6488820892: {
        "nome": "Erick",
        "mention": "[Erick](tg://user?id=6488820892)",
        "intervalo_h": None,
        "carga_diaria_h": None,
        "clickup_id": 296428627,
        "drive_name": "erickbrunol.n",
    },
    2048504320: {
        "nome": "Nagia",
        "mention": "[Nagia](tg://user?id=2048504320)",
        "intervalo_h": 1,
        "carga_diaria_h": 8,
        "clickup_id": 84120914,
        "drive_name": "helena.metricabim",
    },
}

# Mapeamento drive_name → telegram_id
DRIVE_PARA_TELEGRAM = {
    v["drive_name"]: k for k, v in EQUIPE.items() if v["drive_name"]
}

ORDEM_PLANILHA = [7615289681, 5777049521, 6488820892, 2048504320]
COLUNAS_POR_PESSOA = 6

# Estado em memória
registros = {}          # {telegram_id: {entrada, data, entrada_extra, extra_min}}
drive_atividades = {}   # {telegram_id: {primeira: {arquivo, hora}, ultima: {arquivo, hora}}}
drive_monitorando = {}  # {telegram_id: True} — quem está com monitoramento ativo
arquivos_vistos = set()

# ─── CREDENCIAIS ─────────────────────────────────────────────
def carregar_credenciais():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        return json.loads(creds_json)
    with open(r"C:\DCE\credenciais.json", "r") as f:
        return json.load(f)

def carregar_clickup_token():
    token = os.environ.get("CLICKUP_TOKEN")
    if token:
        return token
    with open(r"C:\DCE\Discord.txt", "r", encoding="utf-8") as f:
        for line in f:
            if "API Clickup:" in line:
                return line.replace("API Clickup:", "").strip()
    return None

# ─── GOOGLE DRIVE ─────────────────────────────────────────────
def autenticar_drive():
    info = carregar_credenciais()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly",
                      "https://www.googleapis.com/auth/spreadsheets"]
    )
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return drive, sheets

def get_caminho_drive(drive, parents):
    partes = []
    atual = parents
    for _ in range(5):
        if not atual:
            break
        pasta_id = atual[0]
        if pasta_id == PASTA_DRIVE_ID:
            break
        try:
            pasta = drive.files().get(
                fileId=pasta_id, fields="name, parents",
                supportsAllDrives=True
            ).execute()
            partes.insert(0, pasta["name"])
            atual = pasta.get("parents", [])
        except:
            break
    return " / ".join(partes) if partes else "raiz"

def buscar_arquivos_drive(drive, desde):
    desde_str = desde.strftime("%Y-%m-%dT%H:%M:%S")
    resultado = drive.files().list(
        q=f"modifiedTime > '{desde_str}Z' and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
        spaces="drive",
        fields="files(id, name, modifiedTime, lastModifyingUser, parents)",
        orderBy="modifiedTime desc",
        corpora="allDrives",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True
    ).execute()
    return resultado.get("files", [])

# ─── CLICKUP ─────────────────────────────────────────────────
def clickup_get(endpoint, token):
    r = requests.get(
        f"https://api.clickup.com/api/v2/{endpoint}",
        headers={"Authorization": token}
    )
    return r.json()

def verificar_clickup_inicio(telegram_id, token):
    """Verifica se a pessoa iniciou alguma atividade no ClickUp hoje"""
    config = EQUIPE[telegram_id]
    clickup_id = config["clickup_id"]
    mention = config["mention"]
    
    agora = datetime.now(MANAUS)
    inicio_dia = int(agora.replace(hour=0, minute=0, second=0).timestamp() * 1000)
    
    # Busca time entries de hoje
    data = clickup_get(
        f"team/{WORKSPACE_CLICKUP}/time_entries?assignee={clickup_id}&start_date={inicio_dia}&end_date={int(agora.timestamp()*1000)}",
        token
    )
    entries = data.get("data", [])
    
    if not entries:
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"⚠️ {mention}, você ainda não iniciou nenhuma atividade no ClickUp hoje!")

def verificar_clickup_encerramento(telegram_id, token):
    """Verifica tarefas com tempo registrado hoje mas campos não preenchidos"""
    config = EQUIPE[telegram_id]
    clickup_id = config["clickup_id"]
    mention = config["mention"]

    agora = datetime.now(MANAUS)
    inicio_dia = int(agora.replace(hour=0, minute=0, second=0).timestamp() * 1000)

    data = clickup_get(
        f"team/{WORKSPACE_CLICKUP}/time_entries?assignee={clickup_id}&start_date={inicio_dia}&end_date={int(agora.timestamp()*1000)}",
        token
    )
    entries = data.get("data", [])

    tarefas_sem_preenchimento = []
    tarefas_vistas = set()

    for entry in entries:
        task = entry.get("task", {})
        task_id = task.get("id")
        task_name = task.get("name", "Tarefa sem nome")

        if not task_id or task_id in tarefas_vistas:
            continue
        tarefas_vistas.add(task_id)

        # Busca campos da tarefa
        task_data = clickup_get(f"task/{task_id}", token)
        custom_fields = task_data.get("custom_fields", [])

        feito = None
        falta = None
        feito_updated = None
        falta_updated = None

        for field in custom_fields:
            nome = field.get("name", "").lower()
            if "foi feito" in nome or "feito" in nome:
                feito = field.get("value")
                feito_updated = field.get("date_updated")
            elif "falta" in nome:
                falta = field.get("value")
                falta_updated = field.get("date_updated")

        # Verifica se foi atualizado hoje
        feito_hoje = feito_updated and int(feito_updated) >= inicio_dia
        falta_hoje = falta_updated and int(falta_updated) >= inicio_dia

        if not feito_hoje or not falta_hoje:
            tarefas_sem_preenchimento.append(task_name)

    if tarefas_sem_preenchimento:
        lista = "\n".join([f"• {t}" for t in tarefas_sem_preenchimento])
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"📋 {mention}, preencha os campos nas seguintes tarefas:\n{lista}")

# ─── TELEGRAM ─────────────────────────────────────────────────
def enviar_telegram(chat_id, thread_id, texto):
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    try:
        requests.post(f"{BASE_URL}/sendMessage", json=payload)
    except Exception as e:
        print(f"Erro ao enviar telegram: {e}")

# ─── PLANILHA ─────────────────────────────────────────────────
def col_letra(n):
    result = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result

def garantir_aba(sheets, nome_aba):
    meta = sheets.spreadsheets().get(spreadsheetId=PLANILHA_ID).execute()
    abas = [s["properties"]["title"] for s in meta["sheets"]]
    if nome_aba not in abas:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=PLANILHA_ID,
            body={"requests": [{"addSheet": {"properties": {"title": nome_aba}}}]}
        ).execute()

    resultado = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID,
        range=f"'{nome_aba}'!A1"
    ).execute()

    if not resultado.get("values"):
        cabecalho = ["Data"]
        for uid in ORDEM_PLANILHA:
            nome = EQUIPE[uid]["nome"]
            cabecalho += [
                f"{nome} Entrada", f"{nome} Saída", f"{nome} Total",
                f"{nome} Extra Entrada", f"{nome} Extra Saída", f"{nome} Extra Total"
            ]
        sheets.spreadsheets().values().update(
            spreadsheetId=PLANILHA_ID,
            range=f"'{nome_aba}'!A1",
            valueInputOption="RAW",
            body={"values": [cabecalho]}
        ).execute()

def get_ou_criar_linha(sheets, nome_aba, data):
    resultado = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID,
        range=f"'{nome_aba}'!A:A"
    ).execute()
    datas = [r[0] if r else "" for r in resultado.get("values", [])]

    if data in datas:
        return datas.index(data) + 1

    proxima = max(len(datas) + 1, 2)
    sheets.spreadsheets().values().update(
        spreadsheetId=PLANILHA_ID,
        range=f"'{nome_aba}'!A{proxima}",
        valueInputOption="RAW",
        body={"values": [[data]]}
    ).execute()
    return proxima

def atualizar_celulas(sheets, nome_aba, linha, col_inicio, valores):
    col_ini = col_letra(col_inicio)
    col_fim = col_letra(col_inicio + len(valores) - 1)
    sheets.spreadsheets().values().update(
        spreadsheetId=PLANILHA_ID,
        range=f"'{nome_aba}'!{col_ini}{linha}:{col_fim}{linha}",
        valueInputOption="RAW",
        body={"values": [valores]}
    ).execute()

def salvar_expediente(sheets, data, user_id, entrada, saida, total_min):
    nome_aba = datetime.now(MANAUS).strftime("%m-%Y")
    garantir_aba(sheets, nome_aba)
    linha = get_ou_criar_linha(sheets, nome_aba, data)
    pos = ORDEM_PLANILHA.index(user_id)
    col_inicio = 1 + pos * COLUNAS_POR_PESSOA
    horas = total_min // 60
    mins = total_min % 60
    atualizar_celulas(sheets, nome_aba, linha, col_inicio,
                     [entrada, saida, f"{horas}h{mins:02d}min"])

def salvar_extra(sheets, data, user_id, entrada_extra, saida_extra, extra_min):
    nome_aba = datetime.now(MANAUS).strftime("%m-%Y")
    garantir_aba(sheets, nome_aba)
    linha = get_ou_criar_linha(sheets, nome_aba, data)
    pos = ORDEM_PLANILHA.index(user_id)
    col_inicio = 1 + pos * COLUNAS_POR_PESSOA + 3
    extra_h = extra_min // 60
    extra_m = extra_min % 60
    atualizar_celulas(sheets, nome_aba, linha, col_inicio,
                     [entrada_extra, saida_extra, f"{extra_h}h{extra_m:02d}min"])

# ─── PROCESSAMENTO DE MENSAGENS ───────────────────────────────
def calcular_intervalo(user_id, minutos_trabalhados):
    if user_id == 6488820892:
        return 60 if minutos_trabalhados > 360 else 0
    intervalo = EQUIPE[user_id].get("intervalo_h") or 0
    return intervalo * 60

def processar_mensagem(msg, sheets, clickup_token):
    texto = msg.get("text", "").strip().lower()
    user_id = msg.get("from", {}).get("id")
    chat_id = str(msg.get("chat", {}).get("id", ""))
    thread_id = msg.get("message_thread_id")

    if chat_id != GRUPO_EQUIPE:
        return
    if thread_id is not None and thread_id != 1:
        return
    if user_id not in EQUIPE:
        return

    agora = datetime.now(MANAUS)
    if agora.weekday() == 6 or agora.hour < 7 or agora.hour >= 21:
        return

    nome = EQUIPE[user_id]["nome"]
    mention = EQUIPE[user_id]["mention"]

    if texto == "/iniciar":
        if user_id not in registros:
            registros[user_id] = {}
        registros[user_id]["entrada"] = agora
        registros[user_id]["data"] = agora.strftime("%d/%m/%Y")
        drive_monitorando[user_id] = True
        drive_atividades[user_id] = {"primeira": None, "ultima": None}

        enviar_telegram(GRUPO_EQUIPE, None,
                       f"✅ *{nome}* iniciou às {agora.strftime('%H:%M')}")

        # Verifica ClickUp após 5min em thread separada
        def checar_clickup_inicio():
            time.sleep(300)
            if user_id in drive_monitorando:
                verificar_clickup_inicio(user_id, clickup_token)
        threading.Thread(target=checar_clickup_inicio, daemon=True).start()

    elif texto == "/encerrando":
        if user_id not in registros or "entrada" not in registros[user_id]:
            enviar_telegram(GRUPO_EQUIPE, None,
                           f"⚠️ {mention}, use /iniciar primeiro.")
            return

        entrada = registros[user_id]["entrada"]
        minutos_brutos = int((agora - entrada).total_seconds() / 60)
        intervalo_min = calcular_intervalo(user_id, minutos_brutos)
        minutos_liquidos = max(0, minutos_brutos - intervalo_min)
        horas = minutos_liquidos // 60
        mins = minutos_liquidos % 60

        # Para o monitoramento do drive
        drive_monitorando.pop(user_id, None)

        # Última atividade no Drive
        ultima_drive = drive_atividades.get(user_id, {}).get("ultima")
        msg_drive = ""
        if ultima_drive:
            msg_drive = f"\n📁 Última atividade Drive: {ultima_drive['hora']}\n_{ultima_drive['arquivo']}_"

        try:
            salvar_expediente(sheets, registros[user_id]["data"], user_id,
                            entrada.strftime("%H:%M"), agora.strftime("%H:%M"),
                            minutos_liquidos)
        except Exception as e:
            print(f"Erro sheets: {e}")

        registros[user_id] = {"data": agora.strftime("%d/%m/%Y")}

        enviar_telegram(GRUPO_EQUIPE, None,
                       f"👋 *{nome}* encerrou às {agora.strftime('%H:%M')}\n"
                       f"⏱ Trabalhado: *{horas}h{mins:02d}min*{msg_drive}")

        # Verifica ClickUp após 5min em thread separada
        def checar_clickup_encerramento():
            time.sleep(300)
            verificar_clickup_encerramento(user_id, clickup_token)
        threading.Thread(target=checar_clickup_encerramento, daemon=True).start()

    elif texto == "/iniciar extra":
        if user_id not in registros:
            registros[user_id] = {}
        registros[user_id]["entrada_extra"] = agora
        if "data" not in registros[user_id]:
            registros[user_id]["data"] = agora.strftime("%d/%m/%Y")
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"⭐ *{nome}* iniciou hora extra às {agora.strftime('%H:%M')}")

    elif texto == "/encerrando extra":
        if user_id not in registros or "entrada_extra" not in registros[user_id]:
            enviar_telegram(GRUPO_EQUIPE, None,
                           f"⚠️ {mention}, use /iniciar extra primeiro.")
            return

        entrada_extra = registros[user_id]["entrada_extra"]
        extra_min = int((agora - entrada_extra).total_seconds() / 60)
        data = registros[user_id].get("data", agora.strftime("%d/%m/%Y"))

        try:
            salvar_extra(sheets, data, user_id,
                        entrada_extra.strftime("%H:%M"), agora.strftime("%H:%M"),
                        extra_min)
        except Exception as e:
            print(f"Erro sheets extra: {e}")

        del registros[user_id]["entrada_extra"]
        extra_h = extra_min // 60
        extra_m = extra_min % 60
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"⭐ *{nome}* encerrou hora extra\n"
                       f"⏱ Extra: *{extra_h}h{extra_m:02d}min*")

# ─── MONITORAMENTO DO DRIVE ───────────────────────────────────
def loop_drive(drive):
    global arquivos_vistos
    ultimo_check = datetime.now(MANAUS) - timedelta(minutes=2)

    while True:
        try:
            agora = datetime.now(MANAUS)
            arquivos = buscar_arquivos_drive(drive, ultimo_check)

            for arquivo in arquivos:
                file_id = arquivo["id"]
                if file_id in arquivos_vistos:
                    continue
                arquivos_vistos.add(file_id)

                nome_arquivo = arquivo["name"]
                modificado_por = arquivo.get("lastModifyingUser", {}).get("displayName", "")
                hora = datetime.fromisoformat(
                    arquivo["modifiedTime"].replace("Z", "+00:00")
                ).astimezone(MANAUS).strftime("%H:%M")
                caminho = get_caminho_drive(drive, arquivo.get("parents", []))

                # Notifica grupo privado
                enviar_telegram(GRUPO_PRIVADO, None,
                               f"📁 *{modificado_por}* salvou\n"
                               f"📂 {caminho}\n"
                               f"📄 {nome_arquivo}\n"
                               f"🕐 {hora}")

                # Verifica se é alguém da equipe monitorada
                telegram_id = DRIVE_PARA_TELEGRAM.get(modificado_por)
                if telegram_id and telegram_id in drive_monitorando:
                    atividades = drive_atividades.get(telegram_id, {})
                    info = {"arquivo": f"{caminho} / {nome_arquivo}", "hora": hora}

                    # Primeira atividade
                    if not atividades.get("primeira"):
                        atividades["primeira"] = info
                        drive_atividades[telegram_id] = atividades
                        nome = EQUIPE[telegram_id]["nome"]
                        enviar_telegram(GRUPO_EQUIPE, None,
                                       f"📁 *{nome}* - Primeira atividade Drive: {hora}\n"
                                       f"_{caminho} / {nome_arquivo}_")

                    # Atualiza última sempre
                    atividades["ultima"] = info
                    drive_atividades[telegram_id] = atividades

            ultimo_check = agora
            if len(arquivos_vistos) > 2000:
                arquivos_vistos.clear()

        except Exception as e:
            print(f"Erro drive: {e}")

        time.sleep(60)

# ─── RELATÓRIO MENSAL ─────────────────────────────────────────
def gerar_relatorio_mensal(sheets):
    agora = datetime.now(MANAUS)
    mes_anterior = agora.replace(day=1) - timedelta(days=1)
    nome_aba = mes_anterior.strftime("%m-%Y")
    mes_str = mes_anterior.strftime("%m/%Y")

    try:
        resultado = sheets.spreadsheets().values().get(
            spreadsheetId=PLANILHA_ID,
            range=f"'{nome_aba}'!A1:Z1000"
        ).execute()
        linhas = resultado.get("values", [])
    except:
        enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS, f"⚠️ Sem dados para {mes_str}")
        return

    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    for linha in linhas:
        writer.writerow(linha)
    csv_bytes = output.getvalue().encode("utf-8-sig")

    requests.post(f"{BASE_URL}/sendDocument", data={
        "chat_id": GRUPO_PRIVADO,
        "message_thread_id": THREAD_RELATORIOS,
        "caption": f"📊 Relatório de ponto — {mes_str}"
    }, files={"document": (f"ponto_{nome_aba}.csv", csv_bytes, "text/csv")})

# ─── MAIN ─────────────────────────────────────────────────────
def main():
    print("🚀 Métrica Bot iniciado!")
    drive, sheets = autenticar_drive()
    clickup_token = carregar_clickup_token()
    print(f"✅ Drive, Sheets e ClickUp conectados")

    # Inicia monitoramento do Drive em thread separada
    threading.Thread(target=loop_drive, args=(drive,), daemon=True).start()
    print("📁 Monitoramento do Drive iniciado")

    offset = None
    relatorio_enviado = False

    while True:
        try:
            params = {"timeout": 10}
            if offset:
                params["offset"] = offset

            r = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=15)
            updates = r.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                if msg:
                    processar_mensagem(msg, sheets, clickup_token)

            agora = datetime.now(MANAUS)
            if agora.day == 1 and agora.hour == 8 and agora.minute == 0:
                if not relatorio_enviado:
                    gerar_relatorio_mensal(sheets)
                    relatorio_enviado = True
            else:
                relatorio_enviado = False

        except Exception as e:
            print(f"Erro main: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
