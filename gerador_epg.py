import urllib.request
import json
from datetime import datetime, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

# 1. Preparar os 7 dias
hoje = datetime.now()
datas_a_pesquisar = [(hoje + timedelta(days=i)).strftime("%d-%m-%Y") for i in range(7)]

canais_dados = {}
programas_dados = {}

# 2. Extrair dados de todos os dias e canais
for data_alvo in datas_a_pesquisar:
    url = f"https://www.rtp.pt/EPG/json/rtp-home-page-tv-radio/list-all-grids/radio/{data_alvo}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req) as resposta:
            dados = json.loads(resposta.read())
    except Exception:
        continue # Ignorar o dia em caso de falha de ligação
    
    estacoes = dados.get("result", {})
    
    for id_canal, conteudo in estacoes.items():
        if id_canal not in canais_dados:
            canais_dados[id_canal] = conteudo.get("_info", {})
            programas_dados[id_canal] = []
        
        # Juntar todos os blocos horários do canal
        for bloco in ["morning", "afternoon", "evening", "primetime"]:
            if bloco in conteudo:
                programas_dados[id_canal].extend(conteudo[bloco])

# 3. Construir a estrutura XMLTV
tv = Element('tv', {'generator-info-name': 'Gerador RTP EPG 7 Dias'})
formato_rtp = "%Y-%m-%d %H:%M:%S"
formato_xmltv = "%Y%m%d%H%M%S +0100"

for id_canal, info in canais_dados.items():
    # Declarar o canal
    canal = SubElement(tv, 'channel', {'id': id_canal})
    nome_canal = SubElement(canal, 'display-name')
    nome_canal.text = info.get('name', id_canal)
    
    # Inserir o logótipo oficial do canal
    url_logo = info.get('logoUrl')
    if url_logo:
        SubElement(canal, 'icon', {'src': url_logo})
    
    # Filtrar programas duplicados e ordenar
    progs_canal = programas_dados[id_canal]
    progs_unicos = {p['date']: p for p in progs_canal}.values()
    progs_ordenados = sorted(progs_unicos, key=lambda x: x["date"])
    
    # 4. Gerar as tags de programação com a duração correcta e imagens dos programas
    for i in range(len(progs_ordenados)):
        prog_actual = progs_ordenados[i]
        inicio_dt = datetime.strptime(prog_actual['date'], formato_rtp)
        
        if i + 1 < len(progs_ordenados):
            fim_dt = datetime.strptime(progs_ordenados[i+1]['date'], formato_rtp)
        else:
            fim_dt = inicio_dt + timedelta(hours=1)
            
        programa = SubElement(tv, 'programme', {
            'start': inicio_dt.strftime(formato_xmltv),
            'stop': fim_dt.strftime(formato_xmltv),
            'channel': id_canal
        })
        
        titulo = SubElement(programa, 'title', {'lang': 'pt'})
        titulo.text = prog_actual.get('name', 'Sem Título')
        
        desc_texto = prog_actual.get('description')
        if desc_texto:
            desc = SubElement(programa, 'desc', {'lang': 'pt'})
            desc.text = desc_texto
            
        # Adicionar o ícone específico do programa, caso exista no feed
        imagens_programa = prog_actual.get('image')
        if imagens_programa and isinstance(imagens_programa, list) and len(imagens_programa) > 0:
            # Optamos pela última imagem da lista, que por norma costuma ser a de maior resolução
            url_img_prog = imagens_programa[-1].get('src')
            if url_img_prog:
                SubElement(programa, 'icon', {'src': url_img_prog})

# 5. Exportar ficheiro
xml_bruto = tostring(tv, 'utf-8')
xml_formatado = minidom.parseString(xml_bruto).toprettyxml(indent="  ")

with open("grelha_rtp_completa.xml", "w", encoding="utf-8") as ficheiro:
    ficheiro.write(xml_formatado)
