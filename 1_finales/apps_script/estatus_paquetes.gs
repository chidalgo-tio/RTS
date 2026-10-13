/**
 * Web App de Extractor_RTS, y el correo diario con el reporte
 *
 * Hace de puente entre el extractor y las hojas CONSOLIDADO_RTS y
 * DEVOLUCIONES, para que Python no necesite credenciales de Google:
 *
 *   GET  ?token=&hoja=&completas=1   -> las columnas A..I como se ven
 *   POST {token, hoja, columna, finales, cambios:[{fila, valores}]}
 *        -> escribe valores desde esa columna (null = no tocar la celda)
 *
 * Ademas, enviarReporte() manda todos los dias a las 10 am el reporte de
 * los RTS y de las devoluciones que aun no estan en DEVUELTO.
 *
 * Instalacion (una sola vez):
 *   1. En la hoja: Extensiones > Apps Script. Pega este archivo.
 *   2. Cambia TOKEN por una clave larga tuya (la misma va en
 *      webapp_sheets.json, junto al extractor).
 *   3. Ejecuta instalarCorreoDiario() una vez y acepta los permisos
 *      (hojas, correo y activadores). Deja programado el correo.
 *   4. Implementar > Nueva implementacion > Aplicacion web
 *        Ejecutar como:      Yo
 *        Quien tiene acceso: Cualquier usuario
 *      Autoriza y copia la URL que termina en /exec.
 *
 * "Cualquier usuario" significa que la URL responde sin iniciar sesion;
 * lo que la protege es el TOKEN. No compartas ni la URL ni la clave.
 *
 * Si cambias este codigo: Implementar > Gestionar implementaciones >
 * editar > Version: nueva. Si no, la URL sigue sirviendo la version vieja.
 */

// Hoja por defecto. El extractor manda la suya (SHEET_ID arriba en
// extraer_estatus_paquetes.py), que es la que manda; esta la usan el
// correo diario y los pedidos que no la traen
const SHEET_ID = '1hQvyCcWZGOo4rQcinDmWg_kYaOfZTcS3H7IZSBeaE9c';
const HOJA = 'CONSOLIDADO_RTS';
const HOJA_DEV = 'DEVOLUCIONES';
const TOKEN = 'CAMBIA-ESTA-CLAVE';

// A quien llega el reporte de las 10 am (varios: separados por coma)
const DESTINATARIOS = 'chidalgo@bdbcompany.com';
const HORA_CORREO = 10;
const ZONA = 'America/Mexico_City';
// El reporte cubre las filas con FECHA (columna A) desde este dia
const DESDE_REPORTE = new Date(2026, 9, 1);   // 01/10/2026 (mes 0 = enero)

// Formato viejo de los .exe anteriores: estatus en H, fecha en I
const COL_ID = 3;       // C
const COL_ESTATUS = 8;  // H
const COL_FECHA = 9;    // I

/**
 * Ejecutala UNA vez desde el editor (boton Ejecutar) para dar permiso al
 * script de abrir la hoja. Sin eso la Web App falla al primer pedido.
 */
function autorizar() {
  const hoja = abrirHoja_();
  Logger.log('OK: ' + hoja.getName() + ', ' + hoja.getLastRow() + ' filas');
}

/**
 * Ejecutala UNA vez desde el editor: programa enviarReporte() todos los
 * dias a las 10 am (hora de Mexico). Si ya estaba, la reemplaza.
 */
function instalarCorreoDiario() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'enviarReporte')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('enviarReporte')
    .timeBased().everyDays(1).atHour(HORA_CORREO).nearMinute(0)
    .inTimezone(ZONA).create();
  Logger.log('Correo programado: todos los dias a las ' + HORA_CORREO +
             ' h, a ' + DESTINATARIOS);
}

function doGet(e) {
  if (!autorizado_(e.parameter.token)) return responder_({ok: false, error: 'token'});

  const hoja = abrirHoja_(e.parameter.sheet, e.parameter.hoja);
  const ultima = hoja.getLastRow();
  if (ultima < 1) return responder_({ok: true, filas: [], tabla: []});

  // Formato viejo: C, H e I. El id con getValues, como numero completo
  const ids = hoja.getRange(1, COL_ID, ultima, 1).getValues();
  const hi = hoja.getRange(1, COL_ESTATUS, ultima, 2).getDisplayValues();
  const filas = ids.map((id, i) => [String(id[0]), hi[i][0], hi[i][1]]);

  // Con ?completas=1 las columnas A..I como se ven: Python elige que
  // columna es el id, el estatus y la fecha segun la hoja
  const salida = {ok: true, filas: filas};
  if (e.parameter.completas === '1') {
    salida.tabla = hoja.getRange(1, 1, ultima, COL_FECHA).getDisplayValues();
  }
  return responder_(salida);
}

function doPost(e) {
  let datos;
  try {
    datos = JSON.parse(e.postData.contents);
  } catch (err) {
    return responder_({ok: false, error: 'json'});
  }
  if (!autorizado_(datos.token)) return responder_({ok: false, error: 'token'});

  // Dos corridas a la vez no deben pisarse
  const candado = LockService.getScriptLock();
  candado.waitLock(30000);
  try {
    const hoja = abrirHoja_(datos.sheet, datos.hoja);
    const r = datos.columna ? escribirColumnas_(hoja, datos)
                            : escribirViejo_(hoja, datos);
    SpreadsheetApp.flush();
    return responder_({ok: true, escritas: r.escritas, omitidas: r.omitidas});
  } finally {
    candado.releaseLock();
  }
}

/**
 * Formato nuevo: cada cambio trae los valores desde `columna` (el primero
 * es el estatus). null deja la celda como esta, asi una nota escrita a
 * mano o una formula no se tocan.
 *
 * Si se va a escribir el estatus y el que ya hay es final, la fila se
 * salta: otro lo marco mientras corria el extractor.
 */
function escribirColumnas_(hoja, datos) {
  const col = Number(datos.columna);
  const finales = datos.finales || FINALES;
  let escritas = 0, omitidas = 0;

  (datos.cambios || []).forEach(c => {
    const fila = Number(c.fila);
    const valores = c.valores || [];
    if (!fila || fila < 1 || !valores.length) { omitidas++; return; }

    if (valores[0] !== null && valores[0] !== undefined) {
      const actual = hoja.getRange(fila, col).getDisplayValue();
      if (esFinal_(actual, finales)) { omitidas++; return; }
    }

    // Tramos seguidos de valores no nulos: un setValues por tramo
    let i = 0;
    while (i < valores.length) {
      if (valores[i] === null || valores[i] === undefined) { i++; continue; }
      let j = i;
      while (j < valores.length && valores[j] !== null && valores[j] !== undefined) j++;
      const rango = hoja.getRange(fila, col + i, 1, j - i);
      // Como texto: si no, Sheets reinterpreta "01/09/2026" segun la
      // configuracion regional y puede leerlo como 9 de enero
      rango.setNumberFormat('@');
      rango.setValues([valores.slice(i, j).map(v => String(v))]);
      i = j;
    }
    escritas++;
  });
  return {escritas: escritas, omitidas: omitidas};
}

/** Lo que mandan los .exe anteriores: estatus y fecha en H e I. */
function escribirViejo_(hoja, datos) {
  let escritas = 0, omitidas = 0;
  (datos.cambios || []).forEach(c => {
    const fila = Number(c.fila);
    if (!fila || fila < 1) { omitidas++; return; }

    const rango = hoja.getRange(fila, COL_ESTATUS, 1, 2);
    const actual = rango.getDisplayValues()[0];
    if (datos.modo === 'actualizar') {
      if (esFinal_(actual[0], FINALES)) { omitidas++; return; }
    } else if (actual[0].trim() || actual[1].trim()) {
      omitidas++; return;
    }
    rango.setNumberFormat('@');
    rango.setValues([[String(c.estatus || ''), String(c.fecha || '')]]);
    escritas++;
  });
  return {escritas: escritas, omitidas: omitidas};
}

// Estatus que ya no cambian en CONSOLIDADO_RTS. Se compara el inicio del
// texto sin acentos, asi "Entregado - Place" tambien cuenta. En
// DEVOLUCIONES el extractor manda su propia lista: solo 'devuelto'
const FINALES = ['entregado', 'danado', 'devuelto'];

function normal_(texto) {
  return String(texto || '').trim().toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '');
}

function esFinal_(estatus, finales) {
  const t = normal_(estatus);
  return (finales || FINALES).some(f => t.indexOf(f) === 0);
}

// ------------------------------------------------------------ Reporte

/**
 * El correo de las 10 am: todos los RTS desde DESDE_REPORTE, y las
 * devoluciones de ese periodo que aun no estan en DEVUELTO, con sus
 * alertas de 24 y 48 horas. Lo dispara el activador de
 * instalarCorreoDiario(); tambien se puede ejecutar a mano para probar.
 */
function enviarReporte() {
  const libro = SpreadsheetApp.openById(SHEET_ID);
  const hoy = Utilities.formatDate(new Date(), ZONA, 'dd/MM/yyyy');

  // CONSOLIDADO_RTS: A fecha, C id, D usuario, E motivo, H estatus, I fecha
  const rts = filasDesde_(libro.getSheetByName(HOJA))
    .map(f => [f[0], f[2], f[3], f[4], f[7] || 'Sin consultar', f[8]]);

  // DEVOLUCIONES: A fecha, B motivo, C zona, D id, E estatus, F fecha, G obs
  const orden = {'MAYOR A 48 HRS': 0, 'ALERTA 24 HRS': 1};
  const dev = filasDesde_(libro.getSheetByName(HOJA_DEV))
    .filter(f => f[3] && !esFinal_(f[4], ['devuelto']))
    .map(f => [f[0], f[1], f[2], f[3], f[4] || 'Sin consultar', f[5], f[6]])
    .sort((a, b) => (orden[a[6]] ?? 2) - (orden[b[6]] ?? 2));

  const a48 = dev.filter(f => f[6] === 'MAYOR A 48 HRS').length;
  const a24 = dev.filter(f => f[6] === 'ALERTA 24 HRS').length;

  const cabRts = ['FECHA', 'ID PQTE', 'USUARIO', 'MOTIVO', 'ESTATUS MELI', 'FECHA ESTATUS'];
  const cabDev = ['FECHA', 'MOTIVO', 'ZONA', 'ID', 'ESTATUS MELI', 'FECHA ESTATUS', 'OBSERVACIONES'];

  const html =
    '<div style="font-family:Arial,sans-serif;font-size:13px;color:#222">' +
    '<h2 style="margin:0 0 4px">Reporte RTS y devoluciones · ' + hoy + '</h2>' +
    '<p style="margin:0 0 14px;color:#555">Filas con fecha desde ' +
    Utilities.formatDate(DESDE_REPORTE, ZONA, 'dd/MM/yyyy') + '. ' +
    'Estatus tomado de Mercado Libre por Extractor_RTS.</p>' +
    '<h3 style="margin:16px 0 4px">Devoluciones sin DEVUELTO: ' + dev.length + '</h3>' +
    '<p style="margin:0 0 6px"><b style="color:#c62828">Mayor a 48 hrs: ' + a48 +
    '</b> &nbsp;·&nbsp; <b style="color:#ef6c00">Alerta 24 hrs: ' + a24 + '</b></p>' +
    tablaHtml_(cabDev, dev, 6) +
    '<h3 style="margin:22px 0 4px">CONSOLIDADO_RTS: ' + rts.length + '</h3>' +
    resumenHtml_(rts, 4) +
    tablaHtml_(cabRts, rts, -1) +
    '</div>';

  // Los mismos datos en CSV, por si el correo se recorta
  const adjuntos = [
    Utilities.newBlob(csv_(cabDev, dev), 'text/csv', 'devoluciones_pendientes.csv'),
    Utilities.newBlob(csv_(cabRts, rts), 'text/csv', 'consolidado_rts.csv'),
  ];

  MailApp.sendEmail({
    to: DESTINATARIOS,
    subject: 'Reporte RTS y devoluciones · ' + hoy +
             (a48 ? ' · ' + a48 + ' mayores a 48 hrs' : ''),
    htmlBody: html,
    attachments: adjuntos,
  });
}

/** Filas con FECHA (columna A) desde DESDE_REPORTE, como se ven. */
function filasDesde_(hoja) {
  if (!hoja) return [];
  const ultima = hoja.getLastRow();
  if (ultima < 2) return [];
  return hoja.getRange(2, 1, ultima - 1, COL_FECHA).getDisplayValues()
    .filter(f => {
      const d = fechaDe_(f[0]);
      return d && d >= DESDE_REPORTE;
    });
}

/** '07-10-26' o '07/10/2026' -> Date. null si no se reconoce. */
function fechaDe_(texto) {
  const m = String(texto || '').trim().match(/^(\d{1,2})[-\/](\d{1,2})[-\/](\d{2}|\d{4})\b/);
  if (!m) return null;
  const anio = m[3].length === 4 ? Number(m[3]) : 2000 + Number(m[3]);
  return new Date(anio, Number(m[2]) - 1, Number(m[1]));
}

function resumenHtml_(filas, col) {
  const cuenta = {};
  filas.forEach(f => { cuenta[f[col]] = (cuenta[f[col]] || 0) + 1; });
  const items = Object.keys(cuenta).sort((a, b) => cuenta[b] - cuenta[a])
    .map(k => esc_(k) + ': <b>' + cuenta[k] + '</b>');
  return '<p style="margin:0 0 6px">' + items.join(' &nbsp;·&nbsp; ') + '</p>';
}

/** colAlerta: la columna que pinta la fila de rojo/naranja (-1 = ninguna). */
function tablaHtml_(cabecera, filas, colAlerta) {
  if (!filas.length) return '<p style="color:#555">Sin filas.</p>';
  const th = 'style="background:#222;color:#fff;padding:4px 8px;text-align:left"';
  const td = 'padding:3px 8px;border-bottom:1px solid #ddd';
  let h = '<table style="border-collapse:collapse;font-size:12px">' +
          '<tr>' + cabecera.map(c => '<th ' + th + '>' + c + '</th>').join('') + '</tr>';
  filas.forEach(f => {
    let fondo = '';
    if (colAlerta >= 0 && f[colAlerta] === 'MAYOR A 48 HRS') fondo = 'background:#ffebee;';
    else if (colAlerta >= 0 && f[colAlerta] === 'ALERTA 24 HRS') fondo = 'background:#fff3e0;';
    h += '<tr>' + f.map(v => '<td style="' + fondo + td + '">' + esc_(v) + '</td>').join('') + '</tr>';
  });
  return h + '</table>';
}

function csv_(cabecera, filas) {
  const linea = f => f.map(v => '"' + String(v || '').replace(/"/g, '""') + '"').join(',');
  // BOM para que Excel lea bien los acentos
  return '﻿' + [linea(cabecera)].concat(filas.map(linea)).join('\r\n');
}

function esc_(v) {
  return String(v || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ------------------------------------------------------------ Comunes

function abrirHoja_(sheetId, nombre) {
  sheetId = sheetId || SHEET_ID;
  nombre = nombre || HOJA;
  const hoja = SpreadsheetApp.openById(sheetId).getSheetByName(nombre);
  if (!hoja) throw new Error('No existe la pestaña ' + nombre);
  return hoja;
}

function autorizado_(token) {
  return TOKEN !== 'CAMBIA-ESTA-CLAVE' && token === TOKEN;
}

function responder_(objeto) {
  return ContentService.createTextOutput(JSON.stringify(objeto))
    .setMimeType(ContentService.MimeType.JSON);
}
