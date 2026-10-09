# -*- coding: utf-8 -*-
"""
Estatus de paquetes - Interfaz grafica

Un solo boton: lee la hoja CONSOLIDADO_RTS, consulta en Mercado Libre el
ultimo estatus de cada paquete desde la fecha elegida (01-10-26 por
defecto) y lo escribe en las columnas H e I.

La primera vez llena todo; las siguientes solo reconsultan los paquetes
cuyo estatus aun puede cambiar (todo menos Entregado, Dañado y Devuelto).

Compilar (ver LEEME.md):
    pyinstaller --onefile --windowed --name Extractor_RTS ^
        --collect-all selenium --add-data "webapp_sheets.json;." estatus_app.py

--collect-all selenium no es opcional: Selenium importa sus modulos al
vuelo y PyInstaller no los ve; sin eso el .exe falla con "No module named
selenium.webdriver.chrome.webdriver". Tambien mete selenium-manager, que
baja el chromedriver que corresponde al Chrome de cada computadora.
"""

import os
import sys
import threading
import traceback
from datetime import datetime

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QUrl, QDate
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QMessageBox, QDateEdit,
)

import extraer_estatus_paquetes as nucleo
from estilo_ventana import (
    FONDO, PANEL, BORDE, TEXTO, SUAVE, AMARILLO, AZUL, VERDE, ROJO,
    FUENTE, FUENTE_NUM, barra_titulo_oscura, _carpeta_base, Tarjeta,
    boton, boton_chico,
)


class Puente(QObject):
    mensaje = Signal(str)
    terminado = Signal(object)
    fallo = Signal(str)


class Ordenes(QObject):
    """Las ordenes viajan por señal: si se llamara al metodo directamente,
    correria en el hilo de la ventana y la congelaria."""
    actualizar = Signal(object)


class Trabajador(QObject):
    def __init__(self, puente):
        super().__init__()
        self.p = puente
        self.detener = threading.Event()
        # Los mensajes del nucleo llegan a la ventana por la señal; Qt los
        # pasa de hilo solo
        nucleo.AVISAR = self.p.mensaje.emit

    @Slot(object)
    def actualizar(self, desde):
        self.detener.clear()
        try:
            resumen = nucleo.ejecutar(desde=desde, detener=self.detener)
            self.p.terminado.emit(resumen)
        except Exception as e:
            traceback.print_exc()
            self.p.fallo.emit(str(e))


class Ventana(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Extractor RTS - Mercado Libre")
        self.resize(640, 480)
        self.setMinimumSize(580, 430)
        self.trabajando = False
        self._armar()
        self._hilo()

    def _armar(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background: {FONDO};")
        raiz = QVBoxLayout(central)
        raiz.setContentsMargins(12, 10, 12, 10)
        raiz.setSpacing(7)

        titulo = QLabel("EXTRACTOR RTS")
        titulo.setStyleSheet(
            f"color: {TEXTO}; font-family: {FUENTE_NUM}; font-size: 17px;"
            f" font-weight: 900; letter-spacing: 1px;")
        raiz.addWidget(titulo)

        self.paso = QLabel("Presiona ACTUALIZAR. Si Chrome pide iniciar sesion "
                           "en Mercado Libre, hazlo ahi.")
        self.paso.setWordWrap(True)
        self.paso.setStyleSheet(
            f"color: {AMARILLO}; font-family: {FUENTE}; font-size: 11px;"
            f" font-weight: 600;")
        raiz.addWidget(self.paso)

        # --- fecha inicial ---
        fila = QHBoxLayout()
        fila.setSpacing(7)
        eti = QLabel("Paquetes con FECHA desde")
        eti.setStyleSheet(f"color: {SUAVE}; font-family: {FUENTE};"
                          f" font-size: 11px;")
        fila.addWidget(eti)

        self.desde = QDateEdit()
        self.desde.setCalendarPopup(True)
        self.desde.setDisplayFormat("dd/MM/yyyy")
        f = nucleo.FECHA_INICIAL
        self.desde.setDate(QDate(f.year, f.month, f.day))
        self.desde.setFixedHeight(30)
        # Con el ancho por defecto el año se ve cortado ("01/10/202")
        self.desde.setMinimumWidth(130)
        self.desde.setStyleSheet(
            f"QDateEdit {{ background: {PANEL}; color: {TEXTO};"
            f" border: 1px solid {BORDE}; border-radius: 6px;"
            f" padding: 0 8px; font-family: {FUENTE}; font-size: 11px; }}"
            f"QDateEdit::drop-down {{ border: none; width: 22px; }}")
        fila.addWidget(self.desde)
        fila.addStretch()
        raiz.addLayout(fila)

        nota = QLabel(f"Hoja {nucleo.HOJA}: lee la columna C y escribe estatus "
                      "en H y fecha en I. No reconsulta los Entregado, Dañado "
                      "ni Devuelto.")
        nota.setWordWrap(True)
        nota.setStyleSheet(f"color: {SUAVE}; font-family: {FUENTE};"
                           f" font-size: 10px;")
        raiz.addWidget(nota)

        # --- botones ---
        botones = QHBoxLayout()
        botones.setSpacing(7)
        self.b_actualizar = boton("ACTUALIZAR ESTATUS", AMARILLO)
        self.b_actualizar.clicked.connect(self.al_actualizar)
        self.b_detener = boton("DETENER", ROJO)
        self.b_detener.clicked.connect(self.al_detener)
        self.b_detener.setEnabled(False)
        botones.addWidget(self.b_actualizar, 3)
        botones.addWidget(self.b_detener, 1)
        raiz.addLayout(botones)

        # --- tarjetas ---
        tarjetas = QHBoxLayout()
        tarjetas.setSpacing(7)
        self.t_consultados = Tarjeta("Consultados", TEXTO)
        self.t_cambiaron = Tarjeta("Filas cambiadas", AMARILLO)
        self.t_finales = Tarjeta("Finales (no se tocan)", VERDE)
        self.t_sin_ver = Tarjeta("No se vieron", ROJO)
        for t in (self.t_consultados, self.t_cambiaron, self.t_finales,
                  self.t_sin_ver):
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
        self.b_carpeta = boton_chico("Abrir carpeta de respaldos")
        self.b_carpeta.clicked.connect(self.al_carpeta)
        pie.addWidget(self.b_carpeta)
        pie.addStretch()
        raiz.addLayout(pie)

    def _hilo(self):
        self.puente = Puente()
        self.ordenes = Ordenes()
        self.hilo = QThread()
        self.trabajador = Trabajador(self.puente)
        self.trabajador.moveToThread(self.hilo)

        self.ordenes.actualizar.connect(self.trabajador.actualizar)
        self.puente.mensaje.connect(self.escribir)
        self.puente.terminado.connect(self.al_terminar)
        self.puente.fallo.connect(self.al_fallar)
        self.hilo.start()

    def escribir(self, t):
        self.registro.appendPlainText(f"[{datetime.now():%H:%M:%S}] {t}")
        barra = self.registro.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _ocupado(self, si):
        self.trabajando = si
        self.b_actualizar.setEnabled(not si)
        self.desde.setEnabled(not si)
        self.b_detener.setEnabled(si)

    def al_actualizar(self):
        for t in (self.t_consultados, self.t_cambiaron, self.t_finales,
                  self.t_sin_ver):
            t.poner("-")
        self._ocupado(True)
        desde = self.desde.date().toPython()
        self.paso.setText(f"Trabajando: paquetes desde {desde:%d/%m/%Y}. Si "
                          "Chrome pide iniciar sesion, hazlo ahi; el programa "
                          "sigue solo.")
        self.ordenes.actualizar.emit(desde)

    def al_detener(self):
        self.b_detener.setEnabled(False)
        self.paso.setText("Deteniendo: se escribe lo ya consultado...")
        self.trabajador.detener.set()

    def al_carpeta(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(_carpeta_base()))

    def al_terminar(self, r):
        self._ocupado(False)
        self.t_consultados.poner(r["consultados"])
        self.t_cambiaron.poner(r["cambiaron"])
        self.t_finales.poner(r["finales"])
        self.t_sin_ver.poner(r["sin_paquete"] + r["sin_respuesta"])

        if r["error"]:
            self.paso.setText("Termino con un problema; lo consultado se guardo.")
            QMessageBox.warning(
                self, "Termino con un problema",
                f"{r['error']}\n\nSe escribieron {r['cambiaron']} filas antes "
                "del problema. Vuelve a presionar ACTUALIZAR para seguir.")
            return
        if not r["consultados"]:
            self.paso.setText("No habia paquetes por consultar.")
            QMessageBox.information(
                self, "Nada que hacer",
                "No hay paquetes por consultar desde esa fecha: o no hay "
                "filas, o todas tienen estatus final.")
            return

        self.paso.setText(f"Listo  ·  {r['ok']}/{r['consultados']} paquetes en "
                          f"{r['minutos']:.1f} min")
        detalle = "\n".join(
            f"   {e}: {n}" for e, n in
            sorted(r["conteo"].items(), key=lambda x: -x[1]))
        aviso = ""
        if r["sin_paquete"]:
            aviso += (f"\n\n{r['sin_paquete']} paquetes no se ven desde el SC "
                      "seleccionado en Mercado Libre: sus filas quedaron igual.")
        if r["sin_respuesta"]:
            aviso += (f"\n{r['sin_respuesta']} fichas no cargaron; se "
                      "reintentan la proxima vez.")
        QMessageBox.information(
            self, "Listo",
            f"{r['ok']} paquetes con estatus\n"
            f"{r['cambiaron']} filas actualizadas en la hoja, "
            f"{r['iguales']} sin cambio\n\n{detalle}{aviso}")

    def al_fallar(self, mensaje):
        self._ocupado(False)
        self.paso.setText("Hubo un problema")
        self.escribir(f"ERROR: {mensaje}")
        QMessageBox.warning(self, "No se pudo", mensaje)

    def closeEvent(self, evento):
        if self.trabajando:
            r = QMessageBox.question(
                self, "Sigue trabajando",
                "Hay una actualizacion en curso. ¿Detenerla y cerrar?\n"
                "Lo ya consultado se escribe en la hoja.")
            if r != QMessageBox.Yes:
                evento.ignore()
                return
            self.trabajador.detener.set()
        self.hilo.quit()
        # Hasta un minuto para escribir lo pendiente y cerrar Chrome
        self.hilo.wait(60000)
        evento.accept()


def main():
    app = QApplication(sys.argv)
    ventana = Ventana()
    ventana.show()
    barra_titulo_oscura(ventana)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
