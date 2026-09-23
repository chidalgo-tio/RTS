# -*- coding: utf-8 -*-
"""Capacidad planeada de Amazon Logistics, por estacion y por dia.

    https://logistics.amazon.com.mx/scheduling/route-plan

De cada dia saca lo que Amazon PIDE y lo que la empresa ACEPTA. Los tres
numeros salen de la misma API que usa el panel:

    GET /scheduling/home/api/v2/route-plan
        ?serviceAreaId=<uuid>&fromDate=<aaaa-mm-dd>&toDate=<aaaa-mm-dd>
        &includeCapcoms=true

Los nombres de los campos estan verificados contra la propia pantalla
(DMT6, semana del 20 al 26 de septiembre de 2026):

    Destinos de ruta   62  ==  suma de routeTarget          62
    Capacidad          56  ==  suma de dspCapacity          56
    Bloques de trabajo 70  ==  suma de reservationQuantity  70

Ojo con esto: cada dia trae VARIAS filas, una por tipo de servicio
(Standard Parcel, Nursery Route, Large Van with Helper...). El total del
dia es la suma de todas. Mirar solo la primera da numeros incompletos.

No toca credenciales: la sesion la abre la persona en Chrome y las
peticiones salen desde la propia pagina con fetch().
"""
import os
import sys
import csv
import json
import time
import re
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

RAIZ = "https://logistics.amazon.com.mx"
URL = RAIZ + "/scheduling/route-plan"
API = RAIZ + "/scheduling/home/api/v2/route-plan"
API_AREAS = RAIZ + "/scheduling/home/api/v2/service-areas"
DOMINIO = "logistics.amazon.com.mx"

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PROFILE_DIR = os.path.join(BASE_DIR, "chrome_amazon")

# Las cuatro de Monterrey. Los ids se confirman al arrancar contra
# service-areas: si Amazon los cambia, se avisa en vez de fallar mudo.
ESTACIONES = {
    "DMT2": "425b0800-37d1-4238-a690-ac6d99a48284",
    "DMT3": "e57fc23e-a7da-4a28-912e-b5f96d26db73",
    "DMT4": "29abf922-720a-4f05-ae8a-85867f6d98e7",
    "DMT6": "41734a2d-d28c-4d96-9d85-b8c4f2e0ce5c",
}

PAUSA = 0.3
DIAS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes",
           "sabado", "domingo"]


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def limpiar(texto):
    if texto is None:
        return ""
    t = str(texto).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", t).strip()


def entero(valor):
    """None y '' cuentan como 0 al sumar, pero se distinguen al mostrar."""
    return valor if isinstance(valor, int) else 0


def cerrar_huerfanos():
    """Chrome que quedo vivo agarrado a este perfil tras un cierre brusco.

    Si no se cierran, el siguiente arranque abre una ventana en blanco
    sin explicar por que.
    """
    if os.name != "nt":
        return
    import subprocess
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -like '*%s*' } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
              % os.path.basename(PROFILE_DIR))
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def crear_driver():
    cerrar_huerfanos()
    o = Options()
    o.add_argument("--user-data-dir=" + PROFILE_DIR)
    o.add_argument("--window-size=1500,1000")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=o)


SCRIPT_GET = """
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}})
  .then(r => r.text().then(t => done({ok: r.ok, status: r.status, body: t})))
  .catch(e => done({ok: false, status: 0, body: String(e)}));
"""


def _pedir(driver, url):
    driver.set_script_timeout(120)
    r = driver.execute_async_script(SCRIPT_GET, url)
    if not r or not r.get("ok"):
        estado = (r or {}).get("status", "?")
        detalle = (r or {}).get("body", "")[:300]
        if estado == 0 and "failed to fetch" in detalle.lower():
            raise RuntimeError(
                "El navegador bloqueo la consulta. Chrome debe estar en "
                "logistics.amazon.com.mx.")
        if estado in (401, 403):
            raise RuntimeError(
                "Amazon no reconoce la sesion (%s). Vuelve a entrar en "
                "Chrome." % estado)
        raise RuntimeError("La API respondio %s: %s" % (estado, detalle))
    return json.loads(r["body"])


def hay_sesion(driver):
    """Dentro es que la API conteste, no que la pagina pese mucho.

    La pantalla de login tambien carga y tiene tamano; lo que no hace es
    devolver el catalogo de estaciones.
    """
    try:
        j = _pedir(driver, API_AREAS)
    except Exception:
        return False
    return bool(_lista_areas(j))


def _lista_areas(j):
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("serviceAreas", "data", "areas"):
            v = j.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                for k2 in ("serviceAreas", "areas"):
                    if isinstance(v.get(k2), list):
                        return v[k2]
    return []


def catalogo(driver):
    """Nombre e id de cada estacion, como los publica Amazon."""
    salida = {}
    for a in _lista_areas(_pedir(driver, API_AREAS)):
        if not isinstance(a, dict):
            continue
        nombre = limpiar(a.get("name") or a.get("serviceAreaName"))
        sid = limpiar(a.get("id") or a.get("serviceAreaId"))
        if nombre and sid:
            salida[nombre] = sid
    return salida


def revisar_ids(driver, avisar=None):
    """Confirma los ids fijos contra el catalogo de hoy.

    Si Amazon reasigna un id, sale un aviso con el nuevo en vez de dejar
    una estacion vacia sin motivo aparente.
    """
    avisar = avisar or (lambda *a: None)
    try:
        cat = catalogo(driver)
    except Exception as e:
        avisar("No se pudo leer el catalogo de estaciones: %s" % str(e)[:120])
        return {}
    vivos, cambios = {}, []
    for clave in ESTACIONES:
        for nombre, sid in cat.items():
            if clave in nombre.upper():
                vivos[clave] = sid
                if sid != ESTACIONES[clave]:
                    cambios.append("%s cambio de id" % clave)
                break
        else:
            cambios.append("%s ya no aparece en el catalogo" % clave)
    for c in cambios:
        avisar("AVISO: %s." % c)
    return vivos


def plan(driver, sid, desde, hasta):
    url = ("%s?serviceAreaId=%s&fromDate=%s&toDate=%s&includeCapcoms=true"
           % (API, sid, desde, hasta))
    return _pedir(driver, url)


def normalizar(estacion, fecha, fila):
    """Una fila por estacion, dia y tipo de servicio."""
    return {
        "estacion": estacion,
        "fecha": fecha,
        "dia": DIAS_ES[datetime.strptime(fecha, "%Y-%m-%d").weekday()],
        "servicio": limpiar(fila.get("serviceTypeName")),
        "ciclo": limpiar(fila.get("cycleId")),
        # Los tres de la pantalla, verificados contra DMT6:
        "solicitado": fila.get("routeTarget"),        # Destinos de ruta
        "aceptado": fila.get("dspCapacity"),          # Capacidad DSP
        "bloques": fila.get("reservationQuantity"),   # Bloques de trabajo
        "asignado": fila.get("rosteredQuantity"),     # ya cubierto
        "suplementario": fila.get("supplementalRouteTarget"),
        "principal": bool(fila.get("isPrimaryWorkBlock")),
    }


def extraer(driver, desde, hasta, estaciones=None, avisar=None):
    """Todas las filas de todas las estaciones en el rango."""
    avisar = avisar or (lambda *a: None)
    usar = estaciones or ESTACIONES
    filas, sin_datos = [], []
    for nom, sid in usar.items():
        try:
            j = plan(driver, sid, desde, hasta)
        except Exception as e:
            avisar("%s fallo: %s" % (nom, str(e)[:110]))
            continue
        por_fecha = ((j.get("data") or {}).get("planSummariesByDate") or {})
        if not por_fecha:
            sin_datos.append(nom)
            continue
        n = 0
        for fecha in sorted(por_fecha):
            for e in por_fecha[fecha] or []:
                filas.append(normalizar(nom, fecha, e))
                n += 1
        avisar("  %s: %d filas en %d dias" % (nom, n, len(por_fecha)))
        time.sleep(PAUSA)
    if sin_datos:
        avisar("Sin plan publicado: %s." % ", ".join(sin_datos))
    return filas


def resumir(filas):
    """Un renglon por estacion y dia, sumando los tipos de servicio.

    Cada dia trae varias filas -Standard Parcel, Nursery, Large Van with
    Helper- y el numero de la pantalla es la suma de todas.
    """
    por = {}
    for f in filas:
        c = (f["estacion"], f["fecha"])
        g = por.setdefault(c, {
            "estacion": f["estacion"], "fecha": f["fecha"], "dia": f["dia"],
            "solicitado": 0, "aceptado": 0, "bloques": 0, "asignado": 0,
            "servicios": 0,
        })
        g["solicitado"] += entero(f["solicitado"])
        g["aceptado"] += entero(f["aceptado"])
        g["bloques"] += entero(f["bloques"])
        g["asignado"] += entero(f["asignado"])
        g["servicios"] += 1
    return [por[k] for k in sorted(por)]


COLUMNAS_DETALLE = [
    ("estacion", "ESTACION"),
    ("fecha", "FECHA"),
    ("dia", "DIA"),
    ("servicio", "TIPO DE SERVICIO"),
    ("ciclo", "CICLO"),
    ("solicitado", "SOLICITADO POR AMAZON"),
    ("aceptado", "ACEPTADO POR LA EMPRESA"),
    ("bloques", "BLOQUES DE TRABAJO"),
    ("asignado", "YA ASIGNADO"),
    ("suplementario", "SUPLEMENTARIO"),
    ("principal", "BLOQUE PRINCIPAL"),
]

COLUMNAS_RESUMEN = [
    ("estacion", "ESTACION"),
    ("fecha", "FECHA"),
    ("dia", "DIA"),
    ("solicitado", "SOLICITADO POR AMAZON"),
    ("aceptado", "ACEPTADO POR LA EMPRESA"),
    ("diferencia", "DIFERENCIA"),
    ("bloques", "BLOQUES DE TRABAJO"),
    ("asignado", "YA ASIGNADO"),
    ("servicios", "TIPOS DE SERVICIO"),
]


def guardar(filas, desde, hasta):
    """Dos CSV: el resumen por dia y el detalle por tipo de servicio."""
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    rango = desde if desde == hasta else "%s_a_%s" % (desde, hasta)

    res = resumir(filas)
    for g in res:
        g["diferencia"] = g["aceptado"] - g["solicitado"]

    p_res = os.path.join(BASE_DIR, "amazon_capacidad_%s_%s.csv"
                         % (rango, sello))
    with open(p_res, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([t for _, t in COLUMNAS_RESUMEN])
        for g in res:
            w.writerow([g.get(k, "") for k, _ in COLUMNAS_RESUMEN])

    p_det = os.path.join(BASE_DIR, "amazon_detalle_%s_%s.csv"
                         % (rango, sello))
    with open(p_det, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([t for _, t in COLUMNAS_DETALLE])
        for r in filas:
            w.writerow(["" if r.get(k) is None else r.get(k)
                        for k, _ in COLUMNAS_DETALLE])
    return p_res, p_det


def semana_de(fecha_iso):
    """El domingo y el sabado de la semana que contiene esa fecha.

    Amazon arranca la semana en domingo, como se ve en el calendario.
    """
    d = datetime.strptime(fecha_iso, "%Y-%m-%d")
    domingo = d - timedelta(days=(d.weekday() + 1) % 7)
    return (domingo.strftime("%Y-%m-%d"),
            (domingo + timedelta(days=6)).strftime("%Y-%m-%d"))


def esperar_sesion(driver, minutos=30):
    driver.get(URL)
    log("=" * 62)
    log("Entra en Amazon en la ventana de Chrome.")
    log("Cuando veas el Plan mensual, sigo yo solo.")
    log("=" * 62)
    t0 = time.time()
    while time.time() - t0 < minutos * 60:
        if hay_sesion(driver):
            return True
        time.sleep(3)
    return False


def main():
    print("=" * 62)
    print("  CAPACIDAD AMAZON - Plan mensual por estacion")
    print("=" * 62)

    hoy = datetime.now().strftime("%Y-%m-%d")
    desde, hasta = semana_de(hoy)
    if len(sys.argv) > 1:
        desde = sys.argv[1].strip()
        hasta = sys.argv[2].strip() if len(sys.argv) > 2 else desde
    log("Rango: %s a %s" % (desde, hasta))
    log("Estaciones: %s" % ", ".join(ESTACIONES))

    driver = None
    try:
        driver = crear_driver()
        if not esperar_sesion(driver):
            log("No se detecto la sesion. Nada que extraer.")
            return
        log("Sesion lista.\n")

        vivos = revisar_ids(driver, avisar=log) or ESTACIONES
        filas = extraer(driver, desde, hasta, vivos, avisar=log)
        if not filas:
            log("No llego ninguna fila.")
            return

        p_res, p_det = guardar(filas, desde, hasta)
        res = resumir(filas)

        print("\n" + "=" * 70)
        print("  %s al %s" % (desde, hasta))
        print("=" * 70)
        print("  %-8s %-12s %-10s %9s %9s %8s"
              % ("ESTACION", "FECHA", "DIA", "PIDEN", "ACEPTAN", "DIF"))
        print("  " + "-" * 62)
        est_actual = None
        for g in res:
            if est_actual and g["estacion"] != est_actual:
                print()
            est_actual = g["estacion"]
            dif = g["aceptado"] - g["solicitado"]
            print("  %-8s %-12s %-10s %9d %9d %+8d"
                  % (g["estacion"], g["fecha"], g["dia"],
                     g["solicitado"], g["aceptado"], dif))

        print("\n  " + "-" * 62)
        for nom in sorted(set(g["estacion"] for g in res)):
            gs = [g for g in res if g["estacion"] == nom]
            print("  %-8s TOTAL semana: piden %d, aceptan %d, bloques %d"
                  % (nom, sum(g["solicitado"] for g in gs),
                     sum(g["aceptado"] for g in gs),
                     sum(g["bloques"] for g in gs)))

        print("\n  %s" % p_res)
        print("  %s" % p_det)
        log("Listo. Chrome queda abierto.")
    except KeyboardInterrupt:
        log("Interrumpido.")
    except Exception as e:
        log("ERROR: %s" % str(e)[:250])
    finally:
        pass


if __name__ == "__main__":
    main()
