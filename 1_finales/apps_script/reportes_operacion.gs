/**
 * Web App de Reporte_Operacion: recibe el reporte "General - Last Mile"
 * de Reportes de operacion (envios.adminml.com/carriers/reports) y lo
 * acomoda en el Google Sheets donde vive este script.
 *
 *   POST {token, accion: 'cargar', reiniciar, desde, hasta,
 *         encabezados: [...], filas: [[...], ...]}
 *        -> Extraccion: lo de esta corrida (reiniciar = primera parte)
 *           Consolidado: una fila por Fecha + Id de la ruta, en orden
 *             cronologico. Si la fila ya estaba y cambio, se actualiza; si
 *             es igual, se deja.
 *           Registro: una linea por parte recibida, con lo que hizo
 *   GET  ?token=  -> {ok: true} (para probar la conexion)
 *
 * Instalacion (una sola vez, la hace clasp; ver LEEME.md):
 *   1. Cambia TOKEN por una clave larga (la misma va en
 *      webapp_reportes.json, junto al extractor).
 *   2. Ejecuta instalar() una vez desde el editor y acepta los permisos.
 *   3. Implementar > Aplicacion web: Ejecutar como "Yo", acceso
 *      "Cualquier usuario". La URL /exec va en webapp_reportes.json.
 *
 * "Cualquier usuario" significa que la URL responde sin iniciar sesion;
 * lo que la protege es el TOKEN. No compartas ni la URL ni la clave.
 */

const TOKEN = 'CAMBIA-ESTA-CLAVE';
const ZONA = 'America/Mexico_City';

const HOJA_EXTRACCION = 'Extraccion';
const HOJA_CONSOLIDADO = 'Consolidado';
const HOJA_REGISTRO = 'Registro';

// La llave de cada fila: el reporte trae una fila por ruta y por dia
const COL_FECHA = 0;   // A: Fecha
const COL_RUTA = 1;    // B: Id de la ruta
// Columnas que van como texto: ids y placa (si no, Sheets les quita ceros
// o los muestra en notacion cientifica)
const COLS_TEXTO = [1, 2, 5];

const AMARILLO = '#ffe600';


function instalar() {
  // Solo para que el editor pida los permisos y deje las pestañas listas
  const libro = SpreadsheetApp.getActiveSpreadsheet();
  PropertiesService.getScriptProperties().setProperty('SHEET_ID', libro.getId());
  [HOJA_EXTRACCION, HOJA_CONSOLIDADO, HOJA_REGISTRO].forEach(n => hoja_(libro, n));
  const reg = libro.getSheetByName(HOJA_REGISTRO);
  if (reg.getLastRow() === 0) {
    reg.appendRow(['Cuando', 'Desde', 'Hasta', 'Filas recibidas', 'Nuevas',
                   'Actualizadas', 'Sin cambio', 'Total consolidado']);
    estiloEncabezado_(reg, 8);
  }
  const hoja1 = libro.getSheetByName('Hoja 1') || libro.getSheetByName('Sheet1');
  if (hoja1 && libro.getSheets().length > 1 && hoja1.getLastRow() === 0) {
    libro.deleteSheet(hoja1);
  }
}


function doGet(e) {
  if ((e.parameter || {}).token !== TOKEN) return json_({ok: false, error: 'token'});
  return json_({ok: true});
}


function doPost(e) {
  let p;
  try {
    p = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ok: false, error: 'el cuerpo no es JSON'});
  }
  if (p.token !== TOKEN) return json_({ok: false, error: 'token'});
  if (p.accion !== 'cargar') return json_({ok: false, error: 'accion desconocida'});

  // Dos extracciones a la vez no deben pisarse el Consolidado
  const candado = LockService.getScriptLock();
  candado.waitLock(120000);
  try {
    return json_(cargar_(p));
  } catch (err) {
    return json_({ok: false, error: String(err)});
  } finally {
    candado.releaseLock();
  }
}


function cargar_(p) {
  const libro = libro_();
  if (libro.getSpreadsheetTimeZone() !== ZONA) libro.setSpreadsheetTimeZone(ZONA);
  const encabezados = p.encabezados || [];
  const filas = (p.filas || []).map(f => normalizar_(f, encabezados.length));
  const ancho = encabezados.length;
  if (!ancho) throw new Error('sin encabezados');

  // --- Extraccion: lo de esta corrida ---
  const ext = hoja_(libro, HOJA_EXTRACCION);
  if (p.reiniciar) {
    ext.clear();
    ext.getRange(1, 1, 1, ancho).setValues([encabezados]);
    estiloEncabezado_(ext, ancho);
  }
  if (filas.length) {
    const ini = Math.max(ext.getLastRow(), 1) + 1;
    escribir_(ext, ini, filas, ancho);
  }

  // --- Consolidado: una fila por Fecha + Ruta ---
  const con = hoja_(libro, HOJA_CONSOLIDADO);
  let actuales = [];
  if (con.getLastRow() > 1) {
    actuales = con.getRange(2, 1, con.getLastRow() - 1, ancho).getValues()
      .map(f => normalizar_(f, ancho));
  }
  const indice = {};
  actuales.forEach((f, i) => { indice[llave_(f)] = i; });

  let nuevas = 0, actualizadas = 0, iguales = 0;
  filas.forEach(f => {
    const k = llave_(f);
    if (k in indice) {
      const i = indice[k];
      if (JSON.stringify(actuales[i]) === JSON.stringify(f)) {
        iguales++;
      } else {
        actuales[i] = f;
        actualizadas++;
      }
    } else {
      indice[k] = actuales.length;
      actuales.push(f);
      nuevas++;
    }
  });

  // Orden cronologico; dentro del dia, por ruta
  actuales.sort((a, b) => llave_(a) < llave_(b) ? -1 : llave_(a) > llave_(b) ? 1 : 0);

  if (nuevas || actualizadas || con.getLastRow() < 1) {
    con.clear();
    con.getRange(1, 1, 1, ancho).setValues([encabezados]);
    estiloEncabezado_(con, ancho);
    if (actuales.length) escribir_(con, 2, actuales, ancho);
  }

  // --- Registro ---
  const reg = hoja_(libro, HOJA_REGISTRO);
  if (reg.getLastRow() === 0) {
    reg.appendRow(['Cuando', 'Desde', 'Hasta', 'Filas recibidas', 'Nuevas',
                   'Actualizadas', 'Sin cambio', 'Total consolidado']);
    estiloEncabezado_(reg, 8);
  }
  reg.appendRow([Utilities.formatDate(new Date(), ZONA, 'dd/MM/yyyy HH:mm'),
                 p.desde || '', p.hasta || '', filas.length, nuevas,
                 actualizadas, iguales, actuales.length]);

  return {ok: true, recibidas: filas.length, nuevas: nuevas,
          actualizadas: actualizadas, iguales: iguales, total: actuales.length};
}


// ------------------------------------------------------------- auxiliares

function libro_() {
  const id = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  if (!id) throw new Error('Falta ejecutar instalar() una vez desde el editor');
  return SpreadsheetApp.openById(id);
}


function hoja_(libro, nombre) {
  return libro.getSheetByName(nombre) || libro.insertSheet(nombre);
}


/** Fecha como 'yyyy-MM-dd', el resto como texto o numero, con el ancho fijo. */
function normalizar_(fila, ancho) {
  const f = [];
  for (let i = 0; i < ancho; i++) {
    let v = fila[i];
    if (v === null || v === undefined) v = '';
    if (i === COL_FECHA) {
      v = v instanceof Date ? Utilities.formatDate(v, ZONA, 'yyyy-MM-dd') : String(v).slice(0, 10);
    } else if (COLS_TEXTO.indexOf(i) >= 0) {
      v = String(v);
    } else if (typeof v === 'string' && v !== '' && !isNaN(Number(v))) {
      v = Number(v);
    }
    f.push(v);
  }
  return f;
}


function llave_(f) {
  return f[COL_FECHA] + '|' + String(f[COL_RUTA]).padStart(15, '0');
}


/** Escribe las filas con la fecha como fecha de verdad y los ids como texto. */
function escribir_(hoja, filaIni, filas, ancho) {
  // La fecha va como texto 'aaaa-mm-dd' en una celda con formato de
  // fecha: Sheets la toma como fecha de ese dia, sin pasar por zonas
  // horarias (con new Date() se corria de dia)
  const valores = filas.map(f => f.slice());
  const rango = hoja.getRange(filaIni, 1, valores.length, ancho);
  COLS_TEXTO.forEach(c => hoja.getRange(filaIni, c + 1, valores.length, 1).setNumberFormat('@'));
  hoja.getRange(filaIni, COL_FECHA + 1, valores.length, 1).setNumberFormat('dd/mm/yyyy');
  rango.setValues(valores);
}


function estiloEncabezado_(hoja, ancho) {
  hoja.getRange(1, 1, 1, ancho).setFontWeight('bold').setBackground(AMARILLO);
  hoja.setFrozenRows(1);
}


function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o))
    .setMimeType(ContentService.MimeType.JSON);
}
