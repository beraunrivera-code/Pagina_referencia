# Sistema MD en contenedor Linux headless (Ubuntu 24.04 · CPython 3.12 · Tk + Xvfb).
#
#   docker build -t sistema-md .
#   docker run --rm -v "$PWD/datos:/data" sistema-md convertir /data/entrada/libro.xlsx
#   docker run --rm sistema-md pruebas            # suite app/tests con Xvfb
#
# Args de construcción (1 = incluir, 0 = omitir):
#   WITH_MARKITDOWN=1  worker MarkItDown con su lock Linux (~400 MB)
#   WITH_LIBREOFFICE=1 XLS/DOC/PPT/XLSB vía copia OOXML (Calc/Writer/Impress sin interfaz, ~350 MB)
#   WITH_OCR=1         Tesseract (spa+eng) para imágenes y páginas PDF escaneadas
# ODA File Converter (DWG) es software propietario sin descarga automática: deja su .deb
# oficial en vendor/ antes de construir y se instala solo si está presente.
FROM ubuntu:24.04

ARG WITH_MARKITDOWN=1
ARG WITH_LIBREOFFICE=1
ARG WITH_OCR=1

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SISTEMA_MD_DATA=/data \
    SISTEMA_MD_PYTHON=/opt/sistema_md/.venv-local/bin/python

# python3-tk: tkinter del Python 3.12 del sistema. xvfb/xauth: pantalla virtual para Tk
# (pruebas, --check) y para el Qt de ODA sin $DISPLAY. Fuentes: Tk necesita al menos una.
RUN set -eu; \
    extra=""; \
    if [ "$WITH_LIBREOFFICE" = "1" ]; then extra="$extra libreoffice-calc libreoffice-writer libreoffice-impress"; fi; \
    if [ "$WITH_OCR" = "1" ]; then extra="$extra tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng"; fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3-tk \
        xvfb xauth fonts-dejavu-core ca-certificates $extra \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/ /tmp/vendor/
RUN set -eu; \
    deb="$(find /tmp/vendor -maxdepth 1 -name 'ODAFileConverter*.deb' | sort | tail -n 1)"; \
    if [ -n "$deb" ]; then \
        apt-get update && apt-get install -y --no-install-recommends "$deb" && rm -rf /var/lib/apt/lists/*; \
    fi; \
    rm -rf /tmp/vendor

# Usuario sin privilegios; uid 1000 para que el volumen /data sea escribible desde el host.
RUN userdel --remove ubuntu 2>/dev/null || true \
    && useradd --create-home --uid 1000 --shell /bin/bash sistemamd \
    && install -d -o sistemamd -g sistemamd /opt/sistema_md /data

WORKDIR /opt/sistema_md
COPY --chown=sistemamd:sistemamd . .
USER sistemamd

# Preparador propio del proyecto: crea .venv-local, instala requirements-local.txt y
# acepta el entorno solo si el control de arranque (Tk incluido) pasa bajo Xvfb.
RUN xvfb-run -a python3.12 preparar_equipo.py --instalar \
    && if [ "$WITH_MARKITDOWN" = "1" ]; then \
        .venv-local/bin/python app/workers/prepare_markitdown.py --python /usr/bin/python3.12 --install; \
    fi

VOLUME ["/data"]
ENTRYPOINT ["/opt/sistema_md/entrypoint.sh"]
