# vendor/: instaladores propietarios opcionales

El `Dockerfile` instala **ODA File Converter** (lectura de DWG) solo si encuentra aquí su paquete
oficial para Linux, por ejemplo `ODAFileConverter_QT6_lnxX64_8.3dll_25.12.deb`.

- Descárgalo de <https://www.opendesign.com/guestfiles/oda_file_converter> aceptando su licencia.
- No se incluye en el repositorio: es software de terceros con licencia propia.
- Sin él, los DXF se leen igual (ezdxf); los DWG fallan con un mensaje explícito.
- En el contenedor no hay `$DISPLAY`: Sistema MD lanza ODA con `xvfb-run -a` automáticamente.
- Fuera de Docker, indica otra ubicación con la variable `SISTEMA_MD_ODA`.
