# Sistema MD · Lo que falta mejorar

Estado a 2026-09-25, tras la adaptación a Linux/Docker y el banco de tortura (`tortura/`).
Qué **no** está hecho o **no** está probado. Nada de esto impide usar el programa: donde
falta algo, el paquete lo marca con `[PENDIENTE: …]` o rechaza el archivo explicando la causa.

## Resumen por prioridad

| Prioridad | Tema | Situación actual |
|---|---|---|
| 🔴 Alta | DWG reales | Nunca probado con un DWG verdadero: falta ODA File Converter |
| 🔴 Alta | Interfaz en Windows tras los cambios | Solo probada en Linux; en Windows falta abrirla y usarla |
| 🔴 Alta | Pruebas automáticas en GitHub | No hay CI: nadie ejecuta las 444 pruebas en cada cambio |
| 🟠 Media | Excel: canales sin extraer | Comentarios, filas/columnas ocultas, gráficos, tablas dinámicas |
| 🟠 Media | PDF: orden de lectura y tablas | Columnas en orden de pintado; tablas sin bordes salen como texto |
| 🟠 Media | OCR | Candidato sin verificar; se equivoca en símbolos («I» → «|») |
| 🟠 Media | PowerPoint: SmartArt y gráficos | Solo se marcan como pendientes visuales |
| 🟡 Baja | Word: revisiones y cuadros de texto | No se distinguen texto insertado/eliminado ni cuadros flotantes |
| 🟡 Baja | CAD: geometría no medida | SPLINE, ELLIPSE y HATCH no suman longitud ni área |
| 🟡 Baja | XLSB real de Excel | Solo probado con un XLSB fabricado a mano |
| 🟡 Baja | Plan maestro | `estado-implementacion`: 0 de 62 tareas aceptadas |

---

## 🔴 1. DWG (AutoCAD) — lo más importante

**Qué pasa hoy:** el programa **no lee DWG por sí mismo**. Necesita *ODA File Converter*
(gratuito pero propietario, con licencia propia) que convierte DWG → DXF; luego ezdxf mide.
En el entorno de pruebas no había ODA, así que:

- Un DWG falso se rechaza bien («Falta ODA File Converter»).
- La ruta real DWG → ODA → DXF (incluido `xvfb-run` en Linux) **solo tiene pruebas simuladas**.
- **Nunca se convirtió un DWG verdadero.**

**Qué hacer:**
1. Instalar ODA File Converter en Windows (<https://www.opendesign.com/guestfiles/oda_file_converter>).
   El programa lo busca en `C:\Program Files\ODA\ODAFileConverter*\` o en la variable `SISTEMA_MD_ODA`.
2. Probar con 3–5 planos reales (versiones AutoCAD 2013, 2018, 2024) y comparar contra el CAD:
   textos, bloques, capas y longitudes.
3. En Docker: dejar el `.deb` Linux de ODA en `vendor/` y reconstruir la imagen.
4. Evaluar **LibreDWG** (`dwg2dxf`, libre GPL) como alternativa cuando no haya ODA; su
   cobertura de versiones es menor y habría que medirla con los mismos planos.

**CAD en general (también DXF):**
- SPLINE, ELLIPSE y HATCH se cuentan pero no suman longitud/área (el metrado queda corto).
- Las xrefs faltantes se nombran, pero no se cargan.
- Las unidades (`$INSUNITS`) se declaran; no se validan contra el dibujo.
- Viewports de paper space: no se relacionan con el model space.

## 🔴 2. Interfaz de Windows sin probar tras los cambios

Todos los cambios se probaron en Linux (con pantalla virtual). La interfaz Tk de Windows **no se
ha abierto** con esta versión. Las partes de Windows (abrir con la aplicación asociada, «Abrir con…»,
«Mostrar en carpeta», Credential Manager, runas fab1–fab5) se probaron solo con simulaciones.

**Qué hacer:** tras `py -3.12 .\preparar_equipo.py --instalar`, abrir `SISTEMA_MD.bat` y recorrer:
convertir un Excel, un Word y un PDF; «Abrir resultado»; «Mostrar en carpeta»; «Abrir con…»;
exportar MD. Anotar cualquier error (la app lo guarda en el diagnóstico).

## 🔴 3. Pruebas automáticas en GitHub (CI)

El repositorio no tiene GitHub Actions. Las 444 pruebas pasan en Linux y en la imagen Docker,
pero solo porque se ejecutaron a mano. Falta un flujo que, en cada cambio:

- ejecute `./entrypoint.sh pruebas` en Ubuntu (con LibreOffice y Tesseract), y
- ejecute la suite en `windows-latest` con Python 3.12 (así se probaría lo de la sección 2).

## 🟠 4. Excel: canales que aún no se extraen

Se extraen: valores, fórmulas con su caché (`formulas.csv`), combinadas, hojas ocultas marcadas,
tablas nativas con letra de columna, imágenes de `xl/media/`. **No se extraen:**

- Comentarios y notas de celda.
- Filas y columnas ocultas (se publican sin marcar que estaban ocultas).
- Gráficos (datos y títulos), tablas dinámicas, validaciones de datos, formato condicional.
- Fecha base 1900/1904 no se declara; las fórmulas **no se recalculan** (se usa la caché).
- Macros de XLSM: se ignoran (correcto por seguridad), pero no se inventaría que existen.

## 🟠 5. PDF

- El texto sale en **orden de pintado**. En páginas a 2–3 columnas se lee bien porque el PDF de
  prueba se pintó en orden; un PDF real con otro orden mezclaría columnas.
- Las tablas sin bordes salen como líneas de texto, no como tabla Markdown.
- Ligaduras (`ﬁ`, `ﬂ`) se conservan como carácter especial: una búsqueda de «fi» no las encuentra.
  Falta decidir si normalizarlas (NFKC) en una copia de búsqueda.
- Candidato natural: **Docling** (ya tiene adaptador, falta prepararlo y validarlo).

## 🟠 6. OCR (imágenes y PDF escaneado)

- Funciona con Tesseract (español + inglés) y se publica **como candidato sin verificar**.
- En la prueba confundió «tipo I» con «tipo |». Números, símbolos y tablas necesitan revisión.
- No reconstruye tablas ni columnas de una imagen; no hay puntuación de confianza por palabra.
- En Windows hay que instalar Tesseract aparte (<https://github.com/UB-Mannheim/tesseract/wiki>),
  con los idiomas `spa` y `eng`.

## 🟠 7. PowerPoint

- SmartArt real y gráficos: no se extrae su texto/datos; la diapositiva queda
  `[PENDIENTE: … contenido visual]`. (El banco de pruebas simula SmartArt con grupos.)
- Imágenes: se conservan pero no se describen (fase visual pendiente).
- Animaciones, audio y video: no se inventarían.

## 🟡 8. Word

- **Revisiones:** el texto eliminado (`w:del`) se omite y el insertado se publica como vigente,
  sin marcar que era una revisión pendiente.
- **Cuadros de texto flotantes:** su texto puede salir pegado al párrafo donde están anclados;
  no está verificado con documentos reales.
- Celdas combinadas: el valor se **repite** en cada celda de la combinación (no hay `colspan`).
- Gráficos y SmartArt de Word: no se extraen.

## 🟡 9. Formatos antiguos (XLS, DOC, PPT) y XLSB

- Pasan por **LibreOffice** sobre una copia. La conversión no es neutra: en el `.doc` de prueba
  LibreOffice renumeró las notas (`[^2]`, `[^3]`). El paquete lo declara en sus advertencias.
- En Windows hay que tener LibreOffice instalado; si no, el archivo se rechaza con esa causa.
- **XLSB:** no existe un escritor libre; se probó con un XLSB fabricado a mano. Falta probar un
  XLSB guardado por Excel real.
- OLE que no es Word/Excel/PowerPoint (Outlook `.msg`, Visio `.vsd`): sin adaptador.

## 🟡 10. Otros

- **Plan maestro:** `estado-implementacion` sigue en 0 de 62 tareas aceptadas: el registro de
  aceptación no se ha actualizado con evidencia; es trabajo de revisión, no de código.
- **Puente fab1–fab5** (Antigravity por usuarios Windows): solo Windows. En Linux falla
  explícitamente; portarlo exigiría usuarios, permisos y un worker nuevos.
- **Motores locales:** MarkItDown listo (Windows y Linux). Docling con adaptador, sin preparar.
  Marker y MinerU pendientes.
- **Validador de tortura:** 24 avisos estructurales (títulos `## Página/unidad N` seguidos del
  `# título` del documento). No pierden contenido, pero la jerarquía de títulos no es ideal.
- **Docker:** construida y probada (444 pruebas, conversión real de XLS y OCR). Imagen de ~2,3 GB;
  se puede adelgazar con `--build-arg WITH_LIBREOFFICE=0` si no se usan formatos antiguos.

---

## Siguiente paso recomendado

1. Instalar ODA File Converter y convertir 3–5 DWG reales (sección 1).
2. Abrir la interfaz en Windows y recorrer el flujo básico (sección 2).
3. Añadir GitHub Actions para Linux + Windows (sección 3).
