# -*- coding: utf-8 -*-
"""
Ultimo estatus de cada paquete - Mercado Libre Envios (Extractor_RTS)

Revisa dos hojas del mismo archivo y, para cada paquete, abre su ficha en
el panel:

    https://envios.adminml.com/logistics/package-management/package/<id>

de donde toma el primer estado del "Historial del paquete" (el mas
reciente) y su fecha ("21/09/2026 14:20").

  CONSOLIDADO_RTS   id en C -> estatus en H, fecha en I
                    No reconsulta Entregado, Dañado ni Devuelto.
  DEVOLUCIONES      id en D -> estatus en E, fecha en F, fecha de esta
                    revision en G, alerta en H.
                    Reconsulta todo lo que no este en Devuelto. En H pone
                    "ALERTA 24 HRS" si entre la FECHA (A) y la revision
                    pasaron 24 horas, o "MAYOR A 48 HRS" si pasaron 48;
                    salvo que este Devuelto, Entregado o en Destruccion.

En las dos, solo filas con FECHA (columna A) desde FECHA_INICIAL (01-10-26).

Como se obtiene el dato: la ficha se lee tal como la dibuja el panel
(texto de la pagina a partir de "Historial del paquete"). La API que la
alimenta aun no esta descubierta; ver skills/api-en-vez-de-scraping.

Acceso a Google Sheets: a traves de una Web App de Apps Script
(apps_script/estatus_paquetes.gs, con las instrucciones dentro), que
tambien manda el correo diario de las 10 am. Python no maneja
credenciales de Google: solo la URL /exec y su clave, en
webapp_sheets.json. Al compilar, ese archivo va dentro del .exe, asi que
el programa funciona igual en cualquier computadora y con cualquier
usuario; uno junto al .exe tiene prioridad sobre el de adentro.

La ventana esta en estatus_app.py. Este archivo tambien se corre solo:

    python extraer_estatus_paquetes.py                  # desde 01-10-26
    python extraer_estatus_paquetes.py --desde 15-10-26
    python extraer_estatus_paquetes.py --limite 10      # prueba corta
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

# El archivo donde se lee y se escribe. Se le manda a la Web App en cada
# pedido: para usar otro basta cambiarlo aqui
SHEET_ID = "1hQvyCcWZGOo4rQcinDmWg_kYaOfZTcS3H7IZSBeaE9c"

# Solo se consultan las filas con FECHA (columna A) desde este dia
FECHA_INICIAL = date(2026, 10, 1)

ALERTA_24 = "ALERTA 24 HRS"
ALERTA_48 = "MAYOR A 48 HRS"
ALERTAS = (ALERTA_24, ALERTA_48)

# Que hoja revisar y donde. Columnas contadas desde 0 (A = 0).
#   estatus:    la del estatus. Le siguen, en este orden: la fecha del
#               estatus; si revision, la fecha de esta revision; si hay
#               alertas, la alerta.
#   finales:    estatus que ya no cambian. Esas filas no se reconsultan.
#               La Web App lo vuelve a revisar antes de escribir.
#   sin_alerta: estatus que no llevan alerta aunque pase el tiempo.
# Se compara el inicio del texto sin acentos: "Entregado - Place" y
# "Entregado-Place" cuentan como "entregado".
HOJAS = (
    {"nombre": "CONSOLIDADO_RTS", "id": 2, "estatus": 7,           # C -> H, I
     "finales": ("entregado", "danado", "devuelto"),
     "revision": False, "alertas": False, "sin_alerta": ()},
    {"nombre": "DEVOLUCIONES", "id": 3, "estatus": 4,              # D -> E, F, G, H
     "finales": ("devuelto",),
     "revision": True, "alertas": True,
     "sin_alerta": ("devuelto", "entregado", "destruccion")},
)

URL_PAQUETE = "https://envios.adminml.com/logistics/package-management/package/"
DOMINIO = "envios.adminml.com"

# Pestañas abiertas a la vez. Con 4 el panel se trababa (Chrome dejaba de
# responder 2 minutos); con 1 rinde ~30 paquetes por minuto sin fallar
PESTANAS = 1
# Segundos maximos esperando a que una ficha muestre su historial
ESPERA_FICHA = 30
# Vueltas extra para las fichas que no cargaron a tiempo
REINTENTOS = 1
# Cada cuantos cambios se escribe en la hoja: si el programa se corta,
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


def es_final(estatus, finales):
    t = sin_acentos(limpiar(estatus)).lower()
    return any(t.startswith(f) for f in finales)


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


def alerta_para(fecha_fila, ahora=None):
    """Horas entre la FECHA de la fila y la revision. La columna A no trae
    hora: se cuenta desde las 00:00 de ese dia."""
    ahora = ahora or datetime.now()
    horas = (ahora - datetime(fecha_fila.year, fecha_fila.month,
                              fecha_fila.day)).total_seconds() / 3600
    if horas >= 48:
        return ALERTA_48
    if horas >= 24:
        return ALERTA_24
    return ""


# ---------------------------------------------------------------- Google

class WebApp:
    """El archivo, visto a traves de la Web App de Apps Script.

    El script corre en la cuenta de Google del dueño de la hoja, asi que
    aqui no hacen falta credenciales: solo la URL /exec y la clave (TOKEN)
    que se puso en el script. Ver apps_script/estatus_paquetes.gs.
    """

    def __init__(self, url, token):
        self.url = url
        self.token = token

    def _pedir(self, metodo, hoja, cuerpo=None, extra=None):
        params = urllib.parse.urlencode(
            {"token": self.token, "sheet": SHEET_ID, "hoja": hoja,
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
            # (por ejemplo, sin permiso sobre el archivo, o sin la pestaña)
            raise RuntimeError(
                f"La Web App no devolvio JSON al leer {hoja}. Revisa que la "
                "pestaña exista, que la URL termine en /exec, que el acceso "
                "sea 'Cualquier usuario' y que el dueño del script pueda "
                f"editar el archivo. Empieza con: {limpiar(texto[:100])}")
        if not resp.get("ok"):
            error = resp.get("error")
            if error == "token":
                raise RuntimeError(
                    "La Web App rechazo la clave: el token de "
                    f"{NOMBRE_CONFIG} no coincide con el TOKEN del script.")
            raise RuntimeError(f"La Web App respondio un error: {error}")
        return resp

    def leer_tabla(self, hoja):
        """Columnas A..I de cada fila, como se ven en la hoja."""
        return self._pedir("GET", hoja, extra={"completas": "1"}).get("tabla") or []

    def escribir(self, hoja, columna, finales, cambios):
        """cambios: [{fila, valores}] desde `columna` (1 = A). None deja la
        celda como esta."""
        cuerpo = {"token": self.token, "sheet": SHEET_ID, "hoja": hoja,
                  "columna": columna, "finales": list(finales),
                  "cambios": cambios}
        return self._pedir("POST", hoja, cuerpo)


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


def leer_pendientes(web, conf, desde=FECHA_INICIAL):
    """Que filas de una hoja consultar.

    Devuelve (pendientes, limpiezas, cuentas):
      pendientes: {id: [fila]}, cada fila un dict con su numero y lo que
                  dice hoy. Un paquete puede estar en varias filas.
      limpiezas:  filas ya finales que todavia muestran una alerta vieja
                  (alguien las marco a mano): solo se les borra la alerta.
    """
    tabla = web.leer_tabla(conf["nombre"])
    c_est = conf["estatus"]
    c_obs = col_alerta(conf)

    pendientes, limpiezas = {}, []
    cuentas = {"en_rango": 0, "finales": 0, "fuera": 0}
    for n, fila in enumerate(tabla, start=1):
        fila = list(fila) + [""] * (9 - len(fila))
        pid = normalizar_id(fila[conf["id"]])
        if not pid:
            continue
        f = fecha_de(fila[0])                    # A
        if desde and (not f or f < desde):
            cuentas["fuera"] += 1
            continue
        cuentas["en_rango"] += 1
        actual = {"fila": n, "fecha_fila": f,
                  "estatus": limpiar(fila[c_est]),
                  "fecha": limpiar(fila[c_est + 1]),
                  "obs": limpiar(fila[c_obs]) if conf["alertas"] else ""}
        if es_final(actual["estatus"], conf["finales"]):
            cuentas["finales"] += 1
            if conf["alertas"] and actual["obs"] in ALERTAS:
                valores = [None] * (c_obs - c_est) + [""]
                limpiezas.append({"fila": n, "valores": valores})
            continue
        pendientes.setdefault(pid, []).append(actual)

    filas = sum(len(v) for v in pendientes.values())
    desde_txt = f"desde {desde:%d-%m-%y}" if desde else "de todas las fechas"
    log(f"{conf['nombre']}: {cuentas['en_rango']} filas {desde_txt}; "
        f"{filas} por consultar ({len(pendientes)} paquetes).")
    if cuentas["finales"]:
        log(f"  {cuentas['finales']} filas ya en estatus final "
            f"({', '.join(conf['finales'])}) no se reconsultan.")
    return pendientes, limpiezas, cuentas


def col_alerta(conf):
    """Columna (desde 0) de la alerta: tras estatus, fecha y revision."""
    return conf["estatus"] + 2 + (1 if conf["revision"] else 0)


def cambio_de_fila(conf, actual, nuevo, ahora=None):
    """Lo que hay que escribir en una fila, o None si nada cambia.

    nuevo: (estatus, fecha) leidos del panel, o None si no se pudo ver el
    paquete; en ese caso solo puede cambiar la alerta.
    ahora: momento de la revision (el mismo para toda la corrida).
    """
    ahora = ahora or datetime.now()
    valores = [None, None]
    estatus = actual["estatus"]
    if nuevo:
        estatus, fecha = nuevo
        if (estatus, fecha) != (actual["estatus"], actual["fecha"]):
            valores = [estatus, fecha]

    if conf["revision"]:
        # Solo si el panel mostro el paquete: es cuando de verdad se reviso
        valores.append(f"{ahora:%d/%m/%Y %H:%M}" if nuevo else None)

    if conf["alertas"]:
        obs = None
        # Nunca se pisa una nota escrita a mano: solo se escribe la alerta
        # si la celda esta vacia o ya tiene una alerta de este programa
        if actual["obs"] in ("",) + ALERTAS and actual["fecha_fila"]:
            alerta = ("" if es_final(estatus, conf["sin_alerta"])
                      else alerta_para(actual["fecha_fila"], ahora))
            if alerta != actual["obs"]:
                obs = alerta
        valores.append(obs)

    if all(v is None for v in valores):
        return None
    return {"fila": actual["fila"], "valores": valores}


def escribir(web, conf, cambios):
    """Manda a la hoja los cambios de una de las pestañas."""
    if web is None or not cambios:
        return
    for intento in range(3):
        try:
            resp = web.escribir(conf["nombre"], conf["estatus"] + 1,
                                conf["finales"], cambios)
            if resp.get("omitidas"):
                log(f"  {conf['nombre']}: {resp['omitidas']} filas cambiaron "
                    "en la hoja mientras corria el programa; no se tocaron.")
            return
        except Exception as e:
            if intento == 2 or "clave" in str(e):
                raise
            log(f"  Fallo al escribir en {conf['nombre']} ({str(e)[:80]}); "
                "reintentando...")
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
        w.writerow(["ID PAQUETE", "HOJA:FILAS", "ESTATUS", "FECHA", "RESULTADO"])
        w.writerows(filas)
    return ruta


def ejecutar(desde=FECHA_INICIAL, pestanas=PESTANAS, limite=0, ids=None,
             detener=None):
    """La corrida completa: hojas -> panel -> hojas. La usan la ventana y
    la linea de comando.

    Un paquete que esta en las dos hojas se consulta una sola vez.

    ids: lista de ids para consultar sin tocar la hoja (solo CSV).
    detener: threading.Event; si se activa, se para y se guarda lo hecho.

    Devuelve un resumen (dict) para mostrar.
    """
    resumen = {"consultados": 0, "ok": 0, "cambiaron": 0,
               "sin_paquete": 0, "sin_respuesta": 0, "finales": 0,
               "alerta_24": 0, "alerta_48": 0,
               "conteo": {}, "respaldo": "", "minutos": 0.0, "error": ""}

    web = None
    # id -> [(conf, fila_actual)]
    pendientes = {}
    # nombre de hoja -> cambios por mandar
    buzon = {conf["nombre"]: [] for conf in HOJAS}
    conf_de = {conf["nombre"]: conf for conf in HOJAS}

    if ids:
        for valor in ids:
            pid = normalizar_id(valor)
            if pid:
                pendientes.setdefault(pid, [])
        log(f"Sin hoja de calculo: {len(pendientes)} ids, solo CSV.")
    else:
        log("Leyendo la hoja de calculo...")
        web = abrir_hoja()
        for conf in HOJAS:
            p, limpiezas, cuentas = leer_pendientes(web, conf, desde)
            resumen["finales"] += cuentas["finales"]
            buzon[conf["nombre"]].extend(limpiezas)
            for pid, filas in p.items():
                pendientes.setdefault(pid, []).extend((conf, f) for f in filas)

    def vaciar(forzar=False):
        for nombre, cambios in buzon.items():
            if cambios and (forzar or len(cambios) >= LOTE_ESCRITURA):
                escribir(web, conf_de[nombre], cambios)
                cambios.clear()

    lista = list(pendientes)
    if limite:
        lista = lista[:limite]
    if not lista:
        log("No hay paquetes por consultar.")
        vaciar(forzar=True)
        return resumen

    respaldo = []
    conteo = resumen["conteo"]
    # Una sola hora de revision para toda la corrida
    ahora = datetime.now()

    def al_terminar(pid, estado, dato):
        filas = pendientes[pid]
        resumen["consultados"] += 1
        donde = ",".join(f"{c['nombre']}:{f['fila']}" for c, f in filas)
        nuevo = dato if estado == "ok" else None
        if nuevo:
            resumen["ok"] += 1
            conteo[nuevo[0]] = conteo.get(nuevo[0], 0) + 1
            respaldo.append([pid, donde, nuevo[0], nuevo[1], "ok"])
        else:
            # Sin dato del panel la fila conserva su estatus; solo puede
            # cambiar la alerta
            resumen["sin_respuesta" if estado == "sin_respuesta"
                    else "sin_paquete"] += 1
            respaldo.append([pid, donde, "", "", dato or estado])

        for conf, actual in filas:
            cambio = cambio_de_fila(conf, actual, nuevo, ahora)
            if conf["alertas"]:
                g = cambio["valores"][-1] if cambio else None
                alerta = actual["obs"] if g is None else g
                if alerta == ALERTA_24:
                    resumen["alerta_24"] += 1
                elif alerta == ALERTA_48:
                    resumen["alerta_48"] += 1
            if cambio:
                resumen["cambiaron"] += 1
                buzon[conf["nombre"]].append(cambio)
        vaciar()

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
            vaciar(forzar=True)
        except Exception as e:
            resumen["error"] = resumen["error"] or str(e)
            log(f"ERROR al escribir los ultimos cambios: {e}")
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
        r = ejecutar(desde, args.pestanas, args.limite, ids)
    except Exception as e:
        log(f"ERROR: {e}")
        return

    print()
    print("=" * 70)
    print(f"  LISTO. {r['ok']}/{r['consultados']} paquetes con estatus "
          f"en {r['minutos']:.1f} min.")
    print(f"  Filas actualizadas: {r['cambiaron']}")
    print(f"  Devoluciones: {r['alerta_48']} mayores a 48 hrs, "
          f"{r['alerta_24']} con alerta de 24 hrs")
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
