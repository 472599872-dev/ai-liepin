from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from liepin_agent.license import current_machine_id, machine_report, write_signed_license


DEFAULT_PRIVATE_KEY = ROOT / "secrets" / "license_private_key.pem"


def resolve_expires_at(expires_at: str, days: int | None) -> str:
    if expires_at:
        return expires_at
    if days is None:
        raise SystemExit("请提供 --expires-at 或 --days。")
    if days <= 0:
        raise SystemExit("--days 必须大于 0。")
    return (date.today() + timedelta(days=days)).isoformat()


def cmd_machine_id(_args: argparse.Namespace) -> int:
    report = machine_report()
    print(report["machine_id"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_init_key(args: argparse.Namespace) -> int:
    private_key_path = Path(args.private_key)
    public_key_path = Path(args.public_key)
    if private_key_path.exists() and not args.force:
        raise SystemExit(f"私钥已存在：{private_key_path}。如需覆盖请加 --force。")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_key_path.parent.mkdir(parents=True, exist_ok=True)
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    print(f"私钥：{private_key_path}")
    print(f"公钥：{public_key_path}")
    print("注意：如果更换公钥，需要同步更新 src/liepin_agent/license.py 里的 PUBLIC_KEY_PEM 并重新打包。")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    machine_id = args.machine_id.strip().upper()
    expires_at = resolve_expires_at(args.expires_at, args.days)
    output = Path(args.output)
    signed = write_signed_license(
        private_key_path=Path(args.private_key),
        output_path=output,
        machine_id=machine_id,
        customer=args.customer,
        expires_at=expires_at,
        valid_from=args.valid_from,
        features=[item.strip() for item in args.features.split(",") if item.strip()],
        note=args.note,
    )
    print(f"已生成：{output}")
    print(f"客户：{signed['customer']}")
    print(f"机器码：{signed['machine_id']}")
    print(f"有效期至：{signed['expires_at']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="猎聘招聘智能体离线授权工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("machine-id", help="显示当前电脑机器码").set_defaults(func=cmd_machine_id)

    init_parser = subparsers.add_parser("init-key", help="生成一套新的授权签名密钥")
    init_parser.add_argument("--private-key", default=str(DEFAULT_PRIVATE_KEY), help="私钥输出路径")
    init_parser.add_argument("--public-key", default=str(ROOT / "secrets" / "license_public_key.pem"), help="公钥输出路径")
    init_parser.add_argument("--force", action="store_true", help="覆盖已有私钥")
    init_parser.set_defaults(func=cmd_init_key)

    gen_parser = subparsers.add_parser("generate", help="根据机器码生成 license.json")
    gen_parser.add_argument("--machine-id", required=True, help="目标电脑机器码")
    gen_parser.add_argument("--customer", required=True, help="客户/使用者名称")
    gen_parser.add_argument("--expires-at", default="", help="到期日期，格式 YYYY-MM-DD；未提供时可使用 --days")
    gen_parser.add_argument("--days", type=int, default=None, help="从今天起授权多少天；例如 365")
    gen_parser.add_argument("--valid-from", default="", help="生效日期，格式 YYYY-MM-DD，可选")
    gen_parser.add_argument("--features", default="desktop,ai_scoring,auto_greeting", help="功能列表，逗号分隔")
    gen_parser.add_argument("--note", default="", help="备注")
    gen_parser.add_argument("--private-key", default=str(DEFAULT_PRIVATE_KEY), help="授权私钥路径")
    gen_parser.add_argument("--output", default="license.json", help="license.json 输出路径")
    gen_parser.set_defaults(func=cmd_generate)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
