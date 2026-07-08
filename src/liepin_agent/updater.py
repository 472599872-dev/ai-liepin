from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from liepin_agent.version import app_root


DEFAULT_UPDATE_MANIFEST_URL = "https://liepinaoyi.oss-cn-beijing.aliyuncs.com/update.json"


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    update_url: str
    sha256: str
    full_url: str = ""
    full_sha256: str = ""
    notes: str = ""
    force: bool = False


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    manifest: UpdateManifest | None
    update_available: bool
    reason: str = ""


def _version_tuple(version: str) -> tuple[int, ...] | None:
    value = str(version or "").strip().lower()
    value = value[1:] if value.startswith("v") else value
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", value):
        return None
    parts = [int(part) for part in value.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(remote_version: str, current_version: str) -> bool:
    remote = _version_tuple(remote_version)
    current = _version_tuple(current_version)
    if remote is None or current is None:
        return False
    return remote > current


def load_manifest(url: str = DEFAULT_UPDATE_MANIFEST_URL, timeout: int = 8) -> UpdateManifest:
    request = urllib.request.Request(url, headers={"User-Agent": "LiepinRecruitingAgent-Updater/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    manifest = UpdateManifest(
        version=str(data.get("version") or "").strip(),
        update_url=str(data.get("update_url") or "").strip(),
        sha256=str(data.get("sha256") or "").strip().lower(),
        full_url=str(data.get("full_url") or "").strip(),
        full_sha256=str(data.get("full_sha256") or "").strip().lower(),
        notes=str(data.get("notes") or "").strip(),
        force=bool(data.get("force")),
    )
    if not manifest.version or not manifest.update_url or not manifest.sha256:
        raise ValueError("更新清单缺少 version/update_url/sha256。")
    return manifest


def check_for_update(
    current_version: str,
    url: str = DEFAULT_UPDATE_MANIFEST_URL,
    timeout: int = 8,
) -> UpdateCheckResult:
    manifest = load_manifest(url, timeout=timeout)
    if _version_tuple(current_version) is None:
        return UpdateCheckResult(current_version, manifest, False, "当前是开发版本，跳过自动更新比较。")
    available = is_newer_version(manifest.version, current_version)
    reason = "发现新版本。" if available else "当前已是最新版本。"
    return UpdateCheckResult(current_version, manifest, available, reason)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def data_dir() -> Path:
    path = app_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_update_package(
    manifest: UpdateManifest,
    *,
    timeout: int = 30,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    update_dir = data_dir() / "updates" / re.sub(r"[^A-Za-z0-9_.-]+", "_", manifest.version)
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / "LiepinRecruitingAgent-update-win64.zip"
    if target.exists() and sha256_file(target).lower() == manifest.sha256:
        if progress:
            progress(target.stat().st_size, target.stat().st_size)
        return target

    part = target.with_suffix(".zip.part")
    request = urllib.request.Request(manifest.update_url, headers={"User-Agent": "LiepinRecruitingAgent-Updater/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response, part.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        written = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            written += len(chunk)
            if progress:
                progress(written, total)

    actual_sha = sha256_file(part).lower()
    if actual_sha != manifest.sha256:
        part.unlink(missing_ok=True)
        raise ValueError(f"更新包校验失败：expected={manifest.sha256}, actual={actual_sha}")
    part.replace(target)
    return target


def auto_install_supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _powershell_update_script() -> str:
    return r'''
param(
    [Parameter(Mandatory=$true)][string]$PackagePath,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ExePath,
    [Parameter(Mandatory=$true)][int]$PidToWait,
    [Parameter(Mandatory=$true)][string]$Version
)

$ErrorActionPreference = "Stop"
Start-Sleep -Milliseconds 500
while (Get-Process -Id $PidToWait -ErrorAction SilentlyContinue) {
    Start-Sleep -Milliseconds 500
}

$safeVersion = $Version -replace '[^\w\.-]', '_'
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $InstallDir "data\backups\before_update_$safeVersion`_$stamp"
New-Item -ItemType Directory -Force $backupDir | Out-Null

foreach ($relative in @(".env", "license.json", "data\license.json", "data\app.db")) {
    $source = Join-Path $InstallDir $relative
    if (Test-Path $source) {
        $target = Join-Path $backupDir ($relative -replace '[\\/:]', '_')
        Copy-Item $source $target -Force
    }
}

Expand-Archive -LiteralPath $PackagePath -DestinationPath $InstallDir -Force
Start-Process -FilePath $ExePath -WorkingDirectory $InstallDir
'''


def launch_external_installer(package_path: Path, manifest: UpdateManifest) -> Path:
    if not auto_install_supported():
        raise RuntimeError("当前运行方式不支持自动覆盖安装。")
    root = app_root()
    script_dir = data_dir() / "updates"
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / f"install_update_{int(time.time())}.ps1"
    script_path.write_text(_powershell_update_script(), encoding="utf-8")

    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-PackagePath",
            str(package_path),
            "-InstallDir",
            str(root),
            "-ExePath",
            str(Path(sys.executable).resolve()),
            "-PidToWait",
            str(os.getpid()),
            "-Version",
            manifest.version,
        ],
        cwd=str(root),
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return script_path


def reveal_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
