"""Installer tests use synthetic packages only; no upstream code is included."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "frontend_overlay.py"


def digest(data):
    return hashlib.sha256(data).hexdigest()


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "frontend overlay installer is not implemented")
        spec = importlib.util.spec_from_file_location("frontend_overlay", SCRIPT)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "package"
        self.assets = self.root / "dist/client/assets/js"
        self.assets.mkdir(parents=True)
        self.bundle = self.assets / "index-fixture.js"
        self.bundle.write_bytes(b"// synthetic main bundle\n")
        self.html = self.root / "dist/client/index.html"
        self.original = (b'<!doctype html>\n<html><head>\n'
                         b'<script type="module" crossorigin src="/assets/js/index-fixture.js"></script>\n'
                         b'</head><body><div id="app"></div></body></html>\n')
        self.html.write_bytes(self.original)
        self.manifest = self.root / "package.json"
        self.set_version("0.7.22")
        self.source = self.base / "singleflight.js"
        self.source.write_bytes(b"(() => { window.__fixture = true; })();\n")
        self.backup = self.base / "backup"
        self.supported = {"0.7.22": {"bundle": "index-fixture.js", "sha256": digest(self.bundle.read_bytes())}}

    def set_version(self, version):
        self.manifest.write_text(json.dumps({"name": "hermes-web-ui", "version": version}))

    def snapshot(self):
        return {str(p.relative_to(self.base)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.base.rglob("*") if p.is_file()}

    def run_overlay(self, action, **kwargs):
        return self.api.run(action, root=self.root, source=self.source,
                            supported=self.supported, **kwargs)

    def test_unknown_versions_refuse_all_actions_without_writes(self):
        for version in ("0.7.24", "0.7.23", "9.0.0", "0.7.22-custom"):
            self.set_version(version)
            before = self.snapshot()
            for action in ("apply", "check", "remove"):
                with self.subTest(version=version, action=action):
                    with self.assertRaisesRegex(self.api.OverlayError, "unsupported"):
                        self.run_overlay(action, backup_dir=self.backup, confirm=True)
                    self.assertEqual(before, self.snapshot())
                    self.assertFalse(self.backup.exists())

    def test_main_bundle_hash_is_required_for_every_action(self):
        self.bundle.write_bytes(b"// unexpected upstream changes\n")
        before = self.snapshot()
        for action in ("apply", "check", "remove"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(self.api.OverlayError, "main bundle.*hash"):
                    self.run_overlay(action, backup_dir=self.backup, confirm=True)
                self.assertEqual(before, self.snapshot())
                self.assertFalse(self.backup.exists())

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = self.api.main(list(args), supported=self.supported, source=self.source)
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_missing_check_returns_two_and_stderr(self):
        before = self.snapshot()
        code, stdout, stderr = self.cli("check", "--root", str(self.root))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("missing", stderr)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.backup.exists())

    def test_cli_requires_explicit_mutation_parameters(self):
        for action in ("apply", "remove"):
            for arguments, message in (([], "--root"),
                                       (["--root", str(self.root)], "--confirm"),
                                       (["--root", str(self.root), "--confirm"], "--backup-dir")):
                with self.subTest(action=action, arguments=arguments):
                    before = self.snapshot()
                    code, stdout, stderr = self.cli(action, *arguments)
                    self.assertEqual(code, 2)
                    self.assertIn(message, stderr)
                    self.assertEqual(stdout, "")
                    self.assertEqual(before, self.snapshot())
        with self.assertRaisesRegex(self.api.OverlayError, "--confirm"):
            self.run_overlay("apply", backup_dir=self.backup)
        with self.assertRaisesRegex(self.api.OverlayError, "unknown action"):
            self.run_overlay("repair", backup_dir=self.backup, confirm=True)
        self.assertFalse(self.backup.exists())

    def test_cli_unsupported_returns_two_without_writes(self):
        self.set_version("0.7.24")
        before = self.snapshot()
        for action in ("check", "apply", "remove"):
            code, _, stderr = self.cli(action, "--root", str(self.root),
                                       "--backup-dir", str(self.backup), "--confirm")
            self.assertEqual(code, 2)
            self.assertIn("unsupported", stderr)
        self.assertEqual(before, self.snapshot())

    def test_backup_directory_inside_package_is_rejected_before_writes(self):
        for backup in (self.root, self.root / "private-backup", self.assets / "backup"):
            with self.subTest(backup=backup):
                before = self.snapshot()
                with self.assertRaisesRegex(self.api.OverlayError, "backup-dir.*outside"):
                    self.run_overlay("apply", backup_dir=backup, confirm=True)
                self.assertEqual(before, self.snapshot())

    def test_partial_backup_is_rejected_before_any_package_writes(self):
        self.backup.mkdir()
        (self.backup / "index.html.original").write_bytes(b"do not overwrite")
        before = self.snapshot()
        with self.assertRaisesRegex(self.api.OverlayError, "backup"):
            self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.assertEqual(before, self.snapshot())

    def test_unknown_html_refuses_before_backup_creation(self):
        main = b'<script type="module" crossorigin src="/assets/js/index-fixture.js"></script>'
        variants = (
            self.original.replace(main, b"<!-- " + main + b" -->"),
            self.original.replace(main, main + main),
            self.original.replace(main, b'<script src="/unowned.js"></script>' + main),
            self.original.replace(main, b'<script type="module">unexpected()</script>' + main),
            self.original.replace(main, b'<base href="https://invalid.example/">' + main),
            self.original.replace(b"index-fixture.js", b"index-other.js"),
            self.original + b"<!-- webui-performance-singleflight:v1 end -->",
        )
        for html in variants:
            with self.subTest(html=html):
                self.html.write_bytes(html)
                before = self.snapshot()
                with self.assertRaisesRegex(self.api.OverlayError, "unknown HTML"):
                    self.run_overlay("apply", backup_dir=self.backup, confirm=True)
                self.assertEqual(before, self.snapshot())
                self.assertFalse(self.backup.exists())

    def test_unowned_target_is_never_overwritten(self):
        target = self.assets / "webui-performance-singleflight-v1.js"
        target.write_bytes(self.source.read_bytes())
        before = self.snapshot()
        with self.assertRaisesRegex(self.api.OverlayError, "unmanaged"):
            self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.backup.exists())

    def test_hash_changes_refuse_mutations_without_writes(self):
        self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        for path in (self.html, self.assets / "webui-performance-singleflight-v1.js",
                     self.backup / "index.html.original", self.bundle):
            contents = path.read_bytes()
            path.write_bytes(contents + b"\n<!-- third-party change -->\n")
            before = self.snapshot()
            for action in ("apply", "check", "remove"):
                with self.subTest(path=path, action=action):
                    with self.assertRaisesRegex(self.api.OverlayError, "hash"):
                        self.run_overlay(action, backup_dir=self.backup, confirm=True)
                    self.assertEqual(before, self.snapshot())
            path.write_bytes(contents)

    def test_receipt_root_and_version_must_match(self):
        self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        path = self.backup / "receipt.json"
        receipt = json.loads(path.read_bytes())
        for field, value in (("root", str(self.base)), ("version", "0.7.24")):
            changed = dict(receipt, **{field: value})
            path.write_text(json.dumps(changed))
            before = self.snapshot()
            with self.assertRaisesRegex(self.api.OverlayError, "receipt.*mismatch"):
                self.run_overlay("remove", backup_dir=self.backup, confirm=True)
            self.assertEqual(before, self.snapshot())
        path.write_text(json.dumps(receipt))

    def test_symlinked_html_is_rejected_without_external_changes(self):
        external = self.base / "external.html"
        external.write_bytes(self.original)
        self.html.unlink()
        self.html.symlink_to(external)
        before = self.snapshot()
        with self.assertRaisesRegex(self.api.OverlayError, "symlink"):
            self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(self.html.is_symlink())
        self.assertFalse(self.backup.exists())

    def test_dangling_target_symlink_is_rejected_before_backups(self):
        target = self.assets / "webui-performance-singleflight-v1.js"
        target.symlink_to(self.base / "absent.js")
        with self.assertRaisesRegex(self.api.OverlayError, "symlink"):
            self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.html.read_bytes(), self.original)
        self.assertTrue(target.is_symlink())

    def test_symlinked_assets_directory_is_rejected(self):
        outside = self.base / "outside-assets"
        self.assets.rename(outside)
        self.assets.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(self.api.OverlayError, "symlink"):
            self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.assertFalse(self.backup.exists())
        self.assertFalse((outside / "webui-performance-singleflight-v1.js").exists())

    def test_check_missing_does_not_require_source_script(self):
        self.source.unlink()
        code, _, stderr = self.cli("check", "--root", str(self.root),
                                    "--backup-dir", str(self.backup))
        self.assertEqual(code, 2)
        self.assertIn("overlay missing", stderr)
        self.assertFalse(self.backup.exists())

    def test_cli_entrypoint_uses_only_the_pinned_release(self):
        self.assertEqual(set(self.api.SUPPORTED), {"0.7.22"})
        self.assertEqual(self.api.SOURCE, SCRIPT.parents[1] / "patches/singleflight.js")
        before = self.snapshot()
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "apply", "--root", str(self.root),
                                 "--backup-dir", str(self.backup), "--confirm"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing or non-regular file", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.backup.exists())

    def test_remove_uses_receipt_not_current_source(self):
        self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.source.write_bytes(b"// changed source\n")
        before = self.snapshot()
        with self.assertRaisesRegex(self.api.OverlayError, "source script hash changed"):
            self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.assertEqual(before, self.snapshot())
        self.source.unlink()
        self.assertEqual(self.run_overlay("remove", backup_dir=self.backup, confirm=True), "removed")
        self.assertEqual(self.html.read_bytes(), self.original)

    def test_failed_html_replace_leaves_original_and_no_overlay(self):
        with mock.patch.object(self.api.os, "replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaisesRegex(OSError, "simulated"):
                self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.assertEqual(self.html.read_bytes(), self.original)
        self.assertFalse((self.assets / "webui-performance-singleflight-v1.js").exists())
        self.assertEqual((self.backup / "index.html.original").read_bytes(), self.original)
        self.assertFalse(list(self.assets.glob(".*")))
        self.assertFalse(list(self.html.parent.glob(".*")))

    def test_cli_success_and_byte_exact_restoration(self):
        original = self.original.replace(b"\n", b"\r\n").replace(b"<body>", "<body>示例".encode())
        self.html.write_bytes(original)
        self.html.chmod(0o640)
        for action, expected in (("apply", "applied"), ("check", "installed and verified"), ("remove", "removed")):
            code, stdout, stderr = self.cli(action, "--root", str(self.root),
                                            "--backup-dir", str(self.backup), "--confirm")
            self.assertEqual((code, stdout.strip(), stderr), (0, expected, ""))
        self.assertEqual(self.html.read_bytes(), original)
        self.assertEqual(self.html.stat().st_mode & 0o777, 0o640)

    def test_invalid_package_metadata_has_clear_stderr_and_no_writes(self):
        for package in ([], {"name": "other", "version": "0.7.22"},
                        {"name": "hermes-web-ui", "version": []}):
            self.manifest.write_text(json.dumps(package))
            before = self.snapshot()
            code, stdout, stderr = self.cli("apply", "--root", str(self.root),
                                            "--backup-dir", str(self.backup), "--confirm")
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("unsupported", stderr)
            self.assertEqual(before, self.snapshot())
            self.assertFalse(self.backup.exists())

    def test_invalid_receipt_hash_is_rejected_before_writes(self):
        self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        receipt_path = self.backup / "receipt.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["script_sha256"] = "not a SHA-256 hash"
        receipt_path.write_text(json.dumps(receipt))
        before = self.snapshot()
        code, stdout, stderr = self.cli("remove", "--root", str(self.root),
                                        "--backup-dir", str(self.backup), "--confirm")
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("receipt", stderr)
        self.assertEqual(before, self.snapshot())

    def test_apply_readback_idempotence_check_and_remove(self):
        result = self.run_overlay("apply", backup_dir=self.backup, confirm=True)
        self.assertEqual(result, "applied")
        target = self.assets / "webui-performance-singleflight-v1.js"
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        deployed = self.html.read_bytes()
        self.assertIn(b'integrity="sha256-', deployed)
        self.assertLess(deployed.index(b"webui-performance-singleflight-v1.js"),
                        deployed.index(b'type="module"'))
        self.assertEqual((self.backup / "index.html.original").read_bytes(), self.original)
        receipt = json.loads((self.backup / "receipt.json").read_bytes())
        self.assertEqual(receipt["root"], str(self.root.resolve()))
        self.assertEqual(receipt["version"], "0.7.22")
        self.assertEqual(receipt["original_html_sha256"], digest(self.original))
        self.assertEqual(receipt["deployed_html_sha256"], digest(deployed))
        self.assertEqual(receipt["script_sha256"], digest(target.read_bytes()))
        before = self.snapshot()
        self.assertEqual(self.run_overlay("apply", backup_dir=self.backup, confirm=True), "already applied")
        self.assertEqual(self.run_overlay("check", backup_dir=self.backup), "installed and verified")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.run_overlay("remove", backup_dir=self.backup, confirm=True), "removed")
        self.assertEqual(self.html.read_bytes(), self.original)
        self.assertFalse(target.exists())
        self.assertEqual((self.backup / "index.html.original").read_bytes(), self.original)
        self.assertEqual(json.loads((self.backup / "receipt.json").read_bytes()), receipt)
        self.assertEqual(self.run_overlay("apply", backup_dir=self.backup, confirm=True), "applied")


if __name__ == "__main__":
    unittest.main()
