"""
Vigilancia Marcaria INPI - Script GitHub Actions
Descarga TODOS los boletines de Marcas Nuevas del miercoles,
analiza similitudes y envia email de alerta.
"""

import os, re, json, smtplib, unicodedata, requests, pdfplumber, logging
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Configuracion ──────────────────────────────────────────────────
EMAIL       = os.environ.get("EMAIL", "estudiodavo@gmail.com")
APP_PASS    = os.environ.get("GMAIL_APP_PASSWORD", "")
UMBRAL      = int(os.environ.get("UMBRAL_SIMILITUD", "70"))
INPI_CUIT   = os.environ.get("INPI_CUIT", "20287461020")
INPI_KEY    = os.environ.get("INPI_API_KEY", "")
INPI_WS_URL = "https://ws.inpi.gob.ar/wsinpi.asmx"
BOLETIN_REF_NUM  = 11108
BOLETIN_REF_DATE = date(2026, 9, 2)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")

# ── Clases vinculadas (Otamendi 4.6 / OMPI) ───────────────────────
CLASES_VINCULADAS = {
    29:[30,31,43], 30:[29,31,43], 31:[29,30],
    32:[33,43], 33:[32,43], 43:[29,30,32,33],
    35:[36,42,38], 36:[35,42], 42:[35,36,9,40], 9:[42,35,38],
    38:[41,42,35], 40:[42,37],
    25:[18,28,24], 18:[25,14], 28:[25,41],
    41:[35,38,42,28],
    44:[45,35,42], 5:[44,3,31],
    11:[20,21,19], 20:[11,21,19], 21:[11,20],
    19:[37,20,40], 37:[19,40,42],
    14:[25,18,35], 45:[35,42,36,44], 16:[41,35,9],
}

TERMINOS_DEBILES = {
    'SERVICIOS','PRODUCTOS','GRUPO','ESTUDIO','EMPRESA','CENTRO','CLUB',
    'DIGITAL','TECH','NET','WEB','ONLINE','GLOBAL','ARGENTINA',
    'COMERCIAL','INDUSTRIAL','NACIONAL','SUPER','MEGA','MAX','PLUS','PRO',
    'PREMIUM','ELITE','MASTER','GYM','FITNESS','SPORT','HOME','SHOP',
    'STORE','MARKET','EXPRESS','TOTAL','REAL','NUEVA','NUEVO','GRAN','GRANDE'
}

# ── Portfolio ──────────────────────────────────────────────────────
def cargar_portfolio():
    ruta = Path(__file__).parent / "data" / "portfolio.json"
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)

# ── Detectar TODOS los boletines de Marcas Nuevas del miercoles ───
def detectar_boletines(hoy=None):
    """
    Detecta los boletines de Marcas Nuevas publicados el miercoles de esta semana.
    Usa la referencia guardada del ultimo analisis para estimar el numero base.
    El INPI publica entre 1 y varios boletines por miercoles — buscamos en rango amplio.
    """
    from datetime import timedelta
    hoy = hoy or date.today()

    # Cargar referencia actualizada si existe
    global BOLETIN_REF_NUM, BOLETIN_REF_DATE
    ref_ruta = Path(__file__).parent / "data" / "referencia.json"
    if ref_ruta.exists():
        try:
            with open(ref_ruta) as f:
                ref = json.load(f)
            from datetime import datetime
            BOLETIN_REF_NUM = ref["num"]
            BOLETIN_REF_DATE = datetime.strptime(ref["fecha"], "%Y-%m-%d").date()
            logging.info(f"Referencia cargada: #{BOLETIN_REF_NUM} = {BOLETIN_REF_DATE}")
        except Exception as e:
            logging.warning(f"Error cargando referencia: {e}")

    # Calcular el miercoles de esta semana
    dia_semana = hoy.weekday()  # 0=lunes, 2=miercoles
    if dia_semana >= 2:
        dias_desde_mie = dia_semana - 2
    else:
        dias_desde_mie = dia_semana + 5
    miercoles = hoy - timedelta(days=dias_desde_mie)

    # Estimar numero de boletin para ese miercoles exacto
    # Dias desde la referencia * promedio historico de boletines por dia
    # Referencia calibrada: 11108 = 02/09/2026
    # En vez de estimar, buscamos en rango amplio y tomamos los que existen
    dias = (miercoles - BOLETIN_REF_DATE).days
    # Promedio historico: entre 11067 (24/06) y 11108 (02/09) = 41 boletines en 70 dias
    promedio_diario = 41 / 70  # ~0.586 boletines por dia
    base = BOLETIN_REF_NUM + round(dias * promedio_diario)
    logging.info(f"Miercoles: {miercoles} — Boletin base estimado: #{base}")

    headers = {"User-Agent": "Mozilla/5.0"}
    boletines = []

    # Buscar SOLO desde base hasta base+6 (max 6 boletines por miercoles)
    # Si el estimado tiene 1-2 de desfase, empezamos desde base-2
    for num in range(base - 10, base + 15):  # Rango amplio para cubrir variacion en publicaciones
        url = f"https://portaltramites.inpi.gob.ar/Uploads/Boletines/{num}_3_.pdf"
        try:
            r = requests.head(url, headers=headers, timeout=15, allow_redirects=True)
            if r.status_code == 200:
                boletines.append(num)
                logging.info(f"  Boletin #{num} ENCONTRADO")
        except Exception as e:
            logging.debug(f"  Boletin #{num} error: {e}")

    # Guardar registro de boletines ya procesados para no repetir
    procesados_ruta = Path(__file__).parent / "data" / "procesados.json"
    procesados = []
    if procesados_ruta.exists():
        with open(procesados_ruta) as f:
            procesados = json.load(f)

    # Filtrar solo los NO procesados aun
    boletines_nuevos = [b for b in boletines if str(b) not in procesados]

    if not boletines_nuevos and boletines:
        logging.info(f"Todos los boletines de esta semana ya fueron procesados: {boletines}")
        return []

    if not boletines_nuevos:
        logging.warning(f"No se encontraron boletines nuevos. Usando estimado #{base}")
        return [base]

    logging.info(f"Boletines nuevos a procesar: {boletines_nuevos}")
    return boletines_nuevos


def marcar_procesados(boletines):
    """Registra los boletines procesados y actualiza la referencia para el proximo miercoles."""
    procesados_ruta = Path(__file__).parent / "data" / "procesados.json"
    procesados = []
    if procesados_ruta.exists():
        with open(procesados_ruta) as f:
            procesados = json.load(f)
    for b in boletines:
        if str(b) not in procesados:
            procesados.append(str(b))
    with open(procesados_ruta, "w") as f:
        json.dump(procesados[-200:], f)

    # Actualizar referencia con el ultimo boletin encontrado
    # para que el proximo miercoles el estimado sea preciso
    if boletines:
        ref_ruta = Path(__file__).parent / "data" / "referencia.json"
        ref = {
            "num": max(boletines),
            "fecha": date.today().strftime("%Y-%m-%d")
        }
        with open(ref_ruta, "w") as f:
            json.dump(ref, f)
        logging.info(f"Referencia actualizada: #{ref['num']} = {ref['fecha']}")

# ── Descargar PDF ──────────────────────────────────────────────────
def descargar(num):
    url = f"https://portaltramites.inpi.gob.ar/Uploads/Boletines/{num}_3_.pdf"
    logging.info(f"Descargando boletin #{num}...")
    r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=90)
    r.raise_for_status()
    ruta = f"/tmp/boletin_{num}.pdf"
    with open(ruta, "wb") as f:
        f.write(r.content)
    logging.info(f"  #{num}: {len(r.content)//1024} KB")
    return ruta

# ── Parser PDF — solo seccion MARCAS NUEVAS ───────────────────────
def parsear(ruta):
    texto = ""
    with pdfplumber.open(ruta) as pdf:
        for page in pdf.pages[1:]:
            t = page.extract_text()
            if t:
                texto += t + "\n"

    idx = texto.find("MARCAS NUEVAS")
    sec = texto[idx:] if idx != -1 else texto

    marcas = []
    for b in re.split(r'\(21\)\s*Acta\s*', sec)[1:]:
        mA = re.search(r'^(\d+)', b)
        mC = re.search(r'\(51\)\s*Clase\s*(\d+)', b)
        if not mA or not mC:
            continue
        mD = re.search(r'\(54\)\s*([^\n\r(]{1,80})', b)
        mT = re.search(r'\(73\)\s*([^\n\r(]{1,120})', b)
        den = mD.group(1).strip() if mD else "[FIGURATIVA]"
        tit = re.sub(r'\s*-\s*[A-Z]{2}\s*\*?\s*$', '',
                     mT.group(1).strip()) if mT else ""
        marcas.append({
            "acta": mA.group(1),
            "clase": int(mC.group(1)),
            "denominacion": den or "[FIGURATIVA]",
            "titular": tit
        })
    return marcas

# ═══════════════════════════════════════════════════════════════════
# MOTOR DE SIMILITUD MARCARIA (Otamendi §4.3-4.7 / Manual OMPI)
#
# NOTA DE CALIBRACION (02/09/2026): el motor anterior reducia la
# denominacion a un "esqueleto" de consonantes (quitando TODAS las
# vocales) y ademas evaluaba por separado un fragmento truncado del
# 45% inicial de cada marca. Esos dos strings resultantes eran muy
# cortos, y Jaro-Winkler sobre strings cortos da puntajes altos con
# apenas 2-3 letras en comun — eso generaba falsos positivos graves
# (ej: BARSATEX vs OBRAI daba ALTO por compartir solo "B" y "R").
#
# Correccion aplicada:
# 1) La fonetica ya NO elimina vocales — solo sustituye letras que
#    suenan igual en espanol rioplatense (V/B, Z/S, C+E/I -> S, etc).
#    Esto mantiene la longitud real de la palabra.
# 2) Se elimina el sub-analisis de "radical truncado al 45%": el
#    propio Jaro-Winkler ya pondera mas las coincidencias iniciales
#    (bonus de prefijo), sin necesidad de truncar artificialmente.
# 3) El puntaje grafico/fonetico exige AHORA acuerdo entre dos
#    metricas distintas: Jaro-Winkler (tolera transposiciones) Y
#    Levenshtein (exige secuencia real de letras). Se toma el MINIMO
#    de ambas — una palabra con las mismas letras en otro orden
#    (ej. BARSATEX vs RESTRENA) puntua alto en Jaro-Winkler pero muy
#    bajo en Levenshtein, y con el minimo el resultado queda bajo,
#    como corresponde.
# ═══════════════════════════════════════════════════════════════════

def norm(s):
    s = s.upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r'[^A-Z0-9\s]', '', s).strip()

# Fonetica espanol rioplatense (Otamendi §4.4.1): B/V indistinguibles,
# Z/S/C(e,i) asimilables (seseo), H muda, QU->K, CH y LL sonidos propios.
# A diferencia de la version anterior, NO se eliminan las vocales.
def fonetica(s):
    s = s.replace("QU", "K")
    s = s.replace("CH", "X")
    s = s.replace("LL", "Y")
    s = s.replace("CE", "SE").replace("CI", "SI")
    s = s.replace("C", "K")       # resto de C (CA/CO/CU/consonante) suena K
    s = s.replace("GE", "JE").replace("GI", "JI")
    s = s.replace("W", "B")
    s = s.replace("V", "B")
    s = s.replace("Z", "S")
    s = s.replace("PH", "F")
    s = s.replace("TH", "T")
    s = s.replace("H", "")        # H es muda
    return s

def jaro_winkler(s1, s2):
    if s1 == s2: return 1.0
    if not s1 or not s2: return 0.0
    md = max(0, max(len(s1), len(s2)) // 2 - 1)
    m1 = [False]*len(s1); m2 = [False]*len(s2)
    matches = 0; t = 0
    for i in range(len(s1)):
        for j in range(max(0, i-md), min(i+md+1, len(s2))):
            if m2[j] or s1[i] != s2[j]: continue
            m1[i] = m2[j] = True; matches += 1; break
    if not matches: return 0.0
    k = 0
    for i in range(len(s1)):
        if not m1[i]: continue
        while not m2[k]: k += 1
        if s1[i] != s2[k]: t += 1
        k += 1
    jaro = (matches/len(s1) + matches/len(s2) + (matches - t/2)/matches) / 3
    p = 0
    for i in range(min(4, len(s1), len(s2))):
        if s1[i] == s2[i]: p += 1
        else: break
    return min(1.0, jaro + p * 0.1 * (1 - jaro))

def levenshtein_ratio(s1, s2):
    if s1 == s2: return 1.0
    if not s1 or not s2: return 0.0
    m, n = len(s1), len(s2)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]; dp[0] = i
        for j in range(1, n + 1):
            tmp = dp[j]
            cost = 0 if s1[i-1] == s2[j-1] else 1
            dp[j] = min(dp[j] + 1, dp[j-1] + 1, prev + cost)
            prev = tmp
    return 1 - dp[n] / max(m, n)

def sim_secuencial(s1, s2):
    """Similitud grafica/fonetica real: exige coincidencia de LETRAS
    (Jaro-Winkler) Y de SECUENCIA (Levenshtein). Se toma el minimo de
    ambas para evitar que palabras con las mismas letras en otro
    orden puntuen alto por casualidad."""
    return min(jaro_winkler(s1, s2), levenshtein_ratio(s1, s2)) * 100

def es_debil(s):
    palabras = s.split()
    if not palabras: return False
    return sum(1 for w in palabras if w in TERMINOS_DEBILES) / len(palabras) >= 0.5

def calcular_similitud(a, b):
    na, nb = norm(a), norm(b)
    if na == nb: return 100
    scores = []

    # 1. Similitud grafica/ortografica (Otamendi §4.3.1-4.3.2)
    scores.append(sim_secuencial(na, nb))

    # 2. Similitud fonetica/auditiva (Otamendi §4.4)
    fa, fb = fonetica(na), fonetica(nb)
    if fa and fb:
        scores.append(sim_secuencial(fa, fb))

    # 3. Contencion — una marca dentro de la otra (Otamendi §4.5 / OMPI):
    #    agregar una palabra generica no elimina el riesgo de confusion.
    if na in nb or nb in na:
        ratio = min(len(na), len(nb)) / max(len(na), len(nb))
        scores.append(68 + ratio * 22)

    # 4. Elemento dominante compartido (Otamendi §4.7.1-d)
    wA = [w for w in na.split() if len(w) > 2 and w not in TERMINOS_DEBILES]
    wB = [w for w in nb.split() if len(w) > 2 and w not in TERMINOS_DEBILES]
    if wA and wB:
        comunes = set(wA) & set(wB)
        if comunes:
            ratio = len(comunes) / max(len(wA), len(wB))
            prim = wA[0] == wB[0]
            scores.append(55 + ratio * 30 + (12 if prim else 0))

    maxsc = round(max(scores))
    # Marcas debiles: menor proteccion (Otamendi §4.7.2-c)
    if es_debil(na) and es_debil(nb) and maxsc < 90:
        return round(maxsc * 0.82)
    return maxsc

def nivel_riesgo(score, rel):
    if not rel: return None
    if score >= 90: return "ALTO"
    if score >= 78 and rel == "identica": return "ALTO"
    if score >= 76 and rel == "vinculada": return "ALTO"
    if score >= 60 and rel == "identica": return "MEDIO"
    if score >= 58 and rel == "vinculada": return "MEDIO"
    if score >= 45 and rel == "identica": return "BAJO"
    if score >= 43 and rel == "vinculada": return "BAJO"
    return None

def relacion_clases(c1, c2):
    if c1 == c2: return "identica"
    if c2 in CLASES_VINCULADAS.get(c1, []): return "vinculada"
    return None

# ── Analisis ───────────────────────────────────────────────────────
def analizar(marcas_boletin, portfolio):
    resultados = {}
    for mi in portfolio:
        amenazas = []
        for mb in marcas_boletin:
            rel = relacion_clases(mb["clase"], mi["clase"])
            if not rel: continue
            sc = calcular_similitud(mb["denominacion"], mi["denominacion"])
            if sc < UMBRAL: continue
            rv = nivel_riesgo(sc, rel)
            if not rv: continue
            amenazas.append({**mb, "score": sc, "riesgo": rv, "rel_clase": rel})
        if amenazas:
            amenazas.sort(key=lambda x: -x["score"])
            key = f"{mi['denominacion']}|{mi['clase']}"
            resultados[key] = {"mi_marca": mi, "amenazas": amenazas}
    return resultados

# ── HTML del reporte ───────────────────────────────────────────────
def generar_html(resultados, boletines, fecha_str, total):
    COLOR = {"ALTO":"#ff4d6d","MEDIO":"#ffc93c","BAJO":"#2dd4a0"}
    ORDEN = {"ALTO":0,"MEDIO":1,"BAJO":2}

    items = sorted(resultados.items(),
        key=lambda x: min(ORDEN[a["riesgo"]] for a in x[1]["amenazas"]))

    altos  = sum(1 for v in resultados.values() if any(a["riesgo"]=="ALTO"  for a in v["amenazas"]))
    medios = sum(1 for v in resultados.values()
        if not any(a["riesgo"]=="ALTO" for a in v["amenazas"])
        and any(a["riesgo"]=="MEDIO" for a in v["amenazas"]))
    bajos  = len(resultados) - altos - medios
    nums   = ", ".join(f"#{n}" for n in boletines)

    cards = ""
    for key, data in items:
        mi = data["mi_marca"]
        top = data["amenazas"][0]["riesgo"]
        url_mi = f"https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado?acta={mi.get('acta','')}"
        filas = ""
        for a in data["amenazas"]:
            url = f"https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado?acta={a['acta']}"
            filas += f"""<tr style="border-bottom:1px solid #2e3350">
              <td style="padding:8px;color:{COLOR[a['riesgo']]};font-weight:700">{a['riesgo']}</td>
              <td style="padding:8px;font-weight:600">{a['denominacion']}</td>
              <td style="padding:8px;color:#7b82a8">Cl.{a['clase']}</td>
              <td style="padding:8px;color:#7b82a8">{a['titular']}</td>
              <td style="padding:8px;color:{COLOR[a['riesgo']]};font-weight:700">{a['score']}%</td>
              <td style="padding:8px;color:#7b82a8">{a['rel_clase']}</td>
              <td style="padding:8px"><a href="{url}" style="color:#4f7fff">Ver INPI →</a></td>
            </tr>"""
        cards += f"""
        <details style="margin-bottom:12px;border:1px solid {COLOR[top]}44;border-radius:8px;overflow:hidden">
          <summary style="background:#1a1d27;padding:12px 16px;cursor:pointer;list-style:none;
            display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <span style="background:{COLOR[top]}22;color:{COLOR[top]};border:1px solid {COLOR[top]}55;
              padding:2px 10px;border-radius:20px;font-size:11px;font-weight:800">{top}</span>
            <span style="font-weight:700;font-size:15px">🔒 {mi['denominacion']}</span>
            <a href="{url_mi}" style="color:#4f7fff;font-size:12px;text-decoration:none">
              Clase {mi['clase']} · Acta {mi.get('acta','—')} 🔗
            </a>
            <span style="color:#7b82a8;font-size:11px;margin-left:auto">{len(data['amenazas'])} amenaza(s)</span>
          </summary>
          <div style="overflow-x:auto;border-top:1px solid {COLOR[top]}33">
            <table style="width:100%;border-collapse:collapse;font-size:13px">
              <thead><tr style="background:#22263a">
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Riesgo</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Denominacion</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Clase</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Titular</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Similitud</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Relacion</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Expediente</th>
              </tr></thead>
              <tbody>{filas}</tbody>
            </table>
          </div>
        </details>"""

    sin = f'<div style="background:#1a1d27;border:1px solid #2e3350;border-radius:10px;padding:40px;text-align:center;color:#7b82a8">Sin conflictos detectados en los boletines analizados.</div>' if not resultados else ""

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Vigilancia Marcas — {fecha_str}</title>
<style>
summary::-webkit-details-marker {{ display:none; }}
summary {{ list-style:none; }}
summary::before {{ content:'▶'; margin-right:8px; color:#7b82a8; font-size:11px; transition:transform .15s; }}
details[open] summary::before {{ transform:rotate(90deg); }}
</style>
</head>
<body style="background:#0f1117;color:#e8eaf6;font-family:system-ui,sans-serif;padding:24px;max-width:960px;margin:0 auto">
<div style="background:#1a1d27;border:1px solid #2e3350;border-radius:12px;padding:20px;margin-bottom:20px">
  <h1 style="margin:0 0 6px;font-size:22px">Vigilancia Marcaria INPI</h1>
  <p style="color:#7b82a8;margin:0;font-size:13px">
    {fecha_str} &nbsp;·&nbsp; Boletines analizados: <b style="color:#4f7fff">{nums}</b>
    &nbsp;·&nbsp; {total} marcas relevadas
    &nbsp;·&nbsp; Criterios: Otamendi + Manual Armonizado OMPI
  </p>
</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px">
  <div style="background:#1a1d27;border:1px solid #2e3350;border-radius:8px;padding:14px;text-align:center">
    <div style="font-size:11px;color:#7b82a8;text-transform:uppercase">Marcas afectadas</div>
    <div style="font-size:26px;font-weight:800">{len(resultados)}</div>
  </div>
  <div style="background:rgba(255,77,109,.04);border:1px solid #ff4d6d44;border-radius:8px;padding:14px;text-align:center">
    <div style="font-size:11px;color:#7b82a8;text-transform:uppercase">Alto</div>
    <div style="font-size:26px;font-weight:800;color:#ff4d6d">{altos}</div>
  </div>
  <div style="background:rgba(255,201,60,.03);border:1px solid #ffc93c44;border-radius:8px;padding:14px;text-align:center">
    <div style="font-size:11px;color:#7b82a8;text-transform:uppercase">Medio</div>
    <div style="font-size:26px;font-weight:800;color:#ffc93c">{medios}</div>
  </div>
  <div style="background:rgba(45,212,160,.02);border:1px solid #2dd4a044;border-radius:8px;padding:14px;text-align:center">
    <div style="font-size:11px;color:#7b82a8;text-transform:uppercase">Bajo</div>
    <div style="font-size:26px;font-weight:800;color:#2dd4a0">{bajos}</div>
  </div>
</div>
{cards}{sin}
<p style="color:#7b82a8;font-size:11px;text-align:center;margin-top:24px">
  Generado automaticamente · estudiodavo@gmail.com · github.com/davoip/marcas-inpi
</p>
</body></html>"""

# ── Email ──────────────────────────────────────────────────────────
def enviar_email(html, boletines, resultados):
    if not APP_PASS:
        logging.warning("GMAIL_APP_PASSWORD no configurado - email no enviado.")
        return

    ORDEN = {"ALTO":0,"MEDIO":1,"BAJO":2}
    altos  = sum(1 for v in resultados.values() if any(a["riesgo"]=="ALTO" for a in v["amenazas"]))
    medios = sum(1 for v in resultados.values() if not any(a["riesgo"]=="ALTO" for a in v["amenazas"]) and any(a["riesgo"]=="MEDIO" for a in v["amenazas"]))
    bajos  = len(resultados) - altos - medios
    nums   = ", ".join(f"#{n}" for n in boletines)
    fecha  = date.today().strftime("%d/%m/%Y")

    if altos:        asunto = f"[Marcas INPI] {altos} ALTO(S), {medios} MEDIO(S) - {fecha}"
    elif resultados: asunto = f"[Marcas INPI] {len(resultados)} conflicto(s) - {fecha}"
    else:            asunto = f"[Marcas INPI] Sin conflictos - {fecha}"

    # Solo 30 conflictos ALTO y MEDIO en el email (evitar limite de Gmail)
    items = sorted(resultados.items(),
        key=lambda x: min(ORDEN[a["riesgo"]] for a in x[1]["amenazas"]))
    items_email = [(k,v) for k,v in items
                   if any(a["riesgo"] in ("ALTO","MEDIO") for a in v["amenazas"])][:30]

    filas = ""
    for key, data in items_email:
        mi  = data["mi_marca"]
        top = data["amenazas"][0]
        c   = {"ALTO":"#ff4d6d","MEDIO":"#ffc93c","BAJO":"#2dd4a0"}[top["riesgo"]]
        url = "https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado?acta=" + top["acta"]
        filas += (
            '<tr style="border-bottom:1px solid #2e3350">'
            f'<td style="padding:7px 8px;color:{c};font-weight:700">{top["riesgo"]}</td>'
            f'<td style="padding:7px 8px;font-weight:600">{mi["denominacion"]}</td>'
            f'<td style="padding:7px 8px;color:#4f7fff">Cl.{mi["clase"]}</td>'
            f'<td style="padding:7px 8px;font-weight:600">{top["denominacion"]}</td>'
            f'<td style="padding:7px 8px;color:#4f7fff">Cl.{top["clase"]}</td>'
            f'<td style="padding:7px 8px;color:#7b82a8">{top["titular"][:25]}</td>'
            f'<td style="padding:7px 8px;color:{c};font-weight:700">{top["score"]}%</td>'
            f'<td style="padding:7px 8px"><a href="{url}" style="color:#4f7fff">Ver</a></td>'
            '</tr>'
        )

    omitidos = len(resultados) - len(items_email)
    nota_omi = f'<p style="color:#7b82a8;font-size:12px;margin:8px 0 0">+ {omitidos} adicionales en la plataforma web.</p>' if omitidos > 0 else ""

    cuerpo = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
        '<body style="background:#0f1117;color:#e8eaf6;font-family:system-ui,sans-serif;padding:24px;max-width:900px;margin:0 auto">'
        '<div style="background:#1a1d27;border:1px solid #2e3350;border-radius:12px;padding:18px;margin-bottom:14px">'
        f'<h1 style="margin:0 0 4px;font-size:19px">Vigilancia Marcaria INPI - {fecha}</h1>'
        f'<p style="color:#7b82a8;margin:0;font-size:13px">Boletines: {nums}</p>'
        '</div>'
        '<div style="display:flex;gap:10px;margin-bottom:14px">'
        f'<div style="flex:1;background:rgba(255,77,109,.06);border:1px solid #ff4d6d44;border-radius:8px;padding:10px;text-align:center"><div style="font-size:10px;color:#7b82a8">ALTO</div><div style="font-size:24px;font-weight:800;color:#ff4d6d">{altos}</div></div>'
        f'<div style="flex:1;background:rgba(255,201,60,.04);border:1px solid #ffc93c44;border-radius:8px;padding:10px;text-align:center"><div style="font-size:10px;color:#7b82a8">MEDIO</div><div style="font-size:24px;font-weight:800;color:#ffc93c">{medios}</div></div>'
        f'<div style="flex:1;background:rgba(45,212,160,.03);border:1px solid #2dd4a044;border-radius:8px;padding:10px;text-align:center"><div style="font-size:10px;color:#7b82a8">BAJO</div><div style="font-size:24px;font-weight:800;color:#2dd4a0">{bajos}</div></div>'
        f'<div style="flex:1;background:#1a1d27;border:1px solid #2e3350;border-radius:8px;padding:10px;text-align:center"><div style="font-size:10px;color:#7b82a8">TOTAL</div><div style="font-size:24px;font-weight:800">{len(resultados)}</div></div>'
        '</div>'
        '<div style="background:#1a1d27;border:1px solid #2e3350;border-radius:8px;overflow:hidden">'
        '<table style="width:100%;border-collapse:collapse;font-size:12px">'
        '<thead><tr style="background:#22263a">'
        '<th style="padding:5px 8px;text-align:left;color:#7b82a8;font-size:10px">Riesgo</th>'
        '<th style="padding:5px 8px;text-align:left;color:#7b82a8;font-size:10px">Mi Marca</th>'
        '<th style="padding:5px 8px;text-align:left;color:#7b82a8;font-size:10px">Cl.</th>'
        '<th style="padding:5px 8px;text-align:left;color:#7b82a8;font-size:10px">En Boletin</th>'
        '<th style="padding:5px 8px;text-align:left;color:#7b82a8;font-size:10px">Cl.</th>'
        '<th style="padding:5px 8px;text-align:left;color:#7b82a8;font-size:10px">Titular</th>'
        '<th style="padding:5px 8px;text-align:left;color:#7b82a8;font-size:10px">Sim.</th>'
        '<th style="padding:5px 8px;text-align:left;color:#7b82a8;font-size:10px">INPI</th>'
        '</tr></thead>'
        f'<tbody>{filas if filas else "<tr><td colspan=8 style=padding:20px;text-align:center;color:#7b82a8>Sin conflictos detectados</td></tr>"}</tbody>'
        '</table>'
        '</div>'
        f'{nota_omi}'
        '<p style="color:#7b82a8;font-size:11px;text-align:center;margin-top:14px">'
        'Reporte completo: davoip.github.io/marcas-inpi'
        '</p>'
        '</body></html>'
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"]    = EMAIL
    msg["To"]      = EMAIL
    msg.attach(MIMEText(cuerpo, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL, APP_PASS)
        smtp.send_message(msg)
    logging.info(f"Email enviado: {asunto}")

# ── Guardar reporte ────────────────────────────────────────────────
def guardar_reporte(html, boletines):
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    nombre = f"reporte_{'_'.join(str(n) for n in boletines)}.html"
    ruta = data_dir / nombre
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(html)
    # Actualizar indice
    idx_ruta = data_dir / "reportes.json"
    reportes = []
    if idx_ruta.exists():
        with open(idx_ruta) as f:
            reportes = json.load(f)
    entrada = {
        "nums": boletines,
        "fecha": date.today().strftime("%d/%m/%Y"),
        "archivo": nombre,
        "label": "+".join(f"#{n}" for n in boletines)
    }
    if not any(r.get("archivo") == nombre for r in reportes):
        reportes.insert(0, entrada)
    with open(idx_ruta, "w") as f:
        json.dump(reportes[:52], f)
    logging.info(f"Reporte guardado: {ruta}")

# ── Main ───────────────────────────────────────────────────────────
def main():
    logging.info("=" * 50)
    logging.info("VIGILANCIA MARCARIA INPI")
    logging.info("=" * 50)

    # Solo corre los miercoles (weekday() == 2)
    # Usar FORZAR_VIGILANCIA=true para ejecutar manualmente cualquier dia
    hoy = date.today()
    es_miercoles = hoy.weekday() == 2
    forzar = os.environ.get("FORZAR_VIGILANCIA", "").lower() == "true"

    if not es_miercoles and not forzar:
        logging.info(f"Hoy es {hoy.strftime('%A %d/%m/%Y')} — no es miercoles. Sin accion.")
        return

    logging.info(f"Ejecutando vigilancia del {hoy.strftime('%d/%m/%Y')}")

    portfolio = cargar_portfolio()
    logging.info(f"Portfolio: {len(portfolio)} marcas")

    # Detectar todos los boletines de Marcas Nuevas de esta semana
    boletines = detectar_boletines()
    logging.info(f"Boletines a procesar: {boletines}")

    # Descargar y parsear todos
    todas_marcas = []
    actas_vistas = set()
    boletines_ok = []
    for num in boletines:
        try:
            ruta = descargar(num)
            marcas = parsear(ruta)
            logging.info(f"Boletin #{num}: {len(marcas)} marcas")
            for m in marcas:
                if m["acta"] not in actas_vistas:
                    actas_vistas.add(m["acta"])
                    todas_marcas.append(m)
            boletines_ok.append(num)
        except Exception as e:
            logging.error(f"Error con boletin #{num}: {e}")

    if not todas_marcas:
        logging.error("No se pudieron descargar boletines.")
        return

    logging.info(f"Total marcas unicas relevadas: {len(todas_marcas)}")

    # Analizar
    resultados = analizar(todas_marcas, portfolio)
    logging.info(f"Marcas propias con conflictos: {len(resultados)}")

    # Generar reporte
    fecha_str = date.today().strftime("%d/%m/%Y")
    html = generar_html(resultados, boletines_ok, fecha_str, len(todas_marcas))
    guardar_reporte(html, boletines_ok)

    # Enviar email
    enviar_email(html, boletines_ok, resultados)
    # Marcar boletines como procesados para no repetirlos
    marcar_procesados(boletines_ok)
    logging.info("Proceso completado.")

if __name__ == "__main__":
    main()
