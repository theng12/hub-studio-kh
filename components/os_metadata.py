"""Finder/OS directory entries are metadata, never application data."""
from __future__ import annotations

OS_METADATA_NAMES = frozenset({
    ".DS_Store", ".localized", "Icon\r", ".Spotlight-V100", ".fseventsd",
    ".Trashes", ".TemporaryItems", ".DocumentRevisions-V100", ".apdisk", ".VolumeIcon.icns",
})


def is_os_metadata(name: str) -> bool:
    """Ignore only known OS names and AppleDouble companions; leave them untouched."""
    return name in OS_METADATA_NAMES or name.startswith("._")
