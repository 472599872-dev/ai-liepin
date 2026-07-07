from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    liepin_username: str = ""
    liepin_password: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = "gpt-4.1-mini"
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"
    dry_run: bool = True

    @property
    def llm_api_key(self) -> str:
        return self.qwen_api_key or self.openai_api_key

    @property
    def llm_base_url(self) -> str:
        return self.qwen_base_url if self.qwen_api_key else self.openai_base_url

    @property
    def llm_model(self) -> str:
        return self.qwen_model if self.qwen_api_key else self.openai_model


class JobConfig(BaseModel):
    title: str
    jd: str
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    reject_keywords: list[str] = Field(default_factory=list)
    min_score: int = Field(default=75, ge=0, le=100)


class SearchConfig(BaseModel):
    url: str
    keywords: list[str] = Field(default_factory=list)
    city: str = ""
    experience: str = ""
    education: str = ""
    max_candidates: int = Field(default=20, ge=1, le=200)


class GreetingConfig(BaseModel):
    custom_message: str = ""
    tone: str = "专业、真诚、简洁"


class BrowserSelectors(BaseModel):
    username_input: str = ""
    password_input: str = ""
    login_button: str = ""
    keyword_input: str = "input"
    search_button: str = "button"
    candidate_cards: str = "[data-candidate-id], .candidate-card, .resume-card, .user-card"
    candidate_link: str = "a"
    resume_text: str = "body"
    greet_button: str = "text=打招呼"
    greeting_textarea: str = "textarea"
    send_button: str = "text=发送"


class BrowserConfig(BaseModel):
    headless: bool = False
    slow_mo_ms: int = Field(default=80, ge=0, le=2000)
    storage_state_path: str = "playwright/.auth/liepin.json"
    selectors: BrowserSelectors = Field(default_factory=BrowserSelectors)


class AppConfig(BaseModel):
    job: JobConfig
    search: SearchConfig
    greeting: GreetingConfig = Field(default_factory=GreetingConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)


def load_config(path: str | Path) -> AppConfig:
    load_dotenv()
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(raw)


def load_env() -> EnvSettings:
    load_dotenv()
    return EnvSettings()
