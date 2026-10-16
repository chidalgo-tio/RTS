# -*- coding: utf-8 -*-
"""
Direcciones de entrega y poligonos - Mercado Libre Envios

Dos pasos:

1. EXTRAER. Por cada id de paquete abre su ficha en el panel:

       https://envios.adminml.com/logistics/package-management/package/<id>

   y lee lo que la ficha muestra en "Datos del comprador" y "Direccion de
   entrega": nombre, telefono, calle, numero, CP, barrio, ciudad,
   provincia, mas datos, referencias, tipo de domicilio y zona
   correspondiente. Las coordenadas salen del enlace "Ver en Google Maps"
   (...maps?q=<latitud>,<longitud>).

   Desde octubre de 2026 la ficha ya no trae estos datos en
   window.__PRELOADED_STATE__ (de ahi los sacaba fechapromesa.py): se
   leen de los campos ya dibujados. Ver 2_preliminares/sondear_direccion.py.

2. POLIGONOS. Agrupa los paquetes con coordenadas en zonas de entrega:
     - por cantidad: N paquetes por poligono, juntando los mas cercanos;
     - por CP: un poligono por codigo postal (y si pasa de N, se parte).
   Opcional: solo ciertos CP. Cada paquete queda con su POLIGONO en el
   reporte, y el mapa (HTML) muestra los puntos y la figura de cada zona.

   Los km por ruta quedan para la siguiente version: habra varios CEDIS
   de salida.

La ventana esta en direcciones_app.py. Este archivo tambien se corre solo:

    python extraer_direcciones.py --ids 48239561960 48233823023
    python extraer_direcciones.py --ids lista.txt --por-poligono 25
    python extraer_direcciones.py --csv direcciones_X.csv --por-cp --solo-cp 37700,37710

Los resultados traen nombres, telefonos y direcciones: se guardan en la
carpeta 'direcciones' junto al programa, que no se sube al repositorio.
"""

import os
import re
import sys
import csv
import json
import math
import time
import argparse
from datetime import datetime

import extraer_estatus_paquetes as base

# Perfil de Chrome propio: este programa y Extractor_RTS pueden estar
# abiertos a la vez sin pelearse el perfil. El nombre no contiene
# "chrome_profile", asi que el cierre de Chrome huerfanos de uno no toca
# el Chrome del otro
base.PROFILE_DIR = os.path.join(base.BASE_DIR, "chrome_direcciones")
CARPETA = os.path.join(base.BASE_DIR, "direcciones")

log = base.log
URL_PAQUETE = base.URL_PAQUETE

# Segundos maximos esperando a que la ficha dibuje la direccion
ESPERA_FICHA = 30
REINTENTOS = 1

# Columnas del reporte, en orden. La etiqueta es la que muestra la ficha
CAMPOS = [
    ("NOMBRE", "Nombre y apellido"),
    ("TELEFONO", "Teléfono"),
    ("CALLE", "Calle"),
    ("NUMERO", "Número"),
    ("CP", "CP"),
    ("BARRIO", "Barrio"),
    ("CIUDAD", "Ciudad"),
    ("PROVINCIA", "Provincia"),
    ("MAS DATOS", "Más datos"),
    ("REFERENCIAS", "Referencias (opcional)"),
    ("TIPO DOMICILIO", "Tipo de domicilio"),
    ("ZONA CORRESPONDIENTE", "Zona correspondiente"),
]
COLUMNAS = (["POLIGONO", "ID"] + [c for c, _ in CAMPOS] +
            ["LATITUD", "LONGITUD", "GOOGLE MAPS", "RESULTADO"])

# Lee la ficha ya dibujada. Cada campo es un <input> cuya etiqueta esta en
# el contenedor. "Nombre y apellido" aparece dos veces (comprador y quien
# recibio): vale la primera, que es la de "Datos del comprador"
JS_FICHA = r"""
const texto = document.body ? document.body.innerText : '';
const campos = [];
document.querySelectorAll('input, textarea').forEach(el => {
  let etiqueta = '';
  if (el.id) { const l = document.querySelector('label[for="' + el.id + '"]'); if (l) etiqueta = l.innerText; }
  if (!etiqueta) { const c = el.closest('div'); etiqueta = c && c.parentElement ? c.parentElement.innerText.split('\n')[0] : ''; }
  campos.push([etiqueta.trim(), (el.value || '').trim()]);
});
const mapa = Array.from(document.querySelectorAll('a')).find(a => /google maps/i.test(a.innerText));
return {href: location.href, campos: campos, maps: mapa ? mapa.href : '',
        direccion: texto.indexOf('Dirección de entrega') >= 0,
        historial: texto.indexOf('Historial del paquete') >= 0,
        noEncontrado: /no encontramos|no tienes (acceso|permiso)/i.test(texto),
        // Paquete de otra estacion: el panel saca de la ficha y muestra
        // "No autorizado" (el HTML lleva kraken-not-authorized)
        noAutorizado: /no autorizado/i.test(texto) ||
          !!document.querySelector('[class*="not-authorized"]'),
        login: /login|auth/i.test(location.hostname + location.pathname)};
"""

RX_COORD = re.compile(r"[?&](?:q|query)=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)")


def coordenadas(url_maps):
    """'...maps?q=25.55,-103.49' -> (25.55, -103.49), o (None, None)."""
    m = RX_COORD.search(url_maps or "")
    if not m:
        return None, None
    lat, lng = float(m.group(1)), float(m.group(2))
    # 0,0 o fuera de rango: no es una ubicacion real
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return None, None
    return lat, lng


def leer_ficha(driver, pid):
    """Mira la pestaña. Devuelve ('ok', fila), ('sin_paquete', motivo),
    ('login', ''), o ('espera', '')."""
    try:
        r = driver.execute_script(JS_FICHA) or {}
    except Exception:
        return "espera", ""
    if r.get("login"):
        return "login", ""
    if r.get("noAutorizado"):
        # Puede llegar ya fuera de la ficha (sin el id en la direccion)
        return "sin_paquete", "De otro SC (no autorizado)"
    if pid not in (r.get("href") or ""):
        return "espera", ""
    if r.get("noEncontrado") and not r.get("historial"):
        return "sin_paquete", "No encontrado o de otro SC"
    if not (r.get("direccion") and r.get("historial")):
        return "espera", ""

    valores = {}
    for etiqueta, valor in r.get("campos") or []:
        valores.setdefault(etiqueta, valor)       # la primera aparicion
    fila = {"ID": pid}
    for col, etiqueta in CAMPOS:
        fila[col] = base.limpiar(valores.get(etiqueta, ""))
    lat, lng = coordenadas(r.get("maps"))
    fila["LATITUD"] = f"{lat:.6f}" if lat is not None else ""
    fila["LONGITUD"] = f"{lng:.6f}" if lng is not None else ""
    fila["GOOGLE MAPS"] = (f"https://www.google.com/maps?q={lat:.6f},{lng:.6f}"
                           if lat is not None else "")
    fila["RESULTADO"] = "ok" if lat is not None else "ok, sin coordenadas"
    # La direccion se dibuja un poco despues del resto: sin calle ni CP
    # todavia puede estar cargando (o el paquete no trae direccion)
    if not (fila["CALLE"] or fila["CP"]):
        fila["RESULTADO"] = "ok, sin direccion en la ficha"
        return "vacia", fila
    return "ok", fila


def extraer(ids, detener=None, al_avanzar=None):
    """Abre cada ficha, una a la vez (con varias pestañas el panel se
    traba; ver Extractor_RTS). Devuelve la lista de filas en el orden de
    los ids, incluidas las que no se pudieron leer."""
    ids = [i for i in dict.fromkeys(base.normalizar_id(x) for x in ids) if i]
    if not ids:
        raise RuntimeError("No hay ids validos: pega un id de paquete por renglon.")
    log(f"{len(ids)} paquetes por consultar.")

    filas = {}

    def guardar(pid, estado, dato):
        if estado == "ok":
            filas[pid] = dato
        else:
            filas[pid] = {"ID": pid, "RESULTADO": dato or estado}
        if al_avanzar:
            al_avanzar(len(filas), len(ids))

    log("Abriendo Chrome...")
    driver = base.crear_driver()
    inicio = time.time()
    try:
        driver.get(URL_PAQUETE + ids[0])
        log("Si Chrome pide iniciar sesion en Mercado Libre, hazlo ahi. "
            "El programa sigue solo en cuanto vea la ficha.")
        cola = [(pid, 0) for pid in ids]
        actual = None                     # (pid, intento, desde)
        limite_login = time.time() + base.ESPERA_LOGIN
        primero = True

        while cola or actual:
            if detener and detener.is_set():
                log("Detenido por el usuario; se guarda lo obtenido.")
                break
            if actual is None:
                pid, intento = cola.pop(0)
                if not primero:
                    try:
                        driver.execute_script("window.location.href = arguments[0];",
                                              URL_PAQUETE + pid)
                    except Exception:
                        driver.get(URL_PAQUETE + pid)
                actual = (pid, intento, time.time())
                time.sleep(0.5)
                continue

            pid, intento, desde = actual
            estado, dato = leer_ficha(driver, pid)
            if primero:
                # La primera ficha espera el inicio de sesion, sin prisa
                if estado == "sin_paquete" and time.time() - desde < 8:
                    estado = "espera"
                if estado == "login" or estado == "espera":
                    if time.time() > limite_login:
                        raise RuntimeError(
                            "No se vio la ficha del primer paquete. Revisa la "
                            "sesion y que el SC seleccionado sea el de estos paquetes.")
                    # Tras el login el panel deja en su pantalla de inicio, no
                    # en la ficha: si ya estamos en el panel pero en otra
                    # pagina, se vuelve a pedir la ficha
                    try:
                        href = driver.current_url or ""
                    except Exception:
                        href = ""
                    if (base.DOMINIO in href and pid not in href
                            and time.time() - desde > 6):
                        log("Sesion detectada; abriendo la ficha del primer paquete...")
                        driver.get(URL_PAQUETE + pid)
                        actual = (pid, intento, time.time())
                    time.sleep(1)
                    continue
                primero = False
                log("Sesion lista.")
            if estado == "login":
                raise RuntimeError("La sesion de Mercado Libre se cerro. "
                                   "Vuelve a extraer: los ya leidos estan en el reporte.")
            if estado == "sin_paquete" and time.time() - desde < 8:
                estado = "espera"
            if estado == "vacia":
                # Se le da tiempo a la direccion; si no llega, va sin ella
                estado = "ok" if time.time() - desde > 12 else "espera"
            if estado in ("ok", "sin_paquete"):
                guardar(pid, estado, dato)
                actual = None
            elif time.time() - desde > ESPERA_FICHA:
                actual = None
                if intento < REINTENTOS:
                    cola.append((pid, intento + 1))
                else:
                    guardar(pid, "sin_respuesta", "La ficha no cargo a tiempo")
            else:
                time.sleep(0.3)
                continue

            n = len(filas)
            if n % 20 == 0 or n == len(ids):
                t = time.time() - inicio
                ritmo = n / t if t else 0
                log(f"  {n}/{len(ids)} paquetes ({ritmo * 60:.0f}/min)")
    finally:
        log("Cerrando Chrome...")
        base.cerrar_driver(driver)

    salida = [filas.get(pid) or {"ID": pid, "RESULTADO": "No consultado"} for pid in ids]
    ok = sum(1 for f in salida if f.get("RESULTADO", "").startswith("ok"))
    con_coord = sum(1 for f in salida if f.get("LATITUD"))
    log(f"Listo: {ok}/{len(ids)} con direccion, {con_coord} con coordenadas, "
        f"en {(time.time() - inicio) / 60:.1f} min.")
    return salida


# ---------------------------------------------------------------- Poligonos

def _km(lat0):
    """Factores para pasar grados a km cerca de lat0 (basta para una ciudad)."""
    return 110.57, 111.32 * math.cos(math.radians(lat0))


def _agrupar_cerca(puntos, por_grupo):
    """Reparte los puntos en grupos de hasta `por_grupo`, juntando los
    cercanos: k-medias con tope de capacidad. puntos: [(y_km, x_km)].
    Devuelve la lista de numero de grupo de cada punto."""
    n = len(puntos)
    k = max(1, math.ceil(n / por_grupo))
    if k == 1:
        return [0] * n

    # Centros iniciales lejos entre si (k-medias++ sin azar: reproducible)
    centros = [puntos[0]]
    while len(centros) < k:
        lejano = max(range(n), key=lambda i: min(
            (puntos[i][0] - c[0]) ** 2 + (puntos[i][1] - c[1]) ** 2 for c in centros))
        centros.append(puntos[lejano])

    grupo = [0] * n
    for _ in range(20):
        # Cada punto al centro mas cercano que aun tenga lugar: primero los
        # pares mas cercanos, asi nadie queda lejos por llegar tarde
        pares = sorted(((puntos[i][0] - c[0]) ** 2 + (puntos[i][1] - c[1]) ** 2, i, j)
                       for i in range(n) for j, c in enumerate(centros))
        nuevo, cupo = [None] * n, [0] * k
        for _d, i, j in pares:
            if nuevo[i] is None and cupo[j] < por_grupo:
                nuevo[i] = j
                cupo[j] += 1
        if nuevo == grupo:
            break
        grupo = nuevo
        for j in range(k):
            miembros = [puntos[i] for i in range(n) if grupo[i] == j]
            if miembros:
                centros[j] = (sum(p[0] for p in miembros) / len(miembros),
                              sum(p[1] for p in miembros) / len(miembros))
    return grupo


def armar_poligonos(filas, modo="cantidad", por_poligono=25, solo_cp=None):
    """Pone la columna POLIGONO a cada fila.

    modo 'cantidad': grupos de hasta por_poligono paquetes cercanos.
    modo 'cp':       un poligono por CP; si un CP pasa de por_poligono
                     (y por_poligono > 0), se parte en varios.
    solo_cp: lista de CP; los demas quedan como 'FUERA (CP)'.
    Los que no tienen coordenadas quedan como 'SIN UBICACION'.

    Devuelve un resumen {poligono: cantidad}.
    """
    solo_cp = {c.strip() for c in (solo_cp or []) if c.strip()}
    con_ubicacion = []
    for f in filas:
        f["POLIGONO"] = ""
        if not f.get("RESULTADO", "").startswith("ok"):
            f["POLIGONO"] = "SIN DATOS"
        elif not f.get("LATITUD"):
            f["POLIGONO"] = "SIN UBICACION"
        elif solo_cp and f.get("CP", "") not in solo_cp:
            f["POLIGONO"] = "FUERA (CP)"
        else:
            con_ubicacion.append(f)

    if con_ubicacion:
        lat0 = sum(float(f["LATITUD"]) for f in con_ubicacion) / len(con_ubicacion)
        ky, kx = _km(lat0)

        if modo == "cp":
            por_cp = {}
            for f in con_ubicacion:
                por_cp.setdefault(f.get("CP") or "SIN CP", []).append(f)
            for cp in sorted(por_cp):
                grupo_cp = por_cp[cp]
                tope = por_poligono if por_poligono and por_poligono > 0 else len(grupo_cp)
                pts = [(float(f["LATITUD"]) * ky, float(f["LONGITUD"]) * kx) for f in grupo_cp]
                asignado = _agrupar_cerca(pts, tope)
                partes = max(asignado) + 1
                for f, g in zip(grupo_cp, asignado):
                    f["POLIGONO"] = f"CP {cp}" + (f"-{g + 1}" if partes > 1 else "")
        else:
            tope = max(1, int(por_poligono or 25))
            pts = [(float(f["LATITUD"]) * ky, float(f["LONGITUD"]) * kx) for f in con_ubicacion]
            asignado = _agrupar_cerca(pts, tope)
            # Numerar de norte a sur y de oeste a este, para que P01 y P02
            # queden cerca en el mapa
            centros = {}
            for f, g in zip(con_ubicacion, asignado):
                centros.setdefault(g, []).append((float(f["LATITUD"]), float(f["LONGITUD"])))
            orden = sorted(centros, key=lambda g: (
                -round(sum(p[0] for p in centros[g]) / len(centros[g]), 2),
                sum(p[1] for p in centros[g]) / len(centros[g])))
            nombre = {g: f"P{i + 1:02d}" for i, g in enumerate(orden)}
            for f, g in zip(con_ubicacion, asignado):
                f["POLIGONO"] = nombre[g]

    resumen = {}
    for f in filas:
        resumen[f["POLIGONO"]] = resumen.get(f["POLIGONO"], 0) + 1
    return resumen


def envolvente(puntos):
    """Cadena monotona: la figura convexa que encierra los puntos (lat, lng)."""
    pts = sorted(set(puntos), key=lambda p: (p[1], p[0]))
    if len(pts) <= 2:
        return pts

    def cruz(o, a, b):
        return (a[1] - o[1]) * (b[0] - o[0]) - (a[0] - o[0]) * (b[1] - o[1])

    abajo, arriba = [], []
    for p in pts:
        while len(abajo) >= 2 and cruz(abajo[-2], abajo[-1], p) <= 0:
            abajo.pop()
        abajo.append(p)
    for p in reversed(pts):
        while len(arriba) >= 2 and cruz(arriba[-2], arriba[-1], p) <= 0:
            arriba.pop()
        arriba.append(p)
    return abajo[:-1] + arriba[:-1]


# ---------------------------------------------------------------- Archivos

def _sello():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def guardar_csv(filas, ruta=None):
    os.makedirs(CARPETA, exist_ok=True)
    ruta = ruta or os.path.join(CARPETA, f"direcciones_{_sello()}.csv")
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for fila in filas:
            w.writerow({c: fila.get(c, "") for c in COLUMNAS})
    return ruta


def leer_csv(ruta):
    """Un reporte anterior, para volver a armar poligonos sin extraer."""
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        muestra = f.read(4096)
        f.seek(0)
        sep = ";" if muestra.count(";") >= muestra.count(",") else ","
        filas = [dict(r) for r in csv.DictReader(f, delimiter=sep)]
    if not filas or "ID" not in filas[0] or "LATITUD" not in filas[0]:
        raise RuntimeError("Ese CSV no es un reporte de direcciones de este programa.")
    return filas


COLORES = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4",
           "#f032e6", "#9a6324", "#469990", "#800000", "#808000", "#000075",
           "#bfef45", "#dcbeff", "#ffd8b1", "#aaffc3", "#fabed4", "#a9a9a9"]


def guardar_mapa(filas, titulo="Poligonos de entrega", ruta=None):
    """Mapa HTML: un color por poligono, su figura, y el detalle de cada
    paquete al tocarlo. Abre en cualquier navegador."""
    os.makedirs(CARPETA, exist_ok=True)
    ruta = ruta or os.path.join(CARPETA, f"mapa_{_sello()}.html")

    grupos = {}
    for f in filas:
        if f.get("LATITUD") and f.get("POLIGONO") not in ("SIN DATOS", "SIN UBICACION", "FUERA (CP)"):
            grupos.setdefault(f["POLIGONO"], []).append(f)

    datos = []
    for i, nombre in enumerate(sorted(grupos)):
        miembros = grupos[nombre]
        datos.append({
            "nombre": nombre,
            "color": COLORES[i % len(COLORES)],
            "figura": envolvente([(float(f["LATITUD"]), float(f["LONGITUD"])) for f in miembros]),
            "puntos": [{
                "lat": float(f["LATITUD"]), "lng": float(f["LONGITUD"]),
                "id": f["ID"], "nombre": f.get("NOMBRE", ""), "tel": f.get("TELEFONO", ""),
                "dir": " ".join(x for x in (f.get("CALLE", ""), f.get("NUMERO", ""))if x),
                "barrio": f.get("BARRIO", ""), "cp": f.get("CP", ""),
                "ciudad": f.get("CIUDAD", ""), "ref": f.get("REFERENCIAS", ""),
                "tipo": f.get("TIPO DOMICILIO", ""),
            } for f in miembros],
        })
    fuera = sum(1 for f in filas if f.get("POLIGONO") in ("SIN DATOS", "SIN UBICACION", "FUERA (CP)"))

    html = PLANTILLA_MAPA.replace("__TITULO__", titulo).replace(
        "__DATOS__", json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")).replace(
        "__FUERA__", str(fuera))
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(html)
    return ruta


PLANTILLA_MAPA = r"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  html, body { margin: 0; height: 100%; font-family: "Segoe UI", Arial, sans-serif; }
  #app { display: flex; height: 100%; }
  #panel { width: 290px; overflow-y: auto; background: #1e2128; color: #e8eaed; padding: 10px; box-sizing: border-box; }
  #mapa { flex: 1; }
  h1 { font-size: 16px; margin: 0 0 4px; }
  .nota { color: #9aa0aa; font-size: 12px; margin-bottom: 8px; }
  input { width: 100%; box-sizing: border-box; padding: 6px; border-radius: 5px; border: 1px solid #3a3f4b; background: #272b34; color: #e8eaed; margin-bottom: 8px; }
  .zona { display: flex; align-items: center; gap: 8px; padding: 6px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .zona:hover { background: #272b34; }
  .muestra { width: 14px; height: 14px; border-radius: 3px; flex: none; }
  .zona b { flex: 1; }
  .popup b { display: block; margin-bottom: 2px; }
  .popup div { font-size: 12px; }
</style></head><body>
<div id="app">
  <div id="panel">
    <h1>__TITULO__</h1>
    <div class="nota" id="totales"></div>
    <input id="buscar" placeholder="Buscar un ID de paquete...">
    <div id="lista"></div>
  </div>
  <div id="mapa"></div>
</div>
<script>
const DATOS = __DATOS__;
const FUERA = __FUERA__;
const mapa = L.map('mapa');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap'}).addTo(mapa);

const esc = s => String(s || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const todos = [], porId = {};
let total = 0;
DATOS.forEach(z => {
  const capa = L.featureGroup().addTo(mapa);
  if (z.figura.length >= 3) {
    L.polygon(z.figura, {color: z.color, weight: 2, fillOpacity: 0.12}).addTo(capa)
      .bindTooltip(z.nombre + ' · ' + z.puntos.length + ' paquetes', {sticky: true});
  }
  z.puntos.forEach(p => {
    const m = L.circleMarker([p.lat, p.lng], {radius: 6, color: '#fff', weight: 1,
      fillColor: z.color, fillOpacity: 0.95}).addTo(capa);
    m.bindPopup('<div class="popup"><b>' + esc(z.nombre) + ' · ID ' + esc(p.id) + '</b>' +
      '<div>' + esc(p.nombre) + ' · ' + esc(p.tel) + '</div>' +
      '<div>' + esc(p.dir) + ', ' + esc(p.barrio) + ', CP ' + esc(p.cp) + ', ' + esc(p.ciudad) + '</div>' +
      (p.ref ? '<div><i>' + esc(p.ref) + '</i></div>' : '') +
      '<div>' + esc(p.tipo) + '</div>' +
      '<div><a target="_blank" href="https://www.google.com/maps?q=' + p.lat + ',' + p.lng + '">Ver en Google Maps</a></div></div>');
    porId[p.id] = m;
    todos.push([p.lat, p.lng]);
  });
  total += z.puntos.length;
  const fila = document.createElement('div');
  fila.className = 'zona';
  fila.innerHTML = '<span class="muestra" style="background:' + z.color + '"></span><b>' +
    esc(z.nombre) + '</b><span>' + z.puntos.length + '</span>';
  fila.onclick = () => mapa.fitBounds(capa.getBounds(), {padding: [30, 30]});
  document.getElementById('lista').appendChild(fila);
});
document.getElementById('totales').textContent =
  DATOS.length + ' polígonos · ' + total + ' paquetes en el mapa' +
  (FUERA ? ' · ' + FUERA + ' fuera del mapa (sin ubicación o filtrados)' : '');
if (todos.length) mapa.fitBounds(todos, {padding: [30, 30]}); else mapa.setView([23.6, -102.5], 5);
document.getElementById('buscar').addEventListener('change', e => {
  const m = porId[e.target.value.trim()];
  if (m) { mapa.setView(m.getLatLng(), 17); m.openPopup(); }
  else if (e.target.value.trim()) alert('Ese ID no está en el mapa.');
});
</script></body></html>
"""


# ---------------------------------------------------------------- Linea de comando

def main():
    ap = argparse.ArgumentParser(description="Direcciones de entrega y poligonos")
    ap.add_argument("--ids", nargs="+", help="ids de paquete, o un .txt con uno por renglon")
    ap.add_argument("--csv", help="reporte anterior: solo arma poligonos")
    ap.add_argument("--por-poligono", type=int, default=25, help="paquetes por poligono")
    ap.add_argument("--por-cp", action="store_true", help="un poligono por codigo postal")
    ap.add_argument("--solo-cp", default="", help="solo estos CP, separados por coma")
    args = ap.parse_args()

    if args.csv:
        filas = leer_csv(args.csv)
    elif args.ids:
        ids = []
        for v in args.ids:
            if os.path.isfile(v):
                with open(v, encoding="utf-8-sig") as f:
                    ids.extend(f.read().split())
            else:
                ids.extend(re.split(r"[,\s]+", v))
        filas = extraer(ids)
    else:
        ap.error("pasa --ids o --csv")

    resumen = armar_poligonos(filas, "cp" if args.por_cp else "cantidad",
                              args.por_poligono, args.solo_cp.split(","))
    print("Reporte:", guardar_csv(filas))
    print("Mapa   :", guardar_mapa(filas))
    for nombre in sorted(resumen):
        print(f"  {nombre:<16} {resumen[nombre]}")


if __name__ == "__main__":
    main()
