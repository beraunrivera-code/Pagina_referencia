# Sistema MD: instalar y comprobar en otra PC

Guía del paquete de prueba. Actualizada el 21 de septiembre de 2026.

**Primero prueba sin API y con documentos sintéticos. No traslades todavía tu biblioteca real.** Este ZIP contiene el programa y sus controles, no un instalador comercial autónomo ni un entorno Python listo para copiar.

## Qué debes reemplazar en los comandos

Los únicos textos que debes sustituir aparecen **EN MAYÚSCULAS Y ENTRE COMILLAS**. Cambia el contenido y conserva las comillas. Las rutas deben corresponder a la PC nueva; no copies el nombre de usuario ni la unidad de la PC anterior.

Ejemplo: en `--salida "CARPETA_DE_EVIDENCIAS_NUEVA"`, reemplaza solo `CARPETA_DE_EVIDENCIAS_NUEVA` por una ruta nueva que hayas elegido. No reemplaces el comando entero.

Todos los comandos de esta guía se ejecutan en **PowerShell**, situado en la carpeta extraída que contiene `SISTEMA_MD.bat`.

## Recorrido recomendado

1. Extraer el ZIP y comprobar Python.
2. Verificar los archivos del paquete.
3. Preparar el núcleo de Sistema MD.
4. Preparar MarkItDown si quieres probar ese motor.
5. Ejecutar el control automático sin IA y revisar la ventana.
6. Elegir las carpetas de trabajo.
7. Configurar API o CLI solo después de que pase la prueba local.
8. Trasladar una copia de la biblioteca, si corresponde, con ambas aplicaciones cerradas.

## 1. Extraer el ZIP completo

- Usa **Extraer todo**. No abras `SISTEMA_MD.bat` desde la vista interna del ZIP.
- Elige una carpeta local donde puedas escribir. Evita para el primer control una unidad de red o una carpeta sincronizada activamente con otra PC.
- No extraigas encima de otra instalación ni sobre una biblioteca existente. Conserva el ZIP recibido como referencia.
- Dentro de la carpeta del programa deben estar `SISTEMA_MD.bat`, `iniciar.py`, `preparar_equipo.py`, `PROBAR_EQUIPO.py`, `MANIFIESTO_PAQUETE.json`, esta guía y la carpeta `app`.

El paquete de programa **no debe contener** documentos reales, claves, sesiones OAuth, `.venv`, `.venv-local`, `.runtime` ni entornos `workers/*/.venv`. Los entornos se crean en el destino. La biblioteca real, si la necesitas, se respalda y traslada por separado.

Abre PowerShell y sitúate en esa carpeta:

```powershell
Set-Location -LiteralPath "CARPETA_DONDE_EXTRASTE_SISTEMA_MD"
```

No hace falta ejecutar el programa como administrador para la prueba ordinaria en una carpeta de tu usuario.

## 2. Comprobar Python 3.12 de 64 bits y Tcl/Tk

El entorno de preparación de este paquete está definido para **Windows x64 y Python 3.12**. No sustituyas por Python 3.13/3.14 ni por una instalación de 32 bits solo porque sea la predeterminada.

Instala Python desde [Python para Windows](https://www.python.org/downloads/windows/), eligiendo la rama 3.12 y el instalador de 64 bits. Incluye Tcl/Tk y el lanzador de Python. No uses el paquete embebible para esta instalación; consulta la [guía oficial de Python 3.12 en Windows](https://docs.python.org/3.12/using/windows.html) si necesitas revisar las opciones.

Comprueba:

```powershell
py -0p
py -3.12 --version
py -3.12 -m tkinter
```

- La primera orden muestra los Python detectados y sus rutas. Identifica el Python **base** 3.12 de esta PC; lo necesitarás para MarkItDown.
- La segunda debe mostrar Python 3.12.x.
- La tercera debe abrir una pequeña ventana de demostración de Tcl/Tk. Ciérrala para continuar.

Si `py` no existe, repara la instalación del lanzador o utiliza la ruta completa del Python base con el operador `&` de PowerShell. Por ejemplo, para inspeccionar el núcleo:

```powershell
& "RUTA_COMPLETA_DEL_PYTHON_312_64_BITS\python.exe" .\preparar_equipo.py
```

No copies el Python ni el runtime de Codex de la otra computadora. Si falta Tcl/Tk, corrige la instalación oficial antes de seguir; instalar un paquete llamado `tkinter` mediante pip no es el procedimiento de esta guía.

## 3. Verificar el paquete antes de instalar

```powershell
py -3.12 .\PROBAR_EQUIPO.py --verificar-paquete
```

Esta opción comprueba los archivos contra `MANIFIESTO_PAQUETE.json`; no instala dependencias ni llama a IA. Revisa el resultado y detente si comunica archivos ausentes o alterados. Vuelve a extraer en una carpeta nueva antes de modificar nada de tu copia anterior.

El manifiesto permite detectar cambios en los archivos enumerados del paquete entregado; no audita archivos añadidos después. No equivale a una firma digital del editor ni demuestra por sí solo que una descarga venga de una fuente confiable. La orden devuelve código 0 si pasa y 2 si requiere revisión.

## 4. Preparar el núcleo

Primero, inspección sin instalar:

```powershell
py -3.12 .\preparar_equipo.py
```

Después, autoriza expresamente la creación del entorno y la descarga de dependencias:

```powershell
py -3.12 .\preparar_equipo.py --instalar
```

El script crea `.venv-local`, descarga desde PyPI las dependencias del núcleo y ejecuta el control de arranque. Solo escribe el recibo de aceptación si ese control termina correctamente. No instala Docling, sus modelos ni los CLI de los proveedores.

**Esta instalación requiere acceso a Internet.** Poder convertir posteriormente sin API no significa que el ZIP permita instalar todo sin conexión.

Si la preparación quedó incompleta, conserva la carpeta y reanuda con el mismo Python y en la misma ubicación:

```powershell
py -3.12 .\preparar_equipo.py --instalar --reanudar
```

`--reanudar` no es una orden de actualización. Se rechaza si el entorno ya fue aceptado, si no tiene un marcador propio válido o si cambió su identidad. No borres marcadores ni recibos para forzar la aceptación.

Comprueba el núcleo preparado:

```powershell
.\SISTEMA_MD.bat --check
```

Busca el resultado global `ok` verdadero. Ese control acredita arranque e importación de dependencias; no certifica extracción, fidelidad, acceso a cuentas ni portabilidad comercial. `blocked_write_attempts` puede informar intentos bloqueados de crear cachés durante el diagnóstico; no significa que se hayan escrito.

Si el lanzador dice que falta `.venv`, no copies la de otra máquina: comprueba que la preparación local haya terminado y exista el recibo `.venv-local/sistema_md_ready.json`.

## 5. Preparar MarkItDown en su entorno separado

Es opcional para la prueba del núcleo, pero necesario para comprobar el motor MarkItDown. Su instalación realizada en la PC de origen **no viaja dentro del ZIP**.

Obtén la ruta del Python base 3.12 de 64 bits con `py -0p`. Primero muestra el plan:

```powershell
py -3.12 .\app\workers\prepare_markitdown.py --python "RUTA_COMPLETA_DEL_PYTHON_312_64_BITS\python.exe"
```

Para instalar:

```powershell
py -3.12 .\app\workers\prepare_markitdown.py --python "RUTA_COMPLETA_DEL_PYTHON_312_64_BITS\python.exe" --install
```

Este script crea `workers/markitdown/.venv`, usa el lock incluido para Windows x64/Python 3.12 y ejecuta una comprobación de dependencias. Descarga paquetes; no convierte documentos ni instala Docling.

Si quedó incompleto, repite **la misma ruta de Python**, sin cambiar el programa de carpeta:

```powershell
py -3.12 .\app\workers\prepare_markitdown.py --python "RUTA_COMPLETA_DEL_PYTHON_312_64_BITS\python.exe" --install --resume
```

Atención a la diferencia: el núcleo usa `--instalar --reanudar`; MarkItDown usa `--install --resume`. Un worker ya aceptado no se sobrescribe. Ante un marcador ajeno, una ruta cambiada o un lock distinto, el script se detiene y conserva los archivos.

Comprueba lo detectado:

```powershell
.\SISTEMA_MD.bat motores-locales
```

Que aparezca un paquete instalado no demuestra que todos sus formatos funcionen. El siguiente control comprueba una conversión concreta.

## 6. Control automático local: sin API ni documentos personales

Usa una **carpeta de evidencias nueva que todavía no exista**. No elijas tu biblioteca ni una carpeta de documentos.

```powershell
.\.venv-local\Scripts\python.exe -B .\PROBAR_EQUIPO.py --salida "CARPETA_DE_EVIDENCIAS_NUEVA"
```

El control crea un TXT sintético, comprueba la conversión nativa y, si MarkItDown está instalado, crea y convierte también un HTML sintético. No configura cuentas ni envía documentos a proveedores.

En la carpeta de evidencias elegida encontrarás `REPORTE_PRUEBA.md` y `REPORTE_PRUEBA.json`. Según las etapas alcanzadas, también estarán `arranque.json`, `integridad_paquete.json` si existe el manifiesto, `fuentes_sinteticas/`, `biblioteca_sintetica/` y `MD/`. Un fallo temprano puede impedir la creación de las evidencias de etapas posteriores.

El código de salida es **0 si el control pasa y 2 si requiere revisión**. Si falta MarkItDown, registra `pendiente_instalacion` y devuelve 2, aunque el TXT nativo haya pasado. Para completar ese control instala el worker y repite en otra carpeta nueva.

Revisa el informe completo:

- Núcleo: el control de arranque debe pasar.
- TXT nativo: debe producir un resultado coherente con su fuente sintética.
- MarkItDown: distingue conversión aprobada de motor ausente o prueba omitida. **Omitido no es aprobado.**
- Errores: conserva el informe; no borres resultados para ocultar un fallo.
- Docling, Marker y MinerU: su ausencia no se convierte en una prueba aprobada de esos motores.

Para repetir, elige otra carpeta nueva de evidencias. No sobrescribas la corrida anterior. Una prueba sintética aprobada tampoco demuestra fidelidad de documentos complejos.

## 7. Revisar la ventana y el resultado

Haz doble clic en `SISTEMA_MD.bat`. La cabecera debe identificar **Sistema MD · GPT CODE** y el modo inicial debe ser **Local (sin envíos)**.

1. En **Trabajo y cola**, agrega una copia de un TXT de prueba y selecciónalo.
2. Elige **Nativo actual** y pulsa **Convertir con motor local**.
3. En **Biblioteca**, abre el resultado. Compara título, párrafos, acentos y orden con la fuente.
4. Comprueba la búsqueda, la lectura y **Mostrar en carpeta**. Verifica también la existencia del MD legible.
5. Si instalaste MarkItDown, añade el HTML sintético del control, elige **MarkItDown · documentos ligeros** y convierte desde la misma ventana.
6. Su resultado debe aparecer en la misma Biblioteca. No debería abrirse otra aplicación de conversión ni una sesión de IA.
7. En **Configurar…**, comprueba que las rutas de Python/modelos se recuperen al volver a abrir el panel. Guardar rutas no instala paquetes ni certifica el motor.

El selector de motor afecta al archivo seleccionado. Los botones **Planificar lote nativo** y **Ejecutar / reanudar nativo** siguen usando el flujo nativo; no ejecutan Docling/MarkItDown en lote por haberlos seleccionado arriba.

Prueba también una ventana pequeña y el escalado de pantalla que utilizas normalmente. Revisa que puedas leer todos los botones, desplazarte y distinguir la pestaña activa. No basta con que el programa no se cierre.

## 8. Elegir biblioteca y carpeta MD

Por defecto:

| Contenido | Ubicación relativa al programa |
|---|---|
| Biblioteca, paquetes y estado de trabajo | `app/datos/` |
| Markdown legibles exportados | `MD/` |
| Entorno del núcleo de esta PC | `.venv-local/` |
| Entorno separado de MarkItDown | `workers/markitdown/.venv/` |

En **IA y conexiones**, despliega **Memoria y reglas de consumo**:

- **Carpeta de datos…**: selecciona una carpeta existente. Se aplica en la próxima apertura; no mueve ni mezcla bibliotecas.
- **Carpeta MD…**: selecciona una carpeta existente para las próximas exportaciones. No mueve ni borra los MD anteriores.

También puedes elegirlas desde PowerShell, con la aplicación cerrada:

```powershell
.\SISTEMA_MD.bat --datos "CARPETA_EXISTENTE_DE_LA_BIBLIOTECA"
.\SISTEMA_MD.bat --exportacion "CARPETA_EXISTENTE_DE_LOS_MD"
.\SISTEMA_MD.bat --check
```

Las rutas internas nuevas se guardan relativas cuando corresponde. Una ruta externa absoluta debe existir en la PC nueva; no se adivina su reemplazo. Si una unidad está desconectada, reconéctala o elige otra ubicación expresamente.

Las variables `SISTEMA_MD_DATA`, `SISTEMA_MD_MARKITDOWN_PYTHON`, `SISTEMA_MD_DOCLING_PYTHON` y `SISTEMA_MD_DOCLING_MODELS`, si las definiste, tienen prioridad sobre sus selecciones guardadas. Una variable heredada puede apuntar a una carpeta de otra máquina. Revísala sin imprimir el entorno completo, que podría contener secretos. No las necesitas para usar las ubicaciones predeterminadas.

## 9. Conectar API: opcional y posterior a la prueba local

La app admite Gemini, DeepSeek, OpenAI, Anthropic y Qwen, con **cinco posiciones de clave por proveedor**. No supone cinco cuotas independientes ni cambia automáticamente de cuenta si una falla.

1. Abre **IA y conexiones** y elige el proveedor correcto.
2. Selecciona el perfil de clave del 1 al 5.
3. Pulsa **Guardar clave** y pega la clave únicamente en el cuadro oculto. No la pegues en esta guía, un JSON, un comando ni un chat.
4. Escribe el identificador exacto de un modelo compatible y autorizado en tu cuenta. No adivines que cualquier modelo admite imágenes.
5. Pulsa **Guardar selección**. Esto recuerda proveedor, modelo y perfil, pero no concede permiso para enviar.
6. Reabre la app: debe recuperar la selección y volver a **Local (sin envíos)**.

Las claves se guardan para el usuario de Windows en el Administrador de credenciales, fuera de la biblioteca. Las variables de entorno de cada proveedor, si existen, tienen prioridad. Los perfiles 2 a 5 usan el sufijo numérico correspondiente, por ejemplo `OPENAI_API_KEY_2`; no hace falta definirlas si usas el botón de guardado.

**Detectar proveedores** solo informa de presencia local. No prueba autenticación, saldo, permisos de modelo, visión ni vigencia de la clave.

Solo cuando quieras hacer una prueba con consumo real: cambia a **Con IA (envío explícito)**, prepara una página sintética, revisa su vista previa y confirma su envío. Mantén pequeño el alcance y el límite. La prueba del paquete no necesita este paso. No utilices una biblioteca real como primera prueba de conectividad.

Qwen está conectado al endpoint internacional de Singapur en esta versión. Una clave de otra región no debe probarse repetidamente suponiendo que es intercambiable. La suscripción de un chat o CLI tampoco equivale a saldo de API.

## 10. Conectar CLI y conservar sesiones por perfil

Instala en la nueva PC **solo los CLI que vayas a usar**, siguiendo su documentación oficial. El ZIP no los incluye ni los instala:

| CLI | Documentación oficial comprobada |
|---|---|
| Codex | [Instalación y uso](https://developers.openai.com/codex/cli), [autenticación](https://developers.openai.com/codex/auth) |
| Claude Code | [Instalación](https://code.claude.com/docs/en/setup), [autenticación](https://code.claude.com/docs/en/authentication) |
| Gemini | [Autenticación y requisitos de acceso](https://geminicli.com/docs/get-started/authentication/) |
| Antigravity | [Instalación y autenticación de agy](https://antigravity.google/docs/cli/install/) |

Una instalación solo dentro de WSL no equivale a un ejecutable Windows visible para esta app. En esta versión, el adaptador de Codex/Claude exige un ejecutable nativo compatible: un wrapper `.cmd`, `.bat` o `.ps1` encontrado en PATH no se acepta como sustituto. Si la detección falla, conserva el diagnóstico y revisa la instalación; no descargues wrappers de terceros para eludirlo.

En la app:

1. Selecciona **Con IA**, porque iniciar sesión implica conexión con el proveedor.
2. Elige el proveedor CLI y el perfil antes de pulsar **Conectar / iniciar sesión**.
3. Completa tú la autenticación oficial. La app no copia tokens ni rellena contraseñas.
4. Cierra la ventana de acceso y vuelve a Sistema MD. Configura modelo y guarda la selección.
5. En usos posteriores, selecciona ese mismo perfil. El CLI conserva su sesión mientras siga siendo válida; puede pedir autenticación de nuevo por caducidad, revocación o política del proveedor.

Perfiles disponibles:

- **Codex y Claude:** Principal y cuenta-1 a cuenta-5 son perfiles propios de Sistema MD. Incluso Principal requiere su primer acceso dentro de este contexto. `pro-1` a `pro-3` se conservan por compatibilidad.
- **Gemini:** Principal puede reutilizar la sesión existente; los otros perfiles son independientes dentro del mecanismo del CLI.
- **Antigravity:** Principal y compatibilidad opcional fab1 a fab5.

Los perfiles fab corresponden a **usuarios Windows ya preparados**, no a cinco alias que funcionen automáticamente en otra computadora. El ZIP no crea esos usuarios, no exporta su llavero y no copia OAuth. No necesitas fab para probar el programa; empieza por Principal. El puente fab requiere preparación y validación específicas, incluido el ejecutable autorizado.

No hay rotación automática para evadir límites. Ejecutar un CLI remoto no es trabajo offline y puede consumir la cuota de la cuenta. Una apertura de login correcta no equivale a una conversión completa verificada.

## 11. TypeSafe: variable aparte, uso optativo

TypeSafe ordena fragmentos recuperados por relevancia. No extrae páginas, no reemplaza Docling/MarkItDown y no certifica fidelidad.

En la nueva cuenta Windows debes configurar por separado la variable de usuario **TYPESAFE_API_KEY** con tu clave. Usa el editor de variables de entorno de Windows; evita introducirla en comandos que queden en el historial. No guardes esa clave en `.env`, archivos del ZIP, capturas o informes. Una variable de usuario persistente tampoco es un almacén cifrado equivalente al Administrador de credenciales.

En **Biblioteca**, escribe una consulta y pulsa **TypeSafe · revisar…**. La vista previa es local. En modo Local no se permite enviar. En modo Con IA, el envío requiere otra confirmación y puede consumir saldo. Se envían consulta, identificadores y fragmentos mostrados, no el archivo nativo completo.

No borres recibos de un intento fallido o incierto para forzar otro envío. La integración conserva trazabilidad y no reintenta automáticamente. Referencia del servicio: [API oficial de TypeSafe](https://docs.typesafe.ai/api).

## 12. Límites que debes reconocer al evaluar

- **Docling:** el adaptador está integrado, pero este ZIP no incluye su entorno ni sus modelos. No hay instalación automática al seleccionarlo. El formulario permite conectar un entorno ya preparado; no confundas guardar una ruta con instalarlo. La interfaz pide páginas PDF explícitas y limita esa operación a 20 páginas.
- **Marker y MinerU:** siguen como alternativas condicionales del plan. No están integrados ni instalados en este paquete. No hay una instrucción de instalación universal validada que esta guía pueda prometer.
- **PDF nativo:** preparar texto e imágenes no equivale a aplicar OCR completo ni a interpretar todos sus gráficos.
- **MarkItDown:** el adaptador es selectivo. No representa todos sus formatos posibles ni garantiza geometría, tablas, fórmulas o figuras completas. HTML/EPUB no reciben páginas físicas inventadas.
- **CAD/Office:** algunos caminos pueden requerir ODA u Office/COM instalados y autorizados en esa PC. Su ausencia debe aparecer como dependencia pendiente, no como conversión exitosa.
- **Offline:** modo Local bloquea los envíos explícitos de la interfaz. Las protecciones Python de los workers no certifican aislamiento de red del sistema operativo ni sustituyen una prueba de salida de red.
- **Comercial:** pasar estos controles no termina la revisión de licencias del conjunto, el instalador firmado, las actualizaciones, la recuperación ni la certificación de segunda PC. Este ZIP es para prueba técnica, no una autorización de redistribución comercial de todas las dependencias.

## 13. Trasladar una biblioteca existente sin arriesgar el original

Hazlo solo después de aprobar el control sintético.

1. En la PC de origen, termina o pausa los trabajos y cierra Sistema MD. No copies una base SQLite mientras la aplicación siga escribiendo.
2. Haz un respaldo independiente de la biblioteca completa, los MD legibles y sus recursos vinculados. No reemplaces el único original por una copia de traslado.
3. Lleva esos datos por separado del ZIP del programa. Conserva juntas las carpetas que usan vínculos relativos y su estructura.
4. En la PC nueva, prueba primero con una copia de esa biblioteca y selecciona su carpeta con **Carpeta de datos…**. Selecciona también la carpeta MD correspondiente.
5. Reabre, revisa el número de documentos, abre varios casos conocidos y verifica imágenes, tablas y enlaces. Contrasta con sus fuentes; un hash íntegro no prueba una buena extracción.
6. Revisa rutas de fuentes externas y trabajos pendientes. Si una ruta pertenece a la PC antigua, vuelve a seleccionarla expresamente; no reasocies documentos únicamente por nombre.

No copies entornos virtuales, runtimes, claves del Administrador de credenciales, `auth.json`, archivos OAuth, carpetas de sesión de `.claude`/`.codex`/Gemini ni perfiles `SistemaMD/perfiles_cli`. Realiza el acceso oficial en el destino.

No abras la misma biblioteca sincronizada en dos PC simultáneamente. SQLite coordina procesos locales; no coordina dos réplicas independientes de OneDrive/Drive. Los MD antiguos tampoco se reescriben automáticamente para arreglar rutas: vuelve a exportar casos controlados y revisa la protección de tus ediciones manuales.

## 14. Qué hacer si algo falla

| Síntoma | Acción segura |
|---|---|
| `py` no se reconoce | Revisa el lanzador o utiliza el Python base completo; no copies el de otra PC. |
| Tcl/Tk no abre | Repara Tcl/Tk en la instalación oficial antes de preparar el núcleo. |
| Dependencias no descargadas | Revisa acceso a PyPI y guarda el error; reanuda con el flag correcto y la misma identidad. |
| Núcleo/worker ya existe | No lo borres ni fuerces `resume` si está aceptado. Usa una instalación nueva separada para comparar. |
| Falta Python/modelos de un motor | Revisa su configuración. No debe cambiar a otra API o motor silenciosamente. |
| La biblioteca parece vacía | Comprueba la carpeta elegida y la variable de datos; no ejecutes limpiezas ni importaciones masivas. |
| Ruta de otra PC o unidad desconectada | Reconecta la unidad o selecciona un destino existente de forma explícita. |
| CLI no detectado | Verifica instalación oficial nativa y PATH; cierra/reabre la app después de instalar. |
| Login terminado, pero falla el envío | Presencia y autenticación no prueban modelo/cuota. Conserva recibo; no repitas una llamada incierta. |
| MD editado no se reemplaza | Es una protección deliberada. Conserva ambas versiones y revisa el informe de exportación. |

Comandos de diagnóstico sin envío de IA:

```powershell
.\SISTEMA_MD.bat --check
.\SISTEMA_MD.bat motores-locales
.\SISTEMA_MD.bat proveedores
.\SISTEMA_MD.bat diagnostico
.\SISTEMA_MD.bat estado-implementacion
```

`diagnostico` puede crear infraestructura local de diagnóstico. `estado-implementacion` informa cobertura del plan, no convierte requisitos pendientes en terminados. No uses **reparar**, vaciar resultados o eliminar archivos como primer intento ante un error desconocido.

## 15. Evidencia mínima para informar el resultado

Conserva `REPORTE_PRUEBA.md`, `REPORTE_PRUEBA.json` y las demás evidencias generadas por `PROBAR_EQUIPO.py`. Antes de compartirlas, revisa nombres de usuario, rutas personales y cualquier dato que no quieras enviar; los informes de arranque y los MD también pueden contener rutas de esta PC. No compartas el volcado del entorno completo, claves, archivos de credenciales, tokens, códigos de acceso ni la biblioteca real.

Lista de comprobación:

- [ ] ZIP extraído por completo en una carpeta nueva.
- [ ] Manifiesto verificado antes de modificar el paquete.
- [ ] Windows x64, Python 3.12 y Tcl/Tk identificados.
- [ ] Núcleo preparado y `--check` aprobado.
- [ ] Control TXT local aprobado.
- [ ] MarkItDown instalado y control HTML aprobado, o ausencia registrada expresamente.
- [ ] Ventana, navegación, botones, búsqueda y apertura de un resultado revisados.
- [ ] Carpeta de datos y carpeta MD comprobadas.
- [ ] Reapertura conserva selección y vuelve a Local.
- [ ] No hubo envíos API/CLI/TypeSafe durante el control local.
- [ ] Limitaciones y pruebas omitidas registradas, sin presentarlas como aprobadas.

Plantilla para comunicar el resultado, sin secretos:

```text
PC de prueba: "ALIAS_NO_PERSONAL_DEL_EQUIPO"
Windows y arquitectura: "VERSION_Y_X64"
Python: "VERSION_3_12_X"
Manifiesto: "APROBADO_O_ERROR"
Núcleo/Tk: "RESULTADO"
TXT nativo: "RESULTADO"
MarkItDown/HTML: "APROBADO_OMITIDO_O_ERROR"
Navegación y MD: "RESULTADO"
Carpetas elegidas: "INTERNAS_O_EXTERNAS_SIN_RUTAS_PERSONALES"
Paso exacto que falló: "NUMERO_Y_COMANDO_SIN_SECRETOS"
Mensaje/código seguro: "ERROR_SIN_CLAVES_NI_TOKENS"
Llamadas a IA realizadas durante esta prueba: "NINGUNA_O_DESCRIBIR_LAS_AUTORIZADAS"
Adjunto: "NOMBRE_DEL_INFORME_REVISADO"
```

## 16. Retirar la prueba sin borrar tus datos

1. Cierra Sistema MD y cualquier terminal de login que haya quedado abierta.
2. Identifica las carpetas de datos y MD seleccionadas. Pueden estar fuera del directorio del programa.
3. Respalda lo que quieras conservar, especialmente si pusiste datos dentro de la carpeta de prueba.
4. Para dejar de usar la prueba basta con no abrirla. Si quieres retirarla, mueve **solo su carpeta de instalación identificada** a la Papelera después del respaldo. No borres una carpeta general de Documentos, Escritorio, OneDrive ni perfiles del usuario.
5. No desinstales Python o CLI compartidos con otros proyectos como parte automática de esta limpieza.
6. Quitar el programa no revoca claves ni necesariamente cierra sesiones de proveedores. Si configuraste accesos para la prueba, gestiona su cierre/revocación por los mecanismos oficiales y comprueba qué otras herramientas los utilizan.

Esta guía no contiene un comando de borrado masivo ni un apagado automático. Conservar los datos tiene prioridad sobre dejar una carpeta vacía.
