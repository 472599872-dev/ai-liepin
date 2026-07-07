from liepin_agent.models import Candidate
from liepin_agent.scoring import CandidateScorer
from liepin_agent.settings import EnvSettings, JobConfig


def local_env() -> EnvSettings:
    return EnvSettings(qwen_api_key="", openai_api_key="")


def test_rule_scoring_rewards_required_keywords() -> None:
    job = JobConfig(
        title="高级 Java 后端工程师",
        jd="Java Spring Boot MySQL Redis",
        must_have=["Java", "Spring Boot", "MySQL", "Redis"],
        nice_to_have=["高并发"],
        reject_keywords=["应届"],
        min_score=75,
    )
    candidate = Candidate(
        name="张三",
        title="后端工程师",
        resume_text="8年 Java 后端经验，熟悉 Spring Boot、MySQL、Redis，做过高并发交易系统。",
    )

    result = CandidateScorer(job, local_env()).score(candidate)

    assert result.score >= 90
    assert "Java" in result.matched_keywords
    assert not result.missing_keywords


def test_rule_scoring_penalizes_reject_keywords() -> None:
    job = JobConfig(
        title="高级 Java 后端工程师",
        jd="Java Spring Boot MySQL Redis",
        must_have=["Java", "Spring Boot", "MySQL", "Redis"],
        reject_keywords=["应届"],
    )
    candidate = Candidate(name="李四", resume_text="应届毕业生，了解 Java。")

    result = CandidateScorer(job, local_env()).score(candidate)

    assert result.score < 75
    assert "应届" in result.risks
