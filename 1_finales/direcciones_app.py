# -*- coding: utf-8 -*-
"""
Direcciones y poligonos - Interfaz grafica

1. Pegas los ids y presionas EXTRAER: trae de cada ficha los datos del
   comprador, la direccion completa y las coordenadas, y guarda el
   reporte (CSV).
2. Eliges como agrupar (paquetes por poligono, o por codigo postal, y si
   quieres solo ciertos CP) y presionas ARMAR POLIGONOS: el reporte sale
   con la columna POLIGONO y se abre el mapa.

Se puede armar otra vez con otras opciones sin volver a extraer, o
cargar un reporte de otro dia.

Compilar:
    pyinstaller --onefile --windowed --name Extractor_Direcciones ^
        --collect-all selenium direcciones_app.py
"""

import os
import sys
import threading
import traceback
from datetime import datetime

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QMessageBox, QRadioButton, QSpinBox, QLineEdit,
    QFileDialog, QButtonGroup,
)

import extraer_direcciones as nucleo
from estilo_ventana import (
    FONDO, PANEL, BORDE, TEXTO, SUAVE, AMARILLO, AZUL, VERDE, ROJO,
    FUENTE, FUENTE_NUM, barra_titulo_oscura, Tarjeta, boton, boton_chico,
)


class Puente(QObject):
    mensaje = Signal(str)
    extraido = Signal(object)
    fallo = Signal(str)


class Ordenes(QObject):
    extraer = Signal(object)


class Trabajador(QObject):
    def __init__(self, puente):
        super().__init__()
        self.p = puente
        self.detener = threading.Event()
        nucleo.base.AVISAR = self.p.mensaje.emit

    @Slot(object)
    def extraer(self, ids):
        self.detener.clear()
        try:
            filas = nucleo.extraer(ids, detener=self.detener)
            self.p.extraido.emit(filas)
        except Exception as e:
            traceback.print_exc()
            self.p.fallo.emit(str(e))


def etiqueta(texto, chica=False):
    l = QLabel(texto)
    l.setWordWrap(True)
    l.setStyleSheet(f"color: {SUAVE}; font-family: {FUENTE};"
                    f" font-size: {10 if chica else 11}px;")
    return l


ESTILO_CAMPO = (
    f"background: {PANEL}; color: {TEXTO}; border: 1px solid {BORDE};"
    f" border-radius: 6px; padding: 2px 6px; font-family: {FUENTE}; font-size: 11px;")


class Ventana(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Direcciones y polígonos - Mercado Libre")
        self.resize(700, 640)
        self.setMinimumSize(620, 580)
        self.filas = []
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

        titulo = QLabel("DIRECCIONES Y POLÍGONOS")
        titulo.setStyleSheet(
            f"color: {TEXTO}; font-family: {FUENTE_NUM}; font-size: 17px;"
            f" font-weight: 900; letter-spacing: 1px;")
        raiz.addWidget(titulo)

        self.paso = QLabel("Paso 1 · Pega los IDs (uno por renglón) y presiona EXTRAER.")
        self.paso.setWordWrap(True)
        self.paso.setStyleSheet(f"color: {AMARILLO}; font-family: {FUENTE};"
                                f" font-size: 11px; font-weight: 600;")
        raiz.addWidget(self.paso)

        # --- ids ---
        self.ids = QPlainTextEdit()
        self.ids.setPlaceholderText("48239561960\n48233823023\n...")
        self.ids.setFixedHeight(110)
        self.ids.setStyleSheet(f"QPlainTextEdit {{ {ESTILO_CAMPO} font-family: Consolas; }}")
        raiz.addWidget(self.ids)

        fila = QHBoxLayout()
        self.b_extraer = boton("1 · EXTRAER DIRECCIONES", AMARILLO)
        self.b_extraer.clicked.connect(self.al_extraer)
        self.b_detener = boton("DETENER", ROJO)
        self.b_detener.clicked.connect(self.al_detener)
        self.b_detener.setEnabled(False)
        fila.addWidget(self.b_extraer, 3)
        fila.addWidget(self.b_detener, 1)
        raiz.addLayout(fila)

        # --- como agrupar ---
        raiz.addWidget(etiqueta("Paso 2 · Cómo armar los polígonos:"))
        self.r_cantidad = QRadioButton("Por cantidad de paquetes")
        self.r_cp = QRadioButton("Por código postal")
        self.r_cantidad.setChecked(True)
        grupo = QButtonGroup(self)
        grupo.addButton(self.r_cantidad)
        grupo.addButton(self.r_cp)
        for r in (self.r_cantidad, self.r_cp):
            r.setStyleSheet(f"color: {TEXTO}; font-family: {FUENTE}; font-size: 11px;")

        opciones = QHBoxLayout()
        opciones.addWidget(self.r_cantidad)
        opciones.addWidget(self.r_cp)
        opciones.addStretch()
        raiz.addLayout(opciones)

        tope = QHBoxLayout()
        tope.addWidget(etiqueta("Máximo de paquetes por polígono:"))
        self.por_poligono = QSpinBox()
        self.por_poligono.setRange(0, 1000)
        self.por_poligono.setValue(25)
        self.por_poligono.setFixedSize(80, 28)
        self.por_poligono.setStyleSheet(f"QSpinBox {{ {ESTILO_CAMPO} }}")
        tope.addWidget(self.por_poligono)
        tope.addWidget(etiqueta("(por CP: 0 = un solo polígono por CP)", chica=True))
        tope.addStretch()
        raiz.addLayout(tope)

        cps = QHBoxLayout()
        cps.addWidget(etiqueta("Solo estos CP (opcional):"))
        self.solo_cp = QLineEdit()
        self.solo_cp.setPlaceholderText("35118, 35119   (vacío = todos)")
        self.solo_cp.setFixedHeight(28)
        self.solo_cp.setStyleSheet(f"QLineEdit {{ {ESTILO_CAMPO} }}")
        cps.addWidget(self.solo_cp, 1)
        raiz.addLayout(cps)

        fila2 = QHBoxLayout()
        self.b_poligonos = boton("2 · ARMAR POLÍGONOS Y ABRIR MAPA", AZUL)
        self.b_poligonos.clicked.connect(self.al_armar)
        self.b_poligonos.setEnabled(False)
        fila2.addWidget(self.b_poligonos, 3)
        self.b_cargar = boton_chico("Cargar reporte anterior")
        self.b_cargar.clicked.connect(self.al_cargar)
        fila2.addWidget(self.b_cargar, 1)
        raiz.addLayout(fila2)

        # --- tarjetas ---
        tarjetas = QHBoxLayout()
        self.t_paquetes = Tarjeta("Paquetes", TEXTO)
        self.t_direccion = Tarjeta("Con dirección", VERDE)
        self.t_ubicacion = Tarjeta("Con ubicación", AZUL)
        self.t_poligonos = Tarjeta("Polígonos", AMARILLO)
        for t in self._tarjetas():
            tarjetas.addWidget(t)
        raiz.addLayout(tarjetas)

        self.registro = QPlainTextEdit()
        self.registro.setReadOnly(True)
        self.registro.setStyleSheet(
            f"QPlainTextEdit {{ background: #16181d; color: {SUAVE};"
            f" border: 1px solid {BORDE}; border-radius: 7px;"
            f" font-family: Consolas, monospace; font-size: 10px; padding: 6px; }}")
        raiz.addWidget(self.registro, 1)

        pie = QHBoxLayout()
        self.b_carpeta = boton_chico("Abrir carpeta de reportes")
        self.b_carpeta.clicked.connect(self.al_carpeta)
        pie.addWidget(self.b_carpeta)
        pie.addStretch()
        raiz.addLayout(pie)

    def _tarjetas(self):
        return (self.t_paquetes, self.t_direccion, self.t_ubicacion, self.t_poligonos)

    def _hilo(self):
        self.puente = Puente()
        self.ordenes = Ordenes()
        self.hilo = QThread()
        self.trabajador = Trabajador(self.puente)
        self.trabajador.moveToThread(self.hilo)
        self.ordenes.extraer.connect(self.trabajador.extraer)
        self.puente.mensaje.connect(self.escribir)
        self.puente.extraido.connect(self.al_extraido)
        self.puente.fallo.connect(self.al_fallar)
        self.hilo.start()

    def escribir(self, t):
        self.registro.appendPlainText(f"[{datetime.now():%H:%M:%S}] {t}")
        barra = self.registro.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _ocupado(self, si):
        self.trabajando = si
        self.b_extraer.setEnabled(not si)
        self.b_detener.setEnabled(si)
        self.b_cargar.setEnabled(not si)
        self.b_poligonos.setEnabled(not si and bool(self.filas))

    def _contar(self):
        f = self.filas
        self.t_paquetes.poner(len(f))
        self.t_direccion.poner(sum(1 for x in f if x.get("RESULTADO", "").startswith("ok")))
        self.t_ubicacion.poner(sum(1 for x in f if x.get("LATITUD")))

    # --- acciones ---
    def al_extraer(self):
        ids = [x for x in self.ids.toPlainText().split() if x.strip()]
        if not ids:
            QMessageBox.warning(self, "Faltan IDs", "Pega al menos un ID de paquete.")
            return
        for t in self._tarjetas():
            t.poner("-")
        self._ocupado(True)
        self.paso.setText("Extrayendo… Si Chrome pide iniciar sesión en Mercado Libre, "
                          "hazlo ahí; el programa sigue solo.")
        self.ordenes.extraer.emit(ids)

    def al_detener(self):
        self.b_detener.setEnabled(False)
        self.trabajador.detener.set()

    def al_extraido(self, filas):
        self.filas = filas
        self._ocupado(False)
        self._contar()
        ruta = nucleo.guardar_csv(filas)
        self.escribir(f"Reporte guardado: {os.path.basename(ruta)[:-4]} (.xlsx para Excel y .csv)")
        self.paso.setText("Paso 2 · Elige cómo agrupar y presiona ARMAR POLÍGONOS.")
        sin = sum(1 for x in filas if not x.get("RESULTADO", "").startswith("ok"))
        if sin:
            self.escribir(f"{sin} paquetes sin datos (no encontrados, de otro SC o "
                          "sin respuesta): quedan en el reporte marcados.")

    def al_cargar(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Reporte de direcciones", nucleo.CARPETA, "CSV (*.csv)")
        if not ruta:
            return
        try:
            self.filas = nucleo.leer_csv(ruta)
        except Exception as e:
            QMessageBox.warning(self, "No se pudo leer", str(e))
            return
        self._contar()
        self.t_poligonos.poner("-")
        self.b_poligonos.setEnabled(True)
        self.escribir(f"Cargado: {os.path.basename(ruta)} ({len(self.filas)} paquetes)")
        self.paso.setText("Paso 2 · Elige cómo agrupar y presiona ARMAR POLÍGONOS.")

    def al_armar(self):
        if not self.filas:
            return
        modo = "cp" if self.r_cp.isChecked() else "cantidad"
        tope = self.por_poligono.value()
        if modo == "cantidad" and tope < 1:
            QMessageBox.warning(self, "Revisa el máximo",
                                "Por cantidad, el máximo de paquetes por polígono debe ser 1 o más.")
            return
        solo = [c for c in self.solo_cp.text().replace(";", ",").split(",") if c.strip()]
        resumen = nucleo.armar_poligonos(self.filas, modo, tope, solo)
        zonas = {k: v for k, v in resumen.items()
                 if k not in ("SIN DATOS", "SIN UBICACION", "FUERA (CP)")}
        self.t_poligonos.poner(len(zonas))

        desc = (f"{tope} paquetes por polígono" if modo == "cantidad" else
                "por código postal" + (f", máximo {tope}" if tope else ""))
        if solo:
            desc += f" · CP {', '.join(solo)}"
        csv_ruta = nucleo.guardar_csv(self.filas)
        mapa = nucleo.guardar_mapa(self.filas, f"Polígonos · {desc}")
        self.escribir(f"Polígonos ({desc}): {len(zonas)}")
        for k in sorted(resumen):
            self.escribir(f"   {k:<16} {resumen[k]}")
        self.escribir(f"Reporte: {os.path.basename(csv_ruta)[:-4]} (.xlsx y .csv) · Mapa: {os.path.basename(mapa)}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(mapa))
        self.paso.setText(f"Listo · {len(zonas)} polígonos. Puedes armarlos otra vez "
                          "con otras opciones.")

    def al_carpeta(self):
        os.makedirs(nucleo.CARPETA, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(nucleo.CARPETA))

    def al_fallar(self, mensaje):
        self._ocupado(False)
        self.paso.setText("Hubo un problema")
        self.escribir(f"ERROR: {mensaje}")
        QMessageBox.warning(self, "No se pudo", mensaje)

    def closeEvent(self, evento):
        if self.trabajando:
            r = QMessageBox.question(self, "Sigue trabajando",
                                     "Hay una extracción en curso. ¿Detenerla y cerrar?")
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
