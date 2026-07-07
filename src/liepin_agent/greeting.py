from __future__ import annotations

from dataclasses import dataclass

from liepin_agent.models import Candidate, ScoreResult
from liepin_agent.settings import EnvSettings, GreetingConfig, JobConfig


@dataclass
class GreetingGenerator:
    job: JobConfig
    greeting: GreetingConfig
    env: EnvSettings

    def build(self, candidate: Candidate, score: ScoreResult) -> str:
        if self.greeting.custom_message.strip():
            return self.greeting.custom_message.strip().format(
                name=candidate.name or "您好",
                job_title=self.job.title,
                score=score.score,
            )
        return self._build_with_template(candidate, score)

    def _build_with_template(self, candidate: Candidate, score: ScoreResult) -> str:
        name = candidate.name or "您好"
        highlights = "、".join(score.matched_keywords[:4]) or "您的经历"
        return (
            f"{name}，您好。我是猎头顾问，正在为客户招聘「{self.job.title}」。"
            f"看到您在{highlights}方面的经历和岗位比较契合，想和您简单沟通一下机会。"
            "如果您近期愿意了解新的职业机会，期待和您进一步交流。"
        )
