from __future__ import annotations

from liepin_agent.updater import UpdateManifest, check_for_update, is_newer_version

from liepin_agent.desktop import _safe_download_filename, _unique_download_name


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


def test_download_filename_helpers(tmp_path) -> None:
    assert _safe_download_filename('a/b:c*简历?.pdf') == "a_b_c_简历_.pdf"
    assert _safe_download_filename("   ") == "download"

    (tmp_path / "简历.pdf").write_text("old", encoding="utf-8")

    assert _unique_download_name(tmp_path, "简历.pdf") == "简历 (1).pdf"
    assert _unique_download_name(tmp_path, "新简历.pdf") == "新简历.pdf"
