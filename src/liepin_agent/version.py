from __future__ import annotations

import os
import sys
from pathlib import Path


DEFAULT_APP_VERSION = "dev"


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def current_version() -> str:
    env_version = os.getenv("LIEPIN_APP_VERSION", "").strip()
    if env_version:
        return env_version

    for path in (app_root() / "VERSION", Path(__file__).resolve().parents[2] / "VERSION"):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value

    return DEFAULT_APP_VERSION


APP_VERSION = current_version()
