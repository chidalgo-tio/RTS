# -*- coding: utf-8 -*-
"""Prueba PackageWorker de fechapromesa.py contra el panel real, sin GUI (PyQt5 simulado)."""
import os, subprocess, sys, time, types

DIR = os.path.dirname(os.path.abspath(__file__))
PERFIL = os.path.join(DIR, "chrome_profile_sonda")
SRC = sys.argv[1]
ARCHIVO = sys.argv[2][1:] if sys.argv[2].startswith("@") else None
IDS = [l.strip() for l in open(ARCHIVO)] if ARCHIVO else sys.argv[2:]
IDS = [i for i in IDS if i.isdigit()]

# --- PyQt5 simulado: solo lo que usa el modulo al importarse
class _Signal:
    def __init__(self, *a): pass
    def __set_name__(self, owner, name): self.name = name
    def __get__(self, obj, owner):
        return types.SimpleNamespace(emit=lambda *a: print(f"  <{self.name}>", *[str(x)[:300] for x in a], flush=True),
                                     connect=lambda *a: None)
qtw = types.ModuleType("PyQt5.QtWidgets")
for n in ("QApplication QMainWindow QWidget QVBoxLayout QHBoxLayout QGridLayout QLabel "
          "QPushButton QTextEdit QLineEdit QMessageBox QFileDialog").split():
    setattr(qtw, n, type(n, (), {}))
qtc = types.ModuleType("PyQt5.QtCore"); qtc.QThread = object; qtc.pyqtSignal = _Signal
sys.modules.update({"PyQt5": types.ModuleType("PyQt5"), "PyQt5.QtWidgets": qtw, "PyQt5.QtCore": qtc})

sys.path.insert(0, os.path.dirname(SRC))
import fechapromesa as fp
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

assert fp.formatear_fecha(1789192800) == "12/sep", fp.formatear_fecha(1789192800)
assert fp.formatear_fecha(None) == "N/A"

subprocess.run(["powershell", "-NoProfile", "-Command",
    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*chrome_profile_sonda*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"])
time.sleep(2)
o = Options(); o.add_argument(f"--user-data-dir={PERFIL}"); o.add_experimental_option("detach", True)

w = fp.PackageWorker(IDS)
w.driver = webdriver.Chrome(options=o)
w.driver.get("https://www.google.com")        # a proposito fuera del panel: preparar_pagina debe corregirlo
w.run_extraction()
import csv, collections
res = w.current_results
if ARCHIVO:
    salida = ARCHIVO.replace(".txt", "_resultado.csv")
    with open(salida, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=["ID", "Fecha Promesa", "CP", "Ciudad"])
        wr.writeheader(); wr.writerows(res)
    print("CSV:", salida)
else:
    for r in res: print("  ", r)
print("filas", len(res), "de", len(IDS), " duplicados en entrada", len(IDS) - len(set(IDS)))
print("fechas", collections.Counter(r["Fecha Promesa"] for r in res).most_common())
print("sin CP", sum(r["CP"] in ("N/A", "Error") for r in res), " sin ciudad", sum(r["Ciudad"] in ("N/A", "Error") for r in res))
print("ID distinto al pedido", [(a, r["ID"]) for a, r in zip(IDS, res) if a != r["ID"]][:5])
