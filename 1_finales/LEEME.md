# Los programas que se usan

Estos siete son los definitivos. Todo lo demás en el repo es el camino que
llevó hasta ellos.

| Programa | Qué hace | Tiempo |
|---|---|---|
| **`DriversMeli.exe`** | Padrón de conductores: ID, nombre, CURP, estatus | ~15 s |
| **`PrefacturasMeli.exe`** | Prefactura con el ID del conductor en cada línea | ~1 min |
| **`RutasMeli.exe`** | Rutas diarias para el control, con calendarios | ~30 s |
| **`CasosPNR.exe`** | Reclamos PNR del período: driver, paquete, monto | ~10 s |
| **`CapacidadMeli.exe`** | Pedidos de vehículos por estación y tipo | ~1 s/día |
| **`MapasMeli.exe`** | Paradas de cada ruta con sus coordenadas | ~1 min/día |
| **`Extractor_RTS.exe`** | Último estatus de cada paquete RTS, escrito en la hoja | ~2 s/paquete |

## `DriversMeli.exe` — el padrón

```
Estatus [ Todos ▾]   ☑ Registrados entre [01/09/2026] [17/09/2026]
```

**Estatus**: Todos, Activos o Bloqueados. Se filtra en la propia API, así
que pedir solo los bloqueados tarda menos que traerlos todos.

**Fechas**: viene puesto el **mes en curso**, del día 1 a hoy. Desmarca la
casilla para descargar el padrón completo sin filtrar.

Salida: `drivers_meli_AAAAMMDD_HHMMSS.txt` y `.csv`

```
ID | Nombre | CURP | Estatus
```

Cuatro columnas: quién es y si está activo o bloqueado.

## `CasosPNR.exe` — los reclamos

Los reclamos PNR (paquetes no recibidos) de la Bandeja de soporte, que la
web muestra de 30 en 30 y ya no deja exportar.

```
Periodo [ agosto 2026 · Q2 (16 al 31) ▾]
Genera el CSV con las 23 columnas de la plataforma
```

**Solo hay que elegir el período** —viene puesto el que está en curso— y
presionar extraer. No hay opciones: el programa hace siempre lo mismo.

Salida: **un archivo**, con el nombre, las columnas, el orden y el
formato del CSV que la plataforma dejaba descargar antes de quitar el
botón:

```
LOGISTICS_PNR - 202608Q2_<sello>.csv
```

```
ID DEL DRIVER | NOMBRE DEL DRIVER |
ID DEL CASO | FECHA DEL CASO | TIPO DE PNR | ESTADO |
PERIODO DE FACTURACION | FECHA PEDIDO DE REVISION | PEDIDO DE REVISION |
FECHA DE CIERRE DE CASO | REP - ASISTENTE | COMENTARIO DE CIERRE |
Nº DE PREFACTURA | ID DE ENVIO | PRODUCTOS | VALOR DE LA COMPRA |
REP TRANSPORTADORA | ID DE TRANSPORTADORA | TRANSPORTADORA |
ESTACION DE ORIGEN | RUTA | ID DEL CONDUCTOR | FECHA DE ENTREGA |
ID DE RECLAMO | FECHA DEL RECLAMO
```

Las dos primeras no venían en el original: se agregaron porque son las
que se usan para cruzar con el padrón y para leer de un vistazo. De la
tercera en adelante, todo calca al CSV de la plataforma —separador coma,
sin BOM y fechas en ISO (`2026-08-19T18:13:41`)—.

Tarda **unos 80 segundos** con 450 casos. La mayor parte se va abriendo
la ficha de cada uno, porque la mitad de las columnas solo están ahí.

### Cuánto se llena

Medido contra 448 casos reales, **23 de las 25**:

```
ID DEL DRIVER              448/448     NOMBRE DEL DRIVER          444/448
```


```
ID DEL CASO                448/448     REP - ASISTENTE             82/448
FECHA DEL CASO             448/448     COMENTARIO DE CIERRE         0/448
TIPO DE PNR                448/448     Nº DE PREFACTURA            56/448
ESTADO                     448/448     REP TRANSPORTADORA          82/448
PERIODO DE FACTURACION     448/448     ID DE TRANSPORTADORA       448/448
FECHA PEDIDO DE REVISION    82/448     TRANSPORTADORA             448/448
PEDIDO DE REVISION          82/448     ESTACION DE ORIGEN         448/448
FECHA DE CIERRE DE CASO    448/448     RUTA                       448/448
ID DE ENVIO                448/448     ID DEL CONDUCTOR           448/448
PRODUCTOS                  448/448     FECHA DE ENTREGA           448/448
VALOR DE LA COMPRA         448/448     ID DE RECLAMO              448/448
                                       FECHA DEL RECLAMO            0/448
```

Las de revisión salen 82 porque solo 82 casos tuvieron una. Las dos en
cero —*Comentario de cierre* y *Fecha del reclamo*— **también venían
vacías en el CSV original**: la primera en todas sus filas, la segunda en
todas menos dos de 2024.

### De dónde sale cada cosa

El listado da doce columnas de un tirón:

```
POST /logistics/case-center/api/feed/search-feed-cases-dec
```

El resto vive en la ficha de cada caso, que **no pide datos a ninguna
API**: la página `/case-center/cases/<id>` viene armada desde el servidor
con un objeto `caseDetail` dentro del HTML. El programa lo recorta
contando llaves, y lo hace **dentro del navegador**: cada página pesa
cerca de 1 MB, y traer 450 enteras a Python agotaría la memoria de
Chrome. Cruzan unos 7 KB por caso.

Las fechas de cierre y revisión salen del historial (`events`), y el
texto de la revisión de las notas (`notes`).

### Los casos sin conductor

Algunos reclamos llegan con el nombre en blanco: Mercado Libre todavía no
asignó conductor. El programa los deja vacíos en vez de inventarlos, y al
terminar dice cuántos son. Aun así traen su **ID de conductor**, que es
lo que cruza con el padrón de `DriversMeli.exe` sin depender del nombre
—que se repite entre personas distintas—.

### Por qué son 12 vueltas

La API acepta **30 casos por página y ni uno más** — con 50 responde 400.
Así que 450 casos son 15 consultas para el listado, en unos 6 segundos.

## `MapasMeli.exe` — las paradas en el mapa

Cada ruta del monitoreo tiene su ficha, con un mapa de las paradas
numeradas. **El mapa no se puede descargar**: lo dibuja Leaflet en el
navegador. Lo que sí están son los datos con los que lo dibuja.

Se ejecuta con el día como argumento, o sin nada para tomar el de ayer:

```
MapasMeli.exe 2026-09-09
```

Salida: **dos archivos**.

El CSV, una fila por parada:

```
FECHA | CEDIS | RUTA | ID RUTA | DRIVER | SECUENCIA | DIRECCION |
LATITUD | LONGITUD | ESTADO | PAQUETES | ENVIOS | SACAS |
TIPO DOMICILIO | PRECISION GEO | FUERA DE RANGO | ID PARADA
```

Y el KML, que se abre en Google Earth o se sube a
[google.com/mymaps](https://google.com/mymaps): un pin por parada,
numerado en orden de visita, agrupado por ruta y coloreado por estado
—verde exitosa, rojo fallida, gris pendiente—.

Medido sobre un día real: **14.238 paradas de 168 rutas en 55 segundos**,
las 14.238 con coordenadas.

### Las columnas que salen a medias

`RUTA` y `SECUENCIA` se llenan en unas 9.800 de las 14.238 filas. No es
un hueco del programa: son **52 rutas ya cerradas**, y Mercado Libre deja
de publicar el nombre y el orden de visita cuando la ruta termina. Se
nota al comparar sus estados —98% de paradas exitosas, contra 92% en las
demás—.

En el KML esas paradas van al final de su ruta, con la dirección como
nombre en vez de un número inventado.

### Por qué no agota la memoria de Chrome

Cada ficha pesa **2,7 MB**. Traer las 168 enteras a Python son 450 MB y
Chrome muere —ya pasó con el extractor de rutas—. El programa recorta el
bloque de paradas **dentro del navegador** y solo cruzan unos 280 KB por
ficha. Además recarga en blanco cada 60 fichas para que suelte lo
acumulado.

## `PrefacturasMeli.exe` — facturación con ID

**No hay que saberse el número.** Al abrir Chrome, el programa consulta el
listado y llena tres desplegables:

```
Año [ 2026 ▼]   Mes [ Julio ▼]   Q [ Q2 ▼]

#6595499  ·  7,151,826.56 MXN  ·  Por pagar
```

Arranca en el **año en curso**, su **último mes con prefactura** y la
**última quincena**, así que normalmente solo hay que presionar
*Extraer TODO*.

Solo lista las **Regular · Last Mile** — por eso no aparecen en cada línea:
son el filtro fijo. Las Complementarias y las de Line Haul quedan fuera.
En el listado real, el mismo período `202607Q2` tiene las tres.

El CSV que descarga el panel trae el **nombre** del conductor pero no su
ID, y hay 25 nombres repetidos en el padrón — dos personas distintas
llamadas igual. Este programa cruza por **número de ruta**, no por texto:

```
prefactura (ruta 147326006)
   ↓ el reporte de operación dice quién hizo esa ruta
ID usuario 4965727
```

### Si la descarga automática falla

A veces la página no ofrece el enlace, o el diálogo cambia. Cuando pasa, el
programa **no se queda a medias**: avisa y ofrece la salida manual.

```
Cargar un CSV descargado a mano
```

Descargas el CSV desde Chrome como siempre (*Descargar → CSV*), presionas
ese botón y eliges el archivo. El resto es idéntico: lee sus fechas, pide
el reporte del rango correcto e inyecta los IDs.

Tu archivo no se toca — el resultado se guarda aparte.

Si eliges un archivo que no es una prefactura, lo detecta y lo dice, en vez
de escribir un resultado sin sentido.

Salida: **un solo archivo**, `JulioQ2.csv`. El nombre es el período, sin
el año. Si ya existe uno igual no lo pisa: escribe `JulioQ2 (2).csv`.

```
ID prefactura | Periodo | Estado | ID ruta | ID usuario | Nombre |
Placa | Concepto | Tipo | Fecha inicio | Fecha fin | Cantidad | Costo | Total
```

Las tres primeras columnas se repiten en cada fila a propósito: así el
archivo **se identifica solo** aunque lo renombren o lo bajen dos veces.

### Los totales, al final del mismo archivo

Cierra con los totales que declara MELI, en el mismo formato que traía la
prefactura original —etiqueta a la izquierda, monto a la derecha— más una
línea que comprueba contra la suma del detalle:

```
Total servicios:                                    +  6266264.00
Total adicionales:                                   +  235653.00
Total penalidades:                                    -  63461.52
Total prefactura:                                   +  6438455.48
Suma del detalle:                                      6438455.48
CUADRA                                                       0.00
```

Sin esto, el sistema puede sumar mal 2,600 filas y nadie se entera. Si los
números no coinciden, la última línea dice `NO CUADRA` y la diferencia.

Para quedarse solo con el detalle, basta filtrar por la columna `Tipo`
(`service`, `additional`, `penalty`): las filas de totales la traen vacía.

### El signo de las penalidades

Las penalidades salen **en negativo**, tal como MELI las declara en
`item_type.operation`. Así la columna Total se suma directo y da el total
de la prefactura, sin tener que decidir qué resta.

Antes venían en positivo y la diferencia era de $168,350 según cómo se
interpretara.

## `RutasMeli.exe` — el control diario

Ventana con dos calendarios. Viene puesto en **ayer**, y hay atajos para
7 y 15 días.

```
Desde [ 16/08/2026 📅]  Hasta [ 16/08/2026 📅]  [Ayer] [7 días] [15 días]
```

Los calendarios abren al hacer clic en cualquier parte del campo, y en su
barra se elige mes y año.

### Hasta cuándo hay datos

Mercado Libre **no guarda el reporte indefinidamente**. Pedir fechas
demasiado viejas devuelve un archivo vacío, y antes eso se descubría a
mitad de la extracción.

Ahora hay un botón que lo averigua:

```
Hasta cuando hay datos en Mercado Libre
```

Busca hacia atrás por bisección —unas 12 consultas, no cientos— y te dice
desde qué fecha se puede extraer. Ofrece ponerla directo en *Desde*.

Además, **antes de cada extracción** comprueba que el rango tenga datos:

- Si no hay nada, avisa y ofrece buscar el límite, sin gastar minutos.
- Si el rango está a medias (el final tiene datos pero el inicio no),
  avisa que el reporte saldrá incompleto y continúa.

Salida: `rutas_<desde>_a_<hasta>_<sello>.txt` y `.csv`

```
FECHA | CEDIS_MELI | ID_USUARIO | DRIVER | Vehiculo | Placas |
Tipo_de_servicio | ZONA_DE_RUTA | RUTA | ID_Ruta | SPR | ENTREGADOS |
FALLIDOS | KM | NO_VISITADOS | PROD_HORA | PERFORMANCE
```

**17 columnas, todas con dato.** `PERFORMANCE` sale como `96,84%`.

### Las rutas de Service Partner no llevan nombre

Comprobado con datos reales: de 48 Service Partner, **ninguna** trae
nombre de ruta, mientras las RD y SDD lo traen todas. Así que el programa
no consulta sus fichas —sería tiempo perdido— ni las cuenta como
faltantes:

```
Con nombre de ruta  113/113
  (48 de Service Partner no llevan nombre)
```

Si el número real no cuadra, entonces sí avisa y sugiere un rango más
corto.

`ExtraerRutas.exe` es la misma extracción en consola, por si la prefieres.

Dos cosas que sí deduce: el `Tipo_de_servicio` sale del vehículo (Media
Milla SP → Service Partner; MLP → RD; con sufijo SDD → SDD), y el nombre
de la `RUTA` (C1_AM1) se lee del HTML de cada ficha, en lotes paralelos.

---

## `Extractor_RTS.exe` — el estatus de los RTS

```
Paquetes con FECHA desde [01/10/2026 ▾]
[ ACTUALIZAR ESTATUS ]  [ DETENER ]
```

Lee la hoja **CONSOLIDADO_RTS** (ids en la columna C), abre la ficha de
cada paquete en el panel y escribe el primer estado del *Historial del
paquete* —el más reciente— en **H** y su fecha en **I**
(`21/09/2026 14:20`).

**Qué filas toca**: solo las de FECHA (columna A) desde el día elegido, y
de esas, las que no tienen estatus o tienen uno que todavía puede cambiar.
Las que dicen **Entregado, Dañado o Devuelto** ya no se consultan ni se
tocan (cuenta el inicio del texto: *Entregado - Place* también). Así la
primera corrida llena todo y las siguientes solo refrescan lo vivo.

**Cualquiera lo puede correr, en cualquier PC con Windows y Chrome**: no
pide cuenta de Google. Escribe a través de una Web App de Apps Script que
corre como el dueño de la hoja, y la URL y la clave van dentro del `.exe`.
Lo único que pide es iniciar sesión en Mercado Libre la primera vez, en la
ventana de Chrome que abre.

**El SC importa**: el panel solo muestra los paquetes del SC elegido en
"Estás viendo". Los de otro SC salen como *no se vieron* y su fila no se
toca.

**El `.exe` no está en el repo**: lleva la clave de la Web App y el repo es
público. Se reparte aparte. Para compilarlo hace falta
`webapp_sheets.json` junto al código (`{"url": ".../exec", "token": "..."}`):

```
pyinstaller --onefile --windowed --name Extractor_RTS ^
    --collect-all selenium --add-data "webapp_sheets.json;." estatus_app.py
```

La Web App está en `apps_script/estatus_paquetes.gs`, con la instalación
explicada arriba del archivo. La hoja se elige en `SHEET_ID`, al inicio de
`extraer_estatus_paquetes.py`.

## Requisitos

Windows 10/11 de 64 bits, Google Chrome, e internet la primera vez (para
que Selenium Manager baje el chromedriver). **No hace falta Python.**

Windows mostrará "Windows protegió tu PC" porque los ejecutables no están
firmados: *Más información* → *Ejecutar de todas formas*.

Los archivos que generan contienen **CURP y nombres** — datos
personales. Trátalos en consecuencia.
