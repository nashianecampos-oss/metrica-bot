import os
import json
import time
import requests
import io
import threading
import re
import unicodedata
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
SESSION_TG = requests.Session()  # reutiliza a conexão e reduz tráfego de rede

# Janela de silêncio (horário de Manaus): sem avisos/cobranças automáticas entre 11h e 14h (almoço)
JANELA_SILENCIO = (11, 14)

def em_janela_silencio(agora=None):
    agora = agora or datetime.now(MANAUS)
    return JANELA_SILENCIO[0] <= agora.hour < JANELA_SILENCIO[1]

def aguardar_fim_silencio():
    """Se estiver na janela de silêncio, espera até o fim dela (usado em verificações atrasadas)."""
    agora = datetime.now(MANAUS)
    if not em_janela_silencio(agora):
        return
    fim = agora.replace(hour=JANELA_SILENCIO[1], minute=0, second=30, microsecond=0)
    time.sleep(max(0, (fim - agora).total_seconds()))

# Mapeamento pasta ClickUp → {grupo, tópico}
PASTA_PARA_TOPICO = {
    "90114672064": {"chat_id": GRUPO_EQUIPE, "thread_id": 347,  "nome": "Mall Reserva Inglesa"},
    "90116580759": {"chat_id": GRUPO_EQUIPE, "thread_id": 354,  "nome": "Horizonte Rio Negro"},
    "90117070701": {"chat_id": GRUPO_EQUIPE, "thread_id": 355,  "nome": "Ideal+ Liberdade 3"},
    "90117196036": {"chat_id": GRUPO_EQUIPE, "thread_id": 356,  "nome": "Elevare+ Vitória"},
    "90117287971": {"chat_id": GRUPO_EQUIPE, "thread_id": 352,  "nome": "Ideal+ Cachoeiras"},
    "90117542148": {"chat_id": GRUPO_EQUIPE, "thread_id": 358,  "nome": "Ideal+ Liberdade 4"},
    "90117793459": {"chat_id": GRUPO_EQUIPE, "thread_id": 357,  "nome": "Elevare+ Flores"},
    "90117793584": {"chat_id": GRUPO_EQUIPE, "thread_id": 359,  "nome": "Elevare+ Torres"},
    "90117989682": {"chat_id": GRUPO_EQUIPE, "thread_id": 1659, "nome": "Ideal+ Jardins"},
    "90118291834": {"chat_id": GRUPO_EQUIPE, "thread_id": 1665, "nome": "Horizonte Ralc"},
    "90118116664": {"chat_id": GRUPO_EQUIPE, "thread_id": 361,  "nome": "Processos"},
    "90116178648": {"chat_id": GRUPO_TODOS,  "thread_id": 47,   "nome": "Treinamentos"},
    "90118020302": {"chat_id": GRUPO_EQUIPE, "thread_id": 9879, "nome": "Ideal+ Liberdade 5"},
    "90118019983": {"chat_id": GRUPO_EQUIPE, "thread_id": 9878, "nome": "Ideal+ Conquista 2"},
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
        "drive_emails": ["helena.metricabim@gmail.com"],
    },
    5777049521: {
        "nome": "Micheli",
        "mention": "[Micheli](tg://user?id=5777049521)",
        "intervalo_h": 0,
        "carga_diaria_h": 6,
        "clickup_id": 194434554,
        "drive_name": "michelemaria792",
        "drive_emails": ["michelemaria792@gmail.com"],
    },
    6488820892: {
        "nome": "Erick",
        "mention": "[Erick](tg://user?id=6488820892)",
        "intervalo_h": None,
        "carga_diaria_h": None,
        "clickup_id": 296428627,
        "drive_name": "erickbrunol.n",
        "drive_emails": ["erickmetrica@gmail.com"],
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
DRIVE_EMAIL_PARA_TELEGRAM = {
    email.lower(): k for k, v in EQUIPE.items() for email in v.get("drive_emails", [])
}

def identificar_pessoa_drive(usuario):
    """Reconhece quem salvou o arquivo: primeiro pelo e-mail da conta Google, depois pelo nome de exibição."""
    email = (usuario.get("emailAddress") or "").strip().lower()
    if email in DRIVE_EMAIL_PARA_TELEGRAM:
        return DRIVE_EMAIL_PARA_TELEGRAM[email]
    return DRIVE_PARA_TELEGRAM.get(usuario.get("displayName", ""))

ORDEM_PLANILHA = [7615289681, 5777049521, 6488820892, 2048504320]
COLUNAS_POR_PESSOA = 6

data_estado = None  # data (dd/mm/aaaa) a que se refere o estado em memória; muda à meia-noite
registros = {}
drive_atividades = {}
drive_monitorando = {}
modo_cliente = set()  # {telegram_id} — pessoa em pasta de cliente, ignora inatividade no Drive
drive_ultimo_ts = {}     # {telegram_id: datetime} última atividade no Drive (ou início do expediente)
drive_ultimo_aviso = {}  # {telegram_id: datetime} último aviso de inatividade enviado
INTERVALO_INATIVIDADE_MIN = 45  # primeiro aviso: 45 min sem atividade no Drive
INTERVALO_REPETICAO_MIN = 5     # depois do primeiro aviso, repete de 5 em 5 min até a pessoa salvar
drive_avisos_encerrados = set()  # {telegram_id} — encerrou expediente ou extra: sem avisos de Drive/ClickUp até /iniciar ou meia-noite

def sem_avisos_de_atividade(telegram_id):
    """True se a pessoa encerrou expediente/extra ou está em hora extra: nenhum aviso de atividade deve ser enviado."""
    if telegram_id in drive_avisos_encerrados:
        return True
    return bool(registros.get(telegram_id, {}).get("entrada_extra"))  # hora extra em andamento
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

def ja_iniciou_hoje(user_id, sheets):
    """Verifica na planilha se a pessoa já registrou entrada hoje"""
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
        col_entrada = 1 + pos * COLUNAS_POR_PESSOA  # coluna Entrada
        resultado2 = sheets.spreadsheets().values().get(
            spreadsheetId=PLANILHA_ID,
            range=f"'{nome_aba}'!{col_letra(col_entrada)}{linha_idx + 1}"
        ).execute()
        valores = resultado2.get("values", [])
        return bool(valores and valores[0])
    except:
        return False

def recuperar_entrada_extra_hoje(user_id, sheets):
    """Se a extra foi iniciada mas o bot reiniciou (perdeu o estado em memória),
    recupera o horário de início a partir da planilha (coluna Extra Entrada)."""
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
            return None
        linha_idx = datas.index(data_hoje)
        pos = ORDEM_PLANILHA.index(user_id)
        col_extra_entrada = 1 + pos * COLUNAS_POR_PESSOA + 3  # coluna Extra Entrada
        resultado2 = sheets.spreadsheets().values().get(
            spreadsheetId=PLANILHA_ID,
            range=f"'{nome_aba}'!{col_letra(col_extra_entrada)}{linha_idx + 1}"
        ).execute()
        valores = resultado2.get("values", [])
        if not (valores and valores[0]):
            return None
        hora_str = valores[0][0]
        hora, minuto = map(int, hora_str.split(":"))
        return agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    except:
        return None

# ─── RESTAURAÇÃO DO ESTADO DO DIA (após reinício/deploy) ──────
def _hora_para_datetime(texto, base):
    try:
        h, m = map(int, str(texto).strip().split(":"))
        return base.replace(hour=h, minute=m, second=0, microsecond=0)
    except Exception:
        return None

def ler_registro_do_dia(sheets):
    """Lê a linha de hoje da planilha: {user_id: {entrada, saida, extra_entrada, extra_saida}} (datetime ou None)."""
    agora = datetime.now(MANAUS)
    data_hoje = agora.strftime("%d/%m/%Y")
    nome_aba = agora.strftime("%m-%Y")
    r = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID, range=f"'{nome_aba}'!A:A"
    ).execute()
    datas = [(l[0] if l else "") for l in r.get("values", [])]
    if data_hoje not in datas:
        return {}
    n = datas.index(data_hoje) + 1
    r2 = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID, range=f"'{nome_aba}'!A{n}:Z{n}"
    ).execute()
    linha = (r2.get("values") or [[]])[0]
    def cel(i):
        return linha[i] if i < len(linha) else ""
    dados = {}
    for pos, uid in enumerate(ORDEM_PLANILHA):
        base = 1 + pos * COLUNAS_POR_PESSOA
        dados[uid] = {
            "entrada": _hora_para_datetime(cel(base), agora),
            "saida": _hora_para_datetime(cel(base + 1), agora),
            "extra_entrada": _hora_para_datetime(cel(base + 3), agora),
            "extra_saida": _hora_para_datetime(cel(base + 4), agora),
        }
    return dados

def restaurar_estado_do_dia(sheets):
    """Depois de um deploy/reinício, reconstrói da planilha quem está trabalhando, quem já encerrou e quem
    está em hora extra, para ninguém precisar repetir comandos. Devolve um resumo em texto."""
    agora = datetime.now(MANAUS)
    data_hoje = agora.strftime("%d/%m/%Y")
    resumo = []
    for uid, d in ler_registro_do_dia(sheets).items():
        nome = EQUIPE.get(uid, {}).get("nome", str(uid))
        ent, sai, ex_ent, ex_sai = d["entrada"], d["saida"], d["extra_entrada"], d["extra_saida"]
        if ent:
            if sai is None or sai <= ent:      # entrada sem saída (ou nova entrada depois da saída): em expediente
                registros.setdefault(uid, {}).update({"entrada": ent, "data": data_hoje})
                drive_monitorando[uid] = True
                drive_atividades[uid] = {"primeira": {"arquivo": "registrada antes do reinício", "hora": "--:--"},
                                         "ultima": None}
                drive_ultimo_ts[uid] = agora    # a contagem de 45 min recomeça agora
                drive_ultimo_aviso.pop(uid, None)
                drive_avisos_encerrados.discard(uid)
                resumo.append(f"{nome}: em expediente desde {ent.strftime('%H:%M')}")
            else:                               # já encerrou
                registros.setdefault(uid, {}).update({"data": data_hoje})
                drive_avisos_encerrados.add(uid)
                encerrou_hoje.add(uid)
                resumo.append(f"{nome}: encerrou às {sai.strftime('%H:%M')}")
        if ex_ent:
            if ex_sai is None or ex_sai <= ex_ent:
                registros.setdefault(uid, {}).update({"entrada_extra": ex_ent, "data": data_hoje})
                resumo.append(f"{nome}: hora extra em andamento desde {ex_ent.strftime('%H:%M')}")
            else:
                drive_avisos_encerrados.add(uid)
    return resumo

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
        r = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=20)
        if r.status_code != 200:
            print(f"Telegram recusou ({r.status_code}) chat={chat_id} thread={thread_id}: {r.text[:200]}")
    except Exception as e:
        print(f"Erro telegram: {e}")

# ─── CLICKUP ─────────────────────────────────────────────────
def clickup_get(endpoint):
    r = requests.get(
        f"https://api.clickup.com/api/v2/{endpoint}",
        headers={"Authorization": CLICKUP_TOKEN}
    )
    return r.json()

ABA_VINCULOS = "vinculos"  # aba da planilha onde ficam os vínculos pasta → tópico (sobrevivem a reinícios)

def _garantir_aba_vinculos(sheets):
    meta = sheets.spreadsheets().get(spreadsheetId=PLANILHA_ID).execute()
    abas = [sh["properties"]["title"] for sh in meta["sheets"]]
    if ABA_VINCULOS not in abas:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=PLANILHA_ID,
            body={"requests": [{"addSheet": {"properties": {"title": ABA_VINCULOS}}}]}
        ).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=PLANILHA_ID, range=f"'{ABA_VINCULOS}'!A1",
            valueInputOption="RAW", body={"values": [["pasta_id", "chat_id", "thread_id", "nome"]]}
        ).execute()

def carregar_vinculos(sheets):
    """Recarrega da planilha os vínculos pasta → tópico criados pelo bot."""
    _garantir_aba_vinculos(sheets)
    r = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID, range=f"'{ABA_VINCULOS}'!A2:D1000"
    ).execute()
    n = 0
    for linha in r.get("values", []):
        if len(linha) >= 3 and linha[0] and linha[2]:
            try:
                vinculos_dinamicos[str(linha[0]).strip()] = {
                    "chat_id": str(linha[1]).strip(),
                    "thread_id": int(linha[2]),
                    "nome": linha[3] if len(linha) > 3 else "",
                }
                n += 1
            except ValueError:
                pass
    print(f"🔗 {n} vínculo(s) pasta→tópico carregado(s) da planilha")

def salvar_vinculo(sheets, pasta_id, destino):
    """Guarda o vínculo na planilha (atualiza a linha se a pasta já existir)."""
    vinculos_dinamicos[pasta_id] = destino
    _garantir_aba_vinculos(sheets)
    r = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID, range=f"'{ABA_VINCULOS}'!A:A"
    ).execute()
    ids = [(l[0] if l else "") for l in r.get("values", [])]
    linha = [pasta_id, str(destino["chat_id"]), destino["thread_id"], destino["nome"]]
    if pasta_id in ids:
        n = ids.index(pasta_id) + 1
        sheets.spreadsheets().values().update(
            spreadsheetId=PLANILHA_ID, range=f"'{ABA_VINCULOS}'!A{n}:D{n}",
            valueInputOption="RAW", body={"values": [linha]}
        ).execute()
    else:
        sheets.spreadsheets().values().append(
            spreadsheetId=PLANILHA_ID, range=f"'{ABA_VINCULOS}'!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [linha]}
        ).execute()

def registrar_vinculo(sheets, pasta_id, thread_id, nome):
    """Confere a pasta no ClickUp, guarda o vínculo e devolve (ok, mensagem para a Nagia)."""
    nome_pasta = "?"
    try:
        pasta = clickup_get(f"folder/{pasta_id}")
        if str(pasta.get("id")) != pasta_id:
            return False, (f"⚠️ Não encontrei a pasta `{pasta_id}` no ClickUp. Confira o ID: "
                           f"é o número depois de /f/ na URL da pasta.")
        nome_pasta = str(pasta.get("name", "?"))
        for ch in "_*`[":
            nome_pasta = nome_pasta.replace(ch, " ")
    except Exception as e:
        print(f"Erro ao conferir pasta {pasta_id}: {e}")
    destino = {"chat_id": GRUPO_EQUIPE, "thread_id": int(thread_id), "nome": nome}
    try:
        salvar_vinculo(sheets, pasta_id, destino)
        aviso = ""
    except Exception as e:
        print(f"Erro ao salvar vínculo na planilha: {e}")
        vinculos_dinamicos[pasta_id] = destino
        aviso = "\n⚠️ Não consegui salvar na planilha: se o bot reiniciar, este vínculo se perde."
    return True, f"✅ Tópico *{nome}* vinculado à pasta `{pasta_id}` ({nome_pasta}).{aviso}"

# ─── APRENDIZADO AUTOMÁTICO DE TÓPICOS E VÍNCULO POR NOME ──────
ABA_TOPICOS = "topicos"      # aba da planilha: tópicos do grupo da equipe que o bot já viu (thread_id, nome)
topicos_conhecidos = {}      # {thread_id: nome}
sheets_global = None         # preenchido no main(); usado pelo webhook
pastas_avisadas = set()      # pastas sem tópico que já geraram aviso nesta execução

def _limpar_md(texto):
    for ch in "_*`[":
        texto = str(texto).replace(ch, " ")
    return texto

def _garantir_aba_generica(sheets, titulo, cabecalho):
    meta = sheets.spreadsheets().get(spreadsheetId=PLANILHA_ID).execute()
    if titulo not in [sh["properties"]["title"] for sh in meta["sheets"]]:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=PLANILHA_ID,
            body={"requests": [{"addSheet": {"properties": {"title": titulo}}}]}
        ).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=PLANILHA_ID, range=f"'{titulo}'!A1",
            valueInputOption="RAW", body={"values": [cabecalho]}
        ).execute()

def carregar_topicos(sheets):
    """Recarrega da planilha os tópicos que o bot já aprendeu."""
    _garantir_aba_generica(sheets, ABA_TOPICOS, ["thread_id", "nome"])
    r = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID, range=f"'{ABA_TOPICOS}'!A2:D1000"
    ).execute()
    for linha in r.get("values", []):
        if len(linha) >= 2 and linha[0]:
            try:
                topicos_conhecidos[int(linha[0])] = linha[1]
            except ValueError:
                pass
    print(f"🧭 {len(topicos_conhecidos)} tópico(s) conhecido(s) carregado(s) da planilha")

def _salvar_topico(sheets, thread_id, nome):
    _garantir_aba_generica(sheets, ABA_TOPICOS, ["thread_id", "nome"])
    r = sheets.spreadsheets().values().get(
        spreadsheetId=PLANILHA_ID, range=f"'{ABA_TOPICOS}'!A:A"
    ).execute()
    ids = [(l[0] if l else "") for l in r.get("values", [])]
    linha = [str(thread_id), nome]
    if str(thread_id) in ids:
        n = ids.index(str(thread_id)) + 1
        sheets.spreadsheets().values().update(
            spreadsheetId=PLANILHA_ID, range=f"'{ABA_TOPICOS}'!A{n}:B{n}",
            valueInputOption="RAW", body={"values": [linha]}
        ).execute()
    else:
        sheets.spreadsheets().values().append(
            spreadsheetId=PLANILHA_ID, range=f"'{ABA_TOPICOS}'!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [linha]}
        ).execute()

def aprender_topico(sheets, msg):
    """Aprende o nome dos tópicos só lendo as conversas do grupo da equipe (nada manual)."""
    thread_id = msg.get("message_thread_id")
    if not thread_id or thread_id == 1:
        return
    nome = ((msg.get("forum_topic_edited") or {}).get("name")
            or (msg.get("forum_topic_created") or {}).get("name"))
    if not nome:
        # mensagem comum no tópico: só serve para descobrir tópicos ainda desconhecidos
        if thread_id in topicos_conhecidos:
            return
        nome = ((msg.get("reply_to_message") or {}).get("forum_topic_created") or {}).get("name")
    if not nome or topicos_conhecidos.get(thread_id) == nome:
        return
    topicos_conhecidos[thread_id] = nome
    try:
        _salvar_topico(sheets, thread_id, nome)
    except Exception as e:
        print(f"Erro ao salvar tópico {thread_id}: {e}")

def _tokens_nome(nome, remover_sufixo=True):
    """Palavras-chave do nome, sem acento/pontuação e sem o ' - NOME DA PESSOA' dos tópicos."""
    n = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode().upper()
    if remover_sufixo:
        n = re.sub(r"\s+-\s+.*$", "", n)
    n = re.sub(r"[^A-Z0-9]+", " ", n)
    return {((t.lstrip("0") or "0") if t.isdigit() else t) for t in n.split()}

def tentar_vincular_por_nome(pasta_id, nome_pasta):
    """Liga a pasta ao tópico de nome equivalente; só vincula se houver exatamente um candidato."""
    usados = {d["thread_id"] for d in list(PASTA_PARA_TOPICO.values()) + list(vinculos_dinamicos.values())
              if str(d["chat_id"]) == str(GRUPO_EQUIPE)}
    alvo = _tokens_nome(nome_pasta, remover_sufixo=False)
    exatos, parciais = [], []
    for thread_id, nome in topicos_conhecidos.items():
        if thread_id in usados:
            continue
        t = _tokens_nome(nome)
        if len(t) < 2:
            continue
        if t == alvo:
            exatos.append((thread_id, nome))
        elif t <= alvo:
            parciais.append((thread_id, nome))
    candidatos = exatos if exatos else parciais
    if len(candidatos) != 1:
        return None
    thread_id, nome = candidatos[0]
    destino = {"chat_id": GRUPO_EQUIPE, "thread_id": thread_id, "nome": nome}
    try:
        salvar_vinculo(sheets_global, pasta_id, destino)
    except Exception as e:
        print(f"Erro ao salvar vínculo automático: {e}")
        vinculos_dinamicos[pasta_id] = destino
    enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS,
                   f"🔗 Vinculei automaticamente a pasta *{_limpar_md(nome_pasta)}* ao tópico *{_limpar_md(nome)}*. "
                   f"Se estiver errado, use /vincular ID_DA_PASTA dentro do tópico certo.")
    return destino

def avisar_pasta_sem_topico(pasta_id, nome_pasta, tarefa):
    if pasta_id in pastas_avisadas:
        return
    pastas_avisadas.add(pasta_id)
    enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS,
                   f"⚠️ Criaram a tarefa *{_limpar_md(tarefa)}* na pasta *{_limpar_md(nome_pasta)}* (`{pasta_id}`), "
                   f"mas não encontrei tópico para ela. Se o tópico existe, basta alguém escrever nele: o bot "
                   f"aprende o nome e vincula sozinho na próxima tarefa. Ou use /vincular {pasta_id} dentro do tópico.")

def get_pasta_da_tarefa(task_id):
    """Retorna (id, nome) da pasta da tarefa, ou (None, None)."""
    try:
        task = clickup_get(f"task/{task_id}")
        for chave in ("project", "folder"):
            obj = task.get(chave, {}) or {}
            if not obj.get("hidden", True) and obj.get("id"):
                return str(obj.get("id")), str(obj.get("name", ""))
        return None, None
    except Exception as e:
        print(f"Erro get_pasta: {e}")
        return None, None

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

    pasta_id, nome_pasta = get_pasta_da_tarefa(task_id)
    print(f"Pasta ID: {pasta_id} | Tarefa: {task_name}")
    if not pasta_id:
        print(f"Pasta não encontrada para tarefa: {task_name}")
        return

    # Busca vínculo
    destino = PASTA_PARA_TOPICO.get(pasta_id) or vinculos_dinamicos.get(pasta_id)
    if not destino:
        destino = tentar_vincular_por_nome(pasta_id, nome_pasta)
    print(f"Destino: {destino}")
    if not destino:
        print(f"Pasta {pasta_id} não mapeada")
        avisar_pasta_sem_topico(pasta_id, nome_pasta, task_name)
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
    # inicio_dia em UTC (ClickUp usa UTC em milliseconds)
    inicio_dia_manaus = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_dia_utc = int(inicio_dia_manaus.astimezone(timezone.utc).timestamp() * 1000)
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
            nome_field = field.get("name", "").lower()
            updated = field.get("date_updated")
            # Verifica se foi atualizado hoje em UTC (ClickUp usa UTC)
            atualizado_hoje = updated and int(updated) >= inicio_dia_utc
            # Também aceita se tem valor preenchido (independente de quando foi atualizado)
            valor = field.get("value")
            tem_valor = bool(valor and str(valor).strip())
            if "foi feito" in nome_field or (nome_field == "o que foi feito?" ):
                feito_hoje = atualizado_hoje or tem_valor
            elif "falta" in nome_field:
                falta_hoje = atualizado_hoje or tem_valor
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
            if agora.weekday() == 6 or agora.hour < 6 or agora.hour >= 21:
                if agora.hour == 0:
                    avisos_enviados.clear()
                    encerrou_hoje.clear()
                continue
            if em_janela_silencio(agora):
                continue  # almoço: sem avisos

            inicio_dia = int(agora.replace(hour=0, minute=0, second=0).timestamp() * 1000)
            data_hoje = agora.strftime("%d/%m/%Y")

            for telegram_id, config in EQUIPE.items():
                if telegram_id == 2048504320:
                    continue
                if telegram_id in drive_monitorando:
                    continue
                if telegram_id in encerrou_hoje or sem_avisos_de_atividade(telegram_id):
                    continue
                # Verifica na planilha se já encerrou ou iniciou hoje (sobrevive a reinícios)
                if ja_encerrou_hoje(telegram_id, sheets):
                    encerrou_hoje.add(telegram_id)
                    continue
                if ja_iniciou_hoje(telegram_id, sheets):
                    continue  # já iniciou hoje, não avisa

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

def loop_virada_do_dia():
    """À meia-noite zera todo o estado do dia (quem estava em expediente, hora extra aberta, modo cliente...).
    Avisa a Nagia quem ficou sem /encerrando, para a saída em branco na planilha não passar despercebida."""
    global data_estado
    while True:
        time.sleep(60)
        try:
            hoje = datetime.now(MANAUS).strftime("%d/%m/%Y")
            if data_estado is None:
                data_estado = hoje
                continue
            if hoje == data_estado:
                continue
            esquecidos = [EQUIPE[u]["nome"] for u in list(drive_monitorando) if u in EQUIPE]
            extras = [EQUIPE[u]["nome"] for u, d in list(registros.items()) if u in EQUIPE and d.get("entrada_extra")]
            if esquecidos:
                enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS,
                               f"⚠️ Ficaram sem /encerrando em {data_estado}: {', '.join(esquecidos)}. "
                               f"A saída desse dia ficou em branco na planilha.")
            if extras:
                enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS,
                               f"⚠️ Hora extra aberta sem /encerrando extra em {data_estado}: {', '.join(extras)}. "
                               f"O fim da extra desse dia ficou em branco na planilha.")
            for estado in (registros, drive_monitorando, drive_ultimo_ts, drive_ultimo_aviso, drive_atividades):
                estado.clear()
            for estado in (modo_cliente, encerrou_hoje, drive_avisos_encerrados):
                estado.clear()
            data_estado = hoje
            print(f"🌅 Novo dia ({hoje}): estado anterior zerado")
        except Exception as e:
            print(f"Erro na virada do dia: {e}")

def loop_aviso_inatividade_drive():
    """A cada minuto, avisa quem está em expediente e passou 45 min sem salvar arquivo no Drive.
    Depois do primeiro aviso, repete de 5 em 5 min até a pessoa salvar um arquivo (ou usar /cliente
    ou /encerrando). Só compara horários na memória."""
    while True:
        time.sleep(60)
        try:
            agora = datetime.now(MANAUS)
            if agora.hour == 0:
                drive_avisos_encerrados.clear()  # novo dia
            if agora.weekday() == 6 or agora.hour < 6 or agora.hour >= 21:
                continue
            if em_janela_silencio(agora):
                # almoço: congela a contagem; ela recomeça do zero ao fim da janela
                for tid in list(drive_monitorando.keys()):
                    drive_ultimo_ts[tid] = agora
                    drive_ultimo_aviso.pop(tid, None)
                continue
            for telegram_id in list(drive_monitorando.keys()):
                config = EQUIPE.get(telegram_id)
                if not config or not (config.get("drive_name") or config.get("drive_emails")):
                    continue  # sem conta do Drive mapeada (ex.: Nagia): não dá para monitorar
                if telegram_id in modo_cliente:
                    continue  # em pasta de cliente (comando /cliente)
                if sem_avisos_de_atividade(telegram_id):
                    continue  # encerrou expediente/extra ou está em hora extra
                ultimo = drive_ultimo_ts.get(telegram_id)
                if not ultimo:
                    continue
                if ultimo.date() != agora.date():
                    continue  # sobra de outro dia: nunca cobrar por isso
                ultimo_aviso = drive_ultimo_aviso.get(telegram_id)
                sem_atividade = int((agora - ultimo).total_seconds() // 60)
                if ultimo_aviso is None:
                    # ainda não avisou: espera 45 min sem atividade
                    devido = (agora - ultimo).total_seconds() >= INTERVALO_INATIVIDADE_MIN * 60
                    texto = (f"⚠️ {config['mention']}, faz {sem_atividade} minutos que não há atividade no Drive. "
                             f"Salve o arquivo em que está trabalhando. "
                             f"Se estiver em pasta de cliente, use /cliente.")
                else:
                    # já avisou: repete a cada 5 min (tolerância de 10 s por causa do ciclo de 1 min)
                    devido = (agora - ultimo_aviso).total_seconds() >= INTERVALO_REPETICAO_MIN * 60 - 10
                    texto = (f"⚠️ {config['mention']}, ainda sem atividade no Drive (faz {sem_atividade} minutos). "
                             f"Salve o arquivo em que está trabalhando ou use /cliente.")
                if devido:
                    drive_ultimo_aviso[telegram_id] = agora
                    enviar_telegram(GRUPO_EQUIPE, None, texto)
        except Exception as e:
            print(f"Erro aviso inatividade drive: {e}")

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
                # chave = arquivo + horário da modificação: cada nova gravação do mesmo arquivo conta como atividade
                chave_gravacao = f"{file_id}:{arquivo.get('modifiedTime', '')}"
                if chave_gravacao in arquivos_vistos:
                    continue
                arquivos_vistos.add(chave_gravacao)
                nome_arquivo = arquivo["name"]
                usuario_drive = arquivo.get("lastModifyingUser", {})
                modificado_por = usuario_drive.get("displayName", "")
                email_drive = usuario_drive.get("emailAddress", "")
                momento = datetime.fromisoformat(
                    arquivo["modifiedTime"].replace("Z", "+00:00")
                ).astimezone(MANAUS)
                hora = momento.strftime("%H:%M")
                caminho = get_caminho_drive(drive, arquivo.get("parents", []))

                telegram_id = identificar_pessoa_drive(usuario_drive)
                marca_conta = "" if telegram_id else " ❓ conta não vinculada a ninguém da equipe"
                enviar_telegram(GRUPO_PRIVADO, None,
                               f"📁 *{modificado_por}*{f' ({email_drive})' if email_drive else ''} salvou{marca_conta}\n📂 {caminho}\n📄 {nome_arquivo}\n🕐 {hora}")

                if telegram_id and telegram_id in drive_monitorando:
                    # Remove do modo cliente se estava lá
                    modo_cliente.discard(telegram_id)
                    # Zera o contador de inatividade
                    drive_ultimo_ts[telegram_id] = max(momento, drive_ultimo_ts.get(telegram_id, momento))
                    drive_ultimo_aviso.pop(telegram_id, None)
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
                    if telegram_id in encerrou_hoje or sem_avisos_de_atividade(telegram_id):
                        continue
                    if telegram_id in modo_cliente:
                        # Voltou ao Drive interno — remove modo cliente
                        modo_cliente.discard(telegram_id)
                        continue
                    # Verifica na planilha se já encerrou ou iniciou hoje (sobrevive a reinícios)
                    if ja_encerrou_hoje(telegram_id, sheets):
                        encerrou_hoje.add(telegram_id)
                        continue
                    if ja_iniciou_hoje(telegram_id, sheets):
                        continue  # já iniciou hoje, não avisa
                    agora_manaus = datetime.now(MANAUS)
                    if agora_manaus.weekday() < 6 and 6 <= agora_manaus.hour < 21 and not em_janela_silencio(agora_manaus):
                        data_hoje = agora_manaus.strftime("%d/%m/%Y")
                        chave = f"aviso_drive_{telegram_id}_{data_hoje}"
                        if chave not in arquivos_vistos:
                            arquivos_vistos.add(chave)
                            mention = EQUIPE[telegram_id]["mention"]
                            enviar_telegram(GRUPO_EQUIPE, None,
                                           f"⚠️ {mention}, detectei atividade no Drive às {hora} "
                                           f"mas você não marcou início. Use /iniciar!")

            ultimo_check = agora
            if len(arquivos_vistos) > 5000:
                arquivos_vistos.clear()
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

    # Aprende os tópicos do grupo da equipe só lendo as mensagens
    if chat_id == GRUPO_EQUIPE:
        try:
            aprender_topico(sheets, msg)
        except Exception as e:
            print(f"Erro ao aprender tópico: {e}")

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
        elif texto.strip().isdigit():
            info = aguardando_vinculo[user_id]
            ok, resposta = registrar_vinculo(sheets, texto.strip(), info["thread_id"], info["nome"])
            if ok:
                aguardando_vinculo.pop(user_id, None)
            enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS, resposta)
            return

    # /vincular <ID da pasta>: vincula o tópico em que o comando foi enviado (só a Nagia)
    if (chat_id == GRUPO_EQUIPE and user_id == 2048504320 and texto.split()
            and texto.split()[0].split("@")[0].lower() == "/vincular"):
        partes = texto.split()
        if thread_id is None or thread_id == 1 or len(partes) < 2 or not partes[1].isdigit():
            enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS,
                           "ℹ️ Use */vincular ID_DA_PASTA* dentro do tópico do projeto (não no Geral).")
            return
        nome_topico = ((msg.get("reply_to_message") or {}).get("forum_topic_created") or {}).get("name") \
                      or f"tópico {thread_id}"
        ok, resposta = registrar_vinculo(sheets, partes[1], thread_id, nome_topico)
        enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS, resposta)
        return

    # Comandos de ponto — só no grupo equipe, tópico geral
    if chat_id != GRUPO_EQUIPE:
        return
    if thread_id is not None and thread_id != 1:
        return
    if user_id not in EQUIPE:
        return

    agora = datetime.now(MANAUS)
    if agora.weekday() == 6 or agora.hour < 6 or agora.hour >= 21:
        return

    nome = EQUIPE[user_id]["nome"]
    mention = EQUIPE[user_id]["mention"]
    texto_lower = texto.lower()

    if texto_lower == "/iniciar":
        # Aviso de duplicado
        if (user_id in registros and "entrada" in registros[user_id]
                and registros[user_id]["entrada"].date() == agora.date()):
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
        drive_ultimo_ts[user_id] = agora
        drive_ultimo_aviso.pop(user_id, None)
        drive_avisos_encerrados.discard(user_id)
        enviar_telegram(GRUPO_EQUIPE, None, f"✅ *{nome}* iniciou às {agora.strftime('%H:%M')}")

        # Salva entrada na planilha imediatamente
        try:
            nome_aba = agora.strftime("%m-%Y")
            garantir_aba(sheets, nome_aba)
            linha = get_ou_criar_linha(sheets, nome_aba, agora.strftime("%d/%m/%Y"))
            pos = ORDEM_PLANILHA.index(user_id)
            col_entrada = 1 + pos * COLUNAS_POR_PESSOA
            atualizar_celulas(sheets, nome_aba, linha, col_entrada, [agora.strftime("%H:%M")])
        except Exception as e:
            print(f"Erro ao salvar entrada: {e}")

        # Verifica ClickUp após 5min
        def checar_clickup_inicio():
            time.sleep(900)  # 15 minutos
            aguardar_fim_silencio()
            if user_id in drive_monitorando:
                verificar_clickup_inicio(user_id)
        threading.Thread(target=checar_clickup_inicio, daemon=True).start()

        # Aviso de inatividade no Drive (a cada 45 min): feito por loop_aviso_inatividade_drive

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
        drive_ultimo_ts.pop(user_id, None)
        drive_ultimo_aviso.pop(user_id, None)
        drive_avisos_encerrados.add(user_id)
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
            aguardar_fim_silencio()
            verificar_clickup_encerramento(user_id)
        threading.Thread(target=checar_clickup_enc, daemon=True).start()

    elif texto_lower == "/cliente":
        modo_cliente.add(user_id)
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"👤 *{nome}* em pasta de cliente — monitoramento Drive pausado.\n"
                       f"Volta ao normal automaticamente quando salvar arquivo nas pastas internas.")

        if user_id in registros and "entrada_extra" in registros[user_id]:
            hora_extra = registros[user_id]["entrada_extra"].strftime("%H:%M")
            enviar_telegram(GRUPO_EQUIPE, None,
                           f"⚠️ {mention}, sua hora extra já estava iniciada às {hora_extra}.")
            return

        if user_id not in registros:
            registros[user_id] = {}
        registros[user_id]["entrada_extra"] = agora
        if "data" not in registros[user_id]:
            registros[user_id]["data"] = agora.strftime("%d/%m/%Y")
        enviar_telegram(GRUPO_EQUIPE, None, f"⭐ *{nome}* iniciou hora extra às {agora.strftime('%H:%M')}")

        # Salva entrada extra na planilha imediatamente (sobrevive a restart do Railway)
        try:
            nome_aba = agora.strftime("%m-%Y")
            garantir_aba(sheets, nome_aba)
            linha = get_ou_criar_linha(sheets, nome_aba, agora.strftime("%d/%m/%Y"))
            pos = ORDEM_PLANILHA.index(user_id)
            col_extra_entrada = 1 + pos * COLUNAS_POR_PESSOA + 3
            atualizar_celulas(sheets, nome_aba, linha, col_extra_entrada, [agora.strftime("%H:%M")])
        except Exception as e:
            print(f"Erro ao salvar entrada extra: {e}")

    elif texto_lower == "/encerrando extra":
        entrada_extra = None
        if user_id in registros and "entrada_extra" in registros[user_id]:
            entrada_extra = registros[user_id]["entrada_extra"]
        else:
            entrada_extra = recuperar_entrada_extra_hoje(user_id, sheets)

        if entrada_extra is None:
            enviar_telegram(GRUPO_EQUIPE, None, f"⚠️ {mention}, use /cliente primeiro para iniciar a extra.")
            return

        if user_id not in registros:
            registros[user_id] = {}
        extra_min = int((agora - entrada_extra).total_seconds() / 60)
        data = agora.strftime("%d/%m/%Y")  # usa sempre a data atual
        try:
            salvar_extra(sheets, data, user_id,
                        entrada_extra.strftime("%H:%M"), agora.strftime("%H:%M"), extra_min)
            print(f"Extra salvo: {nome} {data} {entrada_extra.strftime('%H:%M')} -> {agora.strftime('%H:%M')} ({extra_min}min)")
        except Exception as e:
            print(f"Erro sheets extra: {e}")
        registros[user_id].pop("entrada_extra", None)
        drive_avisos_encerrados.add(user_id)
        drive_ultimo_aviso.pop(user_id, None)
        extra_h = extra_min // 60
        extra_m = extra_min % 60
        enviar_telegram(GRUPO_EQUIPE, None,
                       f"⭐ *{nome}* encerrou hora extra\n⏱ Extra: *{extra_h}h{extra_m:02d}min*")

# ─── RELATÓRIO MENSAL ─────────────────────────────────────────
def gerar_relatorio_horas_extras(sheets):
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
        enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS, f"⚠️ Sem dados de extras para {mes_str}")
        return

    if len(linhas) <= 1:
        enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS, f"⚠️ Sem registros de extras em {mes_str}")
        return

    relatorio = [f"⭐ *Horas extras — {mes_str}*\n"]

    for i, uid in enumerate(ORDEM_PLANILHA):
        if uid == 2048504320:  # Nagia não entra
            continue
        nome = EQUIPE[uid]["nome"]
        col_extra = 1 + i * COLUNAS_POR_PESSOA + 5  # coluna Extra Total (0-based)

        total_min = 0
        for linha in linhas[1:]:
            if len(linha) > col_extra and linha[col_extra]:
                valor = linha[col_extra]
                try:
                    h = int(valor.split("h")[0])
                    m = int(valor.split("h")[1].replace("min", ""))
                    total_min += h * 60 + m
                except:
                    pass

        horas = total_min // 60
        mins = total_min % 60
        relatorio.append(f"👤 *{nome}*: {horas}h{mins:02d}min")

    enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS, "\n".join(relatorio))

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
    global sheets_global, data_estado
    sheets_global = sheets
    data_estado = datetime.now(MANAUS).strftime("%d/%m/%Y")
    try:
        carregar_vinculos(sheets)
        carregar_topicos(sheets)
    except Exception as e:
        print(f"Erro ao carregar vínculos/tópicos: {e}")
    try:
        resumo = restaurar_estado_do_dia(sheets)
        print("♻️ Estado do dia restaurado:", "; ".join(resumo) if resumo else "nada a restaurar")
        enviar_telegram(GRUPO_PRIVADO, THREAD_RELATORIOS,
                       "♻️ Bot reiniciado às " + datetime.now(MANAUS).strftime("%H:%M") + ". "
                       + ("Restaurado da planilha:\n" + "\n".join(f"• {x}" for x in resumo) if resumo
                          else "Nada a restaurar."))
    except Exception as e:
        print(f"Erro ao restaurar estado do dia: {e}")

    threading.Thread(target=loop_drive, args=(drive, sheets), daemon=True).start()
    threading.Thread(target=loop_webhook, daemon=True).start()
    threading.Thread(target=loop_verificar_clickup_sem_inicio, args=(sheets,), daemon=True).start()
    threading.Thread(target=loop_aviso_inatividade_drive, daemon=True).start()
    threading.Thread(target=loop_virada_do_dia, daemon=True).start()

    offset = None
    relatorio_enviado = False

    while True:
        try:
            params = {"timeout": 10}
            if offset:
                params["offset"] = offset
            r = SESSION_TG.get(f"{BASE_URL}/getUpdates", params=params, timeout=15)
            if r.status_code != 200:
                # Token inválido (401), conflito (409) etc.: não pode repetir sem pausa
                print(f"Telegram getUpdates falhou ({r.status_code}): {r.text[:150]}")
                time.sleep(30)
                continue
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
                    gerar_relatorio_horas_extras(sheets)
                    relatorio_enviado = True
            else:
                relatorio_enviado = False
        except Exception as e:
            print(f"Erro main: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
