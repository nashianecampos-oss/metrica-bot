import os
import json
import time
import requests
import io
import threading
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from google.oauth2 import service_account
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── CONFIGURAÇÕES ───────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CLICKUP_TOKEN = os.environ.get("CLICKUP_TOKEN")
GRUPO_EQUIPE = "-1004373927366"
GRUPO_TODOS = "-1004387758894"
GRUPO_PRIVADO = "-1004404379489"
THREAD_RELATORIOS = 19
PLANILHA_ID = "1yeJdT45QwqN9HvyvxEGdRxK6DQwEIJK_TRFqd9tI2-A"
PASTA_DRIVE_ID = "1vdj8uJ1-M-XJjim7tNlBRKGFWovxhUJP"
WORKSPACE_CLICKUP = "9011144418"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "metrica2026")
PORT = int(os.environ.get("PORT", 8080))
# ─────────────────────────────────────────────────────────────

MANAUS = timezone(timedelta(hours=-4))
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Mapeamento pasta ClickUp → {grupo, tópico}
PASTA_PARA_TOPICO = {
    "90114672064": {"chat_id": GRUPO_EQUIPE, "thread_id": 347,  "nome": "Mall Reserva Inglesa"},
    "90116580759": {"chat_id": GRUPO_EQUIPE, "thread_id": 354,  "nome": "Horizonte Rio Negro"},
    "90117070701": {"chat_id": GRUPO_EQUIPE, "thread_id": 350,  "nome": "Ideal+ Liberdade 3"},
    "90117196036": {"chat_id": GRUPO_EQUIPE, "thread_id": 356,  "nome": "Elevare+ Vitória"},
    "90117287971": {"chat_id": GRUPO_EQUIPE, "thread_id": 352,  "nome": "Ideal+ Cachoeiras"},
    "90117542148": {"chat_id": GRUPO_EQUIPE, "thread_id": 358,  "nome": "Ideal+ Liberdade 4"},
    "90117793459": {"chat_id": GRUPO_EQUIPE, "thread_id": 357,  "nome": "Elevare+ Flores"},
    "90117793584": {"chat_id": GRUPO_EQUIPE, "thread_id": 359,  "nome": "Elevare+ Torres"},
    "90117989682": {"chat_id": GRUPO_EQUIPE, "thread_id": 1659, "nome": "Ideal+ Jardins"},
    "90118291834": {"chat_id": GRUPO_EQUIPE, "thread_id": 1665, "nome": "Horizonte Ralc"},
    "90118116664": {"chat_id": GRUPO_EQUIPE, "thread_id": 361,  "nome": "Processos"},
    "90116178648": {"chat_id": GRUPO_TODOS,  "thread_id": 47,   "nome": "Treinamentos"},
}

# Vínculos dinâmicos salvos pelo bot (pasta_id → {chat_id, thread_id, nome})
vinculos_dinamicos = {}

# Aguardando vínculo: thread_id novo → aguardando resposta da Nagia
aguardando_vinculo = {}

EQUIPE = {
    7615289681: {
        "nome": "Helena",
        "mention": "[Helena](tg://user?id=7615289681)",
        "intervalo_h": 1,
        "carga_diaria_h": 6,
        "clickup_id": 75487185,
        "drive_name": "helena.metricabim",
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
        "drive_name": None,
    },
}

DRIVE_PARA_TELEGRAM = {
    v["drive_name"]: k for k, v in EQUIPE.items() if v["drive_name"]
}

ORDEM_PLANILHA = [7615289681, 5777049521, 6488820892, 2048504320]
COLUNAS_POR_PESSOA = 6

registros = {}
drive_atividades = {}
drive_monitorando = {}
encerrou_hoje = set()  # {telegram_id} — quem já trabalhou e encerrou hoje
arquivos_vistos = set()

def ja_encerrou_hoje(user_id, sheets):
    """Verifica na planilha se a pessoa já registrou saída hoje"""
    try:
        agora = datetime.now(MANAUS)
        data_hoje = agora.strftime("%d/%m/%Y")
        nome_aba = agora.strftime("%m-%Y")
        resultado = sheets.spreadsheets().values().get(
            spreadsheetId=PLANILHA_ID,
            range=f"'{nome_aba}'!A:A"
        ).execute()
        datas = [r[0] if r else "" for r in resultado.get("values", [])]
        if data_hoje not in datas:
            return False
        linha_idx = datas.index(data_hoje)
        pos = ORDEM_PLANILHA.index(user_id)
        col_saida = 1 + pos * COLUNAS_POR_PESSOA + 1  # coluna Saída (0-based)
        resultado2 = sheets.spreadsheets().values().get(
            spreadsheetId=PLANILHA_ID,
            range=f"'{nome_aba}'!{col_letra(col_saida)}{linha_idx + 1}"
        ).execute()
        valores = resultado2.get("values", [])
        return bool(valores and valores[0])
    except:
        return False

# ─── CREDENCIAIS ─────────────────────────────────────────────
def carregar_credenciais():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        return json.loads(creds_json)
    with open(r"C:\DCE\credenciais.json", "r") as f:
        return json.load(f)

def autenticar_google():
    info = carregar_credenciais()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=[
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/spreadsheets"
        ]
    )
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return drive, sheets

# ─── TELEGRAM ─────────────────────────────────────────────────
def enviar_telegram(chat_id, thread_id, texto):
    payload = {"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"}
    if thread_id:
        payload["message_thread_id"] = thread_id
    try:
        requests.post(f"{BASE_URL}/sendMessage", json=payload)
    except Exception as e:
        print(f"Erro telegram: {e}")

# ─── CLICKUP ─────────────────────────────────────────────────
def clickup_get(endpoint):
    r = requests.get(
        f"https://api.clickup.com/api/v2/{endpoint}",
        headers={"Authorization": CLICKUP_TOKEN}
    )
    return r.json()

def get_pasta_id_da_tarefa(task_id):
    """Retorna o folder_id da tarefa usando project.id"""
    try:
        task = clickup_get(f"task/{task_id}")
        # project.id é o folder_id
        project = task.get("project", {})
        if not project.get("hidden", True):
            return str(project.get("id"))
        folder = task.get("folder", {})
        if not folder.get("hidden", True):
            return str(folder.get("id"))
        return None
    except Exception as e:
        print(f"Erro get_pasta: {e}")
        return None

def notificar_tarefa_criada(task_data):
    """Processa webhook de tarefa criada"""
    task_id = task_data.get("id")
    task_name = task_data.get("name", "Sem nome")
    task_url = task_data.get("url", f"https://app.clickup.com/t/{task_id}")
    assignees = task_data.get("assignees", [])
    responsavel = assignees[0].get("username", "Não atribuído") if assignees else "Não atribuído"

    # Ignora tarefas com status OBJETIVO
    status = task_data.get("status", {}).get("status", "").upper()
    if "OBJETIVO" in status:
        print(f"Ignorando tarefa com status OBJETIVO: {task_name}")
        return

    pasta_id = get_pasta_id_da_tarefa(task_id)
    if not pasta_id:
        return

    # Busca vínculo
    destino = PASTA_PARA_TOPICO.get(pasta_id) or vinculos_dinamicos.get(pasta_id)
    if not destino:
        return

    mensagem = (
        f"🆕 *Nova tarefa criada!*\n"
        f"📌 {task_name}\n"
        f"👤 {responsavel}\n"
        f"🔗 [Abrir no ClickUp]({task_url})"
    )
    enviar_telegram(destino["chat_id"], destino["thread_id"], mensagem)

def verificar_clickup_inicio(telegram_id):
    config = EQUIPE[telegram_id]
    clickup_id = config["clickup_id"]
    mention = config["mention"]
    agora = datetime.now(MANAUS)
    inicio_dia = int(agora.replace(hour=0, minute=0, second=0).timestamp() * 1000)

    # Verifica se tem timer rodando agora
    timer_rodando = clickup_get(f"team/{WORKSPACE_CLICKUP}/time_entries/current?assignee={clickup_id}")
    if timer_rodando.get("data"):
        return  # timer ativo, não avisa

    # Verifica time entries registradas hoje
    data = clickup_get(f"team/{WORKSPACE_CLICKUP}/time_entries?assignee={clickup_id}&start_date={inicio_dia}&end_date={int(agora.timestamp()*1000)}")
    if not data.get("data"):
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"⚠️ {mention}, você ainda não iniciou nenhuma atividade no ClickUp hoje!")

def verificar_clickup_encerramento(telegram_id):
    config = EQUIPE[telegram_id]
    clickup_id = config["clickup_id"]
    mention = config["mention"]
    agora = datetime.now(MANAUS)
    inicio_dia = int(agora.replace(hour=0, minute=0, second=0).timestamp() * 1000)
    data = clickup_get(f"team/{WORKSPACE_CLICKUP}/time_entries?assignee={clickup_id}&start_date={inicio_dia}&end_date={int(agora.timestamp()*1000)}")
    entries = data.get("data", [])
    tarefas_sem = []
    vistas = set()
    for entry in entries:
        task = entry.get("task", {})
        task_id = task.get("id")
        task_name = task.get("name", "Tarefa sem nome")
        if not task_id or task_id in vistas:
            continue
        vistas.add(task_id)
        task_data = clickup_get(f"task/{task_id}")
        custom_fields = task_data.get("custom_fields", [])
        feito_hoje = False
        falta_hoje = False
        for field in custom_fields:
            nome = field.get("name", "").lower()
            updated = field.get("date_updated")
            atualizado_hoje = updated and int(updated) >= inicio_dia
            if "foi feito" in nome or "feito" in nome:
                feito_hoje = atualizado_hoje
            elif "falta" in nome:
                falta_hoje = atualizado_hoje
        if not feito_hoje or not falta_hoje:
            tarefas_sem.append(task_name)
    if tarefas_sem:
        lista = "\n".join([f"• {t}" for t in tarefas_sem])
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"📋 {mention}, preencha os campos nas tarefas:\n{lista}")

# ─── WEBHOOK HTTP SERVER ───────────────────────────────────────
class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silencia logs

    def do_POST(self):
        if self.path != "/webhook/clickup":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            event = data.get("event")
            if event == "taskCreated":
                # ClickUp envia task_id em history_items, não em task{}
                task_id = data.get("task_id")
                if not task_id:
                    history = data.get("history_items", [])
                    if history:
                        task_id = history[0].get("parent_id")
                if task_id:
                    print(f"Task ID: {task_id}")
                    def processar_task():
                        try:
                            time.sleep(20)  # aguarda 20s para tarefa ser configurada
                            task_data = clickup_get(f"task/{task_id}")
                            notificar_tarefa_criada(task_data)
                        except Exception as e:
                            print(f"Erro ao buscar tarefa: {e}")
                    threading.Thread(target=processar_task, daemon=True).start()
                else:
                    print("Task ID não encontrado no payload")
        except Exception as e:
            print(f"Erro webhook: {e}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Metrica Bot online")

def loop_webhook():
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"🌐 Webhook HTTP rodando na porta {PORT}")
    server.serve_forever()

def loop_verificar_clickup_sem_inicio(sheets):
    """Verifica a cada 30min se alguém tem atividade no ClickUp sem ter dado /iniciar"""
    avisos_enviados = set()
    while True:
        time.sleep(1800)
        try:
            agora = datetime.now(MANAUS)
            if agora.weekday() == 6 or agora.hour < 7 or agora.hour >= 21:
                if agora.hour == 0:
                    avisos_enviados.clear()
                    encerrou_hoje.clear()
                continue

            inicio_dia = int(agora.replace(hour=0, minute=0, second=0).timestamp() * 1000)
            data_hoje = agora.strftime("%d/%m/%Y")

            for telegram_id, config in EQUIPE.items():
                if telegram_id == 2048504320:
                    continue
                if telegram_id in drive_monitorando:
                    continue
                if telegram_id in encerrou_hoje:
                    continue
                # Verifica na planilha se já encerrou hoje (sobrevive a reinícios)
                if ja_encerrou_hoje(telegram_id, sheets):
                    encerrou_hoje.add(telegram_id)
                    continue

                chave = f"{telegram_id}_{data_hoje}"
                if chave in avisos_enviados:
                    continue

                clickup_id = config["clickup_id"]
                mention = config["mention"]

                # Verifica se tem timer rodando agora
                timer = clickup_get(f"team/{WORKSPACE_CLICKUP}/time_entries/current?assignee={clickup_id}")
                if timer.get("data"):
                    continue  # timer ativo, não avisa

                data = clickup_get(f"team/{WORKSPACE_CLICKUP}/time_entries?assignee={clickup_id}&start_date={inicio_dia}&end_date={int(agora.timestamp()*1000)}")
                entries = data.get("data", [])

                if entries:
                    avisos_enviados.add(chave)
                    enviar_telegram(GRUPO_EQUIPE, None,
                                   f"⚠️ {mention}, detectei atividade no ClickUp hoje "
                                   f"mas você não marcou início. Use /iniciar!")
        except Exception as e:
            print(f"Erro verificar clickup sem inicio: {e}")

# ─── GOOGLE DRIVE ─────────────────────────────────────────────
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
            pasta = drive.files().get(fileId=pasta_id, fields="name, parents", supportsAllDrives=True).execute()
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

def loop_drive(drive, sheets):
    global arquivos_vistos
    ultimo_check = datetime.now(MANAUS)  # ignora histórico anterior ao deploy
    print(f"📁 Drive monitorando a partir de {ultimo_check.strftime('%H:%M')}")
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

                enviar_telegram(GRUPO_PRIVADO, None,
                               f"📁 *{modificado_por}* salvou\n📂 {caminho}\n📄 {nome_arquivo}\n🕐 {hora}")

                telegram_id = DRIVE_PARA_TELEGRAM.get(modificado_por)
                if telegram_id and telegram_id in drive_monitorando:
                    atividades = drive_atividades.get(telegram_id, {})
                    info = {"arquivo": f"{caminho} / {nome_arquivo}", "hora": hora}
                    if not atividades.get("primeira"):
                        atividades["primeira"] = info
                        drive_atividades[telegram_id] = atividades
                        nome = EQUIPE[telegram_id]["nome"]
                        enviar_telegram(GRUPO_EQUIPE, None,
                                       f"📁 *{nome}* - Primeira atividade Drive: {hora}\n_{caminho} / {nome_arquivo}_")
                    atividades["ultima"] = info
                    drive_atividades[telegram_id] = atividades
                elif telegram_id and telegram_id not in drive_monitorando:
                    if telegram_id in encerrou_hoje:
                        continue
                    # Verifica na planilha se já encerrou hoje (sobrevive a reinícios)
                    if ja_encerrou_hoje(telegram_id, sheets):
                        encerrou_hoje.add(telegram_id)
                        continue
                    agora_manaus = datetime.now(MANAUS)
                    if agora_manaus.weekday() < 6 and 7 <= agora_manaus.hour < 21:
                        data_hoje = agora_manaus.strftime("%d/%m/%Y")
                        chave = f"aviso_drive_{telegram_id}_{data_hoje}"
                        if chave not in arquivos_vistos:
                            arquivos_vistos.add(chave)
                            mention = EQUIPE[telegram_id]["mention"]
                            enviar_telegram(GRUPO_EQUIPE, None,
                                           f"⚠️ {mention}, detectei atividade no Drive às {hora} "
                                           f"mas você não marcou início. Use /iniciar!")

            ultimo_check = agora
            if len(arquivos_vistos) > 2000:
                arquivos_vistos.clear()
                encerrou_hoje.clear()
        except Exception as e:
            print(f"Erro drive: {e}")
        time.sleep(60)

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
        spreadsheetId=PLANILHA_ID, range=f"'{nome_aba}'!A1"
    ).execute()
    if not resultado.get("values"):
        cabecalho = ["Data"]
        for uid in ORDEM_PLANILHA:
            nome = EQUIPE[uid]["nome"]
            cabecalho += [f"{nome} Entrada", f"{nome} Saída", f"{nome} Total",
                         f"{nome} Extra Entrada", f"{nome} Extra Saída", f"{nome} Extra Total"]
        sheets.spreadsheets().values().update(
            spreadsheetId=PLANILHA_ID, range=f"'{nome_aba}'!A1",
            valueInputOption="RAW", body={"values": [cabecalho]}
        ).execute()

def get_ou_criar_linha(sheets, nome_aba, data):
    resultado = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID, range=f"'{nome_aba}'!A:A"
    ).execute()
    datas = [r[0] if r else "" for r in resultado.get("values", [])]
    if data in datas:
        return datas.index(data) + 1
    proxima = max(len(datas) + 1, 2)
    sheets.spreadsheets().values().update(
        spreadsheetId=PLANILHA_ID, range=f"'{nome_aba}'!A{proxima}",
        valueInputOption="RAW", body={"values": [[data]]}
    ).execute()
    return proxima

def atualizar_celulas(sheets, nome_aba, linha, col_inicio, valores):
    col_ini = col_letra(col_inicio)
    col_fim = col_letra(col_inicio + len(valores) - 1)
    sheets.spreadsheets().values().update(
        spreadsheetId=PLANILHA_ID,
        range=f"'{nome_aba}'!{col_ini}{linha}:{col_fim}{linha}",
        valueInputOption="RAW", body={"values": [valores]}
    ).execute()

def salvar_expediente(sheets, data, user_id, entrada, saida, total_min):
    nome_aba = datetime.now(MANAUS).strftime("%m-%Y")
    garantir_aba(sheets, nome_aba)
    linha = get_ou_criar_linha(sheets, nome_aba, data)
    pos = ORDEM_PLANILHA.index(user_id)
    col_inicio = 1 + pos * COLUNAS_POR_PESSOA
    horas = total_min // 60
    mins = total_min % 60
    atualizar_celulas(sheets, nome_aba, linha, col_inicio, [entrada, saida, f"{horas}h{mins:02d}min"])

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
    if user_id == 6488820892:  # Erick: desconta 1h só se trabalhar mais de 6h
        return 60 if minutos_trabalhados > 360 else 0
    # Helena e Micheli: só desconta intervalo se trabalhar mais de 6h
    if minutos_trabalhados <= 360:
        return 0
    intervalo = EQUIPE[user_id].get("intervalo_h") or 0
    return intervalo * 60

def processar_mensagem(msg, sheets):
    texto = msg.get("text", "").strip()
    user_id = msg.get("from", {}).get("id")
    chat_id = str(msg.get("chat", {}).get("id", ""))
    thread_id = msg.get("message_thread_id")

    # Detecta novo tópico criado no grupo equipe
    if chat_id == GRUPO_EQUIPE and msg.get("forum_topic_created"):
        nome_topico = msg["forum_topic_created"]["name"]
        novo_thread_id = msg["message_id"]
        aguardando_vinculo[user_id] = {"thread_id": novo_thread_id, "nome": nome_topico}
        enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS,
                       f"🆕 Novo tópico criado: *{nome_topico}*\n"
                       f"Quer vincular a uma pasta do ClickUp?\n"
                       f"Responda com o ID da pasta (ex: 90114672064) ou *não* para ignorar.")
        return

    # Resposta de vínculo de tópico (no grupo privado)
    if chat_id == GRUPO_PRIVADO and user_id == 2048504320 and user_id in aguardando_vinculo:
        texto_lower = texto.lower().strip()
        if texto_lower == "não" or texto_lower == "nao":
            del aguardando_vinculo[user_id]
            enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS, "Ok, tópico não vinculado.")
            return
        elif texto.isdigit():
            info = aguardando_vinculo.pop(user_id)
            vinculos_dinamicos[texto] = {
                "chat_id": GRUPO_EQUIPE,
                "thread_id": info["thread_id"],
                "nome": info["nome"]
            }
            enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS,
                           f"✅ Tópico *{info['nome']}* vinculado à pasta `{texto}`!")
            return

    # Comandos de ponto — só no grupo equipe, tópico geral
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
    texto_lower = texto.lower()

    if texto_lower == "/iniciar":
        # Aviso de duplicado
        if user_id in registros and "entrada" in registros[user_id]:
            hora_entrada = registros[user_id]["entrada"].strftime("%H:%M")
            enviar_telegram(GRUPO_EQUIPE, None,
                           f"⚠️ {mention}, você já iniciou às {hora_entrada}.\n"
                           f"Use /encerrando primeiro se quiser encerrar o expediente atual.")
            return

        if user_id not in registros:
            registros[user_id] = {}
        registros[user_id]["entrada"] = agora
        registros[user_id]["data"] = agora.strftime("%d/%m/%Y")
        drive_monitorando[user_id] = True
        drive_atividades[user_id] = {"primeira": None, "ultima": None}
        enviar_telegram(GRUPO_EQUIPE, None, f"✅ *{nome}* iniciou às {agora.strftime('%H:%M')}")

        # Verifica ClickUp após 5min
        def checar_clickup_inicio():
            time.sleep(900)  # 15 minutos
            if user_id in drive_monitorando:
                verificar_clickup_inicio(user_id)
        threading.Thread(target=checar_clickup_inicio, daemon=True).start()

        # Verifica Drive após 45min
        def checar_drive_inicio():
            time.sleep(2700)
            if user_id in drive_monitorando:
                atividades = drive_atividades.get(user_id, {})
                if not atividades.get("primeira"):
                    enviar_telegram(GRUPO_EQUIPE, None,
                                   f"⚠️ {mention}, você iniciou há 45 minutos mas ainda não há "
                                   f"nenhuma atividade registrada no Drive!")
        threading.Thread(target=checar_drive_inicio, daemon=True).start()

    elif texto_lower == "/encerrando":
        if user_id not in registros or "entrada" not in registros[user_id]:
            enviar_telegram(GRUPO_EQUIPE, None, f"⚠️ {mention}, use /iniciar primeiro.")
            return
        entrada = registros[user_id]["entrada"]
        minutos_brutos = int((agora - entrada).total_seconds() / 60)
        intervalo_min = calcular_intervalo(user_id, minutos_brutos)
        minutos_liquidos = max(0, minutos_brutos - intervalo_min)
        horas = minutos_liquidos // 60
        mins = minutos_liquidos % 60
        drive_monitorando.pop(user_id, None)
        encerrou_hoje.add(user_id)
        ultima_drive = drive_atividades.get(user_id, {}).get("ultima")
        msg_drive = f"\n📁 Última atividade Drive: {ultima_drive['hora']}\n_{ultima_drive['arquivo']}_" if ultima_drive else ""
        try:
            salvar_expediente(sheets, registros[user_id]["data"], user_id,
                            entrada.strftime("%H:%M"), agora.strftime("%H:%M"), minutos_liquidos)
        except Exception as e:
            print(f"Erro sheets: {e}")
        registros[user_id] = {"data": agora.strftime("%d/%m/%Y")}
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"👋 *{nome}* encerrou às {agora.strftime('%H:%M')}\n"
                       f"⏱ Trabalhado: *{horas}h{mins:02d}min*{msg_drive}")
        def checar_clickup_enc():
            time.sleep(300)
            verificar_clickup_encerramento(user_id)
        threading.Thread(target=checar_clickup_enc, daemon=True).start()

    elif texto_lower == "/iniciar extra":
        if user_id not in registros:
            registros[user_id] = {}
        registros[user_id]["entrada_extra"] = agora
        if "data" not in registros[user_id]:
            registros[user_id]["data"] = agora.strftime("%d/%m/%Y")
        enviar_telegram(GRUPO_EQUIPE, None, f"⭐ *{nome}* iniciou hora extra às {agora.strftime('%H:%M')}")

    elif texto_lower == "/encerrando extra":
        if user_id not in registros or "entrada_extra" not in registros[user_id]:
            enviar_telegram(GRUPO_EQUIPE, None, f"⚠️ {mention}, use /iniciar extra primeiro.")
            return
        entrada_extra = registros[user_id]["entrada_extra"]
        extra_min = int((agora - entrada_extra).total_seconds() / 60)
        data = registros[user_id].get("data", agora.strftime("%d/%m/%Y"))
        try:
            salvar_extra(sheets, data, user_id,
                        entrada_extra.strftime("%H:%M"), agora.strftime("%H:%M"), extra_min)
        except Exception as e:
            print(f"Erro sheets extra: {e}")
        del registros[user_id]["entrada_extra"]
        extra_h = extra_min // 60
        extra_m = extra_min % 60
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"⭐ *{nome}* encerrou hora extra\n⏱ Extra: *{extra_h}h{extra_m:02d}min*")

# ─── RELATÓRIO MENSAL ─────────────────────────────────────────
def gerar_relatorio_mensal(sheets):
    agora = datetime.now(MANAUS)
    mes_anterior = agora.replace(day=1) - timedelta(days=1)
    nome_aba = mes_anterior.strftime("%m-%Y")
    mes_str = mes_anterior.strftime("%m/%Y")
    try:
        resultado = sheets.spreadsheets().values().get(
            spreadsheetId=PLANILHA_ID, range=f"'{nome_aba}'!A1:Z1000"
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
        "chat_id": GRUPO_PRIVADO, "message_thread_id": THREAD_RELATORIOS,
        "caption": f"📊 Relatório de ponto — {mes_str}"
    }, files={"document": (f"ponto_{nome_aba}.csv", csv_bytes, "text/csv")})

# ─── MAIN ─────────────────────────────────────────────────────
def main():
    print("🚀 Métrica Bot iniciado!")
    drive, sheets = autenticar_google()
    print("✅ Google conectado")

    threading.Thread(target=loop_drive, args=(drive, sheets), daemon=True).start()
    threading.Thread(target=loop_webhook, daemon=True).start()
    threading.Thread(target=loop_verificar_clickup_sem_inicio, args=(sheets,), daemon=True).start()

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
                    processar_mensagem(msg, sheets)
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
