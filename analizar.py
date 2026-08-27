"""
Vigilancia Marcaria INPI — Script para GitHub Actions
Corre automáticamente cada miércoles via GitHub Actions.
Descarga el boletín de Marcas Nuevas, analiza similitudes
con el portfolio y envía alertas por email.
"""

import os, re, json, smtplib, unicodedata, requests, pdfplumber
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from jellyfish import jaro_winkler_similarity

# ── Configuración desde variables de entorno (GitHub Secrets) ──────────────────
EMAIL       = os.environ.get("EMAIL", "estudiodavo@gmail.com")
APP_PASS    = os.environ.get("GMAIL_APP_PASSWORD", "")
UMBRAL      = int(os.environ.get("UMBRAL_SIMILITUD", "70"))
BOLETIN_REF = 11067
BOLETIN_DATE= date(2026, 6, 24)

# ── Clases vinculadas (criterio OMPI) ─────────────────────────────────────────
CLASES_VINCULADAS = {
    29:[30,31,43], 30:[29,31,43], 31:[29,30],
    32:[33,43], 33:[32,43], 43:[29,30,32,33],
    35:[36,42], 36:[35,42], 42:[35,36,9], 9:[42,35],
    25:[18,28], 28:[25,41], 41:[35,38,42], 38:[41,42,35],
    40:[42], 11:[20,21], 20:[11,21], 21:[11,20],
}

# ── Portfolio desde data/portfolio.json ───────────────────────────────────────
def cargar_portfolio():
    ruta = os.path.join(os.path.dirname(__file__), "data", "portfolio.json")
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)

# ── Número de boletín estimado ────────────────────────────────────────────────
def numero_boletin(hoy=None):
    hoy = hoy or date.today()
    semanas = round((hoy - BOLETIN_DATE).days / 7)
    return BOLETIN_REF + semanas

# ── Descarga PDF ──────────────────────────────────────────────────────────────
def descargar(num):
    url = f"https://portaltramites.inpi.gob.ar/Uploads/Boletines/{num}_3_.pdf"
    print(f"Descargando boletín #{num}...")
    r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=90)
    r.raise_for_status()
    ruta = f"/tmp/boletin_{num}.pdf"
    with open(ruta, "wb") as f: f.write(r.content)
    print(f"  {len(r.content)//1024} KB descargados")
    return ruta

# ── Parser PDF ────────────────────────────────────────────────────────────────
def parsear(ruta):
    texto = ""
    with pdfplumber.open(ruta) as pdf:
        for page in pdf.pages[1:]:
            t = page.extract_text()
            if t: texto += t + "\n"
    idx = texto.find("MARCAS NUEVAS")
    sec = texto[idx:] if idx != -1 else texto
    marcas = []
    for b in re.split(r'\(21\)\s*Acta\s*', sec)[1:]:
        mA = re.search(r'^(\d+)', b)
        mC = re.search(r'\(51\)\s*Clase\s*(\d+)', b)
        if not mA or not mC: continue
        mD = re.search(r'\(54\)\s*([^\n\r(]{1,80})', b)
        mT = re.search(r'\(73\)\s*([^\n\r(]{1,120})', b)
        den = mD.group(1).strip() if mD else "[FIGURATIVA]"
        tit = re.sub(r'\s*-\s*[A-Z]{2}\s*\*?\s*$', '', mT.group(1).strip()) if mT else ""
        marcas.append({"acta":mA.group(1),"clase":int(mC.group(1)),"denominacion":den or "[FIGURATIVA]","titular":tit})
    print(f"  {len(marcas)} marcas encontradas en el boletín")
    return marcas

# ── Motor de similitud (Otamendi + OMPI) ─────────────────────────────────────
def norm(s):
    s = s.upper()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r'[^A-Z0-9\s]', '', s).strip()

def fonetica(s):
    for a, b in [("QU","K"),("CE","SE"),("CI","SI"),("GE","JE"),("GI","JI"),
                 ("CH","X"),("LL","Y"),("V","B"),("Z","S"),("H","")]:
        s = s.replace(a, b)
    s = re.sub(r'[AEIOU]', '', s)
    return re.sub(r'(.)\1+', r'\1', s)

def similitud(a, b):
    na, nb = norm(a), norm(b)
    if na == nb: return 100
    scores = [jaro_winkler_similarity(na, nb) * 100]
    fa, fb = fonetica(na), fonetica(nb)
    if fa and fb: scores.append(jaro_winkler_similarity(fa, fb) * 100)
    if na in nb or nb in na: scores.append(85)
    wa = [w for w in na.split() if len(w) > 3]
    wb = [w for w in nb.split() if len(w) > 3]
    if wa and wb:
        comunes = set(wa) & set(wb)
        if comunes: scores.append(60 + len(comunes)/max(len(wa),len(wb)) * 30)
    return round(max(scores))

def riesgo(score, rel):
    if not rel: return None
    if score >= 90 or (score >= 80 and rel == "idéntica"): return "ALTO"
    if score >= 70 and rel == "idéntica": return "ALTO"
    if score >= 70 and rel == "vinculada": return "MEDIO"
    if score >= 55 and rel == "idéntica": return "MEDIO"
    if score >= 55: return "BAJO"
    if score >= 40 and rel == "idéntica": return "BAJO"
    return None

def analizar(boletin, portfolio):
    resultados = {}
    for mi in portfolio:
        conflictos = []
        for mb in boletin:
            c1, c2 = mb["clase"], mi["clase"]
            rel = "idéntica" if c1==c2 else ("vinculada" if c2 in CLASES_VINCULADAS.get(c1,[]) else None)
            if not rel: continue
            sc = similitud(mb["denominacion"], mi["denominacion"])
            if sc < UMBRAL: continue
            rv = riesgo(sc, rel)
            if not rv: continue
            conflictos.append({**mb, "score":sc, "riesgo":rv, "rel_clase":rel})
        if conflictos:
            conflictos.sort(key=lambda x: -x["score"])
            key = f"{mi['denominacion']} (Cl.{mi['clase']})"
            resultados[key] = {"mi_marca": mi, "amenazas": conflictos}
    return resultados

# ── Generar HTML del reporte ─────────────────────────────────────────────────
def generar_html(resultados, num, fecha, total):
    COLOR = {"ALTO":"#ff4d6d","MEDIO":"#ffc93c","BAJO":"#2dd4a0"}
    BG    = {"ALTO":"rgba(255,77,109,.06)","MEDIO":"rgba(255,201,60,.04)","BAJO":"rgba(45,212,160,.03)"}
    ORDEN = {"ALTO":0,"MEDIO":1,"BAJO":2}

    items = sorted(resultados.items(), key=lambda x: min(ORDEN[a["riesgo"]] for a in x[1]["amenazas"]))
    altos  = sum(1 for v in resultados.values() if any(a["riesgo"]=="ALTO"  for a in v["amenazas"]))
    medios = sum(1 for v in resultados.values() if not any(a["riesgo"]=="ALTO" for a in v["amenazas"]) and any(a["riesgo"]=="MEDIO" for a in v["amenazas"]))
    bajos  = len(resultados) - altos - medios

    cards = ""
    for key, data in items:
        mi = data["mi_marca"]
        top = data["amenazas"][0]["riesgo"]
        filas = ""
        for a in data["amenazas"]:
            url = f"https://portaltramites.inpi.gob.ar/MarcasConsultas/Resultado?acta={a['acta']}"
            filas += f"""<tr style="border-bottom:1px solid #2e3350">
              <td style="padding:8px;color:{COLOR[a['riesgo']]};font-weight:700">{a['riesgo']}</td>
              <td style="padding:8px;font-weight:600">{a['denominacion']}</td>
              <td style="padding:8px;color:#7b82a8">Cl. {a['clase']}</td>
              <td style="padding:8px;color:#7b82a8">{a['titular']}</td>
              <td style="padding:8px;color:{COLOR[a['riesgo']]};font-weight:700">{a['score']}%</td>
              <td style="padding:8px;color:#7b82a8">{a['rel_clase']}</td>
              <td style="padding:8px"><a href="{url}" style="color:#4f7fff;font-size:12px">Ver →</a></td>
            </tr>"""
        cards += f"""<div style="margin-bottom:16px;border:1px solid {COLOR[top]}55;border-radius:8px;overflow:hidden;background:{BG[top]}">
          <div style="padding:12px 16px;border-bottom:1px solid {COLOR[top]}33;display:flex;align-items:center;gap:10px">
            <span style="background:{COLOR[top]}22;color:{COLOR[top]};border:1px solid {COLOR[top]}55;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:800">{top}</span>
            <span style="font-weight:700;font-size:15px">🔒 {mi['denominacion']}</span>
            <span style="color:#4f7fff;font-size:12px">Clase {mi['clase']}</span>
            <span style="color:#7b82a8;font-size:12px;margin-left:auto">Acta {mi.get('acta','—')}</span>
          </div>
          <div style="overflow-x:auto">
            <table style="width:100%;border-collapse:collapse;font-size:13px">
              <thead><tr style="background:#1a1d27">
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Riesgo</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Denominación</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Clase</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Titular</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Similitud</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">Relación</th>
                <th style="padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px">INPI</th>
              </tr></thead>
              <tbody>{filas}</tbody>
            </table>
          </div>
        </div>"""

    sin_conflictos = f"""<div style="background:#1a1d27;border:1px solid #2e3350;border-radius:10px;padding:40px;text-align:center;color:#7b82a8">
        ✅ Sin conflictos detectados en el boletín #{num}</div>""" if not resultados else ""

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Vigilancia Marcas — Boletín #{num}</title></head>
<body style="background:#0f1117;color:#e8eaf6;font-family:system-ui,sans-serif;padding:24px;max-width:960px;margin:0 auto">
<div style="background:#1a1d27;border:1px solid #2e3350;border-radius:12px;padding:20px;margin-bottom:20px">
  <h1 style="margin:0 0 4px;font-size:22px">📋 Vigilancia Marcaria — Boletín #{num}</h1>
  <p style="color:#7b82a8;margin:0;font-size:13px">Publicación: {fecha} · {total} marcas relevadas · Criterios: Otamendi + Manual Armonizado OMPI</p>
</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px">
  <div style="background:#1a1d27;border:1px solid #2e3350;border-radius:8px;padding:14px;text-align:center">
    <div style="font-size:11px;color:#7b82a8;text-transform:uppercase">Afectadas</div>
    <div style="font-size:26px;font-weight:800">{len(resultados)}</div>
  </div>
  <div style="background:rgba(255,77,109,.04);border:1px solid #ff4d6d44;border-radius:8px;padding:14px;text-align:center">
    <div style="font-size:11px;color:#7b82a8;text-transform:uppercase">🔴 Alto</div>
    <div style="font-size:26px;font-weight:800;color:#ff4d6d">{altos}</div>
  </div>
  <div style="background:rgba(255,201,60,.03);border:1px solid #ffc93c44;border-radius:8px;padding:14px;text-align:center">
    <div style="font-size:11px;color:#7b82a8;text-transform:uppercase">🟡 Medio</div>
    <div style="font-size:26px;font-weight:800;color:#ffc93c">{medios}</div>
  </div>
  <div style="background:rgba(45,212,160,.02);border:1px solid #2dd4a044;border-radius:8px;padding:14px;text-align:center">
    <div style="font-size:11px;color:#7b82a8;text-transform:uppercase">🟢 Bajo</div>
    <div style="font-size:26px;font-weight:800;color:#2dd4a0">{bajos}</div>
  </div>
</div>
{cards}{sin_conflictos}
<p style="color:#7b82a8;font-size:11px;text-align:center;margin-top:24px">
  Generado automáticamente · estudiodavo@gmail.com · github.com/davoip/marcas-inpi</p>
</body></html>"""

# ── Enviar email ──────────────────────────────────────────────────────────────
def enviar_email(html, num, resultados):
    if not APP_PASS:
        print("GMAIL_APP_PASSWORD no configurado — email no enviado.")
        return
    altos = sum(1 for v in resultados.values() if any(a["riesgo"]=="ALTO" for a in v["amenazas"]))
    if altos:       asunto = f"[Marcas INPI] ⚠ {altos} conflicto(s) ALTO(S) — Boletín #{num}"
    elif resultados:asunto = f"[Marcas INPI] {len(resultados)} conflicto(s) — Boletín #{num}"
    else:           asunto = f"[Marcas INPI] Sin conflictos — Boletín #{num} ✅"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = EMAIL
    msg["To"]   = EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL, APP_PASS)
        smtp.send_message(msg)
    print(f"Email enviado: {asunto}")

# ── Guardar reporte en data/ para GitHub Pages ───────────────────────────────
def guardar_reporte(html, num):
    ruta = os.path.join(os.path.dirname(__file__), "data", f"reporte_{num}.html")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(html)
    # Actualizar índice de reportes
    idx_ruta = os.path.join(os.path.dirname(__file__), "data", "reportes.json")
    reportes = []
    if os.path.exists(idx_ruta):
        with open(idx_ruta) as f:
            reportes = json.load(f)
    if not any(r["num"] == num for r in reportes):
        reportes.insert(0, {"num": num, "fecha": date.today().strftime("%d/%m/%Y"), "archivo": f"reporte_{num}.html"})
    with open(idx_ruta, "w") as f:
        json.dump(reportes[:52], f)  # guardar último año
    print(f"Reporte guardado: {ruta}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("VIGILANCIA MARCARIA INPI")
    print("=" * 50)

    portfolio = cargar_portfolio()
    print(f"Portfolio: {len(portfolio)} marcas")

    num = numero_boletin()
    print(f"Boletín estimado: #{num}")

    try:
        ruta_pdf = descargar(num)
    except requests.HTTPError as e:
        print(f"Error al descargar boletín #{num}: {e}")
        print("Puede que aún no esté publicado. Se reintentará la próxima ejecución.")
        return

    boletin = parsear(ruta_pdf)
    resultados = analizar(boletin, portfolio)
    print(f"Marcas propias con conflictos: {len(resultados)}")

    html = generar_html(resultados, num, date.today().strftime("%d/%m/%Y"), len(boletin))
    guardar_reporte(html, num)
    enviar_email(html, num, resultados)
    print("Proceso completado.")

if __name__ == "__main__":
    main()
