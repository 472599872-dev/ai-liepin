from __future__ import annotations

import argparse

from rich.console import Console

from liepin_agent.runner import RecruitingAgent, decisions_to_json
from liepin_agent.settings import load_config, load_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="liepin-agent")
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", default="config/example_job.yml", help="岗位配置文件路径")
    parser.add_argument("--config", default="config/example_job.yml", help="岗位配置文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", parents=[config_parent], help="打开猎聘并保存登录态")
    run_parser = subparsers.add_parser("run", parents=[config_parent], help="搜索、评分并按阈值打招呼")
    run_parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(args.config)
    env = load_env()
    console = Console()
    agent = RecruitingAgent(config, env, console)

    if args.command == "login":
        agent.login()
        return

    if args.command == "run":
        decisions = agent.run()
        if args.json:
            console.print(decisions_to_json(decisions))


if __name__ == "__main__":
    main()
