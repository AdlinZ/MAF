"""Collect license files for Python distributions bundled in the release."""

from __future__ import annotations

import argparse
import re
import shutil
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path


PACKAGES = (
    "numpy",
    "opencv-python",
    "Pillow",
    "PyAutoGUI",
    "pytesseract",
    "pystray",
    "winotify",
    "pymsgbox",
    "pytweening",
    "pyscreeze",
    "pygetwindow",
    "mouseinfo",
    "six",
    "pyrect",
    "pyperclip",
    "pyinstaller",
)

LICENSE_PATTERN = re.compile(r"(^|/)(license|copying|notice|authors?)(\.|$)", re.IGNORECASE)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def collect_package(package: str, destination: Path) -> str:
    try:
        dist = distribution(package)
    except PackageNotFoundError:
        return f"- {package}: not installed"

    canonical_name = dist.metadata.get("Name", package)
    version = dist.version
    target = destination / f"{safe_name(canonical_name)}-{safe_name(version)}"
    target.mkdir(parents=True, exist_ok=True)

    candidates = []
    license_files = dist.metadata.get_all("License-File") or []
    for relative in license_files:
        candidate = dist.locate_file(relative)
        if candidate.is_file():
            candidates.append(candidate)

    if not candidates:
        for relative in dist.files or []:
            normalized = str(relative).replace("\\", "/")
            if ".dist-info/" in normalized and LICENSE_PATTERN.search(normalized):
                candidate = dist.locate_file(relative)
                if candidate.is_file():
                    candidates.append(candidate)

    copied = []
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        output = target / safe_name(candidate.name)
        shutil.copy2(candidate, output)
        copied.append(output.name)

    homepage = dist.metadata.get("Home-page") or dist.metadata.get("Project-URL") or ""
    declared_license = dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See license files"
    if "\n" in declared_license or len(declared_license) > 160:
        declared_license = "See included license files"
    details = [
        f"Name: {canonical_name}",
        f"Version: {version}",
        f"Declared license: {declared_license}",
    ]
    if homepage:
        details.append(f"Project: {homepage}")
    details.append(f"Collected files: {', '.join(copied) if copied else 'none found'}")
    (target / "PACKAGE.txt").write_text("\n".join(details) + "\n", encoding="utf-8")
    return f"- {canonical_name} {version}: {len(copied)} license file(s)"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    results = [collect_package(package, destination) for package in PACKAGES]
    (destination / "README.txt").write_text(
        "Third-party license files included with the MAF Windows release.\n\n"
        + "\n".join(results)
        + "\n",
        encoding="utf-8",
    )
    print("\n".join(results))


if __name__ == "__main__":
    main()
