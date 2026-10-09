# -*- coding: utf-8 -*-
"""Sonda 2: trae el HTML crudo de la ficha via fetch (como lo haria el extractor) y lo guarda."""
import os, subprocess, sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

DIR = os.path.dirname(os.path.abspath(__file__))
PERFIL = os.path.join(DIR, "chrome_profile_sonda")
PID = sys.argv[1] if len(sys.argv) > 1 else "47956196042"
URL = "https://envios.adminml.com/logistics/package-management/package/"

# cerrar el Chrome que dejo abierto la sonda anterior (solo el de este perfil)
subprocess.run(["powershell", "-NoProfile", "-Command",
    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*chrome_profile_sonda*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"])
time.sleep(2)
for n in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"):
    try: os.remove(os.path.join(PERFIL, n))
    except Exception: pass

o = Options()
o.add_argument(f"--user-data-dir={PERFIL}")
o.add_argument("--start-maximized")
o.add_experimental_option("detach", True)
d = webdriver.Chrome(options=o)
d.get(URL + PID)
time.sleep(8)
print("url:", d.current_url, flush=True)
if "auth" in d.current_url:
    print("SIN SESION"); sys.exit(1)

d.set_script_timeout(60)
r = d.execute_async_script("""
const done = arguments[arguments.length-1];
fetch(arguments[0], {credentials:'include'}).then(r => r.text().then(t => done({status:r.status, url:r.url, body:t})))
 .catch(e => done({status:0, body:String(e)}));""", URL + PID)
print("fetch status:", r["status"], "url:", r.get("url"), "tam:", len(r["body"]), flush=True)
open(os.path.join(DIR, "ficha_fetch.html"), "w", encoding="utf-8").write(r["body"])
open(os.path.join(DIR, "ficha_dom.html"), "w", encoding="utf-8").write(d.page_source)
print("guardado", flush=True)
