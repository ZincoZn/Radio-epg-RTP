#!/usr/bin/env python3

import argparse
import copy
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta
from pathlib import Path
from html import unescape
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURAÇÃO
# ============================================================

API_BASE = (
    "https://www.rtp.pt/EPG/json/"
    "rtp-channels-page/list-grid/radio"
)

XMLTV_FILE = Path("grelha_rtp_completa.xml")

LISBON = ZoneInfo("Europe/Lisbon")

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; RTP-Radio-EPG/2.0; +https://github.com/)"
)

MAX_RETRIES = 4
RETRY_DELAY = 3

# Correspondência já confirmada entre API RTP e XMLTV.
CHANNEL_MAP = {
    1: "antena-1",
    2: "antena-2",
    3: "antena-3",
    4: "rdp-africa",
    5: "rdp-internacional",
    7: "rdp-acores-antena-1",
    8: "rdp-madeira-antena-1",
    9: "rdp-madeira-antena-3",
}


# ============================================================
# UTILITÁRIOS
# ============================================================

def limpar_html(texto):
    if texto is None:
        return ""

    texto = unescape(str(texto))
    texto = re.sub(r"<[^>]+>", "", texto)
    return " ".join(texto.split()).strip()


def xml_escape(texto):
    return limpar_html(texto)


def data_parse(valor):
    return datetime.strptime(valor, "%d-%m-%Y").date()


def data_str(data):
    return data.strftime("%d-%m-%Y")


def local_datetime(valor):
    """
    Converte:
        2026-08-24 00:00:00
    para datetime aware em Europe/Lisbon.
    """
    dt = datetime.strptime(
        valor,
        "%Y-%m-%d %H:%M:%S"
    )

    return dt.replace(tzinfo=LISBON)


def xmltv_datetime(dt):
    """
    XMLTV:
        YYYYMMDDHHMMSS +0100
    """
    offset = dt.utcoffset()

    if offset is None:
        offset = timedelta(0)

    total_minutes = int(
        offset.total_seconds() / 60
    )

    sinal = "+" if total_minutes >= 0 else "-"

    total_minutes = abs(total_minutes)

    horas = total_minutes // 60
    minutos = total_minutes % 60

    return (
        dt.strftime("%Y%m%d%H%M%S")
        + f" {sinal}{horas:02d}{minutos:02d}"
    )


def criar_elemento(parent, tag, texto=None):
    elemento = ET.SubElement(parent, tag)

    if texto:
        elemento.text = texto

    return elemento


# ============================================================
# API RTP
# ============================================================

def construir_url(channel_id, data):
    return (
        f"{API_BASE}/"
        f"{channel_id}/"
        f"{data_str(data)}/"
        f"lis"
    )


def consultar_url(url):
    ultimo_erro = None

    for tentativa in range(1, MAX_RETRIES + 1):

        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=30
            ) as resposta:

                status = resposta.getcode()

                if status != 200:
                    raise urllib.error.HTTPError(
                        url,
                        status,
                        f"HTTP {status}",
                        resposta.headers,
                        None,
                    )

                conteudo = resposta.read()

            return {
                "ok": True,
                "status": 200,
                "data": json.loads(
                    conteudo.decode("utf-8")
                ),
                "erro": None,
            }

        except urllib.error.HTTPError as e:

            ultimo_erro = f"HTTP {e.code}"

            # 404 = canal/data não disponível.
            # Não vale a pena repetir.
            if e.code == 404:
                return {
                    "ok": False,
                    "status": 404,
                    "data": None,
                    "erro": "HTTP 404",
                }

            # Erros temporários: repetir.
            if e.code in (
                429,
                500,
                502,
                503,
                504,
            ):
                if tentativa < MAX_RETRIES:
                    time.sleep(
                        RETRY_DELAY * tentativa
                    )
                    continue

            return {
                "ok": False,
                "status": e.code,
                "data": None,
                "erro": ultimo_erro,
            }

        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
        ) as e:

            ultimo_erro = str(e)

            if tentativa < MAX_RETRIES:
                time.sleep(
                    RETRY_DELAY * tentativa
                )
                continue

            return {
                "ok": False,
                "status": None,
                "data": None,
                "erro": ultimo_erro,
            }

        except Exception as e:

            ultimo_erro = str(e)

            if tentativa < MAX_RETRIES:
                time.sleep(
                    RETRY_DELAY * tentativa
                )
                continue

            return {
                "ok": False,
                "status": None,
                "data": None,
                "erro": ultimo_erro,
            }

    return {
        "ok": False,
        "status": None,
        "data": None,
        "erro": ultimo_erro or "Erro desconhecido",
    }


def extrair_programas(data):
    """
    Estrutura confirmada da API:

    result:
      morning
      afternoon
      evening
    """

    if not isinstance(data, dict):
        return []

    result = data.get("result", {})

    if not isinstance(result, dict):
        return []

    programas = []

    for periodo in (
        "morning",
        "afternoon",
        "evening",
    ):

        lista = result.get(periodo, [])

        if not isinstance(lista, list):
            continue

        for programa in lista:

            if not isinstance(programa, dict):
                continue

            p = copy.deepcopy(programa)
            p["_periodo"] = periodo

            if p.get("date"):
                programas.append(p)

    programas.sort(
        key=lambda x: x.get("date", "")
    )

    return programas


def nome_canal(data):
    info = data.get("_info", {})

    if isinstance(info, dict):
        return limpar_html(
            info.get("name", "")
        )

    return ""


# ============================================================
# DESCOBERTA DE CANAIS
# ============================================================

def descobrir_canais(data_inicial, max_id):

    print()
    print("=" * 70)
    print(" DESCOBERTA DE CANAIS RTP")
    print("=" * 70)

    print(
        f"Data: {data_str(data_inicial)}"
    )

    print(
        f"IDs testados: 1 → {max_id}"
    )

    canais = {}

    for channel_id in range(1, max_id + 1):

        print(
            f"[{channel_id:02d}/{max_id:02d}] "
            f"ID {channel_id}...",
            end=" ",
            flush=True,
        )

        url = construir_url(
            channel_id,
            data_inicial
        )

        resposta = consultar_url(url)

        if resposta["ok"]:

            data = resposta["data"]
            programas = extrair_programas(data)
            nome = nome_canal(data)

            if nome and programas:

                canais[channel_id] = {
                    "id": channel_id,
                    "name": nome,
                    "data": data,
                    "programas": programas,
                }

                print(
                    f"OK → {nome} "
                    f"({len(programas)} programas)"
                )

            else:
                print(
                    "OK → resposta sem "
                    "programação reconhecível"
                )

        else:

            erro = resposta["erro"] or "erro"

            print(
                f"ERRO → {erro}"
            )

    print()
    print(
        f"Canais encontrados: {len(canais)}"
    )

    return canais


# ============================================================
# XMLTV
# ============================================================

def carregar_xml():

    if not XMLTV_FILE.exists():
        print(
            f"[ERRO] Não existe: "
            f"{XMLTV_FILE.resolve()}"
        )
        return None

    try:
        tree = ET.parse(XMLTV_FILE)
        return tree

    except Exception as e:
        print(
            f"[ERRO] Não foi possível abrir "
            f"{XMLTV_FILE}: {e}"
        )
        return None


def obter_channels(root):

    resultado = {}

    for channel in root.findall("channel"):

        channel_id = channel.get("id")

        if channel_id:
            resultado[channel_id] = channel

    return resultado


def garantir_canal(root, channel_id, nome):

    channels = obter_channels(root)

    if channel_id in channels:
        return channels[channel_id]

    channel = ET.Element(
        "channel",
        {"id": channel_id}
    )

    criar_elemento(
        channel,
        "display-name",
        nome
    )

    root.insert(
        0,
        channel
    )

    return channel


# ============================================================
# MATCH API → XMLTV
# ============================================================

def fazer_match(root, canais):

    print()
    print("=" * 70)
    print(" MATCH API RTP → XMLTV")
    print("=" * 70)

    channels = obter_channels(root)

    matches = {}

    for api_id, canal in canais.items():

        xmltv_id = CHANNEL_MAP.get(api_id)

        if not xmltv_id:
            print(
                f"[AVISO] API {api_id} "
                f"sem correspondência XMLTV"
            )
            continue

        if xmltv_id not in channels:

            print(
                f"[AVISO] {canal['name']:<32} "
                f"API {api_id} → "
                f"XMLTV {xmltv_id} "
                f"(canal não existente)"
            )

            garantir_canal(
                root,
                xmltv_id,
                canal["name"]
            )

        else:

            print(
                f"[OK] {canal['name']:<32} "
                f"API {api_id:<3} → "
                f"XMLTV {xmltv_id}"
            )

        matches[api_id] = xmltv_id

    return matches


# ============================================================
# PROGRAMAS XMLTV
# ============================================================

def programa_data_local(programme):

    start = programme.get("start", "")

    if len(start) < 14:
        return ""

    try:
        return (
            start[0:4]
            + "-"
            + start[4:6]
            + "-"
            + start[6:8]
        )

    except Exception:
        return ""


def remover_programas_do_dia(
    root,
    channel_id,
    data,
):

    data_iso = data.isoformat()

    removidos = 0

    for programme in list(
        root.findall("programme")
    ):

        if programme.get("channel") != channel_id:
            continue

        if (
            programa_data_local(programme)
            == data_iso
        ):

            root.remove(programme)
            removidos += 1

    return removidos


def criar_programme(
    root,
    channel_id,
    programa,
    proximo_programa=None,
):

    inicio = local_datetime(
        programa["date"]
    )

    if proximo_programa:
        fim = local_datetime(
            proximo_programa["date"]
        )

    else:
        # Para o último programa do dia,
        # assumimos 24:00 do mesmo dia.
        fim = (
            inicio.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(days=1)
        )

    # Segurança contra dados anómalos.
    if fim <= inicio:
        fim = inicio + timedelta(minutes=5)

    programme = ET.Element(
        "programme",
        {
            "channel": channel_id,
            "start": xmltv_datetime(inicio),
            "stop": xmltv_datetime(fim),
        },
    )

    nome = limpar_html(
        programa.get("name", "")
    )

    if nome:
        criar_elemento(
            programme,
            "title",
            nome
        )

    descricao = limpar_html(
        programa.get("description", "")
    )

    if descricao:
        criar_elemento(
            programme,
            "desc",
            descricao
        )

    url = programa.get("url")

    if url:
        criar_elemento(
            programme,
            "url",
            url
        )

    image_list = (
        programa.get("image", [])
        or []
    )

    imagem = None

    # Preferir imagem 384px.
    for img in image_list:

        if not isinstance(img, dict):
            continue

        if str(
            img.get("width", "")
        ) == "384":

            imagem = img.get("src")
            break

    if not imagem and image_list:

        primeiro = image_list[0]

        if isinstance(primeiro, dict):
            imagem = primeiro.get("src")

    if imagem:
        icon = ET.SubElement(
            programme,
            "icon",
            {"src": imagem}
        )

    symbols = (
        programa.get("symbols", [])
        or []
    )

    symbol_names = []

    for symbol in symbols:

        if not isinstance(symbol, dict):
            continue

        nome_symbol = limpar_html(
            symbol.get(
                "symbol_description",
                ""
            )
        )

        if nome_symbol:
            symbol_names.append(
                nome_symbol
            )

    if symbol_names:

        ET.SubElement(
            programme,
            "category",
        ).text = ", ".join(
            symbol_names
        )

    watch_again = programa.get(
        "watch_again_url"
    )

    if watch_again:

        criar_elemento(
            programme,
            "previously-shown",
            None
        )

        # XMLTV não possui um campo
        # universal específico para Watch Again.
        # Guardamos a informação em URL.
        if not url:
            criar_elemento(
                programme,
                "url",
                watch_again
            )

    root.append(programme)


def inserir_programas(
    root,
    xmltv_id,
    programas,
    data,
):

    # Só chamamos esta função quando a API
    # forneceu dados válidos.
    remover_programas_do_dia(
        root,
        xmltv_id,
        data
    )

    for index, programa in enumerate(
        programas
    ):

        proximo = None

        if index + 1 < len(programas):
            proximo = programas[
                index + 1
            ]

        criar_programme(
            root,
            xmltv_id,
            programa,
            proximo
        )


# ============================================================
# ORDENAÇÃO
# ============================================================

def ordenar_programas(root):

    canais = list(
        root.findall("channel")
    )

    programas = list(
        root.findall("programme")
    )

    programas.sort(
        key=lambda p: (
            p.get("channel", ""),
            p.get("start", ""),
        )
    )

    for programme in root.findall(
        "programme"
    ):
        root.remove(programme)

    for programme in programas:
        root.append(programme)


# ============================================================
# FORMATAÇÃO XML
# ============================================================

def indentar_xml(elemento, nivel=0):

    indent = "\n" + (
        "  " * nivel
    )

    if len(elemento):

        if not elemento.text or not elemento.text.strip():
            elemento.text = indent + "  "

        for filho in elemento:

            indentar_xml(
                filho,
                nivel + 1
            )

            if (
                not filho.tail
                or not filho.tail.strip()
            ):
                filho.tail = (
                    indent + "  "
                )

        if (
            not elemento[-1].tail
            or not elemento[-1].tail.strip()
        ):
            elemento[-1].tail = indent

    else:

        if nivel:
            elemento.tail = indent


# ============================================================
# VALIDAÇÃO
# ============================================================

def validar_xml(root):

    channels = root.findall(
        "channel"
    )

    programmes = root.findall(
        "programme"
    )

    print()
    print("=" * 70)
    print(" VALIDAÇÃO")
    print("=" * 70)

    print(
        f"[OK] Canais XMLTV: "
        f"{len(channels)}"
    )

    print(
        f"[OK] Programas XMLTV: "
        f"{len(programmes)}"
    )

    if not channels:
        print(
            "[ERRO] XMLTV sem canais."
        )
        return False

    if not programmes:
        print(
            "[ERRO] XMLTV sem programas."
        )
        return False

    return True


# ============================================================
# PROCESSAMENTO DE UM DIA
# ============================================================

def processar_dia(
    root,
    canais,
    data,
    matches,
):

    print()
    print("-" * 70)
    print(
        f"[INFO] EPG {data_str(data)}"
    )

    total = 0

    for api_id, xmltv_id in matches.items():

        nome = canais[api_id]["name"]

        url = construir_url(
            api_id,
            data
        )

        resposta = consultar_url(url)

        if not resposta["ok"]:

            erro = resposta["erro"]

            print(
                f"  [AVISO] "
                f"{nome}: {erro}"
            )

            # MUITO IMPORTANTE:
            # não remover dados existentes.
            continue

        programas = extrair_programas(
            resposta["data"]
        )

        if not programas:

            print(
                f"  [AVISO] "
                f"{nome}: resposta sem "
                f"programas"
            )

            # Também não apagamos dados existentes.
            continue

        # Garantir que os programas pertencem
        # realmente ao dia solicitado.
        programas_validos = []

        for p in programas:

            try:
                dt = local_datetime(
                    p["date"]
                )

                if dt.date() == data:
                    programas_validos.append(p)

            except Exception:
                continue

        if not programas_validos:

            print(
                f"  [AVISO] "
                f"{nome}: nenhum programa "
                f"válido para {data_str(data)}"
            )

            continue

        inserir_programas(
            root,
            xmltv_id,
            programas_validos,
            data
        )

        quantidade = len(
            programas_validos
        )

        total += quantidade

        print(
            f"  {nome:<32} "
            f"{quantidade:>3} programas"
        )

    return total


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "RTP Radio EPG — API → XMLTV"
        )
    )

    parser.add_argument(
        "--date",
        default=None,
        help=(
            "Data inicial DD-MM-YYYY. "
            "Por omissão usa a data de Lisboa."
        ),
    )

    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Número de dias a gerar.",
    )

    parser.add_argument(
        "--max-id",
        type=int,
        default=15,
        help="ID máximo a testar.",
    )

    args = parser.parse_args()

    if args.days < 1:
        print(
            "[ERRO] --days deve ser >= 1"
        )
        sys.exit(1)

    if args.max_id < 1:
        print(
            "[ERRO] --max-id deve ser >= 1"
        )
        sys.exit(1)

    if args.date:

        try:
            data_inicial = data_parse(
                args.date
            )

        except ValueError:
            print(
                "[ERRO] Data inválida. "
                "Use DD-MM-YYYY."
            )
            sys.exit(1)

    else:

        data_inicial = datetime.now(
            LISBON
        ).date()

    print()
    print("=" * 70)
    print(" RTP RADIO EPG — API → XMLTV")
    print("=" * 70)

    print(
        f"[INFO] Data inicial: "
        f"{data_str(data_inicial)}"
    )

    print(
        f"[INFO] Dias: {args.days}"
    )

    print(
        f"[INFO] XMLTV: {XMLTV_FILE}"
    )

    print(
        f"[INFO] API: {API_BASE}"
    )

    # --------------------------------------------------------
    # Carregar XML antes de fazer qualquer alteração.
    # --------------------------------------------------------

    tree = carregar_xml()

    if tree is None:
        sys.exit(1)

    root = tree.getroot()

    # --------------------------------------------------------
    # Descobrir canais.
    # --------------------------------------------------------

    canais = descobrir_canais(
        data_inicial,
        args.max_id
    )

    if not canais:

        print()
        print(
            "[ERRO] Nenhum canal encontrado."
        )

        print(
            "[AVISO] O XMLTV existente "
            "não será alterado."
        )

        # Não falhar o GitHub Actions por
        # uma indisponibilidade temporária.
        sys.exit(0)

    # --------------------------------------------------------
    # Match.
    # --------------------------------------------------------

    matches = fazer_match(
        root,
        canais
    )

    if not matches:

        print(
            "[ERRO] Nenhuma correspondência "
            "API → XMLTV."
        )

        sys.exit(1)

    # --------------------------------------------------------
    # Processar todos os dias.
    # --------------------------------------------------------

    total_api = 0

    for numero_dia in range(args.days):

        data = (
            data_inicial
            + timedelta(days=numero_dia)
        )

        total_api += processar_dia(
            root,
            canais,
            data,
            matches
        )

    print()
    print(
        "=" * 70
    )

    print(
        f"[INFO] Programas obtidos "
        f"da API nesta execução: "
        f"{total_api}"
    )

    # --------------------------------------------------------
    # Ordenar.
    # --------------------------------------------------------

    ordenar_programas(root)

    # --------------------------------------------------------
    # Validar.
    # --------------------------------------------------------

    if not validar_xml(root):
        print(
            "[ERRO] XMLTV não passou "
            "a validação."
        )
        sys.exit(1)

    # --------------------------------------------------------
    # Escrever.
    # --------------------------------------------------------

    print()
    print(
        "[INFO] A atualizar XMLTV..."
    )

    indentar_xml(root)

    tree.write(
        XMLTV_FILE,
        encoding="utf-8",
        xml_declaration=True
    )

    # --------------------------------------------------------
    # Resultado.
    # --------------------------------------------------------

    channels_count = len(
        root.findall("channel")
    )

    programmes_count = len(
        root.findall("programme")
    )

    print(
        f"[OK] Canais XMLTV: "
        f"{channels_count}"
    )

    print(
        f"[OK] Programas XMLTV: "
        f"{programmes_count}"
    )

    print()
    print("=" * 70)
    print(" CONCLUÍDO")
    print("=" * 70)

    print()
    print(
        f"[OK] XMLTV atualizado: "
        f"{XMLTV_FILE}"
    )

    print(
        f"[OK] Canais: "
        f"{channels_count}"
    )

    print(
        f"[OK] Programas: "
        f"{programmes_count}"
    )

    print()


if __name__ == "__main__":
    main()
