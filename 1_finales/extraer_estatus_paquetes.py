# -*- coding: utf-8 -*-
"""
Ultimo estatus de cada paquete - Mercado Libre Envios

Lee los ids de paquete de la hoja CONSOLIDADO_RTS (columna C), abre la
ficha de cada uno en el panel:

    https://envios.adminml.com/logistics/package-management/package/<id>

y escribe en la misma hoja el ultimo estado del "Historial del paquete"
(el primero de la lista, que es el mas reciente) y su fecha:

    H = estatus         ("Para despachar")
    I = fecha y hora    ("21/09/2026 14:20")

Que filas se consultan:
  - Solo las que tienen FECHA (columna A) desde FECHA_INICIAL (01-10-26).
  - Las que aun no tienen estatus, y las que tienen uno que todavia puede
    cambiar. Las de estatus final (Entregado, Dañado, Devuelto) ya no se
    vuelven a consultar ni se tocan.

Como se obtiene el dato: la ficha se lee tal como la dibuja el panel
(texto de la pagina a partir de "Historial del paquete"). La API que la
alimenta aun no esta descubierta; ver skills/api-en-vez-de-scraping.

Acceso a Google Sheets: a traves de una Web App de Apps Script
(apps_script/estatus_paquetes.gs, con las instrucciones dentro). Python no
maneja credenciales de Google: solo la URL /exec y su clave, en
webapp_sheets.json. Al compilar, ese archivo va dentro del .exe, asi que
el programa funciona igual en cualquier computadora y con cualquier
usuario; uno junto al .exe tiene prioridad sobre el de adentro.

La ventana esta en estatus_app.py. Este archivo tambien se corre solo:

    python extraer_estatus_paquetes.py                  # desde 01-10-26
    python extraer_estatus_paquetes.py --desde 15-10-26
    python extraer_estatus_paquetes.py --limite 10      # prueba corta
    python extraer_estatus_paquetes.py --solo-vacias    # no reconsultar
    python extraer_estatus_paquetes.py --ids 4800... 4801...   # sin hoja, solo CSV

Requisitos:
    pip install selenium
"""

import os
import re
import sys
import csv
import json
import time
import argparse
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# La hoja donde se lee y se escribe. Se le manda a la Web App en cada
# pedido: para usar otra hoja basta cambiarla aqui
SHEET_ID = "11QCHtlUFyT_iG3juyC0RpoGXgeuZ7UHtVas3Ru_uLtU"
HOJA = "CONSOLIDADO_RTS"
COL_ID = "C"
COL_ESTATUS = "H"
COL_FECHA = "I"

# Solo se consultan las filas con FECHA (columna A) desde este dia
FECHA_INICIAL = date(2026, 10, 1)

# Estatus que ya no cambian: esas filas no se vuelven a consultar. Se
# compara el inicio del texto sin acentos, asi "Entregado - Place" tambien
# cuenta. La misma lista vive en FINALES del .gs, que lo vuelve a revisar
# antes de escribir
FINALES = ("entregado", "danado", "devuelto")

URL_PAQUETE = "https://envios.adminml.com/logistics/package-management/package/"
DOMINIO = "envios.adminml.com"

# Pestañas abiertas a la vez. Con 4 el panel se trababa (Chrome dejaba de
# responder 2 minutos); con 1 rinde ~30 paquetes por minuto sin fallar
PESTANAS = 1
# Segundos maximos esperando a que una ficha muestre su historial
ESPERA_FICHA = 30
# Vueltas extra para las fichas que no cargaron a tiempo
REINTENTOS = 1
# Cada cuantos resultados se escribe en la hoja: si el programa se corta,
# lo ya escrito no se pierde
LOTE_ESCRITURA = 25
# Tiempo maximo para iniciar sesion en Chrome (la primera vez en una
# computadora nueva hay que escribir usuario y clave)
ESPERA_LOGIN = 600
# Segundos que se le dan a Chrome para cerrarse antes de forzarlo
ESPERA_CIERRE = 20
PAUSA = 0.3

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# El mismo perfil que los demas programas: si ya iniciaste sesion con
# ellos, aqui no hace falta volver a hacerlo
PROFILE_DIR = os.path.join(BASE_DIR, "chrome_profile")
NOMBRE_CONFIG = "webapp_sheets.json"

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

# "21/sep 14:20 hs"
RX_FECHA = re.compile(
    r"(\d{1,2})\s*/\s*([A-Za-zÁÉÍÓÚáéíóú]{3,4})\.?\s+(\d{1,2}):(\d{2})\s*hs\.?",
    re.IGNORECASE,
)

# Pantallas en las que no hay historial que esperar
RX_SIN_PAQUETE = re.compile(
    r"no (encontramos|existe|pudimos encontrar)|no se encontr|"
    r"no tienes (acceso|permiso)|p[aá]gina no encontrada",
    re.IGNORECASE,
)

# La ventana se cuelga aqui para recibir los mensajes del registro
AVISAR = None


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)
    if AVISAR:
        try:
            AVISAR(msg)
        except Exception:
            pass


def limpiar(texto):
    if texto is None:
        return ""
    return " ".join(str(texto).split()).strip()


def sin_acentos(texto):
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def es_final(estatus):
    t = sin_acentos(limpiar(estatus)).lower()
    return any(t.startswith(f) for f in FINALES)


def fecha_de(texto):
    """'07-10-26' o '07/10/2026' -> date. None si no se reconoce."""
    m = re.match(r"^\s*(\d{1,2})[-/](\d{1,2})[-/](\d{2}|\d{4})\b", texto or "")
    if not m:
        return None
    dia, mes, anio = int(m.group(1)), int(m.group(2)), m.group(3)
    anio = int(anio) if len(anio) == 4 else 2000 + int(anio)
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


# ---------------------------------------------------------------- Google

class WebApp:
    """La hoja, vista a traves de la Web App de Apps Script.

    El script corre en la cuenta de Google del dueño de la hoja, asi que
    aqui no hacen falta credenciales: solo la URL /exec y la clave (TOKEN)
    que se puso en el script. Ver apps_script/estatus_paquetes.gs.
    """

    def __init__(self, url, token):
        self.url = url
        self.token = token

    def _pedir(self, metodo, cuerpo=None, extra=None):
        params = urllib.parse.urlencode(
            {"token": self.token, "sheet": SHEET_ID, "hoja": HOJA,
             **(extra or {})})
        datos = None
        cabeceras = {}
        if cuerpo is not None:
            datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
            cabeceras["Content-Type"] = "application/json"
        # Apps Script responde con un 302 a googleusercontent.com; urllib lo
        # sigue con un GET, que es justo como hay que leer la respuesta
        req = urllib.request.Request(
            f"{self.url}?{params}", data=datos, headers=cabeceras, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                texto = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"La Web App respondio HTTP {e.code}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"No se pudo conectar con la Web App: {e.reason}")

        try:
            resp = json.loads(texto)
        except json.JSONDecodeError:
            # Suele ser la pagina de login de Google o un error del script
            # (por ejemplo, sin permiso sobre la hoja de SHEET_ID)
            raise RuntimeError(
                "La Web App no devolvio JSON. Revisa que la URL termine en "
                "/exec, que el acceso sea 'Cualquier usuario' y que el dueño "
                "del script pueda editar la hoja. "
                f"Empieza con: {limpiar(texto[:100])}")
        if not resp.get("ok"):
            error = resp.get("error")
            if error == "token":
                raise RuntimeError(
                    "La Web App rechazo la clave: el token de "
                    f"{NOMBRE_CONFIG} no coincide con el TOKEN del script.")
            raise RuntimeError(f"La Web App respondio un error: {error}")
        return resp

    def leer_tabla(self):
        """Columnas A..I de cada fila, como se ven en la hoja."""
        return self._pedir("GET", extra={"completas": "1"}).get("tabla") or []

    def escribir(self, cambios, actualizar):
        cuerpo = {"token": self.token, "sheet": SHEET_ID, "hoja": HOJA,
                  "cambios": cambios}
        if actualizar:
            cuerpo["modo"] = "actualizar"
        return self._pedir("POST", cuerpo)


def ruta_config():
    """El webapp_sheets.json junto al programa manda; si no hay, el que
    viene dentro del .exe."""
    candidatos = [os.path.join(BASE_DIR, NOMBRE_CONFIG)]
    interno = getattr(sys, "_MEIPASS", None)
    if interno:
        candidatos.append(os.path.join(interno, NOMBRE_CONFIG))
    for ruta in candidatos:
        if os.path.isfile(ruta):
            return ruta
    return None


def abrir_hoja():
    """Lee webapp_sheets.json y devuelve la Web App lista para usar."""
    ruta = ruta_config()
    if not ruta:
        raise RuntimeError(
            f"Falta {NOMBRE_CONFIG} junto al programa, con la URL /exec de "
            "la Web App y su clave: "
            '{"url": "https://script.google.com/macros/s/.../exec", '
            '"token": "..."}')
    with open(ruta, encoding="utf-8-sig") as f:
        config = json.load(f)
    url = limpiar(config.get("url"))
    token = limpiar(config.get("token"))
    if "/exec" not in url or "..." in url or not token:
        raise RuntimeError(f"Completa url y token en {ruta}.")
    return WebApp(url, token)


def normalizar_id(valor):
    """La columna puede traer el id como numero, texto o con separadores."""
    if valor is None or valor == "":
        return ""
    if isinstance(valor, float):
        valor = int(valor)
    digitos = re.sub(r"\D", "", str(valor))
    # Un id de paquete tiene 10 u 11 digitos; lo demas es cabecera o basura
    return digitos if len(digitos) >= 8 else ""


def leer_pendientes(hoja, desde=FECHA_INICIAL, actualizar=True):
    """Que filas consultar.

    desde: solo filas con FECHA (columna A) desde ese dia. None = todas.
    actualizar: True consulta tambien las que ya tienen un estatus que aun
    puede cambiar; False solo las que tienen H e I vacias.

    Devuelve ({id: {fila: (estatus_actual, fecha_actual)}}, cuentas): un
    mismo paquete puede estar en varias filas y basta consultarlo una vez.
    """
    tabla = hoja.leer_tabla()

    pendientes = {}
    cuentas = {"en_rango": 0, "finales": 0, "con_dato": 0, "fuera": 0}
    for n, fila in enumerate(tabla, start=1):
        fila = list(fila) + [""] * (9 - len(fila))
        pid = normalizar_id(fila[2])            # C
        if not pid:
            continue
        if desde:
            f = fecha_de(fila[0])               # A
            if not f or f < desde:
                cuentas["fuera"] += 1
                continue
        cuentas["en_rango"] += 1
        estatus, fecha = limpiar(fila[7]), limpiar(fila[8])     # H, I
        if es_final(estatus):
            cuentas["finales"] += 1
            continue
        if not actualizar and (estatus or fecha):
            cuentas["con_dato"] += 1
            continue
        pendientes.setdefault(pid, {})[n] = (estatus, fecha)

    filas = sum(len(v) for v in pendientes.values())
    desde_txt = f"desde {desde:%d-%m-%y}" if desde else "de todas las fechas"
    log(f"Hoja: {cuentas['en_rango']} filas {desde_txt}; "
        f"{filas} por consultar ({len(pendientes)} paquetes distintos).")
    if cuentas["finales"]:
        log(f"  {cuentas['finales']} filas con estatus final "
            f"({', '.join(FINALES)}) no se vuelven a consultar.")
    if cuentas["con_dato"]:
        log(f"  {cuentas['con_dato']} filas ya tenian dato y se respetan.")
    return pendientes, cuentas


def escribir(hoja, resultados, actualizar=True):
    """resultados: [(fila, estatus, fecha)]. Solo toca H:I de esas filas.

    La Web App vuelve a mirar cada fila antes de escribir: nunca pisa un
    estatus final, y en modo solo-vacias tampoco una fila con dato.
    """
    if hoja is None or not resultados:
        return
    cambios = [{"fila": fila, "estatus": estatus, "fecha": fecha}
               for fila, estatus, fecha in resultados]
    for intento in range(3):
        try:
            resp = hoja.escribir(cambios, actualizar)
            if resp.get("omitidas"):
                log(f"  {resp['omitidas']} filas cambiaron en la hoja mientras "
                    "corria el programa; no se tocaron.")
            return
        except Exception as e:
            if intento == 2 or "clave" in str(e):
                raise
            log(f"  Fallo al escribir en la hoja ({str(e)[:80]}); reintentando...")
            time.sleep(5 * (intento + 1))


# ---------------------------------------------------------------- Chrome

def _sin_ventana():
    """En Windows, que los procesos auxiliares no abran una consola negra
    (importa en el .exe, que no tiene consola propia)."""
    import subprocess

    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def cerrar_chrome_huerfano():
    """Cierra los Chrome que quedaron usando NUESTRO perfil (ver extraer_api).

    Solo los que tienen la ruta de este perfil en su linea de comando: el
    Chrome personal del usuario no se toca.
    """
    if os.name != "nt":
        return
    marca = os.path.basename(PROFILE_DIR)
    proyecto = os.path.basename(os.path.dirname(PROFILE_DIR))
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{proyecto}*' -and "
        f"$_.CommandLine -like '*{marca}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue; $_.ProcessId }"
    )
    try:
        import subprocess

        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=25,
            creationflags=_sin_ventana(),
        )
        if any(l.strip().isdigit() for l in (res.stdout or "").split()):
            log("Se cerraron los Chrome de una corrida anterior.")
            time.sleep(2)
    except Exception:
        pass


def limpiar_lock():
    if not os.path.isdir(PROFILE_DIR):
        return
    cerrar_chrome_huerfano()
    for nombre in ("lockfile", "LOCK", "SingletonLock", "SingletonCookie",
                   "SingletonSocket", "DevToolsActivePort"):
        for carpeta in (PROFILE_DIR, os.path.join(PROFILE_DIR, "Default")):
            try:
                ruta = os.path.join(carpeta, nombre)
                if os.path.exists(ruta):
                    os.remove(ruta)
            except Exception:
                pass


def crear_driver():
    limpiar_lock()
    opts = Options()
    opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--lang=es-MX")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    # Navegar sin esperar la carga: el programa vigila la pestaña por su
    # cuenta y asi un panel lento no deja colgado a Selenium
    opts.page_load_strategy = "none"
    servicio = Service(popen_kw={"creation_flags": _sin_ventana()})
    try:
        return webdriver.Chrome(options=opts, service=servicio)
    except Exception as e:
        texto = str(e).lower()
        if "user data directory is already in use" in texto:
            raise RuntimeError(
                "El perfil de Chrome esta en uso por otra ventana. Cierra las "
                "ventanas que abrio este programa y reintenta.")
        if "devtoolsactiveport" in texto or "chrome failed to start" in texto:
            raise RuntimeError(
                "Chrome no pudo arrancar. Cierra las ventanas de Chrome que "
                "abrio este programa y reintenta.")
        if "cannot find chrome" in texto or "no chrome binary" in texto:
            raise RuntimeError(
                "No se encontro Google Chrome en esta computadora. Instalalo "
                "desde google.com/chrome y reintenta.")
        raise


def cerrar_driver(driver):
    """driver.quit() con limite: a veces Chrome no contesta y se queda
    colgado minutos. Si no cierra a tiempo, se fuerza."""
    if not driver:
        return
    hilo = threading.Thread(target=lambda: _quit_silencioso(driver), daemon=True)
    hilo.start()
    hilo.join(ESPERA_CIERRE)
    if hilo.is_alive():
        log("Chrome no respondia al cerrar; se cierra a la fuerza.")
        try:
            driver.service.process.kill()
        except Exception:
            pass
        cerrar_chrome_huerfano()


def _quit_silencioso(driver):
    try:
        driver.quit()
    except Exception:
        pass


def fecha_completa(dia, mes_txt, hora, minuto):
    """'21', 'sep', '14', '20' -> '21/09/2026 14:20'.

    El panel no muestra el año. Se toma el actual, salvo que el mes quede
    en el futuro (un paquete de diciembre consultado en enero).
    """
    mes = MESES.get(mes_txt.lower()[:3])
    if not mes:
        return f"{dia}/{mes_txt} {hora}:{minuto}"
    hoy = datetime.now()
    anio = hoy.year - 1 if mes > hoy.month + 1 else hoy.year
    return f"{int(dia):02d}/{mes:02d}/{anio} {int(hora):02d}:{minuto}"


def leer_ultimo_estado(texto):
    """Del texto de la ficha, el primer estado del historial.

    Devuelve (estatus, fecha), o None si el historial aun no se dibuja.
    """
    i = texto.find("Historial del paquete")
    if i < 0:
        return None
    trozo = texto[i:]
    m = RX_FECHA.search(trozo)
    if not m:
        return None

    # Tras la fecha viene el estado; el separador "|" puede llegar en la
    # misma linea o en una propia
    estatus = ""
    for linea in trozo[m.end():].splitlines():
        linea = limpiar(linea).strip("|·").strip()
        if linea:
            estatus = linea
            break
    if not estatus or RX_FECHA.match(estatus):
        return None
    return estatus, fecha_completa(*m.groups())


LEER_PESTANA = """
return {href: location.href, estado: document.readyState,
        texto: document.body ? document.body.innerText : ''};
"""


def revisar(driver, pid):
    """Mira la pestaña actual. Devuelve ('ok', (estatus, fecha)),
    ('sin_paquete', motivo), ('login', ''), o ('espera', '')."""
    try:
        r = driver.execute_script(LEER_PESTANA) or {}
    except Exception:
        return "espera", ""
    href = r.get("href") or ""
    texto = r.get("texto") or ""

    if DOMINIO not in href or "login" in href.lower():
        # Aun en la pagina anterior, o el panel mando a iniciar sesion
        return ("login", "") if "login" in href.lower() else ("espera", "")
    if pid not in href:
        return "espera", ""

    ultimo = leer_ultimo_estado(texto)
    if ultimo:
        return "ok", ultimo
    if r.get("estado") == "complete":
        m = RX_SIN_PAQUETE.search(texto)
        if m:
            return "sin_paquete", limpiar(m.group(0))
    return "espera", ""


def esperar_sesion(driver, pid, detener=None):
    """Abre el primer paquete y espera a que el historial se vea.

    No se pide ENTER ni boton: el programa sigue solo en cuanto hay datos.
    """
    driver.get(URL_PAQUETE + pid)
    log("Si Chrome pide iniciar sesion en Mercado Libre, hazlo ahi. "
        "El programa continua solo en cuanto vea el historial del paquete.")
    limite = time.time() + ESPERA_LOGIN
    volver = False
    while time.time() < limite:
        if detener and detener.is_set():
            raise RuntimeError("Detenido por el usuario.")
        estado, dato = revisar(driver, pid)
        if estado in ("ok", "sin_paquete"):
            return estado, dato
        # Tras el login el panel puede dejarnos en otra pantalla
        try:
            href = driver.current_url or ""
        except Exception:
            href = ""
        if DOMINIO in href and pid not in href and "login" not in href.lower():
            if volver:
                driver.get(URL_PAQUETE + pid)
                volver = False
            else:
                volver = True
        time.sleep(2)
    raise RuntimeError(
        f"En {ESPERA_LOGIN // 60} minutos no se vio el historial del paquete "
        f"{pid}. Revisa que la sesion este abierta y que el SC seleccionado "
        "en el panel sea el de estos paquetes.")


def consultar(driver, ids, al_terminar, pestanas=PESTANAS, detener=None):
    """Reparte los ids entre las pestañas y va entregando resultados.

    al_terminar(pid, estado, dato) se llama con cada paquete resuelto.
    Devuelve los ids que no cargaron ni tras los reintentos.
    """
    pestanas = max(1, pestanas)
    handles = [driver.current_window_handle]
    while len(handles) < pestanas:
        driver.switch_to.new_window("tab")
        handles.append(driver.current_window_handle)

    cola = [(pid, 0) for pid in ids]
    ocupadas = {}                   # handle -> (pid, intento, inicio)
    fallidos = []
    total = len(ids)
    hechos = 0
    inicio = time.time()

    while cola or ocupadas:
        if detener and detener.is_set():
            log("Detenido por el usuario; se guarda lo obtenido.")
            break
        for h in handles:
            try:
                driver.switch_to.window(h)
            except Exception as e:
                # La pestaña cambiando de pagina justo ahora: se mira en la
                # siguiente vuelta
                if "navigation" in str(e) or "detached" in str(e):
                    continue
                raise

            if h in ocupadas:
                pid, intento, desde = ocupadas[h]
                estado, dato = revisar(driver, pid)
                # El historial llega despues de la pagina: un "no encontramos"
                # en los primeros segundos puede ser la ficha a medio cargar
                if estado == "sin_paquete" and time.time() - desde < 8:
                    estado = "espera"
                if estado == "login":
                    raise RuntimeError(
                        "La sesion de Mercado Libre se cerro. Vuelve a correr "
                        "el programa: retoma lo que falta.")
                if estado in ("ok", "sin_paquete"):
                    del ocupadas[h]
                    hechos += 1
                    al_terminar(pid, estado, dato)
                elif time.time() - desde > ESPERA_FICHA:
                    del ocupadas[h]
                    if intento < REINTENTOS:
                        cola.append((pid, intento + 1))
                    else:
                        hechos += 1
                        fallidos.append(pid)
                        al_terminar(pid, "sin_respuesta", "")
                else:
                    continue

                if hechos % 20 == 0 or hechos == total:
                    t = time.time() - inicio
                    ritmo = hechos / t if t else 0
                    faltan = (total - hechos) / ritmo if ritmo else 0
                    log(f"  {hechos}/{total} paquetes "
                        f"({ritmo * 60:.0f}/min, faltan ~{faltan / 60:.1f} min)")

            if h not in ocupadas and cola:
                pid, intento = cola.pop(0)
                try:
                    driver.execute_script(
                        "window.location.href = arguments[0];", URL_PAQUETE + pid)
                except Exception as e:
                    # Si la pestaña aun estaba redirigiendo, Chrome corta el
                    # script con "aborted by navigation" o "frame detached".
                    # Se vuelve a pedir con get(), que no depende del frame
                    if "navigation" not in str(e) and "detached" not in str(e):
                        raise
                    driver.get(URL_PAQUETE + pid)
                ocupadas[h] = (pid, intento, time.time())

        time.sleep(PAUSA)

    return fallidos


# ---------------------------------------------------------------- Corrida

def guardar_respaldo(filas):
    """Copia local de lo consultado, incluidos los que fallaron."""
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join(BASE_DIR, f"estatus_paquetes_{sello}.csv")
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["ID PAQUETE", "FILAS", "ESTATUS", "FECHA", "RESULTADO"])
        w.writerows(filas)
    return ruta


def ejecutar(desde=FECHA_INICIAL, actualizar=True, pestanas=PESTANAS,
             limite=0, ids=None, detener=None):
    """La corrida completa: hoja -> panel -> hoja. La usan la ventana y la
    linea de comando.

    ids: lista de ids para consultar sin tocar la hoja (solo CSV).
    detener: threading.Event; si se activa, se para y se guarda lo hecho.

    Devuelve un resumen (dict) para mostrar.
    """
    resumen = {"consultados": 0, "ok": 0, "cambiaron": 0, "iguales": 0,
               "sin_paquete": 0, "sin_respuesta": 0, "finales": 0,
               "conteo": {}, "respaldo": "", "minutos": 0.0, "error": ""}

    hoja = None
    if ids:
        pendientes = {}
        for valor in ids:
            pid = normalizar_id(valor)
            if pid:
                pendientes.setdefault(pid, {})
        log(f"Sin hoja de calculo: {len(pendientes)} ids, solo CSV.")
    else:
        log("Leyendo la hoja de calculo...")
        hoja = abrir_hoja()
        pendientes, cuentas = leer_pendientes(hoja, desde, actualizar)
        resumen["finales"] = cuentas["finales"]

    lista = list(pendientes)
    if limite:
        lista = lista[:limite]
    if not lista:
        log("No hay paquetes por consultar.")
        return resumen

    por_escribir = []
    respaldo = []
    conteo = resumen["conteo"]

    def al_terminar(pid, estado, dato):
        filas = pendientes[pid]
        nums = ",".join(map(str, filas))
        resumen["consultados"] += 1
        if estado == "ok":
            estatus, fecha = dato
            resumen["ok"] += 1
            conteo[estatus] = conteo.get(estatus, 0) + 1
            for fila, (antes_e, antes_f) in filas.items():
                # Solo se manda lo que cambio: una fila que ya dice lo mismo
                # no gasta una escritura
                if (antes_e, antes_f) == (estatus, fecha):
                    resumen["iguales"] += 1
                else:
                    resumen["cambiaron"] += 1
                    por_escribir.append((fila, estatus, fecha))
            respaldo.append([pid, nums, estatus, fecha, "ok"])
        else:
            # No se escribe nada en la hoja: la proxima corrida lo reintenta
            resumen[estado if estado == "sin_respuesta" else "sin_paquete"] += 1
            respaldo.append([pid, nums, "", "", dato or estado])
        if len(por_escribir) >= LOTE_ESCRITURA:
            escribir(hoja, por_escribir, actualizar)
            por_escribir.clear()

    log("Abriendo Chrome...")
    driver = crear_driver()
    inicio = time.time()
    try:
        primero = lista[0]
        estado, dato = esperar_sesion(driver, primero, detener)
        log("Sesion lista.")
        al_terminar(primero, estado, dato)

        if len(lista) > 1:
            log(f"Consultando {len(lista) - 1} paquetes...")
            consultar(driver, lista[1:], al_terminar, pestanas, detener)
    except Exception as e:
        resumen["error"] = str(e)
        log(f"ERROR: {e}")
    finally:
        try:
            escribir(hoja, por_escribir, actualizar)
        except Exception as e:
            resumen["error"] = resumen["error"] or str(e)
            log(f"ERROR al escribir las ultimas {len(por_escribir)} filas: {e}")
        log("Cerrando Chrome...")
        cerrar_driver(driver)

    resumen["respaldo"] = guardar_respaldo(respaldo)
    resumen["minutos"] = (time.time() - inicio) / 60
    return resumen


def main():
    ap = argparse.ArgumentParser(description="Ultimo estatus de paquetes MELI")
    ap.add_argument("--desde", default=FECHA_INICIAL.strftime("%d-%m-%y"),
                    metavar="DD-MM-AA",
                    help="solo filas con FECHA desde ese dia "
                         f"(defecto {FECHA_INICIAL:%d-%m-%y}); 'todas' = sin filtro")
    ap.add_argument("--solo-vacias", action="store_true",
                    help="consultar solo filas sin estatus (no reconsultar)")
    ap.add_argument("--pestanas", type=int, default=PESTANAS,
                    help=f"pestañas en paralelo (defecto {PESTANAS})")
    ap.add_argument("--limite", type=int, default=0,
                    help="consultar solo los primeros N paquetes (prueba)")
    ap.add_argument("--ids", nargs="+", metavar="ID",
                    help="consultar estos ids sin tocar la hoja; solo genera "
                         "el CSV (acepta tambien un archivo .txt con un id "
                         "por linea)")
    args = ap.parse_args()

    if args.desde.strip().lower() == "todas":
        desde = None
    else:
        desde = fecha_de(args.desde)
        if not desde:
            print("--desde va como DD-MM-AA (01-10-26) o 'todas'.")
            return

    ids = None
    if args.ids:
        ids = []
        for valor in args.ids:
            if os.path.isfile(valor):
                with open(valor, encoding="utf-8-sig") as f:
                    ids.extend(f.read().split())
            else:
                ids.extend(re.split(r"[,\s]+", valor))

    print("=" * 70)
    print("  ULTIMO ESTATUS DE PAQUETES - MERCADO LIBRE ENVIOS")
    print("=" * 70)
    try:
        r = ejecutar(desde, not args.solo_vacias, args.pestanas,
                     args.limite, ids)
    except Exception as e:
        log(f"ERROR: {e}")
        return

    print()
    print("=" * 70)
    print(f"  LISTO. {r['ok']}/{r['consultados']} paquetes con estatus "
          f"en {r['minutos']:.1f} min.")
    print(f"  Filas que cambiaron: {r['cambiaron']}   sin cambio: {r['iguales']}")
    if r["finales"]:
        print(f"  Filas con estatus final, no consultadas: {r['finales']}")
    print()
    for clave in sorted(r["conteo"], key=lambda k: -r["conteo"][k]):
        print(f"    {clave:<34} {r['conteo'][clave]}")
    if r["sin_paquete"]:
        print(f"\n    {r['sin_paquete']} paquetes no se ven desde el SC seleccionado.")
    if r["sin_respuesta"]:
        print(f"    {r['sin_respuesta']} fichas no cargaron; se reintentan la "
              "proxima vez.")
    if r["respaldo"]:
        print(f"\n  Respaldo: {r['respaldo']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
