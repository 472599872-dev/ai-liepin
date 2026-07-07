from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from liepin_agent.browser import LiepinBrowser
from liepin_agent.greeting import GreetingGenerator
from liepin_agent.models import GreetingDecision
from liepin_agent.scoring import CandidateScorer
from liepin_agent.settings import AppConfig, EnvSettings


class RecruitingAgent:
    def __init__(self, config: AppConfig, env: EnvSettings, console: Console | None = None) -> None:
        self.config = config
        self.env = env
        self.console = console or Console()
        self.scorer = CandidateScorer(config.job, env)
        self.greeter = GreetingGenerator(config.job, config.greeting, env)

    def login(self) -> None:
        with LiepinBrowser(self.config, self.env) as browser:
            browser.login()
        self.console.print("[green]登录态已保存。[/green]")

    def run(self) -> list[GreetingDecision]:
        with LiepinBrowser(self.config, self.env) as browser:
            candidates = browser.search_candidates()
            self.console.print(f"找到候选人：{len(candidates)}")
            decisions: list[GreetingDecision] = []
            for candidate in candidates:
                score = self.scorer.score(candidate)
                greeting = self.greeter.build(candidate, score)
                should_greet = score.score >= self.config.job.min_score
                sent = False
                if should_greet:
                    sent = browser.send_greeting(candidate, greeting)
                decision = GreetingDecision(
                    candidate=candidate,
                    score=score,
                    greeting=greeting,
                    should_greet=should_greet,
                    sent=sent,
                    dry_run=self.env.dry_run,
                )
                decisions.append(decision)
                self._append_log(decision)
            self._print_summary(decisions)
            return decisions

    def _append_log(self, decision: GreetingDecision) -> None:
        path = Path("data/runs.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(decision.model_dump_json() + "\n")

    def _print_summary(self, decisions: list[GreetingDecision]) -> None:
        table = Table(title="候选人评分结果")
        table.add_column("候选人")
        table.add_column("分数", justify="right")
        table.add_column("是否打招呼")
        table.add_column("发送状态")
        table.add_column("摘要")

        for decision in decisions:
            table.add_row(
                decision.candidate.name or "-",
                str(decision.score.score),
                "是" if decision.should_greet else "否",
                "dry-run" if decision.dry_run else ("已发送" if decision.sent else "未发送"),
                decision.score.summary[:80],
            )
        self.console.print(table)

        if decisions:
            approved = [item for item in decisions if item.should_greet]
            self.console.print(
                f"超过阈值：{len(approved)}/{len(decisions)}；"
                f"DRY_RUN={str(self.env.dry_run).lower()}。"
            )


def decisions_to_json(decisions: list[GreetingDecision]) -> str:
    return json.dumps([item.model_dump(mode="json") for item in decisions], ensure_ascii=False, indent=2)

