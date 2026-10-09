import sys
import time
import csv
import threading
from datetime import datetime, timezone, timedelta
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QLabel, QPushButton, QTextEdit,
                             QLineEdit, QMessageBox, QFileDialog)
from PyQt5.QtCore import QThread, pyqtSignal
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL_PAQUETE = 'https://envios.adminml.com/logistics/package-management/package/'
DOMINIO = 'envios.adminml.com'

# Mexico no tiene horario de verano desde 2022: hora del centro fija en UTC-6.
# promise_date.to llega como epoch a las 00:00 de ese huso (1789192800 -> 12/sep).
ZONA_MX = timezone(timedelta(hours=-6))
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# La ficha del paquete no usa ninguna API: llega armada desde el servidor, con
# los datos en window.__PRELOADED_STATE__. Se pide el HTML con fetch (la sesion
# la pone el navegador) y se recorta ese objeto DENTRO de Chrome: cada ficha pesa
# ~1.3 MB y a Python solo regresan los cuatro campos.
JS_LOTE = r"""
const [base, ids] = arguments;
const done = arguments[arguments.length - 1];
const MARCA = 'window.__PRELOADED_STATE__ = ';

function recortar(t) {
  const i = t.indexOf(MARCA);
  if (i < 0) return null;
  const ini = t.indexOf('{', i + MARCA.length);
  let prof = 0, enTexto = false, escapado = false;
  for (let j = ini; j < t.length; j++) {
    const c = t[j];
    if (escapado) { escapado = false; continue; }
    if (c.charCodeAt(0) === 92) { escapado = true; continue; }
    if (c === '"') { enTexto = !enTexto; continue; }
    if (enTexto) continue;
    if (c === '{') prof++;
    else if (c === '}') { prof--; if (prof === 0) return t.substring(ini, j + 1); }
  }
  return null;
}

Promise.all(ids.map(id =>
  fetch(base + id, {credentials: 'include'})
    .then(r => r.text().then(t => {
      if (!r.ok) return {id: id, status: r.status};
      if (r.url.indexOf('/package-management/') < 0) return {id: id, status: r.status, login: true};
      // Paquete de otra estacion: el panel responde 200 con la pantalla "No autorizado"
      if (t.indexOf('kraken-not-authorized') >= 0) return {id: id, status: r.status, noauth: true};
      const s = recortar(t);
      if (!s) return {id: id, status: r.status, error: 'sin datos en la ficha'};
      let d;
      try { d = JSON.parse(s).shipmentDetail; } catch (e) { return {id: id, status: r.status, error: 'JSON: ' + e}; }
      if (!d) return {id: id, status: r.status, error: 'sin shipmentDetail'};
      const ri = d.receiver_info || {}, pd = d.promise_date || {};
      // Rango "09/sep - 12/sep" -> to; promesa de un solo dia ("08/oct") -> solo trae from
      return {id: id, status: 200, ok: true, sid: d.id, promesa: pd.to || pd.from,
              cp: ri.zip_code, ciudad: ri.city};
    }))
    .catch(e => ({id: id, status: 0, error: String(e)}))
)).then(done);
"""


def formatear_fecha(epoch):
    """1789192800 -> '12/sep', igual que la parte final de '09/sep - 12/sep' en pantalla."""
    if not epoch:
        return "N/A"
    dt = datetime.fromtimestamp(int(epoch), ZONA_MX)
    return f"{dt.day:02d}/{MESES[dt.month - 1]}"


class PackageWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(list)
    error_signal = pyqtSignal(str)
    paused_signal = pyqtSignal()

    def __init__(self, lista_ids):
        super().__init__()
        self.lista_ids = lista_ids
        self.driver = None
        self.is_paused = False
        self.current_results = []

        # Parámetros por defecto (se sobreescriben desde la UI)
        self.lote = 12
        self.pausa_lote = 0.3
        self.reintentos = 2

    def setup_browser(self):
        try:
            chrome_options = Options()
            chrome_options.add_argument("--start-maximized")
            chrome_options.add_experimental_option("detach", True)
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.get(URL_PAQUETE + self.lista_ids[0])
            self.update_signal.emit(">>> NAVEGADOR ABIERTO: Inicia sesión y presiona '2. Iniciar Extracción'.")
        except Exception as e:
            self.error_signal.emit(f"Error al abrir Chrome: {str(e)}")

    def custom_sleep(self, seconds):
        """Espera inteligente que permite que el botón de pausa siga funcionando"""
        end_time = time.time() + seconds
        while time.time() < end_time:
            while self.is_paused:
                time.sleep(0.5)
                end_time += 0.5  # Desplaza el tiempo final si estamos pausados
            time.sleep(0.1)

    def preparar_pagina(self):
        """El fetch solo funciona si Chrome esta en el dominio del panel."""
        try:
            for handle in self.driver.window_handles:
                self.driver.switch_to.window(handle)
                if DOMINIO in (self.driver.current_url or ""):
                    return
        except Exception:
            pass
        self.update_signal.emit(">>> Chrome no está en el panel; volviendo a la ficha...")
        self.driver.get(URL_PAQUETE + self.lista_ids[0])
        time.sleep(3)

    def pedir_lote(self, ids):
        """Pide varias fichas en paralelo. Devuelve {id: fila} de las que respondieron, y si caducó la sesión."""
        self.driver.set_script_timeout(180)
        respuestas = self.driver.execute_async_script(JS_LOTE, URL_PAQUETE, list(ids)) or []
        filas, sin_sesion, error_dicho = {}, False, False
        for r in respuestas:
            if r.get("ok"):
                filas[r["id"]] = {"ID": str(r.get("sid") or r["id"]),
                                  "Fecha Promesa": formatear_fecha(r.get("promesa")),
                                  "CP": r.get("cp") or "N/A",
                                  "Ciudad": r.get("ciudad") or "N/A"}
                continue
            if r.get("noauth"):
                # Reintentar no sirve: hay que cambiar de estacion en el panel
                filas[r["id"]] = {"ID": r["id"], "Fecha Promesa": "No autorizado", "CP": "N/A", "Ciudad": "N/A"}
                continue
            # La sesion caducada redirige al login de otro dominio: fetch lo rechaza con status 0
            if r.get("login") or r.get("status") in (0, 401, 403):
                sin_sesion = True
            elif 400 <= (r.get("status") or 0) < 500 and r.get("status") != 429:
                # 404/422: el ID no existe; reintentarlo no cambia nada
                filas[r["id"]] = {"ID": r["id"], "Fecha Promesa": "No existe", "CP": "N/A", "Ciudad": "N/A"}
            if not error_dicho:
                self.update_signal.emit(f"   [!] {r['id']}: HTTP {r.get('status')} {r.get('error', '')[:120]}")
                error_dicho = True
        sin_sesion = sin_sesion and not filas
        return filas, sin_sesion

    def esperar_login(self):
        self.is_paused = True
        self.paused_signal.emit()
        self.update_signal.emit(">>> La sesión parece caducada. Inicia sesión otra vez en Chrome y presiona 'Reanudar'.")
        while self.is_paused:
            time.sleep(0.5)
        self.preparar_pagina()

    def run_extraction(self):
        self.current_results = []
        try:
            self.preparar_pagina()
            ids = list(self.lista_ids)
            total = len(ids)
            resultados, faltantes = {}, []
            inicio = time.time()

            i = 0
            while i < total:
                while self.is_paused:
                    time.sleep(0.5)
                lote = ids[i:i + self.lote]
                filas, sin_sesion = self.pedir_lote(lote)
                if sin_sesion:
                    self.esperar_login()
                    continue  # se repite el mismo lote
                resultados.update(filas)
                faltantes.extend(x for x in lote if x not in filas)
                i += len(lote)
                self.current_results = [resultados[x] for x in ids if x in resultados]
                self.update_signal.emit(f"Procesados {min(i, total)}/{total}")
                self.custom_sleep(self.pausa_lote)

            # Alguna ficha puede fallar por intermitencia: reintentar en lotes mas chicos
            for vuelta in range(1, self.reintentos + 1):
                if not faltantes:
                    break
                self.update_signal.emit(f">>> Reintento {vuelta}: {len(faltantes)} sin respuesta...")
                self.custom_sleep(1.5)
                quedan, paso = [], max(3, self.lote // 2)
                for j in range(0, len(faltantes), paso):
                    lote = faltantes[j:j + paso]
                    filas, _ = self.pedir_lote(lote)
                    resultados.update(filas)
                    quedan.extend(x for x in lote if x not in filas)
                    self.custom_sleep(self.pausa_lote * 2)
                faltantes = quedan

            self.current_results = [resultados.get(x) or {"ID": x, "Fecha Promesa": "Error", "CP": "Error", "Ciudad": "Error"}
                                    for x in ids]
            no_autorizados = [x for x in ids if resultados.get(x, {}).get("Fecha Promesa") == "No autorizado"]
            no_existen = [x for x in ids if resultados.get(x, {}).get("Fecha Promesa") == "No existe"]
            correctos = total - len(faltantes) - len(no_autorizados) - len(no_existen)
            self.update_signal.emit(f">>> Lote finalizado en {time.time() - inicio:.0f} s: "
                                    f"{correctos}/{total} con datos.")
            if no_existen:
                self.update_signal.emit(f">>> {len(no_existen)} 'No existe' (el ID no corresponde a ningún paquete).")
            if no_autorizados:
                self.update_signal.emit(f">>> {len(no_autorizados)} 'No autorizado': son de otra estación. "
                                        f"Cambia la estación en el panel (\"Estás viendo: ...\") y extráelos aparte.")
            if faltantes:
                self.update_signal.emit(f">>> Sin respuesta ({len(faltantes)}), quedan como 'Error': {', '.join(faltantes[:20])}")
            self.finished_signal.emit(self.current_results)
        except Exception as e:
            self.error_signal.emit(f"Error en la extracción: {str(e)}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Extractor Fecha Promesa v2026 - Modo API")
        self.setMinimumSize(850, 750)
        self.results_data = []
        self.worker = None
        self.setup_ui()

    def setup_ui(self):
        central = QWidget()
        layout = QVBoxLayout()

        layout.addWidget(QLabel("<b>Nombre de la Estación (opcional):</b>"))
        self.station_name = QLineEdit()
        self.station_name.setPlaceholderText("Ejemplo: SMT1 (Si se deja vacío será 'reporte_paquetes')")
        layout.addWidget(self.station_name)

        # --- SECCIÓN DE CONFIGURACIÓN ---
        config_group = QWidget()
        config_layout = QGridLayout(config_group)
        config_layout.setContentsMargins(0, 5, 0, 10)

        config_layout.addWidget(QLabel("<b>Paquetes en paralelo por lote:</b>"), 0, 0)
        self.lote_input = QLineEdit("12")
        config_layout.addWidget(self.lote_input, 0, 1)

        config_layout.addWidget(QLabel("<b>Pausa entre lotes (s):</b>"), 1, 0)
        self.pausa_input = QLineEdit("0.3")
        config_layout.addWidget(self.pausa_input, 1, 1)

        layout.addWidget(config_group)
        # ----------------------------------------

        layout.addWidget(QLabel("<b>Pega aquí los IDs (Columna de Excel):</b>"))
        self.input_text = QTextEdit()
        layout.addWidget(self.input_text)

        btns_main = QHBoxLayout()
        self.btn_open = QPushButton("1. Abrir Navegador / Login")
        self.btn_open.clicked.connect(self.init_browser)
        self.btn_open.setStyleSheet("background-color: #bbdefb; height: 45px; font-weight: bold;")

        self.btn_run = QPushButton("2. Iniciar Extracción")
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self.start_process)
        self.btn_run.setStyleSheet("background-color: #c8e6c9; height: 45px; font-weight: bold;")

        self.btn_pause = QPushButton("Pausar")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_pause.setStyleSheet("background-color: #ffe082; height: 45px; font-weight: bold;")

        btns_main.addWidget(self.btn_open)
        btns_main.addWidget(self.btn_run)
        btns_main.addWidget(self.btn_pause)
        layout.addLayout(btns_main)

        btns_util = QHBoxLayout()
        self.btn_clear = QPushButton("Limpiar Todo / Nuevo Lote")
        self.btn_clear.clicked.connect(self.clear_data)
        self.btn_clear.setStyleSheet("background-color: #ffecb3;")

        self.btn_save = QPushButton("Guardar Resultado (CSV)")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_csv)

        btns_util.addWidget(self.btn_clear)
        btns_util.addWidget(self.btn_save)
        layout.addLayout(btns_util)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("background: #1e1e1e; color: #00ff00; font-family: Consolas;")
        layout.addWidget(self.log_area)

        central.setLayout(layout)
        self.setCentralWidget(central)

    def mostrar_pausado(self):
        self.btn_pause.setText("Reanudar")
        self.btn_pause.setStyleSheet("background-color: #81c784; height: 45px; font-weight: bold;")
        self.results_data = self.worker.current_results
        self.btn_save.setEnabled(True)

    def toggle_pause(self):
        if not self.worker: return

        if not self.worker.is_paused:
            self.worker.is_paused = True
            self.mostrar_pausado()
            self.log_area.append(">>> Extracción PAUSADA. Puedes guardar los datos procesados hasta ahora.")
        else:
            self.worker.is_paused = False
            self.btn_pause.setText("Pausar")
            self.btn_pause.setStyleSheet("background-color: #ffe082; height: 45px; font-weight: bold;")
            self.log_area.append(">>> Extracción REANUDADA...")
            self.btn_save.setEnabled(False)

    def clear_data(self):
        self.input_text.clear()
        self.log_area.clear()
        self.results_data = []
        self.btn_save.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.log_area.append(">>> Datos limpios. Navegador listo para nuevo lote.")

    def init_browser(self):
        ids_text = self.input_text.toPlainText().strip()
        ids = [i.strip() for i in ids_text.split('\n') if i.strip().isdigit()]

        if not ids:
            QMessageBox.warning(self, "Error", "Pega IDs válidos primero para abrir el navegador.")
            return

        if not self.worker or not self.worker.driver:
            self.worker = PackageWorker(ids)
            self.worker.update_signal.connect(lambda m: self.log_area.append(m))
            self.worker.finished_signal.connect(self.on_finished)
            self.worker.error_signal.connect(lambda e: QMessageBox.critical(self, "Error", e))
            self.worker.paused_signal.connect(self.mostrar_pausado)
            self.worker.setup_browser()
            self.btn_run.setEnabled(True)
        else:
            self.log_area.append(">>> Navegador ya abierto. Se usará la sesión actual.")
            self.btn_run.setEnabled(True)

    def start_process(self):
        ids_text = self.input_text.toPlainText().strip()
        ids = [i.strip() for i in ids_text.split('\n') if i.strip().isdigit()]

        if not ids:
            QMessageBox.warning(self, "Error", "No hay IDs para procesar.")
            return

        # Capturamos la configuración ingresada por el usuario en la UI
        try:
            self.worker.lote = max(1, int(self.lote_input.text()))
            self.worker.pausa_lote = float(self.pausa_input.text())
        except ValueError:
            QMessageBox.warning(self, "Error de Formato", "Por favor, ingresa solo números válidos en la configuración.")
            return

        self.worker.lista_ids = ids
        self.worker.is_paused = False

        self.btn_run.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.btn_save.setEnabled(False)

        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("Pausar")
        self.btn_pause.setStyleSheet("background-color: #ffe082; height: 45px; font-weight: bold;")

        threading.Thread(target=self.worker.run_extraction, daemon=True).start()

    def on_finished(self, data):
        self.results_data = data
        self.btn_save.setEnabled(True)
        self.btn_run.setEnabled(True)
        self.btn_clear.setEnabled(True)

        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("Pausar")

        QMessageBox.information(self, "Éxito", "Lote procesado correctamente.")

    def save_csv(self):
        station = self.station_name.text().strip()
        default_name = f"{station}.csv" if station else "reporte_paquetes.csv"

        path, _ = QFileDialog.getSaveFileName(self, "Guardar", default_name, "CSV (*.csv)")
        if path:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=["ID", "Fecha Promesa", "CP", "Ciudad"])
                writer.writeheader()
                writer.writerows(self.results_data)
            self.log_area.append(f">>> Guardado correctamente en: {path}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
