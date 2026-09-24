"""Carpetas exclusivas de pruebas, compatibles con el sandbox de Windows.

TemporaryDirectory usa mkdir(0o700); aquí se usa el modo predeterminado de
Path.mkdir como en producción, sin cambiar permisos ni configuraciones.
"""

import shutil
import uuid
from pathlib import Path


class workspace_temp:
    def __init__(self):
        self.parent = Path(__file__).resolve().parent / "_runs"
        self.parent.mkdir(exist_ok=True)
        self.path = self.parent / ("fixture-" + uuid.uuid4().hex)
        self.path.mkdir()
        self.name = str(self.path)

    def cleanup(self):
        target = self.path.resolve()
        if target.parent != self.parent.resolve() or not target.name.startswith("fixture-"):
            raise ValueError("Limpieza fuera del ámbito de pruebas")
        shutil.rmtree(target)
