# -*- coding: utf-8 -*-
"""
Donde trae la ficha del paquete los datos del comprador y la direccion?

fechapromesa.py los sacaba de window.__PRELOADED_STATE__.shipmentDetail,
pero en octubre de 2026 el HTML de la ficha (1.2 MB) ya no trae
receiver_info, zip_code ni nada del comprador: llegan despues, por
alguna peticion de la pagina.

Esta sonda abre la ficha, espera a que se dibuje y reporta:
  - TODAS las peticiones que hizo la pagina (ids enmascarados),
  - el enlace "Ver en Google Maps" con los digitos tapados, para ver si
    lleva coordenadas o la direccion,
  - las etiquetas de los campos de "Datos del comprador" y "Direccion de
    entrega" y si tienen valor (sin mostrarlo).

Nunca imprime valores: sirve para saber de donde sacar los datos.

Uso:
    python sondear_direccion.py 48239561960
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "1_finales"))
import extraer_estatus_paquetes as base  # noqa: E402

base.PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "1_finales", "chrome_profile")

JS_DOM = r"""
const campos = Array.from(document.querySelectorAll('input, textarea')).map(el => {
  // La etiqueta: el <label> asociado o el texto del contenedor
  let etiqueta = '';
  if (el.id) { const l = document.querySelector('label[for="' + el.id + '"]'); if (l) etiqueta = l.innerText; }
  if (!etiqueta) { const c = el.closest('div'); etiqueta = c && c.parentElement ? c.parentElement.innerText.split('\n')[0] : ''; }
  const v = el.value || '';
  return {etiqueta: etiqueta.trim().slice(0, 40), largo: v.length, tipo: el.tagName,
          nombre: el.name || '', clase: (el.className || '').toString().slice(0, 40)};
});
const enlaces = Array.from(document.querySelectorAll('a')).filter(a =>
  /maps|google/i.test(a.href) || /google maps/i.test(a.innerText)).map(a => ({texto: a.innerText.trim(), href: a.href}));
const recursos = Array.from(performance.getEntriesByType('resource')).map(e => e.name);
const texto = document.body ? document.body.innerText : '';
return {campos: campos, enlaces: enlaces, recursos: recursos,
        secciones: ['Datos del comprador', 'Dirección de entrega', 'Historial del paquete']
          .map(s => s + ': ' + (texto.indexOf(s) >= 0))};
"""


def tapar(texto):
    """Los digitos se ven como 9 y las letras como x: queda la forma."""
    texto = re.sub(r"\d", "9", texto)
    return re.sub(r"(?<=[=/,:])[A-Za-zÁÉÍÓÚáéíóúñÑ%+.\-]{3,}", "xxx", texto)


def main():
    ids = [a for a in sys.argv[1:] if a.isdigit()]
    if not ids:
        print("Pasa uno o mas ids de paquete.")
        return
    driver = base.crear_driver()
    try:
        for pid in ids:
            driver.get(base.URL_PAQUETE + pid)
            print("Si Chrome pide iniciar sesion, hazlo; se sigue solo.")
            for _ in range(150):
                estado, _dato = base.revisar(driver, pid)
                if estado in ("ok", "sin_paquete"):
                    break
                time.sleep(2)
            time.sleep(6)       # lo que carga despues del historial
            r = driver.execute_script(JS_DOM) or {}
            print("=" * 74)
            print(f"{pid}: {r.get('secciones')}")
            print("  campos de la ficha:")
            for c in r.get("campos", []):
                print(f"    {c['etiqueta']:<28} {c['tipo']:<8} con valor: "
                      f"{'si' if c['largo'] else 'no':<3} name={c['nombre']!r}")
            print("  enlaces a mapas:")
            for e in r.get("enlaces", []):
                print(f"    {e['texto']!r}: {tapar(e['href'])[:200]}")
            print("  peticiones de la pagina (sin js/css/imagenes):")
            vistos = set()
            for u in r.get("recursos", []):
                if re.search(r"\.(js|css|png|svg|jpg|woff2?|ico)(\?|$)", u):
                    continue
                u = re.sub(r"\d{6,}", "<num>", u)[:150]
                if u not in vistos:
                    vistos.add(u)
                    print("    ", u)
    finally:
        base.cerrar_driver(driver)


if __name__ == "__main__":
    main()
