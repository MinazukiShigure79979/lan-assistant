"""Single source of truth for the Lan.Assistant version.

Release scripts read this file.  Keep the numeric parts in sync with
DISPLAY_VERSION: 1.0.0 -> v1.0.
"""
from __future__ import annotations

VERSION = "1.0.0"
DISPLAY_VERSION = "v1.0"
INSTALLER_VERSION = "1.0.0.0"
BUILD_CHANNEL = "stable"


def as_dict() -> dict[str, str]:
    return {
        "version": VERSION,
        "display_version": DISPLAY_VERSION,
        "installer_version": INSTALLER_VERSION,
        "channel": BUILD_CHANNEL,
    }
