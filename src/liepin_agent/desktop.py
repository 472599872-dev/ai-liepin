from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from liepin_agent.db import Database, loads
from liepin_agent.greeting import GreetingGenerator
from liepin_agent.jd_parser import (
    ACTIVE_OPTIONS,
    EDUCATION_OPTIONS,
    GENDER_OPTIONS,
    JOB_HOPPING_OPTIONS,
    JOB_STATUS_OPTIONS,
    KEYWORD_MATCH_OPTIONS,
    LANGUAGE_OPTIONS,
    RECRUIT_TYPE_OPTIONS,
    RESUME_LANGUAGE_OPTIONS,
    SCHOOL_TAG_OPTIONS,
    WORK_YEAR_OPTIONS,
    draft_preview_lines,
    draft_to_job_fields,
    parse_jd_with_qwen,
)
from liepin_agent.license import check_license, default_license_paths
from liepin_agent.liepin_scripts import SEARCH_PAGE_URL
from liepin_agent.liepin_page_adapter import LiepinPageAdapter
from liepin_agent.models import Candidate, ScoreResult
from liepin_agent.scoring import CandidateScorer
from liepin_agent.settings import EnvSettings, GreetingConfig, JobConfig, load_env
from liepin_agent.task_engine import HumanIntervention, TaskEngine, TaskStatus, TaskStep
from liepin_agent.task_executor import TaskExecutionContext, TaskExecutor
from liepin_agent.task_runtime import GREETED_STATUSES, TaskNotice, TaskRuntime
from liepin_agent.task_workflow import (
    WorkflowAction,
    decide_condition_apply_validation,
    decide_card_collection_validation,
    decide_detail_validation,
    decide_greeting_result,
    decide_open_candidate_result,
    decide_page_turn_result,
    decide_resume_validation,
)


TASK_CONDITION_STEP_DELAY_MS = 2000
TASK_TARGET_OPTIONS = [("查看简历人数", "resume"), ("沟通人数", "greeting")]
TASK_BLOCKING_CONDITION_KEYS = {"keywords", "position_keywords", "age_min", "age_max"}
TASK_TERMINAL_STATUSES = {
    TaskStatus.DONE.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.SKIPPED.value,
}


def _random_delay_ms(min_ms: int, max_ms: int) -> int:
    lower = max(0, int(min_ms))
    upper = max(lower, int(max_ms))
    return random.randint(lower, upper)


def _task_chain_delay_ms(fallback_ms: int = 0) -> int:
    if fallback_ms and int(fallback_ms) > 1000:
        spread = max(600, int(fallback_ms * 0.35))
        return _random_delay_ms(max(1000, int(fallback_ms) - spread), int(fallback_ms) + spread)
    return _random_delay_ms(3200, 6800)


def _configure_qt_webengine_env() -> None:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ.setdefault("QT_OPENGL", "software")
    existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    merged = existing_flags.split()
    for flag in ("--disable-gpu", "--disable-features=WebOTP,WebUSB,WebPayments"):
        if flag not in merged:
            merged.append(flag)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(merged)


def _configure_packaged_cwd() -> None:
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).resolve().parent)


def _require_qt():
    try:
        from PySide6.QtCore import QTimer, QUrl, Qt
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMessageBox,
            QScrollArea,
            QPushButton,
            QSpinBox,
            QSplitter,
            QTabWidget,
            QTableWidget,
            QTableWidgetItem,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "桌面应用依赖未安装。请先运行：.venv/bin/python -m pip install -e \".[desktop,dev]\""
        ) from exc
    return locals()


def _csv(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，\n]", text) if item.strip()]


def _unique_preserve(items: list[str], limit: int = 50) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for item in items:
        value = re.sub(r"\s+", " ", str(item)).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= limit:
            break
    return values


def _single_work_year_value(value: Any) -> str:
    if isinstance(value, str):
        items = _csv(value)
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    aliases = {
        "博士/博士后": "10年以上",
        "10年+": "10年以上",
        "10年以上": "10年以上",
        "5-10年": "5-10年",
        "3-5年": "3-5年",
        "1-3年": "1-3年",
        "应届生": "应届生",
        "不限": "不限",
    }
    rank = {"应届生": 0, "1-3年": 1, "3-5年": 2, "5-10年": 3, "10年以上": 4, "不限": 5}
    normalized = []
    for item in items:
        mapped = aliases.get(item, item)
        if mapped in rank and mapped not in normalized:
            normalized.append(mapped)
    preferred = [item for item in sorted(normalized, key=lambda item: rank[item]) if item != "不限"]
    if preferred:
        return preferred[-1]
    return normalized[0] if normalized else "不限"


def _single_work_year_list(value: Any) -> list[str]:
    selected = _single_work_year_value(value)
    return [] if selected == "不限" else [selected]


def _condition_text(value: Any) -> str:
    return str(value or "").strip()


def _condition_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return _csv(value)
    return []


def _non_neutral_choice(value: Any, neutral_values: set[str]) -> str:
    text = _condition_text(value)
    return "" if not text or text in neutral_values else text


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:60]
    return ""


def _candidate_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _candidate_external_id(profile_url: str, fallback_text: str = "") -> str:
    url = str(profile_url or "").strip()
    if url:
        matched = re.search(r"(?:res_id_encode|res_id|resumeId|resume_id)=([^&#]+)", url, flags=re.I)
        if matched:
            return matched.group(1)[:64]
        if "/resume/" in url:
            tail = url.rstrip("/").split("/")[-1]
            if tail and tail.lower() not in {"showresumedetail", "resume"}:
                return tail[:64]
        return _candidate_key(url)
    return _candidate_key(fallback_text or "")


def _join_values(value: Any) -> str:
    if isinstance(value, list):
        return "，".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _derive_route_job_fields(jd: str) -> dict[str, list[str]]:
    keyword_candidates = [
        "数字孪生",
        "工业仿真",
        "仿真建模",
        "生产排程",
        "排产",
        "流程优化",
        "Unreal Engine",
        "Omniverse",
        "Isaac Sim",
        "Python",
        "机器学习",
        "智能调度",
        "离散事件仿真",
        "运筹优化",
        "APS",
        "MES",
    ]
    must_candidates = [
        "数字孪生",
        "工业仿真",
        "生产排程",
        "Python",
        "机器学习",
    ]
    nice_candidates = [
        "Unreal Engine",
        "Omniverse",
        "Isaac Sim",
        "强化学习",
        "生成式模型",
        "离散事件仿真",
        "运筹优化",
        "APS",
        "MES",
        "ROS",
        "USD",
        "PhysX",
    ]

    def keep(items: list[str]) -> list[str]:
        matched = [item for item in items if item.lower() in jd.lower()]
        return matched or items[:8]

    return {
        "keywords": keep(keyword_candidates)[:10],
        "must_have": keep(must_candidates),
        "nice_to_have": keep(nice_candidates),
        "reject_keywords": [],
    }


def _js_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {"raw": value}
    return {}


def _candidate_list_signature(payload: Any, limit: int = 6) -> str:
    data = _js_payload(payload)
    cards = data.get("cards") if isinstance(data.get("cards"), list) else []
    samples: list[str] = []
    for card in cards[:limit]:
        if not isinstance(card, dict):
            continue
        text = re.sub(r"\s+", " ", str(card.get("text") or "")).strip()
        name = re.sub(r"\s+", " ", str(card.get("name") or "")).strip()
        samples.append(f"{name}|{text[:180]}")
    return json.dumps(
        {
            "count": len(cards),
            "result": str(data.get("resultCountText") or ""),
            "samples": samples,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _compact_text(text: str, limit: int = 120) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _status_label(value: str) -> str:
    mapping = {
        "not_fetched": "未抓",
        "list_card": "列表",
        "fetched": "已抓",
        "needs_attachment": "需附件",
        "incomplete": "不完整",
        "not_sent": "未沟通",
        "generated": "已生成",
        "opening_selected_dry_run": "开聊已选",
        "opening_sent": "已开聊",
        "followup_filled_dry_run": "补充已填",
        "followup_filled_not_sent": "已开聊/补充未发",
        "followup_sent": "已发送",
        "continued_followup_filled_dry_run": "续聊已填",
        "continued_followup_sent": "续聊已发送",
        "followup_already_exists": "话术已存在",
        "sent": "已发送",
        "failed": "失败",
    }
    return mapping.get(str(value or ""), str(value or "-"))


def _candidate_state_label(row: Any) -> str:
    state = str(row["candidate_state"] or "active")
    resume_status = str(row["resume_status"] or "")
    score = row["score"]
    greeting_status = str(row["latest_greeting_status"] or row["greeting_status"] or "")
    if state == "rejected":
        return "已淘汰"
    if resume_status == "list_card":
        return "列表卡片"
    if greeting_status in {
        "opening_selected_dry_run",
        "opening_sent",
        "followup_filled_dry_run",
        "followup_filled_not_sent",
        "followup_sent",
        "continued_followup_filled_dry_run",
        "continued_followup_sent",
        "followup_already_exists",
        "sent",
    }:
        return "已沟通"
    if score is not None:
        return "已评分"
    return "未评分"


def _candidate_primary_status(row: Any) -> str:
    state = str(row["candidate_state"] or "active")
    if state == "rejected":
        return "淘汰"
    greeting_status = str(row["latest_greeting_status"] or row["greeting_status"] or "")
    if greeting_status in {
        "opening_selected_dry_run",
        "opening_sent",
        "followup_filled_dry_run",
        "followup_filled_not_sent",
        "followup_sent",
        "continued_followup_filled_dry_run",
        "continued_followup_sent",
        "followup_already_exists",
        "sent",
    }:
        return "已沟通"
    if row["score"] is not None:
        return "已评分"
    resume_status = str(row["resume_status"] or "")
    if resume_status in {"fetched", "needs_attachment", "incomplete"}:
        return "已抓取"
    if resume_status == "list_card":
        return "列表"
    return "待处理"


def _candidate_filter_buckets(row: Any) -> set[str]:
    buckets: set[str] = set()
    state = str(row["candidate_state"] or "active")
    resume_status = str(row["resume_status"] or "")
    score = row["score"]
    greeting_status = str(row["latest_greeting_status"] or row["greeting_status"] or "")
    if state == "rejected":
        buckets.add("rejected")
    if resume_status == "list_card":
        buckets.add("list_card")
    if score is None:
        buckets.add("unscored")
    else:
        buckets.add("scored")
    if greeting_status in {
        "opening_selected_dry_run",
        "opening_sent",
        "followup_filled_dry_run",
        "followup_filled_not_sent",
        "followup_sent",
        "continued_followup_filled_dry_run",
        "continued_followup_sent",
        "followup_already_exists",
        "sent",
    }:
        buckets.add("contacted")
    return buckets


def _task_is_cancelled(state: DesktopState, task_id: int | None) -> bool:
    if task_id is None:
        return False
    task_id = int(task_id)
    if task_id in state.cancelled_task_ids:
        return True
    row = state.db.fetch_one("SELECT status FROM tasks WHERE id = ?", (task_id,))
    return bool(row and row["status"] == TaskStatus.CANCELLED.value)


def _task_status(state: DesktopState, task_id: int | None) -> str:
    if task_id is None:
        return ""
    row = state.db.fetch_one("SELECT status FROM tasks WHERE id = ?", (int(task_id),))
    return str(row["status"] or "") if row else ""


def _task_is_paused(state: DesktopState, task_id: int | None) -> bool:
    return _task_status(state, task_id) == TaskStatus.PAUSED_NEEDS_USER.value


def _task_automation_blocked(state: DesktopState, task_id: int | None) -> bool:
    return _task_is_cancelled(state, task_id) or _task_is_paused(state, task_id)


def _task_block_label(state: DesktopState, task_id: int | None) -> str:
    if _task_is_cancelled(state, task_id):
        return "已终止"
    if _task_is_paused(state, task_id):
        return "已暂停"
    return "不可继续"


def _parse_candidate_card_text(text: str) -> dict[str, str]:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    age = (re.search(r"(\d{2}岁)", value) or [None, ""])[1]
    experience = (re.search(r"(工作\d+年)", value) or [None, ""])[1]
    education = (re.search(r"(博士|硕士|本科|大专|中专)", value) or [None, ""])[1]
    city = ""
    if education:
        after_edu = value.split(education, 1)[1].strip()
        city = (after_edu.split(" ", 1)[0] or "").strip("，,|")
    title = ""
    title_match = re.search(r"求职期望[:：]\s*([^·。；\n]{1,80})", value)
    if title_match:
        title = title_match.group(1).strip()
    company = ""
    company_match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9（）()·\-\s]{2,40})\s*·\s*([^·\s]{2,30})\s*\d{4}\.", value)
    if company_match:
        company = company_match.group(1).strip()
        if not title:
            title = company_match.group(2).strip()
    return {
        "title": title,
        "company": company,
        "city": city,
        "experience": experience or age,
        "education": education,
    }


def _valid_candidate_name(name: str) -> bool:
    value = re.sub(r"\s+", " ", str(name or "")).strip()
    if not value or len(value) > 20:
        return False
    if re.search(r"每日任务|我的主页|个人中心|安全中心|中文简历|查看大图|立即沟通|继续沟通|求职意向|简历信息|在线|活跃|方便联系时间", value):
        return False
    if re.search(r"\d{2}岁|工作\d+年|本科|硕士|博士|大专|中专", value):
        return False
    return bool(re.match(r"^[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z\*＊先生女士]{1,12}(?:\s*阅)?$", value))


def _clean_candidate_name(name: str) -> str:
    value = re.sub(r"\s+", " ", str(name or "")).strip()
    value = re.sub(r"\s*(?:阅|活跃|在线)$", "", value).strip()
    value = re.sub(r"^(?:今天活跃|3天内活跃|1周内活跃|30天内活跃)\s*", "", value).strip()
    return value


def _candidate_name_from_resume_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines() if line.strip()]
    start = 0
    for index, line in enumerate(lines):
        if line == "查看大图" or "简历编号" in line:
            start = index
            break
    for line in lines[start:start + 14]:
        if _valid_candidate_name(line):
            return line
    return ""


def _extract_search_click_hints(events: list[Any]) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in events:
        if not isinstance(item, dict) or item.get("type") != "click":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        target = payload.get("target")
        if not isinstance(target, dict):
            continue
        text = str(target.get("text") or "").strip()
        cls = str(target.get("cls") or "").strip()
        tag = str(target.get("tag") or "").strip().lower()
        role = str(target.get("role") or "").strip().lower()
        element_id = str(target.get("id") or "").strip()
        if not (
            re.search(r"搜\s*索|查\s*询|找\s*人", text)
            or re.search(r"search[-_ ]?btn|search", cls, flags=re.I)
            or re.search(r"search", element_id, flags=re.I)
        ):
            continue
        hint = {
            "tag": tag,
            "id": element_id,
            "cls": cls,
            "role": role,
            "text": text,
        }
        fingerprint = "|".join([hint["tag"], hint["id"], hint["cls"], hint["role"], hint["text"]])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        hints.append(hint)
    return hints


def _load_latest_search_click_hints(record_dir: Path) -> tuple[list[dict[str, str]], str]:
    if not record_dir.exists():
        return [], ""
    record_files = sorted(record_dir.glob("web_record_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in record_files:
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception:
            continue
        events = data.get("events")
        if not isinstance(events, list):
            continue
        hints = _extract_search_click_hints(events)
        if hints:
            return hints, str(path)
    return [], ""


def _resume_completeness(diagnostics: dict[str, Any], text: str) -> tuple[str, list[str], str]:
    text_length = int(diagnostics.get("textLength") or len(text))
    section_count = len(diagnostics.get("matchedSections") or [])
    action_count = len(diagnostics.get("matchedActions") or [])
    card_count = int(diagnostics.get("visibleCandidateCards") or 0)
    project_total = diagnostics.get("projectTotal")
    project_visible = int(diagnostics.get("projectVisible") or 0)
    warnings: list[str] = []

    if not diagnostics.get("isResumeDetail"):
        warnings.append("当前 URL 不是猎聘简历详情页，可能仍在搜索列表或其他页面。")
    if text_length < 800:
        warnings.append("页面文本少于 800 字，可能没有进入完整简历。")
    if section_count < 3:
        warnings.append("简历区块命中少于 3 个，可能只抓到摘要或列表页。")
    if card_count >= 3 and section_count < 3:
        warnings.append("页面上仍有多个候选人卡片，当前可能是搜索列表而非候选人详情。")
    if action_count == 0:
        warnings.append("未发现沟通/联系入口，可能不是可联系的简历详情页。")
    if project_total and project_visible < int(project_total):
        warnings.append(f"项目经历未完全展开：当前 {project_visible}/{project_total} 段。")
    if diagnostics.get("remainingProjectExpanders"):
        warnings.append("页面仍存在未点击的项目经历展开入口。")
    attachment_status = "none"
    if diagnostics.get("hasUnauthorizedAttachment"):
        attachment_status = "needs_request"
        warnings.append("存在附件简历但需要索要/授权，系统未自动获取附件内容。")
    elif diagnostics.get("hasAttachmentResume"):
        attachment_status = "present"
    if "登录" in text[:1000] and text_length < 1500:
        warnings.append("页面可能停留在登录或验证状态。")

    projects_complete = not project_total or project_visible >= int(project_total)
    if diagnostics.get("isResumeDetail") and text_length >= 1800 and section_count >= 5 and projects_complete:
        status = "高：像完整简历"
    elif diagnostics.get("isResumeDetail") and text_length >= 1000 and section_count >= 2:
        status = "中：可能是简历详情，但建议人工看一眼"
    else:
        status = "低：不建议直接评分/打招呼"
    return status, warnings, attachment_status


class DesktopState:
    def __init__(self, db: Database, env: EnvSettings) -> None:
        self.db = db
        self.env = env
        self.engine = TaskEngine(db)
        self.runtime = TaskRuntime(db, self.engine)
        self.executor = TaskExecutor(db, self.engine, self.runtime)
        self.current_account_id: int | None = None
        self.current_job_id: int | None = None
        self.current_task_id: int | None = None
        self.current_task_min_score: int | None = None
        self.current_task_auto_greet: bool | None = None
        self.current_task_dry_run: bool | None = None
        self.current_task_use_ai_scoring: bool | None = None
        self.current_task_queue_mode: bool = False
        self.last_candidate_id: int | None = None
        self.last_resume_text = ""
        self.last_greeting = ""
        self.last_greeting_log_id: int | None = None
        self.last_parsed_job_fields: dict[str, Any] = {}
        self.last_parsed_job_id: int | None = None
        self.profile = None
        self.profiles: dict[int, Any] = {}
        self.page = None
        self.last_load_progress = 0
        self.load_timeout_timer = None
        self.pending_auto_login_account_id: int | None = None
        self.pending_task_apply_id: int | None = None
        self.popup_open_count = 0
        self.search_click_hints: list[dict[str, str]] = []
        self.search_click_hints_source = ""
        self.next_candidate_index = 0
        self.last_opened_candidate_index: int | None = None
        self.current_list_page_index: int = 1
        self.current_page_card_count: int = 0
        self.cancelled_task_ids: set[int] = set()


def build_app() -> int:
    _configure_packaged_cwd()
    _configure_qt_webengine_env()
    qt = _require_qt()
    QApplication = qt["QApplication"]
    QCheckBox = qt["QCheckBox"]
    QComboBox = qt["QComboBox"]
    QDialog = qt["QDialog"]
    QDialogButtonBox = qt["QDialogButtonBox"]
    QFormLayout = qt["QFormLayout"]
    QGridLayout = qt["QGridLayout"]
    QGroupBox = qt["QGroupBox"]
    QHBoxLayout = qt["QHBoxLayout"]
    QLabel = qt["QLabel"]
    QLineEdit = qt["QLineEdit"]
    QListWidget = qt["QListWidget"]
    QListWidgetItem = qt["QListWidgetItem"]
    QMainWindow = qt["QMainWindow"]
    QMessageBox = qt["QMessageBox"]
    QScrollArea = qt["QScrollArea"]
    QPushButton = qt["QPushButton"]
    QSpinBox = qt["QSpinBox"]
    QSplitter = qt["QSplitter"]
    QTabWidget = qt["QTabWidget"]
    QTableWidget = qt["QTableWidget"]
    QTableWidgetItem = qt["QTableWidgetItem"]
    QTextEdit = qt["QTextEdit"]
    QTimer = qt["QTimer"]
    Qt = qt["Qt"]
    QUrl = qt["QUrl"]
    QVBoxLayout = qt["QVBoxLayout"]
    QWidget = qt["QWidget"]
    QWebEnginePage = qt["QWebEnginePage"]
    QWebEngineProfile = qt["QWebEngineProfile"]
    QWebEngineView = qt["QWebEngineView"]

    CITY_OPTIONS = [
        "北京",
        "上海",
        "广州",
        "深圳",
        "天津",
        "苏州",
        "重庆",
        "南京",
        "杭州",
        "大连",
        "成都",
        "武汉",
        "西安",
        "宁波",
        "嘉兴",
        "绍兴",
        "台州",
        "厦门",
        "青岛",
        "合肥",
        "长沙",
        "郑州",
        "无锡",
        "常州",
        "东莞",
        "佛山",
    ]
    INDUSTRY_OPTIONS = [
        "IT互联网技术",
        "电子/通信/半导体",
        "销售/客服",
        "运营",
        "人力/行政/财务/法务",
        "高级管理",
        "市场/公关/广告/会展",
        "生产/制造/研发",
        "制药/医疗器械/医疗护理",
        "汽车",
        "房地产/建筑/物业",
        "金融",
        "能源/化工/环保",
        "服装/纺织/皮革",
        "机械/设备/重工",
        "交通/物流/贸易",
    ]
    POSITION_OPTIONS = [
        "Java",
        "C++",
        "Python",
        "Golang",
        "Node.js",
        "前端开发",
        "算法工程师",
        "机器学习",
        "深度学习",
        "仿真工程师",
        "数字孪生工程师",
        "机械工程师",
        "UE4",
        "U3D",
        "项目经理",
        "产品经理",
        "生产计划/排程",
        "工业工程师",
        "自动化工程师",
        "机器人开发工程师",
    ]

    app = QApplication(sys.argv)
    license_check = check_license()
    if not license_check.ok:
        dialog = QDialog()
        dialog.setWindowTitle("未授权电脑")
        dialog.setModal(True)
        dialog.resize(620, 420)
        layout = QVBoxLayout(dialog)
        title = QLabel("当前电脑未获得授权，应用无法启动。")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        detail = QTextEdit()
        detail.setReadOnly(True)
        detail.setPlainText(
            "\n".join(
                [
                    f"原因：{license_check.reason}",
                    "",
                    "请把下面的机器码发给授权管理员：",
                    license_check.machine_id,
                    "",
                    "授权文件放置位置：",
                    *[str(path) for path in default_license_paths()],
                    "",
                    "拿到 license.json 后，请放到软件目录或 data 目录下，然后重新打开应用。",
                ]
            )
        )
        layout.addWidget(detail)
        buttons = QHBoxLayout()
        copy_machine_btn = QPushButton("复制机器码")
        copy_report_btn = QPushButton("复制完整信息")
        close_btn = QPushButton("退出")
        buttons.addWidget(copy_machine_btn)
        buttons.addWidget(copy_report_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        copy_machine_btn.clicked.connect(lambda: app.clipboard().setText(license_check.machine_id))
        copy_report_btn.clicked.connect(lambda: app.clipboard().setText(detail.toPlainText()))
        close_btn.clicked.connect(dialog.reject)
        dialog.exec()
        return 2
    app.setStyleSheet(
        """
        QWidget {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial;
            font-size: 13px;
            color: #1d1d1f;
            background: #f5f5f7;
        }
        QMainWindow, QDialog {
            background: #f5f5f7;
        }
        QTabWidget::pane {
            border: 1px solid #d2d2d7;
            border-radius: 12px;
            background: #ffffff;
        }
        QTabBar::tab {
            min-height: 24px;
            padding: 5px 14px;
            margin: 2px;
            border-radius: 7px;
            background: transparent;
            color: #515154;
        }
        QTabBar::tab:selected {
            background: #007aff;
            color: #ffffff;
        }
        QPushButton {
            border: 0;
            border-radius: 8px;
            padding: 7px 14px;
            background: #e8e8ed;
            color: #1d1d1f;
        }
        QPushButton:hover {
            background: #dedee4;
        }
        QPushButton:pressed {
            background: #d2d2d7;
        }
        QPushButton:disabled {
            color: #a1a1a6;
            background: #eeeeef;
        }
        QLineEdit, QTextEdit, QSpinBox, QComboBox {
            border: 1px solid #d2d2d7;
            border-radius: 8px;
            padding: 6px 8px;
            background: #ffffff;
            selection-background-color: #007aff;
        }
        QTextEdit {
            padding: 8px;
        }
        QTableWidget {
            border: 1px solid #d2d2d7;
            border-radius: 10px;
            background: #ffffff;
            gridline-color: #eeeeef;
            selection-background-color: #d6eaff;
            selection-color: #1d1d1f;
        }
        QHeaderView::section {
            background: #fbfbfd;
            border: 0;
            border-bottom: 1px solid #e5e5ea;
            padding: 7px 8px;
            color: #6e6e73;
            font-weight: 600;
        }
        QGroupBox {
            border: 1px solid #d2d2d7;
            border-radius: 12px;
            margin-top: 10px;
            padding: 14px 10px 10px 10px;
            background: #ffffff;
            font-weight: 600;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 4px;
        }
        QSplitter::handle {
            background: #e5e5ea;
        }
        """
    )
    db = Database()
    db.init()
    state = DesktopState(db, load_env())
    record_dir = Path.cwd() / "data" / "recordings"
    loaded_hints, loaded_source = _load_latest_search_click_hints(record_dir)
    if loaded_hints:
        state.search_click_hints = loaded_hints
        state.search_click_hints_source = loaded_source

    window = QMainWindow()
    window.setWindowTitle("猎聘招聘智能体工作台")
    window.resize(1600, 980)

    root = QSplitter()
    tabs = QTabWidget()
    tabs.setMinimumWidth(560)
    tabs.setMaximumWidth(700)

    class BrowserPane(QWidget):
        def __init__(self) -> None:
            super().__init__()
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self.tabs = QTabWidget()
            self.tabs.setTabsClosable(True)
            self.tabs.setMovable(True)
            self.tabs.tabCloseRequested.connect(self._close_tab)
            layout.addWidget(self.tabs)
            self._ensure_home_tab()

        def _short_label(self, text: str) -> str:
            value = (text or "").strip()
            if not value:
                return "新页面"
            return value if len(value) <= 20 else f"{value[:20]}…"

        def _refresh_tab_title(self, view: Any, fallback: str = "新页面") -> None:
            idx = self.tabs.indexOf(view)
            if idx < 0:
                return
            title = (view.title() or "").strip()
            if not title:
                title = (view.url().toString() or "").strip()
            if not title:
                title = fallback
            self.tabs.setTabText(idx, self._short_label(title))
            self.tabs.setTabToolTip(idx, view.url().toString() or "")

        def _bind_view(self, view: Any, fallback: str = "新页面") -> None:
            view.titleChanged.connect(lambda _text, v=view, fb=fallback: self._refresh_tab_title(v, fb))
            view.urlChanged.connect(lambda _url, v=view, fb=fallback: self._refresh_tab_title(v, fb))

        def _create_view(self, label: str) -> Any:
            view = QWebEngineView()
            self._bind_view(view, label)
            self.tabs.addTab(view, self._short_label(label))
            self._refresh_tab_title(view, label)
            return view

        def _ensure_home_tab(self) -> Any:
            if self.tabs.count() > 0:
                widget = self.tabs.widget(0)
                if widget:
                    return widget
            return self._create_view("找人页")

        def _close_tab(self, index: int) -> None:
            if self.tabs.count() <= 1:
                return
            widget = self.tabs.widget(index)
            self.tabs.removeTab(index)
            if widget:
                widget.deleteLater()

        def reset_tabs(self) -> Any:
            while self.tabs.count():
                widget = self.tabs.widget(0)
                self.tabs.removeTab(0)
                if widget:
                    widget.deleteLater()
            view = self._create_view("找人页")
            self.tabs.setCurrentWidget(view)
            return view

        def current_view(self) -> Any:
            widget = self.tabs.currentWidget()
            if widget:
                return widget
            return self._ensure_home_tab()

        def home_view(self) -> Any:
            return self._ensure_home_tab()

        def switch_home(self) -> Any:
            view = self.home_view()
            self.tabs.setCurrentWidget(view)
            return view

        def open_home_url(self, url: Any) -> None:
            view = self.home_view()
            self.tabs.setCurrentWidget(view)
            view.page().setUrl(url)

        def new_tab(self, label: str = "新页面", switch: bool = True) -> Any:
            view = self._create_view(label)
            if switch:
                self.tabs.setCurrentWidget(view)
            return view

        def page(self) -> Any:
            return self.current_view().page()

        def setPage(self, page: Any) -> None:  # noqa: N802 - Qt style
            self.current_view().setPage(page)

        def url(self) -> Any:
            return self.current_view().url()

        def close_current_detail_tab(self) -> bool:
            index = self.tabs.currentIndex()
            if index <= 0 or self.tabs.count() <= 1:
                self.switch_home()
                return False
            self._close_tab(index)
            self.switch_home()
            return True

        def close_work_tabs(self) -> int:
            self.switch_home()
            closed = 0
            for index in range(self.tabs.count() - 1, 0, -1):
                self._close_tab(index)
                closed += 1
            self.switch_home()
            return closed

    web = BrowserPane()
    page_adapter = LiepinPageAdapter(web, lambda delay_ms, callback: QTimer.singleShot(delay_ms, callback))
    process_log = QTextEdit()
    process_log.setReadOnly(True)
    process_log.document().setMaximumBlockCount(1000)
    result = QTextEdit()
    result.setReadOnly(True)
    mode_status_label = None

    def append_log(lines: list[str]) -> None:
        if not lines:
            return
        stamped = [f"[{datetime.now().strftime('%H:%M:%S')}] {lines[0]}"] + [f"  {line}" for line in lines[1:]]
        process_log.append("\n".join(stamped))

    def message(text: str) -> None:
        QMessageBox.information(window, "猎聘招聘智能体", text)

    class MultiSelectField(QWidget):
        def __init__(
            self,
            title: str,
            options: list[str],
            *,
            allow_custom: bool = False,
            placeholder: str = "",
        ) -> None:
            super().__init__()
            self.title = title
            self.options = [str(item).strip() for item in options if str(item).strip()]
            self.allow_custom = allow_custom
            self.edit = QLineEdit()
            self.edit.setReadOnly(True)
            self.edit.setPlaceholderText(placeholder or ("可多选" if options else "可填写多个值"))
            self.button = QPushButton("选择")
            wrapper = QHBoxLayout(self)
            wrapper.setContentsMargins(0, 0, 0, 0)
            wrapper.setSpacing(6)
            wrapper.addWidget(self.edit, 1)
            wrapper.addWidget(self.button)
            self.button.clicked.connect(self.open_dialog)

        def values(self) -> list[str]:
            return _csv(self.edit.text())

        def set_values(self, values: Any) -> None:
            if isinstance(values, str):
                items = _csv(values)
            elif isinstance(values, list):
                items = [str(item).strip() for item in values if str(item).strip()]
            else:
                items = []
            self.edit.setText("，".join(_unique_preserve(items, limit=50)))

        def text(self) -> str:
            return self.edit.text()

        def setText(self, value: str) -> None:  # noqa: N802 - keep QLineEdit-like API
            self.set_values(value)

        def open_dialog(self) -> None:
            dialog = QDialog(window)
            dialog.setWindowTitle(self.title)
            dialog.setModal(True)
            dialog.resize(460, 560)
            layout = QVBoxLayout(dialog)
            hint = "勾选需要的选项。"
            if self.allow_custom:
                hint += " 下方可以补充自定义值，用逗号或换行分隔。"
            layout.addWidget(QLabel(hint))
            list_widget = QListWidget()
            selected = set(self.values())
            for option in self.options:
                item = QListWidgetItem(option)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if option in selected else Qt.CheckState.Unchecked)
                list_widget.addItem(item)
            layout.addWidget(list_widget, 1)
            custom_edit = QTextEdit()
            custom_edit.setPlaceholderText("补充自定义值")
            custom_values = [value for value in self.values() if value not in set(self.options)]
            custom_edit.setPlainText("\n".join(custom_values))
            if self.allow_custom:
                layout.addWidget(custom_edit)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.Accepted:
                return
            values: list[str] = []
            for index in range(list_widget.count()):
                item = list_widget.item(index)
                if item.checkState() == Qt.CheckState.Checked:
                    values.append(item.text())
            if self.allow_custom:
                values.extend(_csv(custom_edit.toPlainText()))
            self.set_values(values)

    def table(headers: list[str]) -> Any:
        widget = QTableWidget()
        widget.setColumnCount(len(headers))
        widget.setHorizontalHeaderLabels(headers)
        widget.setSelectionBehavior(QTableWidget.SelectRows)
        widget.setEditTriggers(QTableWidget.NoEditTriggers)
        return widget

    accounts_table = table(["ID", "账号", "用户名", "状态", "Profile", "启用"])
    jobs_table = table(["ID", "岗位", "查询概览", "阈值", "自动打招呼", "Dry-run"])
    tasks_table = table(["ID", "任务", "账号", "状态", "进度"])
    candidates_table = table(["ID", "选", "候选人", "评分", "状态", "关键信息", "来源", "更新"])
    alerts_table = table(["ID", "级别", "状态", "原因", "处理提示", "时间"])
    logs_table = table(["ID", "级别", "步骤", "消息", "时间"])

    account_combo = QComboBox()
    job_combo = QComboBox()
    task_job_combo = QComboBox()
    task_account_combo = QComboBox()
    candidate_job_filter = QComboBox()
    candidate_page = {"page": 1, "page_size": 100, "total": 0}
    candidate_page_label = None
    candidate_prev_page_btn = None
    candidate_next_page_btn = None
    task_rows_cache: list[Any] = []
    task_summary_label = None
    task_detail_text = None
    task_recent_logs_text = None

    def selected_id(table_widget: Any) -> int | None:
        row = table_widget.currentRow()
        if row < 0:
            return None
        item = table_widget.item(row, 0)
        if not item:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def set_rows(table_widget: Any, rows: list[list[Any]]) -> None:
        table_widget.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table_widget.setItem(r, c, QTableWidgetItem("" if value is None else str(value)))
        table_widget.resizeColumnsToContents()

    def set_candidate_rows(rows: list[list[Any]], checked_ids: set[int]) -> None:
        candidates_table.blockSignals(True)
        candidates_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            candidate_id = int(row[0])
            for c, value in enumerate(row):
                if c == 1:
                    item = QTableWidgetItem("")
                    item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    item.setCheckState(Qt.CheckState.Checked if candidate_id in checked_ids else Qt.CheckState.Unchecked)
                else:
                    item = QTableWidgetItem("" if value is None else str(value))
                candidates_table.setItem(r, c, item)
        candidates_table.resizeColumnsToContents()
        candidates_table.blockSignals(False)

    def refresh_accounts() -> None:
        rows = db.fetch_all("SELECT * FROM accounts ORDER BY id DESC")
        set_rows(
            accounts_table,
            [[r["id"], r["name"], r["username"], r["status"], r["profile_dir"], "是" if r["enabled"] else "否"] for r in rows],
        )
        account_combo.clear()
        task_account_combo.clear()
        for r in rows:
            label = f'{r["id"]}: {r["name"]}'
            account_combo.addItem(label, int(r["id"]))
            task_account_combo.addItem(label, int(r["id"]))

    def refresh_jobs() -> None:
        keep_values = {
            job_combo: job_combo.currentData(),
            task_job_combo: task_job_combo.currentData(),
            candidate_job_filter: candidate_job_filter.currentData(),
        }
        rows = db.fetch_all("SELECT * FROM jobs ORDER BY id DESC")
        def query_summary(row: Any) -> str:
            conditions = row_search_conditions(row)
            chips: list[str] = []
            for text in [
                _join_values(conditions.get("expected_city")),
                _join_values(conditions.get("work_years")),
                _join_values(conditions.get("education")),
                _join_values(conditions.get("current_industry")),
                _join_values(conditions.get("current_position")),
            ]:
                if text:
                    chips.append(text)
            if not chips:
                chips.append("未配置")
            return _compact_text(" / ".join(chips), 34)
        set_rows(
            jobs_table,
            [
                [
                    r["id"],
                    r["title"],
                    query_summary(r),
                    r["min_score"],
                    "是" if r["auto_greet"] else "否",
                    "是" if r["dry_run"] else "否",
                ]
                for r in rows
            ],
        )
        for combo in (job_combo, task_job_combo, candidate_job_filter):
            combo.clear()
            combo.addItem("全部岗位", None)
            for r in rows:
                combo.addItem(f'{r["id"]}: {r["title"]}', int(r["id"]))
            keep_value = keep_values.get(combo)
            if keep_value is not None:
                index = combo.findData(keep_value)
                if index >= 0:
                    combo.setCurrentIndex(index)

    def refresh_tasks() -> None:
        nonlocal task_rows_cache
        keep_task_id = selected_id(tasks_table)
        rows = db.fetch_all(
            """
            SELECT
                t.*,
                j.title AS job_title,
                j.min_score AS job_min_score,
                a.name AS account_name,
                (
                    SELECT COUNT(*) FROM candidates c
                    WHERE c.source_task_id = t.id
                      AND (
                          c.last_snapshot_id IS NOT NULL
                          OR c.resume_status IN ('fetched', 'needs_attachment', 'incomplete')
                          OR c.score IS NOT NULL
                      )
                ) AS candidate_count,
                (
                    SELECT COUNT(*) FROM candidates c
                    WHERE c.source_task_id = t.id
                      AND (c.last_snapshot_id IS NOT NULL OR c.resume_status IN ('fetched', 'needs_attachment', 'incomplete'))
                ) AS fetched_count,
                (
                    SELECT COUNT(*) FROM candidates c
                    WHERE c.source_task_id = t.id
                      AND c.score IS NOT NULL
                ) AS scored_count,
                (
                    SELECT COUNT(DISTINCT gl.candidate_id) FROM greeting_logs gl
                    WHERE gl.task_id = t.id
                      AND gl.candidate_id IS NOT NULL
                      AND gl.status IN ('opening_selected_dry_run', 'opening_sent', 'followup_filled_dry_run', 'followup_filled_not_sent', 'followup_sent', 'continued_followup_filled_dry_run', 'continued_followup_sent', 'followup_already_exists', 'sent')
                ) AS greeted_count,
                (
                    SELECT COUNT(*) FROM alerts al
                    WHERE al.task_id = t.id
                      AND al.status = 'open'
                ) AS open_alert_count
            FROM tasks t
            JOIN jobs j ON j.id = t.job_id
            JOIN accounts a ON a.id = t.account_id
            ORDER BY COALESCE(t.sort_order, t.id) ASC, t.id ASC
            """
        )
        task_rows_cache = rows

        def status_text(value: str) -> str:
            mapping = {
                "pending": "待执行",
                "running": "执行中",
                "paused_needs_user": "待人工处理",
                "done": "已完成",
                "failed": "失败",
                "skipped": "已跳过",
                "cancelled": "已终止",
            }
            return mapping.get(value, value)

        def progress_text(row: Any) -> str:
            progress = state.runtime.progress(int(row["id"]))
            current_count = progress.current_count
            max_candidates = progress.max_candidates
            shown_count = min(current_count, max_candidates) if max_candidates > 0 else current_count
            return (
                f"{progress.target_label} {shown_count}/{max_candidates if max_candidates > 0 else current_count}\n"
                f"抓{int(row['fetched_count'] or 0)} 评{int(row['scored_count'] or 0)} 沟通{int(row['greeted_count'] or 0)} 提醒{int(row['open_alert_count'] or 0)}"
            )

        set_rows(
            tasks_table,
            [
                [
                    r["id"],
                    f'{r["name"]}\n{_compact_text(r["job_title"], 26)}',
                    r["account_name"],
                    status_text(r["status"]),
                    progress_text(r),
                ]
                for r in rows
            ],
        )
        tasks_table.setColumnHidden(0, True)
        tasks_table.setColumnWidth(1, 245)
        tasks_table.setColumnWidth(2, 64)
        tasks_table.setColumnWidth(3, 76)
        tasks_table.horizontalHeader().setStretchLastSection(True)
        tasks_table.resizeRowsToContents()
        for row_index in range(tasks_table.rowCount()):
            tasks_table.setRowHeight(row_index, max(tasks_table.rowHeight(row_index), 48))
        if task_summary_label is not None:
            pending = sum(1 for row in rows if row["status"] == "pending")
            running = sum(1 for row in rows if row["status"] == "running")
            paused = sum(1 for row in rows if row["status"] == "paused_needs_user")
            cancelled = sum(1 for row in rows if row["status"] == "cancelled")
            alerts = sum(int(row["open_alert_count"] or 0) for row in rows)
            summary_parts = [f"任务 {len(rows)}", f"运行 {running}"]
            if pending:
                summary_parts.append(f"待执行 {pending}")
            if paused:
                summary_parts.append(f"待处理 {paused}")
            if cancelled:
                summary_parts.append(f"已终止 {cancelled}")
            if alerts:
                summary_parts.append(f"提醒 {alerts}")
            task_summary_label.setText(" · ".join(summary_parts))
        if keep_task_id:
            for idx, task_row in enumerate(rows):
                if int(task_row["id"]) == int(keep_task_id):
                    tasks_table.selectRow(idx)
                    break
        elif rows and tasks_table.currentRow() < 0:
            tasks_table.selectRow(0)
        update_selected_task_panel()

    def refresh_candidates() -> None:
        keep_candidate_id = selected_id(candidates_table)
        checked_ids = checked_candidate_ids() if candidates_table.columnCount() > 1 else set()
        job_id = candidate_job_filter.currentData()
        greeted_placeholders = ",".join("?" for _ in GREETED_STATUSES)
        contacted_sql = (
            f"(c.greeting_status IN ({greeted_placeholders}) "
            f"OR EXISTS (SELECT 1 FROM greeting_logs gls WHERE gls.candidate_id = c.id AND gls.status IN ({greeted_placeholders})))"
        )

        def job_where(prefix: str = "c") -> tuple[list[str], list[Any]]:
            conditions = [f"{prefix}.resume_status != 'list_card'"]
            values: list[Any] = []
            if job_id:
                conditions.append(f"{prefix}.job_id = ?")
                values.append(job_id)
            return conditions, values

        def score_filter_sql() -> tuple[str, list[Any]]:
            mode = candidate_score_filter_combo.currentData()
            if mode == "unscored":
                return "c.score IS NULL", []
            if mode == "lt60":
                return "c.score < 60", []
            if mode == "60_74":
                return "c.score BETWEEN 60 AND 74", []
            if mode == "75_89":
                return "c.score BETWEEN 75 AND 89", []
            if mode == "ge90":
                return "c.score >= 90", []
            return "", []

        def status_filter_sql() -> tuple[str, list[Any]]:
            selected = set(candidate_state_filter_keys)
            normal_selected = selected & {"unscored", "scored", "contacted"}
            parts: list[str] = []
            values: list[Any] = []
            normal_parts: list[str] = []
            if {"unscored", "scored"} <= normal_selected:
                normal_parts.append("1 = 1")
            else:
                if "unscored" in normal_selected:
                    normal_parts.append("c.score IS NULL")
                if "scored" in normal_selected:
                    normal_parts.append("c.score IS NOT NULL")
            if "contacted" in normal_selected and "1 = 1" not in normal_parts:
                normal_parts.append(contacted_sql)
                values.extend(GREETED_STATUSES)
                values.extend(GREETED_STATUSES)
            if normal_parts:
                parts.append(f"(c.candidate_state != 'rejected' AND ({' OR '.join(normal_parts)}))")
            if "rejected" in selected:
                parts.append("c.candidate_state = 'rejected'")
            if not parts:
                return "1 = 0", []
            return f"({' OR '.join(parts)})", values

        def search_filter_sql() -> tuple[str, list[Any]]:
            query = candidate_search_edit.text().strip()
            if not query:
                return "", []
            like_value = f"%{query}%"
            fields = [
                "c.name",
                "c.title",
                "c.company",
                "c.city",
                "c.experience",
                "c.education",
                "j.title",
                "a.name",
                "t.name",
                "c.score_summary",
                "c.profile_url",
            ]
            clauses = [f"COALESCE({field}, '') LIKE ?" for field in fields]
            values = [like_value for _ in fields]
            clauses.append("EXISTS (SELECT 1 FROM score_results srs WHERE srs.candidate_id = c.id AND COALESCE(srs.summary, '') LIKE ?)")
            values.append(like_value)
            return f"({' OR '.join(clauses)})", values

        def order_sql() -> str:
            mode = candidate_sort_combo.currentData()
            if mode == "score_asc":
                return "c.score IS NULL ASC, c.score ASC, c.id ASC"
            if mode == "created_desc":
                return "c.created_at DESC, c.id DESC"
            if mode == "updated_desc":
                return "c.updated_at DESC, c.id DESC"
            return "c.score IS NULL ASC, c.score DESC, c.updated_at DESC, c.id DESC"

        base_conditions, base_params = job_where("c")
        list_card_conditions = ["c.resume_status = 'list_card'"]
        list_card_params: list[Any] = []
        if job_id:
            list_card_conditions.append("c.job_id = ?")
            list_card_params.append(job_id)
        stats_row = db.fetch_one(
            f"""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN c.score IS NOT NULL THEN 1 ELSE 0 END), 0) AS scored,
                COALESCE(SUM(CASE WHEN {contacted_sql} THEN 1 ELSE 0 END), 0) AS contacted,
                COALESCE(SUM(CASE WHEN c.candidate_state = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected
            FROM candidates c
            WHERE {' AND '.join(base_conditions)}
            """,
            [*GREETED_STATUSES, *GREETED_STATUSES, *base_params],
        )
        list_card_row = db.fetch_one(
            f"SELECT COUNT(*) AS count FROM candidates c WHERE {' AND '.join(list_card_conditions)}",
            list_card_params,
        )
        filtered_conditions = list(base_conditions)
        filtered_params = list(base_params)
        for sql_part, sql_values in (score_filter_sql(), status_filter_sql(), search_filter_sql()):
            if sql_part:
                filtered_conditions.append(sql_part)
                filtered_params.extend(sql_values)
        from_sql = """
            FROM candidates c
            JOIN jobs j ON j.id = c.job_id
            LEFT JOIN accounts a ON a.id = c.source_account_id
            LEFT JOIN tasks t ON t.id = c.source_task_id
        """
        where_sql = f"WHERE {' AND '.join(filtered_conditions)}"
        filtered_count_row = db.fetch_one(f"SELECT COUNT(*) AS count {from_sql} {where_sql}", filtered_params)
        filtered_count = int(filtered_count_row["count"] if filtered_count_row else 0)
        page_size = max(20, int(candidate_page.get("page_size") or 100))
        page_count = max(1, (filtered_count + page_size - 1) // page_size)
        current_page = min(max(1, int(candidate_page.get("page") or 1)), page_count)
        candidate_page["page"] = current_page
        candidate_page["page_size"] = page_size
        candidate_page["total"] = filtered_count
        offset = (current_page - 1) * page_size
        base_sql = """
            SELECT
                c.*,
                j.title AS job_title,
                a.name AS account_name,
                t.name AS task_name,
                rs.completeness AS snapshot_completeness,
                rs.text_length AS snapshot_text_length,
                rs.created_at AS snapshot_at,
                sr.summary AS latest_score_summary,
                sr.created_at AS score_at,
                gl.status AS latest_greeting_status,
                gl.dry_run AS latest_greeting_dry_run,
                gl.created_at AS greeting_at
            FROM candidates c
            JOIN jobs j ON j.id = c.job_id
            LEFT JOIN accounts a ON a.id = c.source_account_id
            LEFT JOIN tasks t ON t.id = c.source_task_id
            LEFT JOIN resume_snapshots rs ON rs.id = c.last_snapshot_id
            LEFT JOIN score_results sr ON sr.id = (
                SELECT id FROM score_results WHERE candidate_id = c.id ORDER BY id DESC LIMIT 1
            )
            LEFT JOIN greeting_logs gl ON gl.id = (
                SELECT id FROM greeting_logs WHERE candidate_id = c.id ORDER BY id DESC LIMIT 1
            )
        """
        rows = db.fetch_all(
            f"""
            {base_sql}
            {where_sql}
            ORDER BY {order_sql()}
            LIMIT ? OFFSET ?
            """,
            [*filtered_params, page_size, offset],
        )
        total = int(stats_row["total"] if stats_row else 0)
        scored = int(stats_row["scored"] if stats_row else 0)
        contacted = int(stats_row["contacted"] if stats_row else 0)
        rejected = int(stats_row["rejected"] if stats_row else 0)
        list_cards = int(list_card_row["count"] if list_card_row else 0)
        if candidate_stats_label is not None:
            candidate_stats_label.setText(
                f"候选人清单    本页 {len(rows)} / 筛选 {filtered_count} / 正式 {total} · 已评 {scored} · 已沟通 {contacted} · 淘汰 {rejected} · 列表暂存 {list_cards}"
            )
        if candidate_page_label is not None:
            candidate_page_label.setText(f"第 {current_page} / {page_count} 页")
        if candidate_prev_page_btn is not None:
            candidate_prev_page_btn.setEnabled(current_page > 1)
        if candidate_next_page_btn is not None:
            candidate_next_page_btn.setEnabled(current_page < page_count)

        def candidate_identity(row: Any) -> str:
            meta = " · ".join(
                str(item)
                for item in [row["city"] or "", row["experience"] or "", row["education"] or ""]
                if str(item or "").strip()
            )
            return f"{row['name'] or '-'}\n{meta or '-'}"

        def candidate_summary(row: Any) -> str:
            if row["latest_score_summary"]:
                return _compact_text(row["latest_score_summary"], 80)
            parts = [
                row["company"] or "",
                row["title"] or "",
                row["job_title"] or "",
            ]
            if not any(str(part or "").strip() for part in parts):
                return _compact_text(row["profile_url"] or "", 80)
            return _compact_text(" · ".join(str(part) for part in parts if str(part or "").strip()), 80)

        def candidate_source(row: Any) -> str:
            source = row["account_name"] or "-"
            task_name = row["task_name"] or row["job_title"] or ""
            if task_name:
                return f"{source}\n{_compact_text(task_name, 18)}"
            return source

        set_candidate_rows(
            [
                [
                    r["id"],
                    "",
                    candidate_identity(r),
                    r["score"] if r["score"] is not None else "-",
                    _candidate_primary_status(r),
                    candidate_summary(r),
                    candidate_source(r),
                    r["updated_at"],
                ]
                for r in rows
            ],
            checked_ids,
        )
        candidates_table.setColumnHidden(0, True)
        candidates_table.setColumnWidth(1, 42)
        candidates_table.setColumnWidth(2, 150)
        candidates_table.setColumnWidth(3, 55)
        candidates_table.setColumnWidth(4, 70)
        candidates_table.setColumnWidth(5, 340)
        candidates_table.setColumnWidth(6, 135)
        candidates_table.setColumnWidth(7, 140)
        candidates_table.horizontalHeader().setStretchLastSection(True)
        candidates_table.resizeRowsToContents()
        for row_index in range(candidates_table.rowCount()):
            candidates_table.setRowHeight(row_index, max(candidates_table.rowHeight(row_index), 54))
        update_candidate_state_filter_button()
        if keep_candidate_id:
            for idx, row in enumerate(rows):
                if int(row["id"]) == int(keep_candidate_id):
                    candidates_table.selectRow(idx)
                    break
        elif rows and candidates_table.currentRow() < 0:
            candidates_table.selectRow(0)
        selected_row = candidate_detail_row(selected_candidate_id()) if selected_candidate_id() else None
        if selected_row and str(selected_row["candidate_state"] or "active") == "rejected":
            candidate_reject_btn.setText("恢复候选")
        else:
            candidate_reject_btn.setText("淘汰")
        update_candidate_selection_bar()

    def refresh_alerts() -> None:
        rows = db.fetch_all("SELECT * FROM alerts ORDER BY id DESC LIMIT 200")
        set_rows(alerts_table, [[r["id"], r["severity"], r["status"], r["reason"], r["action_hint"], r["created_at"]] for r in rows])

    def refresh_logs() -> None:
        rows = db.fetch_all("SELECT * FROM execution_logs ORDER BY id DESC LIMIT 300")
        set_rows(logs_table, [[r["id"], r["level"], r["step"], r["message"], r["created_at"]] for r in rows])

    def refresh_mode_status() -> None:
        nonlocal mode_status_label
        if mode_status_label is None:
            return
        def count(sql: str) -> int:
            row = db.fetch_one(sql)
            return int(row["c"]) if row else 0

        running_task_count = count("SELECT COUNT(*) AS c FROM tasks WHERE status = 'running'")
        open_alert_count = count("SELECT COUNT(*) AS c FROM alerts WHERE status = 'open'")
        parts = ["运行中" if running_task_count else "空闲"]
        if open_alert_count:
            parts.append(f"提醒 {open_alert_count}")
        mode_status_label.setText(" · ".join(parts))

    def update_selected_task_panel() -> None:
        if task_detail_text is None or task_recent_logs_text is None:
            return
        task_id = selected_id(tasks_table)
        if not task_id:
            task_detail_text.setPlainText("请选择一条任务。")
            task_recent_logs_text.setPlainText("")
            return
        row = db.fetch_one(
            """
            SELECT
                t.*,
                j.title AS job_title,
                j.min_score AS job_min_score,
                a.name AS account_name,
                a.username AS account_username,
                t.use_ai_scoring AS use_ai_scoring,
                (
                    SELECT COUNT(*) FROM candidates c
                    WHERE c.source_task_id = t.id
                      AND (
                          c.last_snapshot_id IS NOT NULL
                          OR c.resume_status IN ('fetched', 'needs_attachment', 'incomplete')
                          OR c.score IS NOT NULL
                      )
                ) AS candidate_count,
                (
                    SELECT COUNT(*) FROM candidates c
                    WHERE c.source_task_id = t.id
                      AND (c.last_snapshot_id IS NOT NULL OR c.resume_status IN ('fetched', 'needs_attachment', 'incomplete'))
                ) AS fetched_count,
                (
                    SELECT COUNT(*) FROM candidates c
                    WHERE c.source_task_id = t.id
                      AND c.score IS NOT NULL
                ) AS scored_count,
                (
                    SELECT COUNT(DISTINCT gl.candidate_id) FROM greeting_logs gl
                    WHERE gl.task_id = t.id
                      AND gl.candidate_id IS NOT NULL
                      AND gl.status IN ('opening_selected_dry_run', 'opening_sent', 'followup_filled_dry_run', 'followup_filled_not_sent', 'followup_sent', 'continued_followup_filled_dry_run', 'continued_followup_sent', 'followup_already_exists', 'sent')
                ) AS greeted_count,
                (
                    SELECT COUNT(*) FROM alerts al
                    WHERE al.task_id = t.id
                      AND al.status = 'open'
                ) AS open_alert_count
            FROM tasks t
            JOIN jobs j ON j.id = t.job_id
            JOIN accounts a ON a.id = t.account_id
            WHERE t.id = ?
            """,
            (int(task_id),),
        )
        if not row:
            task_detail_text.setPlainText("任务不存在。")
            task_recent_logs_text.setPlainText("")
            return
        status_hint = {
            "pending": "下一步：运行队首任务或运行选中任务。",
            "running": "下一步：查看右侧猎聘页面，按当前步骤继续验收。",
            "paused_needs_user": "下一步：处理提醒后继续运行选中任务。",
            "failed": "下一步：查看最近日志，修复后重置为待执行。",
            "done": "下一步：查看候选人清单和沟通记录。",
            "skipped": "下一步：如需重跑，重置为待执行。",
            "cancelled": "下一步：如需重跑，重置为待执行。",
        }.get(row["status"], "下一步：查看日志确认状态。")
        threshold = row["greet_min_score"] if row["greet_min_score"] is not None else row["job_min_score"]
        progress = state.runtime.progress(int(task_id))
        current_count = progress.current_count
        max_candidates = progress.max_candidates
        shown_count = min(current_count, max_candidates) if max_candidates > 0 else current_count
        checkpoint = loads(row["checkpoint_json"], {}) if "checkpoint_json" in row.keys() else {}
        if not isinstance(checkpoint, dict):
            checkpoint = {}
        checkpoint_line = (
            f"断点：{checkpoint.get('resume_action') or '-'} / 第 {int(checkpoint.get('page_index') or 1)} 页 / "
            f"下一位第 {int(checkpoint.get('next_candidate_index') or 0) + 1} 位 / 保存于 {checkpoint.get('saved_at') or '-'}"
            if checkpoint
            else "断点：-"
        )
        detail_lines = [
            f"{row['name']}    ID：{row['id']}",
            f"岗位：{row['job_title']}",
            f"账号：{row['account_name']} / {row['account_username']}",
            f"状态：{row['status']}    步骤：{row['current_step']}",
            checkpoint_line,
            f"目标：{progress.target_label} {shown_count}/{row['max_candidates']}，已抓 {row['fetched_count']}，已评 {row['scored_count']}，已沟通 {row['greeted_count']}",
            f"提醒：{row['open_alert_count']}    优先级：{row['priority']}    排序：{row['sort_order']}",
            f"任务筛选：年龄 {row['age_min'] or '-'}-{row['age_max'] or '-'}",
            f"结果过滤：已查看={'隐藏' if row['hide_viewed'] else '不隐藏'} / 已沟通={'隐藏' if row['hide_contacted'] else '不隐藏'} / 已获取联系方式={'隐藏' if row['hide_contact_info'] else '不隐藏'}",
            f"模式：{'自动沟通' if row['auto_greet'] else '只抓取'} / {'Dry-run' if row['dry_run'] else '实发'} / {'AI评分' if row['use_ai_scoring'] else '关键词评分'} / 阈值 {threshold}",
            f"计划：{row['schedule_text'] or '立即'}    重试：{row['attempt_count']}/{row['retry_limit']}，间隔 {row['retry_interval_sec']} 秒",
            f"最近运行：{row['last_run_at'] or '-'}    下次运行：{row['next_run_at'] or '-'}",
            f"错误：{row['last_error'] or '-'}",
            "",
            status_hint,
        ]
        task_detail_text.setPlainText("\n".join(detail_lines))
        logs = db.fetch_all(
            """
            SELECT level, step, message, created_at
            FROM execution_logs
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (int(task_id),),
        )
        alerts = db.fetch_all(
            """
            SELECT severity, reason, action_hint, created_at
            FROM alerts
            WHERE task_id = ? AND status = 'open'
            ORDER BY id DESC
            LIMIT 5
            """,
            (int(task_id),),
        )
        log_lines: list[str] = []
        if alerts:
            log_lines.append("未处理提醒")
            for alert in alerts:
                log_lines.append(f"[{alert['created_at']}] {alert['severity']} {alert['reason']}：{alert['action_hint']}")
            log_lines.append("")
        log_lines.append("最近日志")
        if logs:
            for item in logs:
                log_lines.append(f"[{item['created_at']}] {item['level']} {item['step']}：{item['message']}")
        else:
            log_lines.append("-")
        task_recent_logs_text.setPlainText("\n".join(log_lines))

    def refresh_all() -> None:
        refresh_accounts()
        refresh_jobs()
        refresh_tasks()
        refresh_candidates()
        refresh_alerts()
        refresh_logs()
        refresh_mode_status()

    def current_job_row() -> Any | None:
        if state.current_task_id:
            task_row = db.fetch_one(
                "SELECT job_id, status FROM tasks WHERE id = ?",
                (int(state.current_task_id),),
            )
            if task_row and task_row["status"] == TaskStatus.RUNNING.value:
                job_id = int(task_row["job_id"])
                row = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
                if row:
                    state.current_job_id = job_id
                    return row
        job_id = job_combo.currentData() or state.current_job_id
        if job_id:
            return db.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = db.fetch_one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
        if row:
            state.current_job_id = int(row["id"])
        return row

    def route_job_row() -> Any | None:
        row = current_job_row()
        if not row:
            row = db.fetch_one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")
        if row:
            state.current_job_id = int(row["id"])
            for combo in (job_combo, task_job_combo):
                index = combo.findData(int(row["id"]))
                if index >= 0:
                    combo.setCurrentIndex(index)
        return row

    def job_config(row: Any) -> JobConfig:
        return JobConfig(
            title=row["title"],
            jd=row["jd"] or "",
            must_have=loads(row["must_have"], []),
            nice_to_have=loads(row["nice_to_have"], []),
            reject_keywords=loads(row["reject_keywords"], []),
            min_score=int(row["min_score"]),
        )

    def greeting_config(row: Any) -> GreetingConfig:
        return GreetingConfig(custom_message=row["greeting_template"] or "")

    def fill_login_from_account(account_id: int, submit: bool) -> None:
        row = db.fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row:
            message("账号不存在。")
            return
        if not row["username"] or not row["password"]:
            append_log(["账号缺少登录信息", f"账号：{row['name']}", "请先在账号页维护登录名和密码并保存。"])
            return

        def handle(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            append_log(
                [
                    "自动登录填充完成" if submit else "已填账号密码，等待人工登录",
                    f"账号：{row['name']}",
                    f"切到密码登录：{'是' if payload.get('switchedPasswordLogin') else '否'}",
                    f"密码模式已就绪：{'是' if payload.get('passwordModeReady') else '否'}",
                    f"账号已填：{'是' if payload.get('filledUsername') else '否'}",
                    f"密码已填：{'是' if payload.get('filledPassword') else '否'}",
                    f"找到登录按钮：{'是' if payload.get('foundLoginButton') else '否'}",
                    f"已点击登录：{'是' if payload.get('clickedLogin') else '否'}",
                    f"页面文档数：{payload.get('documentCount') or '-'}，输入框数：{payload.get('inputCount') or '-'}",
                    f"说明：{payload.get('reason') or '-'}",
                    "如果出现短信/验证码/滑块，请人工完成，然后点“检查登录状态”。",
                ]
            )
            QTimer.singleShot(1200, lambda: check_login_status(False))

        def run_fill() -> None:
            page_adapter.fill_login(row["username"], row["password"], submit, handle)

        def handle_switch(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            append_log(
                [
                    "密码登录切换检查完成",
                    f"找到密码登录：{'是' if payload.get('foundPasswordTab') else '否'}",
                    f"已点击密码登录：{'是' if payload.get('clickedPasswordTab') else '否'}",
                    f"点击前激活：{payload.get('beforeActiveText') or '-'}",
                    f"页面文档数：{payload.get('documentCount') or '-'}，输入框：{payload.get('inputPlaceholders') or '-'}",
                ]
            )
            QTimer.singleShot(500, run_fill)

        page_adapter.switch_password_login(handle_switch)

    def ensure_account_profile(account_id: int | None) -> tuple[Any, Any, bool]:
        if not account_id:
            raise ValueError("请先选择账号。")
        row = db.fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise ValueError("账号不存在。")
        profile_dir = Path(row["profile_dir"]).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile = state.profiles.get(account_id)
        profile_reused = profile is not None
        if profile is None:
            profile = QWebEngineProfile(f"account-{account_id}", window)
            profile.setPersistentStoragePath(str(profile_dir / "storage"))
            profile.setCachePath(str(profile_dir / "cache"))
            if hasattr(QWebEngineProfile, "PersistentCookiesPolicy"):
                profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            else:
                profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
            if hasattr(QWebEngineProfile, "HttpCacheType"):
                profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
            elif hasattr(QWebEngineProfile, "DiskHttpCache"):
                profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
            profile.setHttpUserAgent(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
            state.profiles[account_id] = profile
        return profile, row, profile_reused

    def use_account(account_id: int | None) -> None:
        try:
            profile, row, profile_reused = ensure_account_profile(account_id)
        except ValueError as exc:
            message(str(exc))
            return

        def attach_page_signals(page: Any) -> None:
            page.urlChanged.connect(lambda url: append_log([f"页面地址变化：{url.toString()}"]))

            def on_load_started() -> None:
                state.last_load_progress = 0
                append_log(["页面开始加载"])
                if state.load_timeout_timer:
                    state.load_timeout_timer.stop()
                timer = QTimer(window)
                timer.setSingleShot(True)

                def on_timeout() -> None:
                    append_log(
                        [
                            "页面加载超时",
                            f"最后进度：{state.last_load_progress}%",
                            f"当前 URL：{web.url().toString()}",
                            "可点击“打开找人页”重试；若仍为空白，说明嵌入式浏览器被页面脚本卡住，需要切换执行引擎。",
                        ]
                    )

                timer.timeout.connect(on_timeout)
                timer.start(20000)
                state.load_timeout_timer = timer

            def on_load_progress(progress: int) -> None:
                state.last_load_progress = progress
                if progress in {25, 50, 75, 100}:
                    append_log([f"页面加载进度：{progress}%"])

            def on_load_finished(ok: bool) -> None:
                if state.load_timeout_timer:
                    state.load_timeout_timer.stop()
                append_log([f"页面加载完成：{'成功' if ok else '失败'}", f"当前 URL：{web.url().toString()}"])
                if ok:
                    pending_login = state.pending_auto_login_account_id
                    pending_task_apply = state.pending_task_apply_id
                    current_url = web.url().toString()
                    if pending_login and re.search(r"/account/login|passport|login", current_url, re.I):
                        state.pending_auto_login_account_id = None
                        QTimer.singleShot(600, lambda account_id=pending_login: fill_login_from_account(account_id, True))
                        return
                    if pending_login and re.search(r"/search/getConditionItem", current_url, re.I):
                        state.pending_auto_login_account_id = None
                    if (
                        pending_task_apply
                        and state.current_task_id == pending_task_apply
                        and re.search(r"/search/getConditionItem", current_url, re.I)
                    ):
                        if _task_automation_blocked(state, pending_task_apply):
                            append_log(["任务自动写条件已跳过", f"任务状态：{_task_block_label(state, pending_task_apply)}"])
                            return
                        state.pending_task_apply_id = None
                        append_log(["任务自动执行：开始写入查询条件，写完后停住验收", f"任务 ID：{pending_task_apply}"])
                        state.engine.set_step(pending_task_apply, TaskStep.APPLY_FILTERS, "任务模式：自动写入查询条件")
                        save_task_checkpoint(pending_task_apply, "apply_filters", TaskStep.APPLY_FILTERS, {"source": "load_finished"})
                        QTimer.singleShot(
                            1200,
                            lambda task_id=pending_task_apply: route_fill_search_and_submit(
                                False,
                                auto_submit=True,
                                trigger_task_id=task_id,
                                task_mode=True,
                            ),
                        )
                        QTimer.singleShot(600, check_login_status)
                        return
                    QTimer.singleShot(500, check_login_status)

            page.loadStarted.connect(on_load_started)
            page.loadProgress.connect(on_load_progress)
            page.loadFinished.connect(on_load_finished)
            if hasattr(page, "renderProcessTerminated"):
                page.renderProcessTerminated.connect(
                    lambda status, exit_code: append_log(
                        [
                            "页面渲染进程已终止",
                            f"状态：{getattr(status, 'name', str(status))}",
                            f"退出码：{exit_code}",
                            f"当前 URL：{web.url().toString()}",
                            "建议先刷新页面再重试“填条件”。",
                        ]
                    )
                )

        class RouteWebEnginePage(QWebEnginePage):
            def javaScriptConsoleMessage(
                self,
                level: Any,
                message: str,
                line_number: int,
                source_id: str,
            ) -> None:
                msg = str(message or "")
                if re.search(r"uncaught|error|exception|TypeError|ReferenceError|SyntaxError", msg, re.I):
                    append_log(
                        [
                            f"JS异常[{getattr(level, 'name', str(level))}]",
                            f"来源：{source_id or '-'}:{line_number}",
                            f"内容：{msg[:240]}",
                        ]
                    )
                super().javaScriptConsoleMessage(level, message, line_number, source_id)

            def createWindow(self, _type: Any) -> Any:
                append_log(["检测到候选人新页面请求", "已在右侧工作区打开候选人详情页。"])
                state.popup_open_count += 1
                detail_view = web.new_tab("候选人详情", switch=True)
                new_page = RouteWebEnginePage(profile, window)
                attach_page_signals(new_page)
                detail_view.setPage(new_page)
                state.page = new_page
                return new_page

        web.reset_tabs()
        page = RouteWebEnginePage(profile, window)
        attach_page_signals(page)
        web.setPage(page)
        state.profile = profile
        state.page = page
        state.current_account_id = account_id
        db.execute("UPDATE accounts SET last_used_at = datetime('now'), updated_at = datetime('now') WHERE id = ?", (account_id,))
        append_log(
            [
                f"已切换账号：{row['name']}",
                f"Profile：{row['profile_dir']}",
                f"Profile模式：{'复用' if profile_reused else '新建'}，Cookie：强制持久化",
            ]
        )
        if row["status"] == "needs_verification":
            append_log(["账号状态：需要人工验证", "请在右侧猎聘登录页完成滑块/验证码后继续。"])

    def open_url_with_account_profile(
        url: str,
        label: str,
        account_id: int | None,
        *,
        log_reason: str,
    ) -> Any | None:
        if not account_id:
            message("这条记录缺少归属账号，不能打开猎聘简历。")
            append_log([f"{log_reason}失败", "原因：候选人没有 source_account_id / 快照 account_id，已停止。"])
            return None
        try:
            profile, account_row, profile_reused = ensure_account_profile(account_id)
        except ValueError as exc:
            message(str(exc))
            append_log([f"{log_reason}失败", f"原因：{exc}"])
            return None
        detail_view = web.new_tab(label, switch=True)
        page = QWebEnginePage(profile, window)
        page.urlChanged.connect(lambda changed_url: append_log([f"页面地址变化：{changed_url.toString()}"]))
        page.loadStarted.connect(lambda: append_log(["页面开始加载"]))
        page.loadFinished.connect(
            lambda ok: append_log(
                [
                    f"页面加载完成：{'成功' if ok else '失败'}",
                    f"当前 URL：{detail_view.url().toString()}",
                ]
            )
        )
        detail_view.setPage(page)
        page.setUrl(QUrl(url))
        append_log(
            [
                log_reason,
                f"使用账号：{account_row['name']}（ID {account_id}）",
                f"Profile：{account_row['profile_dir']}（{'复用' if profile_reused else '新建'}）",
                f"URL：{url}",
            ]
        )
        return detail_view

    def open_search_page() -> None:
        if not state.current_account_id:
            selected = account_combo.currentData()
            use_account(selected)
        web.open_home_url(QUrl(SEARCH_PAGE_URL))

    # Accounts tab
    account_tab = QWidget()
    account_layout = QVBoxLayout(account_tab)
    account_form = QFormLayout()
    account_name = QLineEdit()
    account_username = QLineEdit()
    account_password = QLineEdit()
    account_password.setEchoMode(QLineEdit.Password)
    account_form.addRow("账号备注", account_name)
    account_form.addRow("登录名", account_username)
    account_form.addRow("密码", account_password)
    account_buttons = QHBoxLayout()
    add_account_btn = QPushButton("新增账号")
    save_account_btn = QPushButton("编辑账号")
    use_account_btn = QPushButton("使用选中账号")
    login_account_btn = QPushButton("打开登录/找人页")
    account_buttons.addWidget(add_account_btn)
    account_buttons.addWidget(save_account_btn)
    account_buttons.addWidget(use_account_btn)
    account_buttons.addWidget(login_account_btn)
    account_layout.addLayout(account_buttons)
    account_layout.addWidget(accounts_table)
    tabs.addTab(account_tab, "账号")

    def open_account_dialog(initial: dict[str, str] | None = None) -> dict[str, str] | None:
        dialog = QDialog(window)
        dialog.setWindowTitle("账号编辑")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name_edit = QLineEdit()
        user_edit = QLineEdit()
        password_edit = QLineEdit()
        password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("账号备注", name_edit)
        form.addRow("登录名", user_edit)
        form.addRow("密码", password_edit)
        if initial:
            name_edit.setText(initial.get("name", ""))
            user_edit.setText(initial.get("username", ""))
            password_edit.setText(initial.get("password", ""))
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return None
        name = name_edit.text().strip()
        username = user_edit.text().strip()
        password = password_edit.text()
        if not name:
            message("请填写账号备注。")
            return None
        if not username:
            message("请填写登录名。")
            return None
        return {"name": name, "username": username, "password": password}

    def add_account() -> None:
        values = open_account_dialog({"name": account_name.text().strip(), "username": account_username.text().strip(), "password": account_password.text()})
        if not values:
            return
        account_id = db.add_account(values["name"], values["username"])
        db.update_account(account_id, values["name"], values["username"], values["password"])
        db.log(f"新增账号：{values['name']}", account_id=account_id)
        account_name.clear()
        account_username.clear()
        account_password.clear()
        refresh_all()

    def load_account_form(account_id: int | None) -> None:
        if not account_id:
            return
        row = db.fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row:
            return
        account_name.setText(row["name"] or "")
        account_username.setText(row["username"] or "")
        account_password.setText(row["password"] or "")
        account_combo.setCurrentIndex(max(0, account_combo.findData(account_id)))

    def save_account() -> None:
        account_id = selected_id(accounts_table) or account_combo.currentData()
        if not account_id:
            message("请先选择要保存的账号。")
            return
        row = db.fetch_one("SELECT * FROM accounts WHERE id = ?", (int(account_id),))
        if not row:
            message("账号不存在。")
            return
        values = open_account_dialog({"name": row["name"] or "", "username": row["username"] or "", "password": row["password"] or ""})
        if not values:
            return
        db.update_account(int(account_id), values["name"], values["username"], values["password"])
        db.log(f"保存账号：{values['name']}", account_id=int(account_id))
        refresh_all()
        append_log(["账号已保存", f"账号：{values['name']}", f"登录名：{values['username']}", "密码：已保存到本机应用数据库（表格不展示）"])

    def auto_login_account(account_id: int | None) -> None:
        if not account_id:
            message("请先选择账号。")
            return
        load_account_form(int(account_id))
        row = db.fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row:
            message("账号不存在。")
            return
        if not row["username"] or not row["password"]:
            append_log(["账号缺少登录信息", f"账号：{row['name']}", "请在账号页维护登录名和密码后保存。"])
            message("这个账号还没有维护完整的登录名和密码。")
            return
        state.pending_auto_login_account_id = int(account_id)
        use_account(int(account_id))
        open_search_page()
        append_log(["已选择账号并准备自动登录", f"账号：{row['name']}", "右侧会打开猎聘；如果跳到登录页，将自动切到密码登录并提交。"])

    add_account_btn.clicked.connect(add_account)
    save_account_btn.clicked.connect(save_account)
    use_account_btn.clicked.connect(lambda: use_account(selected_id(accounts_table)))
    login_account_btn.clicked.connect(lambda: auto_login_account(selected_id(accounts_table) or account_combo.currentData()))
    accounts_table.cellClicked.connect(lambda row, _col: auto_login_account(int(accounts_table.item(row, 0).text())))
    accounts_table.cellDoubleClicked.connect(lambda _row, _col: save_account())

    # Jobs tab
    job_tab = QWidget()
    job_layout = QVBoxLayout(job_tab)
    job_form_box = QGroupBox("岗位基础信息")
    job_form_layout = QGridLayout(job_form_box)
    job_title = QLineEdit()
    job_min_score = QSpinBox()
    job_min_score.setRange(0, 100)
    job_min_score.setValue(75)
    job_auto_greet = QCheckBox("自动打招呼")
    job_dry_run = QCheckBox("Dry-run")
    job_dry_run.setChecked(True)
    job_jd = QTextEdit()
    job_greeting = QTextEdit()
    job_followup = QTextEdit()
    job_form_layout.addWidget(QLabel("岗位"), 0, 0)
    job_form_layout.addWidget(job_title, 0, 1)
    job_form_layout.addWidget(QLabel("阈值"), 1, 0)
    job_form_layout.addWidget(job_min_score, 1, 1)
    job_form_layout.addWidget(job_auto_greet, 2, 0)
    job_form_layout.addWidget(job_dry_run, 2, 1)
    job_form_layout.addWidget(QLabel("JD"), 3, 0)
    job_form_layout.addWidget(job_jd, 3, 1)
    job_form_layout.addWidget(QLabel("默认话术"), 4, 0)
    job_form_layout.addWidget(job_greeting, 4, 1)
    job_form_layout.addWidget(QLabel("二次补充话术"), 5, 0)
    job_form_layout.addWidget(job_followup, 5, 1)

    def combo(options: list[str]) -> Any:
        widget = QComboBox()
        for option in options:
            widget.addItem(option)
        return widget

    def age_spin() -> Any:
        widget = QSpinBox()
        widget.setRange(0, 80)
        widget.setSpecialValueText("不限")
        return widget

    def age_range_widget(min_widget: Any, max_widget: Any) -> Any:
        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(6)
        wrapper_layout.addWidget(min_widget)
        wrapper_layout.addWidget(QLabel("至"))
        wrapper_layout.addWidget(max_widget)
        return wrapper

    job_query_box = QGroupBox("猎聘查询条件")
    job_query_layout = QGridLayout(job_query_box)
    job_keyword_match = combo(KEYWORD_MATCH_OPTIONS)
    job_keywords = QLineEdit()
    job_position_keywords = QLineEdit()
    job_company_keywords = QLineEdit()
    job_current_city = MultiSelectField("目前城市", CITY_OPTIONS, allow_custom=True)
    job_expected_city = MultiSelectField("期望城市", CITY_OPTIONS, allow_custom=True)
    job_work_years = combo([item for item in WORK_YEAR_OPTIONS if item != "自定义"])
    job_education = MultiSelectField("教育经历", [item for item in EDUCATION_OPTIONS if item != "不限"])
    job_recruit_type = combo(RECRUIT_TYPE_OPTIONS)
    job_school_tags = MultiSelectField("院校要求", [item for item in SCHOOL_TAG_OPTIONS if item != "不限"])
    job_current_industry = MultiSelectField("当前行业", INDUSTRY_OPTIONS, allow_custom=True)
    job_current_position = MultiSelectField("当前职位", POSITION_OPTIONS, allow_custom=True)
    job_expected_industry = MultiSelectField("期望行业", INDUSTRY_OPTIONS, allow_custom=True)
    job_expected_position = MultiSelectField("期望职位", POSITION_OPTIONS, allow_custom=True)
    job_active_days = combo(ACTIVE_OPTIONS)
    job_gender = combo(GENDER_OPTIONS)
    job_hopping = combo(JOB_HOPPING_OPTIONS)
    job_languages = MultiSelectField("语言能力", [item for item in LANGUAGE_OPTIONS if item != "不限"], allow_custom=True)
    job_schools = QLineEdit()
    job_majors = QLineEdit()
    job_status = MultiSelectField("求职状态", JOB_STATUS_OPTIONS)
    job_resume_language = combo(RESUME_LANGUAGE_OPTIONS)
    job_city = job_expected_city
    job_experience = job_work_years
    query_rows = [
        ("关键词匹配", job_keyword_match),
        ("搜索关键词", job_keywords),
        ("职位名称", job_position_keywords),
        ("公司名称", job_company_keywords),
        ("目前城市", job_current_city),
        ("期望城市", job_expected_city),
        ("工作年限", job_work_years),
        ("教育经历", job_education),
        ("统招要求", job_recruit_type),
        ("院校要求", job_school_tags),
        ("当前行业", job_current_industry),
        ("当前职位", job_current_position),
        ("期望行业", job_expected_industry),
        ("期望职位", job_expected_position),
        ("活跃度", job_active_days),
        ("性别", job_gender),
        ("跳槽频率", job_hopping),
        ("语言能力", job_languages),
        ("学校", job_schools),
        ("专业", job_majors),
        ("求职状态", job_status),
        ("简历语言", job_resume_language),
    ]
    for index, (label, widget) in enumerate(query_rows):
        row = index // 2
        col = (index % 2) * 2
        job_query_layout.addWidget(QLabel(label), row, col)
        job_query_layout.addWidget(widget, row, col + 1)

    job_score_box = QGroupBox("评分条件")
    job_score_layout = QGridLayout(job_score_box)
    job_must = QLineEdit()
    job_nice = QLineEdit()
    job_reject = QLineEdit()
    job_score_layout.addWidget(QLabel("必备项"), 0, 0)
    job_score_layout.addWidget(job_must, 0, 1)
    job_score_layout.addWidget(QLabel("加分项"), 1, 0)
    job_score_layout.addWidget(job_nice, 1, 1)
    job_score_layout.addWidget(QLabel("排除项"), 2, 0)
    job_score_layout.addWidget(job_reject, 2, 1)

    save_job_btn = QPushButton("编辑")
    add_job_btn = QPushButton("新增")
    delete_job_btn = QPushButton("删除")
    parse_job_btn = QPushButton("解析JD")
    parsed_conditions_box = QGroupBox("解析条件确认")
    parsed_conditions_layout = QHBoxLayout(parsed_conditions_box)
    parsed_conditions_hint = QLabel("解析后会自动弹出确认窗口；你也可以手动再次打开。")
    apply_parsed_conditions_btn = QPushButton("打开解析条件确认弹窗")
    apply_parsed_conditions_btn.setEnabled(False)
    parsed_conditions_layout.addWidget(parsed_conditions_hint)
    parsed_conditions_layout.addWidget(apply_parsed_conditions_btn)
    job_buttons = QHBoxLayout()
    job_buttons.addWidget(add_job_btn)
    job_buttons.addWidget(save_job_btn)
    job_buttons.addWidget(delete_job_btn)
    job_layout.addLayout(job_buttons)
    job_layout.addWidget(jobs_table)
    tabs.addTab(job_tab, "岗位")

    parsed_condition_specs = [
        ("keyword_match", "关键词匹配", True),
        ("keywords", "搜索关键词", True),
        ("position_keywords", "职位名称", True),
        ("company_keywords", "公司名称", False),
        ("current_city", "目前城市", False),
        ("expected_city", "期望城市", True),
        ("work_years", "工作年限", True),
        ("education", "教育经历", True),
        ("recruit_type", "统招要求", False),
        ("school_tags", "院校要求", False),
        ("current_industry", "当前行业", True),
        ("current_position", "当前职位", True),
        ("expected_industry", "期望行业", False),
        ("expected_position", "期望职位", False),
        ("active_days", "活跃度", True),
        ("gender", "性别", False),
        ("job_hopping", "跳槽频率", False),
        ("languages", "语言能力", False),
        ("schools", "学校", False),
        ("majors", "专业", False),
        ("job_status", "求职状态", False),
        ("resume_language", "简历语言", False),
        ("must_have", "评分必备项", True),
        ("nice_to_have", "评分加分项", True),
        ("reject_keywords", "评分排除项", False),
    ]

    def set_combo_text(widget: Any, value: str) -> None:
        index = widget.findText(value)
        if index >= 0:
            widget.setCurrentIndex(index)

    def optional_age_value(widget: Any) -> int | None:
        value = int(widget.value() or 0)
        return value or None

    def set_age_value(widget: Any, value: Any) -> None:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        widget.setValue(parsed if 16 <= parsed <= 80 else 0)

    def form_search_conditions() -> dict[str, Any]:
        return {
            "keyword_match": job_keyword_match.currentText(),
            "keywords": _csv(job_keywords.text()),
            "position_keywords": _csv(job_position_keywords.text()),
            "company_keywords": _csv(job_company_keywords.text()),
            "current_city": job_current_city.values(),
            "expected_city": job_expected_city.values(),
            "work_years": _single_work_year_list(job_work_years.currentText()),
            "education": job_education.values(),
            "recruit_type": job_recruit_type.currentText(),
            "school_tags": job_school_tags.values(),
            "current_industry": job_current_industry.values(),
            "current_position": job_current_position.values(),
            "expected_industry": job_expected_industry.values(),
            "expected_position": job_expected_position.values(),
            "active_days": job_active_days.currentText(),
            "gender": job_gender.currentText(),
            "job_hopping": job_hopping.currentText(),
            "languages": job_languages.values(),
            "schools": _csv(job_schools.text()),
            "majors": _csv(job_majors.text()),
            "job_status": job_status.values(),
            "resume_language": job_resume_language.currentText(),
        }

    def apply_search_conditions_to_form(conditions: dict[str, Any]) -> None:
        set_combo_text(job_keyword_match, str(conditions.get("keyword_match") or KEYWORD_MATCH_OPTIONS[0]))
        job_keywords.setText(_join_values(conditions.get("keywords")))
        job_position_keywords.setText(_join_values(conditions.get("position_keywords")))
        job_company_keywords.setText(_join_values(conditions.get("company_keywords")))
        job_current_city.setText(_join_values(conditions.get("current_city")))
        job_expected_city.setText(_join_values(conditions.get("expected_city")))
        set_combo_text(job_work_years, _single_work_year_value(conditions.get("work_years")))
        job_education.setText(_join_values(conditions.get("education")))
        set_combo_text(job_recruit_type, str(conditions.get("recruit_type") or RECRUIT_TYPE_OPTIONS[0]))
        job_school_tags.setText(_join_values(conditions.get("school_tags")))
        job_current_industry.setText(_join_values(conditions.get("current_industry")))
        job_current_position.setText(_join_values(conditions.get("current_position")))
        job_expected_industry.setText(_join_values(conditions.get("expected_industry")))
        job_expected_position.setText(_join_values(conditions.get("expected_position")))
        set_combo_text(job_active_days, str(conditions.get("active_days") or "30天内活跃"))
        set_combo_text(job_gender, str(conditions.get("gender") or "不限"))
        set_combo_text(job_hopping, str(conditions.get("job_hopping") or JOB_HOPPING_OPTIONS[0]))
        job_languages.setText(_join_values(conditions.get("languages")))
        job_schools.setText(_join_values(conditions.get("schools")))
        job_majors.setText(_join_values(conditions.get("majors")))
        job_status.setText(_join_values(conditions.get("job_status")))
        set_combo_text(job_resume_language, str(conditions.get("resume_language") or RESUME_LANGUAGE_OPTIONS[0]))

    def draft_to_form_fields(parse_result: Any) -> dict[str, Any]:
        draft = parse_result.draft
        fields = draft_to_job_fields(draft)
        data = draft.model_dump(mode="json")
        data["reject_keywords"] = data.get("exclude_keywords", [])
        data["must_have"] = fields["must_have"]
        data["nice_to_have"] = fields["nice_to_have"]
        return data

    def field_display_value(value: Any) -> str:
        if isinstance(value, list):
            return "，".join(str(item) for item in value if str(item).strip())
        return str(value or "").strip()

    def reset_parsed_condition_state() -> None:
        state.last_parsed_job_fields = {}
        state.last_parsed_job_id = None
        parsed_conditions_hint.setText("解析后会自动弹出确认窗口；你也可以手动再次打开。")
        apply_parsed_conditions_btn.setEnabled(False)

    def apply_selected_parsed_conditions(selected_keys: list[str]) -> list[str]:
        fields = state.last_parsed_job_fields
        applied: list[str] = []
        conditions = form_search_conditions()
        for key, label, _default_checked in parsed_condition_specs:
            if key not in selected_keys:
                continue
            if key == "must_have":
                job_must.setText(field_display_value(fields.get(key)))
            elif key == "nice_to_have":
                job_nice.setText(field_display_value(fields.get(key)))
            elif key == "reject_keywords":
                job_reject.setText(field_display_value(fields.get(key)))
            elif key in conditions:
                conditions[key] = fields.get(key)
            applied.append(label)
        apply_search_conditions_to_form(conditions)
        return applied

    def open_parsed_condition_dialog() -> None:
        fields = state.last_parsed_job_fields
        if not fields:
            message("还没有可应用的 JD 解析结果。")
            return
        current_form_job_id = job_combo.currentData() or state.current_job_id
        if state.last_parsed_job_id and current_form_job_id and int(state.last_parsed_job_id) != int(current_form_job_id):
            message("解析结果所属岗位和当前岗位不一致，请重新选择岗位后再解析。")
            append_log(
                [
                    "已阻止跨岗位应用解析结果",
                    f"解析岗位 ID：{state.last_parsed_job_id}",
                    f"当前岗位 ID：{current_form_job_id}",
                ]
            )
            return
        dialog = QDialog(window)
        dialog.setWindowTitle("解析条件确认")
        dialog.setModal(True)
        dialog.resize(680, 760)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("请勾选本次要写入岗位配置的条件；未勾选的字段会保持当前表单内容。"))
        checks: dict[str, Any] = {}
        for key, label, default_checked in parsed_condition_specs:
            value = field_display_value(fields.get(key))
            checkbox = QCheckBox(f"{label}：{value or '未解析到'}")
            checkbox.setEnabled(bool(value))
            checkbox.setChecked(bool(value) and default_checked)
            if key == "reject_keywords" and value:
                checkbox.setToolTip("排除项默认不勾选，避免因为模型理解偏差导致搜索范围过窄。")
            layout.addWidget(checkbox)
            checks[key] = checkbox
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button:
            ok_button.setText("应用勾选条件")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            append_log(["已取消应用解析条件", "你可以点击“打开解析条件确认弹窗”再次选择。"])
            return
        selected = [key for key, checkbox in checks.items() if checkbox.isChecked()]
        if not selected:
            message("你没有勾选任何要应用的条件。")
            return
        applied = apply_selected_parsed_conditions(selected)
        append_log(["已应用勾选的解析条件", f"字段：{'、'.join(applied)}"])
        parsed_conditions_hint.setText("解析条件已应用。需要调整时可再次打开确认弹窗。")

    def apply_job_parse_result(title: str, parse_result: Any) -> dict[str, Any]:
        fields = draft_to_form_fields(parse_result)
        state.last_parsed_job_fields = fields
        state.last_parsed_job_id = job_combo.currentData() or state.current_job_id
        apply_parsed_conditions_btn.setEnabled(True)
        parsed_conditions_hint.setText("解析完成，已自动弹出确认窗口；也可以手动再次打开。")
        preview_lines = draft_preview_lines(parse_result.draft, parse_result.source, parse_result.error)
        result.setPlainText("\n".join(["岗位 JD 解析预览", f"岗位：{title}", ""] + preview_lines))
        append_log(
            [
                "岗位 JD 解析完成，请勾选要应用的条件",
                f"来源：{'千问' if parse_result.source == 'qwen' else '本地规则回退'}",
                f"搜索关键词：{field_display_value(fields.get('keywords')) or '-'}",
                f"职位：{field_display_value(fields.get('position_keywords')) or '-'}；年限：{field_display_value(fields.get('work_years')) or '-'}；学历：{field_display_value(fields.get('education')) or '-'}",
            ]
        )
        QTimer.singleShot(100, open_parsed_condition_dialog)
        return fields

    def row_search_conditions(row: Any) -> dict[str, Any]:
        conditions = loads(row["search_conditions"], {}) if "search_conditions" in row.keys() else {}
        if not isinstance(conditions, dict):
            conditions = {}
        if not conditions:
            conditions = {
                "keyword_match": "包含任意关键词",
                "keywords": loads(row["keywords"], []),
                "expected_city": _csv(row["city"] or ""),
                "work_years": _csv(row["experience"] or ""),
                "education": _csv(row["education"] or ""),
                "recruit_type": RECRUIT_TYPE_OPTIONS[0],
                "active_days": "30天内活跃",
                "gender": "不限",
                "job_hopping": JOB_HOPPING_OPTIONS[0],
                "resume_language": RESUME_LANGUAGE_OPTIONS[0],
            }
        return conditions

    def parse_job_form_jd() -> None:
        title = job_title.text().strip()
        jd = job_jd.toPlainText().strip()
        parse_job_id = job_combo.currentData() or state.current_job_id
        if not title or not jd:
            message("请先在岗位页填写岗位名称和 JD。")
            return
        append_log(["开始解析岗位 JD", f"岗位：{title}", f"模型：{state.env.qwen_model if state.env.qwen_api_key else '本地规则回退'}"])
        parse_job_btn.setEnabled(False)
        parse_job_btn.setText("解析中...")
        result.setPlainText("正在后台解析 JD，请稍候。窗口不会卡住，可以继续查看页面。")
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        def worker() -> None:
            try:
                result_queue.put(("ok", parse_jd_with_qwen(title, jd, state.env)))
            except Exception as exc:
                result_queue.put(("error", exc))

        def poll_result() -> None:
            try:
                status, payload = result_queue.get_nowait()
            except queue.Empty:
                QTimer.singleShot(150, poll_result)
                return
            parse_job_btn.setEnabled(True)
            parse_job_btn.setText("解析JD")
            if status == "ok":
                current_form_job_id = job_combo.currentData() or state.current_job_id
                if parse_job_id and current_form_job_id and int(parse_job_id) != int(current_form_job_id):
                    result.setPlainText("JD 解析已完成，但当前岗位已切换，结果未应用。请重新点击解析。")
                    append_log(
                        [
                            "已丢弃过期 JD 解析结果",
                            f"解析岗位 ID：{parse_job_id}",
                            f"当前岗位 ID：{current_form_job_id}",
                        ]
                    )
                    reset_parsed_condition_state()
                    return
                apply_job_parse_result(title, payload)
            else:
                result.setPlainText(f"JD 解析失败：{payload}")
                append_log(["JD 解析失败", str(payload)])

        threading.Thread(target=worker, daemon=True).start()
        QTimer.singleShot(150, poll_result)

    def load_job_form(job_id: int | None) -> None:
        if not job_id:
            return
        row = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not row:
            return
        state.current_job_id = int(row["id"])
        job_title.setText(row["title"] or "")
        apply_search_conditions_to_form(row_search_conditions(row))
        job_min_score.setValue(int(row["min_score"] or 75))
        job_must.setText("，".join(loads(row["must_have"], [])))
        job_nice.setText("，".join(loads(row["nice_to_have"], [])))
        job_reject.setText("，".join(loads(row["reject_keywords"], [])))
        job_auto_greet.setChecked(bool(row["auto_greet"]))
        job_dry_run.setChecked(bool(row["dry_run"]))
        job_jd.setPlainText(row["jd"] or "")
        job_greeting.setPlainText(row["greeting_template"] or "")
        job_followup.setPlainText(row["followup_template"] or "")
        job_combo.setCurrentIndex(max(0, job_combo.findData(job_id)))
        reset_parsed_condition_state()
        append_log(["已加载岗位配置", f"岗位：{row['title']}"])

    def open_job_dialog(existing_row: Any | None = None) -> dict[str, Any] | None:
        dialog = QDialog(window)
        dialog.setWindowTitle("岗位编辑")
        dialog.setModal(True)
        dialog.resize(980, 900)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setSpacing(12)
        scroll.setWidget(body)
        layout.addWidget(scroll)

        basic_box = QGroupBox("岗位基础信息")
        basic_layout = QGridLayout(basic_box)
        title_edit = QLineEdit()
        min_score_edit = QSpinBox()
        min_score_edit.setRange(0, 100)
        auto_greet_edit = QCheckBox("自动打招呼")
        dry_run_edit = QCheckBox("Dry-run")
        jd_edit = QTextEdit()
        greeting_edit = QTextEdit()
        followup_edit = QTextEdit()
        basic_layout.addWidget(QLabel("岗位"), 0, 0)
        basic_layout.addWidget(title_edit, 0, 1)
        basic_layout.addWidget(QLabel("阈值"), 1, 0)
        basic_layout.addWidget(min_score_edit, 1, 1)
        basic_layout.addWidget(auto_greet_edit, 2, 0)
        basic_layout.addWidget(dry_run_edit, 2, 1)
        basic_layout.addWidget(QLabel("JD"), 3, 0)
        basic_layout.addWidget(jd_edit, 3, 1)
        basic_layout.addWidget(QLabel("默认话术"), 4, 0)
        basic_layout.addWidget(greeting_edit, 4, 1)
        basic_layout.addWidget(QLabel("二次补充话术"), 5, 0)
        basic_layout.addWidget(followup_edit, 5, 1)

        query_box = QGroupBox("猎聘查询条件")
        query_layout = QGridLayout(query_box)
        query_fields = {
            "keyword_match": combo(KEYWORD_MATCH_OPTIONS),
            "keywords": QLineEdit(),
            "position_keywords": QLineEdit(),
            "company_keywords": QLineEdit(),
            "current_city": MultiSelectField("目前城市", CITY_OPTIONS, allow_custom=True),
            "expected_city": MultiSelectField("期望城市", CITY_OPTIONS, allow_custom=True),
            "work_years": combo([item for item in WORK_YEAR_OPTIONS if item != "自定义"]),
            "education": MultiSelectField("教育经历", [item for item in EDUCATION_OPTIONS if item != "不限"]),
            "recruit_type": combo(RECRUIT_TYPE_OPTIONS),
            "school_tags": MultiSelectField("院校要求", [item for item in SCHOOL_TAG_OPTIONS if item != "不限"]),
            "current_industry": MultiSelectField("当前行业", INDUSTRY_OPTIONS, allow_custom=True),
            "current_position": MultiSelectField("当前职位", POSITION_OPTIONS, allow_custom=True),
            "expected_industry": MultiSelectField("期望行业", INDUSTRY_OPTIONS, allow_custom=True),
            "expected_position": MultiSelectField("期望职位", POSITION_OPTIONS, allow_custom=True),
            "active_days": combo(ACTIVE_OPTIONS),
            "gender": combo(GENDER_OPTIONS),
            "job_hopping": combo(JOB_HOPPING_OPTIONS),
            "languages": MultiSelectField("语言能力", [item for item in LANGUAGE_OPTIONS if item != "不限"], allow_custom=True),
            "schools": QLineEdit(),
            "majors": QLineEdit(),
            "job_status": MultiSelectField("求职状态", JOB_STATUS_OPTIONS),
            "resume_language": combo(RESUME_LANGUAGE_OPTIONS),
        }
        query_rows = [
            ("关键词匹配", "keyword_match"),
            ("搜索关键词", "keywords"),
            ("职位名称", "position_keywords"),
            ("公司名称", "company_keywords"),
            ("目前城市", "current_city"),
            ("期望城市", "expected_city"),
            ("工作年限", "work_years"),
            ("教育经历", "education"),
            ("统招要求", "recruit_type"),
            ("院校要求", "school_tags"),
            ("当前行业", "current_industry"),
            ("当前职位", "current_position"),
            ("期望行业", "expected_industry"),
            ("期望职位", "expected_position"),
            ("活跃度", "active_days"),
            ("性别", "gender"),
            ("跳槽频率", "job_hopping"),
            ("语言能力", "languages"),
            ("学校", "schools"),
            ("专业", "majors"),
            ("求职状态", "job_status"),
            ("简历语言", "resume_language"),
        ]
        for index, (label, key) in enumerate(query_rows):
            row = index // 2
            col = (index % 2) * 2
            query_layout.addWidget(QLabel(label), row, col)
            query_layout.addWidget(query_fields[key], row, col + 1)

        score_box = QGroupBox("评分条件")
        score_layout = QGridLayout(score_box)
        must_edit = QLineEdit()
        nice_edit = QLineEdit()
        reject_edit = QLineEdit()
        score_layout.addWidget(QLabel("必备项"), 0, 0)
        score_layout.addWidget(must_edit, 0, 1)
        score_layout.addWidget(QLabel("加分项"), 1, 0)
        score_layout.addWidget(nice_edit, 1, 1)
        score_layout.addWidget(QLabel("排除项"), 2, 0)
        score_layout.addWidget(reject_edit, 2, 1)

        parse_bar = QHBoxLayout()
        parse_hint = QLabel("填写 JD 后可在这里直接解析并确认查询条件。")
        parse_job_btn_local = QPushButton("解析JD")
        confirm_parsed_btn = QPushButton("条件确认")
        confirm_parsed_btn.setEnabled(False)
        parse_bar.addWidget(parse_hint)
        parse_bar.addWidget(confirm_parsed_btn)
        parse_bar.addWidget(parse_job_btn_local)

        body_layout.addWidget(basic_box)
        body_layout.addWidget(query_box)
        body_layout.addWidget(score_box)
        body_layout.addLayout(parse_bar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        body_layout.addWidget(buttons)

        parsed_fields: dict[str, Any] = {}

        def apply_field_values(values: dict[str, Any]) -> None:
            title_edit.setText(values.get("title") or "")
            jd_edit.setPlainText(values.get("jd") or "")
            greeting_edit.setPlainText(values.get("greeting_template") or "")
            followup_edit.setPlainText(values.get("followup_template") or "")
            min_score_edit.setValue(int(values.get("min_score") or 75))
            auto_greet_edit.setChecked(bool(values.get("auto_greet")))
            dry_run_edit.setChecked(bool(values.get("dry_run", True)))
            set_combo_text(query_fields["keyword_match"], str(values.get("keyword_match") or KEYWORD_MATCH_OPTIONS[0]))
            for key in (
                "keywords",
                "position_keywords",
                "company_keywords",
                "current_city",
                "expected_city",
                "education",
                "school_tags",
                "current_industry",
                "current_position",
                "expected_industry",
                "expected_position",
                "languages",
                "schools",
                "majors",
                "job_status",
            ):
                query_fields[key].setText(_join_values(values.get(key)))
            set_combo_text(query_fields["recruit_type"], str(values.get("recruit_type") or RECRUIT_TYPE_OPTIONS[0]))
            set_combo_text(query_fields["work_years"], _single_work_year_value(values.get("work_years")))
            set_combo_text(query_fields["active_days"], str(values.get("active_days") or "30天内活跃"))
            set_combo_text(query_fields["gender"], str(values.get("gender") or "不限"))
            set_combo_text(query_fields["job_hopping"], str(values.get("job_hopping") or JOB_HOPPING_OPTIONS[0]))
            set_combo_text(query_fields["resume_language"], str(values.get("resume_language") or RESUME_LANGUAGE_OPTIONS[0]))
            must_edit.setText(_join_values(values.get("must_have")))
            nice_edit.setText(_join_values(values.get("nice_to_have")))
            reject_edit.setText(_join_values(values.get("reject_keywords")))

        def collect_values() -> dict[str, Any]:
            return {
                "title": title_edit.text().strip(),
                "jd": jd_edit.toPlainText().strip(),
                "greeting_template": greeting_edit.toPlainText().strip(),
                "followup_template": followup_edit.toPlainText().strip(),
                "min_score": min_score_edit.value(),
                "auto_greet": auto_greet_edit.isChecked(),
                "dry_run": dry_run_edit.isChecked(),
                "keyword_match": query_fields["keyword_match"].currentText(),
                "keywords": _csv(query_fields["keywords"].text()),
                "position_keywords": _csv(query_fields["position_keywords"].text()),
                "company_keywords": _csv(query_fields["company_keywords"].text()),
                "current_city": query_fields["current_city"].values(),
                "expected_city": query_fields["expected_city"].values(),
                "work_years": _single_work_year_list(query_fields["work_years"].currentText()),
                "education": query_fields["education"].values(),
                "recruit_type": query_fields["recruit_type"].currentText(),
                "school_tags": query_fields["school_tags"].values(),
                "current_industry": query_fields["current_industry"].values(),
                "current_position": query_fields["current_position"].values(),
                "expected_industry": query_fields["expected_industry"].values(),
                "expected_position": query_fields["expected_position"].values(),
                "active_days": query_fields["active_days"].currentText(),
                "gender": query_fields["gender"].currentText(),
                "job_hopping": query_fields["job_hopping"].currentText(),
                "languages": query_fields["languages"].values(),
                "schools": _csv(query_fields["schools"].text()),
                "majors": _csv(query_fields["majors"].text()),
                "job_status": query_fields["job_status"].values(),
                "resume_language": query_fields["resume_language"].currentText(),
                "must_have": _csv(must_edit.text()),
                "nice_to_have": _csv(nice_edit.text()),
                "reject_keywords": _csv(reject_edit.text()),
            }

        def open_parsed_condition_dialog() -> None:
            if not parsed_fields:
                message("还没有可应用的 JD 解析结果。")
                return
            confirm = QDialog(dialog)
            confirm.setWindowTitle("解析条件确认")
            confirm.setModal(True)
            confirm.resize(720, 760)
            confirm_layout = QVBoxLayout(confirm)
            confirm_layout.addWidget(QLabel("请勾选本次要写入岗位配置的条件；未勾选的字段会保持当前表单内容。"))
            checks: dict[str, Any] = {}
            for key, label, default_checked in parsed_condition_specs:
                value = field_display_value(parsed_fields.get(key))
                checkbox = QCheckBox(f"{label}：{value or '未解析到'}")
                checkbox.setEnabled(bool(value))
                checkbox.setChecked(bool(value) and default_checked)
                if key == "reject_keywords" and value:
                    checkbox.setToolTip("排除项默认不勾选，避免因为模型理解偏差导致搜索范围过窄。")
                confirm_layout.addWidget(checkbox)
                checks[key] = checkbox
            ok_cancel = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            ok_button = ok_cancel.button(QDialogButtonBox.StandardButton.Ok)
            if ok_button:
                ok_button.setText("应用勾选条件")
            ok_cancel.accepted.connect(confirm.accept)
            ok_cancel.rejected.connect(confirm.reject)
            confirm_layout.addWidget(ok_cancel)
            if confirm.exec() != QDialog.Accepted:
                return
            selected = [key for key, checkbox in checks.items() if checkbox.isChecked()]
            if not selected:
                message("你没有勾选任何要应用的条件。")
                return
            for key in selected:
                if key == "must_have":
                    must_edit.setText(field_display_value(parsed_fields.get(key)))
                    continue
                if key == "nice_to_have":
                    nice_edit.setText(field_display_value(parsed_fields.get(key)))
                    continue
                if key == "reject_keywords":
                    reject_edit.setText(field_display_value(parsed_fields.get(key)))
                    continue
                if key in query_fields:
                    widget = query_fields[key]
                    if hasattr(widget, "setText"):
                        widget.setText(field_display_value(parsed_fields.get(key)))
                    else:
                        set_combo_text(widget, field_display_value(parsed_fields.get(key)))
            parse_hint.setText("解析条件已应用。需要调整时可再次打开确认弹窗。")

        def parse_jd_async() -> None:
            title = title_edit.text().strip()
            jd = jd_edit.toPlainText().strip()
            if not title or not jd:
                message("请先填写岗位名称和 JD。")
                return
            append_log(["开始解析岗位 JD", f"岗位：{title}", f"模型：{state.env.qwen_model if state.env.qwen_api_key else '本地规则回退'}"])
            parse_job_btn_local.setEnabled(False)
            parse_job_btn_local.setText("解析中...")
            result.setPlainText("正在后台解析 JD，请稍候。窗口不会卡住，可以继续编辑表单。")
            result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

            def worker() -> None:
                try:
                    result_queue.put(("ok", parse_jd_with_qwen(title, jd, state.env)))
                except Exception as exc:
                    result_queue.put(("error", exc))

            def poll_result() -> None:
                try:
                    status, payload = result_queue.get_nowait()
                except queue.Empty:
                    QTimer.singleShot(150, poll_result)
                    return
                parse_job_btn_local.setEnabled(True)
                parse_job_btn_local.setText("解析JD")
                if status == "ok":
                    fields = draft_to_form_fields(payload)
                    parsed_fields.clear()
                    parsed_fields.update(fields)
                    confirm_parsed_btn.setEnabled(True)
                    parse_hint.setText("解析完成，已生成可确认条件。")
                    preview_lines = draft_preview_lines(payload.draft, payload.source, payload.error)
                    result.setPlainText("\n".join(["岗位 JD 解析预览", f"岗位：{title}", ""] + preview_lines))
                    append_log(
                        [
                            "岗位 JD 解析完成，请勾选要应用的条件",
                            f"来源：{'千问' if payload.source == 'qwen' else '本地规则回退'}",
                            f"搜索关键词：{field_display_value(fields.get('keywords')) or '-'}",
                            f"职位：{field_display_value(fields.get('position_keywords')) or '-'}；年限：{field_display_value(fields.get('work_years')) or '-'}；学历：{field_display_value(fields.get('education')) or '-'}",
                        ]
                    )
                    QTimer.singleShot(100, open_parsed_condition_dialog)
                else:
                    result.setPlainText(f"JD 解析失败：{payload}")
                    append_log(["JD 解析失败", str(payload)])

            threading.Thread(target=worker, daemon=True).start()
            QTimer.singleShot(150, poll_result)

        parse_job_btn_local.clicked.connect(parse_jd_async)
        confirm_parsed_btn.clicked.connect(open_parsed_condition_dialog)

        if existing_row:
            row_conditions = row_search_conditions(existing_row)
            apply_field_values(
                {
                    "title": existing_row["title"] or "",
                    "jd": existing_row["jd"] or "",
                    "greeting_template": existing_row["greeting_template"] or "",
                    "followup_template": existing_row["followup_template"] or "",
                    "min_score": int(existing_row["min_score"] or 75),
                    "auto_greet": bool(existing_row["auto_greet"]),
                    "dry_run": bool(existing_row["dry_run"]),
                    "keyword_match": row_conditions.get("keyword_match") or KEYWORD_MATCH_OPTIONS[0],
                    "keywords": row_conditions.get("keywords") or loads(existing_row["keywords"], []),
                    "position_keywords": row_conditions.get("position_keywords") or [],
                    "company_keywords": row_conditions.get("company_keywords") or [],
                    "current_city": row_conditions.get("current_city") or [],
                    "expected_city": row_conditions.get("expected_city") or _csv(existing_row["city"] or ""),
                    "work_years": row_conditions.get("work_years") or _csv(existing_row["experience"] or ""),
                    "education": row_conditions.get("education") or _csv(existing_row["education"] or ""),
                    "recruit_type": row_conditions.get("recruit_type") or RECRUIT_TYPE_OPTIONS[0],
                    "school_tags": row_conditions.get("school_tags") or [],
                    "current_industry": row_conditions.get("current_industry") or [],
                    "current_position": row_conditions.get("current_position") or [],
                    "expected_industry": row_conditions.get("expected_industry") or [],
                    "expected_position": row_conditions.get("expected_position") or [],
                    "active_days": row_conditions.get("active_days") or "30天内活跃",
                    "gender": row_conditions.get("gender") or "不限",
                    "job_hopping": row_conditions.get("job_hopping") or JOB_HOPPING_OPTIONS[0],
                    "languages": row_conditions.get("languages") or [],
                    "schools": row_conditions.get("schools") or [],
                    "majors": row_conditions.get("majors") or [],
                    "job_status": row_conditions.get("job_status") or [],
                    "resume_language": row_conditions.get("resume_language") or RESUME_LANGUAGE_OPTIONS[0],
                    "must_have": loads(existing_row["must_have"], []),
                    "nice_to_have": loads(existing_row["nice_to_have"], []),
                    "reject_keywords": loads(existing_row["reject_keywords"], []),
                }
            )
        else:
            apply_field_values(
                {
                    "min_score": 75,
                    "dry_run": True,
                    "keyword_match": KEYWORD_MATCH_OPTIONS[0],
                    "recruit_type": RECRUIT_TYPE_OPTIONS[0],
                    "active_days": "30天内活跃",
                    "gender": "不限",
                    "job_hopping": JOB_HOPPING_OPTIONS[0],
                    "resume_language": RESUME_LANGUAGE_OPTIONS[0],
                }
            )

        if dialog.exec() != QDialog.Accepted:
            return None
        values = collect_values()
        if not values["title"]:
            message("请填写岗位名称。")
            return None
        return values

    def job_search_conditions_from_values(values: dict[str, Any]) -> dict[str, Any]:
        return {
            "_schema_version": 2,
            "keyword_match": values["keyword_match"],
            "keywords": values["keywords"],
            "position_keywords": values["position_keywords"],
            "company_keywords": values["company_keywords"],
            "current_city": values["current_city"],
            "expected_city": values["expected_city"],
            "work_years": values["work_years"],
            "education": values["education"],
            "recruit_type": values["recruit_type"],
            "school_tags": values["school_tags"],
            "current_industry": values["current_industry"],
            "current_position": values["current_position"],
            "expected_industry": values["expected_industry"],
            "expected_position": values["expected_position"],
            "active_days": values["active_days"],
            "gender": values["gender"],
            "job_hopping": values["job_hopping"],
            "languages": values["languages"],
            "schools": values["schools"],
            "majors": values["majors"],
            "job_status": values["job_status"],
            "resume_language": values["resume_language"],
        }

    def add_job(values: dict[str, Any]) -> None:
        title = values["title"].strip()
        if not title:
            message("请填写岗位名称。")
            return
        job_id = db.add_job(
            title=title,
            jd=values["jd"].strip(),
            keywords=values["keywords"],
            must_have=values["must_have"],
            nice_to_have=values["nice_to_have"],
            reject_keywords=values["reject_keywords"],
            city="，".join(values["expected_city"]),
            experience="，".join(values["work_years"]),
            education="，".join(values["education"]),
            search_conditions=job_search_conditions_from_values(values),
            min_score=int(values["min_score"]),
            greeting_template=values["greeting_template"].strip(),
            followup_template=values["followup_template"].strip(),
            auto_greet=bool(values["auto_greet"]),
            dry_run=bool(values["dry_run"]),
        )
        state.current_job_id = int(job_id)
        db.log(f"新增岗位：{title}", payload={"job_id": job_id})
        refresh_all()
        append_log(["岗位已新增", f"岗位：{title}"])

    def save_job(job_id: int, values: dict[str, Any]) -> None:
        title = values["title"].strip()
        if not title:
            message("请填写岗位名称。")
            return
        db.execute(
            """
            UPDATE jobs
            SET title = ?, jd = ?, keywords = ?, must_have = ?, nice_to_have = ?,
                reject_keywords = ?, city = ?, experience = ?, education = ?,
                search_conditions = ?, min_score = ?, greeting_template = ?, followup_template = ?, auto_greet = ?, dry_run = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                title,
                values["jd"].strip(),
                json.dumps(values["keywords"], ensure_ascii=False),
                json.dumps(values["must_have"], ensure_ascii=False),
                json.dumps(values["nice_to_have"], ensure_ascii=False),
                json.dumps(values["reject_keywords"], ensure_ascii=False),
                "，".join(values["expected_city"]),
                "，".join(values["work_years"]),
                "，".join(values["education"]),
                json.dumps(job_search_conditions_from_values(values), ensure_ascii=False),
                int(values["min_score"]),
                values["greeting_template"].strip(),
                values["followup_template"].strip(),
                int(values["auto_greet"]),
                int(values["dry_run"]),
                int(job_id),
            ),
        )
        db.log(f"保存岗位：{title}", payload={"job_id": int(job_id)})
        refresh_all()
        append_log(["岗位已保存", f"岗位：{title}"])

    def add_job_from_dialog() -> None:
        values = open_job_dialog(None)
        if not values:
            return
        add_job(values)

    def edit_job_from_dialog() -> None:
        job_id = selected_id(jobs_table) or job_combo.currentData()
        if not job_id:
            message("请先在列表中选择岗位。")
            return
        row = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (int(job_id),))
        if not row:
            message("岗位不存在。")
            return
        values = open_job_dialog(row)
        if not values:
            return
        state.current_job_id = int(job_id)
        save_job(int(job_id), values)

    def delete_selected_job() -> None:
        job_id = selected_id(jobs_table) or job_combo.currentData()
        if not job_id:
            message("请先选择岗位。")
            return
        row = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (int(job_id),))
        if not row:
            message("岗位不存在。")
            return
        ok = QMessageBox.question(
            window,
            "删除岗位",
            f"确认删除岗位「{row['title']}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        db.execute("DELETE FROM jobs WHERE id = ?", (int(job_id),))
        if state.current_job_id == int(job_id):
            state.current_job_id = None
        db.log(f"删除岗位：{row['title']}", payload={"job_id": int(job_id)})
        refresh_all()

    save_job_btn.clicked.connect(edit_job_from_dialog)
    add_job_btn.clicked.connect(add_job_from_dialog)
    delete_job_btn.clicked.connect(delete_selected_job)
    jobs_table.cellClicked.connect(lambda row, _col: load_job_form(int(jobs_table.item(row, 0).text())))
    jobs_table.cellDoubleClicked.connect(lambda _row, _col: edit_job_from_dialog())

    # Tasks tab
    task_tab = QWidget()
    task_layout = QVBoxLayout(task_tab)
    task_layout.setContentsMargins(12, 10, 12, 12)
    task_layout.setSpacing(8)
    task_summary_label = QLabel("队列加载中...")
    task_summary_label.setFixedHeight(28)
    task_layout.addWidget(task_summary_label)

    add_task_btn = QPushButton("新增")
    edit_task_btn = QPushButton("编辑")
    clone_task_btn = QPushButton("复制")
    delete_task_btn = QPushButton("删除")
    move_up_btn = QPushButton("上移")
    move_down_btn = QPushButton("下移")
    reorder_by_priority_btn = QPushButton("按优先级重排")
    run_task_btn = QPushButton("运行选中")
    run_next_task_btn = QPushButton("全部运行")
    stop_task_btn = QPushButton("终止任务")
    pause_task_btn = QPushButton("暂停")
    resume_task_btn = QPushButton("继续")
    retry_step_btn = QPushButton("重试步骤")
    skip_task_candidate_btn = QPushButton("跳过人选")
    reset_task_btn = QPushButton("重置任务")
    refresh_task_btn = QPushButton("刷新")
    task_detail_popup_btn = QPushButton("详情")
    task_action_buttons = [
        run_task_btn,
        run_next_task_btn,
        resume_task_btn,
        retry_step_btn,
        pause_task_btn,
        skip_task_candidate_btn,
        stop_task_btn,
        reset_task_btn,
        add_task_btn,
        edit_task_btn,
        clone_task_btn,
        delete_task_btn,
        move_up_btn,
        move_down_btn,
        reorder_by_priority_btn,
        refresh_task_btn,
        task_detail_popup_btn,
    ]
    for button in task_action_buttons:
        button.setMinimumHeight(30)
        button.setMinimumWidth(76)

    task_actions_panel = QWidget()
    task_actions_grid = QGridLayout(task_actions_panel)
    task_actions_grid.setContentsMargins(0, 0, 0, 0)
    task_actions_grid.setHorizontalSpacing(8)
    task_actions_grid.setVerticalSpacing(8)

    def add_task_action_row(row_index: int, label: str, buttons: list[Any]) -> None:
        title = QLabel(label)
        title.setMinimumWidth(34)
        title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        task_actions_grid.addWidget(title, row_index, 0)
        for col, button in enumerate(buttons, start=1):
            task_actions_grid.addWidget(button, row_index, col)
        task_actions_grid.setColumnStretch(len(buttons) + 1, 1)

    add_task_action_row(0, "维护", [add_task_btn, edit_task_btn, clone_task_btn, move_up_btn, move_down_btn, reorder_by_priority_btn])
    add_task_action_row(1, "执行", [run_next_task_btn, run_task_btn, pause_task_btn, resume_task_btn, retry_step_btn, skip_task_candidate_btn, refresh_task_btn, task_detail_popup_btn])
    add_task_action_row(2, "危险", [reset_task_btn, stop_task_btn, delete_task_btn])
    task_layout.addWidget(task_actions_panel)

    task_splitter = QSplitter()
    task_splitter.setOrientation(Qt.Orientation.Horizontal)
    task_splitter.addWidget(tasks_table)
    task_side = QWidget()
    task_side_layout = QVBoxLayout(task_side)
    task_detail_box = QGroupBox("选中任务")
    task_detail_layout = QVBoxLayout(task_detail_box)
    task_detail_text = QTextEdit()
    task_detail_text.setReadOnly(True)
    task_detail_text.setMinimumHeight(190)
    task_detail_layout.addWidget(task_detail_text)
    task_log_box = QGroupBox("提醒与日志")
    task_log_layout = QVBoxLayout(task_log_box)
    task_recent_logs_text = QTextEdit()
    task_recent_logs_text.setReadOnly(True)
    task_recent_logs_text.setMinimumHeight(220)
    task_log_layout.addWidget(task_recent_logs_text)
    task_side_layout.addWidget(task_detail_box)
    task_side_layout.addWidget(task_log_box)
    task_splitter.addWidget(task_side)
    task_side.setVisible(False)
    task_splitter.setStretchFactor(0, 3)
    task_splitter.setStretchFactor(1, 2)
    task_splitter.setMinimumHeight(360)
    task_layout.addWidget(task_splitter)
    task_layout.setStretch(0, 0)
    task_layout.setStretch(1, 0)
    task_layout.setStretch(2, 1)
    tabs.addTab(task_tab, "任务")

    def task_defaults_for_job(job_id: int | None) -> dict[str, Any]:
        if not job_id:
            return {"min_score": 75, "auto_greet": False, "dry_run": True, "use_ai_scoring": True}
        row = db.fetch_one("SELECT min_score, auto_greet, dry_run FROM jobs WHERE id = ?", (int(job_id),))
        if not row:
            return {"min_score": 75, "auto_greet": False, "dry_run": True, "use_ai_scoring": True}
        return {
            "min_score": int(row["min_score"]),
            "auto_greet": bool(row["auto_greet"]),
            "dry_run": bool(row["dry_run"]),
            "use_ai_scoring": True,
        }

    def open_task_dialog(initial: dict[str, Any] | None = None) -> dict[str, Any] | None:
        initial = dict(initial or {})
        dialog = QDialog(window)
        dialog.setWindowTitle("编辑任务" if initial else "创建任务")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        default_name = initial.get("name") or f"任务-{datetime.now().strftime('%m%d-%H%M')}"
        name_edit = QLineEdit(default_name)
        job_edit = QComboBox()
        account_edit = QComboBox()
        for i in range(task_job_combo.count()):
            data = task_job_combo.itemData(i)
            text = task_job_combo.itemText(i)
            if data:
                job_edit.addItem(text, data)
        for i in range(task_account_combo.count()):
            data = task_account_combo.itemData(i)
            text = task_account_combo.itemText(i)
            if data:
                account_edit.addItem(text, data)
        max_edit = QSpinBox()
        max_edit.setRange(1, 1000)
        max_edit.setValue(int(initial.get("max_candidates") or 30))
        target_type_edit = QComboBox()
        for label, value in TASK_TARGET_OPTIONS:
            target_type_edit.addItem(label, value)
        target_type = str(initial.get("target_type") or "resume")
        target_index = target_type_edit.findData(target_type if target_type in {"resume", "greeting"} else "resume")
        if target_index >= 0:
            target_type_edit.setCurrentIndex(target_index)
        hide_viewed_edit = QCheckBox("隐藏已查看")
        hide_viewed_edit.setChecked(bool(initial.get("hide_viewed")))
        hide_contacted_edit = QCheckBox("隐藏已沟通")
        hide_contacted_edit.setChecked(bool(initial.get("hide_contacted")))
        hide_contact_info_edit = QCheckBox("隐藏已获取联系方式")
        hide_contact_info_edit.setChecked(bool(initial.get("hide_contact_info")))
        age_min_edit = age_spin()
        age_max_edit = age_spin()
        set_age_value(age_min_edit, initial.get("age_min"))
        set_age_value(age_max_edit, initial.get("age_max"))
        score_follow_job = QCheckBox("使用岗位默认阈值")
        score_follow_job.setChecked(initial.get("greet_min_score") is None)
        score_edit = QSpinBox()
        score_edit.setRange(0, 100)
        score_edit.setValue(int(initial.get("greet_min_score") or 75))
        priority_edit = QSpinBox()
        priority_edit.setRange(1, 999)
        priority_edit.setValue(int(initial.get("priority") or 100))
        schedule_edit = QLineEdit(str(initial.get("schedule_text") or ""))
        schedule_edit.setPlaceholderText("例如：立即 / 今天18:30 / 每天09:30")
        retry_limit_edit = QSpinBox()
        retry_limit_edit.setRange(0, 10)
        retry_limit_edit.setValue(int(initial.get("retry_limit") or 1))
        retry_interval_edit = QSpinBox()
        retry_interval_edit.setRange(10, 3600)
        retry_interval_edit.setValue(int(initial.get("retry_interval_sec") or 60))
        auto_edit = QCheckBox("自动打招呼")
        auto_edit.setChecked(bool(initial.get("auto_greet")))
        dry_edit = QCheckBox("Dry-run")
        dry_edit.setChecked(bool(initial.get("dry_run", True)))
        ai_score_edit = QCheckBox("评分走AI（千问）")
        ai_score_edit.setChecked(bool(initial.get("use_ai_scoring", True)))

        default_job_id = initial.get("job_id")
        if default_job_id is None:
            default_job_id = task_job_combo.currentData()
        default_account_id = initial.get("account_id")
        if default_account_id is None:
            default_account_id = task_account_combo.currentData()
        if default_job_id:
            idx = job_edit.findData(int(default_job_id))
            if idx >= 0:
                job_edit.setCurrentIndex(idx)
        if default_account_id:
            idx = account_edit.findData(int(default_account_id))
            if idx >= 0:
                account_edit.setCurrentIndex(idx)

        def sync_job_defaults() -> None:
            defaults = task_defaults_for_job(job_edit.currentData())
            if score_follow_job.isChecked():
                score_edit.setValue(int(defaults["min_score"]))
            if not initial:
                auto_edit.setChecked(bool(defaults["auto_greet"]))
                dry_edit.setChecked(bool(defaults["dry_run"]))
                ai_score_edit.setChecked(bool(defaults["use_ai_scoring"]))

        def sync_score_enabled() -> None:
            score_edit.setEnabled(not score_follow_job.isChecked())

        job_edit.currentIndexChanged.connect(sync_job_defaults)
        score_follow_job.toggled.connect(sync_score_enabled)
        sync_job_defaults()
        sync_score_enabled()

        form.addRow("任务名", name_edit)
        form.addRow("岗位", job_edit)
        form.addRow("账号", account_edit)
        target_widget = QWidget()
        target_layout = QHBoxLayout(target_widget)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(target_type_edit)
        target_layout.addWidget(max_edit)
        form.addRow("任务目标", target_widget)
        result_filter_widget = QWidget()
        result_filter_layout = QHBoxLayout(result_filter_widget)
        result_filter_layout.setContentsMargins(0, 0, 0, 0)
        result_filter_layout.addWidget(hide_viewed_edit)
        result_filter_layout.addWidget(hide_contacted_edit)
        result_filter_layout.addWidget(hide_contact_info_edit)
        form.addRow("结果过滤", result_filter_widget)
        form.addRow("年龄", age_range_widget(age_min_edit, age_max_edit))
        form.addRow("", score_follow_job)
        form.addRow("打招呼阈值", score_edit)
        form.addRow("优先级", priority_edit)
        form.addRow("计划", schedule_edit)
        form.addRow("失败重试次数", retry_limit_edit)
        form.addRow("重试间隔(秒)", retry_interval_edit)
        form.addRow("", auto_edit)
        form.addRow("", dry_edit)
        form.addRow("", ai_score_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return None
        if job_edit.currentData() is None or account_edit.currentData() is None:
            message("请选择任务岗位和账号。")
            return None
        return {
            "name": name_edit.text().strip() or f"任务-{datetime.now().strftime('%m%d-%H%M')}",
            "job_id": int(job_edit.currentData()),
            "account_id": int(account_edit.currentData()),
            "max_candidates": max_edit.value(),
            "target_type": str(target_type_edit.currentData() or "resume"),
            "hide_viewed": hide_viewed_edit.isChecked(),
            "hide_contacted": hide_contacted_edit.isChecked(),
            "hide_contact_info": hide_contact_info_edit.isChecked(),
            "age_min": optional_age_value(age_min_edit),
            "age_max": optional_age_value(age_max_edit),
            "greet_min_score": None if score_follow_job.isChecked() else score_edit.value(),
            "priority": priority_edit.value(),
            "auto_greet": auto_edit.isChecked(),
            "dry_run": dry_edit.isChecked(),
            "use_ai_scoring": ai_score_edit.isChecked(),
            "schedule_text": schedule_edit.text().strip(),
            "retry_limit": retry_limit_edit.value(),
            "retry_interval_sec": retry_interval_edit.value(),
        }

    def add_task() -> None:
        values = open_task_dialog(None)
        if not values:
            return
        task_id = db.add_task(
            name=values["name"],
            job_id=values["job_id"],
            account_id=values["account_id"],
            max_candidates=values["max_candidates"],
            target_type=values["target_type"],
            hide_viewed=values["hide_viewed"],
            hide_contacted=values["hide_contacted"],
            hide_contact_info=values["hide_contact_info"],
            auto_greet=values["auto_greet"],
            dry_run=values["dry_run"],
            use_ai_scoring=values["use_ai_scoring"],
            schedule_text=values["schedule_text"],
            greet_min_score=values["greet_min_score"],
            age_min=values["age_min"],
            age_max=values["age_max"],
            priority=values["priority"],
            retry_limit=values["retry_limit"],
            retry_interval_sec=values["retry_interval_sec"],
        )
        db.log(f"新增任务：{values['name']}", task_id=task_id, account_id=values["account_id"])
        refresh_all()

    def selected_task_row() -> Any | None:
        task_id = selected_id(tasks_table)
        if not task_id:
            return None
        return db.fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    def edit_task() -> None:
        task = selected_task_row()
        if not task:
            message("请先选择任务。")
            return
        values = open_task_dialog(dict(task))
        if not values:
            return
        db.update_task(
            int(task["id"]),
            name=values["name"],
            job_id=values["job_id"],
            account_id=values["account_id"],
            max_candidates=values["max_candidates"],
            target_type=values["target_type"],
            hide_viewed=values["hide_viewed"],
            hide_contacted=values["hide_contacted"],
            hide_contact_info=values["hide_contact_info"],
            auto_greet=values["auto_greet"],
            dry_run=values["dry_run"],
            use_ai_scoring=values["use_ai_scoring"],
            schedule_text=values["schedule_text"],
            greet_min_score=values["greet_min_score"],
            age_min=values["age_min"],
            age_max=values["age_max"],
            priority=values["priority"],
            retry_limit=values["retry_limit"],
            retry_interval_sec=values["retry_interval_sec"],
        )
        db.log(f"编辑任务：{values['name']}", task_id=int(task["id"]), account_id=values["account_id"])
        refresh_all()

    def clone_task() -> None:
        task = selected_task_row()
        if not task:
            message("请先选择任务。")
            return
        values = dict(task)
        values["name"] = f'{task["name"]}-复制'
        values = open_task_dialog(values)
        if not values:
            return
        task_id = db.add_task(
            name=values["name"],
            job_id=values["job_id"],
            account_id=values["account_id"],
            max_candidates=values["max_candidates"],
            target_type=values["target_type"],
            hide_viewed=values["hide_viewed"],
            hide_contacted=values["hide_contacted"],
            hide_contact_info=values["hide_contact_info"],
            auto_greet=values["auto_greet"],
            dry_run=values["dry_run"],
            use_ai_scoring=values["use_ai_scoring"],
            schedule_text=values["schedule_text"],
            greet_min_score=values["greet_min_score"],
            age_min=values["age_min"],
            age_max=values["age_max"],
            priority=values["priority"],
            retry_limit=values["retry_limit"],
            retry_interval_sec=values["retry_interval_sec"],
        )
        db.log(f"复制任务：{values['name']}", task_id=task_id, account_id=values["account_id"])
        refresh_all()

    def delete_task() -> None:
        task = selected_task_row()
        if not task:
            message("请先选择任务。")
            return
        ok = QMessageBox.question(
            window,
            "删除任务",
            f"确认删除任务「{task['name']}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        db.execute("DELETE FROM tasks WHERE id = ?", (int(task["id"]),))
        db.log(f"删除任务：{task['name']}")
        refresh_all()

    def persist_task_order(task_ids: list[int]) -> None:
        for idx, task_id in enumerate(task_ids, start=1):
            db.execute(
                "UPDATE tasks SET sort_order = ?, updated_at = datetime('now') WHERE id = ?",
                (idx * 10, int(task_id)),
            )

    def move_task(offset: int) -> None:
        if not task_rows_cache:
            return
        current_row = tasks_table.currentRow()
        if current_row < 0:
            message("请先选择任务。")
            return
        target = current_row + offset
        if target < 0 or target >= len(task_rows_cache):
            return
        task_ids = [int(row["id"]) for row in task_rows_cache]
        task_ids[current_row], task_ids[target] = task_ids[target], task_ids[current_row]
        persist_task_order(task_ids)
        refresh_tasks()
        tasks_table.selectRow(target)

    def reorder_tasks_by_priority() -> None:
        rows = db.fetch_all("SELECT id FROM tasks ORDER BY priority DESC, COALESCE(sort_order, id) ASC, id ASC")
        if not rows:
            return
        persist_task_order([int(row["id"]) for row in rows])
        refresh_tasks()
        append_log(["任务队列已按优先级重排"])

    def infer_resume_action(step: str) -> str:
        mapping = {
            TaskStep.OPEN_ACCOUNT.value: "open_search",
            TaskStep.CHECK_LOGIN.value: "open_search",
            TaskStep.OPEN_SEARCH.value: "open_search",
            TaskStep.APPLY_FILTERS.value: "apply_filters",
            TaskStep.COLLECT_CARDS.value: "collect_cards",
            TaskStep.OPEN_RESUME.value: "open_candidate",
            TaskStep.EXPAND_RESUME.value: "extract_resume",
            TaskStep.EXTRACT_RESUME.value: "extract_resume",
            TaskStep.SCORE.value: "score_resume",
            TaskStep.GENERATE_GREETING.value: "score_resume",
            TaskStep.SEND_GREETING.value: "send_greeting",
            TaskStep.NEXT_CANDIDATE.value: "next_candidate",
        }
        return mapping.get(str(step or ""), "open_search")

    def current_task_checkpoint(
        task_id: int,
        resume_action: str,
        step: TaskStep | str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        step_value = step.value if isinstance(step, TaskStep) else str(step or "")
        task_row = db.fetch_one("SELECT job_id, account_id FROM tasks WHERE id = ?", (int(task_id),))
        return {
            "version": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "resume_action": resume_action,
            "step": step_value,
            "task_id": int(task_id),
            "account_id": int(task_row["account_id"]) if task_row else state.current_account_id,
            "job_id": int(task_row["job_id"]) if task_row else state.current_job_id,
            "queue_mode": bool(state.current_task_queue_mode),
            "page_index": int(state.current_list_page_index or 1),
            "next_candidate_index": int(state.next_candidate_index or 0),
            "last_opened_candidate_index": state.last_opened_candidate_index,
            "current_page_card_count": int(state.current_page_card_count or 0),
            "candidate_id": state.last_candidate_id,
            "greeting_log_id": state.last_greeting_log_id,
            "url": web.url().toString(),
            "payload": payload or {},
        }

    def save_task_checkpoint(
        task_id: int | None,
        resume_action: str,
        step: TaskStep | str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not task_id:
            return
        state.engine.set_checkpoint(int(task_id), current_task_checkpoint(int(task_id), resume_action, step, payload))

    def restore_task_runtime(task_id: int, *, queue_mode: bool | None = None) -> dict[str, Any]:
        row = db.fetch_one("SELECT * FROM tasks WHERE id = ?", (int(task_id),))
        if not row:
            return {}
        checkpoint = loads(row["checkpoint_json"], {}) if "checkpoint_json" in row.keys() else {}
        if not isinstance(checkpoint, dict):
            checkpoint = {}
        state.cancelled_task_ids.discard(int(task_id))
        state.current_task_id = int(task_id)
        state.current_job_id = int(row["job_id"])
        state.current_task_queue_mode = bool(checkpoint.get("queue_mode")) if queue_mode is None else bool(queue_mode)
        state.current_task_min_score = int(row["greet_min_score"]) if row["greet_min_score"] is not None else None
        state.current_task_auto_greet = bool(row["auto_greet"])
        state.current_task_dry_run = bool(row["dry_run"])
        state.current_task_use_ai_scoring = bool(row["use_ai_scoring"])
        state.next_candidate_index = int(checkpoint.get("next_candidate_index") or 0)
        state.last_opened_candidate_index = (
            int(checkpoint["last_opened_candidate_index"])
            if checkpoint.get("last_opened_candidate_index") is not None
            else None
        )
        state.current_page_card_count = int(checkpoint.get("current_page_card_count") or 0)
        state.current_list_page_index = int(checkpoint.get("page_index") or 1)
        state.last_candidate_id = int(checkpoint["candidate_id"]) if checkpoint.get("candidate_id") else None
        state.last_greeting_log_id = int(checkpoint["greeting_log_id"]) if checkpoint.get("greeting_log_id") else None
        state.last_resume_text = ""
        state.last_greeting = ""
        if state.last_candidate_id:
            candidate_row = db.fetch_one(
                """
                SELECT c.greeting, rs.resume_text
                FROM candidates c
                LEFT JOIN resume_snapshots rs ON rs.id = c.last_snapshot_id
                WHERE c.id = ?
                """,
                (int(state.last_candidate_id),),
            )
            if candidate_row:
                state.last_resume_text = candidate_row["resume_text"] or ""
                state.last_greeting = candidate_row["greeting"] or ""
        for combo in (job_combo, task_job_combo, candidate_job_filter):
            idx = combo.findData(state.current_job_id)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        task_account_id = int(row["account_id"])
        for combo in (account_combo, task_account_combo):
            idx = combo.findData(task_account_id)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        if state.current_account_id != task_account_id or state.page is None:
            use_account(task_account_id)
        else:
            append_log(["断点恢复：复用当前账号页面", f"账号 ID：{task_account_id}", f"当前 URL：{web.url().toString() or '-'}"])
        return checkpoint

    def run_task(task_id: int, queue_mode: bool = False) -> bool:
        start_result = state.executor.start_task(int(task_id), queue_mode=queue_mode)
        if not start_result:
            return False
        state.cancelled_task_ids.discard(task_id)
        state.current_task_id = task_id
        state.current_task_queue_mode = bool(queue_mode)
        state.current_job_id = start_result.job_id
        state.current_task_min_score = start_result.min_score
        state.current_task_auto_greet = start_result.auto_greet
        state.current_task_dry_run = start_result.dry_run
        state.current_task_use_ai_scoring = start_result.use_ai_scoring
        state.next_candidate_index = 0
        state.last_opened_candidate_index = None
        state.current_page_card_count = 0
        state.current_list_page_index = 1
        state.last_candidate_id = None
        state.last_resume_text = ""
        state.last_greeting = ""
        state.last_greeting_log_id = None
        for combo in (job_combo, task_job_combo, candidate_job_filter):
            idx = combo.findData(start_result.job_id)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        for combo in (account_combo, task_account_combo):
            idx = combo.findData(start_result.account_id)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        use_account(start_result.account_id)
        state.pending_task_apply_id = task_id
        save_task_checkpoint(task_id, "apply_filters", TaskStep.APPLY_FILTERS, {"fresh_start": True})
        open_search_page()
        append_log(start_result.lines)
        return True

    def continue_task_queue(after_task_id: int) -> None:
        if not state.current_task_queue_mode:
            return
        next_task_id = state.executor.next_queued_task_id(int(after_task_id))
        if not next_task_id:
            state.current_task_queue_mode = False
            append_log([
                "队列任务已全部执行完成",
                f"最后完成任务 ID：{after_task_id}",
            ])
            refresh_all()
            return
        append_log(
            [
                "队列模式：准备自动启动下一条任务",
                f"当前任务：{after_task_id}",
                f"下一任务：{next_task_id}",
            ]
        )
        refresh_all()
        QTimer.singleShot(1200, lambda task_id=next_task_id: run_task(task_id, queue_mode=True))

    def dispatch_task_resume(task_id: int, *, retry: bool = False) -> None:
        row = db.fetch_one("SELECT name, status, current_step FROM tasks WHERE id = ?", (int(task_id),))
        if not row:
            message("任务不存在。")
            return
        task_status = str(row["status"] or "")
        if task_status in TASK_TERMINAL_STATUSES:
            status_label = {
                TaskStatus.DONE.value: "已完成",
                TaskStatus.FAILED.value: "已失败",
                TaskStatus.CANCELLED.value: "已终止",
                TaskStatus.SKIPPED.value: "已跳过",
            }.get(task_status, task_status)
            append_log(
                [
                    "任务不能从断点继续",
                    f"任务：{row['name']}",
                    f"当前状态：{status_label}",
                    "请点击“运行选中”重新跑，或先“重置任务”再进入队列执行。",
                ]
            )
            message(f"任务当前状态为{status_label}，不能继续断点。请运行选中或重置任务。")
            refresh_all()
            return
        if task_status == TaskStatus.PENDING.value:
            append_log(
                [
                    "待执行任务没有断点可继续",
                    f"任务：{row['name']}",
                    "请点击“运行选中”或“全部运行”。",
                ]
            )
            message("待执行任务没有断点，请点击运行选中或全部运行。")
            return
        checkpoint = restore_task_runtime(int(task_id))
        step_value = str((checkpoint.get("step") if checkpoint else "") or (row["current_step"] if row else "") or TaskStep.OPEN_SEARCH.value)
        action = str((checkpoint.get("resume_action") if checkpoint else "") or infer_resume_action(step_value))
        state.engine.resume(int(task_id), step_value)
        append_log(
            [
                "任务重试当前步骤" if retry else "任务从断点继续",
                f"任务 ID：{task_id}",
                f"步骤：{step_value}",
                f"恢复动作：{action}",
                f"下一位序号：第 {int(state.next_candidate_index or 0) + 1} 位",
            ]
        )
        refresh_all()
        if action == "open_search":
            state.pending_task_apply_id = int(task_id)
            open_search_page()
            return
        if action == "apply_filters":
            current_url = web.url().toString()
            if re.search(r"/search/getConditionItem", current_url, re.I):
                route_fill_search_and_submit(False, auto_submit=True, trigger_task_id=int(task_id), task_mode=True)
            else:
                state.pending_task_apply_id = int(task_id)
                open_search_page()
            return
        if action == "click_search":
            route_click_search_only(show_popup_on_fail=False, trigger_task_id=int(task_id), max_attempts=1)
            return
        if action == "apply_result_filters":
            route_apply_task_result_filters(
                int(task_id),
                lambda task_id=int(task_id): collect_candidate_cards_strict(
                    trigger_task_id=task_id,
                    task_mode=True,
                    show_popup_on_empty=False,
                    page_index=state.current_list_page_index,
                ),
            )
            return
        if action == "collect_cards":
            collect_candidate_cards_strict(
                trigger_task_id=int(task_id),
                task_mode=True,
                show_popup_on_empty=False,
                page_index=state.current_list_page_index,
            )
            return
        if action == "open_candidate":
            route_collect_and_open_candidate(None, close_current_detail=False, task_mode=True)
            return
        if action == "extract_resume":
            analyze_current_resume(auto_advance=True)
            return
        if action == "score_resume":
            score_last_resume(task_id=int(task_id))
            return
        if action == "send_greeting":
            fill_or_send_greeting(auto_trigger=True, expected_candidate_id=state.last_candidate_id)
            return
        if action == "next_candidate":
            schedule_next_candidate_open(int(task_id), 400)
            return
        state.pending_task_apply_id = int(task_id)
        open_search_page()

    def task_notice_without_pause(
        task_id: int | None,
        reason: str,
        detail: str = "",
        action_hint: str = "",
        *,
        severity: str = "warning",
        step: TaskStep | str = TaskStep.OPEN_RESUME,
        candidate_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not task_id:
            return
        state.runtime.notice_without_pause(
            int(task_id),
            TaskNotice(
                reason=reason,
                detail=detail,
                action_hint=action_hint,
                severity=severity,
                step=step,
                candidate_id=candidate_id,
                payload=payload or {},
            ),
            fallback_account_id=state.current_account_id,
        )

    def finish_task_without_pause(
        task_id: int | None,
        reason: str,
        detail: str = "",
        action_hint: str = "",
        *,
        severity: str = "info",
        step: TaskStep | str = TaskStep.DONE,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not task_id:
            return
        if _task_is_cancelled(state, int(task_id)):
            append_log(["任务已被终止，不再执行自动收尾", f"任务 ID：{task_id}"])
            return
        result_payload = state.executor.finish_without_pause(
            int(task_id),
            TaskNotice(
                reason=reason,
                detail=detail,
                action_hint=action_hint,
                severity=severity,
                step=step,
                payload=payload or {},
            ),
            fallback_account_id=state.current_account_id,
        )
        append_log(result_payload.lines)
        continue_task_queue(int(task_id))
        refresh_all()

    def skip_current_candidate_and_continue(
        reason: str,
        detail: str = "",
        action_hint: str = "",
        *,
        candidate_id: int | None = None,
        payload: dict[str, Any] | None = None,
        delay_ms: int = 900,
    ) -> None:
        task_id = state.current_task_id
        if not task_id:
            return
        if _task_automation_blocked(state, int(task_id)):
            append_log(["已跳过候选人异常续跑", f"任务 ID：{task_id}", f"任务状态：{_task_block_label(state, int(task_id))}"])
            return
        context = TaskExecutionContext(
            task_id=int(task_id),
            account_id=state.current_account_id,
            next_candidate_index=int(state.next_candidate_index or 0),
            current_page_card_count=int(state.current_page_card_count or 0),
            current_url=web.url().toString(),
            last_candidate_id=state.last_candidate_id,
            last_opened_candidate_index=state.last_opened_candidate_index,
        )
        skip_result = state.executor.skip_candidate_and_plan_next(
            context,
            TaskNotice(
                reason=reason,
                detail=detail,
                action_hint=action_hint or "系统已跳过该候选人，继续处理下一位。",
                severity="warning",
                step=TaskStep.NEXT_CANDIDATE,
                candidate_id=candidate_id,
                payload=payload or {},
            ),
            fallback_candidate_id=state.last_candidate_id,
            advance_payload=payload or {},
        )
        state.next_candidate_index = skip_result.next_candidate_index
        append_log(skip_result.lines)
        refresh_all()
        schedule_next_candidate_open(int(task_id), delay_ms)

    def stop_running_task() -> None:
        task_id = state.current_task_id or selected_id(tasks_table)
        if not task_id:
            message("当前没有正在执行的任务。")
            return
        task = db.fetch_one("SELECT * FROM tasks WHERE id = ?", (int(task_id),))
        if not task:
            message("任务不存在。")
            return
        state.cancelled_task_ids.add(int(task_id))
        state.pending_task_apply_id = None
        state.current_task_queue_mode = False
        state.current_task_id = int(task_id)
        state.last_candidate_id = None
        state.last_resume_text = ""
        state.last_greeting = ""
        state.last_greeting_log_id = None
        cancel_result = state.executor.cancel_task(int(task_id), "用户手动终止了当前任务执行")
        append_log([f"已终止任务：{task['name']}", *cancel_result.lines[1:], "后续自动步骤将被取消。"])
        refresh_all()

    def run_selected_task() -> None:
        task_id = selected_id(tasks_table)
        if not task_id:
            message("请先选择任务。")
            return
        run_task(int(task_id), queue_mode=False)
        refresh_all()

    def run_next_task() -> None:
        row = db.fetch_one(
            """
            SELECT id FROM tasks
            WHERE status IN ('pending', 'failed')
              AND (next_run_at IS NULL OR next_run_at <= datetime('now'))
            ORDER BY COALESCE(sort_order, id) ASC, id ASC
            LIMIT 1
            """
        )
        if not row:
            message("队列中没有可执行任务。")
            return
        run_task(int(row["id"]), queue_mode=True)
        refresh_all()

    def pause_selected_task() -> None:
        task_id = state.current_task_id or selected_id(tasks_table)
        if not task_id:
            return
        row = db.fetch_one("SELECT current_step FROM tasks WHERE id = ?", (int(task_id),))
        step_value = str(row["current_step"] or TaskStep.OPEN_SEARCH.value) if row else TaskStep.OPEN_SEARCH.value
        save_task_checkpoint(int(task_id), infer_resume_action(step_value), step_value, {"manual_pause": True})
        state.engine.pause_for_user(
            int(task_id),
            HumanIntervention(
                reason="人工标记暂停",
                detail="用户手动将任务标记为需要人工处理。",
                action_hint="处理页面问题后点击“继续当前任务”从断点恢复，或点“跳过当前人选”。",
            ),
        )
        state.pending_task_apply_id = None
        refresh_all()

    def resume_selected_task() -> None:
        task_id = selected_id(tasks_table) or state.current_task_id
        if not task_id:
            message("请先选择要继续的任务。")
            return
        dispatch_task_resume(int(task_id), retry=False)

    def retry_selected_task_step() -> None:
        task_id = selected_id(tasks_table) or state.current_task_id
        if not task_id:
            message("请先选择要重试的任务。")
            return
        dispatch_task_resume(int(task_id), retry=True)

    def skip_selected_task_candidate() -> None:
        task_id = selected_id(tasks_table) or state.current_task_id
        if not task_id:
            message("请先选择任务。")
            return
        restore_task_runtime(int(task_id))
        if _task_is_paused(state, int(task_id)):
            state.engine.resume(int(task_id), TaskStep.NEXT_CANDIDATE)
        skip_current_candidate_and_continue(
            "用户手动跳过当前人选",
            "用户在任务页点击“跳过当前人选”。",
            "系统将从断点里的下一位候选人继续。",
            candidate_id=state.last_candidate_id,
            payload={"manual_skip": True},
            delay_ms=400,
        )

    def reset_selected_task() -> None:
        task = selected_task_row()
        if not task:
            message("请先选择任务。")
            return
        db.execute(
            """
            UPDATE tasks
            SET status = 'pending', current_step = 'INIT', last_error = '', checkpoint_json = '{}', next_run_at = NULL,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (int(task["id"]),),
        )
        db.log(f"任务重置为待执行：{task['name']}", task_id=int(task["id"]), account_id=task["account_id"])
        refresh_all()

    def show_selected_task_detail() -> None:
        if not selected_id(tasks_table):
            message("请先选择任务。")
            return
        update_selected_task_panel()
        dialog = QDialog(window)
        dialog.setWindowTitle("任务详情")
        dialog.resize(760, 640)
        layout = QVBoxLayout(dialog)
        detail = QTextEdit()
        detail.setReadOnly(True)
        detail.setPlainText(task_detail_text.toPlainText() if task_detail_text is not None else "")
        logs = QTextEdit()
        logs.setReadOnly(True)
        logs.setPlainText(task_recent_logs_text.toPlainText() if task_recent_logs_text is not None else "")
        layout.addWidget(QLabel("任务概况"))
        layout.addWidget(detail)
        layout.addWidget(QLabel("提醒与日志"))
        layout.addWidget(logs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    add_task_btn.clicked.connect(add_task)
    edit_task_btn.clicked.connect(edit_task)
    clone_task_btn.clicked.connect(clone_task)
    delete_task_btn.clicked.connect(delete_task)
    move_up_btn.clicked.connect(lambda: move_task(-1))
    move_down_btn.clicked.connect(lambda: move_task(1))
    reorder_by_priority_btn.clicked.connect(reorder_tasks_by_priority)
    run_task_btn.clicked.connect(run_selected_task)
    run_next_task_btn.clicked.connect(run_next_task)
    stop_task_btn.clicked.connect(stop_running_task)
    pause_task_btn.clicked.connect(pause_selected_task)
    resume_task_btn.clicked.connect(resume_selected_task)
    retry_step_btn.clicked.connect(retry_selected_task_step)
    skip_task_candidate_btn.clicked.connect(skip_selected_task_candidate)
    reset_task_btn.clicked.connect(reset_selected_task)
    refresh_task_btn.clicked.connect(refresh_tasks)
    task_detail_popup_btn.clicked.connect(show_selected_task_detail)
    tasks_table.itemSelectionChanged.connect(update_selected_task_panel)
    tasks_table.cellDoubleClicked.connect(lambda _row, _col: edit_task())

    # Candidates tab
    candidate_tab = QWidget()
    candidate_layout = QVBoxLayout(candidate_tab)
    candidate_layout.setContentsMargins(12, 10, 12, 12)
    candidate_layout.setSpacing(8)
    candidate_toolbar = QVBoxLayout()
    candidate_toolbar.setSpacing(8)
    candidate_stats_label = QLabel("候选人清单")
    candidate_filter_row = QHBoxLayout()
    candidate_filter_row.setSpacing(8)
    candidate_batch_bar = QWidget()
    candidate_batch_row = QHBoxLayout()
    candidate_batch_row.setContentsMargins(10, 6, 10, 6)
    candidate_batch_row.setSpacing(8)
    candidate_batch_bar.setLayout(candidate_batch_row)
    candidate_batch_bar.setVisible(False)
    candidate_selected_label = QLabel("已选 0 人")
    candidate_single_row = QHBoxLayout()
    candidate_single_row.setSpacing(8)
    candidate_detail_btn = QPushButton("查看详情")
    candidate_open_profile_btn = QPushButton("打开简历")
    candidate_snapshot_btn = QPushButton("查看全文快照")
    candidate_refresh_btn = QPushButton("刷新")
    candidate_reject_btn = QPushButton("淘汰")
    candidate_delete_btn = QPushButton("删除")
    candidate_select_all_btn = QPushButton("全选本页")
    candidate_clear_selection_btn = QPushButton("取消选择")
    candidate_batch_reject_btn = QPushButton("批量淘汰")
    candidate_batch_restore_btn = QPushButton("批量恢复")
    candidate_batch_delete_btn = QPushButton("批量删除")
    candidate_search_edit = QLineEdit()
    candidate_search_edit.setPlaceholderText("搜索候选人 / 公司 / 职位 / 摘要")
    candidate_sort_combo = QComboBox()
    candidate_sort_combo.addItem("评分高到低", "score_desc")
    candidate_sort_combo.addItem("评分低到高", "score_asc")
    candidate_sort_combo.addItem("最近获取", "created_desc")
    candidate_sort_combo.addItem("最近更新", "updated_desc")
    candidate_score_filter_combo = QComboBox()
    candidate_score_filter_combo.addItem("评分：全部", "all")
    candidate_score_filter_combo.addItem("评分：未评分", "unscored")
    candidate_score_filter_combo.addItem("评分：0-59", "lt60")
    candidate_score_filter_combo.addItem("评分：60-74", "60_74")
    candidate_score_filter_combo.addItem("评分：75-89", "75_89")
    candidate_score_filter_combo.addItem("评分：90+", "ge90")
    candidate_state_filter_btn = QPushButton("状态筛选")
    candidate_clear_filters_btn = QPushButton("重置筛选")
    candidate_prev_page_btn = QPushButton("上一页")
    candidate_next_page_btn = QPushButton("下一页")
    candidate_page_label = QLabel("第 1 / 1 页")
    candidate_page_size_combo = QComboBox()
    for size in (50, 100, 200):
        candidate_page_size_combo.addItem(f"每页 {size}", size)
    candidate_page_size_combo.setCurrentIndex(1)
    candidate_state_filter_keys: set[str] = {"unscored", "scored", "contacted", "rejected"}
    candidate_state_options = [
        ("unscored", "未评分"),
        ("scored", "已评分"),
        ("contacted", "已沟通"),
        ("rejected", "已淘汰"),
    ]
    candidate_filter_row.addWidget(QLabel("岗位"))
    candidate_filter_row.addWidget(candidate_job_filter)
    candidate_filter_row.addWidget(QLabel("排序"))
    candidate_filter_row.addWidget(candidate_sort_combo)
    candidate_filter_row.addWidget(QLabel("评分"))
    candidate_filter_row.addWidget(candidate_score_filter_combo)
    candidate_filter_row.addWidget(candidate_search_edit, stretch=1)
    candidate_filter_row.addWidget(candidate_state_filter_btn)
    candidate_filter_row.addWidget(candidate_clear_filters_btn)
    candidate_filter_row.addWidget(candidate_refresh_btn)
    candidate_filter_row.addWidget(candidate_prev_page_btn)
    candidate_filter_row.addWidget(candidate_page_label)
    candidate_filter_row.addWidget(candidate_next_page_btn)
    candidate_filter_row.addWidget(candidate_page_size_combo)
    candidate_batch_row.addWidget(candidate_selected_label)
    candidate_batch_row.addWidget(candidate_select_all_btn)
    candidate_batch_row.addWidget(candidate_clear_selection_btn)
    candidate_batch_row.addWidget(candidate_batch_reject_btn)
    candidate_batch_row.addWidget(candidate_batch_restore_btn)
    candidate_batch_row.addWidget(candidate_batch_delete_btn)
    candidate_batch_row.addStretch(1)
    candidate_single_row.addWidget(QLabel("当前人选"))
    candidate_single_row.addWidget(candidate_detail_btn)
    candidate_single_row.addWidget(candidate_open_profile_btn)
    candidate_single_row.addWidget(candidate_snapshot_btn)
    candidate_single_row.addWidget(candidate_reject_btn)
    candidate_single_row.addWidget(candidate_delete_btn)
    candidate_single_row.addStretch(1)
    candidate_toolbar.addWidget(candidate_stats_label)
    candidate_toolbar.addLayout(candidate_filter_row)
    candidate_toolbar.addWidget(candidate_batch_bar)
    candidate_layout.addLayout(candidate_toolbar)
    candidate_layout.addWidget(candidates_table)
    candidate_layout.addLayout(candidate_single_row)
    tabs.addTab(candidate_tab, "候选人")
    candidate_job_filter.currentIndexChanged.connect(lambda _index: reset_candidate_page())

    def update_candidate_state_filter_button() -> None:
        selected = len(candidate_state_filter_keys)
        total = len(candidate_state_options)
        candidate_state_filter_btn.setText("状态筛选" if selected == total else f"状态筛选({selected})")

    def candidate_score_passes(row: Any) -> bool:
        mode = candidate_score_filter_combo.currentData()
        score = row["score"]
        if mode == "all":
            return True
        if mode == "unscored":
            return score is None
        if score is None:
            return False
        score_value = int(score)
        return {
            "lt60": score_value < 60,
            "60_74": 60 <= score_value <= 74,
            "75_89": 75 <= score_value <= 89,
            "ge90": score_value >= 90,
        }.get(mode, True)

    def candidate_status_selected(row: Any) -> bool:
        return bool(_candidate_filter_buckets(row) & candidate_state_filter_keys)

    def sort_candidates(rows: list[Any]) -> list[Any]:
        mode = candidate_sort_combo.currentData()

        def updated_key(row: Any) -> str:
            return str(row["updated_at"] or "")

        def created_key(row: Any) -> str:
            return str(row["created_at"] or "")

        if mode == "created_desc":
            return sorted(rows, key=lambda row: (created_key(row), int(row["id"])), reverse=True)
        if mode == "updated_desc":
            return sorted(rows, key=lambda row: (updated_key(row), int(row["id"])), reverse=True)
        rows = sorted(rows, key=lambda row: (updated_key(row), int(row["id"])), reverse=True)
        if mode == "score_asc":
            return sorted(rows, key=lambda row: (row["score"] is None, int(row["score"] or 0), int(row["id"])))
        return sorted(rows, key=lambda row: (row["score"] is None, -int(row["score"] or 0), int(row["id"])))

    def open_candidate_state_filter_dialog() -> None:
        dialog = QDialog(window)
        dialog.setWindowTitle("状态筛选")
        dialog.setModal(True)
        dialog.resize(420, 300)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("多选状态用于同时查看不同阶段的候选人。"))
        checks: dict[str, Any] = {}
        for key, label in candidate_state_options:
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in candidate_state_filter_keys)
            layout.addWidget(checkbox)
            checks[key] = checkbox
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button:
            ok_button.setText("应用筛选")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        selected = {key for key, checkbox in checks.items() if checkbox.isChecked()}
        if not selected:
            message("至少保留一个状态筛选项。")
            return
        candidate_state_filter_keys.clear()
        candidate_state_filter_keys.update(selected)
        update_candidate_state_filter_button()
        candidate_page["page"] = 1
        refresh_candidates()

    def reset_candidate_page() -> None:
        candidate_page["page"] = 1
        refresh_candidates()

    def change_candidate_page(delta: int) -> None:
        candidate_page["page"] = max(1, int(candidate_page.get("page") or 1) + int(delta))
        refresh_candidates()

    def change_candidate_page_size() -> None:
        candidate_page["page_size"] = int(candidate_page_size_combo.currentData() or 100)
        candidate_page["page"] = 1
        refresh_candidates()

    def reset_candidate_filters() -> None:
        candidate_sort_combo.setCurrentIndex(0)
        candidate_score_filter_combo.setCurrentIndex(0)
        candidate_search_edit.clear()
        candidate_state_filter_keys.clear()
        candidate_state_filter_keys.update(key for key, _label in candidate_state_options)
        update_candidate_state_filter_button()
        candidate_page["page"] = 1
        refresh_candidates()

    def toggle_candidate_reject() -> None:
        candidate_id = selected_candidate_id()
        if not candidate_id:
            message("请先选择候选人。")
            return
        row = candidate_detail_row(candidate_id)
        if not row:
            message("未找到候选人。")
            return
        current_state = str(row["candidate_state"] or "active")
        next_state = "active" if current_state == "rejected" else "rejected"
        db.set_candidate_state(candidate_id, next_state)
        db.log(
            f"候选人状态已更新：{row['name'] or candidate_id} -> {next_state}",
            task_id=row["source_task_id"],
            account_id=row["source_account_id"],
            level="info",
            step="候选人状态",
        )
        refresh_candidates()

    def delete_selected_candidate() -> None:
        candidate_id = selected_candidate_id()
        if not candidate_id:
            message("请先选择候选人。")
            return
        row = candidate_detail_row(candidate_id)
        if not row:
            message("未找到候选人。")
            return
        display_name = row["name"] or f"候选人 {candidate_id}"
        ok = QMessageBox.question(
            window,
            "删除候选人",
            f"确认删除「{display_name}」的本地记录吗？\n\n关联的简历快照、评分结果和沟通日志也会一并删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        db.log(
            f"删除候选人：{display_name}",
            task_id=row["source_task_id"],
            account_id=row["source_account_id"],
            level="info",
            step="候选人删除",
            payload={
                "candidate_id": candidate_id,
                "job_id": row["job_id"],
                "profile_url": row["profile_url"] or "",
            },
        )
        db.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))
        if state.last_candidate_id == candidate_id:
            state.last_candidate_id = None
            state.last_resume_text = ""
            state.last_greeting = ""
            state.last_greeting_log_id = None
        refresh_candidates()
        refresh_logs()
        refresh_mode_status()

    def batch_set_candidate_state(next_state: str) -> None:
        candidate_ids = sorted(checked_candidate_ids())
        if not candidate_ids:
            message("请先勾选候选人。")
            return
        label = "淘汰" if next_state == "rejected" else "恢复"
        ok = QMessageBox.question(
            window,
            f"批量{label}",
            f"确认{label}已勾选的 {len(candidate_ids)} 位候选人吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        for candidate_id in candidate_ids:
            db.set_candidate_state(candidate_id, next_state)
        db.log(
            f"批量{label}候选人：{len(candidate_ids)} 位",
            level="info",
            step="候选人批量状态",
            payload={"candidate_ids": candidate_ids, "next_state": next_state},
        )
        refresh_candidates()
        refresh_logs()

    def batch_delete_candidates() -> None:
        candidate_ids = sorted(checked_candidate_ids())
        if not candidate_ids:
            message("请先勾选候选人。")
            return
        names: list[str] = []
        for candidate_id in candidate_ids[:8]:
            row = candidate_detail_row(candidate_id)
            names.append(str((row["name"] if row else "") or candidate_id))
        preview = "、".join(names) + ("..." if len(candidate_ids) > len(names) else "")
        ok = QMessageBox.question(
            window,
            "批量删除候选人",
            f"确认删除已勾选的 {len(candidate_ids)} 位候选人的本地记录吗？\n\n{preview}\n\n关联的简历快照、评分结果和沟通日志也会一并删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        placeholders = ",".join("?" for _ in candidate_ids)
        db.log(
            f"批量删除候选人：{len(candidate_ids)} 位",
            level="info",
            step="候选人批量删除",
            payload={"candidate_ids": candidate_ids},
        )
        db.execute(f"DELETE FROM candidates WHERE id IN ({placeholders})", candidate_ids)
        if state.last_candidate_id in set(candidate_ids):
            state.last_candidate_id = None
            state.last_resume_text = ""
            state.last_greeting = ""
            state.last_greeting_log_id = None
        refresh_candidates()
        refresh_logs()
        refresh_mode_status()

    def selected_candidate_id() -> int | None:
        return selected_id(candidates_table)

    def checked_candidate_ids() -> set[int]:
        ids: set[int] = set()
        for row_index in range(candidates_table.rowCount()):
            id_item = candidates_table.item(row_index, 0)
            check_item = candidates_table.item(row_index, 1)
            if not id_item or not check_item:
                continue
            if check_item.checkState() == Qt.CheckState.Checked:
                try:
                    ids.add(int(id_item.text()))
                except ValueError:
                    continue
        return ids

    def set_current_page_candidate_checked(checked: bool) -> None:
        state_value = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row_index in range(candidates_table.rowCount()):
            item = candidates_table.item(row_index, 1)
            if item:
                item.setCheckState(state_value)
        update_candidate_selection_bar()

    def update_candidate_selection_bar() -> None:
        count = len(checked_candidate_ids())
        candidate_selected_label.setText(f"已选 {count} 人")
        candidate_batch_bar.setVisible(count > 0)

    candidate_state_filter_btn.clicked.connect(open_candidate_state_filter_dialog)
    candidate_clear_filters_btn.clicked.connect(reset_candidate_filters)
    candidate_sort_combo.currentIndexChanged.connect(lambda _index: reset_candidate_page())
    candidate_score_filter_combo.currentIndexChanged.connect(lambda _index: reset_candidate_page())
    candidate_search_edit.textChanged.connect(lambda _text: reset_candidate_page())
    candidate_prev_page_btn.clicked.connect(lambda: change_candidate_page(-1))
    candidate_next_page_btn.clicked.connect(lambda: change_candidate_page(1))
    candidate_page_size_combo.currentIndexChanged.connect(lambda _index: change_candidate_page_size())
    candidate_reject_btn.clicked.connect(toggle_candidate_reject)
    candidate_delete_btn.clicked.connect(delete_selected_candidate)
    candidate_select_all_btn.clicked.connect(lambda: set_current_page_candidate_checked(True))
    candidate_clear_selection_btn.clicked.connect(lambda: set_current_page_candidate_checked(False))
    candidate_batch_reject_btn.clicked.connect(lambda: batch_set_candidate_state("rejected"))
    candidate_batch_restore_btn.clicked.connect(lambda: batch_set_candidate_state("active"))
    candidate_batch_delete_btn.clicked.connect(batch_delete_candidates)
    candidates_table.itemChanged.connect(lambda item: update_candidate_selection_bar() if item.column() == 1 else None)
    update_candidate_state_filter_button()

    def candidate_detail_row(candidate_id: int) -> Any | None:
        return db.fetch_one(
            """
            SELECT
                c.*,
                j.title AS job_title,
                a.name AS account_name,
                t.name AS task_name,
                rs.id AS snapshot_id,
                rs.url AS snapshot_url,
                rs.title AS snapshot_title,
                rs.text_length AS snapshot_text_length,
                rs.line_count AS snapshot_line_count,
                rs.matched_sections AS snapshot_sections,
                rs.project_total AS snapshot_project_total,
                rs.project_visible AS snapshot_project_visible,
                rs.has_attachment_resume AS snapshot_has_attachment,
                rs.has_unauthorized_attachment AS snapshot_has_unauthorized_attachment,
                rs.completeness AS snapshot_completeness,
                rs.warnings AS snapshot_warnings,
                rs.resume_text AS snapshot_text,
                rs.account_id AS snapshot_account_id,
                rs.created_at AS snapshot_at,
                sr.id AS score_id,
                sr.score AS latest_score,
                sr.matched_keywords AS latest_matched,
                sr.missing_keywords AS latest_missing,
                sr.risks AS latest_risks,
                sr.summary AS latest_score_summary,
                sr.created_at AS score_at,
                gl.id AS greeting_id,
                gl.message AS latest_greeting_message,
                gl.status AS latest_greeting_status,
                gl.dry_run AS latest_greeting_dry_run,
                gl.created_at AS greeting_at
            FROM candidates c
            JOIN jobs j ON j.id = c.job_id
            LEFT JOIN accounts a ON a.id = c.source_account_id
            LEFT JOIN tasks t ON t.id = c.source_task_id
            LEFT JOIN resume_snapshots rs ON rs.id = c.last_snapshot_id
            LEFT JOIN score_results sr ON sr.id = (
                SELECT id FROM score_results WHERE candidate_id = c.id ORDER BY id DESC LIMIT 1
            )
            LEFT JOIN greeting_logs gl ON gl.id = (
                SELECT id FROM greeting_logs WHERE candidate_id = c.id ORDER BY id DESC LIMIT 1
            )
            WHERE c.id = ?
            """,
            (candidate_id,),
        )

    def show_candidate_snapshot(candidate_id: int | None = None) -> None:
        candidate_id = candidate_id or selected_candidate_id()
        if not candidate_id:
            message("请先选择候选人。")
            return
        row = candidate_detail_row(candidate_id)
        if not row:
            message("未找到候选人。")
            return
        text = row["snapshot_text"] or ""
        if not text:
            message("这个候选人还没有简历全文快照。")
            return
        dialog = QDialog(window)
        dialog.setWindowTitle(f"简历全文 - {row['name'] or candidate_id}")
        dialog.resize(900, 720)
        layout = QVBoxLayout(dialog)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(text)
        layout.addWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def open_candidate_profile(candidate_id: int | None = None) -> None:
        candidate_id = candidate_id or selected_candidate_id()
        if not candidate_id:
            message("请先选择候选人。")
            return
        row = candidate_detail_row(candidate_id)
        if not row:
            message("未找到候选人。")
            return
        url = row["profile_url"] or row["snapshot_url"] or ""
        if not str(url).startswith("http"):
            message("这个候选人没有可打开的猎聘简历链接。")
            return
        account_id = row["source_account_id"] or row["snapshot_account_id"]
        opened = open_url_with_account_profile(
            str(url),
            "候选人详情",
            int(account_id) if account_id else None,
            log_reason="已从候选人清单打开猎聘简历",
        )
        if opened:
            append_log(["候选人来源校验", f"候选人：{row['name'] or candidate_id}", f"归属账号ID：{account_id}"])

    def show_candidate_detail(candidate_id: int | None = None) -> None:
        candidate_id = candidate_id or selected_candidate_id()
        if not candidate_id:
            message("请先选择候选人。")
            return
        row = candidate_detail_row(candidate_id)
        if not row:
            message("未找到候选人。")
            return
        sections = loads(row["snapshot_sections"] or "[]", [])
        warnings = loads(row["snapshot_warnings"] or "[]", [])
        matched = loads(row["latest_matched"] or row["matched_keywords"] or "[]", [])
        missing = loads(row["latest_missing"] or row["missing_keywords"] or "[]", [])
        risks = loads(row["latest_risks"] or row["risks"] or "[]", [])
        dialog = QDialog(window)
        dialog.setWindowTitle(f"候选人详情 - {row['name'] or candidate_id}")
        dialog.resize(820, 680)
        layout = QVBoxLayout(dialog)
        content = QTextEdit()
        content.setReadOnly(True)
        lines = [
            f"{row['name'] or '-'}    评分：{row['latest_score'] if row['latest_score'] is not None else (row['score'] if row['score'] is not None else '-')}",
            f"岗位：{row['job_title']}",
            f"账号：{row['account_name'] or '-'}    任务：{row['task_name'] or '-'}",
            f"状态：{_candidate_state_label(row)} / 简历 {_status_label(row['resume_status'])} / 沟通 {_status_label(row['latest_greeting_status'] or row['greeting_status'])}",
            f"更新时间：{row['updated_at']}",
            "",
            "简历摘要",
            f"完整性：{row['snapshot_completeness'] or '-'}",
            f"文本：{row['snapshot_text_length'] or '-'} 字 / {row['snapshot_line_count'] or '-'} 行",
            f"模块：{'，'.join(sections) or '-'}",
            f"项目经历：{row['snapshot_project_visible'] or 0}/{row['snapshot_project_total'] or '未知'}",
            f"附件风险：{'需索要/授权' if row['snapshot_has_unauthorized_attachment'] else ('有附件' if row['snapshot_has_attachment'] else '-')}",
            f"抓取风险：{'；'.join(warnings) if warnings else '-'}",
            "",
            "评分报告",
            f"分数：{row['latest_score'] if row['latest_score'] is not None else '-'}",
            f"摘要：{row['latest_score_summary'] or row['score_summary'] or '-'}",
            f"匹配：{'，'.join(matched) or '-'}",
            f"缺失：{'，'.join(missing) or '-'}",
            f"风险：{'；'.join(risks) or '-'}",
            "",
            "沟通记录",
            f"状态：{_status_label(row['latest_greeting_status'] or row['greeting_status'])}",
            f"Dry-run：{'是' if row['latest_greeting_dry_run'] else '否'}",
            f"时间：{row['greeting_at'] or '-'}",
            "话术：",
            row["latest_greeting_message"] or row["greeting"] or "-",
            "",
            "简历预览",
            _compact_text(row["snapshot_text"] or "", 700) or "-",
        ]
        content.setPlainText("\n".join(lines))
        layout.addWidget(content)
        button_row = QHBoxLayout()
        open_btn = QPushButton("打开猎聘简历")
        snapshot_btn = QPushButton("查看全文快照")
        close_btn = QPushButton("关闭")
        button_row.addWidget(open_btn)
        button_row.addWidget(snapshot_btn)
        button_row.addStretch(1)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)
        open_btn.clicked.connect(lambda: open_candidate_profile(candidate_id))
        snapshot_btn.clicked.connect(lambda: show_candidate_snapshot(candidate_id))
        close_btn.clicked.connect(dialog.reject)
        dialog.exec()

    candidate_detail_btn.clicked.connect(lambda: show_candidate_detail())
    candidate_open_profile_btn.clicked.connect(lambda: open_candidate_profile())
    candidate_snapshot_btn.clicked.connect(lambda: show_candidate_snapshot())
    candidate_refresh_btn.clicked.connect(refresh_candidates)
    candidates_table.cellDoubleClicked.connect(lambda _row, _col: show_candidate_detail())

    # Alerts/logs tabs
    alert_tab = QWidget()
    alert_layout = QVBoxLayout(alert_tab)
    resolve_alert_btn = QPushButton("关闭选中提醒")
    alert_layout.addWidget(resolve_alert_btn)
    alert_layout.addWidget(alerts_table)
    tabs.addTab(alert_tab, "提醒")

    def resolve_alert() -> None:
        alert_id = selected_id(alerts_table)
        if not alert_id:
            return
        db.execute("UPDATE alerts SET status = 'resolved', resolved_at = datetime('now') WHERE id = ?", (alert_id,))
        refresh_alerts()

    resolve_alert_btn.clicked.connect(resolve_alert)

    logs_tab = QWidget()
    logs_layout = QVBoxLayout(logs_tab)
    refresh_logs_btn = QPushButton("刷新日志")
    logs_layout.addWidget(refresh_logs_btn)
    logs_layout.addWidget(logs_table)
    tabs.addTab(logs_tab, "日志")
    refresh_logs_btn.clicked.connect(refresh_logs)

    # Browser panel
    browser_panel = QWidget()
    browser_layout = QVBoxLayout(browser_panel)
    browser_layout.setContentsMargins(0, 0, 0, 0)
    context_form = QHBoxLayout()
    context_form.addWidget(QLabel("当前账号"))
    context_form.addWidget(account_combo)
    context_form.addWidget(QLabel("当前岗位"))
    context_form.addWidget(job_combo)
    use_context_btn = QPushButton("应用上下文")
    context_form.addWidget(use_context_btn)
    browser_buttons = QHBoxLayout()
    open_search_btn = QPushButton("打开找人页")
    fill_search_btn = QPushButton("填入搜索词")
    collect_cards_btn = QPushButton("抓取列表候选人")
    analyze_resume_btn = QPushButton("抓当前简历")
    score_resume_btn = QPushButton("评分/生成话术")
    view_resume_btn = QPushButton("查看抓取全文")
    greet_btn = QPushButton("填充/发送问候")
    for btn in (open_search_btn, fill_search_btn, collect_cards_btn, analyze_resume_btn, score_resume_btn, view_resume_btn, greet_btn):
        browser_buttons.addWidget(btn)

    route_box = QGroupBox("单路线验证")
    route_layout = QGridLayout(route_box)
    route_password = QLineEdit()
    route_password.setEchoMode(QLineEdit.Password)
    route_password.setPlaceholderText("本次登录密码，不保存")
    route_fill_login_btn = QPushButton("1 填账号密码")
    route_check_login_btn = QPushButton("检查登录状态")
    route_sync_job_btn = QPushButton("2 维护JD条件")
    route_city_test_btn = QPushButton("3 城市单测（不搜索）")
    route_search_btn = QPushButton("3 填条件（不搜索）")
    route_submit_search_btn = QPushButton("3.1 确认后点搜索")
    route_next_page_test_btn = QPushButton("3.2 测试下一页")
    route_hide_viewed_test_btn = QPushButton("3.3 隐藏已查看")
    route_hide_contacted_test_btn = QPushButton("3.4 隐藏已沟通")
    route_hide_contact_test_btn = QPushButton("3.5 隐藏已获取联系方式")
    route_open_first_btn = QPushButton("4 打开当前候选人")
    route_next_candidate_btn = QPushButton("4.1 关闭并打开下一位")
    route_extract_btn = QPushButton("5 抓当前简历")
    route_record_start_btn = QPushButton("开始录制网页操作")
    route_record_stop_btn = QPushButton("结束录制并保存")
    route_layout.addWidget(QLabel("密码"), 0, 0)
    route_layout.addWidget(route_password, 0, 1)
    route_layout.addWidget(route_fill_login_btn, 0, 2)
    route_layout.addWidget(route_check_login_btn, 0, 3)
    route_layout.addWidget(route_sync_job_btn, 0, 4)
    route_layout.addWidget(route_city_test_btn, 1, 0)
    route_layout.addWidget(route_search_btn, 1, 1)
    route_layout.addWidget(route_submit_search_btn, 1, 2)
    route_layout.addWidget(route_next_page_test_btn, 1, 3)
    route_layout.addWidget(route_open_first_btn, 1, 4)
    route_layout.addWidget(route_next_candidate_btn, 1, 5)
    route_layout.addWidget(route_extract_btn, 2, 0)
    route_layout.addWidget(route_hide_viewed_test_btn, 2, 1)
    route_layout.addWidget(route_hide_contacted_test_btn, 2, 2)
    route_layout.addWidget(route_hide_contact_test_btn, 2, 3)
    record_layout = QHBoxLayout()
    record_layout.addWidget(route_record_start_btn)
    record_layout.addWidget(route_record_stop_btn)
    lower = QSplitter()
    lower.addWidget(result)
    lower.addWidget(process_log)
    debug_dialog = QDialog(window)
    debug_dialog.setWindowTitle("调试控制台")
    debug_dialog.resize(1180, 760)
    debug_layout = QVBoxLayout(debug_dialog)
    debug_layout.addLayout(context_form)
    debug_layout.addWidget(route_box)
    debug_layout.addLayout(record_layout)
    debug_layout.addLayout(browser_buttons)
    debug_layout.addWidget(lower, stretch=1)
    browser_layout.addWidget(web, stretch=1)

    def apply_context() -> None:
        state.current_job_id = job_combo.currentData()
        state.current_task_id = None
        state.current_task_queue_mode = False
        state.current_task_min_score = None
        state.current_task_auto_greet = None
        state.current_task_dry_run = None
        state.current_task_use_ai_scoring = None
        state.pending_task_apply_id = None
        use_account(account_combo.currentData())
        append_log(
            [
                "上下文已应用",
                f"账号 ID：{state.current_account_id or '-'}",
                f"岗位 ID：{state.current_job_id or '-'}",
            ]
        )

    use_context_btn.clicked.connect(apply_context)
    open_search_btn.clicked.connect(open_search_page)

    def check_login_status(show_popup: bool = False) -> None:
        account_id = state.current_account_id or account_combo.currentData()
        row = db.fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,)) if account_id else None

        def handle(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            current_url = payload.get("url") or web.url().toString()
            status = payload.get("status") or "unknown"
            if status == "unknown":
                if re.search(r"/account/login|passport|login", current_url, re.I):
                    status = "needs_verification" if payload.get("needsVerification") else "needs_login"
                elif re.search(r"/search/getConditionItem", current_url, re.I):
                    status = "logged_in"
            status_label = {
                "logged_in": "已登录",
                "needs_login": "需要登录",
                "needs_verification": "需要人工验证",
                "unknown": "未知",
            }.get(status, status)
            details = [
                "登录状态检查完成",
                f"状态：{status_label}",
                f"URL：{current_url}",
                f"登录页：{'是' if payload.get('isLoginUrl') or payload.get('hasLoginForm') else '否'}",
                f"找人页：{'是' if payload.get('hasSearchPage') else '否'}",
                f"需要验证：{'是' if payload.get('needsVerification') else '否'}",
                f"页面文档数：{payload.get('documentCount') or '-'}",
            ]
            append_log(details)
            if row and status in {"logged_in", "needs_login", "needs_verification"}:
                db.execute(
                    "UPDATE accounts SET status = ?, updated_at = datetime('now') WHERE id = ?",
                    (status, int(row["id"])),
                )
                refresh_all()
            task_status = _task_status(state, state.current_task_id)
            if (
                state.current_task_id
                and task_status == TaskStatus.RUNNING.value
                and status in {"needs_login", "needs_verification"}
                and not _task_is_paused(state, state.current_task_id)
            ):
                save_task_checkpoint(
                    state.current_task_id,
                    "open_search",
                    TaskStep.CHECK_LOGIN,
                    {"login_status": status, "url": current_url},
                )
                state.engine.pause_for_user(
                    int(state.current_task_id),
                    HumanIntervention(
                        reason="账号需要人工登录验证" if status == "needs_verification" else "账号需要重新登录",
                        detail=f"当前 URL：{current_url}",
                        action_hint="请在右侧完成登录/验证码后，点击“继续当前任务”。",
                        severity="warning",
                    ),
                )
                refresh_all()
            if show_popup:
                if status == "logged_in":
                    message("登录状态已确认：已进入猎聘找人环境。")
                elif status == "needs_verification":
                    message("当前仍需要人工完成验证码/短信/滑块验证。")
                elif status == "needs_login":
                    message("当前仍在登录页，请先填账号密码并登录。")
                else:
                    message("暂时无法判断登录状态，请看过程日志。")

        page_adapter.check_login_status(handle)

    def fill_login_for_current_account() -> None:
        account_id = account_combo.currentData()
        row = db.fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,)) if account_id else None
        if not row:
            message("请先选择账号。")
            return
        password = route_password.text() or row["password"]
        if not password:
            message("请先在账号页维护密码，或在顶部临时密码框填写本次密码。")
            return

        def handle(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            append_log(
                [
                    "已填账号密码，等待人工登录",
                    f"账号：{row['name']}",
                    f"切到密码登录：{'是' if payload.get('switchedPasswordLogin') else '否'}",
                    f"密码模式已就绪：{'是' if payload.get('passwordModeReady') else '否'}",
                    f"账号已填：{'是' if payload.get('filledUsername') else '否'}",
                    f"密码已填：{'是' if payload.get('filledPassword') else '否'}",
                    f"找到登录按钮：{'是' if payload.get('foundLoginButton') else '否'}",
                    f"已点击登录：{'是' if payload.get('clickedLogin') else '否'}",
                    f"页面文档数：{payload.get('documentCount') or '-'}，输入框数：{payload.get('inputCount') or '-'}",
                    f"说明：{payload.get('reason') or '-'}",
                    "请在页面里点击登录，并完成滑块/短信验证。完成后点“检查登录状态”。",
                ]
            )
            message("账号密码已填好。请你在右侧猎聘页面点击登录，并完成滑块/短信验证。")
            QTimer.singleShot(800, lambda: check_login_status(False))

        def run_fill() -> None:
            page_adapter.fill_login(row["username"], password, False, handle)

        def handle_switch(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            append_log(
                [
                    "密码登录切换检查完成",
                    f"找到密码登录：{'是' if payload.get('foundPasswordTab') else '否'}",
                    f"已点击密码登录：{'是' if payload.get('clickedPasswordTab') else '否'}",
                    f"点击前激活：{payload.get('beforeActiveText') or '-'}",
                    f"页面文档数：{payload.get('documentCount') or '-'}，输入框：{payload.get('inputPlaceholders') or '-'}",
                ]
            )
            QTimer.singleShot(500, run_fill)

        page_adapter.switch_password_login(handle_switch)

    def sync_route_job_from_jd(job_id: int | None = None) -> None:
        row = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (int(job_id),)) if job_id else route_job_row()
        if not row:
            title = job_title.text().strip()
            jd = job_jd.toPlainText().strip()
            if not title or not jd:
                message("请先在岗位页填写岗位名称和 JD，或选择已有岗位。")
                return
            append_log(["开始解析 JD 搜索条件", f"岗位：{title}", f"模型：{state.env.qwen_model if state.env.qwen_api_key else '本地规则回退'}"])
            parse_result = parse_jd_with_qwen(title, jd, state.env)
            fields = draft_to_job_fields(parse_result.draft)
            search_conditions = parse_result.draft.model_dump(mode="json")
            job_id = db.add_job(
                title=title,
                jd=jd,
                keywords=fields["keywords"],
                must_have=fields["must_have"],
                nice_to_have=fields["nice_to_have"],
                reject_keywords=fields["reject_keywords"],
                city=fields["city"],
                experience=fields["experience"],
                education=fields["education"],
                search_conditions=search_conditions,
            )
            db.log("单路线维护JD岗位", payload={"job_id": job_id, "search_conditions": search_conditions, "draft": parse_result.draft.model_dump()})
        else:
            title = row["title"] or ""
            jd = row["jd"] or ""
            append_log(["开始解析 JD 搜索条件", f"岗位：{title}", f"模型：{state.env.qwen_model if state.env.qwen_api_key else '本地规则回退'}"])
            parse_result = parse_jd_with_qwen(title, jd, state.env)
            fields = draft_to_job_fields(parse_result.draft)
            search_conditions = parse_result.draft.model_dump(mode="json")
            db.execute(
                """
                UPDATE jobs
                SET keywords = ?, must_have = ?, nice_to_have = ?, reject_keywords = ?,
                    city = ?, experience = ?, education = ?, search_conditions = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    json.dumps(fields["keywords"], ensure_ascii=False),
                    json.dumps(fields["must_have"], ensure_ascii=False),
                    json.dumps(fields["nice_to_have"], ensure_ascii=False),
                    json.dumps(fields["reject_keywords"], ensure_ascii=False),
                    fields["city"],
                    fields["experience"],
                    fields["education"],
                    json.dumps(search_conditions, ensure_ascii=False),
                    int(row["id"]),
                ),
            )
            db.log("单路线维护JD岗位", payload={"job_id": int(row["id"]), "search_conditions": search_conditions, "draft": parse_result.draft.model_dump()})
        refresh_all()
        row = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (int(job_id),)) if job_id else route_job_row()
        preview_lines = draft_preview_lines(parse_result.draft, parse_result.source, parse_result.error)
        result.setPlainText("\n".join(["当前单路线岗位配置", f"岗位：{row['title'] if row else '-'}", ""] + preview_lines))
        append_log(
            [
                "JD 条件维护完成",
                f"来源：{'千问' if parse_result.source == 'qwen' else '本地规则回退'}",
                f"搜索词：{' '.join(fields['keywords'])}",
                f"经验：{fields['experience'] or '-'}；学历：{fields['education'] or '-'}；城市：{fields['city'] or '-'}",
            ]
        )

    def route_fill_search_and_submit(
        city_only: bool = False,
        *,
        auto_submit: bool = False,
        trigger_task_id: int | None = None,
        task_mode: bool = False,
    ) -> None:
        active_task_id = trigger_task_id or state.current_task_id
        if _task_automation_blocked(state, active_task_id):
            append_log(["已跳过写条件", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
            return
        current_url = web.url().toString()
        if not re.search(r"/search/getConditionItem", current_url, re.I):
            append_log(
                [
                    "当前不在猎聘找人筛选页，已阻止填条件动作",
                    f"当前 URL：{current_url or '-'}",
                    "请先点击“打开找人页”进入 https://h.liepin.com/search/getConditionItem 再重试。",
                ]
            )
            message("请先进入猎聘找人筛选页，再执行“填条件”。")
            return
        row = None
        if task_mode and active_task_id:
            task_job = db.fetch_one(
                """
                SELECT j.*
                FROM tasks t
                JOIN jobs j ON j.id = t.job_id
                WHERE t.id = ?
                """,
                (int(active_task_id),),
            )
            row = task_job
            if row:
                state.current_job_id = int(row["id"])
                idx = job_combo.findData(int(row["id"]))
                if idx >= 0:
                    job_combo.setCurrentIndex(idx)
        if not row:
            row = route_job_row()
        if not row:
            message("请先维护岗位 JD。")
            return
        raw_search_conditions = loads(row["search_conditions"], {}) if "search_conditions" in row.keys() else {}
        if not isinstance(raw_search_conditions, dict) or not raw_search_conditions:
            append_log(["岗位尚未生成猎聘搜索条件，先执行“2 维护JD条件”自动解析。"])
            sync_route_job_from_jd(int(row["id"]))
            row = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (int(row["id"]),))
            if not row:
                message("岗位解析后未找到可用岗位配置。")
                return
        search_conditions = row_search_conditions(row)
        keywords = _condition_list(search_conditions.get("keywords") or loads(row["keywords"], []))
        if not keywords:
            sync_route_job_from_jd(int(row["id"]))
            row = db.fetch_one("SELECT * FROM jobs WHERE id = ?", (int(row["id"]),))
            search_conditions = row_search_conditions(row) if row else {}
            keywords = _condition_list(search_conditions.get("keywords") or (loads(row["keywords"], []) if row else []))
        task_age_min = None
        task_age_max = None
        if task_mode and active_task_id:
            task_row = db.fetch_one("SELECT age_min, age_max FROM tasks WHERE id = ?", (int(active_task_id),))
            if task_row:
                task_age_min = task_row["age_min"]
                task_age_max = task_row["age_max"]
                if task_age_min and task_age_max and int(task_age_min) > int(task_age_max):
                    task_age_min, task_age_max = task_age_max, task_age_min
        payload = {
            "keyword_match": _condition_text(search_conditions.get("keyword_match") or "包含任意关键词") if keywords else "",
            "keywords": keywords,
            "position_keywords": _condition_list(search_conditions.get("position_keywords")),
            "company_keywords": _condition_list(search_conditions.get("company_keywords")),
            "current_city": _condition_list(search_conditions.get("current_city")),
            "expected_city": _condition_list(search_conditions.get("expected_city")),
            "work_years": _condition_list(search_conditions.get("work_years")),
            "education": _condition_list(search_conditions.get("education")),
            "recruit_type": _non_neutral_choice(
                search_conditions.get("recruit_type"),
                {RECRUIT_TYPE_OPTIONS[0], "不限", "统招不限", "统招/非统招不限"},
            ),
            "school_tags": [item for item in _condition_list(search_conditions.get("school_tags")) if item != "不限"],
            "current_industry": _condition_list(search_conditions.get("current_industry")),
            "current_position": _condition_list(search_conditions.get("current_position")),
            "age_min": task_age_min,
            "age_max": task_age_max,
            "active_days": _non_neutral_choice(search_conditions.get("active_days"), {ACTIVE_OPTIONS[0], "活跃度（不限）"}),
            "gender": _non_neutral_choice(search_conditions.get("gender"), {GENDER_OPTIONS[0], "性别（不限）"}),
            "job_hopping": _non_neutral_choice(search_conditions.get("job_hopping"), {JOB_HOPPING_OPTIONS[0], "不限"}),
            "languages": [item for item in _condition_list(search_conditions.get("languages")) if item != "不限"],
            "expected_industry": _condition_list(search_conditions.get("expected_industry")),
            "expected_position": _condition_list(search_conditions.get("expected_position")),
            "schools": _condition_list(search_conditions.get("schools")),
            "majors": _condition_list(search_conditions.get("majors")),
            "job_status": _condition_list(search_conditions.get("job_status")),
            "resume_language": _non_neutral_choice(search_conditions.get("resume_language"), {RESUME_LANGUAGE_OPTIONS[0], "不限"}),
            "click_search": False,
            "step_delay_ms": 2000,
        }
        validation_keys = [
            "keyword_match",
            "keywords",
            "position_keywords",
            "company_keywords",
            "current_city",
            "expected_city",
            "work_years",
            "education",
            "recruit_type",
            "school_tags",
            "current_industry",
            "current_position",
            "age_min",
            "age_max",
            "active_days",
            "gender",
            "job_hopping",
            "languages",
            "expected_industry",
            "expected_position",
            "schools",
            "majors",
            "job_status",
            "resume_language",
        ]

        def has_target_value(key: str) -> bool:
            value = payload.get(key)
            if isinstance(value, list):
                return bool([item for item in value if str(item).strip()])
            return bool(str(value or "").strip())

        def display_payload_value(key: str) -> str:
            value = payload.get(key)
            if isinstance(value, list):
                return _join_values(value) or "-"
            return str(value or "-")

        if city_only:
            payload.update(
                {
                    "keyword_match": "",
                    "keywords": [],
                    "position_keywords": [],
                    "company_keywords": [],
                    "work_years": [],
                    "education": [],
                    "recruit_type": "",
                    "school_tags": [],
                    "current_industry": [],
                    "current_position": [],
                    "age_min": None,
                    "age_max": None,
                    "active_days": "",
                    "gender": "",
                    "job_hopping": "",
                    "languages": [],
                    "expected_industry": [],
                    "expected_position": [],
                    "schools": [],
                    "majors": [],
                    "job_status": [],
                    "resume_language": "",
                    "step_delay_ms": 1200,
                }
            )
        elif task_mode:
            payload["step_delay_ms"] = TASK_CONDITION_STEP_DELAY_MS
        target_keys = [key for key in validation_keys if has_target_value(key)]
        if task_mode and active_task_id:
            save_task_checkpoint(
                active_task_id,
                "apply_filters",
                TaskStep.APPLY_FILTERS,
                {"target_keys": target_keys, "auto_submit": bool(auto_submit), "city_only": bool(city_only)},
            )
        payload_preview_lines: list[str] = []
        for key in (
            "keyword_match",
            "keywords",
            "position_keywords",
            "company_keywords",
            "current_city",
            "expected_city",
            "work_years",
            "education",
            "recruit_type",
            "school_tags",
            "current_industry",
            "current_position",
            "age_min",
            "age_max",
            "active_days",
            "gender",
            "job_hopping",
            "languages",
            "expected_industry",
            "expected_position",
            "schools",
            "majors",
            "job_status",
            "resume_language",
            "step_delay_ms",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                text = _join_values(value) or "-"
            else:
                text = str(value or "-")
            payload_preview_lines.append(f"传参[{key}]：{text}")
        append_log(
            [
                (
                    "开始写入猎聘查询条件（写完将自动搜索）"
                    if auto_submit and not city_only
                    else ("开始写入猎聘查询条件（不触发搜索）" if not city_only else "开始城市单测写入（不触发搜索）")
                ),
                f"搜索词：{' '.join(payload['keywords']) or row['title']}",
                f"职位关键词：{_join_values(payload['position_keywords']) or '-'}",
                f"城市：目前[{_join_values(payload['current_city']) or '-'}] / 期望[{_join_values(payload['expected_city']) or '-'}]",
                f"行业：当前[{_join_values(payload['current_industry']) or '-'}] / 期望[{_join_values(payload['expected_industry']) or '-'}]",
                f"职位：当前[{_join_values(payload['current_position']) or '-'}] / 期望[{_join_values(payload['expected_position']) or '-'}]",
                f"年龄：{payload.get('age_min') or '-'}-{payload.get('age_max') or '-'}",
                f"写入节奏：每个条件间隔 {int(payload.get('step_delay_ms') or 0)}ms",
                "本次默认传参如下：",
                *payload_preview_lines,
            ]
        )

        def handle_apply(payload_result: dict[str, Any] | None) -> None:
            if _task_automation_blocked(state, active_task_id):
                append_log(["已跳过条件写入结果处理", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
                return
            payload_result = payload_result or {}
            raw_payload = payload_result if isinstance(payload_result, dict) else {"raw": str(payload_result)}
            applied = payload_result.get("applied") if isinstance(payload_result.get("applied"), dict) else {}
            skipped = payload_result.get("skipped") if isinstance(payload_result.get("skipped"), dict) else {}
            group_results = payload_result.get("groupResults") if isinstance(payload_result.get("groupResults"), dict) else {}
            verify_payload = payload_result.get("verify") if isinstance(payload_result.get("verify"), dict) else {}
            row_tag_snapshots = verify_payload.get("rowTagSnapshots") if isinstance(verify_payload.get("rowTagSnapshots"), dict) else {}
            row_inspectors = verify_payload.get("rowInspectors") if isinstance(verify_payload.get("rowInspectors"), dict) else {}
            step_trace = verify_payload.get("stepTrace") if isinstance(verify_payload.get("stepTrace"), list) else []
            applied_keys = "、".join(applied.keys()) or "-"
            skipped_summary = "；".join(f"{k}:{v}" for k, v in skipped.items()) or "-"
            error_text = payload_result.get("error") or "-"
            stack_text = payload_result.get("stack") or "-"
            error_stage = payload_result.get("stage") or "-"
            applied_count = len(applied)
            has_apply_error = bool(payload_result.get("error") or payload_result.get("stage"))
            condition_validation = decide_condition_apply_validation(
                target_keys=target_keys,
                applied_keys=list(applied.keys()),
                has_apply_error=has_apply_error,
                blocking_keys=TASK_BLOCKING_CONDITION_KEYS,
            )
            failed_target_keys = condition_validation.failed_target_keys
            passed_target_keys = condition_validation.passed_target_keys
            blocking_failed_target_keys = condition_validation.blocking_failed_target_keys
            soft_failed_target_keys = condition_validation.soft_failed_target_keys
            validation_passed = condition_validation.validation_passed
            validation_status = condition_validation.validation_status
            group_lines: list[str] = []
            for group_name, group_payload in group_results.items():
                if not isinstance(group_payload, dict):
                    continue
                group_applied = group_payload.get("applied") or []
                group_skipped = group_payload.get("skipped") or []
                total = int(group_payload.get("total") or 0)
                group_lines.append(
                    f"分组[{group_name}]：成功{len(group_applied)}/{total or (len(group_applied) + len(group_skipped))}，"
                    f"成功项[{_join_values(group_applied) or '-'}]，跳过项[{_join_values(group_skipped) or '-'}]"
                )
            row_snapshot_lines: list[str] = []
            for key in ("work_years", "education", "current_city", "expected_city", "school_tags", "languages"):
                snapshot = row_tag_snapshots.get(key)
                if not isinstance(snapshot, dict):
                    continue
                active_values = _join_values(snapshot.get("active")) or "-"
                row_snapshot_lines.append(
                    f"行快照[{key}]：找到行={'是' if snapshot.get('found') else '否'}；激活值[{active_values}]"
                )
            row_inspector_lines: list[str] = []
            for key in ("age", "expected_city", "school_tags", "languages", "expected_position"):
                inspector = row_inspectors.get(key)
                if not isinstance(inspector, dict):
                    continue
                options = _join_values(inspector.get("options")) or "-"
                inputs = inspector.get("inputs") if isinstance(inspector.get("inputs"), list) else []
                input_summary = "；".join(
                    f"id={item.get('id') or '-'}|name={item.get('name') or '-'}|placeholder={item.get('placeholder') or '-'}"
                    for item in inputs[:4]
                    if isinstance(item, dict)
                ) or "-"
                row_inspector_lines.append(
                    f"行结构[{key}]：找到行={'是' if inspector.get('found') else '否'}；行文本[{str(inspector.get('rowText') or '')[:120]}]；候选项[{options}]；输入框[{input_summary}]"
                )
            modal_logs = verify_payload.get("modalLogs") if isinstance(verify_payload.get("modalLogs"), dict) else {}
            modal_log_lines: list[str] = []
            for key, logs in modal_logs.items():
                if not isinstance(logs, list):
                    continue
                modal_log_lines.append(f"弹窗轨迹[{key}]：{_join_values(logs) or '-'}")
            step_trace_lines: list[str] = []
            for item in step_trace[:30]:
                if not isinstance(item, dict):
                    continue
                step_trace_lines.append(
                    f"写入轨迹#{item.get('index') or '-'}[{item.get('field') or '-'}]："
                    f"{item.get('status') or '-'}；耗时{item.get('durationMs') or '-'}ms；"
                        f"详情[{str(item.get('detail') or '-')[:120]}]"
                    )
            validation_lines = [
                "猎聘查询条件验收报告",
                f"状态：{validation_status}",
                f"目标字段数：{len(target_keys)}",
                f"通过字段：{_join_values(passed_target_keys) or '-'}",
                f"失败字段：{_join_values(failed_target_keys) or '-'}",
                "",
                "目标传参：",
            ]
            validation_lines.extend(f"{key}: {display_payload_value(key)}" for key in target_keys)
            validation_lines.extend(
                [
                    "",
                    "脚本状态：",
                    f"错误：{payload_result.get('error') or '-'}",
                    f"阶段：{payload_result.get('stage') or '-'}",
                    f"搜索按钮可见：{'是' if payload_result.get('searchButtonFound') else '否'}",
                ]
            )
            result.setPlainText("\n".join(validation_lines))
            append_log(
                [
                    "猎聘查询条件写入完成",
                    f"原始回调：{json.dumps(raw_payload, ensure_ascii=False)[:500]}",
                    f"验收状态：{validation_status}",
                    f"目标字段数：{len(target_keys)}；通过：{len(passed_target_keys)}；失败：{len(failed_target_keys)}",
                    f"验收失败字段：{_join_values(failed_target_keys) or '-'}",
                    f"成功字段：{applied_keys}",
                    f"跳过字段：{skipped_summary}",
                    f"脚本错误：{error_text}",
                    f"错误阶段：{error_stage}",
                    f"错误堆栈：{stack_text if stack_text != '-' else '-'}",
                    f"检测到搜索按钮：{'是' if payload_result.get('searchButtonFound') else '否'}",
                    f"搜索按钮点击：{'是' if payload_result.get('clickedSearch') else '否'}",
                    f"关键词输入框：{verify_payload.get('keywordInputValue') or '-'}",
                    f"职位输入框：{verify_payload.get('positionInputValue') or '-'}",
                    f"公司输入框：{verify_payload.get('companyInputValue') or '-'}",
                    f"当前职位输入框：{verify_payload.get('currentPositionInputValue') or '-'}",
                    f"筛选项行数：{verify_payload.get('searchItemCountBefore') or '-'} -> {verify_payload.get('searchItemCountAfter') or '-'}",
                    f"更多筛选点击次数：{verify_payload.get('expandToggleClicked') or '0'}",
                    f"可见条件行：{'、'.join((verify_payload.get('visibleRowTitles') or [])[:12]) or '-'}",
                    f"逐项写入轨迹条数：{len(step_trace)}",
                    *step_trace_lines,
                    *row_snapshot_lines,
                    *row_inspector_lines,
                    *modal_log_lines,
                    *group_lines,
                    (
                        "任务模式：将自动点击搜索。"
                        if auto_submit and not city_only
                        else "请先人工确认页面条件是否符合预期，再点击“3.1 确认后点搜索”。"
                    ),
                ]
            )
            if active_task_id and task_mode:
                state.engine.set_step(
                    active_task_id,
                    TaskStep.APPLY_FILTERS,
                    "任务模式：条件写入验收结果",
                    payload={
                        "validation_status": validation_status,
                        "validation_passed": validation_passed,
                        "target_keys": target_keys,
                        "passed_target_keys": passed_target_keys,
                        "failed_target_keys": failed_target_keys,
                        "blocking_failed_target_keys": blocking_failed_target_keys,
                        "soft_failed_target_keys": soft_failed_target_keys,
                        "applied_count": applied_count,
                        "skipped_count": len(skipped),
                        "error": payload_result.get("error") or "",
                        "stage": payload_result.get("stage") or "",
                        "search_button_found": bool(payload_result.get("searchButtonFound")),
                    },
                )
                if has_apply_error or not validation_passed:
                    issue_reason = "条件写入/验收存在异常，已继续任务" if has_apply_error else "条件验收未通过，已继续任务"
                    issue_detail = (
                        f"状态：{validation_status}；通过字段：{len(passed_target_keys)} / {len(target_keys)}；"
                        f"失败字段：{_join_values(failed_target_keys) or '-'}；"
                        f"阻断字段：{_join_values(blocking_failed_target_keys) or ('未写入任何条件' if applied_count == 0 else '-')}; "
                        f"错误：{payload_result.get('error') or '-'}；阶段：{payload_result.get('stage') or '-'}。"
                    )
                    append_log(
                        [
                            "任务模式：查询条件验收问题已记录，继续执行",
                            f"状态：{validation_status}",
                            f"目标字段：{len(target_keys)} 个",
                            f"通过字段：{len(passed_target_keys)} 个",
                            f"失败字段：{_join_values(failed_target_keys) or '-'}",
                            f"阻断字段：{_join_values(blocking_failed_target_keys) or ('未写入任何条件' if applied_count == 0 else '-')}",
                            f"脚本错误：{payload_result.get('error') or '-'}",
                            "按当前策略不再中断任务，系统将继续点击搜索。",
                        ]
                    )
                    task_notice_without_pause(
                        active_task_id,
                        issue_reason,
                        issue_detail,
                        "系统已继续搜索；如结果偏差较大，请事后根据日志调整岗位查询条件或页面控件适配。",
                        severity="warning",
                        step=TaskStep.APPLY_FILTERS,
                        payload={
                            "passed_target_keys": passed_target_keys,
                            "failed_target_keys": failed_target_keys,
                            "blocking_failed_target_keys": blocking_failed_target_keys,
                            "soft_failed_target_keys": soft_failed_target_keys,
                            "error": payload_result.get("error") or "",
                            "stage": payload_result.get("stage") or "",
                            "continued": True,
                        },
                    )
                if auto_submit and not city_only:
                    state.engine.set_step(active_task_id, TaskStep.COLLECT_CARDS, "任务模式：条件写入完成，自动点击搜索")
                    save_task_checkpoint(active_task_id, "click_search", TaskStep.COLLECT_CARDS, {"from": "apply_filters"})
                    refresh_all()
                    QTimer.singleShot(
                        _random_delay_ms(2200, 4200),
                        lambda task_id=active_task_id: route_click_search_only(
                            show_popup_on_fail=False,
                            trigger_task_id=task_id,
                            max_attempts=1,
                        ),
                    )
                    return
                task_notice_without_pause(
                    active_task_id,
                    "条件写入完成但未配置自动搜索",
                    "所有目标查询条件已通过脚本验收；当前调用未开启 auto_submit，因此系统不再转人工暂停。",
                    "如需自动跑完整链路，请从任务执行入口启动；调试时可手动点击搜索。",
                    severity="info",
                    step=TaskStep.APPLY_FILTERS,
                )
                refresh_all()
                return
            if auto_submit and not city_only:
                if trigger_task_id and state.current_task_id != trigger_task_id:
                    append_log(["任务自动搜索已跳过：当前任务已切换", f"预期任务：{trigger_task_id}，当前任务：{state.current_task_id or '-'}"])
                    return
                if has_apply_error or applied_count == 0:
                    append_log(
                        [
                            "任务自动搜索遇到条件写入/验收问题，已记录并继续执行",
                            f"错误：{payload_result.get('error') or '-'}",
                            f"阶段：{payload_result.get('stage') or '-'}",
                            f"成功字段数：{applied_count}",
                            "按当前策略不再中断任务，系统将继续点击搜索。",
                        ]
                    )
                    if active_task_id:
                        task_notice_without_pause(
                            active_task_id,
                            "条件写入/验收存在异常，已继续任务",
                            "系统在写入查询条件时出现异常或未写入任何字段。",
                            "系统已继续搜索；请事后查看日志中的条件写入报告。",
                            step=TaskStep.APPLY_FILTERS,
                            payload={
                                "error": payload_result.get("error") or "",
                                "stage": payload_result.get("stage") or "",
                                "applied_count": applied_count,
                                "continued": True,
                            },
                        )
                        refresh_all()
                if active_task_id:
                    state.engine.set_step(active_task_id, TaskStep.COLLECT_CARDS, "任务模式：条件写入完成，自动点击搜索")
                    save_task_checkpoint(active_task_id, "click_search", TaskStep.COLLECT_CARDS, {"from": "apply_filters"})
                QTimer.singleShot(
                    _random_delay_ms(2200, 4200),
                    lambda task_id=active_task_id: route_click_search_only(
                        show_popup_on_fail=False,
                        trigger_task_id=task_id,
                        max_attempts=1,
                    ),
                )

        result_var = "__liepin_apply_result__"
        poll_interval_ms = 200
        step_delay_ms = int(payload.get("step_delay_ms") or 0)
        estimated_step_count = 8 if city_only else max(8, len(target_keys) + 4)
        max_wait_ms = 90000 + max(0, step_delay_ms) * estimated_step_count
        max_poll_attempts = max(450, int(max_wait_ms / poll_interval_ms))
        page_adapter.apply_conditions(
            payload,
            lambda value: handle_apply(
                _js_payload(value)
                or {
                    "error": "写入条件脚本超时，未拿到有效回传",
                    "applied": {},
                    "skipped": {},
                }
            ),
            result_var=result_var,
            poll_interval_ms=poll_interval_ms,
            max_poll_attempts=max_poll_attempts,
        )

    def task_result_filter_labels(task_id: int | None) -> list[str]:
        if not task_id:
            return []
        row = db.fetch_one(
            "SELECT hide_viewed, hide_contacted, hide_contact_info FROM tasks WHERE id = ?",
            (int(task_id),),
        )
        if not row:
            return []
        labels: list[str] = []
        if row["hide_viewed"]:
            labels.append("隐藏已查看")
        if row["hide_contacted"]:
            labels.append("隐藏已沟通")
        if row["hide_contact_info"]:
            labels.append("隐藏已获取联系方式")
        return labels

    def route_apply_task_result_filters(task_id: int, done_callback: Callable[[], None]) -> None:
        labels = task_result_filter_labels(task_id)
        if not labels:
            done_callback()
            return
        if _task_automation_blocked(state, task_id):
            append_log(["已跳过搜索结果过滤", f"任务 ID：{task_id}", f"任务状态：{_task_block_label(state, task_id)}"])
            return
        append_log(["开始应用搜索结果过滤", f"任务 ID：{task_id}", f"目标：{_join_values(labels)}"])
        save_task_checkpoint(task_id, "apply_result_filters", TaskStep.COLLECT_CARDS, {"labels": labels})
        state.engine.set_step(task_id, TaskStep.COLLECT_CARDS, "任务模式：搜索后应用结果过滤")
        results: list[dict[str, Any]] = []

        def apply_one(index: int, attempt: int = 1) -> None:
            if _task_automation_blocked(state, task_id):
                append_log(["已停止搜索结果过滤", f"任务 ID：{task_id}", f"任务状态：{_task_block_label(state, task_id)}"])
                return
            if index >= len(labels):
                failed = [item for item in results if not item.get("ok")]
                db.log(
                    "任务模式：搜索结果过滤完成",
                    task_id=task_id,
                    account_id=state.current_account_id,
                    step=TaskStep.COLLECT_CARDS.value,
                    level="warning" if failed else "info",
                    payload={"labels": labels, "results": results},
                )
                append_log(
                    [
                        "搜索结果过滤完成",
                        f"成功：{len(results) - len(failed)}/{len(labels)}",
                        f"失败：{_join_values([str(item.get('targetText') or '-') for item in failed]) or '-'}",
                    ]
                )
                if failed:
                    state.engine.fail(task_id, f"搜索结果过滤失败：{_join_values([str(item.get('targetText') or '-') for item in failed])}")
                    task_notice_without_pause(
                        task_id,
                        "搜索结果过滤失败，任务已跳过",
                        f"失败项：{_join_values([str(item.get('targetText') or '-') for item in failed]) or '-'}",
                        "请在调试里单测对应过滤项；队列会继续执行下一条任务。",
                        severity="warning",
                        step=TaskStep.COLLECT_CARDS,
                        payload={"labels": labels, "results": results},
                    )
                    continue_task_queue(task_id)
                    refresh_all()
                    return
                done_callback()
                return
            label = labels[index]

            def handle_filter(payload: dict[str, Any] | str | None) -> None:
                payload = _js_payload(payload)
                payload["targetText"] = payload.get("targetText") or label
                payload["attempt"] = attempt
                ok = bool(payload.get("ok"))
                append_log(
                    [
                        "搜索结果过滤项处理完成",
                        f"目标：{label}",
                        f"状态：{'通过' if ok else '未通过'}",
                        f"找到：{'是' if payload.get('found') else '否'}；点击：{'是' if payload.get('clicked') else '否'}；最终：{'已选中' if payload.get('after') else '未选中'}",
                    ]
                )
                if not ok and attempt < 2:
                    QTimer.singleShot(_random_delay_ms(1000, 1800), lambda: apply_one(index, attempt + 1))
                    return
                results.append(payload)
                QTimer.singleShot(_random_delay_ms(800, 1500), lambda: apply_one(index + 1, 1))

            page_adapter.toggle_result_filter(label, handle_filter)

        QTimer.singleShot(_random_delay_ms(800, 1600), lambda: apply_one(0, 1))

    def route_click_search_only(
        *,
        show_popup_on_fail: bool = True,
        trigger_task_id: int | None = None,
        max_attempts: int = 1,
    ) -> None:
        active_task_id = trigger_task_id or state.current_task_id
        if _task_automation_blocked(state, active_task_id):
            append_log(["已跳过点击搜索", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
            return
        if trigger_task_id:
            task_row = db.fetch_one("SELECT status, current_step FROM tasks WHERE id = ?", (int(trigger_task_id),))
            current_step = str(task_row["current_step"] or "") if task_row else ""
            current_status = str(task_row["status"] or "") if task_row else ""
            if current_status != TaskStatus.RUNNING.value or current_step not in {
                TaskStep.APPLY_FILTERS.value,
                TaskStep.COLLECT_CARDS.value,
            }:
                db.log(
                    "任务模式：已丢弃过期搜索点击动作",
                    task_id=trigger_task_id,
                    account_id=state.current_account_id,
                    step=current_step,
                    payload={
                        "status": current_status,
                        "current_step": current_step,
                        "reason": "搜索阶段延迟动作已过期，当前任务已进入后续简历流程",
                    },
                )
                return
        click_state = {"attempt": 0}

        def run_attempt() -> None:
            click_state["attempt"] += 1
            page_adapter.click_search(state.search_click_hints, handle_click)

        def handle_click(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            if active_task_id:
                db.log(
                    "搜索按钮点击结果",
                    task_id=active_task_id,
                    account_id=state.current_account_id,
                    step=TaskStep.COLLECT_CARDS.value,
                    payload={
                        "attempt": click_state["attempt"],
                        "max_attempts": max(1, int(max_attempts)),
                        "result_source": payload.get("resultSource") or "",
                        "clicked_search": bool(payload.get("clickedSearch")),
                        "matched_by_hint": bool(payload.get("matchedByHint")),
                        "hint_count": payload.get("hintCount"),
                        "direct_candidate_count": payload.get("directCandidateCount"),
                        "best_direct_score": payload.get("bestDirectScore"),
                        "best_hint_score": payload.get("bestHintScore"),
                        "search_button_text": payload.get("searchButtonText") or "",
                        "search_button_class": payload.get("searchButtonClass") or "",
                        "input_value": payload.get("inputValue") or "",
                        "url": payload.get("url") or web.url().toString(),
                    },
                )
            append_log(
                [
                    "已执行搜索按钮点击",
                    f"尝试次数：{click_state['attempt']}/{max(1, int(max_attempts))}",
                    f"点击来源：{payload.get('resultSource') or '-'}",
                    f"直接候选数：{payload.get('directCandidateCount') if payload.get('directCandidateCount') is not None else '-'}",
                    f"直接最高分：{payload.get('bestDirectScore') if payload.get('bestDirectScore') is not None else '-'}",
                    f"录制指纹数：{payload.get('hintCount') if payload.get('hintCount') is not None else len(state.search_click_hints)}",
                    f"录制命中：{'是' if payload.get('matchedByHint') else '否'}",
                    f"搜索按钮点击：{'是' if payload.get('clickedSearch') else '否'}",
                    f"按钮文本：{payload.get('searchButtonText') or '-'}",
                    f"按钮类名：{payload.get('searchButtonClass') or '-'}",
                    f"输入框当前值：{payload.get('inputValue') or '-'}",
                ]
            )
            if payload.get("clickedSearch"):
                if active_task_id:
                    if _task_automation_blocked(state, active_task_id):
                        append_log(["已跳过搜索后抓列表", f"任务 ID：{active_task_id}", f"任务状态：{_task_block_label(state, active_task_id)}"])
                        return
                    db.execute(
                        "UPDATE tasks SET status = ?, last_error = '', updated_at = datetime('now') WHERE id = ?",
                        (TaskStatus.RUNNING.value, active_task_id),
                    )
                    state.engine.set_step(active_task_id, TaskStep.COLLECT_CARDS, "任务模式：已自动点击搜索，等待结果加载")
                    save_task_checkpoint(active_task_id, "collect_cards", TaskStep.COLLECT_CARDS, {"from": "click_search"})

                    def collect_after_search(task_id: int) -> None:
                        if _task_automation_blocked(state, task_id):
                            append_log(["已跳过搜索结果抓取", f"任务 ID：{task_id}", f"任务状态：{_task_block_label(state, task_id)}"])
                            return
                        task_row = db.fetch_one("SELECT status, current_step FROM tasks WHERE id = ?", (int(task_id),))
                        current_step = str(task_row["current_step"] or "") if task_row else ""
                        current_status = str(task_row["status"] or "") if task_row else ""
                        if current_status != TaskStatus.RUNNING.value or current_step != TaskStep.COLLECT_CARDS.value:
                            db.log(
                                "任务模式：已丢弃过期搜索结果抓取动作",
                                task_id=task_id,
                                account_id=state.current_account_id,
                                step=current_step,
                                payload={
                                    "status": current_status,
                                    "current_step": current_step,
                                    "reason": "搜索结果抓取延迟动作已过期，当前任务已进入后续简历流程",
                                },
                            )
                            return
                        collect_candidate_cards_strict(
                            trigger_task_id=task_id,
                            task_mode=True,
                            show_popup_on_empty=False,
                        )

                    QTimer.singleShot(
                        _random_delay_ms(5000, 8500),
                        lambda task_id=active_task_id: route_apply_task_result_filters(
                            task_id,
                            lambda task_id=task_id: collect_after_search(task_id),
                        ),
                    )
                return
            if click_state["attempt"] < max(1, int(max_attempts)):
                QTimer.singleShot(_random_delay_ms(1600, 3200), run_attempt)
                return
            if active_task_id:
                state.engine.fail(int(active_task_id), "搜索按钮点击失败")
                append_log(
                    [
                        "搜索按钮点击失败，任务不再熔断暂停",
                        "未识别到猎聘页面里的搜索按钮，请确认当前页面仍在找人查询页。",
                        "当前任务已标记失败，队列模式会继续下一条任务。",
                    ]
                )
                continue_task_queue(int(active_task_id))
                refresh_all()
            if show_popup_on_fail:
                message("未识别到猎聘搜索按钮，请确认当前页面仍在找人查询页。")

        run_attempt()

    def route_start_web_recording() -> None:
        def handle_start(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            append_log(
                [
                    "网页录制已启动",
                    f"已在录制：{'是' if payload.get('alreadyRunning') else '否'}",
                    f"URL：{payload.get('url') or web.url().toString()}",
                    f"标题：{payload.get('title') or '-'}",
                    "请现在手工操作：按实际流程点击目标元素（例如搜索按钮）。",
                    "操作完成后点击“结束录制并保存”。",
                ]
            )
            message("已开始录制网页操作。请按实际流程操作一次，然后点“结束录制并保存”。")

        page_adapter.start_recording(handle_start)

    def route_stop_web_recording() -> None:
        def handle_stop(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            if not payload.get("ok"):
                append_log(
                    [
                        "网页录制未结束",
                        f"原因：{payload.get('reason') or '未知'}",
                        "请先点击“开始录制网页操作”。",
                    ]
                )
                message("当前没有进行中的网页录制。")
                return
            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            record = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "account_id": state.current_account_id,
                "job_id": state.current_job_id,
                "url": payload.get("url") or web.url().toString(),
                "title": payload.get("title") or "",
                "duration_ms": int(payload.get("durationMs") or 0),
                "event_count": len(events),
                "events": events,
            }
            record_dir.mkdir(parents=True, exist_ok=True)
            file_path = record_dir / f"web_record_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            file_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            search_hints = _extract_search_click_hints(events)
            if search_hints:
                state.search_click_hints = search_hints
                state.search_click_hints_source = str(file_path)
            tail_lines: list[str] = []
            for item in events[-8:]:
                if not isinstance(item, dict):
                    continue
                target = item.get("payload", {}).get("target", {}) if isinstance(item.get("payload"), dict) else {}
                target_text = ""
                if isinstance(target, dict):
                    target_text = target.get("text") or target.get("placeholder") or target.get("id") or target.get("tag") or ""
                tail_lines.append(f"{item.get('t', '-')}ms | {item.get('type', '-')} | {target_text}")
            append_log(
                [
                    "网页录制已保存",
                    f"文件：{file_path}",
                    f"事件数：{len(events)}；时长：{record['duration_ms']}ms",
                    f"搜索按钮指纹：{len(search_hints)} 条",
                    f"尾部事件：{' || '.join(tail_lines) if tail_lines else '-'}",
                    "已按本次录制更新按钮点击指纹，后续点击将优先使用该轨迹。",
                ]
            )
            result.setPlainText(
                "\n".join(
                    [
                        "录制已保存",
                        str(file_path),
                        "",
                        f"事件数：{len(events)}",
                        f"时长：{record['duration_ms']}ms",
                        "",
                        "最近事件：",
                        *tail_lines,
                    ]
                )
            )
            message(f"录制已保存：{file_path.name}")

        page_adapter.stop_recording(handle_stop)

    def route_collect_and_open_candidate(
        candidate_index: int | None = None,
        *,
        close_current_detail: bool = False,
        task_mode: bool = False,
    ) -> None:
        active_task_id = state.current_task_id
        if _task_automation_blocked(state, active_task_id):
            append_log(["已跳过打开候选人", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
            return
        row = route_job_row()
        if not row:
            message("请先维护岗位 JD。")
            return
        if task_mode and active_task_id:
            progress = state.runtime.progress(int(active_task_id))
            if progress.reached_limit:
                closed_work_tabs = web.close_work_tabs() if close_current_detail and task_mode else 0
                append_log(
                    [
                        "任务已达到目标，停止继续打开候选人",
                        f"目标：{progress.target_label} {progress.current_count}/{progress.max_candidates}",
                        f"已抓简历：{progress.processed_candidates}；已沟通：{progress.greeted_candidates}",
                        f"任务 ID：{active_task_id}",
                        f"已关闭工作页：{closed_work_tabs}",
                    ]
                )
                state.engine.finish(int(active_task_id))
                continue_task_queue(int(active_task_id))
                refresh_all()
                return
        target_index = max(0, int(state.next_candidate_index if candidate_index is None else candidate_index))
        if task_mode and active_task_id:
            save_task_checkpoint(
                active_task_id,
                "open_candidate",
                TaskStep.OPEN_RESUME,
                {"target_index": target_index, "close_current_detail": bool(close_current_detail)},
            )
        closed_detail = False
        closed_work_tabs = 0
        if close_current_detail:
            if task_mode:
                closed_work_tabs = web.close_work_tabs()
                closed_detail = closed_work_tabs > 0
                if active_task_id:
                    db.log(
                        "任务模式：已关闭简历/沟通工作页，等待找人页稳定",
                        task_id=active_task_id,
                        account_id=state.current_account_id,
                        step=TaskStep.OPEN_RESUME.value,
                        payload={
                            "target_index": target_index,
                            "closed_work_tabs": closed_work_tabs,
                            "current_url": web.url().toString(),
                            "delay_ms": 1200,
                        },
                    )
                if closed_detail:
                    append_log(
                        [
                            "已关闭上一位候选人的工作页",
                            f"关闭数量：{closed_work_tabs}",
                            f"下一位目标序号：第 {target_index + 1} 位",
                            "等待找人页稳定后继续。",
                        ]
                    )
                    refresh_all()
                    timer = QTimer(window)
                    timer.setSingleShot(True)
                    timer.timeout.connect(
                        lambda: (
                            timer.deleteLater(),
                            route_collect_and_open_candidate(candidate_index, close_current_detail=False, task_mode=True),
                        )
                    )
                    timer.start(_random_delay_ms(1800, 3500))
                    return
            else:
                closed_detail = web.close_current_detail_tab()
        web.switch_home()
        if task_mode and active_task_id:
            db.log(
                "任务模式：开始打开列表候选人",
                task_id=active_task_id,
                account_id=state.current_account_id,
                step=TaskStep.OPEN_RESUME.value,
                payload={
                    "target_index": target_index,
                    "closed_detail": closed_detail,
                    "closed_work_tabs": closed_work_tabs,
                    "current_page_card_count": int(state.current_page_card_count or 0),
                    "current_url": web.url().toString(),
                },
            )
        append_log(
            [
                "准备打开列表候选人",
                f"目标序号：第 {target_index + 1} 位",
                f"已关闭当前详情页：{'是' if closed_detail else '否'}",
                f"已关闭工作页数量：{closed_work_tabs}",
            ]
        )
        current_page_card_count = int(state.current_page_card_count or 0)
        if task_mode and current_page_card_count and target_index >= current_page_card_count:
            append_log(
                [
                    "本页候选人已分析完，准备翻到下一页",
                    f"当前页候选人数：{current_page_card_count}",
                    f"下一位目标序号：第 {target_index + 1} 位",
                ]
            )

            def wait_next_page_changed(
                before_signature: str,
                next_payload: dict[str, Any],
                retries: int = 12,
            ) -> None:
                if _task_automation_blocked(state, active_task_id):
                    append_log(["已跳过下一页验收", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
                    return

                def handle_after_page(after_payload: dict[str, Any] | None) -> None:
                    after_payload = _js_payload(after_payload)
                    after_signature = _candidate_list_signature(after_payload)
                    changed = bool(after_signature and after_signature != before_signature)
                    decision = decide_page_turn_result(
                        clicked_next=True,
                        changed=changed,
                        retries_left=retries,
                    )
                    if decision.action == WorkflowAction.CONTINUE:
                        state.current_list_page_index += 1
                        state.next_candidate_index = 0
                        state.last_opened_candidate_index = None
                        state.current_page_card_count = 0
                        if active_task_id:
                            save_task_checkpoint(
                                active_task_id,
                                "collect_cards",
                                TaskStep.COLLECT_CARDS,
                                {"page_index": state.current_list_page_index, "from": "next_page"},
                            )
                        if active_task_id:
                            db.log(
                                "任务模式：下一页验收通过",
                                task_id=active_task_id,
                                account_id=state.current_account_id,
                                step=TaskStep.COLLECT_CARDS.value,
                                payload={
                                    "page_index": state.current_list_page_index,
                                    "button_text": next_payload.get("buttonText") or "",
                                    "before_signature": before_signature[:500],
                                    "after_signature": after_signature[:500],
                                    "after_count": after_payload.get("count") or 0,
                                    "after_url": after_payload.get("url") or web.url().toString(),
                                },
                            )
                        append_log(
                            [
                                "下一页验收通过，开始抓取新页候选人",
                                f"新页码：第 {state.current_list_page_index} 页",
                                f"按钮文本：{next_payload.get('buttonText') or '-'}",
                                f"新页卡片数：{after_payload.get('count') or 0}",
                            ]
                        )
                        QTimer.singleShot(
                            300,
                            lambda: collect_candidate_cards_strict(
                                trigger_task_id=active_task_id,
                                task_mode=True,
                                show_popup_on_empty=False,
                                page_index=state.current_list_page_index,
                            ),
                        )
                        return
                    if decision.action == WorkflowAction.FINISH_TASK:
                        if active_task_id:
                            db.log(
                                "任务模式：下一页验收失败，页面未变化",
                                task_id=active_task_id,
                                account_id=state.current_account_id,
                                step=TaskStep.OPEN_RESUME.value,
                                level="warning",
                                payload={
                                    "button_text": next_payload.get("buttonText") or "",
                                    "next_payload": next_payload,
                                    "before_signature": before_signature[:500],
                                    "after_signature": after_signature[:500],
                                    "after_url": after_payload.get("url") or web.url().toString(),
                                },
                            )
                            finish_task_without_pause(
                                active_task_id,
                                decision.reason,
                                decision.detail,
                                "请事后查看分页单测；当前批量任务不会停在人工作业。",
                                severity="warning",
                                step=TaskStep.COLLECT_CARDS,
                                payload={
                                    "button_text": next_payload.get("buttonText") or "",
                                    "before_signature": before_signature[:500],
                                    "after_signature": after_signature[:500],
                                },
                            )
                        return
                    QTimer.singleShot(_random_delay_ms(1200, 2600), lambda: wait_next_page_changed(before_signature, next_payload, retries - 1))

                web.switch_home()
                page_adapter.collect_cards(handle_after_page)

            def after_next_page(next_payload: dict[str, Any] | None, before_signature: str) -> None:
                next_payload = _js_payload(next_payload)
                if active_task_id:
                    db.log(
                        "任务模式：下一页点击结果",
                        task_id=active_task_id,
                        account_id=state.current_account_id,
                        step=TaskStep.OPEN_RESUME.value,
                        level="info" if next_payload.get("clickedNext") else "warning",
                        payload=next_payload,
                    )
                if next_payload.get("error"):
                    append_log(
                        [
                            "下一页按钮脚本异常",
                            f"原因：{next_payload.get('error') or '-'}",
                            f"堆栈：{str(next_payload.get('stack') or '-')[:500]}",
                        ]
                    )
                decision = decide_page_turn_result(clicked_next=bool(next_payload.get("clickedNext")))
                if decision.action == WorkflowAction.FINISH_TASK:
                    append_log(
                        [
                            "未找到下一页按钮或下一页已结束",
                            f"当前页卡片数：{current_page_card_count}",
                        ]
                    )
                    if active_task_id:
                        finish_task_without_pause(
                            active_task_id,
                            decision.reason,
                            decision.detail,
                            "如需继续扩大人选，可提高任务目标人数或调整搜索条件后重新运行。",
                            severity="info",
                            step=TaskStep.DONE,
                            payload=next_payload,
                        )
                    else:
                        message("已经没有下一页候选人。")
                    return
                QTimer.singleShot(_random_delay_ms(1600, 3200), lambda: wait_next_page_changed(before_signature, next_payload))

            page_adapter.collect_cards(
                lambda before_payload: page_adapter.click_next_page(
                    lambda next_payload, sig=_candidate_list_signature(before_payload): after_next_page(next_payload, sig),
                )
            )
            return

        def validate_detail_open(open_payload: dict[str, Any], popup_before: int, retries: int = 24) -> None:
            if _task_automation_blocked(state, state.current_task_id):
                append_log(["已跳过候选人详情验收", f"任务 ID：{state.current_task_id or '-'}", f"任务状态：{_task_block_label(state, state.current_task_id)}"])
                return
            current_url = web.url().toString()
            title = ""
            try:
                title = web.current_view().title() or ""
            except Exception:
                title = ""
            opened_via_popup = state.popup_open_count > popup_before
            is_detail = bool(re.search(r"/resume/showresumedetail/?|res_id_encode=", current_url, re.I))
            if is_detail or retries <= 0:
                validation_passed = is_detail
                validation_status = "通过" if validation_passed else "未通过"
                target = open_payload.get("target") if isinstance(open_payload.get("target"), dict) else {}
                lines = [
                    "候选人详情页打开验收报告",
                    f"状态：{validation_status}",
                    f"岗位：{row['title']}",
                    f"打开方式：{'详情链接直达' if open_payload.get('directNavigate') else '列表卡片点击'}",
                    f"新页面事件：{'是' if opened_via_popup else '否'}",
                    f"列表卡片数：{open_payload.get('cardCount') or '-'}",
                    f"目标序号：第 {int(open_payload.get('requestedIndex') or target_index) + 1} 位",
                    f"实际序号：第 {int(open_payload.get('selectedIndex') or target_index) + 1} 位",
                    f"DOM卡片索引：{open_payload.get('cardIndex') if open_payload.get('cardIndex') is not None else '-'}",
                    f"点击节点：{target.get('tag') or '-'} #{target.get('id') or '-'} .{target.get('cls') or '-'}",
                    f"点击节点文本：{target.get('text') or '-'}",
                    f"详情链接：{open_payload.get('href') or '-'}",
                    f"打开前 URL：{open_payload.get('urlBefore') or '-'}",
                    f"当前 URL：{current_url or '-'}",
                    f"当前标题：{title or '-'}",
                    f"是否详情页：{'是' if is_detail else '否'}",
                    "",
                    "卡片摘要：",
                    str(open_payload.get("textPreview") or "-"),
                ]
                result.setPlainText("\n".join(lines))
                active_task_id = state.current_task_id
                db.log(
                    "候选人详情页打开验收结果",
                    task_id=active_task_id,
                    account_id=state.current_account_id,
                    step=TaskStep.OPEN_RESUME.value,
                    level="info" if validation_passed else "warning",
                    payload={
                        "validation_status": validation_status,
                        "validation_passed": validation_passed,
                        "opened_via_popup": opened_via_popup,
                        "is_detail": is_detail,
                        "current_url": current_url,
                        "title": title,
                        "open_payload": open_payload,
                    },
                )
                append_log(
                    [
                        "候选人详情页打开验收完成",
                        f"状态：{validation_status}",
                        f"目标序号：第 {int(open_payload.get('requestedIndex') or target_index) + 1} 位",
                        f"新页面事件：{'是' if opened_via_popup else '否'}",
                        f"当前 URL：{current_url or '-'}",
                        f"是否详情页：{'是' if is_detail else '否'}",
                        (
                            "已停在详情页。确认无误后再点击“5 抓当前简历”。"
                            if validation_passed
                            else "未确认进入详情页，系统未继续抓简历。"
                        ),
                    ]
                )
                if validation_passed:
                    opened_index = int(open_payload.get("selectedIndex") if open_payload.get("selectedIndex") is not None else target_index)
                    state.last_opened_candidate_index = opened_index
                    state.next_candidate_index = opened_index + 1
                    opened_card_count = int(open_payload.get("cardCount") or 0)
                    if opened_card_count > 0:
                        state.current_page_card_count = opened_card_count
                    else:
                        state.current_page_card_count = max(state.current_page_card_count, opened_index + 1)
                if active_task_id:
                    decision = decide_detail_validation(is_detail=is_detail, task_mode=task_mode, current_url=current_url or "")
                    if decision.action == WorkflowAction.CONTINUE:
                        state.engine.set_step(
                            active_task_id,
                            TaskStep.EXTRACT_RESUME,
                            f"任务模式：{decision.reason}",
                            payload={
                                "opened_index": int(open_payload.get("selectedIndex") if open_payload.get("selectedIndex") is not None else target_index),
                                "url": current_url or "",
                            },
                        )
                        save_task_checkpoint(
                            active_task_id,
                            "extract_resume",
                            TaskStep.EXTRACT_RESUME,
                            {"opened_index": int(open_payload.get("selectedIndex") if open_payload.get("selectedIndex") is not None else target_index), "url": current_url or ""},
                        )
                        refresh_all()
                        QTimer.singleShot(_random_delay_ms(1800, 3600), lambda: analyze_current_resume(auto_advance=True))
                        return
                    if decision.action == WorkflowAction.PAUSE_FOR_MANUAL and validation_passed:
                        state.engine.pause_for_user(
                            active_task_id,
                            HumanIntervention(
                                reason=decision.reason,
                                detail=f"已打开第 {opened_index + 1} 位候选人详情页：{current_url}",
                                action_hint="请确认右侧详情页无误后，点击“5 抓当前简历”；处理完后点“4.1 关闭并打开下一位”。",
                                severity="info",
                            ),
                        )
                    else:
                        if decision.action == WorkflowAction.SKIP_CANDIDATE:
                            skip_current_candidate_and_continue(
                                decision.reason,
                                decision.detail,
                                "系统已跳过该候选人，继续打开下一位。",
                                payload={
                                    "current_url": current_url or "",
                                    "open_payload": open_payload,
                                },
                            )
                            refresh_all()
                            return
                        state.engine.pause_for_user(
                            active_task_id,
                            HumanIntervention(
                                reason=decision.reason,
                                detail=decision.detail,
                                action_hint="请查看左侧验收报告和右侧页面，确认卡片点击规则后重试。",
                            ),
                        )
                    refresh_all()
                return
            QTimer.singleShot(500, lambda: validate_detail_open(open_payload, popup_before, retries - 1))

        def open_candidate(preferred_href: str = "") -> None:
            if _task_automation_blocked(state, state.current_task_id):
                append_log(["已跳过打开候选人动作", f"任务 ID：{state.current_task_id or '-'}", f"任务状态：{_task_block_label(state, state.current_task_id)}"])
                return
            href = str(preferred_href or "").strip()
            if href and re.search(r"/resume/showresumedetail/?|res_id_encode=", href, re.I):
                popup_before = state.popup_open_count
                open_payload = {
                    "clicked": True,
                    "directNavigate": True,
                    "usedLink": True,
                    "href": href,
                    "urlBefore": web.url().toString(),
                    "textPreview": "使用列表抓取到的详情链接打开",
                    "cardCount": "-",
                    "cardIndex": target_index,
                    "requestedIndex": target_index,
                    "selectedIndex": target_index,
                    "target": {"tag": "direct-url", "text": "详情链接直达"},
                }
                append_log(
                    [
                        "将使用列表抓取到的详情链接直接跳转",
                        f"详情链接：{href}",
                        "打开后只做详情页验收，不自动抓取简历。",
                    ]
                )
                open_url_with_account_profile(
                    href,
                    "候选人详情",
                    state.current_account_id,
                    log_reason="将使用列表抓取到的详情链接直接跳转",
                )
                QTimer.singleShot(_random_delay_ms(1200, 2600), lambda: validate_detail_open(open_payload, popup_before))
                return

            def handle_open(payload: dict[str, Any] | None) -> None:
                payload = _js_payload(payload)
                if payload.get("stack"):
                    append_log(
                        [
                            "打开首个候选人脚本返回异常",
                            f"原因：{payload.get('reason') or payload.get('error') or '-'}",
                            f"堆栈：{str(payload.get('stack') or '-')[:500]}",
                        ]
                    )
                if not payload.get("clicked"):
                    decision = decide_open_candidate_result(payload, task_mode=bool(task_mode and state.current_task_id))
                    if decision.action == WorkflowAction.PAGE_NEXT:
                        card_count = int(payload.get("cardCount") or 0)
                        if card_count > 0:
                            state.current_page_card_count = card_count
                        db.log(
                            "任务模式：目标序号超出当前可点击卡片，转入翻页判断",
                            task_id=state.current_task_id,
                            account_id=state.current_account_id,
                            step=TaskStep.OPEN_RESUME.value,
                            payload={
                                "target_index": target_index,
                                "card_count": card_count,
                                "reason": payload.get("message") or payload.get("reason") or "",
                                "current_url": web.url().toString(),
                            },
                        )
                        append_log(
                            [
                                "目标候选人超出当前可点击卡片，准备翻页",
                                f"目标序号：第 {target_index + 1} 位",
                                f"当前可点击卡片数：{card_count or '-'}",
                            ]
                        )
                        QTimer.singleShot(
                            300,
                            lambda idx=target_index: route_collect_and_open_candidate(
                                idx,
                                close_current_detail=False,
                                task_mode=True,
                            ),
                        )
                        return
                    append_log(
                        [
                            decision.reason,
                            decision.detail or payload.get("reason") or "没有找到候选人卡片",
                            f"文档上下文：{payload.get('documentCount') or '-'}；选择器候选：{payload.get('selectorCardCount') or '-'}；行级候选：{payload.get('heuristicCardCount') or '-'}",
                        ]
                    )
                    if decision.action == WorkflowAction.SKIP_CANDIDATE and task_mode and active_task_id:
                        skip_current_candidate_and_continue(
                            decision.reason,
                            decision.detail or "没有找到候选人卡片。",
                            "系统已跳过该候选人，继续打开下一位。",
                            payload=payload,
                        )
                    else:
                        message(decision.detail or payload.get("reason") or "没有找到候选人卡片。")
                    return
                href = str(payload.get("href") or "").strip()
                if href and re.search(r"/resume/showresumedetail/?|res_id_encode=", href, re.I):
                    append_log(
                        [
                            "已获取首个候选人详情链接，应用将直接跳转",
                            f"详情链接：{href}",
                            "打开后只做详情页验收，不自动抓取简历。",
                        ]
                    )
                    open_url_with_account_profile(
                        href,
                        "候选人详情",
                        state.current_account_id,
                        log_reason="已获取首个候选人详情链接，应用将直接跳转",
                    )
                    QTimer.singleShot(_random_delay_ms(1200, 2600), lambda data=payload: validate_detail_open(data, open_state["popup_before"]))
                    return
                append_log(
                    [
                        "已点击首个候选人卡片",
                        f"列表卡片数：{payload.get('cardCount')}",
                        f"文档上下文数：{payload.get('documentCount') or '-'}",
                        f"选择器候选：{payload.get('selectorCardCount') or '-'}；行级候选：{payload.get('heuristicCardCount') or '-'}",
                        f"直接跳转：{'是' if payload.get('directNavigate') else '否'}",
                        f"使用链接：{'是' if payload.get('usedLink') else '否'}",
                        f"详情链接：{payload.get('href') or '-'}",
                        f"URL 变化：{payload.get('urlBefore') or '-'} -> {payload.get('urlAfter') or '-'}",
                        f"卡片摘要：{payload.get('textPreview') or '-'}",
                        "等待详情页加载完成后做打开验收。",
                    ]
                    )
                QTimer.singleShot(_random_delay_ms(1200, 2600), lambda data=payload: validate_detail_open(data, open_state["popup_before"]))

            open_state = {"popup_before": state.popup_open_count}
            page_adapter.open_candidate_by_index(target_index, handle_open)

        if task_mode and target_index > 0:
            db.log(
                "任务模式：跳过列表重抓，直接按序号点击候选人",
                task_id=active_task_id,
                account_id=state.current_account_id,
                step=TaskStep.OPEN_RESUME.value,
                payload={
                    "target_index": target_index,
                    "current_page_card_count": int(state.current_page_card_count or 0),
                    "current_url": web.url().toString(),
                    "delay_ms": 900,
                },
            )
            append_log(
                [
                    "任务模式：直接打开下一位候选人",
                    f"目标序号：第 {target_index + 1} 位",
                    "不再重新触发列表抓取，避免回到首位候选人。",
                ]
            )
            refresh_all()
            timer = QTimer(window)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: (timer.deleteLater(), open_candidate("")))
            timer.start(_random_delay_ms(1600, 3200))
            return

        def handle_cards(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            cards = payload.get("cards") or []
            if payload.get("error"):
                append_log(
                    [
                        "候选人列表抓取脚本异常",
                        f"原因：{payload.get('error') or '-'}",
                        f"堆栈：{str(payload.get('stack') or '-')[:500]}",
                    ]
                )
            if not cards:
                if task_mode and active_task_id:
                    db.log(
                        "任务模式：打开下一位时列表候选人为空",
                        task_id=active_task_id,
                        account_id=state.current_account_id,
                        step=TaskStep.OPEN_RESUME.value,
                        level="warning",
                        payload=payload,
                    )
                append_log(
                    [
                        "列表候选人抓取为空，已停止打开首个候选人",
                        f"原始节点：{payload.get('rawCount') or '-'}；选择器候选：{payload.get('selectorNodeCount') or '-'}；行级候选：{payload.get('heuristicNodeCount') or '-'}；文档上下文：{payload.get('documentCount') or '-'}",
                        f"候选详情链接数：{payload.get('candidateLinkCount') or '-'}；样本链接：{_join_values((payload.get('sampleCandidateLinks') or [])[:3]) or '-'}",
                        f"样本行文本：{_join_values((payload.get('sampleRows') or [])[:2]) or '-'}",
                        f"当前 URL：{payload.get('url') or web.url().toString()}",
                        "请先确认页面确实有搜索结果。",
                    ]
                )
                if task_mode and active_task_id:
                    finish_task_without_pause(
                        active_task_id,
                        "未检测到候选人卡片",
                        "当前搜索结果页未抓取到候选人卡片，系统已结束当前任务并继续队列。",
                        "请事后检查该岗位/账号的搜索条件和页面加载情况。",
                        severity="warning",
                        step=TaskStep.COLLECT_CARDS,
                        payload=payload,
                    )
                else:
                    message("未检测到候选人卡片，请确认搜索结果已加载。")
                return
            saved = 0
            target_href = ""
            for card in cards:
                text = card.get("text", "")
                url = card.get("href") or ""
                if (
                    saved == target_index
                    and isinstance(url, str)
                    and re.search(r"/resume/showresumedetail/?|res_id_encode=", url, re.I)
                ):
                    target_href = url
                key = _candidate_external_id(url, text)
                parsed_card = _parse_candidate_card_text(text)
                db.upsert_candidate(
                    {
                        "job_id": int(row["id"]),
                        "source_account_id": state.current_account_id,
                        "source_task_id": state.current_task_id,
                        "external_id": key,
                        "name": _clean_candidate_name(card.get("name") or _first_non_empty_line(text)),
                        "title": parsed_card["title"],
                        "company": parsed_card["company"],
                        "city": parsed_card["city"],
                        "experience": parsed_card["experience"],
                        "education": parsed_card["education"],
                        "profile_url": url or f"list-card:{key}",
                        "search_keyword": " ".join(loads(row["keywords"], [])),
                        "resume_status": "list_card",
                    }
                )
                saved += 1
            append_log(
                [
                    "列表候选人摘要抓取完成",
                    f"当前页：第 {page_index} 页",
                    f"发现/保存：{saved} 条（原始节点：{payload.get('rawCount') or '-'}；选择器候选：{payload.get('selectorNodeCount') or '-'}；行级候选：{payload.get('heuristicNodeCount') or '-'}；文档上下文：{payload.get('documentCount') or '-'})",
                    f"目标序号：第 {target_index + 1} 位",
                    f"当前页首个候选人链接：{current_first_href or '-'}",
                    "现在继续判断是否还有下一页。",
                ]
            )
            if task_mode and active_task_id:
                db.log(
                    "任务模式：打开下一位前列表摘要抓取完成",
                    task_id=active_task_id,
                    account_id=state.current_account_id,
                    step=TaskStep.OPEN_RESUME.value,
                    payload={
                        "saved": saved,
                        "target_index": target_index,
                        "current_first_href": current_first_href or "",
                        "raw_count": payload.get("rawCount"),
                        "selector_node_count": payload.get("selectorNodeCount"),
                        "heuristic_node_count": payload.get("heuristicNodeCount"),
                        "document_count": payload.get("documentCount"),
                    },
                )
            refresh_all()
            if target_index >= len(cards):
                append_log(
                    [
                        "目标候选人序号超出当前列表",
                        f"目标序号：第 {target_index + 1} 位；当前列表：{len(cards)} 位",
                    ]
                )
                if task_mode and active_task_id:
                    finish_task_without_pause(
                        active_task_id,
                        "当前列表没有下一位候选人",
                        f"目标序号第 {target_index + 1} 位超出当前列表 {len(cards)} 位，系统已结束当前任务并继续队列。",
                        "如果页面实际还有更多候选人，请查看分页日志。",
                        severity="info",
                        step=TaskStep.DONE,
                        payload={"target_index": target_index, "card_count": len(cards)},
                    )
                else:
                    message("已经没有下一位候选人。")
                return
            QTimer.singleShot(300, lambda href=target_href: open_candidate(href))

        def run_collect_cards_js() -> None:
            if _task_automation_blocked(state, active_task_id):
                append_log(["已跳过打开候选人前列表抓取", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
                return
            web.switch_home()
            page_adapter.collect_cards(handle_cards)

        if task_mode:
            db.log(
                "任务模式：等待找人页稳定后抓取列表",
                task_id=active_task_id,
                account_id=state.current_account_id,
                step=TaskStep.OPEN_RESUME.value,
                payload={
                    "target_index": target_index,
                    "current_url": web.url().toString(),
                    "delay_ms": 800,
                },
            )
            timer = QTimer(window)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: (timer.deleteLater(), run_collect_cards_js()))
            timer.start(800)
        else:
            run_collect_cards_js()

    def route_test_next_page() -> None:
        web.switch_home()
        append_log(["开始单测下一页按钮", "仅点击分页并验收页面变化，不打开候选人。"])

        def handle_before_page(before_payload: dict[str, Any] | None) -> None:
            before_payload = _js_payload(before_payload)
            before_signature = _candidate_list_signature(before_payload)
            before_count = int(before_payload.get("count") or 0)

            def handle_click_next(click_payload: dict[str, Any] | None) -> None:
                click_payload = _js_payload(click_payload)
                if not click_payload.get("clickedNext"):
                    report = [
                        "下一页单测失败",
                        "原因：未找到可点击的下一页按钮",
                        f"点击返回：{json.dumps(click_payload, ensure_ascii=False)[:1200]}",
                    ]
                    result.setPlainText("\n".join(report))
                    append_log(report)
                    db.log(
                        "下一页单测失败：未找到按钮",
                        account_id=state.current_account_id,
                        step=TaskStep.OPEN_RESUME.value,
                        level="warning",
                        payload=click_payload,
                    )
                    return

                def poll_after_page(retries: int = 12) -> None:
                    def handle_after_page(after_payload: dict[str, Any] | None) -> None:
                        after_payload = _js_payload(after_payload)
                        after_signature = _candidate_list_signature(after_payload)
                        after_count = int(after_payload.get("count") or 0)
                        changed = bool(after_signature and after_signature != before_signature)
                        if changed or retries <= 0:
                            status = "通过" if changed else "未通过"
                            report = [
                                "下一页单测报告",
                                f"状态：{status}",
                                f"点击来源：{click_payload.get('source') or '-'}",
                                f"按钮文本：{click_payload.get('buttonText') or '-'}",
                                f"按钮类名：{click_payload.get('buttonClass') or '-'}",
                                f"点击目标：{click_payload.get('targetTag') or '-'} .{click_payload.get('targetClass') or '-'}",
                                f"点击前 URL：{click_payload.get('urlBefore') or '-'}",
                                f"点击后即时 URL：{click_payload.get('urlAfter') or '-'}",
                                f"当前 URL：{after_payload.get('url') or web.url().toString()}",
                                f"点击前卡片数：{before_count}",
                                f"点击后卡片数：{after_count}",
                                f"页面指纹变化：{'是' if changed else '否'}",
                                "",
                                "点击返回：",
                                json.dumps(click_payload, ensure_ascii=False, indent=2)[:2000],
                            ]
                            result.setPlainText("\n".join(report))
                            append_log(
                                [
                                    "下一页单测完成",
                                    f"状态：{status}",
                                    f"来源：{click_payload.get('source') or '-'}",
                                    f"页面指纹变化：{'是' if changed else '否'}",
                                ]
                            )
                            db.log(
                                f"下一页单测{'通过' if changed else '失败'}",
                                account_id=state.current_account_id,
                                step=TaskStep.OPEN_RESUME.value,
                                level="info" if changed else "warning",
                                payload={
                                    "changed": changed,
                                    "click_payload": click_payload,
                                    "before_count": before_count,
                                    "after_count": after_count,
                                    "before_signature": before_signature[:500],
                                    "after_signature": after_signature[:500],
                                    "after_url": after_payload.get("url") or web.url().toString(),
                                },
                            )
                            if not changed:
                                message("下一页单测未通过：点击后页面没有变化。")
                            return
                        QTimer.singleShot(700, lambda: poll_after_page(retries - 1))

                    web.switch_home()
                    page_adapter.collect_cards(handle_after_page)

                QTimer.singleShot(900, poll_after_page)

            page_adapter.click_next_page(handle_click_next)

        page_adapter.collect_cards(handle_before_page)

    def route_test_result_filter(label: str) -> None:
        append_log(["开始结果过滤单测", f"目标：{label}", f"当前 URL：{web.url().toString()}"])

        def handle(payload: dict[str, Any] | str | None) -> None:
            payload = _js_payload(payload)
            ok = bool(payload.get("ok"))
            found = bool(payload.get("found"))
            clicked = bool(payload.get("clicked"))
            before = bool(payload.get("before"))
            after = bool(payload.get("after"))
            candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
            report = [
                "搜索结果过滤单测",
                f"目标：{label}",
                f"状态：{'通过' if ok else '未通过'}",
                f"找到控件：{'是' if found else '否'}",
                f"点击动作：{'是' if clicked else '否'}",
                f"点击前：{'已选中' if before else '未选中'}",
                f"点击后：{'已选中' if after else '未选中'}",
                f"候选控件数：{payload.get('candidateCount') if payload.get('candidateCount') is not None else '-'}",
                f"错误：{payload.get('error') or '-'}",
                "",
                "候选控件：",
                json.dumps(candidates, ensure_ascii=False, indent=2)[:3000],
            ]
            result.setPlainText("\n".join(report))
            append_log(
                [
                    "结果过滤单测完成",
                    f"目标：{label}",
                    f"状态：{'通过' if ok else '未通过'}",
                    f"找到：{'是' if found else '否'}；点击：{'是' if clicked else '否'}；最终：{'已选中' if after else '未选中'}",
                    f"错误：{payload.get('error') or '-'}",
                ]
            )
            db.log(
                f"结果过滤单测：{label}",
                account_id=state.current_account_id,
                step=TaskStep.COLLECT_CARDS.value,
                level="info" if ok else "warning",
                payload=payload,
            )
            if not ok:
                message(f"{label} 单测未通过，请确认搜索结果已加载。")

        page_adapter.toggle_result_filter(label, handle)

    route_fill_login_btn.clicked.connect(fill_login_for_current_account)
    route_check_login_btn.clicked.connect(lambda: check_login_status(True))
    route_sync_job_btn.clicked.connect(sync_route_job_from_jd)
    route_city_test_btn.clicked.connect(lambda: route_fill_search_and_submit(True))
    route_search_btn.clicked.connect(lambda: route_fill_search_and_submit(False))
    route_submit_search_btn.clicked.connect(route_click_search_only)
    route_next_page_test_btn.clicked.connect(route_test_next_page)
    route_hide_viewed_test_btn.clicked.connect(lambda: route_test_result_filter("隐藏已查看"))
    route_hide_contacted_test_btn.clicked.connect(lambda: route_test_result_filter("隐藏已沟通"))
    route_hide_contact_test_btn.clicked.connect(lambda: route_test_result_filter("隐藏已获取联系方式"))
    route_record_start_btn.clicked.connect(route_start_web_recording)
    route_record_stop_btn.clicked.connect(route_stop_web_recording)
    route_open_first_btn.clicked.connect(lambda: route_collect_and_open_candidate(0))
    route_next_candidate_btn.clicked.connect(lambda: route_collect_and_open_candidate(None, close_current_detail=True))

    def collect_candidate_cards_strict(
        *,
        trigger_task_id: int | None = None,
        task_mode: bool = False,
        show_popup_on_empty: bool = True,
        page_index: int = 1,
        first_href: str = "",
        seen_pages: set[str] | None = None,
    ) -> None:
        active_task_id_at_start = trigger_task_id or state.current_task_id
        if _task_automation_blocked(state, active_task_id_at_start):
            append_log(["已跳过候选人列表抓取", f"任务 ID：{active_task_id_at_start or '-'}", f"任务状态：{_task_block_label(state, active_task_id_at_start)}"])
            return
        if task_mode and active_task_id_at_start:
            save_task_checkpoint(
                active_task_id_at_start,
                "collect_cards",
                TaskStep.COLLECT_CARDS,
                {"page_index": page_index, "first_href": first_href},
            )
        row = current_job_row()
        if not row:
            message("请先选择岗位。")
            return
        seen_pages = seen_pages or set()

        def card_preview_lines(cards: list[dict[str, Any]], limit: int = 5) -> list[str]:
            lines: list[str] = []
            for index, card in enumerate(cards[:limit], start=1):
                text = re.sub(r"\s+", " ", str(card.get("text") or "")).strip()
                href = str(card.get("href") or "").strip()
                lines.append(f"{index}. {text[:180] or '-'}")
                lines.append(f"   详情链接：{href or '-'}")
            return lines or ["-"]

        def handle(payload: dict[str, Any] | None) -> None:
            payload = _js_payload(payload)
            cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
            active_task_id = trigger_task_id or state.current_task_id
            if _task_automation_blocked(state, active_task_id):
                append_log(["已跳过候选人列表处理", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
                return
            account_id = state.current_account_id
            job_id = int(row["id"])
            has_error = bool(payload.get("error"))
            state.current_page_card_count = len(cards)
            state.current_list_page_index = page_index
            current_first_href = first_href
            page_signature = json.dumps(
                {
                    "resultCountText": str(payload.get("resultCountText") or ""),
                    "sampleCandidateLinks": (payload.get("sampleCandidateLinks") or [])[:3],
                    "sampleRows": (payload.get("sampleRows") or [])[:2],
                    "count": payload.get("count") or 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if page_signature in seen_pages:
                append_log(
                    [
                        "分页抓取检测到重复页面，停止继续翻页",
                        f"页码：第 {page_index} 页",
                        f"页面指纹：{page_signature[:220]}",
                    ]
                )
                if task_mode:
                    refresh_all()
                else:
                    refresh_all()
                return
            seen_pages.add(page_signature)
            progress = None
            max_candidates = 0
            current_target_count = 0
            if active_task_id:
                progress = state.runtime.progress(int(active_task_id))
                max_candidates = progress.max_candidates
                current_target_count = progress.current_count
            remaining_slots = progress.remaining_slots if progress and max_candidates > 0 else len(cards)
            if task_mode and max_candidates > 0 and remaining_slots <= 0:
                append_log(
                    [
                        "任务已达到目标，跳过本页候选人处理",
                        f"目标：{progress.target_label if progress else '人选'} {current_target_count}/{max_candidates}",
                        f"已抓简历：{progress.processed_candidates if progress else '-'}；已沟通：{progress.greeted_candidates if progress else '-'}",
                        f"页码：第 {page_index} 页",
                    ]
                )
                if active_task_id:
                    state.engine.finish(int(active_task_id))
                    continue_task_queue(int(active_task_id))
                refresh_all()
                return
            if task_mode and progress and progress.should_limit_collected_cards and max_candidates > 0 and len(cards) > remaining_slots:
                append_log(
                    [
                        "当前页候选人超出任务目标，已按剩余名额截断",
                        f"当前已处理：{current_target_count}/{max_candidates}",
                        f"本页候选数：{len(cards)}，剩余名额：{remaining_slots}",
                    ]
                )
                cards = cards[:remaining_slots]
            saved = 0
            for card in cards:
                if not isinstance(card, dict):
                    continue
                text = str(card.get("text") or "")
                url = str(card.get("href") or "")
                if not current_first_href and re.search(r"/resume/showresumedetail/?|res_id_encode=", url, re.I):
                    current_first_href = url
                key = _candidate_external_id(url, text)
                name = _clean_candidate_name(card.get("name") or _first_non_empty_line(text))
                parsed_card = _parse_candidate_card_text(text)
                db.upsert_candidate(
                    {
                        "job_id": job_id,
                        "source_account_id": account_id,
                        "source_task_id": active_task_id,
                        "external_id": key,
                        "name": name,
                        "title": parsed_card["title"],
                        "company": parsed_card["company"],
                        "city": parsed_card["city"],
                        "experience": parsed_card["experience"],
                        "education": parsed_card["education"],
                        "profile_url": url or f"list-card:{key}",
                        "search_keyword": " ".join(loads(row["keywords"], [])),
                        "resume_status": "list_card",
                    }
                )
                saved += 1

            result_count_text = str(payload.get("resultCountText") or "")
            result_count_match = re.search(r"([0-9][0-9,]*)", result_count_text)
            result_count_number = int(result_count_match.group(1).replace(",", "")) if result_count_match else None
            collection_decision = decide_card_collection_validation(
                has_error=has_error,
                saved_count=saved,
                result_count_number=result_count_number,
            )
            validation_passed = collection_decision.action == WorkflowAction.CONTINUE
            validation_status = "通过" if validation_passed else "未通过"
            sample_links = payload.get("sampleCandidateLinks") if isinstance(payload.get("sampleCandidateLinks"), list) else []
            sample_rows = payload.get("sampleRows") if isinstance(payload.get("sampleRows"), list) else []
            report_lines = [
                "候选人列表抓取验收报告",
                f"状态：{validation_status}",
                f"岗位：{row['title']}",
                f"当前 URL：{payload.get('url') or web.url().toString()}",
                f"页面总人数文本：{result_count_text or '-'}",
                f"保存候选人：{saved}",
                f"脚本卡片数：{payload.get('count') or 0}",
                f"去重前卡片数：{payload.get('preDedupCount') or '-'}",
                f"原始节点：{payload.get('rawCount') or '-'}",
                f"选择器候选：{payload.get('selectorNodeCount') or '-'}",
                f"行级候选：{payload.get('heuristicNodeCount') or '-'}",
                f"详情链接数：{payload.get('candidateLinkCount') or '-'}",
                f"首个详情链接：{first_href or '-'}",
                f"错误：{payload.get('error') or '-'}",
                "",
                "前几张候选人摘要：",
                *card_preview_lines(cards),
                "",
                "样本详情链接：",
                *([str(item) for item in sample_links[:5]] or ["-"]),
                "",
                "样本行文本：",
                *([str(item)[:240] for item in sample_rows[:3]] or ["-"]),
            ]
            result.setPlainText("\n".join(report_lines))
            log_payload = {
                "validation_status": validation_status,
                "validation_passed": validation_passed,
                "saved_count": saved,
                "script_count": payload.get("count") or 0,
                "pre_dedup_count": payload.get("preDedupCount"),
                "raw_count": payload.get("rawCount"),
                "selector_node_count": payload.get("selectorNodeCount"),
                "heuristic_node_count": payload.get("heuristicNodeCount"),
                "result_count_text": result_count_text,
                "result_count_number": result_count_number,
                "candidate_link_count": payload.get("candidateLinkCount"),
                "first_href": first_href,
                "sample_links": sample_links[:5],
                "sample_cards": [
                    {
                        "name": card.get("name") or "",
                        "href": card.get("href") or "",
                        "text": re.sub(r"\s+", " ", str(card.get("text") or "")).strip()[:240],
                    }
                    for card in cards[:5]
                    if isinstance(card, dict)
                ],
                "error": payload.get("error") or "",
                "url": payload.get("url") or web.url().toString(),
            }
            db.log(
                "候选人列表抓取验收结果",
                task_id=active_task_id,
                account_id=account_id,
                step=TaskStep.COLLECT_CARDS.value,
                level="info" if validation_passed else "warning",
                payload=log_payload,
            )
            append_log(
                [
                    "候选人列表抓取验收完成",
                    f"状态：{validation_status}",
                    f"页面总人数：{result_count_text or '-'}",
                    f"保存候选人：{saved}；脚本卡片数：{payload.get('count') or 0}；去重前：{payload.get('preDedupCount') or '-'}",
                    f"原始节点：{payload.get('rawCount') or '-'}；选择器候选：{payload.get('selectorNodeCount') or '-'}；行级候选：{payload.get('heuristicNodeCount') or '-'}",
                    f"详情链接数：{payload.get('candidateLinkCount') or '-'}；首个详情链接：{first_href or '-'}",
                    "任务模式下系统会自动进入首位候选人；验收异常会记录日志但不转人工暂停。",
                ]
            )
            refresh_all()
            if not validation_passed:
                if active_task_id and task_mode:
                    finish_task_without_pause(
                        active_task_id,
                        collection_decision.reason,
                        (
                            f"保存候选人 {saved} 条；脚本卡片数 {payload.get('count') or 0}；"
                            f"页面总数 {result_count_text or '-'}；原始节点 {payload.get('rawCount') or '-'}；"
                            f"详情链接数 {payload.get('candidateLinkCount') or '-'}。"
                        ),
                        "系统已结束当前任务并继续队列；请事后查看搜索结果是否加载、样本行文本和列表识别规则。",
                        severity="warning",
                        step=TaskStep.COLLECT_CARDS,
                        payload=log_payload,
                    )
                if show_popup_on_empty:
                    message("未通过候选人列表抓取验收，请查看左侧报告。")
                return
            state.next_candidate_index = 0
            state.last_opened_candidate_index = None
            if active_task_id and task_mode:
                def wait_for_detail_open(open_href: str, retries: int = 24) -> None:
                    if _task_automation_blocked(state, active_task_id):
                        append_log(["已跳过详情页验收", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
                        return
                    current_url = web.url().toString()
                    is_detail = bool(re.search(r"/resume/showresumedetail/?|res_id_encode=", current_url, re.I))
                    if is_detail or retries <= 0:
                        if not is_detail:
                            append_log(
                                [
                                    "本页首个候选人打开验收未通过",
                                    f"当前 URL：{current_url or '-'}",
                                ]
                            )
                            skip_current_candidate_and_continue(
                                "本页首个候选人详情页打开验收未通过",
                                f"当前 URL 未识别为简历详情页：{current_url or '-'}。",
                                "系统已跳过该候选人，继续打开下一位。",
                                payload={"current_url": current_url or "", "href": open_href, "page_index": page_index},
                            )
                            return
                        state.engine.set_step(
                            active_task_id,
                            TaskStep.EXTRACT_RESUME,
                            "任务模式：本页候选人打开完成，自动抓当前简历",
                            payload={
                                "page_index": page_index,
                                "href": open_href,
                                "url": current_url or "",
                            },
                        )
                        save_task_checkpoint(
                            active_task_id,
                            "extract_resume",
                            TaskStep.EXTRACT_RESUME,
                            {"page_index": page_index, "href": open_href, "url": current_url or ""},
                        )
                        refresh_all()
                        QTimer.singleShot(_random_delay_ms(1800, 3600), lambda: analyze_current_resume(auto_advance=True))
                        return
                    QTimer.singleShot(500, lambda: wait_for_detail_open(open_href, retries - 1))

                open_href = current_first_href or first_href
                detail_href = open_href if re.search(r"/resume/showresumedetail/?|res_id_encode=", open_href or "", re.I) else ""
                if not detail_href:
                    def validate_clicked_candidate(open_payload: dict[str, Any], popup_before: int, retries: int = 24) -> None:
                        if _task_automation_blocked(state, active_task_id):
                            append_log(["已跳过点击候选人后的详情页验收", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
                            return
                        current_url = web.url().toString()
                        try:
                            current_url = web.current_view().url().toString() or current_url
                        except Exception:
                            pass
                        is_detail = bool(re.search(r"/resume/showresumedetail/?|res_id_encode=", current_url, re.I))
                        opened_via_popup = state.popup_open_count > popup_before
                        if is_detail or retries <= 0:
                            db.log(
                                "任务模式：点击候选人卡片打开详情验收结果",
                                task_id=active_task_id,
                                account_id=account_id,
                                step=TaskStep.OPEN_RESUME.value,
                                level="info" if is_detail else "warning",
                                payload={
                                    "validation_passed": is_detail,
                                    "opened_via_popup": opened_via_popup,
                                    "current_url": current_url,
                                    "open_payload": open_payload,
                                },
                            )
                            if not is_detail:
                                skip_current_candidate_and_continue(
                                    "候选人卡片点击后未进入简历详情页",
                                    f"当前 URL 未识别为简历详情页：{current_url or '-'}。",
                                    "系统已跳过该候选人，继续打开下一位。",
                                    payload={
                                        "current_url": current_url or "",
                                        "opened_via_popup": opened_via_popup,
                                        "open_payload": open_payload,
                                    },
                                )
                                return
                            opened_index = int(open_payload.get("selectedIndex") or 0)
                            state.last_opened_candidate_index = opened_index
                            state.next_candidate_index = opened_index + 1
                            state.current_page_card_count = max(state.current_page_card_count, saved)
                            state.engine.set_step(
                                active_task_id,
                                TaskStep.EXTRACT_RESUME,
                                "任务模式：候选人详情页验收通过，自动抓当前简历",
                                payload={
                                    "page_index": page_index,
                                    "opened_index": opened_index,
                                    "url": current_url or "",
                                },
                            )
                            save_task_checkpoint(
                                active_task_id,
                                "extract_resume",
                                TaskStep.EXTRACT_RESUME,
                                {"page_index": page_index, "opened_index": opened_index, "url": current_url or ""},
                            )
                            refresh_all()
                            QTimer.singleShot(_random_delay_ms(1800, 3600), lambda: analyze_current_resume(auto_advance=True))
                            return
                        QTimer.singleShot(500, lambda: validate_clicked_candidate(open_payload, popup_before, retries - 1))

                    def handle_click_open(click_payload: dict[str, Any] | None) -> None:
                        payload_open = _js_payload(click_payload)
                        if not payload_open.get("clicked"):
                            db.log(
                                "任务模式：点击候选人卡片失败",
                                task_id=active_task_id,
                                account_id=account_id,
                                step=TaskStep.OPEN_RESUME.value,
                                level="warning",
                                payload=payload_open,
                            )
                            skip_current_candidate_and_continue(
                                "未能点击候选人卡片",
                                payload_open.get("reason") or "打开候选人的 JS 未找到可点击目标。",
                                "系统已跳过该候选人，继续打开下一位。",
                                payload=payload_open,
                            )
                            return
                        href_from_click = str(payload_open.get("href") or "").strip()
                        if href_from_click and re.search(r"/resume/showresumedetail/?|res_id_encode=", href_from_click, re.I):
                            open_url_with_account_profile(
                                href_from_click,
                                "候选人详情",
                                state.current_account_id,
                                log_reason="任务模式：点击脚本返回详情链接，直接跳转",
                            )
                        validate_clicked_candidate(payload_open, popup_before)

                    append_log(
                        [
                            "本页候选人抓取完成，但未找到可直跳的简历详情链接",
                            f"岗位：{row['title']}",
                            f"页码：第 {page_index} 页",
                            "系统将改为点击首位候选人卡片打开详情。",
                        ]
                    )
                    state.current_list_page_index = page_index
                    state.engine.set_step(
                        active_task_id,
                        TaskStep.OPEN_RESUME,
                        "任务模式：本页候选人抓取完成，直接点击首个候选人",
                        payload={
                            "saved_count": saved,
                            "result_count_text": result_count_text or "",
                            "page_index": page_index,
                            "href": "",
                        },
                    )
                    save_task_checkpoint(
                        active_task_id,
                        "open_candidate",
                        TaskStep.OPEN_RESUME,
                        {"page_index": page_index, "target_index": 0, "href": ""},
                    )
                    refresh_all()
                    web.switch_home()
                    popup_before = state.popup_open_count
                    QTimer.singleShot(
                        _random_delay_ms(1200, 2400),
                        lambda: page_adapter.open_candidate_by_index(0, handle_click_open),
                    )
                    return
                state.current_list_page_index = page_index
                state.engine.set_step(
                    active_task_id,
                    TaskStep.OPEN_RESUME,
                    "任务模式：本页候选人抓取完成，自动打开首个候选人",
                    payload={
                        "saved_count": saved,
                        "result_count_text": result_count_text or "",
                        "page_index": page_index,
                        "href": detail_href,
                    },
                )
                save_task_checkpoint(
                    active_task_id,
                    "open_candidate",
                    TaskStep.OPEN_RESUME,
                    {"page_index": page_index, "target_index": 0, "href": detail_href},
                )
                refresh_all()
                append_log(
                    [
                        "本页候选人抓取完成，自动打开首个候选人",
                        f"页数：第 {page_index} 页",
                        f"详情链接：{detail_href}",
                    ]
                )
                open_url_with_account_profile(
                    detail_href,
                    "候选人详情",
                    state.current_account_id,
                    log_reason="本页候选人抓取完成后打开首个候选人",
                )
                QTimer.singleShot(_random_delay_ms(1600, 3200), lambda: wait_for_detail_open(detail_href))
                return

            if current_first_href:
                QTimer.singleShot(300, lambda href=current_first_href: open_candidate(href))

        page_adapter.collect_cards(handle)

    def fill_search() -> None:
        row = current_job_row()
        if not row:
            message("请先选择岗位。")
            return
        keywords = " ".join(loads(row["keywords"], []))
        page_adapter.fill_search_keywords(keywords, lambda value: append_log([f"搜索填充完成：{value}"]))

    fill_search_btn.clicked.connect(fill_search)

    def collect_cards() -> None:
        collect_candidate_cards_strict(task_mode=False)

    collect_cards_btn.clicked.connect(collect_cards)

    def schedule_next_candidate_open(task_id: int, delay_ms: int = 900) -> None:
        if not task_id:
            return
        if _task_automation_blocked(state, task_id):
            append_log(["已跳过进入下一位候选人", f"任务 ID：{task_id}", f"任务状态：{_task_block_label(state, task_id)}"])
            return
        save_task_checkpoint(task_id, "next_candidate", TaskStep.NEXT_CANDIDATE, {"delay_ms": delay_ms})

        def do_open_next() -> None:
            if _task_automation_blocked(state, task_id):
                append_log(["已取消打开下一位候选人", f"任务 ID：{task_id}", f"任务状态：{_task_block_label(state, task_id)}"])
                return
            state.executor.log_open_next_execution(
                TaskExecutionContext(
                    task_id=task_id,
                    account_id=state.current_account_id,
                    next_candidate_index=int(state.next_candidate_index or 0),
                    current_page_card_count=int(state.current_page_card_count or 0),
                    current_url=web.url().toString(),
                    last_candidate_id=state.last_candidate_id,
                    last_opened_candidate_index=state.last_opened_candidate_index,
                )
            )
            route_collect_and_open_candidate(
                None,
                close_current_detail=True,
                task_mode=True,
            )

        timer = QTimer(window)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: (timer.deleteLater(), do_open_next()))
        timer.start(_task_chain_delay_ms(delay_ms))

    def advance_to_next_candidate(reason: str, payload: dict[str, Any] | None = None, delay_ms: int = 900) -> None:
        task_id = state.current_task_id
        payload = payload or {}
        if not task_id:
            return
        if _task_automation_blocked(state, task_id):
            append_log(["已跳过进入下一位候选人", f"任务 ID：{task_id}", f"任务状态：{_task_block_label(state, task_id)}"])
            return
        next_index = int(state.next_candidate_index or 0)
        context = TaskExecutionContext(
            task_id=task_id,
            account_id=state.current_account_id,
            next_candidate_index=next_index,
            current_page_card_count=int(state.current_page_card_count or 0),
            current_url=web.url().toString(),
            last_candidate_id=state.last_candidate_id,
            last_opened_candidate_index=state.last_opened_candidate_index,
        )
        step_result = state.executor.set_next_candidate_step(context, reason, payload)
        save_task_checkpoint(task_id, "next_candidate", TaskStep.NEXT_CANDIDATE, payload)
        append_log(step_result.lines)
        refresh_all()
        schedule_next_candidate_open(int(task_id), delay_ms)

    def analyze_current_resume(auto_advance: bool = False) -> None:
        task_id = state.current_task_id
        if _task_automation_blocked(state, task_id):
            append_log(["已跳过抓简历", f"任务 ID：{task_id or '-'}", f"任务状态：{_task_block_label(state, task_id)}"])
            return
        row = current_job_row()
        if not row:
            message("请先选择岗位。")
            return
        job_id = int(row["id"])
        account_id = state.current_account_id
        if task_id:
            save_task_checkpoint(task_id, "extract_resume", TaskStep.EXTRACT_RESUME, {"auto_advance": bool(auto_advance), "url": web.url().toString()})

        def inspect() -> None:
            inspect_state = {"done": False}

            def normalize_diagnostics(diagnostics: dict[str, Any] | None) -> dict[str, Any]:
                data = dict(diagnostics or {})
                text = str(data.get("resumeText") or "")
                data.setdefault("url", web.url().toString())
                page_title = ""
                try:
                    page_title = web.current_view().title() or ""
                except Exception:
                    page_title = ""
                data.setdefault("title", page_title)
                if re.search(r"/resume/showresumedetail/?|res_id_encode=", data.get("url") or ""):
                    data["isResumeDetail"] = True
                if text and not data.get("textLength"):
                    data["textLength"] = len(text)
                if text and not data.get("lineCount"):
                    data["lineCount"] = len([line for line in text.splitlines() if line.strip()])
                if text and not data.get("matchedSections"):
                    section_words = [
                        "简历信息",
                        "基本信息",
                        "求职意向",
                        "工作经历",
                        "项目经历",
                        "教育经历",
                        "资格证书",
                        "语言能力",
                        "我的技能",
                        "自我评价",
                    ]
                    data["matchedSections"] = [word for word in section_words if word in text]
                if text and not data.get("matchedActions"):
                    data["matchedActions"] = [
                        word for word in ("立即沟通", "沟通", "聊一聊", "联系", "查看联系方式") if word in text
                    ]
                if text and data.get("projectVisible") is None:
                    data["projectVisible"] = text.count("项目职务：")
                if text and not data.get("hasAttachmentResume"):
                    data["hasAttachmentResume"] = "附件简历" in text or "已上传附件简历" in text
                if text and not data.get("hasUnauthorizedAttachment"):
                    data["hasUnauthorizedAttachment"] = any(token in text for token in ("索要附件", "索要简历", "向TA索要"))
                return data

            def finish_inspect(diagnostics: dict[str, Any] | None) -> None:
                if inspect_state["done"]:
                    return
                inspect_state["done"] = True
                try:
                    handle_diagnostics(normalize_diagnostics(diagnostics))
                except Exception as exc:
                    append_log(["简历验收处理异常", str(exc)])
                    if auto_advance and task_id:
                        skip_current_candidate_and_continue(
                            "简历验收处理异常",
                            str(exc),
                            "系统已跳过该候选人，继续下一位。",
                            payload={"url": web.url().toString(), "error": str(exc)},
                        )

            def with_dom_diagnostics(diagnostics: dict[str, Any] | None) -> None:
                if isinstance(diagnostics, dict) and diagnostics.get("error") and task_id:
                    db.log(
                        "简历检查脚本返回异常",
                        task_id=task_id,
                        account_id=account_id,
                        level="warning",
                        step=TaskStep.EXTRACT_RESUME.value,
                        payload=diagnostics,
                    )
                if not isinstance(diagnostics, dict) or not str(diagnostics.get("resumeText") or "").strip():
                    fallback_state = {"done": False}

                    def fallback_timeout() -> None:
                        if fallback_state["done"] or inspect_state["done"]:
                            return
                        fallback_state["done"] = True
                        if task_id:
                            db.log(
                                "简历页面文本兜底超时",
                                task_id=task_id,
                                account_id=account_id,
                                level="warning",
                                step=TaskStep.EXTRACT_RESUME.value,
                                payload={"url": web.url().toString(), "diagnostics": diagnostics or {}},
                            )
                        finish_inspect(diagnostics)

                    def with_plain_text(plain_text: str) -> None:
                        if fallback_state["done"] or inspect_state["done"]:
                            return
                        fallback_state["done"] = True
                        data = dict(diagnostics or {})
                        plain_text = plain_text or ""
                        if plain_text:
                            data["resumeText"] = plain_text
                            data["textLength"] = len(plain_text)
                            data["lineCount"] = len([line for line in plain_text.splitlines() if line.strip()])
                            if task_id:
                                db.log(
                                    "简历检查脚本正文为空，已使用页面文本兜底",
                                    task_id=task_id,
                                    account_id=account_id,
                                    level="warning",
                                    step=TaskStep.EXTRACT_RESUME.value,
                                    payload={"url": web.url().toString(), "text_length": len(plain_text)},
                                )
                        finish_inspect(data)

                    QTimer.singleShot(3000, fallback_timeout)
                    try:
                        web.page().toPlainText(with_plain_text)
                    except Exception as exc:
                        fallback_state["done"] = True
                        data = dict(diagnostics or {})
                        data["fallbackError"] = str(exc)
                        finish_inspect(data)
                    return
                finish_inspect(diagnostics)

            def inspect_timeout() -> None:
                if inspect_state["done"]:
                    return
                current_url = web.url().toString()
                append_log(["简历检查脚本超时，按当前 URL 做失败验收", f"当前 URL：{current_url or '-'}"])
                if task_id:
                    db.log(
                        "简历检查脚本超时",
                        task_id=task_id,
                        account_id=account_id,
                        level="warning",
                        step=TaskStep.EXTRACT_RESUME.value,
                        payload={"url": current_url or "", "auto_advance": auto_advance},
                    )
                finish_inspect(
                    {
                        "url": current_url,
                        "title": "",
                        "isResumeDetail": bool(re.search(r"/resume/showresumedetail/?|res_id_encode=", current_url, re.I)),
                        "resumeText": "",
                        "textLength": 0,
                        "lineCount": 0,
                        "matchedSections": [],
                        "matchedActions": [],
                    }
                )

            if auto_advance:
                QTimer.singleShot(10000, inspect_timeout)
            page_adapter.inspect_resume(with_dom_diagnostics)

        def prepared(prep: dict[str, Any] | None) -> None:
            if _task_automation_blocked(state, task_id):
                append_log(["已跳过简历准备检查", f"任务 ID：{task_id or '-'}", f"任务状态：{_task_block_label(state, task_id)}"])
                return
            prep = prep or {}
            current_url = prep.get("url") or web.url().toString()
            if re.search(r"/resume/showresumedetail/?|res_id_encode=", current_url):
                prep["isResumeDetail"] = True
            if auto_advance and task_id and not prep.get("isResumeDetail"):
                append_log(
                    [
                        "自动任务准备抓简历时发现当前不是详情页，已拦截",
                        f"当前 URL：{current_url or '-'}",
                        "系统将重新点击当前候选人卡片，不再把搜索页当简历抓取。",
                    ]
                )
                state.engine.set_step(
                    int(task_id),
                    TaskStep.OPEN_RESUME,
                    "任务模式：当前页不是简历详情，重新打开候选人",
                    payload={"url": current_url or "", "next_candidate_index": state.next_candidate_index},
                )
                refresh_all()
                QTimer.singleShot(
                    300,
                    lambda: route_collect_and_open_candidate(
                        None,
                        close_current_detail=False,
                        task_mode=True,
                    ),
                )
                return
            append_log(
                [
                    "页面准备完成",
                    f"是否详情页：{'是' if prep.get('isResumeDetail') else '否'}",
                    f"展开项目经历：{'，'.join(prep.get('clickedExpanders') or []) or '无可展开项'}",
                ]
            )
            QTimer.singleShot(_random_delay_ms(1400, 2800), inspect)

        def handle_diagnostics(diagnostics: dict[str, Any] | None) -> None:
            if _task_automation_blocked(state, task_id):
                append_log(["已跳过简历验收结果处理", f"任务 ID：{task_id or '-'}", f"任务状态：{_task_block_label(state, task_id)}"])
                return
            diagnostics = diagnostics or {}
            text = diagnostics.get("resumeText") or ""
            completeness, warnings, attachment_status = _resume_completeness(diagnostics, text)
            if not diagnostics.get("isResumeDetail") and int(diagnostics.get("visibleCandidateCards") or 0) > 0:
                append_log(
                    [
                        "当前仍在搜索结果页，未进入候选人简历详情。",
                        f"当前 URL：{diagnostics.get('url') or web.url().toString()}",
                        "请先打开候选人卡片（新页面）后再抓取。",
                    ]
                )
                result.setPlainText(
                    "\n".join(
                        [
                            "未进入候选人简历详情页，已停止评分。",
                            f"当前 URL：{diagnostics.get('url') or web.url().toString()}",
                            f"可见候选人卡片数：{diagnostics.get('visibleCandidateCards') or 0}",
                        ]
                    )
                )
                if auto_advance and task_id:
                    skip_current_candidate_and_continue(
                        "抓简历时仍停留在搜索结果页",
                        f"当前 URL：{diagnostics.get('url') or web.url().toString()}；可见候选人卡片数：{diagnostics.get('visibleCandidateCards') or 0}。",
                        "系统已跳过该候选人，继续打开下一位。",
                        payload={
                            "url": diagnostics.get("url") or web.url().toString(),
                            "visible_candidate_cards": diagnostics.get("visibleCandidateCards") or 0,
                        },
                    )
                return
            extracted_name = (diagnostics.get("candidateName") or "").strip()
            if not _valid_candidate_name(extracted_name):
                extracted_name = _candidate_name_from_resume_text(text)
            name = extracted_name or _first_non_empty_line(text)
            url = diagnostics.get("url") or web.url().toString()
            resume_status = "fetched"
            if completeness.startswith("低"):
                resume_status = "incomplete"
            elif attachment_status == "needs_request":
                resume_status = "needs_attachment"

            candidate_id = db.upsert_candidate(
                {
                    "job_id": job_id,
                    "source_account_id": account_id,
                    "source_task_id": task_id,
                    "name": name,
                    "profile_url": url,
                    "resume_status": resume_status,
                }
            )
            snapshot_id = db.add_snapshot(
                {
                    "candidate_id": candidate_id,
                    "job_id": job_id,
                    "account_id": account_id,
                    "task_id": task_id,
                    "url": url,
                    "title": diagnostics.get("title") or "",
                    "text_length": diagnostics.get("textLength") or len(text),
                    "line_count": diagnostics.get("lineCount") or 0,
                    "matched_sections": diagnostics.get("matchedSections") or [],
                    "project_total": diagnostics.get("projectTotal"),
                    "project_visible": diagnostics.get("projectVisible"),
                    "has_attachment_resume": diagnostics.get("hasAttachmentResume"),
                    "has_unauthorized_attachment": diagnostics.get("hasUnauthorizedAttachment"),
                    "completeness": completeness,
                    "warnings": warnings,
                    "resume_text": text,
                    "resume_status": resume_status,
                }
            )

            state.last_candidate_id = candidate_id
            state.last_resume_text = text
            state.last_greeting = ""
            if warnings:
                db.log("简历抓取存在风险", task_id=task_id, account_id=account_id, level="warning", step=TaskStep.EXTRACT_RESUME.value, payload={"warnings": warnings})
            if attachment_status == "needs_request":
                db.alert(
                    reason="附件简历需人工决策",
                    detail="候选人存在附件简历，但索要附件可能触发沟通或权益消耗，系统未自动处理。",
                    action_hint="按当前正文评分，或人工决定是否索要附件后继续。",
                    task_id=task_id,
                    account_id=account_id,
                    candidate_id=candidate_id,
                )
            matched_sections = diagnostics.get("matchedSections") or []
            matched_actions = diagnostics.get("matchedActions") or []
            project_total = diagnostics.get("projectTotal")
            project_visible = diagnostics.get("projectVisible") or 0
            body_preview = str(diagnostics.get("bodyPreview") or re.sub(r"\s+", " ", text).strip()[:500])
            validation_passed = bool(diagnostics.get("isResumeDetail")) and not completeness.startswith("低")
            validation_status = "通过" if validation_passed else "未通过"

            result.setPlainText(
                "\n".join(
                    [
                        "当前简历抓取验收报告",
                        f"状态：{validation_status}",
                        f"候选人：{name or '当前页面'}",
                        f"候选人 ID：{candidate_id}，快照 ID：{snapshot_id}",
                        f"简历完整性：{completeness}",
                        f"详情页：{'是' if diagnostics.get('isResumeDetail') else '否'}",
                        f"URL：{url}",
                        f"标题：{diagnostics.get('title') or '-'}",
                        f"页面文本：{diagnostics.get('textLength')} 字 / {diagnostics.get('lineCount')} 行",
                        f"识别模块：{'，'.join(matched_sections) or '-'}",
                        f"页面动作：{'，'.join(matched_actions) or '-'}",
                        f"项目经历：{project_visible}/{project_total or '未知'} 段",
                        f"附件简历：{'存在但未授权/需索要' if attachment_status == 'needs_request' else ('存在' if attachment_status == 'present' else '未发现')}",
                        f"抓取风险：{'；'.join(warnings) if warnings else '-'}",
                        "",
                        "正文预览：",
                        body_preview or "-",
                    ]
                )
            )
            db.log(
                "当前简历抓取验收结果",
                task_id=task_id,
                account_id=account_id,
                step=TaskStep.EXTRACT_RESUME.value,
                level="info" if validation_passed else "warning",
                payload={
                    "validation_status": validation_status,
                    "validation_passed": validation_passed,
                    "candidate_id": candidate_id,
                    "snapshot_id": snapshot_id,
                    "name": name,
                    "url": url,
                    "text_length": diagnostics.get("textLength") or len(text),
                    "line_count": diagnostics.get("lineCount") or 0,
                    "completeness": completeness,
                    "matched_sections": matched_sections,
                    "matched_actions": matched_actions,
                    "project_total": project_total,
                    "project_visible": project_visible,
                    "attachment_status": attachment_status,
                    "warnings": warnings,
                },
            )
            append_log(
                [
                    "当前简历抓取验收完成",
                    f"状态：{validation_status}",
                    f"完整性：{completeness}",
                    f"识别模块：{'，'.join(matched_sections) or '-'}",
                    f"项目经历：{project_visible}/{project_total or '未知'}",
                    f"附件状态：{attachment_status}",
                    "已保存快照，但未评分、未生成话术。",
                ]
            )
            if task_id:
                decision = decide_resume_validation(
                    validation_passed=validation_passed,
                    auto_advance=auto_advance,
                    completeness=completeness,
                    warnings=warnings,
                )
                if decision.action == WorkflowAction.SCORE_RESUME:
                    state.engine.set_step(
                        task_id,
                        TaskStep.SCORE,
                        f"任务模式：{decision.reason}",
                        payload={"candidate_id": candidate_id, "snapshot_id": snapshot_id},
                    )
                    save_task_checkpoint(task_id, "score_resume", TaskStep.SCORE, {"candidate_id": candidate_id, "snapshot_id": snapshot_id})
                    refresh_all()
                    QTimer.singleShot(_random_delay_ms(1800, 3600), lambda task_id=task_id: score_last_resume(task_id=task_id))
                elif decision.action == WorkflowAction.PAUSE_FOR_MANUAL and validation_passed:
                    state.engine.pause_for_user(
                        task_id,
                        HumanIntervention(
                            reason=decision.reason,
                            detail=f"已保存简历快照 {snapshot_id}，完整性：{completeness}。",
                            action_hint="请确认左侧简历抓取验收报告无误后，再进入评分步骤。",
                            severity="info",
                        ),
                    )
                else:
                    if decision.action == WorkflowAction.SKIP_CANDIDATE:
                        skip_current_candidate_and_continue(
                            decision.reason,
                            decision.detail,
                            "系统已保存能抓到的快照并跳过该候选人，继续下一位。",
                            candidate_id=candidate_id,
                            payload={
                                "candidate_id": candidate_id,
                                "snapshot_id": snapshot_id,
                                "completeness": completeness,
                                "warnings": warnings,
                                "url": url,
                            },
                        )
                    else:
                        state.engine.pause_for_user(
                            task_id,
                            HumanIntervention(
                                reason=decision.reason,
                                detail=decision.detail,
                                action_hint="请检查右侧页面是否为完整简历详情页，必要时手工展开后重试。",
                            ),
                        )
            refresh_all()

        append_log(["开始分析当前简历"])
        if task_id:
            db.log(
                "开始分析当前简历",
                task_id=task_id,
                account_id=account_id,
                step=TaskStep.EXTRACT_RESUME.value,
                payload={"auto_advance": auto_advance, "url": web.url().toString()},
            )
        prepare_state = {"done": False}

        def prepared_once(prep: dict[str, Any] | None) -> None:
            if prepare_state["done"]:
                return
            prepare_state["done"] = True
            if isinstance(prep, dict) and prep.get("error") and task_id:
                db.log(
                    "简历准备脚本返回异常",
                    task_id=task_id,
                    account_id=account_id,
                    level="warning",
                    step=TaskStep.EXTRACT_RESUME.value,
                    payload=prep,
                )
            prepared(prep)

        def prepare_timeout() -> None:
            if prepare_state["done"]:
                return
            current_url = web.url().toString()
            append_log(["简历准备脚本超时，改用当前页面文本继续验收", f"当前 URL：{current_url or '-'}"])
            if task_id:
                db.log(
                    "简历准备脚本超时，改用当前页面文本继续验收",
                    task_id=task_id,
                    account_id=account_id,
                    level="warning",
                    step=TaskStep.EXTRACT_RESUME.value,
                    payload={"url": current_url or "", "auto_advance": auto_advance},
                )
            prepared_once(
                {
                    "url": current_url,
                    "isResumeDetail": bool(re.search(r"/resume/showresumedetail/?|res_id_encode=", current_url, re.I)),
                    "clickedExpanders": [],
                    "prepareTimeout": True,
                }
            )

        if auto_advance:
            QTimer.singleShot(10000, prepare_timeout)
        page_adapter.prepare_resume(prepared_once)

    analyze_resume_btn.clicked.connect(analyze_current_resume)
    route_extract_btn.clicked.connect(lambda: analyze_current_resume(auto_advance=True))

    def score_last_resume(*, task_id: int | None = None) -> None:
        active_task_id = task_id or state.current_task_id
        if _task_automation_blocked(state, active_task_id):
            append_log(["已跳过评分", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
            return
        row = current_job_row()
        if not row:
            message("请先选择岗位。")
            return
        if not state.last_resume_text or not state.last_candidate_id:
            message("请先点击“5 抓当前简历”，完成简历抓取验收。")
            return
        candidate_row = db.fetch_one("SELECT * FROM candidates WHERE id = ?", (state.last_candidate_id,))
        if not candidate_row:
            message("最近抓取的候选人记录不存在，请重新抓当前简历。")
            return
        candidate = Candidate(
            name=candidate_row["name"] or "当前页面候选人",
            title=candidate_row["title"] or "当前页面候选人",
            profile_url=candidate_row["profile_url"] or "",
            resume_text=state.last_resume_text,
        )
        job_snapshot = dict(row)
        candidate_id = int(state.last_candidate_id)
        job_id = int(row["id"])
        account_id = state.current_account_id
        dry_run_flag = state.current_task_dry_run if state.current_task_dry_run is not None else bool(row["dry_run"])
        threshold = state.current_task_min_score if state.current_task_min_score is not None else int(row["min_score"])
        auto_greet = state.current_task_auto_greet if state.current_task_auto_greet is not None else bool(row["auto_greet"])
        use_ai_scoring = state.current_task_use_ai_scoring if state.current_task_use_ai_scoring is not None else True
        if active_task_id:
            save_task_checkpoint(active_task_id, "score_resume", TaskStep.SCORE, {"candidate_id": candidate_id})
        result.setPlainText("当前简历评分正在后台计算，请稍候...")

        work_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                score = CandidateScorer(job_config(job_snapshot), state.env).score(candidate, use_llm=use_ai_scoring)
                greeting = GreetingGenerator(job_config(job_snapshot), greeting_config(job_snapshot), state.env).build(candidate, score)
                work_q.put({"ok": True, "score": score.model_dump(), "greeting": greeting})
            except Exception as exc:
                work_q.put({"ok": False, "error": str(exc)})

        def apply_worker_result() -> None:
            if _task_automation_blocked(state, active_task_id):
                append_log(["已跳过评分结果落库", f"任务 ID：{active_task_id or '-'}", f"任务状态：{_task_block_label(state, active_task_id)}"])
                return
            try:
                payload = work_q.get_nowait()
            except queue.Empty:
                QTimer.singleShot(120, apply_worker_result)
                return
            if not payload.get("ok"):
                error_text = str(payload.get("error") or "unknown")
                result.setPlainText(f"当前简历评分失败：{error_text}")
                append_log(["当前简历评分失败", error_text])
                if active_task_id:
                    skip_current_candidate_and_continue(
                        "当前简历评分失败",
                        f"评分后台计算失败：{error_text}",
                        "系统已跳过该候选人，继续下一位。",
                        candidate_id=state.last_candidate_id,
                        payload={"error": error_text},
                    )
                refresh_all()
                return

            score = ScoreResult.model_validate(payload["score"])
            greeting = str(payload.get("greeting") or "")
            db.add_score(candidate_id, job_id, score.score, score.matched_keywords, score.missing_keywords, score.risks, score.summary)
            greeting_log_id = db.add_greeting_log(candidate_id, active_task_id, account_id, greeting, "generated", dry_run_flag)
            db.execute(
                """
                UPDATE candidates
                SET score = ?, score_summary = ?, matched_keywords = ?, missing_keywords = ?,
                    risks = ?, greeting = ?, greeting_status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    score.score,
                    score.summary,
                    json.dumps(score.matched_keywords, ensure_ascii=False),
                    json.dumps(score.missing_keywords, ensure_ascii=False),
                    json.dumps(score.risks, ensure_ascii=False),
                    greeting,
                    "generated",
                    candidate_id,
                ),
            )
            state.last_greeting = greeting
            state.last_greeting_log_id = greeting_log_id
            should_greet = score.score >= threshold
            result.setPlainText(
                "\n".join(
                    [
                        "当前简历评分结果",
                        f"候选人：{candidate.name}",
                        f"候选人 ID：{candidate_id}",
                        f"岗位：{row['title']}",
                        f"分数：{score.score}/100",
                        f"阈值：{threshold}",
                        f"评分模式：{'AI' if use_ai_scoring else '关键词规则'}",
                        f"判断：{'达到阈值' if should_greet else '未达到阈值'}",
                        f"自动打招呼：{'开启' if auto_greet else '关闭'}",
                        f"Dry-run：{'是' if dry_run_flag else '否'}",
                        f"匹配项：{'，'.join(score.matched_keywords) or '-'}",
                        f"缺失项：{'，'.join(score.missing_keywords) or '-'}",
                        f"风险：{'，'.join(score.risks) or '-'}",
                        f"摘要：{score.summary}",
                        "",
                        "打招呼话术：",
                        greeting,
                    ]
                )
            )
            db.log(
                "当前简历评分完成",
                task_id=active_task_id,
                account_id=account_id,
                step=TaskStep.SCORE.value,
                payload={
                    "candidate_id": candidate_id,
                    "score": score.score,
                    "threshold": threshold,
                    "use_ai_scoring": use_ai_scoring,
                    "should_greet": should_greet,
                    "auto_greet": auto_greet,
                    "dry_run": dry_run_flag,
                    "scoring_mode": "ai" if use_ai_scoring else "rules",
                    "greeting_log_id": greeting_log_id,
                    "matched_keywords": score.matched_keywords,
                    "missing_keywords": score.missing_keywords,
                    "risks": score.risks,
                },
            )
            append_log(
                [
                    "当前简历评分完成",
                    f"分数：{score.score}/100；阈值：{threshold}",
                    f"评分模式：{'AI' if use_ai_scoring else '关键词规则'}",
                    f"判断：{'达到阈值' if should_greet else '未达到阈值'}",
                    f"自动打招呼：{'开启' if auto_greet else '关闭'}；Dry-run：{'是' if dry_run_flag else '否'}",
                    (
                        "达到阈值且开启自动打招呼，将执行沟通流程。"
                        if should_greet and auto_greet
                        else "已生成话术但未发送。"
                    ),
                ]
            )
            if should_greet and auto_greet:
                if active_task_id:
                    save_task_checkpoint(active_task_id, "send_greeting", TaskStep.SEND_GREETING, {"candidate_id": candidate_id, "greeting_log_id": greeting_log_id})
                QTimer.singleShot(_random_delay_ms(2200, 4500), lambda candidate_id=candidate_id: fill_or_send_greeting(auto_trigger=True, expected_candidate_id=candidate_id))
            elif active_task_id:
                advance_to_next_candidate(
                    "任务模式：当前简历未达阈值，自动进入下一位候选人",
                    payload={
                        "candidate_id": candidate_id,
                        "score": score.score,
                        "threshold": threshold,
                        "should_greet": should_greet,
                    },
                    delay_ms=900,
                )
            refresh_all()

        threading.Thread(target=worker, daemon=True).start()
        QTimer.singleShot(80, apply_worker_result)

    score_resume_btn.clicked.connect(score_last_resume)

    def view_last_resume() -> None:
        if not state.last_resume_text:
            message("还没有抓取到简历全文。")
            return
        result.setPlainText(f"最近一次抓取全文\n字符数：{len(state.last_resume_text)}\n\n{state.last_resume_text}")

    view_resume_btn.clicked.connect(view_last_resume)

    def fill_or_send_greeting(*, auto_trigger: bool = False, expected_candidate_id: int | None = None) -> None:
        task_id = state.current_task_id
        if _task_automation_blocked(state, task_id):
            append_log(["已跳过立即沟通", f"任务 ID：{task_id or '-'}", f"任务状态：{_task_block_label(state, task_id)}"])
            return
        row = current_job_row()
        opening_greeting = "你好~我这里有个职位很适合你，待遇优厚，了解一下吗？期待回复！"
        initial_followup = (state.last_greeting or "").strip()
        continued_followup = ""
        if row and "followup_template" in row.keys():
            continued_followup = (row["followup_template"] or "").strip()
        if not initial_followup and not continued_followup:
            message("请先评分生成话术，或在岗位里填写二次补充话术。")
            return
        dry_run = True if not row else (state.current_task_dry_run if state.current_task_dry_run is not None else bool(row["dry_run"]))
        candidate_id = expected_candidate_id or state.last_candidate_id
        account_id = state.current_account_id
        if task_id:
            save_task_checkpoint(task_id, "send_greeting", TaskStep.SEND_GREETING, {"candidate_id": candidate_id})

        def handle(payload: dict[str, Any] | None) -> None:
            if _task_automation_blocked(state, task_id):
                append_log(["已跳过立即沟通结果处理", f"任务 ID：{task_id or '-'}", f"任务状态：{_task_block_label(state, task_id)}"])
                return
            payload = _js_payload(payload)
            opened = bool(payload.get("opened"))
            opening_selected = bool(payload.get("openingSelected"))
            opening_sent = bool(payload.get("openingSent"))
            followup_filled = bool(payload.get("followupFilled"))
            followup_sent = bool(payload.get("followupSent"))
            continued_chat = bool(payload.get("continuedChat") or payload.get("alreadyInChat"))
            followup_already_exists = bool(payload.get("followupAlreadyExists"))
            followup_mode = str(payload.get("followupMode") or ("continued" if continued_chat else "initial"))
            used_followup = continued_followup if followup_mode == "continued" and continued_followup else initial_followup
            status = "failed"
            if followup_already_exists:
                status = "followup_already_exists"
            elif followup_sent and continued_chat:
                status = "continued_followup_sent"
            elif followup_filled and continued_chat and dry_run:
                status = "continued_followup_filled_dry_run"
            elif followup_sent:
                status = "followup_sent"
            elif followup_filled and continued_chat:
                status = "followup_filled_not_sent"
            elif opening_sent and followup_filled and dry_run:
                status = "followup_filled_dry_run"
            elif opening_sent and followup_filled:
                status = "followup_filled_not_sent"
            elif opening_sent:
                status = "opening_sent"
            elif opening_selected and dry_run:
                status = "opening_selected_dry_run"
            elif opening_selected:
                status = "opening_selected_not_sent"
            elif opened:
                status = "opened_not_selected"
            should_write_new_log = bool(
                candidate_id
                and followup_mode == "continued"
                and (followup_filled or followup_sent or followup_already_exists)
            )
            if should_write_new_log:
                state.last_greeting_log_id = db.add_greeting_log(candidate_id, task_id, account_id, used_followup, status, dry_run)
            elif state.last_greeting_log_id:
                db.execute("UPDATE greeting_logs SET status = ? WHERE id = ?", (status, state.last_greeting_log_id))
            elif candidate_id:
                state.last_greeting_log_id = db.add_greeting_log(candidate_id, task_id, account_id, used_followup, status, dry_run)
            if candidate_id:
                db.execute(
                    """
                    UPDATE candidates
                    SET greeting = ?, greeting_status = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (used_followup, status, candidate_id),
                )
            db.log(
                "立即沟通动作结果",
                task_id=task_id,
                account_id=account_id,
                step=TaskStep.SEND_GREETING.value,
                level="info" if (opening_selected or continued_chat or followup_already_exists) else "warning",
                payload={
                    "candidate_id": candidate_id,
                    "auto_trigger": auto_trigger,
                    "status": status,
                    "dry_run": dry_run,
                    "opened": opened,
                    "opening_greeting": opening_greeting,
                    "opening_modal_found": bool(payload.get("openingModalFound")),
                    "opening_selected": opening_selected,
                    "opening_sent": opening_sent,
                    "followup_filled": followup_filled,
                    "followup_sent": followup_sent,
                    "followup_mode": followup_mode,
                    "continued_chat": continued_chat,
                    "followup_already_exists": followup_already_exists,
                    "already_in_chat": bool(payload.get("alreadyInChat")),
                    "reason": payload.get("reason") or "",
                    "error": payload.get("error") or "",
                    "stack": str(payload.get("stack") or "")[:800],
                    "greet_button": payload.get("greetButton") or {},
                    "selected_greeting": payload.get("selectedGreeting") or "",
                    "greeting_options": payload.get("greetingOptions") or [],
                    "no_job_button": payload.get("noJobButton") or {},
                    "editor": payload.get("editor") or {},
                    "send_button": payload.get("sendButton") or {},
                    "send_fallback": payload.get("sendFallback") or "",
                    "editor_after_send": payload.get("editorAfterSend") or "",
                    "send_candidates": payload.get("sendCandidates") or [],
                    "duplicate_check": payload.get("duplicateCheck") or {},
                    "followup_length": payload.get("followupLength") or len(used_followup),
                    "initial_followup_length": len(initial_followup),
                    "continued_followup_length": len(continued_followup),
                },
            )
            result.setPlainText(
                "\n".join(
                    [
                        "立即沟通验收报告",
                        f"候选人 ID：{candidate_id or '-'}",
                        f"触发方式：{'自动' if auto_trigger else '手动'}",
                        f"Dry-run：{'是' if dry_run else '否'}",
                        f"状态：{status}",
                        f"打开沟通入口：{'是' if opened else '否'}",
                        f"开聊弹窗：{'是' if payload.get('openingModalFound') else '否'}",
                        f"已在沟通窗口：{'是' if continued_chat else '否'}",
                        f"选中指定开聊语：{'是' if opening_selected else '否'}",
                        f"第一句已发出：{'是' if opening_sent else '否'}",
                        f"补充类型：{'二次补充' if followup_mode == 'continued' else '首次补充'}",
                        f"补充话术已填入：{'是' if followup_filled else '否'}",
                        f"补充话术已发送：{'是' if followup_sent else '否'}",
                        f"补充话术已存在：{'是' if followup_already_exists else '否'}",
                        f"原因：{payload.get('reason') or '-'}",
                        f"错误：{payload.get('error') or '-'}",
                        f"打招呼按钮：{json.dumps(payload.get('greetButton') or {}, ensure_ascii=False)}",
                        f"不选择职位按钮：{json.dumps(payload.get('noJobButton') or {}, ensure_ascii=False)}",
                        f"输入框：{json.dumps(payload.get('editor') or {}, ensure_ascii=False)}",
                        f"发送按钮：{json.dumps(payload.get('sendButton') or {}, ensure_ascii=False)}",
                        "",
                        "指定开聊语：",
                        opening_greeting,
                        "",
                        "识别到的开聊语：",
                        *[str(item) for item in (payload.get("greetingOptions") or [])[:8]],
                        "",
                        "补充话术：",
                        used_followup,
                    ]
                )
            )
            append_log(
                [
                    "立即沟通动作完成",
                    f"状态：{status}",
                    f"开聊语选中：{'是' if opening_selected else '否'}；第一句发送：{'是' if opening_sent else '否'}",
                    f"补充类型：{'二次补充' if followup_mode == 'continued' else '首次补充'}",
                    f"继续沟通：{'是' if continued_chat else '否'}；补充填入：{'是' if followup_filled else '否'}；补充发送：{'是' if followup_sent else '否'}；已存在：{'是' if followup_already_exists else '否'}",
                    f"Dry-run：{'是' if dry_run else '否'}",
                    f"原因：{payload.get('reason') or '-'}",
                    f"错误：{payload.get('error') or '-'}",
                ]
            )
            if task_id:
                decision = decide_greeting_result(
                    auto_trigger=auto_trigger,
                    opening_selected=opening_selected,
                    opening_sent=opening_sent,
                    followup_filled=followup_filled,
                    followup_sent=followup_sent,
                    followup_already_exists=followup_already_exists,
                )
                if decision.action == WorkflowAction.CONTINUE:
                    advance_to_next_candidate(
                        f"任务模式：{decision.reason}",
                        payload={
                            "candidate_id": candidate_id,
                            "status": status,
                            "dry_run": dry_run,
                        },
                        delay_ms=900,
                    )
                else:
                    if decision.action == WorkflowAction.SKIP_CANDIDATE:
                        skip_current_candidate_and_continue(
                            decision.reason,
                            f"沟通状态：{status}；Dry-run：{'是' if dry_run else '否'}；原因：{payload.get('reason') or '-'}；错误：{payload.get('error') or '-'}。",
                            "系统已记录沟通失败并跳过该候选人，继续下一位。",
                            candidate_id=candidate_id,
                            payload={
                                "status": status,
                                "dry_run": dry_run,
                                "reason": payload.get("reason") or "",
                                "error": payload.get("error") or "",
                            },
                        )
                    else:
                        state.engine.pause_for_user(
                            task_id,
                            HumanIntervention(
                                reason=decision.reason,
                                detail=f"沟通状态：{status}；Dry-run：{'是' if dry_run else '否'}。",
                                action_hint="请确认右侧沟通窗口和左侧验收报告；处理完后可进入下一位候选人。",
                                severity="info" if opening_selected else "warning",
                            ),
                        )
            refresh_all()

        result_var = "__liepin_greet_result__"
        page_adapter.greet(
            opening_greeting,
            initial_followup,
            continued_followup,
            dry_run,
            lambda value: handle(_js_payload(value) or {"error": "立即沟通脚本超时，未拿到有效回传"}),
            result_var=result_var,
            poll_interval_ms=200,
            max_poll_attempts=80,
        )

    greet_btn.clicked.connect(lambda: fill_or_send_greeting())

    mode_bar = QWidget()
    mode_bar.setFixedHeight(48)
    mode_layout = QHBoxLayout(mode_bar)
    mode_layout.setContentsMargins(12, 8, 12, 8)
    mode_layout.addWidget(QLabel("状态"))
    mode_status_label = QLabel("空闲")
    mode_layout.addWidget(mode_status_label)
    mode_layout.addStretch(1)
    debug_btn = QPushButton("调试")
    mode_layout.addWidget(debug_btn)
    debug_btn.clicked.connect(debug_dialog.show)

    root.addWidget(tabs)
    root.addWidget(browser_panel)
    root.setStretchFactor(0, 0)
    root.setStretchFactor(1, 1)
    central = QWidget()
    central_layout = QVBoxLayout(central)
    central_layout.setContentsMargins(0, 0, 0, 0)
    central_layout.addWidget(mode_bar)
    central_layout.addWidget(root, stretch=1)
    window.setCentralWidget(central)

    db.log("应用启动")
    refresh_all()
    window.show()

    def startup_open() -> None:
        if account_combo.count() > 0:
            use_account(account_combo.currentData())
            append_log(["启动完成：已选择默认账号并打开找人页"])
        else:
            append_log(["启动完成：暂无账号，已打开找人页登录入口"])
        if state.search_click_hints:
            append_log(
                [
                    "已加载搜索按钮录制指纹",
                    f"条数：{len(state.search_click_hints)}",
                    f"来源：{state.search_click_hints_source or '-'}",
                ]
            )
        else:
            append_log(["未加载到搜索按钮录制指纹", "当前会使用内置搜索按钮识别逻辑；录制仅作为辅助诊断。"])
        open_search_page()

    QTimer.singleShot(0, startup_open)
    return app.exec()


def main() -> None:
    raise SystemExit(build_app())


if __name__ == "__main__":
    main()
