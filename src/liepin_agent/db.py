from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = Path("data/app.db")
PROFILE_ROOT = Path("profiles/app")


def utc_now_sql() -> str:
    return "datetime('now')"


class Database:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                username TEXT DEFAULT '',
                password TEXT DEFAULT '',
                profile_dir TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'needs_login',
                enabled INTEGER NOT NULL DEFAULT 1,
                daily_search_limit INTEGER NOT NULL DEFAULT 100,
                daily_greeting_limit INTEGER NOT NULL DEFAULT 30,
                last_used_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                jd TEXT NOT NULL DEFAULT '',
                keywords TEXT NOT NULL DEFAULT '[]',
                must_have TEXT NOT NULL DEFAULT '[]',
                nice_to_have TEXT NOT NULL DEFAULT '[]',
                reject_keywords TEXT NOT NULL DEFAULT '[]',
                city TEXT DEFAULT '',
                experience TEXT DEFAULT '',
                education TEXT DEFAULT '',
                search_conditions TEXT NOT NULL DEFAULT '{}',
                min_score INTEGER NOT NULL DEFAULT 75,
                greeting_template TEXT DEFAULT '',
                followup_template TEXT DEFAULT '',
                auto_greet INTEGER NOT NULL DEFAULT 0,
                dry_run INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                task_type TEXT NOT NULL DEFAULT 'search_score_greet',
                status TEXT NOT NULL DEFAULT 'pending',
                current_step TEXT NOT NULL DEFAULT 'INIT',
                schedule_text TEXT DEFAULT '',
                max_candidates INTEGER NOT NULL DEFAULT 30,
                target_type TEXT NOT NULL DEFAULT 'resume',
                hide_viewed INTEGER NOT NULL DEFAULT 0,
                hide_contacted INTEGER NOT NULL DEFAULT 0,
                hide_contact_info INTEGER NOT NULL DEFAULT 0,
                auto_greet INTEGER NOT NULL DEFAULT 0,
                dry_run INTEGER NOT NULL DEFAULT 1,
                use_ai_scoring INTEGER NOT NULL DEFAULT 1,
                greet_min_score INTEGER,
                age_min INTEGER,
                age_max INTEGER,
                priority INTEGER NOT NULL DEFAULT 100,
                sort_order INTEGER NOT NULL DEFAULT 0,
                retry_limit INTEGER NOT NULL DEFAULT 1,
                retry_interval_sec INTEGER NOT NULL DEFAULT 60,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                checkpoint_json TEXT NOT NULL DEFAULT '{}',
                last_run_at TEXT,
                next_run_at TEXT,
                last_error TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                started_at TEXT,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                source_account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                source_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                source TEXT NOT NULL DEFAULT 'liepin',
                external_id TEXT DEFAULT '',
                name TEXT DEFAULT '',
                title TEXT DEFAULT '',
                company TEXT DEFAULT '',
                city TEXT DEFAULT '',
                experience TEXT DEFAULT '',
                education TEXT DEFAULT '',
                profile_url TEXT NOT NULL DEFAULT '',
                search_keyword TEXT DEFAULT '',
                resume_status TEXT NOT NULL DEFAULT 'not_fetched',
                score INTEGER,
                score_summary TEXT DEFAULT '',
                matched_keywords TEXT NOT NULL DEFAULT '[]',
                missing_keywords TEXT NOT NULL DEFAULT '[]',
                risks TEXT NOT NULL DEFAULT '[]',
                candidate_state TEXT NOT NULL DEFAULT 'active',
                greeting TEXT DEFAULT '',
                greeting_status TEXT NOT NULL DEFAULT 'not_sent',
                last_snapshot_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(job_id, profile_url)
            );

            CREATE TABLE IF NOT EXISTS resume_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER REFERENCES candidates(id) ON DELETE CASCADE,
                job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                url TEXT NOT NULL DEFAULT '',
                title TEXT DEFAULT '',
                text_length INTEGER NOT NULL DEFAULT 0,
                line_count INTEGER NOT NULL DEFAULT 0,
                matched_sections TEXT NOT NULL DEFAULT '[]',
                project_total INTEGER,
                project_visible INTEGER,
                has_attachment_resume INTEGER NOT NULL DEFAULT 0,
                has_unauthorized_attachment INTEGER NOT NULL DEFAULT 0,
                completeness TEXT DEFAULT '',
                warnings TEXT NOT NULL DEFAULT '[]',
                resume_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS score_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                score INTEGER NOT NULL,
                matched_keywords TEXT NOT NULL DEFAULT '[]',
                missing_keywords TEXT NOT NULL DEFAULT '[]',
                risks TEXT NOT NULL DEFAULT '[]',
                summary TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS greeting_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER REFERENCES candidates(id) ON DELETE CASCADE,
                task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'generated',
                dry_run INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                level TEXT NOT NULL DEFAULT 'info',
                step TEXT DEFAULT '',
                message TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                candidate_id INTEGER REFERENCES candidates(id) ON DELETE SET NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                status TEXT NOT NULL DEFAULT 'open',
                reason TEXT NOT NULL,
                detail TEXT DEFAULT '',
                action_hint TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at TEXT
            );
            """
        )
        self.conn.commit()
        self.ensure_column("accounts", "password", "TEXT DEFAULT ''")
        self.ensure_column("jobs", "search_conditions", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("jobs", "followup_template", "TEXT DEFAULT ''")
        self.ensure_column("tasks", "greet_min_score", "INTEGER")
        self.ensure_column("tasks", "target_type", "TEXT NOT NULL DEFAULT 'resume'")
        self.ensure_column("tasks", "hide_viewed", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("tasks", "hide_contacted", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("tasks", "hide_contact_info", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("tasks", "priority", "INTEGER NOT NULL DEFAULT 100")
        self.ensure_column("tasks", "sort_order", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("tasks", "retry_limit", "INTEGER NOT NULL DEFAULT 1")
        self.ensure_column("tasks", "retry_interval_sec", "INTEGER NOT NULL DEFAULT 60")
        self.ensure_column("tasks", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("tasks", "checkpoint_json", "TEXT NOT NULL DEFAULT '{}'")
        self.ensure_column("tasks", "last_run_at", "TEXT")
        self.ensure_column("tasks", "next_run_at", "TEXT")
        self.ensure_column("tasks", "use_ai_scoring", "INTEGER NOT NULL DEFAULT 1")
        self.ensure_column("tasks", "age_min", "INTEGER")
        self.ensure_column("tasks", "age_max", "INTEGER")
        self.ensure_column("candidates", "candidate_state", "TEXT NOT NULL DEFAULT 'active'")
        self.conn.execute("UPDATE tasks SET sort_order = id WHERE COALESCE(sort_order, 0) = 0")
        self.conn.execute("UPDATE tasks SET priority = 100 WHERE priority IS NULL")
        self.conn.execute("UPDATE tasks SET use_ai_scoring = 1 WHERE use_ai_scoring IS NULL")
        self.conn.execute("UPDATE candidates SET candidate_state = 'active' WHERE candidate_state IS NULL OR candidate_state = ''")
        self.ensure_indexes()
        self.normalize_account_profiles()
        self.conn.commit()

    def ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.conn.commit()

    def ensure_indexes(self) -> None:
        self.conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_score_results_candidate_id_id
            ON score_results(candidate_id, id DESC);

            CREATE INDEX IF NOT EXISTS idx_greeting_logs_candidate_id_id
            ON greeting_logs(candidate_id, id DESC);

            CREATE INDEX IF NOT EXISTS idx_greeting_logs_task_status_candidate
            ON greeting_logs(task_id, status, candidate_id);

            CREATE INDEX IF NOT EXISTS idx_candidates_source_task
            ON candidates(source_task_id);

            CREATE INDEX IF NOT EXISTS idx_candidates_job_status_updated
            ON candidates(job_id, resume_status, updated_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_candidates_job_score_updated
            ON candidates(job_id, score, updated_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_resume_snapshots_candidate_id_id
            ON resume_snapshots(candidate_id, id DESC);

            CREATE INDEX IF NOT EXISTS idx_execution_logs_task_id_id
            ON execution_logs(task_id, id DESC);

            CREATE INDEX IF NOT EXISTS idx_alerts_task_status_id
            ON alerts(task_id, status, id DESC);
            """
        )

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)))

    def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cursor = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return int(cursor.lastrowid)

    def add_account(self, name: str, username: str = "") -> int:
        account_id = self.execute(
            """
            INSERT INTO accounts (name, username, profile_dir)
            VALUES (?, ?, ?)
            """,
            (name, username, "profiles/account_pending"),
        )
        profile_dir = str(PROFILE_ROOT / f"account_{account_id}")
        self.execute("UPDATE accounts SET profile_dir = ? WHERE id = ?", (profile_dir, account_id))
        return account_id

    def update_account(self, account_id: int, name: str, username: str, password: str) -> None:
        self.execute(
            """
            UPDATE accounts
            SET name = ?, username = ?, password = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (name, username, password, account_id),
        )

    def normalize_account_profiles(self) -> None:
        rows = self.fetch_all("SELECT id, profile_dir FROM accounts ORDER BY id")
        for row in rows:
            account_id = int(row["id"])
            current = str(row["profile_dir"] or "")
            expected = str(PROFILE_ROOT / f"account_{account_id}")
            if current == expected:
                continue
            should_migrate = (
                not current.isascii()
                or current == "profiles/account_pending"
                or current.startswith("profiles/account_")
            )
            if not should_migrate:
                continue
            current_path = Path(current)
            expected_path = Path(expected)
            try:
                if current_path.exists() and not current.startswith("profiles/account_") and not expected_path.exists():
                    expected_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(current_path), str(expected_path))
                else:
                    expected_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            self.conn.execute(
                "UPDATE accounts SET profile_dir = ?, updated_at = datetime('now') WHERE id = ?",
                (expected, account_id),
            )
        self.conn.commit()

    def add_job(
        self,
        title: str,
        jd: str,
        keywords: list[str],
        must_have: list[str],
        nice_to_have: list[str],
        reject_keywords: list[str],
        city: str = "",
        experience: str = "",
        education: str = "",
        search_conditions: dict[str, Any] | None = None,
        min_score: int = 75,
        greeting_template: str = "",
        followup_template: str = "",
        auto_greet: bool = False,
        dry_run: bool = True,
    ) -> int:
        return self.execute(
            """
            INSERT INTO jobs (
                title, jd, keywords, must_have, nice_to_have, reject_keywords,
                city, experience, education, search_conditions, min_score, greeting_template, followup_template, auto_greet, dry_run
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                jd,
                dumps(keywords),
                dumps(must_have),
                dumps(nice_to_have),
                dumps(reject_keywords),
                city,
                experience,
                education,
                dumps(search_conditions or {}),
                min_score,
                greeting_template,
                followup_template,
                int(auto_greet),
                int(dry_run),
            ),
        )

    def add_task(
        self,
        name: str,
        job_id: int,
        account_id: int,
        max_candidates: int = 30,
        target_type: str = "resume",
        hide_viewed: bool = False,
        hide_contacted: bool = False,
        hide_contact_info: bool = False,
        auto_greet: bool = False,
        dry_run: bool = True,
        use_ai_scoring: bool = True,
        schedule_text: str = "",
        greet_min_score: int | None = None,
        age_min: int | None = None,
        age_max: int | None = None,
        priority: int = 100,
        retry_limit: int = 1,
        retry_interval_sec: int = 60,
        sort_order: int | None = None,
    ) -> int:
        if sort_order is None:
            row = self.fetch_one("SELECT COALESCE(MAX(sort_order), 0) AS v FROM tasks")
            sort_order = int(row["v"]) + 10 if row else 10
        return self.execute(
            """
            INSERT INTO tasks (
                name, job_id, account_id, max_candidates, target_type,
                hide_viewed, hide_contacted, hide_contact_info,
                auto_greet, dry_run, use_ai_scoring, schedule_text,
                greet_min_score, age_min, age_max, priority, retry_limit, retry_interval_sec, sort_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                job_id,
                account_id,
                max_candidates,
                target_type if target_type in {"resume", "greeting"} else "resume",
                int(hide_viewed),
                int(hide_contacted),
                int(hide_contact_info),
                int(auto_greet),
                int(dry_run),
                int(use_ai_scoring),
                schedule_text,
                int(greet_min_score) if greet_min_score is not None else None,
                int(age_min) if age_min is not None else None,
                int(age_max) if age_max is not None else None,
                int(priority),
                int(retry_limit),
                int(retry_interval_sec),
                int(sort_order),
            ),
        )

    def update_task(
        self,
        task_id: int,
        *,
        name: str,
        job_id: int,
        account_id: int,
        max_candidates: int,
        target_type: str,
        hide_viewed: bool,
        hide_contacted: bool,
        hide_contact_info: bool,
        auto_greet: bool,
        dry_run: bool,
        use_ai_scoring: bool,
        schedule_text: str,
        greet_min_score: int | None,
        age_min: int | None,
        age_max: int | None,
        priority: int,
        retry_limit: int,
        retry_interval_sec: int,
    ) -> None:
        self.execute(
            """
            UPDATE tasks
            SET name = ?, job_id = ?, account_id = ?, max_candidates = ?, target_type = ?,
                hide_viewed = ?, hide_contacted = ?, hide_contact_info = ?,
                auto_greet = ?, dry_run = ?,
                use_ai_scoring = ?, schedule_text = ?, greet_min_score = ?, age_min = ?, age_max = ?,
                priority = ?, retry_limit = ?, retry_interval_sec = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                name,
                int(job_id),
                int(account_id),
                int(max_candidates),
                target_type if target_type in {"resume", "greeting"} else "resume",
                int(hide_viewed),
                int(hide_contacted),
                int(hide_contact_info),
                int(auto_greet),
                int(dry_run),
                int(use_ai_scoring),
                schedule_text,
                int(greet_min_score) if greet_min_score is not None else None,
                int(age_min) if age_min is not None else None,
                int(age_max) if age_max is not None else None,
                int(priority),
                int(retry_limit),
                int(retry_interval_sec),
                int(task_id),
            ),
        )

    def upsert_candidate(self, payload: dict[str, Any]) -> int:
        job_id = int(payload["job_id"])
        profile_url = payload.get("profile_url") or ""
        row = self.fetch_one(
            "SELECT id FROM candidates WHERE job_id = ? AND profile_url = ?",
            (job_id, profile_url),
        )
        values = (
            job_id,
            payload.get("source_account_id"),
            payload.get("source_task_id"),
            payload.get("source", "liepin"),
            payload.get("external_id", ""),
            payload.get("name", ""),
            payload.get("title", ""),
            payload.get("company", ""),
            payload.get("city", ""),
            payload.get("experience", ""),
            payload.get("education", ""),
            profile_url,
            payload.get("search_keyword", ""),
            payload.get("resume_status", "not_fetched"),
        )
        if row:
            self.execute(
                """
                UPDATE candidates
                SET source_account_id = ?, source_task_id = ?, source = ?, external_id = ?, name = ?,
                    title = ?, company = ?, city = ?, experience = ?, education = ?, search_keyword = ?,
                    resume_status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    values[1],
                    values[2],
                    values[3],
                    values[4],
                    values[5],
                    values[6],
                    values[7],
                    values[8],
                    values[9],
                    values[10],
                    values[12],
                    values[13],
                    int(row["id"]),
                ),
            )
            return int(row["id"])
        return self.execute(
            """
            INSERT INTO candidates (
                job_id, source_account_id, source_task_id, source, external_id, name, title, company,
                city, experience, education, profile_url, search_keyword, resume_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    def add_snapshot(self, payload: dict[str, Any]) -> int:
        snapshot_id = self.execute(
            """
            INSERT INTO resume_snapshots (
                candidate_id, job_id, account_id, task_id, url, title, text_length, line_count,
                matched_sections, project_total, project_visible, has_attachment_resume,
                has_unauthorized_attachment, completeness, warnings, resume_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("candidate_id"),
                payload.get("job_id"),
                payload.get("account_id"),
                payload.get("task_id"),
                payload.get("url", ""),
                payload.get("title", ""),
                int(payload.get("text_length") or 0),
                int(payload.get("line_count") or 0),
                dumps(payload.get("matched_sections") or []),
                payload.get("project_total"),
                payload.get("project_visible"),
                int(bool(payload.get("has_attachment_resume"))),
                int(bool(payload.get("has_unauthorized_attachment"))),
                payload.get("completeness", ""),
                dumps(payload.get("warnings") or []),
                payload.get("resume_text", ""),
            ),
        )
        if payload.get("candidate_id"):
            self.execute(
                """
                UPDATE candidates
                SET last_snapshot_id = ?, resume_status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (snapshot_id, payload.get("resume_status", "fetched"), payload["candidate_id"]),
            )
        return snapshot_id

    def add_score(self, candidate_id: int, job_id: int, score: int, matched: list[str], missing: list[str], risks: list[str], summary: str) -> int:
        score_id = self.execute(
            """
            INSERT INTO score_results (candidate_id, job_id, score, matched_keywords, missing_keywords, risks, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (candidate_id, job_id, score, dumps(matched), dumps(missing), dumps(risks), summary),
        )
        self.execute(
            """
            UPDATE candidates
            SET score = ?, score_summary = ?, matched_keywords = ?, missing_keywords = ?, risks = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (score, summary, dumps(matched), dumps(missing), dumps(risks), candidate_id),
        )
        return score_id

    def set_candidate_state(self, candidate_id: int, state: str) -> None:
        self.execute(
            """
            UPDATE candidates
            SET candidate_state = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (state, candidate_id),
        )

    def add_greeting_log(self, candidate_id: int | None, task_id: int | None, account_id: int | None, message: str, status: str, dry_run: bool) -> int:
        return self.execute(
            """
            INSERT INTO greeting_logs (candidate_id, task_id, account_id, message, status, dry_run)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (candidate_id, task_id, account_id, message, status, int(dry_run)),
        )

    def log(self, message: str, task_id: int | None = None, account_id: int | None = None, level: str = "info", step: str = "", payload: dict[str, Any] | None = None) -> int:
        return self.execute(
            """
            INSERT INTO execution_logs (task_id, account_id, level, step, message, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, account_id, level, step, message, dumps(payload or {})),
        )

    def alert(
        self,
        reason: str,
        detail: str = "",
        action_hint: str = "",
        task_id: int | None = None,
        account_id: int | None = None,
        candidate_id: int | None = None,
        severity: str = "warning",
    ) -> int:
        return self.execute(
            """
            INSERT INTO alerts (task_id, account_id, candidate_id, severity, reason, detail, action_hint)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, account_id, candidate_id, severity, reason, detail, action_hint),
        )


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def slugify(value: str) -> str:
    cleaned = re_sub_nonword(value.strip().lower())
    return cleaned or "account"


def re_sub_nonword(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value).strip("_")
