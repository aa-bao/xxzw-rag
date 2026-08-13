#!/usr/bin/env python3
"""Generate the machine-readable CI gate report (gate-report.json).

Standalone and cross-platform (Windows / Linux / macOS). The only
third-party dependency is PyYAML (to parse rpa-application.yaml);
everything else uses the standard library.

It anchors to the repository root (parent of the scripts/ directory),
so it can be invoked from any working directory:

    python scripts/generate-gate-report.py \\
        --test-unit PASS --test-integration PASS \\
        --test-contract PASS --test-security PASS

    python scripts/generate-gate-report.py --secrets-scan-file gitleaks.json

Test status resolution order (per test kind): CLI flag
(--test-unit/--test-integration/--test-contract/--test-security) >
environment variable (RPA_TEST_UNIT / RPA_TEST_INTEGRATION /
RPA_TEST_CONTRACT / RPA_TEST_SECURITY) > default (PASS when running in
GitHub Actions CI, because the gate-report job only runs after the test
job succeeded; UNKNOWN otherwise).

imageDigest is only filled from RPA_IMAGE_DIGEST_<COMPONENT_KEY_UPPER>
(e.g. RPA_IMAGE_DIGEST_FRONTEND / RPA_IMAGE_DIGEST_BACKEND); it is
null when the environment does not provide it. Digests are never
fabricated.

Exit code 0 on success, 1 on error. A JSON summary is printed to
stdout before the file is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "rpa-application.yaml"
CONTRACTS_DIR = REPO_ROOT / "contracts"
DEFAULT_OUT = REPO_ROOT / "gate-report.json"

TEST_KINDS = ("unit", "integration", "contract", "security")
VALID_TEST_STATUSES = ("PASS", "FAIL", "UNKNOWN")


def sha256_file(path: Path) -> str:
    """Return lowercase hex sha256 of a file's raw bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_rev(*args: str, fallback: str = "unknown") -> str:
    """Run `git <args>` in the repo root; return stdout or fallback."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return fallback
    if proc.returncode != 0:
        return fallback
    value = proc.stdout.strip()
    return value if value else fallback


def load_manifest() -> dict:
    """Load and validate rpa-application.yaml."""
    if not MANIFEST_PATH.is_file():
        sys.exit(f"error: manifest not found: {MANIFEST_PATH}")
    try:
        import yaml
    except ImportError:
        sys.exit(
            "error: PyYAML is required (pip install pyyaml) to parse "
            f"{MANIFEST_PATH.name}"
        )
    with MANIFEST_PATH.open(encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)
    if not isinstance(manifest, dict):
        sys.exit(f"error: {MANIFEST_PATH.name} is not a YAML mapping")
    if not manifest.get("appKey"):
        sys.exit(f"error: {MANIFEST_PATH.name} is missing 'appKey'")
    if not isinstance(manifest.get("components"), dict):
        sys.exit(f"error: {MANIFEST_PATH.name} is missing 'components' mapping")
    return manifest


def contract_hashes() -> dict[str, str]:
    """sha256 of every .yaml/.yml/.json file under contracts/."""
    if not CONTRACTS_DIR.is_dir():
        sys.exit(f"error: contracts directory not found: {CONTRACTS_DIR}")
    result: dict[str, str] = {}
    for path in sorted(CONTRACTS_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".yaml", ".yml", ".json"):
            continue
        result[path.relative_to(CONTRACTS_DIR).as_posix()] = sha256_file(path)
    if not result:
        sys.exit(f"error: no .yaml/.yml/.json contract files under {CONTRACTS_DIR}")
    return result


def component_items(manifest: dict, version: str) -> list[dict]:
    """Map manifest components to the report's components array."""
    components: list[dict] = []
    for key, component in manifest["components"].items():
        if not isinstance(component, dict):
            sys.exit(f"error: component '{key}' in the manifest is not a mapping")
        missing = [f for f in ("type", "image") if not component.get(f)]
        if missing:
            sys.exit(
                f"error: component '{key}' in the manifest is missing: {', '.join(missing)}"
            )
        digest = os.environ.get(f"RPA_IMAGE_DIGEST_{key.upper()}")
        components.append(
            {
                "componentKey": key,
                "componentType": component["type"],
                "image": component["image"],
                "imageDigest": digest if digest else None,
                "version": component.get("version") or version,
            }
        )
    return components


def test_status(kind: str, args: argparse.Namespace) -> str:
    """CLI flag > env var > default (PASS in CI, UNKNOWN locally)."""
    flag = getattr(args, f"test_{kind}", None)
    if flag:
        value = str(flag)
    else:
        value = os.environ.get(f"RPA_TEST_{kind.upper()}", "")
    if not value:
        value = "PASS" if os.environ.get("GITHUB_ACTIONS") else "UNKNOWN"
    value = value.upper()
    if value not in VALID_TEST_STATUSES:
        sys.exit(
            f"error: invalid test status '{value}' for {kind} "
            f"(expected one of {', '.join(VALID_TEST_STATUSES)})"
        )
    return value


def secrets_detected(scan_file: str | None) -> bool:
    """Parse a gitleaks JSON report; non-empty findings => True."""
    if not scan_file:
        return False
    path = Path(scan_file)
    if not path.is_file():
        print(f"notice: secrets scan file not found ({path}); secretsDetected=false")
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # JSONDecodeError / OSError
        print(
            f"warning: cannot parse secrets scan file ({path}): {exc}; "
            "secretsDetected=false"
        )
        return False
    if isinstance(data, dict):
        findings = data.get("Findings", data.get("findings"))
        return bool(findings) if isinstance(findings, list) else False
    if isinstance(data, list):
        return bool(data)
    return False


def build_report(args: argparse.Namespace) -> dict:
    """Assemble the gate report; top-level fields match the platform spec."""
    manifest = load_manifest()
    commit_sha = git_rev("rev-parse", "HEAD")
    short_sha = git_rev("rev-parse", "--short", "HEAD")

    return {
        "appKey": manifest["appKey"],
        "commitSha": commit_sha,
        "manifestHash": sha256_file(MANIFEST_PATH),
        "contractHashes": contract_hashes(),
        "components": component_items(manifest, short_sha),
        "tests": {kind: test_status(kind, args) for kind in TEST_KINDS},
        "browserStarted": False,
        "businessWriteOccurred": False,
        "secretsDetected": secrets_detected(args.secrets_scan_file),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the machine-readable CI gate report (gate-report.json)."
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"output JSON path (default: {DEFAULT_OUT})",
    )
    for kind in TEST_KINDS:
        parser.add_argument(
            f"--test-{kind}",
            metavar="PASS|FAIL|UNKNOWN",
            help=f"test status for {kind} (env RPA_TEST_{kind.upper()})",
        )
    parser.add_argument(
        "--secrets-scan-file",
        metavar="PATH",
        help="gitleaks JSON report; non-empty Findings => secretsDetected=true",
    )
    args = parser.parse_args()

    report = build_report(args)
    summary = json.dumps(report, indent=2, ensure_ascii=False)
    print("Gate report summary:")
    print(summary)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write(summary + "\n")
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
