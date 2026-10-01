#!/usr/bin/env python3
"""Build a source-only ZIP from an explicit allowlist, without Git metadata."""
from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ('.gitattributes', '.gitignore', 'LICENSE', 'README.md', 'SKILL.md', 'agents/openai.yaml', 'assets/site/app.js', 'assets/site/index.html', 'assets/site/styles.css', 'references/collection-workflow.md', 'references/official-sources.json', 'references/schema.md', 'scripts/package_skill.py', 'scripts/shangan_schedule.py', 'scripts/snapshot_official.py', 'scripts/source_registry.py', 'tests/fixtures/candidate-duplicate-events.json', 'tests/fixtures/candidate-invalid-date.json', 'tests/fixtures/candidate-missing-evidence.json', 'tests/fixtures/candidate-review-mixed.json', 'tests/fixtures/candidate-unofficial-source.json', 'tests/fixtures/official-sources.json', 'tests/fixtures/published-one-exam.json', 'tests/test_cli.py', 'tests/test_packaging.py')

PATTERNS = {
    "private-key": r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    "service-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{15,}|AKIA[A-Z0-9]{16})\b",
    "personal-path": r"(?:/" r"Users/|/" r"home/|[A-Za-z]:\\Users\\)[^\s/\\<>]+",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "mobile-number": r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])",
    "credential-value": r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_+/=-]{12,}[\"']",
    "authenticated-url": r"https?://[^\s/:@]+:[^\s/@]+@",
}


def findings(text: str) -> list[str]:
    # Return rule names only; never echo suspected secret values.
    return [name for name, pattern in PATTERNS.items() if re.search(pattern, text)]


def package(root: Path, output: Path) -> Path:
    members = []
    for name in FILES:
        path = root / name
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError(f"Refusing symlink: {name}")
        body = path.read_bytes()
        hits = findings(body.decode("utf-8"))
        if hits:
            raise ValueError(f"Sensitive content in {name}: {', '.join(hits)}")
        members.append((name, body))
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "shangan-schedule.zip"
    checksum = output / "shangan-schedule.zip.sha256"
    if archive.exists() or checksum.exists():
        raise FileExistsError("Release output exists; choose a new --output directory")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, body in members:
            entry = zipfile.ZipInfo(f"shangan-schedule/{name}", (2020, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(entry, body)
    checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n", encoding="utf-8")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        result = package(ROOT, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"ERROR {error}\n")
    print(f"Packaged {len(FILES)} reviewed files: {result}")
