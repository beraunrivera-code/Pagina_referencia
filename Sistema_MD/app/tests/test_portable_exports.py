"""Enlaces legibles portables sin modificar el paquete ni copiar activos al MD."""
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit
from unittest import TestCase
from unittest.mock import patch

from conversion.documents import digest
from conversion.storage import export_markdown, publish, verify_artifacts
from support import workspace_temp


class PortableExportsTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.base = Path(fixture.name)
        self.install = self.base / "equipo original"
        self.root = self.install / "biblioteca con espacios"
        self.destination = self.install / "MD legibles"
        asset = "imagenes/plano A (copia).png"
        image = b"fixture imagen no enviada"
        document = {"schema_version": 1, "title": "Plano de control", "expected_units": [1],
                    "units": [{"number": 1, "image_asset": asset, "image_sha256": digest(image),
                               "blocks": [{"id": "p1-b1", "kind": "paragraph", "text": "Texto de control.\n"}]}]}
        self.package = publish(self.root, document, {asset: image})

    def links(self, markdown):
        return re.findall(r"\]\(<([^>]+)>\)", markdown)

    def test_move_library_and_markdown_keeps_all_generated_links_valid(self):
        result = export_markdown(self.root, self.package["id"], self.destination)
        exported = Path(result["path"])
        contents = exported.read_text(encoding="utf-8")
        self.assertTrue(result["portable_links"])
        self.assertEqual(result["link_mode"], "relative")
        links = self.links(contents)
        self.assertEqual(len(links), 3)
        self.assertTrue(all(not urlsplit(link).scheme for link in links))
        self.assertTrue(any("biblioteca%20con%20espacios" in link for link in links))
        self.assertTrue(any("plano%20A%20%28copia%29.png" in link for link in links))
        for link in links:
            self.assertTrue((exported.parent / unquote(link)).resolve().is_file())
        self.assertEqual(list(self.destination.iterdir()), [exported])
        self.assertTrue(verify_artifacts(Path(self.package["output"])))

        relocated = self.base / "otro equipo"
        shutil.copytree(self.install, relocated)
        moved_md = relocated / exported.relative_to(self.install)
        self.assertEqual(moved_md.read_bytes(), exported.read_bytes())
        for link in self.links(moved_md.read_text(encoding="utf-8")):
            target = (moved_md.parent / unquote(link)).resolve()
            self.assertTrue(target.is_relative_to(relocated))
            self.assertTrue(target.is_file())

    def test_relative_export_remains_deduplicated(self):
        first = export_markdown(self.root, self.package["id"], self.destination)
        second = export_markdown(self.root, self.package["id"], self.destination)
        self.assertEqual(first["path"], second["path"])
        self.assertFalse(second["created"])
        self.assertTrue(second["portable_links"])

    def test_cross_drive_fallback_is_explicit_and_encoded(self):
        with patch("conversion.storage.os.path.relpath", side_effect=ValueError("different drives")):
            result = export_markdown(self.root, self.package["id"], self.destination)
        self.assertFalse(result["portable_links"])
        self.assertEqual(result["link_mode"], "absolute")
        self.assertIn("unidades distintas", result["link_warning"])
        links = self.links(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(links), 3)
        self.assertTrue(all(link.startswith("file:///") for link in links))
        self.assertTrue(any("plano%20A%20%28copia%29.png" in link for link in links))
        self.assertTrue(verify_artifacts(Path(self.package["output"])))

    def test_existing_manual_markdown_never_overwritten(self):
        self.destination.mkdir(parents=True)
        manual = self.destination / "Plano de control.md"
        manual.write_text("MI EDICION MANUAL", encoding="utf-8")
        result = export_markdown(self.root, self.package["id"], self.destination)
        self.assertNotEqual(Path(result["path"]), manual)
        self.assertEqual(manual.read_text(), "MI EDICION MANUAL")
        self.assertTrue(result["portable_links"])
