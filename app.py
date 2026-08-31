"""
Servidor intermediario Railway para busquedas en la API del INPI.
Recibe denominacion + clases desde la web y consulta ConsultaDenominacion.
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, xml.etree.ElementTree as ET, os, unicodedata, re

app = Flask(__name__)
CORS(app)

INPI_CUIT = os.environ.get("INPI_CUIT", "20287461020")
INPI_KEY  = os.environ.get("INPI_API_KEY", "")
INPI_WS   = "https://ws.inpi.gob.ar/wsinpi.asmx"

CLASES_VINCULADAS = {
    29:[30,31,43],30:[29,31,43],31:[29,30],32:[33,43],33:[32,43],43:[29,30,32,33],
    35:[36,42,38],36:[35,42],42:[35,36,9,40],9:[42,35,38],38:[41,42,35],40:[42,37],
    25:[18,28,24],18:[25,14],28:[25,41],41:[35,38,42,28],
    44:[45,35,42],5:[44,3,31],11:[20,21,19],20:[11,21,19],21:[11,20],
    19:[37,20,40],37:[19,40,42],14:[25,18,35],45:[35,42,36,44],16:[41,35,9],
}

TERMINOS_DEBILES = {
    'SERVICIOS','PRODUCTOS','GRUPO','ESTUDIO','EMPRESA','CENTRO','CLUB',
    'DIGITAL','TECH','NET','WEB','ONLINE','GLOBAL','ARGENTINA','COMERCIAL',
    'INDUSTRIAL','NACIONAL','SUPER','MEGA','MAX','PLUS','PRO','PREMIUM',
    'ELITE','MASTER','GYM','FITNESS','SPORT','HOME','SHOP','STORE',
    'MARKET','EXPRESS','TOTAL','REAL','NUEVA','NUEVO','GRAN','GRANDE'
}

def norm(s):
    s = s.upper()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Z0-9\s]', '', s).strip()

def fonetica(s):
    for a, b in [("GU","G"),("QU","K"),("CE","SE"),("CI","SI"),("GE","JE"),
                 ("GI","JI"),("CH","X"),("LL","Y"),("PH","F"),("TH","T"),
                 ("W","V"),("V","B"),("Z","S"),("H","")]:
        s = s.replace(a, b)
    s = re.sub(r'[AEIOU]', '', s)
    return re.sub(r'(.)\1+', r'\1', s)

def jaro_winkler(s1, s2):
    if s1 == s2: return 1.0
    if not s1 or not s2: return 0.0
    md = max(0, max(len(s1), len(s2)) // 2 - 1)
    m1 = [False]*len(s1); m2 = [False]*len(s2)
    matches = 0; t = 0
    for i in range(len(s1)):
        for j in range(max(0,i-md), min(i+md+1, len(s2))):
            if m2[j] or s1[i] != s2[j]: continue
            m1[i] = m2[j] = True; matches += 1; break
    if not matches: return 0.0
    k = 0
    for i in range(len(s1)):
        if not m1[i]: continue
        while not m2[k]: k += 1
        if s1[i] != s2[k]: t += 1
        k += 1
    j = (matches/len(s1)+matches/len(s2)+(matches-t/2)/matches)/3
    p = sum(1 for i in range(min(4,len(s1),len(s2))) if s1[i]==s2[i] and not any(s1[ii]!=s2[ii] for ii in range(i)))
    return min(1.0, j + p * 0.1 * (1 - j))

def calc_sim(a, b):
    na, nb = norm(a), norm(b)
    if na == nb: return 100
    scores = []
    scores.append(jaro_winkler(na, nb) * 100)
    corte = max(3, int(max(len(na), len(nb)) * 0.45))
    si = jaro_winkler(na[:corte], nb[:corte]) * 100
    if si > scores[0]: scores.append(si * 0.92 + scores[0] * 0.08)
    fa, fb = fonetica(na), fonetica(nb)
    if fa and fb:
        scores.append(jaro_winkler(fa, fb) * 100)
        fia, fib = fonetica(na[:corte]), fonetica(nb[:corte])
        if fia and fib: scores.append(jaro_winkler(fia, fib) * 100 * 0.92)
    if na in nb or nb in na:
        r = min(len(na),len(nb))/max(len(na),len(nb))
        scores.append(68 + r * 22)
    wA = [w for w in na.split() if len(w)>2 and w not in TERMINOS_DEBILES]
    wB = [w for w in nb.split() if len(w)>2 and w not in TERMINOS_DEBILES]
    if wA and wB:
        com = set(wA) & set(wB)
        if com:
            r = len(com)/max(len(wA),len(wB))
            prim = wA[0]==wB[0] if wA and wB else False
            scores.append(55 + r*30 + (12 if prim else 0))
    return round(max(scores))

def nivel_riesgo(score, rel):
    if not rel: return None
    if score >= 95: return 'ALTO'
    if score >= 80 and rel == 'identica': return 'ALTO'
    if score >= 78 and rel == 'vinculada': return 'ALTO'
    if score >= 65 and rel == 'identica': return 'MEDIO'
    if score >= 62 and rel == 'vinculada': return 'MEDIO'
    if score >= 50 and rel == 'identica': return 'BAJO'
    if score >= 48 and rel == 'vinculada': return 'BAJO'
    return None

def rel_clase(c1, c2):
    if c1 == c2: return 'identica'
    if c2 in CLASES_VINCULADAS.get(c1, []): return 'vinculada'
    return None

def soap_call(accion, body):
    envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>{body}</soap:Body>
</soap:Envelope>"""
    r = requests.post(INPI_WS, data=envelope.encode('utf-8'),
        headers={"Content-Type":"text/xml; charset=utf-8",
                 "SOAPAction":f"http://tempuri.org/{accion}"},
        timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.content)

def consultar_denominacion(denominacion, clase=""):
    body = f"""<ConsultaDenominacion xmlns="http://tempuri.org/">
      <strCuit>{INPI_CUIT}</strCuit>
      <strClave>{INPI_KEY}</strClave>
      <strDenominacion>{denominacion}</strDenominacion>
      <strClase>{clase}</strClase>
    </ConsultaDenominacion>"""
    root = soap_call("ConsultaDenominacion", body)
    ns = 'http://tempuri.org/'
    marcas = []
    for grilla in root.iter(f'{{{ns}}}GrillaMarcas'):
        def get(tag):
            el = grilla.find(f'{{{ns}}}{tag}')
            return el.text.strip() if el is not None and el.text else ''
        acta = get('Acta'); den = get('Denominacion'); clase_v = get('Clase')
        est_raw = get('Estado'); tit = get('Titulares')
        if not acta or not den: continue
        estado_map = {'N':'En Tramite','C':'Concedida','T':'Caducada',
                      'D':'Denegada','A':'Abandonada','S':'Desistida'}
        estado = estado_map.get(est_raw, est_raw or 'En Tramite')
        tit_clean = re.sub(r'^\d{11}\s+', '', tit)
        tit_clean = re.sub(r'\s+\d+\.\d+%$', '', tit_clean).strip()
        marcas.append({
            "acta": acta, "denominacion": den,
            "clase": int(clase_v) if clase_v.isdigit() else 0,
            "estado": estado, "titular": tit_clean
        })
    return marcas

@app.route('/ping')
def ping():
    return jsonify({"status": "ok", "inpi_key": bool(INPI_KEY)})

@app.route('/buscar', methods=['POST'])
def buscar():
    data = request.get_json()
    denominacion = data.get('denominacion', '').strip().upper()
    clases = data.get('clases', list(range(1, 46)))
    umbral = data.get('umbral', 60)

    if not denominacion:
        return jsonify({"error": "denominacion requerida"}), 400
    if not INPI_KEY:
        return jsonify({"error": "API INPI no configurada"}), 503

    resultados = []
    errores = []

    for clase in clases:
        try:
            marcas = consultar_denominacion(denominacion, str(clase))
            for m in marcas:
                rel = rel_clase(clase, m['clase']) or rel_clase(m['clase'], clase)
                if not rel: rel = 'identica' if m['clase']==clase else None
                if not rel: continue
                sc = calc_sim(denominacion, m['denominacion'])
                if sc < umbral: continue
                rv = nivel_riesgo(sc, rel)
                if not rv: continue
                # Evitar duplicados por acta
                if not any(r['acta']==m['acta'] for r in resultados):
                    resultados.append({**m, 'score': sc, 'riesgo': rv, 'rel_clase': rel})
        except Exception as e:
            errores.append(f"Clase {clase}: {str(e)}")

    resultados.sort(key=lambda x: -x['score'])
    return jsonify({
        "denominacion": denominacion,
        "total": len(resultados),
        "resultados": resultados[:100],
        "errores": errores[:5] if errores else []
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
