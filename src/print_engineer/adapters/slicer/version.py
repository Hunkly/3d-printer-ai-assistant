"""Slicer version detection helpers.

The slicers do not support a clean ``--version`` flag (OrcaSlicer replies
``Invalid option --version``; Bambu Studio ignores it), so versions are
recovered from:

- the Bambu Studio ``--help`` banner (``BambuStudio-02.06.00.51:``)
- the Windows registry ``DisplayVersion`` of the uninstall key
- a byte scan of the installed binaries (``OrcaSlicer 2.3.2`` appears in the
  ``OrcaSlicer.dll`` PE image)
"""

from __future__ import annotations

import re
from pathlib import Path

from print_engineer.errors import SlicerError

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?")
_BAMBU_BANNER_RE = re.compile(r"BambuStudio-(\d+\.\d+\.\d+(?:\.\d+)?)")
_BINARY_ORCA_RE = re.compile(rb"OrcaSlicer[/\s](\d+\.\d+(?:\.\d+)+)")


def parse_version(version: str) -> tuple[int, int, int, int] | None:
    """Parse a dotted version string into an ordered tuple of ints."""
    match = _VERSION_RE.search(version)
    if not match:
        return None
    groups = match.groups()
    major = int(groups[0])
    minor = int(groups[1])
    patch = int(groups[2]) if groups[2] is not None else 0
    build = int(groups[3]) if groups[3] is not None else 0
    return (major, minor, patch, build)


def version_tuple(version: str) -> tuple[int, int, int, int]:
    """Return a comparable tuple for *version*, or raise ``SlicerError``."""
    parsed = parse_version(version)
    if parsed is None:
        raise SlicerError(f"Unparseable version string: {version!r}")
    return parsed


def version_gte(version: str, minimum: str) -> bool:
    """True if *version* is >= *minimum* (both dotted version strings)."""
    return version_tuple(version) >= version_tuple(minimum)


def parse_bambu_banner(text: str) -> str | None:
    """Extract the version from a Bambu Studio ``--help`` banner."""
    match = _BAMBU_BANNER_RE.search(text)
    return match.group(1) if match else None


def scan_binary_for_version(binary: Path) -> str | None:
    """Byte-scan *binary* for an ``OrcaSlicer x.y.z`` string."""
    try:
        data = binary.read_bytes()
    except OSError:
        return None
    match = _BINARY_ORCA_RE.search(data)
    if not match:
        return None
    return match.group(1).decode("ascii")


def _registry_display_version(key_path: str) -> str | None:
    """Read the ``DisplayVersion`` value under the given uninstall key path."""
    try:
        import winreg  # only available on Windows
    except ImportError:
        return None

    roots = (
        winreg.HKEY_CURRENT_USER,
        winreg.HKEY_LOCAL_MACHINE,
        winreg.HKEY_LOCAL_MACHINE,
    )
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for root, view in zip(roots, views, strict=True):
        try:
            with winreg.OpenKey(root, key_path, access=winreg.KEY_READ | view) as key:
                value, _ = winreg.QueryValueEx(key, "DisplayVersion")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except OSError:
            continue
    return None


def registry_display_version(installer_name: str) -> str | None:
    """Look up an uninstall ``DisplayVersion`` by installer name substring."""
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        import winreg  # only available on Windows
    except ImportError:
        return None

    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for root in roots:
        for view in views:
            try:
                with winreg.OpenKey(root, uninstall, access=winreg.KEY_READ | view) as base:
                    index = 0
                    while True:
                        try:
                            name = winreg.EnumKey(base, index)
                        except OSError:
                            break
                        index += 1
                        if installer_name.lower() not in name.lower():
                            continue
                        with winreg.OpenKey(base, name) as sub:
                            try:
                                value, _ = winreg.QueryValueEx(sub, "DisplayVersion")
                            except OSError:
                                continue
                        if isinstance(value, str) and value.strip():
                            return value.strip()
            except OSError:
                continue
    return None


def detect_orca_version(install_dir: Path) -> tuple[str | None, str | None]:
    """Detect the OrcaSlicer version.

    Returns ``(version, source)`` where source is one of ``"registry"``,
    ``"binary"``, or ``None``. Registry is cheap; the binary scan is the
    reliable fallback when no uninstall key exists.
    """
    version = registry_display_version("OrcaSlicer")
    if version:
        return version, "registry"
    for candidate in (install_dir / "OrcaSlicer.dll", install_dir / "orca-slicer.exe"):
        if candidate.is_file():
            version = scan_binary_for_version(candidate)
            if version:
                return version, "binary"
    return None, None
