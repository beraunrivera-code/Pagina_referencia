# Sistema MD en Linux / contenedor

Mismo programa que en Windows; `entrypoint.sh` cumple el papel de `SISTEMA_MD.bat`.

## Contenedor (Ubuntu 24.04, headless)

```bash
docker build -t sistema-md .                                   # ver args en el Dockerfile
docker run --rm -v "$PWD/datos:/data" sistema-md convertir /data/entrada/libro.xlsx
docker run --rm -v "$PWD/datos:/data" sistema-md informe
docker run --rm sistema-md pruebas                             # suite app/tests con Xvfb
docker run --rm sistema-md --check                             # control de arranque
```

- `/data` es la biblioteca (`SISTEMA_MD_DATA`). Usuario `sistemamd` (uid 1000).
- Args: `WITH_MARKITDOWN`, `WITH_LIBREOFFICE` (XLS/DOC/PPT/XLSB), `WITH_OCR` (Tesseract); todos a 1.
- DWG: deja el `.deb` oficial de ODA File Converter en `vendor/` antes de construir.

## Linux nativo

```bash
sudo apt install python3.12 python3.12-venv python3-tk xvfb      # + libreoffice-calc/-writer/-impress, tesseract-ocr-spa
python3.12 preparar_equipo.py --instalar
./entrypoint.sh                     # interfaz si hay $DISPLAY; si no, ayuda de la CLI
./entrypoint.sh pruebas             # usa xvfb-run automáticamente sin escritorio
python3.12 app/workers/prepare_markitdown.py --python /usr/bin/python3.12 --install   # opcional
```

## Qué cambia respecto a Windows

| Función | Windows | Linux |
|---|---|---|
| Abrir resultado / vista | asociación y navegador del registro | `xdg-open` con escritorio; headless solo registra la ruta |
| Claves API | Administrador de credenciales o variable | solo variables de entorno (`OPENAI_API_KEY_2`, `TYPESAFE_API_KEY`…) |
| Perfiles CLI | `%LOCALAPPDATA%\SistemaMD` | `$XDG_DATA_HOME/SistemaMD` (`~/.local/share`) |
| Acceso CLI | consola nueva | la terminal desde la que se lanzó |
| ODA | `Program Files\ODA` | `SISTEMA_MD_ODA`, `/usr/bin/ODAFileConverter*`, `/opt/ODAFileConverter*`; sin `$DISPLAY` se lanza con `xvfb-run -a` |
| Puente fab1–fab5 | runas + Authenticode + ACL NTFS | **no disponible**: falla cerrado con `fab_windows`; no se omiten ni simulan esas garantías |
| Worker MarkItDown | lock `win-py312` | lock `linux-py312` (mismas versiones, sin `pyreadline3`) |
