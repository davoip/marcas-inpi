"""
Actualizar Portfolio e Indice de Busqueda via API INPI
Corre todos los dias via GitHub Actions.
Hace 4 cosas:
1. Consulta marcas de agentes 2435 y 2432 (ConsultaCuitOTitular)
2. Agrega marcas nuevas al portfolio.json
3. Consulta novedades de tramites en curso (ConsultaNotificaciones)  
4. Genera indice de busqueda (data/indice_inpi.json) para busquedas web
"""

import os, json, logging, requests, xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

INPI_CUIT   = os.environ.get("INPI_CUIT", "20287461020")
INPI_KEY    = os.environ.get("INPI_API_KEY", "")
INPI_WS_URL = "https://ws.inpi.gob.ar/wsinpi.asmx"
EMAIL       = os.environ.get("EMAIL", "estudiodavo@gmail.com")
APP_PASS    = os.environ.get("GMAIL_APP_PASSWORD", "")
AGENTES     = ["2435", "2432"]

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── Helpers ────────────────────────────────────────────────────────
def cargar_json(nombre, default):
    ruta = DATA_DIR / nombre
    if ruta.exists():
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    return default

def guardar_json(nombre, data):
    with open(DATA_DIR / nombre, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def soap_call(accion, body):
    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>{body}</soap:Body>
</soap:Envelope>"""
    r = requests.post(INPI_WS_URL,
        data=envelope.encode("utf-8"),
        headers={"Content-Type":"text/xml; charset=utf-8",
                 "SOAPAction":f"http://tempuri.org/{accion}"},
        timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.content)

# ── 1. Consultar marcas por agente ────────────────────────────────
def consultar_agente(agente):
    """
    Consulta marcas del agente via ConsultaCuitOTitular.
    XML real del INPI usa tags ns1:GrillaMarcas con hijos:
    Acta, Denominacion, Clase, Titulares, Estado (N=tramite, C=concedida),
    Fecha_Ingreso, Tipo_Marca, Numero_Resolucion
    Solo trae marcas En Tramite (Estado=N o sin Estado) para detectar novedades.
    """
    body = f"""<ConsultaCuitOTitular xmlns="http://tempuri.org/">
      <strCuit>{INPI_CUIT}</strCuit>
      <strClave>{INPI_KEY}</strClave>
      <strAgente>{agente}</strAgente>
    </ConsultaCuitOTitular>"""
    try:
        root = soap_call("ConsultaCuitOTitular", body)
        ns = 'http://tempuri.org/'
        marcas = []

        for grilla in root.iter(f'{{{ns}}}GrillaMarcas'):
            def get(tag):
                el = grilla.find(f'{{{ns}}}{tag}')
                return el.text.strip() if el is not None and el.text else ''

            acta  = get('Acta')
            den   = get('Denominacion')
            clase = get('Clase')
            tit   = get('Titulares')
            est_raw = get('Estado')   # N=tramite, C=concedida, T=caduca, etc
            fecha = get('Fecha_Ingreso')
            tipo  = get('Tipo_Marca')
            resol = get('Numero_Resolucion')

            if not acta or not den:
                continue

            # Mapear estado
            estado_map = {
                'N': 'En Trámite', 'C': 'Concedida', 'T': 'Caducada',
                'D': 'Denegada', 'A': 'Abandonada', 'S': 'Desistida'
            }
            estado = estado_map.get(est_raw, 'En Trámite' if not est_raw else est_raw)

            # Limpiar titular (a veces viene con CUIT adelante: "20123456789  NOMBRE 100.00%")
            import re
            tit_clean = re.sub(r'^\d{11}\s+', '', tit)
            tit_clean = re.sub(r'\s+\d+\.\d+%$', '', tit_clean).strip()

            # Fecha de ingreso formateada
            fecha_fmt = ''
            if fecha and 'T' in fecha:
                try:
                    from datetime import datetime
                    fecha_fmt = datetime.fromisoformat(fecha.split('T')[0]).strftime('%d/%m/%Y')
                except: fecha_fmt = fecha[:10]

            marcas.append({
                "acta": acta,
                "denominacion": den,
                "clase": int(clase) if clase.isdigit() else 0,
                "estado": estado,
                "titular": tit_clean,
                "tipo_marca": tipo,
                "fecha_ingreso": fecha_fmt,
                "numero_resolucion": resol,
                "agente": agente
            })

        logging.info(f"  Agente {agente}: {len(marcas)} marcas totales en INPI")

        # Solo devolver las En Tramite para detectar novedades
        en_tramite = [m for m in marcas if m['estado'] == 'En Trámite']
        logging.info(f"  Agente {agente}: {len(en_tramite)} marcas En Tramite")
        return en_tramite

    except Exception as e:
        logging.error(f"Error agente {agente}: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return []

# ── 2. Consultar novedades de un tramite ──────────────────────────
def consultar_notificaciones(acta):
    body = f"""<ConsultaNotificaciones xmlns="http://tempuri.org/">
      <strCuit>{INPI_CUIT}</strClave>
      <strClave>{INPI_KEY}</strClave>
      <strActa>{acta}</strActa>
    </ConsultaNotificaciones>"""
    try:
        root = soap_call("ConsultaNotificaciones", body)
        notifs = []
        for elem in root.iter():
            if elem.text and elem.text.strip() and len(elem.text.strip()) > 5:
                tag = elem.tag.split('}')[-1].lower()
                if any(k in tag for k in ['notif','estado','resol','mensaje']):
                    notifs.append(elem.text.strip())
        return list(set(notifs))
    except Exception as e:
        logging.warning(f"Error notif acta {acta}: {e}")
        return []

# ── 3. Consultar denominacion para indice de busqueda ─────────────
def consultar_denominacion(denominacion, clase=""):
    body = f"""<ConsultaDenominacion xmlns="http://tempuri.org/">
      <strCuit>{INPI_CUIT}</strCuit>
      <strClave>{INPI_KEY}</strClave>
      <strDenominacion>{denominacion}</strDenominacion>
      <strClase>{clase}</strClase>
    </ConsultaDenominacion>"""
    try:
        root = soap_call("ConsultaDenominacion", body)
        marcas = []
        for elem in root.iter():
            acta  = elem.get('acta') or elem.get('Acta')
            den   = elem.get('denominacion') or elem.get('Denominacion')
            clase_v = elem.get('clase') or elem.get('Clase')
            est   = elem.get('estado') or elem.get('Estado','')
            tit   = elem.get('titular') or elem.get('Titular','')
            if acta and den:
                marcas.append({
                    "acta": acta.strip(), "denominacion": den.strip(),
                    "clase": int(clase_v) if clase_v and str(clase_v).isdigit() else 0,
                    "estado": est.strip(), "titular": tit.strip()
                })
        return marcas
    except Exception as e:
        logging.warning(f"Error ConsultaDenominacion '{denominacion}': {e}")
        return []

# ── 4. Generar indice de busqueda ─────────────────────────────────
def generar_indice(portfolio):
    """
    Genera un indice JSON con todas las marcas del portfolio
    mas datos adicionales de la API para busquedas rapidas desde la web.
    Se actualiza diariamente.
    """
    indice = {
        "fecha_actualizacion": date.today().strftime("%d/%m/%Y"),
        "total_marcas": len(portfolio),
        "marcas": portfolio,
        "por_titular": {},
        "por_clase": {}
    }
    # Agrupar por titular — normalizar para busqueda
    for m in portfolio:
        tit = m.get("titular", "").strip()
        if tit:
            if tit not in indice["por_titular"]:
                indice["por_titular"][tit] = []
            indice["por_titular"][tit].append(m)
        # También indexar por denominacion para busquedas por nombre
        den = m.get("denominacion", "").strip()
        if den:
            if den not in indice["por_titular"]:
                indice["por_titular"][den] = []
    # Agrupar por clase
    for m in portfolio:
        cl = str(m.get("clase", ""))
        if cl not in indice["por_clase"]:
            indice["por_clase"][cl] = []
        indice["por_clase"][cl].append(m)

    guardar_json("indice_inpi.json", indice)
    logging.info(f"Indice generado: {len(portfolio)} marcas, {len(indice['por_titular'])} titulares")

# ── Email de novedades ─────────────────────────────────────────────
def enviar_email(nuevas, novedades_tramites):
    if not APP_PASS or (not nuevas and not novedades_tramites):
        return
    hoy = date.today().strftime("%d/%m/%Y")
    asunto = f"[Marcas INPI] Novedades {hoy}"
    if nuevas:
        asunto = f"[Marcas INPI] {len(nuevas)} marca(s) nueva(s) — {hoy}"

    def tabla(filas_html, titulo, color):
        return f"""<div style="margin-bottom:16px">
          <div style="font-weight:700;color:{color};margin-bottom:8px">{titulo}</div>
          <div style="background:#1a1d27;border:1px solid {color}44;border-radius:8px;overflow:hidden">
            <table style="width:100%;border-collapse:collapse;font-size:13px">{filas_html}</table>
          </div></div>"""

    filas_n = "<thead><tr style='background:#22263a'>" + \
        "".join(f"<th style='padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px'>{h}</th>"
                for h in ["Denominación","Clase","Estado","Acta","Agente"]) + \
        "</tr></thead><tbody>" + \
        "".join(f"<tr style='border-bottom:1px solid #2e3350'>" +
                f"<td style='padding:8px;font-weight:600'>{m.get('denominacion','')}</td>" +
                f"<td style='padding:8px;color:#4f7fff'>Cl.{m.get('clase','')}</td>" +
                f"<td style='padding:8px;color:#ffc93c'>{m.get('estado','')}</td>" +
                f"<td style='padding:8px;color:#7b82a8'>{m.get('acta','')}</td>" +
                f"<td style='padding:8px;color:#7b82a8'>Ag.{m.get('agente','')}</td></tr>"
                for m in nuevas) + "</tbody>" if nuevas else ""

    filas_t = "<thead><tr style='background:#22263a'>" + \
        "".join(f"<th style='padding:6px 8px;text-align:left;color:#7b82a8;font-size:11px'>{h}</th>"
                for h in ["Acta","Denominación","Novedad"]) + \
        "</tr></thead><tbody>" + \
        "".join(f"<tr style='border-bottom:1px solid #2e3350'>" +
                f"<td style='padding:8px;color:#4f7fff'>{n.get('acta','')}</td>" +
                f"<td style='padding:8px;font-weight:600'>{n.get('denominacion','')}</td>" +
                f"<td style='padding:8px;color:#7b82a8'>{n.get('novedad','')}</td></tr>"
                for n in novedades_tramites) + "</tbody>" if novedades_tramites else ""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="background:#0f1117;color:#e8eaf6;font-family:system-ui,sans-serif;padding:24px;max-width:800px;margin:0 auto">
  <div style="background:#1a1d27;border:1px solid #2e3350;border-radius:12px;padding:20px;margin-bottom:20px">
    <h1 style="margin:0 0 6px;font-size:20px">Novedades Marcarias — {hoy}</h1>
    <p style="color:#7b82a8;margin:0;font-size:13px">Agentes 2435 y 2432 · Estudio Davo</p>
  </div>
  {tabla(filas_n, '✅ MARCAS NUEVAS DETECTADAS', '#2dd4a0') if nuevas else ''}
  {tabla(filas_t, '📋 NOVEDADES EN TRÁMITES', '#ffc93c') if novedades_tramites else ''}
  <p style="color:#7b82a8;font-size:11px;text-align:center;margin-top:24px">
    Generado automaticamente · estudiodavo@gmail.com
  </p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = EMAIL
    msg["To"] = EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL, APP_PASS)
        smtp.send_message(msg)
    logging.info(f"Email enviado: {asunto}")

# ── Consultar estado detallado de un tramite ─────────────────────
def consultar_estado_detallado(acta):
    """
    Consulta notificaciones de un tramite para obtener sub-estado y fecha de publicacion.
    Usa el namespace real del INPI: http://tempuri.org/
    """
    body = f"""<ConsultaNotificaciones xmlns="http://tempuri.org/">
      <strCuit>{INPI_CUIT}</strCuit>
      <strClave>{INPI_KEY}</strClave>
      <strActa>{acta}</strActa>
    </ConsultaNotificaciones>"""
    try:
        root = soap_call("ConsultaNotificaciones", body)
        ns = 'http://tempuri.org/'
        datos = {}

        # Loguear XML para debug en primera ejecucion
        xml_str = ET.tostring(root, encoding='unicode')
        if 'GrillaNotificaciones' in xml_str or 'Notificacion' in xml_str:
            logging.debug(f"Notif acta {acta}: {xml_str[:500]}")

        # Buscar en todos los elementos con el namespace
        for elem in root.iter():
            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            val = (elem.text or '').strip()
            if not val: continue

            tag_l = tag.lower()
            if any(k in tag_l for k in ['subestado','sub_estado','etapa','fase',
                                          'division','estado_actual','tiponotif']):
                datos['sub_estado'] = val
            elif any(k in tag_l for k in ['fechaestado','fecha_estado','fechacambio',
                                            'fecha_cambio','fechanotif','fecha_notif']):
                # Formatear fecha
                try:
                    from datetime import datetime
                    if 'T' in val:
                        val = datetime.fromisoformat(val.split('T')[0]).strftime('%d/%m/%Y')
                    datos['fecha_estado'] = val
                except: datos['fecha_estado'] = val
            elif any(k in tag_l for k in ['fechapub','fecha_pub','fechaboletin',
                                            'fecha_boletin','publicacion','fechaPublicacion']):
                try:
                    from datetime import datetime
                    if 'T' in val:
                        val = datetime.fromisoformat(val.split('T')[0]).strftime('%d/%m/%Y')
                    datos['fecha_publicacion'] = val
                except: datos['fecha_publicacion'] = val

        if datos:
            logging.debug(f"  Acta {acta} estado: {datos}")
        return datos
    except Exception as e:
        logging.debug(f"Error estado detallado acta {acta}: {e}")
        return {}

# ── Main ───────────────────────────────────────────────────────────
def main():
    logging.info("="*50)
    logging.info("ACTUALIZACION DIARIA PORTFOLIO + INDICE INPI")
    logging.info("="*50)

    if not INPI_KEY:
        logging.warning("INPI_API_KEY no configurada. Saliendo.")
        return

    portfolio = cargar_json("portfolio.json", [])
    actas_existentes = {m["acta"] for m in portfolio if m.get("acta")}
    logging.info(f"Portfolio actual: {len(portfolio)} marcas")

    # 1. Detectar marcas nuevas de los agentes
    nuevas = []
    for agente in AGENTES:
        logging.info(f"Consultando agente {agente}...")
        for m in consultar_agente(agente):
            if m.get("acta") and m["acta"] not in actas_existentes:
                nuevas.append(m)
                actas_existentes.add(m["acta"])
                logging.info(f"  NUEVA: {m['denominacion']} Cl.{m['clase']} Acta {m['acta']}")

    if nuevas:
        portfolio.extend(nuevas)
        guardar_json("portfolio.json", portfolio)
        logging.info(f"+{len(nuevas)} marcas nuevas agregadas al portfolio")
    else:
        logging.info("Sin marcas nuevas.")

    # Actualizar estado detallado de tramites en curso
    logging.info("Actualizando estado detallado de tramites...")
    actualizados = 0
    tramites_activos = [m for m in portfolio
                       if m.get("estado","").strip().lower() in
                       ("en trámite","en tramite","en tr\u00e1mite","tramite","trámite")
                       and m.get("acta") and m["acta"] != "—"][:30]
    logging.info(f"Tramites activos para actualizar estado: {len(tramites_activos)}")  # max 30/dia
    for m in tramites_activos:
        datos = consultar_estado_detallado(m["acta"])
        if datos:
            m.update(datos)
            actualizados += 1
    if actualizados:
        guardar_json("portfolio.json", portfolio)
        logging.info(f"Estado detallado actualizado: {actualizados} marcas")

    # 2. Consultar novedades en tramites
    novedades_hist = cargar_json("novedades.json", [])
    novedades_hoy = []
    tramites = [m for m in portfolio
                if m.get("estado","").lower() in ("en trámite","en tramite")
                and m.get("acta")][:20]  # max 20/dia

    for m in tramites:
        for notif in consultar_notificaciones(m["acta"]):
            entrada = {"acta": m["acta"], "denominacion": m.get("denominacion",""),
                      "novedad": notif, "fecha": date.today().strftime("%d/%m/%Y")}
            if not any(n["acta"]==m["acta"] and n["novedad"]==notif
                      for n in novedades_hist):
                novedades_hoy.append(entrada)
                novedades_hist.insert(0, entrada)

    if novedades_hoy:
        guardar_json("novedades.json", novedades_hist[:200])
        logging.info(f"{len(novedades_hoy)} novedades nuevas en tramites")

    # 3. Generar indice de busqueda para la web
    generar_indice(portfolio)

    # 4. Enviar email si hay novedades
    logging.info("Proceso completado.")

if __name__ == "__main__":
    main()
