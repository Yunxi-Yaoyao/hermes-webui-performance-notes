#!/usr/bin/env python3
"""Explicit, fail-closed frontend overlay installer (Python standard library).

Only index.html and the named overlay asset are changed in the package. Backups
and receipts live in a user-selected directory outside the package. Each file
publication is atomic; this is not a multi-file transaction or a package-manager
lock. Do not run alongside npm upgrades or other frontend writers.
"""

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile


SUPPORTED = {
    "0.7.22": {
        "bundle": "index-D5atb6oK.js",
        "sha256": "696b73797001d5a36421e2410ee1c8228e922f6cab725222a65ddcb24cfbd64b",
    },
}
SOURCE = Path(__file__).resolve().parents[1] / "patches" / "singleflight.js"
ASSET = "webui-performance-singleflight-v1.js"
BEGIN = "<!-- webui-performance-singleflight:v1 begin -->"
END = "<!-- webui-performance-singleflight:v1 end -->"


class OverlayError(Exception):
    """A failed safety check; the CLI reports these errors with status 2."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def block(script_sha):
    integrity = base64.b64encode(bytes.fromhex(script_sha)).decode("ascii")
    return (f'{BEGIN}\n<script src="/assets/js/{ASSET}" '
            f'integrity="sha256-{integrity}" crossorigin="anonymous"></script>\n'
            f'{END}\n').encode("ascii")


class IndexParser(HTMLParser):
    """Recognize the expected entrypoint, not script-shaped text in comments."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.has_base = False

    def handle_starttag(self, tag, attrs):
        if tag == "base":
            self.has_base = True
        if tag == "script":
            self.scripts.append(dict(attrs))


def deployed_html(original, bundle, script_sha):
    """Insert exactly one owned block without reserializing the original HTML."""
    pattern = rb'<script\s+type="module"\s+crossorigin\s+src="/assets/js/'
    pattern += re.escape(bundle.encode("ascii")) + rb'"></script>'
    matches = list(re.finditer(pattern, original))
    parser = IndexParser()
    parser.feed(original.decode("utf-8"))
    parser.close()
    external = [s for s in parser.scripts if "src" in s]
    modules = [s for s in parser.scripts if s.get("type") == "module"]
    if (len(matches) != 1 or b"singleflight" in original.lower() or parser.has_base
            or len(external) != 1 or len(modules) != 1
            or external[0] != modules[0]
            or external[0].get("src") != f'/assets/js/{bundle}'):
        raise OverlayError("unknown HTML: expected one unmodified main module and no overlay")
    offset = matches[0].start()
    return original[:offset] + block(script_sha) + original[offset:]


def publish(path, data, *, replace=False, mode=0o644):
    """Write a sibling temporary file, then atomically publish its complete bytes.

    New files use link rather than replace so an unexpected target is never
    overwritten. Temporary files are always removed, including on failure.
    """
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def safe_file(path, boundary, *, allow_missing=False):
    """Reject symlinks and non-regular files before making any writes."""
    current = boundary
    parts = path.relative_to(boundary).parts
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            raise OverlayError(f"refusing symlink: {current}")
        if index < len(parts) - 1 and current.exists() and not current.is_dir():
            raise OverlayError(f"not a directory: {current}")
    if not path.exists() and allow_missing:
        return
    if not path.is_file():
        raise OverlayError(f"missing or non-regular file: {path}")


def receipt_for(root, version, rule, original, deployed, script_sha):
    return {
        "schema": 1,
        "root": str(root),
        "version": version,
        "bundle_sha256": rule["sha256"],
        "original_html_sha256": sha256(original),
        "deployed_html_sha256": sha256(deployed),
        "script_sha256": script_sha,
    }


def run(action, *, root, source=SOURCE, supported=None, backup_dir=None, confirm=False):
    """Run against an explicit package root; supported is injectable for tests."""
    if action not in ("apply", "check", "remove"):
        raise OverlayError(f"unknown action: {action}")
    if action in ("apply", "remove"):
        if not confirm:
            raise OverlayError(f"{action} requires --confirm")
        if backup_dir is None:
            raise OverlayError(f"{action} requires --backup-dir outside the package root")
    root = Path(root).resolve(strict=True)
    safe_file(root / "package.json", root)
    package = json.loads((root / "package.json").read_bytes())
    if not isinstance(package, dict):
        raise OverlayError("unsupported package metadata: expected a JSON object")
    rules = SUPPORTED if supported is None else supported
    version = package.get("version")
    if (package.get("name") != "hermes-web-ui"
            or not isinstance(version, str) or version not in rules):
        raise OverlayError(f"unsupported package/version: {package.get('name')} {version}")
    rule = rules[version]
    assets = root / "dist/client/assets/js"
    safe_file(assets / rule["bundle"], root)
    if sha256((assets / rule["bundle"]).read_bytes()) != rule["sha256"]:
        raise OverlayError("unsupported main bundle SHA-256 hash")
    html_path = root / "dist/client/index.html"
    target = assets / ASSET
    safe_file(html_path, root)
    safe_file(target, root, allow_missing=True)
    current = html_path.read_bytes()
    if backup_dir is None:
        if BEGIN.encode() not in current and not target.exists():
            raise OverlayError("overlay missing: not installed")
        raise OverlayError("--backup-dir is required to verify the installed receipt")
    backup = Path(backup_dir).resolve()
    if backup.is_relative_to(root):
        raise OverlayError("--backup-dir must be outside the package root")
    if backup.exists() and not backup.is_dir():
        raise OverlayError("--backup-dir must be a directory")
    receipt_path = backup / "receipt.json"
    original_path = backup / "index.html.original"
    safe_file(receipt_path, backup, allow_missing=True)
    safe_file(original_path, backup, allow_missing=True)
    if original_path.exists() != receipt_path.exists():
        raise OverlayError("incomplete backup: both original HTML and receipt are required")
    managed = BEGIN.encode() in current or target.exists()
    if not managed and action != "apply":
        raise OverlayError("overlay missing: not installed")

    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_bytes())
        if (not isinstance(receipt, dict)
                or not isinstance(receipt.get("script_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", receipt["script_sha256"])):
            raise OverlayError("invalid receipt: missing or malformed script SHA-256 hash")
        original = original_path.read_bytes()
        script_sha = receipt["script_sha256"]
        deployed = deployed_html(original, rule["bundle"], script_sha)
        if receipt != receipt_for(root, version, rule, original, deployed, script_sha):
            raise OverlayError("receipt root/version/hash mismatch")
    elif managed:
        raise OverlayError("unmanaged target HTML/script: receipt is missing")
    else:
        original = current
        script_sha = sha256(Path(source).read_bytes())
        deployed = deployed_html(original, rule["bundle"], script_sha)
        receipt = receipt_for(root, version, rule, original, deployed, script_sha)

    if managed:
        if current != deployed or not target.is_file() or sha256(target.read_bytes()) != script_sha:
            raise OverlayError("deployed HTML/script hash mismatch; refusing changes")
        if action == "check":
            return "installed and verified"
        if action == "apply":
            if sha256(Path(source).read_bytes()) != script_sha:
                raise OverlayError("source script hash changed; refusing overwrite")
            return "already applied"
        publish(html_path, original, replace=True, mode=html_path.stat().st_mode & 0o777)
        target.unlink()
        if html_path.read_bytes() != original or target.exists():
            raise OverlayError("remove readback failed")
        return "removed"

    if action != "apply":
        raise OverlayError("overlay missing: not installed")
    if current != original:
        raise OverlayError("original HTML hash mismatch")
    script = Path(source).read_bytes()
    if sha256(script) != script_sha:
        raise OverlayError("source script hash changed")
    if not receipt_path.exists():
        backup.mkdir(parents=True, exist_ok=True)
        publish(original_path, original, mode=0o600)
        publish(receipt_path, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(), mode=0o600)
    publish(target, script)
    try:
        if sha256(target.read_bytes()) != script_sha:
            raise OverlayError("script readback hash mismatch")
        publish(html_path, deployed, replace=True, mode=html_path.stat().st_mode & 0o777)
    except Exception:
        if target.is_file() and target.read_bytes() == script:
            target.unlink()
        raise
    if html_path.read_bytes() != deployed:
        raise OverlayError("HTML readback hash mismatch")
    return "applied"


def main(argv=None, *, supported=None, source=SOURCE):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("apply", "check", "remove"))
    parser.add_argument("--root", required=True, type=Path, help="explicit hermes-web-ui npm package root")
    parser.add_argument("--backup-dir", type=Path, help="external backup directory; required for mutations and installed checks")
    parser.add_argument("--confirm", action="store_true", help="authorize apply/remove file changes")
    args = parser.parse_args(argv)
    try:
        result = run(args.action, root=args.root, backup_dir=args.backup_dir,
                     confirm=args.confirm, supported=supported, source=source)
    except (OverlayError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"frontend-overlay: {exc}", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
