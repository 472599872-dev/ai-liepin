from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


APP_ID = "liepin-recruiting-agent"
LICENSE_VERSION = 1
MACHINE_ID_ALGORITHM = "machine:v2"
LICENSE_FILENAMES = ("license.json", "data/license.json")
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAk5V5KwLR4vyF8SXMDwn1
qyf7P+vEWgbKQPb6nxYDIp8oGLMdGszYIso72cLSMfM2KXSlU38/wz72R1yabdoh
0wdk/qrAtEWxw+AzJvXlP8ppkfXeFwCyo6kYzhYSUgbrikuzCa20ZuaDcHRJd11y
ptTiLI9SQGWsK1CYC6x7eYX9BNHGuY6SthovdgCursocsiGbmgvK8w1yROxezytj
4sjfKDmyhYjX371V98LPeYXvPOUVeVQrtYKNzJ2A2lFD5ZIJ4M76j0njwxKMR/+j
BrAi3o7r4M79jxpTUhNmX5zAocfTAhWyStJ7Au675hNaaaPJf51u9OOwNEzli7Mr
TPD4jTABFRP5XJlUZn/joS0+hhRNPKqxx0rsJg6+oddxOyVOjwtFVzFroLBLM8eY
l76n2lJcZkFxQskiqEICUKbRs4ruXeMGovSl5l5y6hNpP0+RiYOusBYbUcCnJxCG
+jEWxQ21H6PiO0zgBATti88qCi3jdqz/oQ/dgu9bJ9cbAgMBAAE=
-----END PUBLIC KEY-----
"""


@dataclass(frozen=True)
class LicenseCheck:
    ok: bool
    machine_id: str
    reason: str
    code: str
    path: Path | None = None
    data: dict[str, Any] | None = None


def _run_command(args: list[str], timeout: float = 2.0) -> str:
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


INVALID_MACHINE_VALUES = {
    "none",
    "null",
    "unknown",
    "to be filled by o.e.m.",
    "default string",
    "system serial number",
    "base board serial number",
    "not available",
    "not applicable",
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
}


def _clean_value(value: Any) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    if not text:
        return ""
    if text.lower() in INVALID_MACHINE_VALUES:
        return ""
    return text


def _windows_registry_machine_guid() -> str:
    if not sys.platform.startswith("win"):
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _typ = winreg.QueryValueEx(key, "MachineGuid")
            return _clean_value(value)
    except Exception:
        return ""


def _powershell_cim_value(class_name: str, field: str) -> str:
    if not sys.platform.startswith("win"):
        return ""
    command = (
        f"$value = (Get-CimInstance -ClassName {class_name} "
        f"| Select-Object -ExpandProperty {field} -ErrorAction SilentlyContinue); "
        "if ($null -ne $value) { [string]$value }"
    )
    return _clean_value(
        _run_command(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            timeout=3.0,
        )
    )


def _wmic_value(alias: str, field: str) -> str:
    output = _run_command(["wmic", alias, "get", field, "/value"])
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() == field.lower():
            cleaned = _clean_value(value)
            if cleaned:
                return cleaned
    lines = [_clean_value(line) for line in output.splitlines()]
    lines = [line for line in lines if line and line.lower() != field.lower()]
    return lines[0] if lines else ""


def _windows_hardware_value(alias: str, field: str, cim_class: str) -> str:
    return _wmic_value(alias, field) or _powershell_cim_value(cim_class, field)


def _mac_value(command: list[str], marker: str = "") -> str:
    output = _run_command(command)
    if not marker:
        return _clean_value(output)
    for line in output.splitlines():
        if marker not in line:
            continue
        if "=" in line:
            return _clean_value(line.split("=", 1)[1].strip().strip('"'))
        return _clean_value(line)
    return ""


def _clean_components(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    cleaned: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key, value in values:
        value = _clean_value(value)
        if not value:
            continue
        marker = (key, value)
        if marker in seen:
            continue
        seen.add(marker)
        cleaned.append(marker)
    return cleaned


def machine_components() -> list[tuple[str, str]]:
    system = platform.system().lower()
    os_name = platform.system()
    if system == "windows":
        machine_guid = _windows_registry_machine_guid()
        if machine_guid:
            return _clean_components([("os", os_name), ("machine_guid", machine_guid)])
        fallback_values = _clean_components(
            [
                ("os", os_name),
                ("cs_uuid", _windows_hardware_value("csproduct", "UUID", "Win32_ComputerSystemProduct")),
                ("baseboard_serial", _windows_hardware_value("baseboard", "SerialNumber", "Win32_BaseBoard")),
                ("bios_serial", _windows_hardware_value("bios", "SerialNumber", "Win32_BIOS")),
                ("cpu_id", _windows_hardware_value("cpu", "ProcessorId", "Win32_Processor")),
            ]
        )
        if len(fallback_values) > 1:
            return fallback_values
        return _clean_components([("os", os_name), ("computer_name", os.environ.get("COMPUTERNAME", ""))])
    elif system == "darwin":
        return _clean_components(
            [
                ("os", os_name),
                ("platform_uuid", _mac_value(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], "IOPlatformUUID")),
                ("serial", _mac_value(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], "IOPlatformSerialNumber")),
                ("hardware_uuid", _mac_value(["system_profiler", "SPHardwareDataType"], "Hardware UUID")),
            ]
        )
    elif system == "linux":
        values: list[tuple[str, str]] = [("os", os_name)]
        for name, path in (
            ("machine_id", "/etc/machine-id"),
            ("dbus_machine_id", "/var/lib/dbus/machine-id"),
            ("product_uuid", "/sys/class/dmi/id/product_uuid"),
            ("board_serial", "/sys/class/dmi/id/board_serial"),
        ):
            try:
                values.append((name, _clean_value(Path(path).read_text(encoding="utf-8", errors="ignore"))))
            except Exception:
                values.append((name, ""))
        return _clean_components(values)
    return _clean_components([("os", os_name), ("hostname", socket.gethostname())])


def current_machine_id() -> str:
    components = machine_components()
    raw = "\n".join(f"{key}={value}" for key, value in components)
    digest = hashlib.sha256(f"{APP_ID}:{MACHINE_ID_ALGORITHM}\n{raw}".encode("utf-8")).hexdigest().upper()
    return f"LPA-{digest[:8]}-{digest[8:16]}-{digest[16:24]}-{digest[24:32]}"


def machine_report() -> dict[str, Any]:
    components = machine_components()
    return {
        "machine_id": current_machine_id(),
        "algorithm": MACHINE_ID_ALGORITHM,
        "platform": platform.platform(),
        "component_count": len(components),
        "components": [{"name": key, "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]} for key, value in components],
        "diagnostics": {
            "hostname_sha256": hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:12],
        },
    }


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding_len = (-len(text)) % 4
    return base64.urlsafe_b64decode((text + ("=" * padding_len)).encode("ascii"))


def canonical_license_bytes(data: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in data.items() if key != "signature"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_license_payload(data: dict[str, Any], private_key_pem: bytes) -> dict[str, Any]:
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    payload = dict(data)
    payload.setdefault("app", APP_ID)
    payload.setdefault("version", LICENSE_VERSION)
    signature = private_key.sign(
        canonical_license_bytes(payload),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    payload["signature"] = _b64url_encode(signature)
    return payload


def verify_signature(data: dict[str, Any]) -> bool:
    signature_text = str(data.get("signature") or "")
    if not signature_text:
        return False
    public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)
    try:
        public_key.verify(
            _b64url_decode(signature_text),
            canonical_license_bytes(data),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError):
        return False


def default_license_paths(base_dir: Path | None = None) -> list[Path]:
    base = Path.cwd() if base_dir is None else Path(base_dir)
    paths: list[Path] = []
    env_path = os.environ.get("LIEPIN_LICENSE_FILE")
    if env_path:
        paths.append(Path(env_path))
    paths.extend(base / name for name in LICENSE_FILENAMES)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        marker = str(path.resolve()) if path.exists() else str(path)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(path)
    return unique


def _parse_date(value: str) -> date | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def validate_license_data(data: dict[str, Any], *, machine_id: str | None = None) -> LicenseCheck:
    machine_id = machine_id or current_machine_id()
    if data.get("app") != APP_ID:
        return LicenseCheck(False, machine_id, "授权文件不是本应用的授权。", "app_mismatch", data=data)
    if int(data.get("version") or 0) != LICENSE_VERSION:
        return LicenseCheck(False, machine_id, "授权文件版本不兼容。", "version_mismatch", data=data)
    licensed_machine = str(data.get("machine_id") or "").strip().upper()
    if licensed_machine != machine_id.upper():
        return LicenseCheck(False, machine_id, "授权文件不属于当前电脑。", "machine_mismatch", data=data)
    if not verify_signature(data):
        return LicenseCheck(False, machine_id, "授权文件签名无效，可能被篡改。", "bad_signature", data=data)
    today = datetime.now(timezone.utc).date()
    valid_from = _parse_date(str(data.get("valid_from") or ""))
    if valid_from and today < valid_from:
        return LicenseCheck(False, machine_id, f"授权尚未生效：{valid_from.isoformat()}。", "not_yet_valid", data=data)
    expires_at = _parse_date(str(data.get("expires_at") or ""))
    if expires_at and today > expires_at:
        return LicenseCheck(False, machine_id, f"授权已过期：{expires_at.isoformat()}。", "expired", data=data)
    return LicenseCheck(True, machine_id, "授权有效。", "ok", data=data)


def check_license(paths: list[Path] | None = None) -> LicenseCheck:
    machine_id = current_machine_id()
    candidates = paths or default_license_paths()
    found_invalid: LicenseCheck | None = None
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            found_invalid = LicenseCheck(False, machine_id, f"授权文件无法读取：{path}", "read_error", path=path)
            continue
        if not isinstance(data, dict):
            found_invalid = LicenseCheck(False, machine_id, f"授权文件格式错误：{path}", "format_error", path=path)
            continue
        result = validate_license_data(data, machine_id=machine_id)
        result = LicenseCheck(result.ok, result.machine_id, result.reason, result.code, path=path, data=data)
        if result.ok:
            return result
        found_invalid = result
    if found_invalid:
        return found_invalid
    return LicenseCheck(False, machine_id, "未找到 license.json。", "missing")


def write_signed_license(
    *,
    private_key_path: Path,
    output_path: Path,
    machine_id: str,
    customer: str,
    expires_at: str,
    features: list[str] | None = None,
    valid_from: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "app": APP_ID,
        "version": LICENSE_VERSION,
        "license_id": hashlib.sha256(f"{customer}|{machine_id}|{expires_at}".encode("utf-8")).hexdigest()[:16],
        "customer": customer,
        "machine_id": machine_id.strip().upper(),
        "issued_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at,
        "features": features or ["desktop", "ai_scoring", "auto_greeting"],
    }
    if valid_from:
        payload["valid_from"] = valid_from
    if note:
        payload["note"] = note
    signed = sign_license_payload(payload, private_key_path.read_bytes())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(signed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return signed
