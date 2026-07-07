from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from liepin_agent.db import Database


class TextEnum(str, Enum):
    pass


class TaskStatus(TextEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED_NEEDS_USER = "paused_needs_user"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskStep(TextEnum):
    INIT = "INIT"
    OPEN_ACCOUNT = "OPEN_ACCOUNT"
    CHECK_LOGIN = "CHECK_LOGIN"
    OPEN_SEARCH = "OPEN_SEARCH"
    APPLY_FILTERS = "APPLY_FILTERS"
    COLLECT_CARDS = "COLLECT_CARDS"
    OPEN_RESUME = "OPEN_RESUME"
    EXPAND_RESUME = "EXPAND_RESUME"
    EXTRACT_RESUME = "EXTRACT_RESUME"
    SCORE = "SCORE"
    GENERATE_GREETING = "GENERATE_GREETING"
    SEND_GREETING = "SEND_GREETING"
    NEXT_CANDIDATE = "NEXT_CANDIDATE"
    DONE = "DONE"
    PAUSED_NEEDS_USER = "PAUSED_NEEDS_USER"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class HumanIntervention:
    reason: str
    detail: str
    action_hint: str
    severity: str = "warning"


class TaskEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def start(self, task_id: int) -> None:
        task = self.db.fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        self.db.execute(
            """
            UPDATE tasks
            SET status = ?, current_step = ?, started_at = COALESCE(started_at, datetime('now')),
                updated_at = datetime('now'), last_error = '',
                attempt_count = COALESCE(attempt_count, 0) + 1,
                last_run_at = datetime('now'),
                checkpoint_json = '{}',
                next_run_at = NULL
            WHERE id = ?
            """,
            (TaskStatus.RUNNING.value, TaskStep.OPEN_ACCOUNT.value, task_id),
        )
        self.db.log("任务开始执行", task_id=task_id, account_id=task["account_id"], step=TaskStep.OPEN_ACCOUNT.value)

    def set_step(self, task_id: int, step: TaskStep | str, message: str = "", payload: dict[str, Any] | None = None) -> None:
        step_value = step.value if isinstance(step, TaskStep) else step
        self.db.execute(
            "UPDATE tasks SET current_step = ?, updated_at = datetime('now') WHERE id = ?",
            (step_value, task_id),
        )
        if message:
            task = self.db.fetch_one("SELECT account_id FROM tasks WHERE id = ?", (task_id,))
            self.db.log(message, task_id=task_id, account_id=task["account_id"] if task else None, step=step_value, payload=payload)

    def set_checkpoint(self, task_id: int, checkpoint: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE tasks SET checkpoint_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(checkpoint or {}, ensure_ascii=False), int(task_id)),
        )

    def checkpoint(self, task_id: int) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT checkpoint_json FROM tasks WHERE id = ?", (int(task_id),))
        if not row:
            return {}
        try:
            value = json.loads(row["checkpoint_json"] or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def resume(self, task_id: int, step: TaskStep | str | None = None) -> bool:
        task = self.db.fetch_one("SELECT account_id, current_step, status FROM tasks WHERE id = ?", (int(task_id),))
        if not task:
            return False
        if task["status"] in {
            TaskStatus.DONE.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.SKIPPED.value,
        }:
            self.db.log(
                "任务不能从终态继续",
                task_id=int(task_id),
                account_id=task["account_id"],
                level="warning",
                step=task["current_step"] or "",
                payload={"status": task["status"]},
            )
            return False
        step_value = step.value if isinstance(step, TaskStep) else str(step or task["current_step"] or TaskStep.OPEN_SEARCH.value)
        self.db.execute(
            """
            UPDATE tasks
            SET status = ?, current_step = ?, last_error = '', updated_at = datetime('now')
            WHERE id = ?
            """,
            (TaskStatus.RUNNING.value, step_value, int(task_id)),
        )
        self.db.log("任务从断点继续", task_id=int(task_id), account_id=task["account_id"], step=step_value)
        return True

    def pause_for_user(self, task_id: int, intervention: HumanIntervention) -> None:
        task = self.db.fetch_one("SELECT account_id FROM tasks WHERE id = ?", (task_id,))
        account_id = int(task["account_id"]) if task else None
        self.db.execute(
            """
            UPDATE tasks
            SET status = ?, current_step = ?, last_error = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (TaskStatus.PAUSED_NEEDS_USER.value, TaskStep.PAUSED_NEEDS_USER.value, intervention.reason, task_id),
        )
        self.db.alert(
            reason=intervention.reason,
            detail=intervention.detail,
            action_hint=intervention.action_hint,
            task_id=task_id,
            account_id=account_id,
            severity=intervention.severity,
        )
        self.db.log(
            f"任务暂停：{intervention.reason}",
            task_id=task_id,
            account_id=account_id,
            level="warning",
            step=TaskStep.PAUSED_NEEDS_USER.value,
            payload={"detail": intervention.detail, "action_hint": intervention.action_hint},
        )

    def fail(self, task_id: int, error: str) -> None:
        task = self.db.fetch_one("SELECT account_id FROM tasks WHERE id = ?", (task_id,))
        self.db.execute(
            """
            UPDATE tasks
            SET status = ?, current_step = ?, last_error = ?, finished_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (TaskStatus.FAILED.value, TaskStep.FAILED.value, error, task_id),
        )
        self.db.log("任务失败", task_id=task_id, account_id=task["account_id"] if task else None, level="error", step=TaskStep.FAILED.value, payload={"error": error})

    def finish(self, task_id: int) -> None:
        task = self.db.fetch_one("SELECT account_id FROM tasks WHERE id = ?", (task_id,))
        self.db.execute(
            """
            UPDATE tasks
            SET status = ?, current_step = ?, checkpoint_json = '{}',
                finished_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ?
            """,
            (TaskStatus.DONE.value, TaskStep.DONE.value, task_id),
        )
        self.db.log("任务完成", task_id=task_id, account_id=task["account_id"] if task else None, step=TaskStep.DONE.value)

    def cancel(self, task_id: int, reason: str = "用户终止任务") -> None:
        task = self.db.fetch_one("SELECT account_id FROM tasks WHERE id = ?", (task_id,))
        self.db.execute(
            """
            UPDATE tasks
            SET status = ?, current_step = ?, last_error = ?, checkpoint_json = '{}',
                finished_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (TaskStatus.CANCELLED.value, TaskStep.CANCELLED.value, reason, task_id),
        )
        self.db.log(
            "任务已终止",
            task_id=task_id,
            account_id=task["account_id"] if task else None,
            level="warning",
            step=TaskStep.CANCELLED.value,
            payload={"reason": reason},
        )
