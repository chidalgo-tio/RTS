# -*- coding: utf-8 -*-
"""
Reporte de operacion - Interfaz grafica

Desde / Hasta y un boton: baja el reporte General - Last Mile de Reportes
de operacion y lo manda al Google Sheets "Reportes de operacion ML"
(Extraccion, Consolidado y Registro). Ver extraer_reporte_operacion.py.

Compilar (ver LEEME.md):
    pyinstaller --onefile --windowed --name Reporte_Operacion ^
        --collect-all selenium --add-data "webapp_reportes.json;." ^
        reporte_operacion_app.py

El .exe lleva dentro la URL y la clave de la Web App: no va al repo.
"""

import sys
import threading
import traceback
from datetime import date, datetime, timedelta

from PySide6.QtCore import QObject, QThread, Signal, Slot, QUrl, QDate
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QMessageBox, QDateEdit,
)

import extraer_reporte_operacion as nucleo
from estilo_ventana import (
    FONDO, PANEL, BORDE, TEXTO, SUAVE, AMARILLO, VERDE, ROJO, AZUL,
    FUENTE, FUENTE_NUM, barra_titulo_oscura, Tarjeta, boton, boton_chico,
)

URL_SHEETS = ("https://docs.google.com/spreadsheets/d/"
              "1lyoWfp8dLrte7Q6qZnyqpnjGMbI5ZFP0awJ1RRyF7Tg/edit")


class Puente(QObject):
    mensaje = Signal(str)
    terminado = Signal(object)
    fallo = Signal(str)


class Ordenes(QObject):
    """Las ordenes viajan por señal para que corran en el hilo de trabajo
    y no congelen la ventana."""
    extraer = Signal(object, object)


class Trabajador(QObject):
    def __init__(self, puente):
        super().__init__()
        self.p = puente
        self.detener = threading.Event()
        nucleo.AVISAR = self.p.mensaje.emit

    @Slot(object, object)
    def extraer(self, desde, hasta):
        self.detener.clear()
        try:
            self.p.terminado.emit(nucleo.ejecutar(desde, hasta, self.detener))
        except Exception as e:
            traceback.print_exc()
            self.p.fallo.emit(str(e))


class Ventana(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Reporte de Operación - Mercado Libre")
        self.resize(640, 470)
        self.setMinimumSize(580, 420)
        self.trabajando = False
        self._armar()
        self._hilo()

    def _fecha(self, valor):
        campo = QDateEdit()
        campo.setCalendarPopup(True)
        campo.setDisplayFormat("dd/MM/yyyy")
        campo.setDate(QDate(valor.year, valor.month, valor.day))
        campo.setFixedHeight(30)
        campo.setMinimumWidth(130)
        campo.setStyleSheet(
            f"QDateEdit {{ background: {PANEL}; color: {TEXTO};"
            f" border: 1px solid {BORDE}; border-radius: 6px;"
            f" padding: 0 8px; font-family: {FUENTE}; font-size: 11px; }}"
            f"QDateEdit::drop-down {{ border: none; width: 22px; }}")
        return campo

    def _etiqueta(self, texto):
        e = QLabel(texto)
        e.setStyleSheet(f"color: {SUAVE}; font-family: {FUENTE}; font-size: 11px;")
        return e

    def _armar(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet(f"background: {FONDO};")
        raiz = QVBoxLayout(central)
        raiz.setContentsMargins(12, 10, 12, 10)
        raiz.setSpacing(7)

        titulo = QLabel("REPORTE DE OPERACIÓN")
        titulo.setStyleSheet(
            f"color: {TEXTO}; font-family: {FUENTE_NUM}; font-size: 17px;"
            f" font-weight: 900; letter-spacing: 1px;")
        raiz.addWidget(titulo)

        self.paso = QLabel("Elige las fechas y presiona EXTRAER. Si Chrome pide "
                           "iniciar sesión en Mercado Libre, hazlo ahí.")
        self.paso.setWordWrap(True)
        self.paso.setStyleSheet(
            f"color: {AMARILLO}; font-family: {FUENTE}; font-size: 11px;"
            f" font-weight: 600;")
        raiz.addWidget(self.paso)

        # --- fechas: por defecto, ayer ---
        ayer = date.today() - timedelta(days=1)
        fila = QHBoxLayout()
        fila.setSpacing(7)
        fila.addWidget(self._etiqueta("Desde"))
        self.desde = self._fecha(ayer)
        fila.addWidget(self.desde)
        fila.addSpacing(10)
        fila.addWidget(self._etiqueta("Hasta"))
        self.hasta = self._fecha(ayer)
        fila.addWidget(self.hasta)
        fila.addSpacing(10)
        for texto, dias in (("Ayer", 1), ("7 días", 7), ("Este mes", 0)):
            b = boton_chico(texto)
            b.clicked.connect(lambda _, d=dias: self.atajo(d))
            fila.addWidget(b)
        fila.addStretch()
        raiz.addLayout(fila)

        nota = QLabel("Reportes de operación › General › Last Mile. Extracción: lo "
                      "de esta corrida. Consolidado: una fila por fecha y ruta, en "
                      "orden cronológico; si una fecha ya estaba, solo se "
                      "actualiza lo que cambió.")
        nota.setWordWrap(True)
        nota.setStyleSheet(f"color: {SUAVE}; font-family: {FUENTE}; font-size: 10px;")
        raiz.addWidget(nota)

        # --- botones ---
        botones = QHBoxLayout()
        botones.setSpacing(7)
        self.b_extraer = boton("EXTRAER AL GOOGLE SHEETS", AMARILLO)
        self.b_extraer.clicked.connect(self.al_extraer)
        self.b_detener = boton("DETENER", ROJO)
        self.b_detener.clicked.connect(self.al_detener)
        self.b_detener.setEnabled(False)
        botones.addWidget(self.b_extraer, 3)
        botones.addWidget(self.b_detener, 1)
        raiz.addLayout(botones)

        # --- tarjetas ---
        tarjetas = QHBoxLayout()
        tarjetas.setSpacing(7)
        self.t_filas = Tarjeta("Filas bajadas", TEXTO)
        self.t_nuevas = Tarjeta("Nuevas", VERDE)
        self.t_actualizadas = Tarjeta("Actualizadas", AMARILLO)
        self.t_iguales = Tarjeta("Sin cambio", SUAVE)
        self.t_total = Tarjeta("Total consolidado", AZUL)
        for t in self._tarjetas():
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

        pie = QHBoxLayout()
        self.b_sheets = boton_chico("Abrir el Google Sheets")
        self.b_sheets.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(URL_SHEETS)))
        pie.addWidget(self.b_sheets)
        pie.addStretch()
        raiz.addLayout(pie)

    def _tarjetas(self):
        return (self.t_filas, self.t_nuevas, self.t_actualizadas,
                self.t_iguales, self.t_total)

    def _hilo(self):
        self.puente = Puente()
        self.ordenes = Ordenes()
        self.hilo = QThread()
        self.trabajador = Trabajador(self.puente)
        self.trabajador.moveToThread(self.hilo)
        self.ordenes.extraer.connect(self.trabajador.extraer)
        self.puente.mensaje.connect(self.escribir)
        self.puente.terminado.connect(self.al_terminar)
        self.puente.fallo.connect(self.al_fallar)
        self.hilo.start()

    def atajo(self, dias):
        hoy = date.today()
        ayer = hoy - timedelta(days=1)
        ini = hoy.replace(day=1) if dias == 0 else ayer - timedelta(days=dias - 1)
        fin = ayer if ayer >= ini else hoy
        self.desde.setDate(QDate(ini.year, ini.month, ini.day))
        self.hasta.setDate(QDate(fin.year, fin.month, fin.day))

    def escribir(self, t):
        self.registro.appendPlainText(f"[{datetime.now():%H:%M:%S}] {t}")
        barra = self.registro.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _ocupado(self, si):
        self.trabajando = si
        self.b_extraer.setEnabled(not si)
        self.desde.setEnabled(not si)
        self.hasta.setEnabled(not si)
        self.b_detener.setEnabled(si)

    def al_extraer(self):
        desde = self.desde.date().toPython()
        hasta = self.hasta.date().toPython()
        if hasta < desde:
            QMessageBox.warning(self, "Fechas", "La fecha Hasta es anterior a Desde.")
            return
        if hasta >= date.today():
            r = QMessageBox.question(
                self, "Incluye hoy",
                "El rango incluye hoy: las rutas del día todavía están en curso "
                "y sus números van a cambiar. ¿Extraer de todas formas?\n\n"
                "(Si vuelves a extraer mañana, se actualizan solas.)")
            if r != QMessageBox.Yes:
                return
        for t in self._tarjetas():
            t.poner("-")
        self._ocupado(True)
        self.paso.setText(f"Trabajando: del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}. "
                          "Si Chrome pide iniciar sesión, hazlo ahí; el programa "
                          "sigue solo.")
        self.ordenes.extraer.emit(desde, hasta)

    def al_detener(self):
        self.b_detener.setEnabled(False)
        self.paso.setText("Deteniendo: lo ya cargado se queda en el Sheets...")
        self.trabajador.detener.set()

    def al_terminar(self, r):
        self._ocupado(False)
        self.t_filas.poner(r["filas"])
        self.t_nuevas.poner(r["nuevas"])
        self.t_actualizadas.poner(r["actualizadas"])
        self.t_iguales.poner(r["iguales"])
        self.t_total.poner(r["total"])
        if r["error"]:
            self.paso.setText("Terminó con un problema; lo ya cargado se quedó en el Sheets.")
            QMessageBox.warning(self, "Terminó con un problema", r["error"])
            return
        self.paso.setText(f"Listo · {r['filas']} filas en {r['minutos']:.1f} min")
        QMessageBox.information(
            self, "Listo",
            f"{r['filas']} filas bajadas de Mercado Libre\n\n"
            f"Consolidado:\n   {r['nuevas']} nuevas\n"
            f"   {r['actualizadas']} actualizadas (cambiaron)\n"
            f"   {r['iguales']} sin cambio\n"
            f"   {r['total']} filas en total")

    def al_fallar(self, mensaje):
        self._ocupado(False)
        self.paso.setText("Hubo un problema")
        self.escribir(f"ERROR: {mensaje}")
        QMessageBox.warning(self, "No se pudo", mensaje)

    def closeEvent(self, evento):
        if self.trabajando:
            r = QMessageBox.question(
                self, "Sigue trabajando",
                "Hay una extracción en curso. ¿Detenerla y cerrar?\n"
                "Lo ya cargado se queda en el Sheets.")
            if r != QMessageBox.Yes:
                evento.ignore()
                return
            self.trabajador.detener.set()
        self.hilo.quit()
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
