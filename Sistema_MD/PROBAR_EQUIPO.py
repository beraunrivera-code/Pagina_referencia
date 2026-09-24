"""Control reproducible con datos sintéticos, sin claves ni llamadas IA.

--verificar-paquete solo usa la biblioteca estándar y no modifica archivos.
--salida crea una carpeta NUEVA con fuentes y evidencias; nunca usa tu biblioteca.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import platform
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def verify_package(root=ROOT):
    root = Path(root).resolve()
    manifest = json.loads((root / 'MANIFIESTO_PAQUETE.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict) or not manifest['files']:
        raise ValueError('Manifiesto inválido')
    problems = []
    for name, expected in manifest['files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise ValueError('Ruta fuera del paquete')
        path = root.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError('Enlace fuera del paquete')
        if not path.is_file():
            problems.append({'file':name, 'status':'ausente'})
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append({'file':name, 'status':'modificado'})
    return {'ok':not problems, 'files_checked':len(manifest['files']), 'problems':problems,
            'notice':'SHA-256 detecta cambios en archivos enumerados; no es firma digital ni auditoría de archivos añadidos.'}


def run_control(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    result = {'date_utc':datetime.now(timezone.utc).isoformat(), 'platform':platform.platform(),
              'python':sys.version.split()[0], 'external_ai_calls':0, 'checks':[],
              'limits':['No certifica fidelidad general, autenticación, offline OS ni instalador comercial.',
                        'No modifica cuentas ni datos existentes. Fuentes exclusivamente sintéticas.']}
    def save(name, value):
        (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        if (ROOT / 'MANIFIESTO_PAQUETE.json').exists():
            integrity = verify_package()
            save('integridad_paquete.json', integrity)
            result['checks'].append({'name':'Integridad del paquete','ok':integrity['ok']})
            if not integrity['ok']:
                raise ValueError('Paquete modificado o incompleto; no continuar')
        import iniciar
        iniciar.configure_process()
        capture = io.StringIO()
        with redirect_stdout(capture):
            start_code = iniciar.check()
        startup = json.loads(capture.getvalue())
        save('arranque.json', startup)
        result['checks'].append({'name':'Arranque y dependencias','ok':start_code == 0})
        if start_code:
            raise RuntimeError('Entorno incompleto; revisar arranque.json')
        from conversion.pipeline import convert_text
        from conversion.storage import export_markdown, verify_artifacts
        from conversion.local_engines import convert_with_engine, engine_python, LocalEngineError
        sources = destination / 'fuentes_sinteticas'
        sources.mkdir()
        library = destination / 'biblioteca_sintetica'
        source = sources / 'Control local.txt'
        source.write_text('Control de otra PC\n\nMaterial: Cemento\nCantidad: 123\n', encoding='utf-8')
        # Guard acotado del proceso padre. El worker tiene su propio guard Python.
        with patch.object(socket,'create_connection',side_effect=AssertionError('Sin red en el control')):
            native = convert_text(library, source)
            native_text = (Path(native['output']) / 'documento.md').read_text(encoding='utf-8')
            ok = verify_artifacts(Path(native['output'])) and 'Cemento' in native_text and '123' in native_text
            native_export = export_markdown(library, native['id'], destination / 'MD')
            result['checks'].append({'name':'TXT nativo e integridad','ok':ok,'export':native_export})
            try:
                engine_python('markitdown')
            except LocalEngineError:
                result['checks'].append({'name':'MarkItDown','ok':False,'status':'pendiente_instalacion',
                    'action':'Preparar el worker según GUIA_PRUEBA_OTRA_PC.md y repetir en una carpeta NUEVA.'})
            else:
                html = sources / 'Control MarkItDown.html'
                html.write_text('<h1>Control MarkItDown</h1><table><tr><th>Material</th><th>Cantidad</th></tr>'
                                '<tr><td>Cemento</td><td>123</td></tr></table>', encoding='utf-8')
                converted = convert_with_engine(library, html, 'markitdown', timeout=120)
                raw = (Path(converted['output']) / 'crudos/markitdown.md').read_text(encoding='utf-8')
                ok = verify_artifacts(Path(converted['output'])) and 'Cemento' in raw and '123' in raw
                exported = export_markdown(library, converted['id'], destination / 'MD')
                result['checks'].append({'name':'MarkItDown HTML real','ok':ok,'export':exported,
                                        'semantic_verified':False,'offline_verified':False})
        result['ok'] = all(row['ok'] for row in result['checks'])
    except Exception as exc:
        result['ok'] = False
        result['error_type'] = type(exc).__name__
        result['action'] = 'Revisar arranque/integridad y la guía. No pegar claves ni borrar recibos para repetir IA.'
    save('REPORTE_PRUEBA.json', result)
    rows = ['# Informe de control de otra PC\n',
            f"Resultado: {'PASA control acotado' if result['ok'] else 'REQUIERE REVISIÓN'}\n",
            'Llamadas a IA: 0. Este control usa únicamente documentos sintéticos.\n']
    rows += [f"- {row['name']}: {'PASA' if row['ok'] else 'PENDIENTE / FALLA'}\n" for row in result['checks']]
    rows += ['\nLeer REPORTE_PRUEBA.json y arranque.json. Revisar manualmente los MD, sus enlaces e interfaz.\n',
             'No prueba modelos/cuentas, fidelidad completa ni aislamiento OS. Los informes pueden contener rutas de esta PC: revísalos antes de compartirlos.\n']
    (destination / 'REPORTE_PRUEBA.md').write_text(''.join(rows), encoding='utf-8')
    return result


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--verificar-paquete', action='store_true')
    mode.add_argument('--salida', type=Path, help='Carpeta NUEVA de evidencias; no se sobrescribe una existente')
    args = parser.parse_args(argv)
    try:
        result = verify_package() if args.verificar_paquete else run_control(args.salida)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['ok'] else 2
    except (OSError,ValueError) as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,
                         'action':'Extraer todo el ZIP, revisar permisos y usar una carpeta de evidencias nueva.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
