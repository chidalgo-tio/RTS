/**
 * Web App para extraer_estatus_paquetes.py
 *
 * Hace de puente entre el extractor y la hoja CONSOLIDADO_RTS, para que
 * Python no necesite credenciales de Google:
 *
 *   GET  ?token=...          -> las columnas C, H e I de cada fila
 *   POST {token, cambios}    -> escribe estatus (H) y fecha (I)
 *
 * Instalacion (una sola vez):
 *   1. En la hoja: Extensiones > Apps Script. Pega este archivo.
 *   2. Cambia TOKEN por una clave larga tuya (la misma va en
 *      webapp_sheets.json, junto al extractor).
 *   3. Ejecuta la funcion autorizar() una vez y acepta los permisos.
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

// Hoja por defecto. El extractor manda la suya (SHEET_ID y HOJA arriba
// en extraer_estatus_paquetes.py), que es la que manda; esta solo se usa
// si un pedido no la trae, como al ejecutar autorizar() desde el editor
const SHEET_ID = '1hQvyCcWZGOo4rQcinDmWg_kYaOfZTcS3H7IZSBeaE9c';
const HOJA = 'CONSOLIDADO_RTS';
const TOKEN = 'CAMBIA-ESTA-CLAVE';

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

function doGet(e) {
  if (!autorizado_(e.parameter.token)) return responder_({ok: false, error: 'token'});

  const hoja = abrirHoja_(e.parameter.sheet, e.parameter.hoja);
  const ultima = hoja.getLastRow();
  if (ultima < 1) return responder_({ok: true, filas: []});

  // El id con getValues: llega como numero completo, no con el formato de
  // la celda. H e I como se ven, para saber si tienen algo.
  const ids = hoja.getRange(1, COL_ID, ultima, 1).getValues();
  const hi = hoja.getRange(1, COL_ESTATUS, ultima, 2).getDisplayValues();

  const filas = ids.map((id, i) => [String(id[0]), hi[i][0], hi[i][1]]);

  // Con ?completas=1 tambien las columnas A..I como se ven, para filtrar
  // por otras columnas (la fecha, el SC) del lado de Python
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
    let escritas = 0, omitidas = 0;

    (datos.cambios || []).forEach(c => {
      const fila = Number(c.fila);
      if (!fila || fila < 1) { omitidas++; return; }

      const rango = hoja.getRange(fila, COL_ESTATUS, 1, 2);
      const actual = rango.getDisplayValues()[0];
      if (datos.modo === 'actualizar') {
        // Se reescribe todo salvo un estatus final: esos ya no cambian
        if (esFinal_(actual[0])) { omitidas++; return; }
      } else if (actual[0].trim() || actual[1].trim()) {
        // Modo normal: si alguien lleno la fila mientras corria, se respeta
        omitidas++; return;
      }

      // Como texto: si no, Sheets reinterpreta "01/09/2026" segun la
      // configuracion regional y puede leerlo como 9 de enero
      rango.setNumberFormat('@');
      rango.setValues([[String(c.estatus || ''), String(c.fecha || '')]]);
      escritas++;
    });

    SpreadsheetApp.flush();
    return responder_({ok: true, escritas: escritas, omitidas: omitidas});
  } finally {
    candado.releaseLock();
  }
}

// Estatus que ya no cambian: en modo 'actualizar' no se tocan. Se compara
// el inicio del texto sin acentos, asi "Entregado - Place" tambien cuenta.
// La misma lista vive en FINALES de extraer_estatus_paquetes.py
const FINALES = ['entregado', 'danado', 'devuelto'];

function esFinal_(estatus) {
  const t = String(estatus || '').trim().toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  return FINALES.some(f => t.indexOf(f) === 0);
}

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
