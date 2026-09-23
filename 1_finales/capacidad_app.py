# -*- coding: utf-8 -*-
"""
Capacidad - Interfaz grafica

Eliges un rango de fechas en dos calendarios y presionas extraer. Por
defecto viene puesto el dia de ayer, que es el caso mas comun.
"""

import os
import re
import sys
import traceback
from datetime import datetime

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QUrl, QDate
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QMessageBox, QDateEdit, QCalendarWidget,
)

import extraer_capacidad as nucleo
from estilo_ventana import (
    FONDO, PANEL, BORDE, TEXTO, SUAVE, AMARILLO, AZUL, VERDE, ROJO,
    FUENTE, FUENTE_NUM, barra_titulo_oscura, _carpeta_base, Tarjeta,
    boton, boton_chico,
)


class Puente(QObject):
    mensaje = Signal(str)
    terminado = Signal(str, object)
    fallo = Signal(str)


class Ordenes(QObject):
    """Las ordenes viajan por señal: si se llamara al metodo directamente,
    correria en el hilo de la ventana y la congelaria."""
    abrir = Signal()
    extraer = Signal(str, str)
    historial = Signal()
    cerrar = Signal()


class Trabajador(QObject):
    def __init__(self, puente):
        super().__init__()
        self.p = puente
        self.driver = None

    def log(self, t):
        self.p.mensaje.emit(t)

    @Slot()
    def abrir_navegador(self):
        try:
            self.log("Abriendo Chrome...")
            self.driver = nucleo.crear_driver()
            d = nucleo.ayer()
            self.driver.get(f"{nucleo.URL}?date_lteq={d}{nucleo.HORA_CORTE}"
                            f"&date_gteq={d}{nucleo.HORA_CORTE}")
            self.log("Chrome abierto. Inicia sesion si te lo pide.")
            self.p.terminado.emit("abierto", None)
        except Exception as e:
            self.p.fallo.emit(self._explicar(e))

    @Slot(str, str)
    def extraer(self, desde, hasta):
        try:
            if not self.driver:
                self.p.fallo.emit("Primero abre Chrome e inicia sesion.")
                return

            self.log(f"Consultando del {desde} al {hasta}...")
            registros, vacios = nucleo.extraer_rango(
                self.driver, desde, hasta, avisar=self.log)

            if not registros:
                self.log("No hubo pedidos. Buscando hasta cuando hay datos...")
                limite = nucleo.primer_dia_con_datos(
                    self.driver, avisar=self.log)
                self.p.terminado.emit("vacio", limite)
                return

            self.log("Guardando...")
            det, res = nucleo.guardar(registros, desde, hasta)
            self.log(f"Listo: {os.path.basename(det)}")
            self.p.terminado.emit("extraido", (registros, vacios, det, res))

        except Exception as e:
            traceback.print_exc()
            self.p.fallo.emit(self._explicar(e))

    @Slot()
    def buscar_historial(self):
        try:
            if not self.driver:
                self.p.fallo.emit("Primero abre Chrome e inicia sesion.")
                return
            self.log("Buscando el dia mas antiguo con datos...")
            limite = nucleo.primer_dia_con_datos(self.driver, avisar=self.log)
            self.p.terminado.emit("historial", limite)
        except Exception as e:
            self.p.fallo.emit(self._explicar(e))

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
        if "Pedidos de vehiculos" in t:
            return t
        # Se compara contra el codigo que devolvio la API, no contra el
        # texto suelto: buscar "401" en cualquier parte del mensaje
        # etiquetaba como sesion caduca errores que no lo eran, y la
        # persona volvia a entrar una y otra vez sin arreglar nada.
        # 401 y 403 no son lo mismo y mandan a la persona a sitios
        # distintos. Cuando se trataban igual, una compañera sin permiso
        # reinicio sesion cuatro veces seguidas sin que sirviera: el
        # mensaje decia "caduco" cuando lo que faltaba era acceso.
        if re.search(r"respondio 401\b", t):
            return ("La sesion caduco.\n"
                    "Vuelve a iniciar sesion en la ventana de Chrome.")
        if re.search(r"respondio 403\b", t):
            return ("Tu cuenta no tiene acceso a Pedidos de vehiculos.\n\n"
                    "No es la sesion: entraste bien, pero Mercado Libre no\n"
                    "deja ver esta seccion con tu usuario. Compruebalo tu\n"
                    "misma abriendo esta direccion en Chrome:\n\n"
                    "envios.adminml.com/logistics/travel-requests/last-mile\n\n"
                    "Si ahi tampoco entras, pide el permiso a quien\n"
                    "administre las cuentas de Mercado Libre.")
        if re.search(r"respondio 422\b", t):
            return ("Mercado Libre rechazo la consulta (422).\n\n"
                    "Suele ser una version vieja del programa: MELI bajo el\n"
                    "limite de resultados por pagina en septiembre de 2026.\n"
                    "Pide la version actualizada de CapacidadMeli.exe.")
        return t


class Ventana(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Capacidad - Mercado Libre")
        self.resize(660, 470)
        self.setMinimumSize(600, 430)
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

        titulo = QLabel("CAPACIDAD  ·  PEDIDOS DE VEHICULOS")
        titulo.setStyleSheet(
            f"color: {TEXTO}; font-family: {FUENTE_NUM}; font-size: 16px;"
            f" font-weight: 900; letter-spacing: 1px;")
        raiz.addWidget(titulo)

        self.paso = QLabel("Paso 1 de 2  ·  Abre Mercado Libre e inicia sesion")
        self.paso.setStyleSheet(
            f"color: {AMARILLO}; font-family: {FUENTE}; font-size: 11px;"
            f" font-weight: 600;")
        raiz.addWidget(self.paso)

        # --- rango de fechas ---
        fila = QHBoxLayout()
        fila.setSpacing(7)
        est_eti = f"color: {SUAVE}; font-family: {FUENTE}; font-size: 11px;"
        estilo = (
            f"QDateEdit {{ background: {PANEL}; color: {TEXTO};"
            f" border: 1px solid {BORDE}; border-radius: 6px; padding: 0 6px;"
            f" font-family: {FUENTE}; font-size: 11px; }}"
            f"QDateEdit::drop-down {{ border: none; width: 20px; }}"
            f"QCalendarWidget QWidget {{ background: {PANEL};"
            f" color: {TEXTO}; }}"
            f"QCalendarWidget QAbstractItemView {{ background: {PANEL};"
            f" color: {TEXTO}; selection-background-color: {AZUL};"
            f" selection-color: #ffffff; outline: none; }}"
            f"QCalendarWidget QToolButton {{ color: {TEXTO};"
            f" background: {PANEL}; border: none; }}"
            f"QCalendarWidget QMenu {{ background: {PANEL}; color: {TEXTO}; }}"
            f"QCalendarWidget QSpinBox {{ background: {PANEL};"
            f" color: {TEXTO}; }}")

        ayer = QDate.currentDate().addDays(-1)
        e1 = QLabel("Desde")
        e1.setStyleSheet(est_eti)
        fila.addWidget(e1)
        self.f_desde = self._fecha(ayer, estilo)
        fila.addWidget(self.f_desde)

        e2 = QLabel("Hasta")
        e2.setStyleSheet(est_eti)
        fila.addWidget(e2)
        self.f_hasta = self._fecha(ayer, estilo)
        fila.addWidget(self.f_hasta)

        for texto, dias in (("Ayer", 1), ("7 dias", 7), ("15 dias", 15),
                            ("30 dias", 30)):
            b = boton_chico(texto)
            b.clicked.connect(lambda _=False, n=dias: self._rango(n))
            fila.addWidget(b)
        fila.addStretch()
        raiz.addLayout(fila)

        # --- botones ---
        botones = QHBoxLayout()
        botones.setSpacing(7)
        self.b_abrir = boton("1 · ABRIR MERCADO LIBRE", AMARILLO)
        self.b_abrir.clicked.connect(self.al_abrir)
        self.b_extraer = boton("2 · EXTRAER CAPACIDAD", AZUL)
        self.b_extraer.clicked.connect(self.al_extraer)
        self.b_extraer.setEnabled(False)
        botones.addWidget(self.b_abrir)
        botones.addWidget(self.b_extraer)
        raiz.addLayout(botones)

        # --- tarjetas ---
        tarjetas = QHBoxLayout()
        tarjetas.setSpacing(7)
        self.t_total = Tarjeta("Pedidos", TEXTO)
        self.t_aceptados = Tarjeta("Aceptados", VERDE)
        self.t_responder = Tarjeta("Para responder", AMARILLO)
        self.t_perdidos = Tarjeta("Rech/Exp/Canc", ROJO)
        for t in (self.t_total, self.t_aceptados, self.t_responder,
                  self.t_perdidos):
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
        self.b_historial = boton_chico("Hasta cuando hay datos")
        self.b_historial.clicked.connect(self.al_historial)
        self.b_historial.setEnabled(False)
        pie.addWidget(self.b_historial)
        self.b_carpeta = boton_chico("Abrir carpeta")
        self.b_carpeta.clicked.connect(self.al_carpeta)
        self.b_carpeta.setEnabled(False)
        pie.addWidget(self.b_carpeta)
        pie.addStretch()
        raiz.addLayout(pie)

    def _fecha(self, valor, estilo):
        """Un campo de fecha cuyo calendario abre al hacer clic encima.

        Por defecto QDateEdit solo abre el calendario con la flechita, y
        hay que atinarle. Aqui abre al pinchar en cualquier parte.
        """
        campo = QDateEdit(valor)
        campo.setCalendarPopup(True)
        campo.setDisplayFormat("dd/MM/yyyy")
        campo.setFixedHeight(30)
        campo.setMinimumWidth(115)
        campo.setStyleSheet(estilo)
        campo.setCursor(Qt.PointingHandCursor)
        campo.setToolTip("Clic para elegir la fecha en el calendario")

        cal = campo.calendarWidget()
        if cal:
            cal.setGridVisible(False)
            cal.setNavigationBarVisible(True)
            cal.setFirstDayOfWeek(Qt.Monday)
            try:
                cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
            except Exception:
                pass

        original = campo.mousePressEvent

        def al_pinchar(evento, c=campo, o=original):
            o(evento)
            if evento.button() == Qt.LeftButton:
                # Qt no expone el popup directamente: se dispara con F4
                from PySide6.QtGui import QKeyEvent
                from PySide6.QtCore import QEvent
                tecla = QKeyEvent(QEvent.KeyPress, Qt.Key_F4, Qt.NoModifier)
                QApplication.sendEvent(c, tecla)

        campo.mousePressEvent = al_pinchar
        return campo

    def _rango(self, dias):
        """Un rango que termina ayer y abarca 'dias' hacia atras."""
        fin = QDate.currentDate().addDays(-1)
        self.f_hasta.setDate(fin)
        self.f_desde.setDate(fin.addDays(-(dias - 1)))

    def _hilo(self):
        self.puente = Puente()
        self.ordenes = Ordenes()
        self.hilo = QThread()
        self.trabajador = Trabajador(self.puente)
        self.trabajador.moveToThread(self.hilo)

        self.ordenes.abrir.connect(self.trabajador.abrir_navegador)
        self.ordenes.extraer.connect(self.trabajador.extraer)
        self.ordenes.historial.connect(self.trabajador.buscar_historial)
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
        d, h = self.f_desde.date(), self.f_hasta.date()
        if d > h:
            QMessageBox.warning(self, "Rango invalido",
                                "La fecha inicial es posterior a la final.")
            return
        dias = d.daysTo(h) + 1
        if dias > 45:
            r = QMessageBox.question(
                self, "Rango largo",
                f"Son {dias} dias, y se consulta uno por uno.\n"
                f"Tardara cerca de {dias // 2} segundos.\n\n¿Continuar?")
            if r != QMessageBox.Yes:
                return
        self.b_extraer.setEnabled(False)
        self.b_historial.setEnabled(False)
        self.paso.setText(f"Extrayendo {dias} dia(s)...")
        self.ordenes.extraer.emit(d.toString("yyyy-MM-dd"),
                                  h.toString("yyyy-MM-dd"))

    def al_historial(self):
        self.b_historial.setEnabled(False)
        self.b_extraer.setEnabled(False)
        self.paso.setText("Buscando el limite del historial...")
        self.ordenes.historial.emit()

    def al_carpeta(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(_carpeta_base()))

    def al_terminar(self, etapa, datos):
        if etapa == "abierto":
            self.b_extraer.setEnabled(True)
            self.b_historial.setEnabled(True)
            self.paso.setText(
                "Paso 2 de 2  ·  Inicia sesion y presiona EXTRAER CAPACIDAD")
            return

        if etapa in ("historial", "vacio"):
            self.b_extraer.setEnabled(True)
            self.b_historial.setEnabled(True)
            limite = datos
            if not limite:
                self.paso.setText("No se encontraron datos")
                QMessageBox.warning(
                    self, "Sin datos",
                    "No se encontraron pedidos en ningun dia reciente.\n"
                    "Revisa que la sesion siga activa.")
                return
            f = QDate.fromString(limite, "yyyy-MM-dd")
            dias = f.daysTo(QDate.currentDate())
            self.paso.setText(
                f"Hay datos desde el {f.toString('dd/MM/yyyy')}")
            cuerpo = (f"Mercado Libre guarda el historial desde el "
                      f"{f.toString('dd/MM/yyyy')}.\n"
                      f"Son {dias} dias hacia atras.\n\n"
                      f"¿Pongo esa fecha en 'Desde'?")
            if etapa == "vacio":
                cuerpo = ("No hubo pedidos en el rango que pediste.\n\n" + cuerpo)
            if QMessageBox.question(self, "Historial", cuerpo) == QMessageBox.Yes:
                self.f_desde.setDate(f)
            return

        registros, vacios, det, res = datos
        self.archivo = det
        cuenta = nucleo.contar_estados(registros)

        aceptados = cuenta.get("Aceptado", 0)
        responder = cuenta.get("Para responder", 0)
        perdidos = (cuenta.get("Rechazado", 0) + cuenta.get("Expirado", 0)
                    + cuenta.get("Cancelado por MELI", 0))
        self.t_total.poner(len(registros))
        self.t_aceptados.poner(aceptados)
        self.t_responder.poner(responder)
        self.t_perdidos.poner(perdidos)

        self.b_extraer.setEnabled(True)
        self.b_historial.setEnabled(True)
        self.b_carpeta.setEnabled(True)
        self.paso.setText(f"Listo  ·  {len(registros)} pedidos")

        detalle = "\n".join(f"   {e}: {n}" for e, n in cuenta.items())
        aviso = ""
        if vacios:
            muestra = ", ".join(vacios[:6])
            if len(vacios) > 6:
                muestra += f" y {len(vacios) - 6} mas"
            aviso = f"\n\nSin datos en {len(vacios)} dia(s): {muestra}"
        QMessageBox.information(
            self, "Listo",
            f"{len(registros)} pedidos de vehiculos\n\n{detalle}{aviso}\n\n"
            f"Detalle:  {os.path.basename(det)}\n"
            f"Resumen:  {os.path.basename(res)}")

    def al_fallar(self, mensaje):
        self.b_abrir.setEnabled(True)
        hay_driver = self.trabajador.driver is not None
        self.b_extraer.setEnabled(hay_driver)
        self.b_historial.setEnabled(hay_driver)
        self.paso.setText("Hubo un problema")
        self.escribir(f"ERROR: {mensaje}")
        QMessageBox.warning(self, "No se pudo", mensaje)

    def closeEvent(self, evento):
        self.ordenes.cerrar.emit()
        self.hilo.quit()
        self.hilo.wait(3000)
        evento.accept()


def main():
    app = QApplication(sys.argv)
    ventana = Ventana()
    ventana.show()
    barra_titulo_oscura(ventana)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
