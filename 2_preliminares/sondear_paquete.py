# -*- coding: utf-8 -*-
"""Sonda: descubre la API que llena la ficha de un paquete (fecha promesa, CP, ciudad).

Abre Chrome con el log de red activo. Espera a que el usuario inicie sesion y abra
cualquier ficha /package-management/package/<id>. Entonces recarga, espia, pulsa
"Direccion de entrega", espia otra vez y escribe un reporte JSON. No guarda cookies.
"""
import json, os, re, sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

DIR = os.path.dirname(os.path.abspath(__file__))
PERFIL = os.path.join(DIR, "chrome_profile_sonda")
SALIDA = os.path.join(DIR, "reporte_paquete.json")
BASURA = (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".ico", ".map", "analytics",
          "newrelic", "datadog", "melidata", "/metrics", "/beacon", "/tracking", "sentry")


def log(m):
    print(time.strftime("[%H:%M:%S] ") + m, flush=True)


def leer(driver):
    pet = {}
    for e in driver.get_log("performance"):
        try:
            msg = json.loads(e["message"])["message"]
        except Exception:
            continue
        p = msg.get("params", {}); rid = p.get("requestId")
        if not rid:
            continue
        if msg.get("method") == "Network.requestWillBeSent":
            r = p.get("request", {})
            pet.setdefault(rid, {}).update(url=r.get("url", ""), metodo=r.get("method"),
                                          tipo=p.get("type"), post=(r.get("postData") or "")[:1000])
        elif msg.get("method") == "Network.responseReceived":
            r = p.get("response", {})
            pet.setdefault(rid, {}).update(status=r.get("status"), mime=r.get("mimeType"))
    return {k: v for k, v in pet.items() if v.get("url")}


def candidatas(driver, pet, pid):
    out = []
    for rid, p in pet.items():
        u = p["url"].lower()
        if not u.startswith("http") or any(b in u for b in BASURA):
            continue
        if "json" not in (p.get("mime") or "") and p.get("tipo") not in ("XHR", "Fetch"):
            continue
        try:
            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid}).get("body", "")
        except Exception:
            body = ""
        p = dict(p, menciona_id=pid in body, menciona_promesa=bool(re.search(r"promise", body, re.I)),
                 tam=len(body), muestra=body[:6000])
        out.append(p)
    out.sort(key=lambda p: (p["menciona_id"], p["menciona_promesa"], p["tam"]), reverse=True)
    return out


def main():
    o = Options()
    o.add_argument(f"--user-data-dir={PERFIL}")
    o.add_argument("--start-maximized")
    o.add_experimental_option("detach", True)
    o.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    o.add_experimental_option("perfLoggingPrefs", {"enableNetwork": True, "enablePage": False})
    d = webdriver.Chrome(options=o)
    d.get("https://envios.adminml.com/logistics/package-management/")
    log("Chrome abierto. Esperando login y una ficha de paquete...")

    pid = None
    for _ in range(1800):                       # hasta 30 min
        m = re.search(r"/package-management/package/(\d+)", d.current_url or "")
        if m and "auth" not in d.current_url:
            pid = m.group(1); break
        d.get_log("performance")                # no acumular
        time.sleep(1)
    if not pid:
        log("No se abrio ninguna ficha. Fin."); return
    log(f"Ficha detectada: {pid}. Espiando la carga...")
    time.sleep(4)
    d.get_log("performance")
    d.refresh(); time.sleep(10)
    carga = candidatas(d, leer(d), pid)

    d.get_log("performance")
    try:
        b = d.find_element(By.XPATH, "//span[contains(text(), 'Dirección de entrega')]")
        d.execute_script("arguments[0].click();", b)
    except Exception as e:
        log(f"No se pudo pulsar 'Direccion de entrega': {e}")
    time.sleep(6)
    direccion = candidatas(d, leer(d), pid)

    html = d.page_source
    rep = {"pid": pid, "url": d.current_url, "carga": carga, "direccion": direccion,
           "html_tam": len(html), "html_menciona_promise": "promise" in html.lower()}
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    log(f"Reporte: {SALIDA}")
    for p in carga[:8]:
        log(f"  CARGA {p.get('status')} id={p['menciona_id']} prom={p['menciona_promesa']} {p['metodo']} {p['url'][:140]}")
    for p in direccion[:8]:
        log(f"  DIREC {p.get('status')} id={p['menciona_id']} {p['metodo']} {p['url'][:140]}")


if __name__ == "__main__":
    main()
