from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from liepin_agent.db import Database
from liepin_agent.task_engine import TaskEngine, TaskStep
from liepin_agent.task_runtime import TaskNotice, TaskRuntime


@dataclass(frozen=True)
class TaskExecutionContext:
    task_id: int
    account_id: int | None = None
    next_candidate_index: int = 0
    current_page_card_count: int = 0
    current_url: str = ""
    last_candidate_id: int | None = None
    last_opened_candidate_index: int | None = None


@dataclass(frozen=True)
class StepResult:
    lines: list[str]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskStartResult:
    task_id: int
    job_id: int
    account_id: int
    min_score: int | None
    auto_greet: bool
    dry_run: bool
    use_ai_scoring: bool
    lines: list[str]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkipResult:
    next_candidate_index: int
    candidate_id: int | None
    lines: list[str]


@dataclass(frozen=True)
class SkipAndAdvanceResult:
    next_candidate_index: int
    candidate_id: int | None
    lines: list[str]
    payload: dict[str, Any] = field(default_factory=dict)


class TaskExecutor:
    """Task orchestration primitives that do not depend on Qt widgets."""

    def __init__(self, db: Database, engine: TaskEngine, runtime: TaskRuntime) -> None:
        self.db = db
        self.engine = engine
        self.runtime = runtime

    def start_task(self, task_id: int, *, queue_mode: bool = False) -> TaskStartResult | None:
        task = self.db.fetch_one("SELECT * FROM tasks WHERE id = ?", (int(task_id),))
        if not task:
            return None
        min_score = int(task["greet_min_score"]) if task["greet_min_score"] is not None else None
        use_ai_scoring = bool(task["use_ai_scoring"])
        payload = {
            "queue_mode": bool(queue_mode),
            "max_candidates": int(task["max_candidates"]),
            "target_type": task["target_type"] or "resume",
            "hide_viewed": bool(task["hide_viewed"]),
            "hide_contacted": bool(task["hide_contacted"]),
            "hide_contact_info": bool(task["hide_contact_info"]),
            "greet_min_score": min_score,
            "use_ai_scoring": use_ai_scoring,
            "age_min": task["age_min"],
            "age_max": task["age_max"],
            "priority": int(task["priority"]),
        }
        self.engine.start(int(task_id))
        self.engine.set_step(
            int(task_id),
            TaskStep.OPEN_SEARCH,
            "打开猎聘找人页",
            payload=payload,
        )
        lines = [
            "任务已启动",
            f'任务：{task["name"]}',
            f'账号：{task["account_id"]}，岗位：{task["job_id"]}',
            f'目标：{"沟通人数" if task["target_type"] == "greeting" else "查看简历人数"} {task["max_candidates"]}，阈值：{min_score if min_score is not None else "跟随岗位"}',
            f'年龄：{task["age_min"] or "-"}-{task["age_max"] or "-"}',
            f'结果过滤：已查看={"隐藏" if task["hide_viewed"] else "不隐藏"}，已沟通={"隐藏" if task["hide_contacted"] else "不隐藏"}，已获取联系方式={"隐藏" if task["hide_contact_info"] else "不隐藏"}',
            f'评分模式：{"AI" if use_ai_scoring else "关键词规则"}',
        ]
        return TaskStartResult(
            task_id=int(task_id),
            job_id=int(task["job_id"]),
            account_id=int(task["account_id"]),
            min_score=min_score,
            auto_greet=bool(task["auto_greet"]),
            dry_run=bool(task["dry_run"]),
            use_ai_scoring=use_ai_scoring,
            lines=lines,
            payload=payload,
        )

    def set_next_candidate_step(
        self,
        context: TaskExecutionContext,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> StepResult:
        step_payload = {
            **(payload or {}),
            "next_candidate_index": int(context.next_candidate_index or 0),
            "current_page_card_count": int(context.current_page_card_count or 0),
            "current_url": context.current_url,
        }
        self.engine.set_step(int(context.task_id), TaskStep.OPEN_RESUME, reason, payload=step_payload)
        self.db.log(
            "任务模式：准备打开下一位候选人",
            task_id=int(context.task_id),
            account_id=context.account_id,
            step=TaskStep.OPEN_RESUME.value,
            payload=step_payload,
        )
        return StepResult(
            lines=[
                reason,
                f"任务 ID：{context.task_id}",
                f"下一位序号：第 {int(context.next_candidate_index or 0) + 1} 位",
                f"当前页候选数：{context.current_page_card_count or '-'}",
            ],
            payload=step_payload,
        )

    def log_open_next_execution(self, context: TaskExecutionContext) -> None:
        self.db.log(
            "任务模式：执行打开下一位候选人",
            task_id=int(context.task_id),
            account_id=context.account_id,
            step=TaskStep.OPEN_RESUME.value,
            payload={
                "next_candidate_index": int(context.next_candidate_index or 0),
                "before_url": context.current_url,
            },
        )

    def next_queued_task_id(self, after_task_id: int) -> int | None:
        return self.runtime.next_queued_task_id(int(after_task_id))

    def finish_without_pause(
        self,
        task_id: int,
        notice: TaskNotice,
        *,
        fallback_account_id: int | None = None,
    ) -> StepResult:
        self.runtime.finish_without_pause(int(task_id), notice, fallback_account_id=fallback_account_id)
        return StepResult(
            lines=[
                "任务已自动收尾，未转人工处理",
                f"原因：{notice.reason}",
                f"任务 ID：{task_id}",
            ],
            payload=notice.payload,
        )

    def cancel_task(self, task_id: int, reason: str = "用户终止任务") -> StepResult:
        self.engine.cancel(int(task_id), reason)
        return StepResult(
            lines=[
                "任务已终止",
                f"原因：{reason}",
                f"任务 ID：{task_id}",
            ],
            payload={"reason": reason},
        )

    def prepare_skip_candidate(
        self,
        context: TaskExecutionContext,
        notice: TaskNotice,
        *,
        fallback_candidate_id: int | None = None,
    ) -> SkipResult:
        candidate_id = notice.candidate_id or fallback_candidate_id or context.last_candidate_id
        if candidate_id:
            self.runtime.mark_candidate_skipped(int(candidate_id))
        next_index = self.runtime.next_candidate_index(
            context.next_candidate_index,
            context.last_opened_candidate_index,
        )
        self.runtime.notice_without_pause(
            int(context.task_id),
            TaskNotice(
                reason=notice.reason,
                detail=notice.detail,
                action_hint=notice.action_hint or "系统已跳过该候选人，继续处理下一位。",
                severity=notice.severity,
                step=notice.step,
                candidate_id=candidate_id,
                payload={"next_candidate_index": next_index, **notice.payload},
            ),
            fallback_account_id=context.account_id,
        )
        return SkipResult(
            next_candidate_index=next_index,
            candidate_id=candidate_id,
            lines=[
                "任务模式：已跳过异常候选人，继续下一位",
                f"原因：{notice.reason}",
                f"候选人 ID：{candidate_id or '-'}",
                f"下一位序号：第 {next_index + 1} 位",
            ],
        )

    def skip_candidate_and_plan_next(
        self,
        context: TaskExecutionContext,
        notice: TaskNotice,
        *,
        advance_reason: str = "任务模式：异常候选人已跳过，自动进入下一位",
        fallback_candidate_id: int | None = None,
        advance_payload: dict[str, Any] | None = None,
    ) -> SkipAndAdvanceResult:
        skip_result = self.prepare_skip_candidate(
            context,
            notice,
            fallback_candidate_id=fallback_candidate_id,
        )
        planned_context = replace(context, next_candidate_index=skip_result.next_candidate_index)
        step_payload = {"skip_reason": notice.reason, **(advance_payload or {})}
        step_result = self.set_next_candidate_step(planned_context, advance_reason, step_payload)
        return SkipAndAdvanceResult(
            next_candidate_index=skip_result.next_candidate_index,
            candidate_id=skip_result.candidate_id,
            lines=[*skip_result.lines, *step_result.lines],
            payload=step_result.payload,
        )
