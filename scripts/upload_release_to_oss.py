from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import oss2


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_type(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return "application/json; charset=utf-8"
    if path.suffix.lower() == ".zip":
        return "application/zip"
    return "application/octet-stream"


def public_url(base_url: str, object_key: str) -> str:
    return f"{base_url.rstrip('/')}/{object_key.lstrip('/')}"


def upload_file(bucket: oss2.Bucket, local_path: Path, object_key: str) -> None:
    headers = {"Content-Type": content_type(local_path)}
    bucket.put_object_from_file(object_key, str(local_path), headers=headers)
    print(f"uploaded {local_path.name} -> oss://{bucket.bucket_name}/{object_key}")


def main() -> None:
    version = require_env("RELEASE_VERSION")
    endpoint = require_env("ALIYUN_OSS_ENDPOINT")
    bucket_name = require_env("ALIYUN_OSS_BUCKET")
    access_key_id = require_env("ALIYUN_ACCESS_KEY_ID")
    access_key_secret = require_env("ALIYUN_ACCESS_KEY_SECRET")
    public_base_url = os.getenv(
        "ALIYUN_OSS_PUBLIC_BASE_URL",
        f"https://{bucket_name}.{endpoint.removeprefix('https://').removeprefix('http://')}",
    ).strip()

    full_zip = DIST / "LiepinRecruitingAgent-win64.zip"
    update_zip = DIST / "LiepinRecruitingAgent-update-win64.zip"
    for path in (full_zip, update_zip):
        if not path.exists():
            raise SystemExit(f"Missing package: {path}")

    release_prefix = f"releases/{version}"
    full_key = f"{release_prefix}/{full_zip.name}"
    update_key = f"{release_prefix}/{update_zip.name}"
    version_manifest_key = f"{release_prefix}/update.json"
    latest_manifest_key = "update.json"

    manifest = {
        "version": version,
        "channel": "stable",
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "update_url": public_url(public_base_url, update_key),
        "full_url": public_url(public_base_url, full_key),
        "sha256": sha256_file(update_zip),
        "full_sha256": sha256_file(full_zip),
        "notes": os.getenv("RELEASE_NOTES", "Windows 自动构建包"),
        "force": False,
    }

    manifest_path = DIST / "update.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, endpoint, bucket_name)

    upload_file(bucket, full_zip, full_key)
    upload_file(bucket, update_zip, update_key)
    upload_file(bucket, manifest_path, version_manifest_key)
    upload_file(bucket, manifest_path, latest_manifest_key)

    print("update manifest uploaded:")
    print(f"version={manifest['version']}")
    print(f"update_url={manifest['update_url']}")
    print(f"full_url={manifest['full_url']}")
    print(f"sha256={manifest['sha256']}")


if __name__ == "__main__":
    main()
