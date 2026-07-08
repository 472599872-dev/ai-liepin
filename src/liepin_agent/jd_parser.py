from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from liepin_agent.settings import EnvSettings


KEYWORD_MATCH_OPTIONS = ["包含全部关键词", "包含任意关键词"]
WORK_YEAR_OPTIONS = ["不限", "应届生", "1-3年", "3-5年", "5-10年", "10年以上", "自定义"]
EDUCATION_OPTIONS = ["不限", "本科", "硕士", "博士/博士后", "大专", "中专/中技", "高中及以下"]
SCHOOL_TAG_OPTIONS = ["不限", "211", "985", "双一流", "海外留学"]
RECRUIT_TYPE_OPTIONS = ["统招/非统招（不限）", "统招本科", "统招硕士", "统招博士", "统招大专"]
ACTIVE_OPTIONS = ["不限", "今天活跃", "3天内活跃", "7天内活跃", "30天内活跃", "最近三个月活跃", "最近半年活跃", "最近一年活跃"]
GENDER_OPTIONS = ["不限", "男", "女"]
JOB_HOPPING_OPTIONS = ["跳槽频率（不限）", "近5年不超过3段", "近3年不超过2段", "近2段均不低于2年"]
LANGUAGE_OPTIONS = ["不限", "普通话", "英语", "日语", "法语", "粤语", "其他"]
JOB_STATUS_OPTIONS = ["离职，正在找工作", "在职，急寻新工作", "在职，看看新机会", "在职，暂无跳槽打算"]
RESUME_LANGUAGE_OPTIONS = ["简历语言（不限）", "中文简历", "英文简历"]


class SalaryRange(BaseModel):
    min: Optional[int] = Field(default=None, ge=0)
    max: Optional[int] = Field(default=None, ge=0)


class Confidence(BaseModel):
    keywords: float = Field(default=0.0, ge=0, le=1)
    position: float = Field(default=0.0, ge=0, le=1)
    industry: float = Field(default=0.0, ge=0, le=1)
    city: float = Field(default=0.0, ge=0, le=1)
    seniority: float = Field(default=0.0, ge=0, le=1)


class SearchConditionDraft(BaseModel):
    keyword_match: str = "包含任意关键词"
    keywords: list[str] = Field(default_factory=list)
    position_keywords: list[str] = Field(default_factory=list)
    company_keywords: list[str] = Field(default_factory=list)
    current_city: list[str] = Field(default_factory=list)
    expected_city: list[str] = Field(default_factory=list)
    work_years: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    recruit_type: str = "统招/非统招（不限）"
    school_tags: list[str] = Field(default_factory=list)
    current_industry: list[str] = Field(default_factory=list)
    current_position: list[str] = Field(default_factory=list)
    age_min: Optional[int] = Field(default=None, ge=0)
    age_max: Optional[int] = Field(default=None, ge=0)
    active_days: str = "不限"
    gender: str = "不限"
    job_hopping: str = "跳槽频率（不限）"
    languages: list[str] = Field(default_factory=list)
    expected_salary: Optional[SalaryRange] = None
    current_salary: Optional[SalaryRange] = None
    expected_industry: list[str] = Field(default_factory=list)
    expected_position: list[str] = Field(default_factory=list)
    schools: list[str] = Field(default_factory=list)
    majors: list[str] = Field(default_factory=list)
    job_status: list[str] = Field(default_factory=list)
    resume_language: str = "简历语言（不限）"
    overseas_work: bool = False
    management_experience: bool = False
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    search_groups: list[dict[str, Any]] = Field(default_factory=list)
    low_confidence_notes: list[str] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)


@dataclass
class ParseResult:
    draft: SearchConditionDraft
    source: str
    error: str = ""


def _unique(items: list[str], limit: int = 20) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = re.sub(r"\s+", " ", str(item)).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _keep_allowed(items: list[str], allowed: list[str], default: Optional[list[str]] = None) -> list[str]:
    values = [item for item in _unique(items) if item in allowed and item != "不限"]
    return values or (default or [])


def _allowed_value(value: str, allowed: list[str], default: str) -> str:
    return value if value in allowed else default


def _coerce_age(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        age = int(float(str(value).replace("岁", "").strip()))
    except (TypeError, ValueError):
        return None
    if age < 16 or age > 80:
        return None
    return age


def normalize_draft(draft: SearchConditionDraft) -> SearchConditionDraft:
    data = draft.model_dump()
    data["keyword_match"] = _allowed_value(data["keyword_match"], KEYWORD_MATCH_OPTIONS, "包含任意关键词")
    data["keywords"] = _unique(data["keywords"], 12)
    data["position_keywords"] = _unique(data["position_keywords"], 8)
    data["company_keywords"] = _unique(data["company_keywords"], 8)
    data["current_city"] = _unique(data["current_city"], 8)
    data["expected_city"] = _unique(data["expected_city"], 8)
    data["work_years"] = _keep_allowed(data["work_years"], WORK_YEAR_OPTIONS)
    data["education"] = _keep_allowed(data["education"], EDUCATION_OPTIONS)
    data["recruit_type"] = _allowed_value(data["recruit_type"], RECRUIT_TYPE_OPTIONS, "统招/非统招（不限）")
    data["school_tags"] = _keep_allowed(data["school_tags"], SCHOOL_TAG_OPTIONS)
    data["age_min"] = _coerce_age(data.get("age_min"))
    data["age_max"] = _coerce_age(data.get("age_max"))
    if data["age_min"] and data["age_max"] and data["age_min"] > data["age_max"]:
        data["age_min"], data["age_max"] = data["age_max"], data["age_min"]
    data["active_days"] = _allowed_value(data["active_days"], ACTIVE_OPTIONS, "不限")
    data["gender"] = _allowed_value(data["gender"], GENDER_OPTIONS, "不限")
    data["job_hopping"] = _allowed_value(data["job_hopping"], JOB_HOPPING_OPTIONS, "跳槽频率（不限）")
    data["languages"] = _keep_allowed(data["languages"], LANGUAGE_OPTIONS)
    data["job_status"] = _keep_allowed(data["job_status"], JOB_STATUS_OPTIONS)
    data["resume_language"] = _allowed_value(data["resume_language"], RESUME_LANGUAGE_OPTIONS, "简历语言（不限）")
    for key in ["current_industry", "current_position", "expected_industry", "expected_position", "schools", "majors"]:
        data[key] = _unique(data[key], 10)
    for key in ["must_have", "nice_to_have", "exclude_keywords", "low_confidence_notes", "reasoning"]:
        data[key] = _unique(data[key], 20)
    return SearchConditionDraft.model_validate(data)


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _schema_prompt() -> dict[str, Any]:
    return {
        "keyword_match_options": KEYWORD_MATCH_OPTIONS,
        "work_year_options": WORK_YEAR_OPTIONS,
        "education_options": EDUCATION_OPTIONS,
        "recruit_type_options": RECRUIT_TYPE_OPTIONS,
        "school_tag_options": SCHOOL_TAG_OPTIONS,
        "active_options": ACTIVE_OPTIONS,
        "gender_options": GENDER_OPTIONS,
        "job_hopping_options": JOB_HOPPING_OPTIONS,
        "language_options": LANGUAGE_OPTIONS,
        "job_status_options": JOB_STATUS_OPTIONS,
        "resume_language_options": RESUME_LANGUAGE_OPTIONS,
    }


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，、/]\s*|\n", value) if item.strip()]
    return [str(value).strip()]


def _active_days_from_value(value: Any) -> str:
    if isinstance(value, str) and value in ACTIVE_OPTIONS:
        return value
    try:
        days = int(value)
    except (TypeError, ValueError):
        return "不限"
    if days <= 1:
        return "今天活跃"
    if days <= 3:
        return "3天内活跃"
    if days <= 7:
        return "7天内活跃"
    if days <= 30:
        return "30天内活跃"
    if days <= 90:
        return "最近三个月活跃"
    if days <= 180:
        return "最近半年活跃"
    return "最近一年活跃"


def _coerce_raw(raw: dict[str, Any]) -> dict[str, Any]:
    if "search_conditions" in raw and isinstance(raw["search_conditions"], dict):
        merged = dict(raw["search_conditions"])
        merged.update({k: v for k, v in raw.items() if k not in {"search_conditions"}})
        raw = merged

    aliases = {
        "keyword_match_option": "keyword_match",
        "keyword_match_type": "keyword_match",
        "position": "position_keywords",
        "positions": "position_keywords",
        "position_titles": "position_keywords",
        "company": "company_keywords",
        "companies": "company_keywords",
        "city": "expected_city",
        "cities": "expected_city",
        "expected_cities": "expected_city",
        "current_cities": "current_city",
        "experience": "work_years",
        "education_level": "education",
        "school_tag": "school_tags",
        "school_requirement": "school_tags",
        "industry": "current_industry",
        "industries": "current_industry",
        "current_industries": "current_industry",
        "current_positions": "current_position",
        "age_from": "age_min",
        "min_age": "age_min",
        "age_lower": "age_min",
        "age_to": "age_max",
        "max_age": "age_max",
        "age_upper": "age_max",
        "expected_industries": "expected_industry",
        "expected_positions": "expected_position",
        "language": "languages",
        "job_hopping_constraint": "job_hopping",
        "resume_lang": "resume_language",
        "must_keywords": "must_have",
        "nice_keywords": "nice_to_have",
        "exclude": "exclude_keywords",
        "reject_keywords": "exclude_keywords",
        "notes": "low_confidence_notes",
    }
    data = dict(raw)
    for old, new in aliases.items():
        if old in data and new not in data:
            data[new] = data[old]
    if "active_within_days" in data and "active_days" not in data:
        data["active_days"] = _active_days_from_value(data["active_within_days"])
    if "age" in data and ("age_min" not in data or "age_max" not in data):
        age_text = str(data.get("age") or "")
        match = re.search(r"(\d{2})\s*[-~至到—]\s*(\d{2})", age_text)
        if match:
            data.setdefault("age_min", int(match.group(1)))
            data.setdefault("age_max", int(match.group(2)))
        else:
            under = re.search(r"(\d{2})\s*岁?\s*(?:以下|以内|内)", age_text)
            above = re.search(r"(\d{2})\s*岁?\s*(?:以上|及以上)", age_text)
            if under:
                data.setdefault("age_max", int(under.group(1)))
            if above:
                data.setdefault("age_min", int(above.group(1)))
    if isinstance(data.get("job_status"), str):
        status_text = data["job_status"]
        matched_status = [item for item in JOB_STATUS_OPTIONS if item in status_text]
        data["job_status"] = matched_status or [status_text]

    list_fields = {
        "keywords",
        "position_keywords",
        "company_keywords",
        "current_city",
        "expected_city",
        "work_years",
        "education",
        "school_tags",
        "current_industry",
        "current_position",
        "languages",
        "expected_industry",
        "expected_position",
        "schools",
        "majors",
        "must_have",
        "nice_to_have",
        "exclude_keywords",
        "low_confidence_notes",
        "reasoning",
    }
    for field in list_fields:
        if field in data:
            data[field] = _as_list(data[field])
    return data


def _output_template() -> dict[str, Any]:
    return {
        "keyword_match": "包含任意关键词",
        "keywords": [],
        "position_keywords": [],
        "company_keywords": [],
        "current_city": [],
        "expected_city": [],
        "work_years": [],
        "education": [],
        "recruit_type": "统招/非统招（不限）",
        "school_tags": [],
        "current_industry": [],
        "current_position": [],
        "age_min": None,
        "age_max": None,
        "active_days": "不限",
        "gender": "不限",
        "job_hopping": "跳槽频率（不限）",
        "languages": [],
        "expected_salary": None,
        "current_salary": None,
        "expected_industry": [],
        "expected_position": [],
        "schools": [],
        "majors": [],
        "job_status": [],
        "resume_language": "简历语言（不限）",
        "overseas_work": False,
        "management_experience": False,
        "must_have": [],
        "nice_to_have": [],
        "exclude_keywords": [],
        "search_groups": [],
        "low_confidence_notes": [],
        "reasoning": [],
        "confidence": {"keywords": 0.9, "position": 0.8, "industry": 0.6, "city": 0.2, "seniority": 0.7},
    }


def parse_jd_with_qwen(title: str, jd: str, env: EnvSettings) -> ParseResult:
    api_key = env.qwen_api_key or env.openai_api_key
    if not api_key:
        return ParseResult(build_rule_based_draft(title, jd), source="rule", error="未配置 QWEN_API_KEY")

    try:
        client = OpenAI(api_key=api_key, base_url=env.qwen_base_url or env.openai_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1")
        payload = {
            "job_title": title,
            "jd": jd,
            "liepin_schema": _schema_prompt(),
            "output_template": _output_template(),
            "requirements": [
                "只输出 JSON，不要 Markdown。",
                "必须使用 output_template 里的字段名，不能改字段名。",
                "下拉/标签字段只能使用 liepin_schema 中已有选项。",
                "只能基于用户提供的 job_title 和 jd 提取搜索条件；不要套用示例、历史岗位或固定行业默认值。",
                "城市、行业、职位、学校、专业不确定时必须留空并写 low_confidence_notes。",
                "关键词要来自 JD 或岗位名称中的硬技能、业务场景、工具、算法方向，不要凭空补充。",
                "搜索条件宁可略宽，后续简历评分再严格。",
                "search_groups 给 2-4 组可分批搜索的策略，每组包含 name、keywords、positions、industries、reason。",
            ],
        }
        response = client.chat.completions.create(
            model=env.qwen_model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是资深猎头搜索专家，负责把岗位 JD 转成猎聘找人页可执行的搜索条件。"
                        "不要虚构页面不存在的固定选项；不确定的条件要显式标注低置信度。"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        content = response.choices[0].message.content or "{}"
        raw = _coerce_raw(_extract_json(content))
        try:
            draft = SearchConditionDraft.model_validate(raw)
        except ValidationError:
            raw = {"keywords": raw.get("keywords", []), "must_have": raw.get("must_have", []), "nice_to_have": raw.get("nice_to_have", [])}
            draft = SearchConditionDraft.model_validate(raw)
        draft = normalize_draft(draft)
        return ParseResult(draft, source="qwen")
    except Exception as exc:
        return ParseResult(build_rule_based_draft(title, jd), source="rule", error=str(exc))


def build_rule_based_draft(title: str, jd: str) -> SearchConditionDraft:
    text = f"{title}\n{jd}"
    english_terms = re.findall(r"\b[A-Za-z][A-Za-z0-9+#.\-/]{1,30}\b", text)
    chinese_terms: list[str] = []
    for value in re.split(r"[\s,，、。；;：:\n\r（）()]+", text):
        value = value.strip()
        value = re.sub(r"^(?:负责|参与|使用|熟悉|了解|掌握|具备|要求|优先|包括|进行|完成|根据|结合)", "", value).strip()
        if len(value) < 2 or re.fullmatch(r"\d+", value):
            continue
        if any(stop in value for stop in ("岗位", "职责", "要求", "优先", "负责", "参与", "具备", "熟悉", "了解")) and len(value) > 8:
            continue
        chinese_terms.append(value)
    keywords = _unique(english_terms + chinese_terms, 12)
    position_keywords = _unique([title], 3) if title.strip() else []
    education = [item for item in EDUCATION_OPTIONS if item != "不限" and item in text]
    work_years = [item for item in WORK_YEAR_OPTIONS if item not in {"不限", "自定义"} and item in text]
    must_have = keywords
    return normalize_draft(
        SearchConditionDraft(
            keyword_match="包含任意关键词",
            keywords=keywords,
            position_keywords=position_keywords,
            work_years=work_years,
            education=education,
            active_days="不限",
            must_have=must_have or keywords[:5],
            reasoning=["本地规则回退：仅从用户填写的岗位名称和 JD 原文抽取词，不补充固定行业默认值。"],
            confidence=Confidence(keywords=0.35 if keywords else 0.0, position=0.35 if position_keywords else 0.0, industry=0.0, city=0.0, seniority=0.0),
        )
    )


def draft_to_job_fields(draft: SearchConditionDraft) -> dict[str, Any]:
    return {
        "keywords": draft.keywords,
        "must_have": draft.must_have or draft.keywords[:6],
        "nice_to_have": draft.nice_to_have,
        "reject_keywords": draft.exclude_keywords,
        "city": "，".join(draft.expected_city or draft.current_city),
        "experience": "，".join(draft.work_years),
        "education": "，".join(draft.education),
    }


def draft_preview_lines(draft: SearchConditionDraft, source: str, error: str = "") -> list[str]:
    lines = [
        "JD 搜索条件解析结果",
        f"来源：{'千问' if source == 'qwen' else '本地规则回退'}" + (f"（{error}）" if error else ""),
        f"关键词匹配：{draft.keyword_match}",
        f"搜索关键词：{'，'.join(draft.keywords) or '-'}",
        f"职位关键词：{'，'.join(draft.position_keywords) or '-'}",
        f"当前/期望行业：{'，'.join(_unique(draft.current_industry + draft.expected_industry)) or '-'}",
        f"当前/期望职位：{'，'.join(_unique(draft.current_position + draft.expected_position)) or '-'}",
        f"城市：{'，'.join(_unique(draft.current_city + draft.expected_city)) or '-'}",
        f"工作年限：{'，'.join(draft.work_years) or '-'}",
        f"学历：{'，'.join(draft.education) or '-'}；统招：{draft.recruit_type}；院校：{'，'.join(draft.school_tags) or '-'}",
        f"活跃度：{draft.active_days}；性别：{draft.gender}；跳槽：{draft.job_hopping}",
        f"语言：{'，'.join(draft.languages) or '-'}；求职状态：{'，'.join(draft.job_status) or '-'}；简历语言：{draft.resume_language}",
        f"必备项：{'，'.join(draft.must_have) or '-'}",
        f"加分项：{'，'.join(draft.nice_to_have) or '-'}",
        f"排除项：{'，'.join(draft.exclude_keywords) or '-'}",
        "搜索分组：",
    ]
    if draft.search_groups:
        for group in draft.search_groups[:4]:
            lines.append(
                f"- {group.get('name', '未命名')}：关键词 {group.get('keywords', [])}；职位 {group.get('positions', [])}；行业 {group.get('industries', [])}"
            )
    else:
        lines.append("- 暂无")
    if draft.low_confidence_notes:
        lines.append("低置信度/需确认：" + "；".join(draft.low_confidence_notes))
    if draft.reasoning:
        lines.append("解析依据：" + "；".join(draft.reasoning[:6]))
    lines.append(
        "置信度："
        f"关键词 {draft.confidence.keywords:.2f}，职位 {draft.confidence.position:.2f}，"
        f"行业 {draft.confidence.industry:.2f}，城市 {draft.confidence.city:.2f}，资历 {draft.confidence.seniority:.2f}"
    )
    return lines
