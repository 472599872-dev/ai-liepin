from liepin_agent import license as license_mod


def test_windows_machine_id_prefers_machine_guid(monkeypatch) -> None:
    monkeypatch.setattr(license_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(license_mod.sys, "platform", "win32")
    monkeypatch.setattr(license_mod, "_windows_registry_machine_guid", lambda: "GUID-123")
    monkeypatch.setattr(license_mod, "_windows_hardware_value", lambda *_args: "CHANGING-HARDWARE")
    monkeypatch.setattr(license_mod.socket, "gethostname", lambda: "host-a")

    first = license_mod.current_machine_id()
    components = license_mod.machine_components()

    monkeypatch.setattr(license_mod, "_windows_hardware_value", lambda *_args: "OTHER-HARDWARE")
    monkeypatch.setattr(license_mod.socket, "gethostname", lambda: "host-b")

    assert license_mod.current_machine_id() == first
    assert components == [("os", "Windows"), ("machine_guid", "GUID-123")]


def test_windows_machine_id_fallback_ignores_hostname(monkeypatch) -> None:
    monkeypatch.setattr(license_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(license_mod.sys, "platform", "win32")
    monkeypatch.setattr(license_mod, "_windows_registry_machine_guid", lambda: "")

    values = {
        ("csproduct", "UUID", "Win32_ComputerSystemProduct"): "CS-UUID",
        ("baseboard", "SerialNumber", "Win32_BaseBoard"): "BOARD-SERIAL",
        ("bios", "SerialNumber", "Win32_BIOS"): "BIOS-SERIAL",
        ("cpu", "ProcessorId", "Win32_Processor"): "CPU-ID",
    }
    monkeypatch.setattr(license_mod, "_windows_hardware_value", lambda *args: values.get(tuple(args), ""))
    monkeypatch.setattr(license_mod.socket, "gethostname", lambda: "host-a")

    first = license_mod.current_machine_id()

    monkeypatch.setattr(license_mod.socket, "gethostname", lambda: "host-b")

    assert license_mod.current_machine_id() == first
    assert ("hostname", "host-b") not in license_mod.machine_components()
