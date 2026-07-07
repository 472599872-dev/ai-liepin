from liepin_agent.greeting import GreetingGenerator
from liepin_agent.models import Candidate, ScoreResult
from liepin_agent.settings import EnvSettings, GreetingConfig, JobConfig


def local_env() -> EnvSettings:
    return EnvSettings(qwen_api_key="", openai_api_key="")


def test_custom_greeting_template() -> None:
    job = JobConfig(title="算法工程师", jd="负责推荐系统")
    greeting = GreetingConfig(custom_message="{name}，岗位是{job_title}，匹配分{score}。")
    candidate = Candidate(name="王五")
    score = ScoreResult(score=88)

    message = GreetingGenerator(job, greeting, local_env()).build(candidate, score)

    assert message == "王五，岗位是算法工程师，匹配分88。"


def test_default_greeting_mentions_job_title() -> None:
    job = JobConfig(title="高级 Java 后端工程师", jd="Java 后端")
    candidate = Candidate(name="赵六")
    score = ScoreResult(score=82, matched_keywords=["Java", "Spring Boot"])

    message = GreetingGenerator(job, GreetingConfig(), local_env()).build(candidate, score)

    assert "高级 Java 后端工程师" in message
    assert "赵六" in message


def test_default_greeting_does_not_call_llm_when_api_key_exists() -> None:
    job = JobConfig(title="机械工程师", jd="机器人项目")
    candidate = Candidate(name="李四")
    score = ScoreResult(score=76, matched_keywords=["机器人", "运动控制"])
    env = EnvSettings(qwen_api_key="fake-key")

    message = GreetingGenerator(job, GreetingConfig(), env).build(candidate, score)

    assert "机械工程师" in message
    assert "李四" in message
