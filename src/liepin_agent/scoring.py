from __future__ import annotations

import json
import re
from dataclasses import dataclass

from openai import OpenAI

from liepin_agent.models import Candidate, ScoreResult
from liepin_agent.settings import EnvSettings, JobConfig


def _contains_keyword(text: str, keyword: str) -> bool:
    return keyword.lower() in text.lower()


def _compact_text(text: str, max_chars: int = 6000) -> str:
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


@dataclass
class CandidateScorer:
    job: JobConfig
    env: EnvSettings

    def score(self, candidate: Candidate, *, use_llm: bool = True) -> ScoreResult:
        if use_llm and self.env.llm_api_key:
            try:
                return self._score_with_llm(candidate)
            except Exception as exc:  # Keep the workflow alive when the model API is unavailable.
                fallback = self._score_with_rules(candidate)
                fallback.risks.append(f"LLM 评分失败，已回退本地规则：{exc}")
                return fallback
        return self._score_with_rules(candidate)

    def _score_with_rules(self, candidate: Candidate) -> ScoreResult:
        text = " ".join(
            [
                candidate.name,
                candidate.title,
                candidate.location,
                candidate.experience,
                candidate.education,
                candidate.resume_text,
            ]
        )
        matched_must = [kw for kw in self.job.must_have if _contains_keyword(text, kw)]
        matched_nice = [kw for kw in self.job.nice_to_have if _contains_keyword(text, kw)]
        missing = [kw for kw in self.job.must_have if kw not in matched_must]
        risks = [kw for kw in self.job.reject_keywords if _contains_keyword(text, kw)]

        score = 45
        if self.job.must_have:
            score += round(40 * len(matched_must) / len(self.job.must_have))
        score += min(10, 4 * len(matched_nice))
        score -= min(35, 15 * len(risks))
        if candidate.resume_text:
            score += 5

        score = max(0, min(100, score))
        summary = (
            f"匹配 {len(matched_must)}/{len(self.job.must_have)} 个必备项，"
            f"{len(matched_nice)} 个加分项。"
        )
        if risks:
            summary += f" 风险关键词：{', '.join(risks)}。"

        return ScoreResult(
            score=score,
            matched_keywords=matched_must + matched_nice,
            missing_keywords=missing,
            risks=risks,
            summary=summary,
        )

    def _score_with_llm(self, candidate: Candidate) -> ScoreResult:
        client_kwargs = {"api_key": self.env.llm_api_key}
        if self.env.llm_base_url:
            client_kwargs["base_url"] = self.env.llm_base_url
        client = OpenAI(**client_kwargs)

        prompt = {
            "job_title": self.job.title,
            "jd": self.job.jd,
            "must_have": self.job.must_have,
            "nice_to_have": self.job.nice_to_have,
            "reject_keywords": self.job.reject_keywords,
            "candidate": {
                "name": candidate.name,
                "title": candidate.title,
                "location": candidate.location,
                "experience": candidate.experience,
                "education": candidate.education,
                "resume_text": _compact_text(candidate.resume_text),
            },
        }
        response = client.chat.completions.create(
            model=self.env.llm_model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是资深猎头顾问。请基于岗位 JD 评估候选人匹配度，"
                        "只输出 JSON：score(0-100整数), matched_keywords数组, "
                        "missing_keywords数组, risks数组, summary字符串。"
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return ScoreResult.model_validate_json(content)
