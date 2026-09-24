"""Bundled-component manifest, fetching and verification (downloads are faked)."""

import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from nfnf_ironmon.components import ComponentError, ComponentManager
from nfnf_ironmon.hashing import sha256_bytes
from nfnf_ironmon.paths import BUNDLE_ROOT
from nfnf_ironmon.platform_support import platform_tag


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def make_targz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def manifest(entries):
    return {"schema": 1, "components": entries}


def comp(cid, platforms):
    return {"id": cid, "name": cid, "version": "1", "license": "MIT", "platforms": platforms}


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def mgr(self, entries, platform="linux-x64"):
        m = manifest(entries)
        (self.root / "components.json").write_text(json.dumps(m))
        return ComponentManager(self.root, m, platform)

    def test_platform_tag_shape(self):
        os_part, arch = platform_tag().split("-")
        self.assertIn(os_part, ("linux", "windows", "macos"))
        self.assertTrue(arch)

    def test_status_missing_then_present(self):
        data = make_targz({"jre-1/bin/java": b"#!/bin/sh\n", "jre-1/lib/x": b"x"})
        m = self.mgr([comp("java", {"linux-x64": {"url": "u", "sha256": sha256_bytes(data),
                                                  "archive": "tar.gz", "strip_components": 1,
                                                  "dest": "runtime/java", "entry": "bin/java"}})])
        self.assertEqual(m.status("java").state, "MISSING")
        self.assertIsNone(m.resolve("java"))
        entry = m.fetch("java", downloader=lambda url: data)
        self.assertEqual(entry, self.root / "runtime/java/bin/java")
        self.assertTrue((self.root / "runtime/java/lib/x").exists())
        self.assertEqual(m.status("java").state, "PRESENT")
        self.assertTrue(entry.stat().st_mode & 0o100)  # executable bit kept

    def test_sha_mismatch_rejected_and_nothing_installed(self):
        data = make_zip({"a.jar": b"x"})
        m = self.mgr([comp("upr", {"any": {"url": "u", "sha256": "0" * 64, "archive": "zip",
                                           "dest": "randomizer/upr", "entry": "a.jar"}})])
        with self.assertRaisesRegex(ComponentError, "mismatch"):
            m.fetch("upr", downloader=lambda url: data)
        self.assertFalse((self.root / "randomizer/upr").exists())

    def test_unpinned_requires_pin_then_records_hash(self):
        data = b"settings"
        m = self.mgr([comp("rnqs", {"any": {"url": "u", "sha256": None, "archive": None,
                                            "dest": "profiles", "entry": "x.rnqs"}})])
        with self.assertRaisesRegex(ComponentError, "--pin"):
            m.fetch("rnqs", downloader=lambda url: data)
        m.fetch("rnqs", pin=True, downloader=lambda url: data)
        saved = json.loads((self.root / "components.json").read_text())
        self.assertEqual(saved["components"][0]["platforms"]["any"]["sha256"], sha256_bytes(data))
        self.assertEqual((self.root / "profiles/x.rnqs").read_bytes(), data)

    def test_path_traversal_rejected(self):
        data = make_zip({"../../evil.txt": b"x"})
        m = self.mgr([comp("bad", {"any": {"url": "u", "sha256": sha256_bytes(data), "archive": "zip",
                                           "dest": "d", "entry": "evil.txt"}})])
        with self.assertRaises(ComponentError):
            m.fetch("bad", downloader=lambda url: data)
        self.assertFalse((self.root.parent / "evil.txt").exists())

    def test_platform_selection(self):
        m = self.mgr([comp("core", {"windows-x64": {"url": "u", "sha256": None, "archive": None,
                                                    "dest": "d", "entry": "core.dll"}})])
        self.assertEqual(m.status("core").state, "UNSUPPORTED_PLATFORM")
        with self.assertRaises(ComponentError):
            m.fetch("core")
        win = ComponentManager(self.root, m.manifest, "windows-x64")
        self.assertEqual(win.entry_path("core"), self.root / "d/core.dll")


class BundledManifestTests(unittest.TestCase):
    def test_project_manifest_is_complete(self):
        m = ComponentManager(BUNDLE_ROOT)
        self.assertTrue({"java-runtime", "upr-zx", "mgba-libretro", "sdl2", "firered-ironmon-rnqs"} <= set(m.ids()))
        for c in m.manifest["components"]:
            with self.subTest(c["id"]):
                self.assertTrue(c["license"])
                self.assertTrue(c.get("redistributable"))
                for plat, p in c["platforms"].items():
                    self.assertTrue(p["url"].startswith("https://"))
                    self.assertFalse(p["dest"].startswith(("/", "..")))
        # linux builds are pinned (verified on first fetch); windows core is not fetched yet
        self.assertIsNotNone(m.spec("java-runtime")["platforms"]["windows-x64"]["sha256"])
        self.assertIsNotNone(m.spec("upr-zx")["platforms"]["any"]["sha256"])


if __name__ == "__main__":
    unittest.main()
