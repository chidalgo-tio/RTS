# -*- coding: utf-8 -*-
"""
Casos PNR - Interfaz grafica

Eliges el periodo de facturacion y presionas extraer. Por defecto viene
puesto el periodo en curso, que es el caso normal.
"""

import os
import sys
import time
import traceback
import urllib.parse
from datetime import datetime

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QPlainTextEdit, QMessageBox, QComboBox,
)

import extraer_pnr as nucleo
from estilo_ventana import (
    FONDO, PANEL, BORDE, TEXTO, SUAVE, AMARILLO, AZUL, VERDE, ROJO,
    FUENTE, FUENTE_NUM, barra_titulo_oscura, _carpeta_base, Tarjeta,
    boton, boton_chico,
)


# El menu ACTUALIZAR > Extraer PNRs del Google Sheets abre
# casospnr://extraer?periodo=202610Q2 y Windows lanza este programa con
# ese enlace como argumento
PROTOCOLO = "casospnr"

# Minutos esperando a que la persona inicie sesion (modo automatico)
ESPERA_LOGIN = 10


def registrar_protocolo():
    """Le dice a Windows que los enlaces casospnr:// abren este .exe. Va en
    HKEY_CURRENT_USER (sin permisos de administrador) y se repite en cada
    arranque, asi sigue sirviendo si el .exe cambia de carpeta."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import winreg
        base = rf"Software\Classes\{PROTOCOLO}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Casos PNR")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{sys.executable}" "%1"')
    except OSError:
        pass


def periodo_del_enlace(argumentos):
    """casospnr://extraer?periodo=202610Q2 -> '202610Q2', o None."""
    for a in argumentos:
        if a.lower().startswith(PROTOCOLO + ":"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(a).query)
            p = (q.get("periodo") or [""])[0].upper()
            if len(p) == 8 and p[:6].isdigit() and p[6:] in ("Q1", "Q2"):
                return p
    return None


class Puente(QObject):
    mensaje = Signal(str)
    terminado = Signal(str, object)
    fallo = Signal(str)


class Ordenes(QObject):
    """Las ordenes viajan por señal: si se llamara al metodo directamente,
    correria en el hilo de la ventana y la congelaria."""
    abrir = Signal()
    extraer = Signal(str)
    automatico = Signal(str)
    cerrar = Signal()


class Trabajador(QObject):
    def __init__(self, puente):
        super().__init__()
        self.p = puente
        self.driver = None
        self.registros = []

    def log(self, t):
        self.p.mensaje.emit(t)

    @Slot()
    def abrir_navegador(self):
        try:
            self.log("Abriendo Chrome...")
            self.driver = nucleo.crear_driver()
            self.driver.get(nucleo.URL)
            self.log("Chrome abierto. Inicia sesion si te lo pide.")
            self.p.terminado.emit("abierto", None)
        except Exception as e:
            self.p.fallo.emit(self._explicar(e))

    @Slot(str)
    def extraer(self, periodo):
        try:
            if not self.driver:
                self.p.fallo.emit("Primero abre Chrome e inicia sesion.")
                return

            self.log(f"Consultando los casos PNR de {periodo}...")
            self.registros = nucleo.extraer_todo(
                self.driver, periodo, avisar=self.log)

            if not self.registros:
                self.p.fallo.emit(
                    f"No hay casos PNR en el periodo {periodo}.\n\n"
                    "Prueba con el periodo anterior.")
                return

            # La mitad de las 23 columnas solo estan en la ficha de cada
            # caso, asi que siempre se abren.
            self.log(f"Abriendo la ficha de cada uno de los "
                     f"{len(self.registros)} casos...")
            n = nucleo.completar_detalles(
                self.driver, self.registros, avisar=self.log)
            self.log(f"Detalles completos: {n}/{len(self.registros)}")

            csvf = nucleo.guardar_control(self.registros, periodo)
            self.log(f"Listo: {os.path.basename(csvf)} (y .xlsx)")

            sheets = None
            if nucleo.hay_sheets():
                self.log("Cargando REPORTE_TEMP en el Google Sheets y "
                         "actualizando el CONSOLIDADO...")
                try:
                    sheets = nucleo.enviar_a_sheets(
                        nucleo.filas_control(self.registros, periodo))
                    self.log(f"Sheets: {sheets.get('nuevos', 0)} nuevos, "
                             f"{sheets.get('actualizados', 0)} actualizados, "
                             f"{sheets.get('sinCambios', 0)} sin cambios.")
                except Exception as e:
                    sheets = {"error": str(e)}
                    self.log(f"ERROR al cargar el Google Sheets: {e}")
            self.p.terminado.emit("extraido", (self.registros, csvf, sheets))

        except Exception as e:
            traceback.print_exc()
            self.p.fallo.emit(self._explicar(e))

    @Slot(str)
    def automatico(self, periodo):
        """Lo pidio el Google Sheets: abrir Chrome, esperar la sesion y
        extraer, sin que nadie pulse nada."""
        try:
            if not self.driver:
                self.log("Abriendo Chrome...")
                self.driver = nucleo.crear_driver()
                self.driver.get(nucleo.URL)
            self.log("Si Chrome pide iniciar sesion en Mercado Libre, hazlo "
                     "ahi; el programa sigue solo.")
            limite = time.time() + ESPERA_LOGIN * 60
            while time.time() < limite:
                time.sleep(2)
                try:
                    url = self.driver.current_url or ""
                except Exception:
                    continue
                if "/logistics/case-center" in url:
                    break
            else:
                self.p.fallo.emit(f"En {ESPERA_LOGIN} minutos no se vio la "
                                  "bandeja de casos con la sesion iniciada.")
                return
            self.log("Sesion lista.")
            time.sleep(3)
        except Exception as e:
            self.p.fallo.emit(self._explicar(e))
            return
        self.extraer(periodo)

    @Slot()
    def cerrar(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def _explicar(self, e):
        """Traduce los errores tecnicos a algo accionable."""
        t = str(e)
        if "user data directory is already in use" in t.lower():
            return ("El perfil de Chrome esta en uso por otra ventana.\n"
                    "Cierra las ventanas que abrio este programa y reintenta.")
        if "DevToolsActivePort" in t:
            return ("Chrome no pudo arrancar.\n"
                    "Cierra todas sus ventanas y vuelve a intentar.")
        if "Bandeja de soporte" in t:
            return t
        if "401" in t or "403" in t:
            return ("La sesion caduco.\n"
                    "Vuelve a iniciar sesion en la ventana de Chrome.")
        return t


def periodos_recientes(cuantos=12):
    """Los ultimos periodos de facturacion, del mas nuevo al mas viejo."""
    hoy = datetime.now()
    ano, mes = hoy.year, hoy.month
    q = 2 if hoy.day > 15 else 1
    lista = []
    for _ in range(cuantos):
        lista.append(f"{ano}{mes:02d}Q{q}")
        q -= 1
        if q == 0:                       # antes de Q1 viene el Q2 anterior
            q = 2
            mes -= 1
            if mes == 0:
                mes, ano = 12, ano - 1
    return lista


MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def nombre_periodo(p):
    """202608Q2 -> 'agosto Q2 (16 al 31)'"""
    ano, mes, q = int(p[:4]), int(p[4:6]), p[6:]
    import calendar
    if q == "Q1":
        dias = "1 al 15"
    else:
        dias = f"16 al {calendar.monthrange(ano, mes)[1]}"
    return f"{MESES[mes]} {ano}  ·  {q} ({dias})"


class Ventana(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Casos PNR - Mercado Libre")
        self.resize(620, 450)
        self.setMinimumSize(560, 410)
        self.archivo = None
        self._armar()
        self._hilo()

    def _armar(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background: {FONDO};")
        raiz = QVBoxLayout(central)
        raiz.setContentsMargins(12, 10, 12, 10)
        raiz.setSpacing(7)

        titulo = QLabel("CASOS PNR")
        titulo.setStyleSheet(
            f"color: {TEXTO}; font-family: {FUENTE_NUM}; font-size: 17px;"
            f" font-weight: 900; letter-spacing: 1px;")
        raiz.addWidget(titulo)

        self.paso = QLabel("Paso 1 de 2  ·  Abre Mercado Libre e inicia sesion")
        self.paso.setStyleSheet(
            f"color: {AMARILLO}; font-family: {FUENTE}; font-size: 11px;"
            f" font-weight: 600;")
        raiz.addWidget(self.paso)

        # --- periodo ---
        fila = QHBoxLayout()
        fila.setSpacing(7)
        eti = QLabel("Periodo")
        eti.setStyleSheet(f"color: {SUAVE}; font-family: {FUENTE};"
                          f" font-size: 11px;")
        fila.addWidget(eti)

        self.combo = QComboBox()
        self.combo.setFixedHeight(30)
        self.combo.setMinimumWidth(230)
        self.combo.setCursor(Qt.PointingHandCursor)
        self.combo.setStyleSheet(
            f"QComboBox {{ background: {PANEL}; color: {TEXTO};"
            f" border: 1px solid {BORDE}; border-radius: 6px;"
            f" padding: 0 8px; font-family: {FUENTE}; font-size: 11px; }}"
            f"QComboBox::drop-down {{ border: none; width: 22px; }}"
            f"QComboBox QAbstractItemView {{ background: {PANEL};"
            f" color: {TEXTO}; selection-background-color: {AZUL};"
            f" border: 1px solid {BORDE}; outline: none; }}")
        for p in periodos_recientes():
            self.combo.addItem(nombre_periodo(p), p)
        fila.addWidget(self.combo)

        # Sin casillas: el programa hace siempre lo mismo, que es lo que
        # se necesita. Antes habia tres y elegir mal daba un archivo
        # distinto del que se esperaba.
        fila.addStretch()
        raiz.addLayout(fila)

        nota = QLabel("Genera el CSV con las 23 columnas de la plataforma")
        nota.setStyleSheet(f"color: {SUAVE}; font-family: {FUENTE};"
                           f" font-size: 10px;")
        raiz.addWidget(nota)

        # --- botones ---
        botones = QHBoxLayout()
        botones.setSpacing(7)
        self.b_abrir = boton("1 · ABRIR MERCADO LIBRE", AMARILLO)
        self.b_abrir.clicked.connect(self.al_abrir)
        self.b_extraer = boton("2 · EXTRAER CASOS", AZUL)
        self.b_extraer.clicked.connect(self.al_extraer)
        self.b_extraer.setEnabled(False)
        botones.addWidget(self.b_abrir)
        botones.addWidget(self.b_extraer)
        raiz.addLayout(botones)

        # --- tarjetas ---
        tarjetas = QHBoxLayout()
        tarjetas.setSpacing(7)
        self.t_casos = Tarjeta("Casos", TEXTO)
        self.t_monto = Tarjeta("Monto total", AMARILLO)
        self.t_abiertos = Tarjeta("Sin cerrar", ROJO)
        self.t_cerrados = Tarjeta("Cerrados", VERDE)
        for t in (self.t_casos, self.t_monto, self.t_abiertos, self.t_cerrados):
            tarjetas.addWidget(t)
        raiz.addLayout(tarjetas)

        # --- registro ---
        self.registro = QPlainTextEdit()
        self.registro.setReadOnly(True)
        self.registro.setStyleSheet(
            f"QPlainTextEdit {{ background: #16181d; color: {SUAVE};"
            f" border: 1px solid {BORDE}; border-radius: 7px;"
            f" font-family: Consolas, monospace; font-size: 10px;"
            f" padding: 6px; }}")
        raiz.addWidget(self.registro, 1)

        # --- pie ---
        pie = QHBoxLayout()
        pie.setSpacing(7)
        self.b_carpeta = boton_chico("Abrir carpeta")
        self.b_carpeta.clicked.connect(self.al_carpeta)
        self.b_carpeta.setEnabled(False)
        pie.addWidget(self.b_carpeta)
        pie.addStretch()
        raiz.addLayout(pie)

    def _hilo(self):
        self.puente = Puente()
        self.ordenes = Ordenes()
        self.hilo = QThread()
        self.trabajador = Trabajador(self.puente)
        self.trabajador.moveToThread(self.hilo)

        self.ordenes.abrir.connect(self.trabajador.abrir_navegador)
        self.ordenes.extraer.connect(self.trabajador.extraer)
        self.ordenes.automatico.connect(self.trabajador.automatico)
        self.ordenes.cerrar.connect(self.trabajador.cerrar)

        self.puente.mensaje.connect(self.escribir)
        self.puente.terminado.connect(self.al_terminar)
        self.puente.fallo.connect(self.al_fallar)
        self.hilo.start()

    def escribir(self, t):
        self.registro.appendPlainText(f"[{datetime.now():%H:%M:%S}] {t}")
        barra = self.registro.verticalScrollBar()
        barra.setValue(barra.maximum())

    def al_abrir(self):
        self.b_abrir.setEnabled(False)
        self.paso.setText("Abriendo Chrome...")
        self.ordenes.abrir.emit()

    def al_extraer(self):
        periodo = self.combo.currentData()
        self.b_extraer.setEnabled(False)
        self.paso.setText(f"Extrayendo los casos de {periodo}...")
        self.ordenes.extraer.emit(periodo)

    def desde_el_sheets(self, periodo):
        """Lo pidio el menu del Google Sheets: elige el periodo y arranca."""
        i = self.combo.findData(periodo)
        if i < 0:
            self.combo.addItem(nombre_periodo(periodo), periodo)
            i = self.combo.count() - 1
        self.combo.setCurrentIndex(i)
        self.escribir(f"Pedido desde el Google Sheets: periodo {periodo}.")
        self.b_abrir.setEnabled(False)
        self.b_extraer.setEnabled(False)
        self.paso.setText(f"Extrayendo {periodo}. Si Chrome pide iniciar "
                          "sesion, hazlo ahi; el programa sigue solo.")
        self.ordenes.automatico.emit(periodo)

    def al_carpeta(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(_carpeta_base()))

    def al_terminar(self, etapa, datos):
        if etapa == "abierto":
            self.b_extraer.setEnabled(True)
            self.paso.setText(
                "Paso 2 de 2  ·  Inicia sesion y presiona EXTRAER CASOS")
            return

        registros, csvf, sheets = datos
        self.archivo = csvf
        por_estado, suma, sin_monto = nucleo.resumen(registros)

        cerrados = por_estado.get("Cerrado", 0)
        self.t_casos.poner(len(registros))
        self.t_monto.poner(f"${suma:,.0f}")
        self.t_abiertos.poner(len(registros) - cerrados)
        self.t_cerrados.poner(cerrados)

        self.b_extraer.setEnabled(True)
        self.b_carpeta.setEnabled(True)
        self.paso.setText(f"Listo  ·  {len(registros)} casos")

        detalle = "\n".join(f"   {e}: {n}" for e, n in
                            sorted(por_estado.items(), key=lambda x: -x[1]))
        sin_driver = sum(1 for r in registros if not r["driver"])
        aviso = ""
        if sin_driver:
            aviso = (f"\n\n{sin_driver} casos vienen sin conductor asignado "
                     "en el panel de Mercado Libre.")
        if sheets and sheets.get("error"):
            aviso += ("\n\nNO se cargo el Google Sheets:\n" + sheets["error"])
        elif sheets:
            aviso += (f"\n\nGoogle Sheets (CONSOLIDADO):\n"
                      f"   {sheets.get('nuevos', 0)} nuevos\n"
                      f"   {sheets.get('actualizados', 0)} actualizados\n"
                      f"   {sheets.get('sinCambios', 0)} sin cambios")
        self.b_abrir.setEnabled(self.trabajador.driver is None)
        QMessageBox.information(
            self, "Listo",
            f"{len(registros)} casos PNR\n\n{detalle}\n\n"
            f"Monto total: ${suma:,.2f}{aviso}\n\n"
            f"{os.path.basename(csvf)} (y .xlsx)")

    def al_fallar(self, mensaje):
        self.b_abrir.setEnabled(True)
        self.b_extraer.setEnabled(self.trabajador.driver is not None)
        self.paso.setText("Hubo un problema")
        self.escribir(f"ERROR: {mensaje}")
        QMessageBox.warning(self, "No se pudo", mensaje)

    def closeEvent(self, evento):
        self.ordenes.cerrar.emit()
        self.hilo.quit()
        self.hilo.wait(3000)
        evento.accept()


def main():
    registrar_protocolo()
    app = QApplication(sys.argv)
    ventana = Ventana()
    ventana.show()
    barra_titulo_oscura(ventana)
    pedido = periodo_del_enlace(sys.argv[1:])
    if pedido:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: ventana.desde_el_sheets(pedido))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
