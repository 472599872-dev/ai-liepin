from __future__ import annotations

from liepin_agent.updater import UpdateManifest, check_for_update, is_newer_version


def test_is_newer_version() -> None:
    assert is_newer_version("v0.1.8", "v0.1.7")
    assert is_newer_version("0.2.0", "v0.1.9")
    assert not is_newer_version("v0.1.7", "v0.1.7")
    assert not is_newer_version("v0.1.6", "v0.1.7")
    assert not is_newer_version("manual-12", "v0.1.7")


def test_check_for_update_skips_dev_version(monkeypatch) -> None:
    manifest = UpdateManifest(
        version="v0.1.8",
        update_url="https://example.test/update.zip",
        sha256="a" * 64,
    )
    monkeypatch.setattr("liepin_agent.updater.load_manifest", lambda url, timeout: manifest)

    result = check_for_update("dev")

    assert result.manifest == manifest
    assert not result.update_available
    assert "开发版本" in result.reason
