from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkflowAction(str, Enum):
    CONTINUE = "continue"
    RETRY_OPEN = "retry_open"
    PAGE_NEXT = "page_next"
    SKIP_CANDIDATE = "skip_candidate"
    FINISH_TASK = "finish_task"
    SCORE_RESUME = "score_resume"
    PAUSE_FOR_MANUAL = "pause_for_manual"


@dataclass(frozen=True)
class WorkflowDecision:
    action: WorkflowAction
    reason: str
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def should_skip_candidate(self) -> bool:
        return self.action == WorkflowAction.SKIP_CANDIDATE

    @property
    def should_finish_task(self) -> bool:
        return self.action == WorkflowAction.FINISH_TASK


@dataclass(frozen=True)
class ConditionApplyValidation:
    validation_passed: bool
    validation_status: str
    passed_target_keys: list[str]
    failed_target_keys: list[str]
    blocking_failed_target_keys: list[str]
    soft_failed_target_keys: list[str]


DEFAULT_BLOCKING_CONDITION_KEYS = frozenset({"keywords", "position_keywords", "age_min", "age_max"})


def decide_condition_apply_validation(
    *,
    target_keys: list[str],
    applied_keys: list[str],
    has_apply_error: bool,
    blocking_keys: set[str] | frozenset[str] = DEFAULT_BLOCKING_CONDITION_KEYS,
) -> ConditionApplyValidation:
    applied_set = set(applied_keys)
    failed_target_keys = [key for key in target_keys if key not in applied_set]
    passed_target_keys = [key for key in target_keys if key in applied_set]
    blocking_failed_target_keys = [key for key in failed_target_keys if key in blocking_keys]
    soft_failed_target_keys = [key for key in failed_target_keys if key not in blocking_keys]
    blocked = bool(has_apply_error or not target_keys or not applied_set or blocking_failed_target_keys)
    if blocked:
        status = "未通过"
    elif soft_failed_target_keys:
        status = "部分通过"
    else:
        status = "通过"
    return ConditionApplyValidation(
        validation_passed=not blocked,
        validation_status=status,
        passed_target_keys=passed_target_keys,
        failed_target_keys=failed_target_keys,
        blocking_failed_target_keys=blocking_failed_target_keys,
        soft_failed_target_keys=soft_failed_target_keys,
    )


def decide_open_candidate_result(payload: dict[str, Any], *, task_mode: bool) -> WorkflowDecision:
    if payload.get("clicked"):
        return WorkflowDecision(WorkflowAction.CONTINUE, "候选人卡片已点击")
    reason = str(payload.get("reason") or payload.get("message") or "没有找到候选人卡片")
    if task_mode and reason == "target_index_out_of_range":
        return WorkflowDecision(
            WorkflowAction.PAGE_NEXT,
            "目标候选人超出当前可点击卡片，准备翻页",
            detail=reason,
            payload={"card_count": int(payload.get("cardCount") or 0)},
        )
    if task_mode:
        return WorkflowDecision(
            WorkflowAction.SKIP_CANDIDATE,
            "未能打开候选人卡片",
            detail=reason,
            payload=payload,
        )
    return WorkflowDecision(
        WorkflowAction.PAUSE_FOR_MANUAL,
        "未能打开候选人卡片",
        detail=reason,
        payload=payload,
    )


def decide_detail_validation(*, is_detail: bool, task_mode: bool, current_url: str) -> WorkflowDecision:
    if is_detail and task_mode:
        return WorkflowDecision(
            WorkflowAction.CONTINUE,
            "候选人详情页验收通过，自动抓当前简历",
            payload={"url": current_url},
        )
    if is_detail:
        return WorkflowDecision(
            WorkflowAction.PAUSE_FOR_MANUAL,
            "候选人详情页待人工确认",
            detail=current_url,
            payload={"url": current_url},
        )
    if task_mode:
        return WorkflowDecision(
            WorkflowAction.SKIP_CANDIDATE,
            "候选人详情页打开验收未通过",
            detail=f"当前 URL 未识别为简历详情页：{current_url or '-'}。",
            payload={"url": current_url},
        )
    return WorkflowDecision(
        WorkflowAction.PAUSE_FOR_MANUAL,
        "候选人详情页打开验收未通过",
        detail=f"当前 URL 未识别为简历详情页：{current_url or '-'}。",
        payload={"url": current_url},
    )


def decide_resume_validation(*, validation_passed: bool, auto_advance: bool, completeness: str, warnings: list[str]) -> WorkflowDecision:
    if validation_passed and auto_advance:
        return WorkflowDecision(
            WorkflowAction.SCORE_RESUME,
            "当前简历抓取完成，自动进入评分",
            payload={"completeness": completeness, "warnings": warnings},
        )
    if validation_passed:
        return WorkflowDecision(
            WorkflowAction.PAUSE_FOR_MANUAL,
            "当前简历待人工确认",
            detail=f"完整性：{completeness}。",
            payload={"completeness": completeness, "warnings": warnings},
        )
    detail = f"简历完整性：{completeness}；风险：{'；'.join(warnings) if warnings else '-'}。"
    if auto_advance:
        return WorkflowDecision(
            WorkflowAction.SKIP_CANDIDATE,
            "当前简历抓取验收未通过",
            detail=detail,
            payload={"completeness": completeness, "warnings": warnings},
        )
    return WorkflowDecision(
        WorkflowAction.PAUSE_FOR_MANUAL,
        "当前简历抓取验收未通过",
        detail=detail,
        payload={"completeness": completeness, "warnings": warnings},
    )


def decide_greeting_result(
    *,
    auto_trigger: bool,
    opening_selected: bool,
    opening_sent: bool,
    followup_filled: bool,
    followup_sent: bool,
    followup_already_exists: bool,
) -> WorkflowDecision:
    accepted = bool(opening_selected or opening_sent or followup_filled or followup_sent or followup_already_exists)
    if auto_trigger and accepted:
        return WorkflowDecision(WorkflowAction.CONTINUE, "立即沟通完成，自动进入下一位候选人")
    if auto_trigger:
        return WorkflowDecision(WorkflowAction.SKIP_CANDIDATE, "立即沟通未完成")
    return WorkflowDecision(WorkflowAction.PAUSE_FOR_MANUAL, "立即沟通待人工确认")


def decide_page_turn_result(*, clicked_next: bool, changed: bool | None = None, retries_left: int | None = None) -> WorkflowDecision:
    if not clicked_next:
        return WorkflowDecision(
            WorkflowAction.FINISH_TASK,
            "已到最后一页",
            detail="系统未找到可点击的下一页按钮，当前任务已按正常结束处理。",
        )
    if changed is True:
        return WorkflowDecision(WorkflowAction.CONTINUE, "下一页验收通过，开始抓取新页候选人")
    if changed is False and retries_left is not None and retries_left <= 0:
        return WorkflowDecision(
            WorkflowAction.FINISH_TASK,
            "下一页点击后页面未变化",
            detail="系统已点击下一页按钮，但列表候选人指纹没有变化。为避免重复处理当前页，已结束当前任务并继续队列。",
        )
    return WorkflowDecision(WorkflowAction.RETRY_OPEN, "等待下一页结果稳定")


def decide_card_collection_validation(
    *,
    has_error: bool,
    saved_count: int,
    result_count_number: int | None = None,
) -> WorkflowDecision:
    validation_passed = (not has_error) and saved_count > 0
    if result_count_number is not None and saved_count > result_count_number:
        validation_passed = False
    if validation_passed:
        return WorkflowDecision(
            WorkflowAction.CONTINUE,
            "候选人列表抓取验收通过",
            payload={"validation_passed": True},
        )
    return WorkflowDecision(
        WorkflowAction.FINISH_TASK,
        "候选人列表抓取验收未通过",
        detail="候选人列表为空、脚本异常，或保存数量超过页面总数。",
        payload={"validation_passed": False},
    )
