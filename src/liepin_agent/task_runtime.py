from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from liepin_agent.db import Database
from liepin_agent.task_engine import TaskEngine, TaskStep


PROCESSED_RESUME_STATUSES = ("fetched", "needs_attachment", "incomplete")
GREETED_STATUSES = (
    "opening_selected_dry_run",
    "opening_sent",
    "followup_filled_dry_run",
    "followup_filled_not_sent",
    "followup_sent",
    "continued_followup_filled_dry_run",
    "continued_followup_sent",
    "followup_already_exists",
    "sent",
)


@dataclass(frozen=True)
class TaskNotice:
    reason: str
    detail: str = ""
    action_hint: str = ""
    severity: str = "warning"
    step: TaskStep | str = TaskStep.OPEN_RESUME
    candidate_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskProgress:
    task_id: int
    max_candidates: int
    target_type: str
    processed_candidates: int
    greeted_candidates: int

    @property
    def target_label(self) -> str:
        return "沟通" if self.target_type == "greeting" else "查看简历"

    @property
    def current_count(self) -> int:
        return self.greeted_candidates if self.target_type == "greeting" else self.processed_candidates

    @property
    def remaining_slots(self) -> int:
        if self.max_candidates <= 0:
            return 0
        return max(0, self.max_candidates - self.current_count)

    @property
    def reached_limit(self) -> bool:
        return self.max_candidates > 0 and self.current_count >= self.max_candidates

    @property
    def should_limit_collected_cards(self) -> bool:
        return self.target_type != "greeting"


class TaskRuntime:
    """Small, testable task policy layer shared by UI and future executors."""

    def __init__(self, db: Database, engine: TaskEngine) -> None:
        self.db = db
        self.engine = engine

    def task_account_id(self, task_id: int, fallback: int | None = None) -> int | None:
        row = self.db.fetch_one("SELECT account_id FROM tasks WHERE id = ?", (int(task_id),))
        if not row or row["account_id"] is None:
            return fallback
        return int(row["account_id"])

    def max_candidates(self, task_id: int) -> int:
        row = self.db.fetch_one("SELECT max_candidates FROM tasks WHERE id = ?", (int(task_id),))
        return int(row["max_candidates"] or 0) if row else 0

    def target_type(self, task_id: int) -> str:
        row = self.db.fetch_one("SELECT target_type FROM tasks WHERE id = ?", (int(task_id),))
        value = str(row["target_type"] or "resume") if row else "resume"
        return value if value in {"resume", "greeting"} else "resume"

    def processed_candidate_count(self, task_id: int) -> int:
        row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS c
            FROM candidates
            WHERE source_task_id = ?
              AND (
                  last_snapshot_id IS NOT NULL
                  OR resume_status IN ('fetched', 'needs_attachment', 'incomplete')
                  OR score IS NOT NULL
              )
            """,
            (int(task_id),),
        )
        return int(row["c"] or 0) if row else 0

    def greeted_candidate_count(self, task_id: int) -> int:
        placeholders = ",".join("?" for _ in GREETED_STATUSES)
        row = self.db.fetch_one(
            f"""
            SELECT COUNT(DISTINCT candidate_id) AS c
            FROM greeting_logs
            WHERE task_id = ?
              AND candidate_id IS NOT NULL
              AND status IN ({placeholders})
            """,
            (int(task_id), *GREETED_STATUSES),
        )
        return int(row["c"] or 0) if row else 0

    def progress(self, task_id: int) -> TaskProgress:
        return TaskProgress(
            task_id=int(task_id),
            max_candidates=self.max_candidates(int(task_id)),
            target_type=self.target_type(int(task_id)),
            processed_candidates=self.processed_candidate_count(int(task_id)),
            greeted_candidates=self.greeted_candidate_count(int(task_id)),
        )

    def next_queued_task_id(self, after_task_id: int) -> int | None:
        task = self.db.fetch_one(
            "SELECT id, COALESCE(sort_order, id) AS sort_key FROM tasks WHERE id = ?",
            (int(after_task_id),),
        )
        if not task:
            return None
        row = self.db.fetch_one(
            """
            SELECT id
            FROM tasks
            WHERE status = 'pending'
              AND (next_run_at IS NULL OR next_run_at <= datetime('now'))
              AND (
                    COALESCE(sort_order, id) > ?
                    OR (COALESCE(sort_order, id) = ? AND id > ?)
                  )
            ORDER BY COALESCE(sort_order, id) ASC, id ASC
            LIMIT 1
            """,
            (int(task["sort_key"] or 0), int(task["sort_key"] or 0), int(after_task_id)),
        )
        return int(row["id"]) if row else None

    def notice_without_pause(self, task_id: int, notice: TaskNotice, *, fallback_account_id: int | None = None) -> None:
        account_id = self.task_account_id(int(task_id), fallback_account_id)
        step_value = notice.step.value if isinstance(notice.step, TaskStep) else str(notice.step or "")
        self.db.alert(
            reason=notice.reason,
            detail=notice.detail,
            action_hint=notice.action_hint,
            task_id=int(task_id),
            account_id=account_id,
            candidate_id=notice.candidate_id,
            severity=notice.severity,
        )
        self.db.log(
            f"任务提醒（不中断）：{notice.reason}",
            task_id=int(task_id),
            account_id=account_id,
            level=notice.severity if notice.severity in {"info", "warning", "error"} else "warning",
            step=step_value,
            payload={
                "detail": notice.detail,
                "action_hint": notice.action_hint,
                **notice.payload,
            },
        )

    def finish_without_pause(
        self,
        task_id: int,
        notice: TaskNotice,
        *,
        fallback_account_id: int | None = None,
    ) -> None:
        self.notice_without_pause(int(task_id), notice, fallback_account_id=fallback_account_id)
        self.engine.finish(int(task_id))

    def mark_candidate_skipped(self, candidate_id: int) -> None:
        self.db.execute(
            """
            UPDATE candidates
            SET resume_status = CASE
                    WHEN resume_status IN ('not_fetched', 'list_card') THEN 'skipped'
                    ELSE resume_status
                END,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (int(candidate_id),),
        )

    @staticmethod
    def next_candidate_index(current_index: int | None, last_opened_index: int | None) -> int:
        if last_opened_index is not None:
            return max(int(current_index or 0), int(last_opened_index) + 1)
        return max(1, int(current_index or 0) + 1)
