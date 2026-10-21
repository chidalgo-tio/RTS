/************************************************************
 *            EXTRACTOR PNR → REPORTE_TEMP → CONSOLIDADO
 ************************************************************
 *
 *  Va en el mismo proyecto del Sheets de PNR, junto al script
 *  del CONSOLIDADO (usa CONFIG y actualizarConsolidado()).
 *
 *  ACTUALIZAR > ⬇️ Extraer PNRs
 *       ↓  ventanita: elegir el periodo (quincena)
 *  casospnr://extraer?periodo=202610Q2
 *       ↓  Windows abre CasosPNR.exe, que abre Chrome (Meli)
 *  CasosPNR.exe extrae los casos y llama a esta Web App:
 *
 *  POST {token, accion: 'cargar_pnr', filas: [[23 valores], ...]}
 *       → REPORTE_TEMP A:W desde la fila 2 (X:AB son fórmulas:
 *         no se tocan)
 *       → actualizarConsolidado()  (lo mismo que el menú
 *         "Actualizar Consolidado de Temporal")
 *       → {ok, nuevos, actualizados, sinCambios, sinId, fueraDeAnio}
 *
 *  La Web App se implementa como: Ejecutar como "Yo", acceso
 *  "Cualquier usuario". La protege TOKEN_PNR (la misma clave va
 *  en webapp_pnr.json, junto a CasosPNR.exe).
 ************************************************************/

const TOKEN_PNR = 'CAMBIA-ESTA-CLAVE';

// REPORTE_TEMP recibe A:W (23 columnas, las del CSV de la plataforma)
const PNR_COLUMNAS = 23;


/************************************************************
 * WEB APP
 ************************************************************/

function doGet(e) {
  if ((e.parameter || {}).token !== TOKEN_PNR) return jsonPnr_({ok: false, error: 'token'});
  return jsonPnr_({ok: true});
}


function doPost(e) {
  let p;
  try {
    p = JSON.parse(e.postData.contents);
  } catch (err) {
    return jsonPnr_({ok: false, error: 'el cuerpo no es JSON'});
  }
  if (p.token !== TOKEN_PNR) return jsonPnr_({ok: false, error: 'token'});
  if (p.accion !== 'cargar_pnr') return jsonPnr_({ok: false, error: 'accion desconocida'});

  // Dos extracciones a la vez no deben pisarse REPORTE_TEMP
  const candado = LockService.getScriptLock();
  candado.waitLock(120000);
  try {
    return jsonPnr_(cargarPnr_(p.filas || []));
  } catch (err) {
    return jsonPnr_({ok: false, error: String(err && err.message || err)});
  } finally {
    candado.releaseLock();
  }
}


function cargarPnr_(filas) {
  const ss = libroPnr_();
  const temp = ss.getSheetByName(CONFIG.TEMP);
  if (!temp) throw new Error('No existe la pestaña "' + CONFIG.TEMP + '".');

  // Cada fila con 23 valores exactos
  const datos = filas
    .map(f => {
      const fila = (f || []).slice(0, PNR_COLUMNAS);
      while (fila.length < PNR_COLUMNAS) fila.push('');
      return fila.map(v => (v === null || v === undefined) ? '' : v);
    })
    .filter(f => f.some(v => String(v).trim() !== ''));

  // Limpiar A:W desde la fila 2 (X:AB no se tocan)
  const ultima = temp.getLastRow();
  if (ultima >= CONFIG.FILA_INICIO_TEMP) {
    temp.getRange(CONFIG.FILA_INICIO_TEMP, 1,
                  ultima - CONFIG.FILA_INICIO_TEMP + 1, PNR_COLUMNAS).clearContent();
  }
  if (!datos.length) {
    return {ok: true, recibidas: 0, nuevos: 0, actualizados: 0, sinCambios: 0};
  }

  const necesarias = CONFIG.FILA_INICIO_TEMP + datos.length - 1;
  if (temp.getMaxRows() < necesarias) {
    temp.insertRowsAfter(temp.getMaxRows(), necesarias - temp.getMaxRows());
  }
  // Los textos se interpretan como si se escribieran a mano: fechas,
  // montos ($349.73) e ids quedan igual que al importar el CSV
  temp.getRange(CONFIG.FILA_INICIO_TEMP, 1, datos.length, PNR_COLUMNAS).setValues(datos);

  // Que las fórmulas de X:AB (BUSCARV a DATA_OPERADORES) calculen
  SpreadsheetApp.flush();

  const r = actualizarConsolidado() || {};
  return Object.assign({ok: true, recibidas: datos.length}, r);
}


function libroPnr_() {
  return SpreadsheetApp.getActiveSpreadsheet() ||
         SpreadsheetApp.openById(OPERADORES_SHEET_ID);
}


/** ss.toast() con pantalla; desde la Web App no hay a quien mostrarlo. */
function avisoPnr_(ss, mensaje, titulo, segundos) {
  try {
    ss.toast(mensaje, titulo, segundos);
  } catch (err) {
    console.log((titulo || '') + ': ' + mensaje);
  }
}


function jsonPnr_(o) {
  return ContentService.createTextOutput(JSON.stringify(o))
    .setMimeType(ContentService.MimeType.JSON);
}


/************************************************************
 * MENÚ: ⬇️ Extraer PNRs
 ************************************************************/

function extraerPnrs() {
  const html = HtmlService.createHtmlOutput(
      PNR_DIALOGO_HTML.replace('__OPCIONES__', opcionesPeriodo_()))
    .setWidth(400).setHeight(330);
  SpreadsheetApp.getUi().showModalDialog(html, 'Extraer PNRs de Mercado Libre');
}


/** Las últimas 8 quincenas, la actual primero. */
function opcionesPeriodo_() {
  const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
                 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  const hoy = new Date();
  let anio = hoy.getFullYear(), mes = hoy.getMonth(), q = hoy.getDate() <= 15 ? 1 : 2;
  const opciones = [];
  for (let i = 0; i < 8; i++) {
    const ultimo = new Date(anio, mes + 1, 0).getDate();
    const valor = anio + String(mes + 1).padStart(2, '0') + 'Q' + q;
    const texto = MESES[mes] + ' ' + anio + ' · Q' + q +
                  (q === 1 ? ' (1 al 15)' : ' (16 al ' + ultimo + ')');
    opciones.push('<option value="' + valor + '">' + texto + '</option>');
    if (q === 2) { q = 1; } else { q = 2; mes--; if (mes < 0) { mes = 11; anio--; } }
  }
  return opciones.join('');
}


/** Se ejecuta una vez desde el editor: pide permisos y oculta REPORTE_TEMP. */
function instalarExtractorPnr() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const temp = ss.getSheetByName(CONFIG.TEMP);
  if (temp && !temp.isSheetHidden()) {
    if (ss.getActiveSheet().getName() === CONFIG.TEMP) {
      ss.setActiveSheet(ss.getSheetByName(CONFIG.CONSOLIDADO));
    }
    temp.hideSheet();
  }
}


const PNR_DIALOGO_HTML = String.raw`
<style>
  body { font-family: Arial, sans-serif; font-size: 13px; color: #333; margin: 4px; }
  select { width: 100%; padding: 6px; font-size: 13px; margin: 6px 0 4px; }
  a.boton { display: block; margin: 14px 0 10px; padding: 11px; text-align: center;
            background: #ffe600; color: #333; font-weight: bold; border-radius: 6px;
            text-decoration: none; }
  .nota { color: #777; font-size: 11px; line-height: 1.45; }
</style>
<div>Periodo de facturación</div>
<select id="periodo">__OPCIONES__</select>
<a class="boton" id="abrir" href="#" target="_blank">Extraer PNRs</a>
<div class="nota">
  Se abre una pestaña nueva y el navegador pregunta si quieres abrir
  <b>CasosPNR</b>: acepta y cierra esa pestaña. El programa abre Chrome con
  Mercado Libre; si pide usuario y contraseña, entra ahí. Al terminar carga
  REPORTE_TEMP y actualiza el CONSOLIDADO solo: recarga esta hoja para verlo.<br><br>
  Solo funciona en computadoras que tengan CasosPNR.exe y lo hayan abierto
  al menos una vez.
</div>
<script>
  const sel = document.getElementById('periodo');
  const a = document.getElementById('abrir');
  function armar() { a.href = 'casospnr://extraer?periodo=' + sel.value; }
  sel.onchange = armar;
  armar();
</script>
`;
