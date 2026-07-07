from liepin_agent.db import Database
from liepin_agent.task_engine import TaskEngine, TaskStatus, TaskStep
from liepin_agent.task_executor import TaskExecutionContext, TaskExecutor
from liepin_agent.task_runtime import TaskNotice, TaskRuntime


def make_executor(tmp_path):
    db = Database(tmp_path / "app.db")
    db.init()
    engine = TaskEngine(db)
    runtime = TaskRuntime(db, engine)
    return db, TaskExecutor(db, engine, runtime)


def test_set_next_candidate_step_logs_payload(tmp_path) -> None:
    db, executor = make_executor(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)

    result = executor.set_next_candidate_step(
        TaskExecutionContext(
            task_id=task_id,
            account_id=account_id,
            next_candidate_index=2,
            current_page_card_count=30,
            current_url="https://example.test/search",
        ),
        "准备下一位",
        {"x": 1},
    )

    task = db.fetch_one("SELECT current_step FROM tasks WHERE id = ?", (task_id,))
    log = db.fetch_one("SELECT message, payload FROM execution_logs WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,))

    assert task["current_step"] == TaskStep.OPEN_RESUME.value
    assert "下一位序号：第 3 位" in result.lines
    assert log["message"] == "任务模式：准备打开下一位候选人"
    assert '"next_candidate_index": 2' in log["payload"]


def test_start_task_sets_running_open_search_and_returns_context(tmp_path) -> None:
    db, executor = make_executor(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task(
        "任务A",
        job_id,
        account_id,
        max_candidates=12,
        auto_greet=True,
        dry_run=False,
        use_ai_scoring=False,
        greet_min_score=81,
        age_min=28,
        age_max=40,
    )

    result = executor.start_task(task_id, queue_mode=True)

    task = db.fetch_one("SELECT status, current_step FROM tasks WHERE id = ?", (task_id,))
    assert result is not None
    assert result.task_id == task_id
    assert result.job_id == job_id
    assert result.account_id == account_id
    assert result.min_score == 81
    assert result.auto_greet is True
    assert result.dry_run is False
    assert result.use_ai_scoring is False
    assert result.payload["queue_mode"] is True
    assert result.payload["max_candidates"] == 12
    assert task["status"] == TaskStatus.RUNNING.value
    assert task["current_step"] == TaskStep.OPEN_SEARCH.value
    assert "任务已启动" in result.lines


def test_prepare_skip_candidate_marks_list_card_and_returns_next_index(tmp_path) -> None:
    db, executor = make_executor(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)
    candidate_id = db.upsert_candidate(
        {
            "job_id": job_id,
            "source_task_id": task_id,
            "profile_url": "list:1",
            "resume_status": "list_card",
        }
    )

    result = executor.prepare_skip_candidate(
        TaskExecutionContext(
            task_id=task_id,
            account_id=account_id,
            next_candidate_index=1,
            last_opened_candidate_index=3,
        ),
        TaskNotice(reason="打不开", candidate_id=candidate_id),
    )

    row = db.fetch_one("SELECT resume_status FROM candidates WHERE id = ?", (candidate_id,))
    alert = db.fetch_one("SELECT reason FROM alerts WHERE task_id = ?", (task_id,))

    assert result.next_candidate_index == 4
    assert row["resume_status"] == "skipped"
    assert alert["reason"] == "打不开"


def test_skip_candidate_and_plan_next_sets_open_resume_step(tmp_path) -> None:
    db, executor = make_executor(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)
    candidate_id = db.upsert_candidate(
        {
            "job_id": job_id,
            "source_task_id": task_id,
            "profile_url": "list:1",
            "resume_status": "list_card",
        }
    )

    result = executor.skip_candidate_and_plan_next(
        TaskExecutionContext(
            task_id=task_id,
            account_id=account_id,
            next_candidate_index=2,
            last_opened_candidate_index=2,
            current_page_card_count=30,
            current_url="https://example.test/search",
        ),
        TaskNotice(reason="详情页异常", candidate_id=candidate_id),
        advance_payload={"url": "https://example.test/resume"},
    )

    task = db.fetch_one("SELECT current_step FROM tasks WHERE id = ?", (task_id,))
    log = db.fetch_one("SELECT message, payload FROM execution_logs WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,))

    assert result.next_candidate_index == 3
    assert result.candidate_id == candidate_id
    assert task["current_step"] == TaskStep.OPEN_RESUME.value
    assert log["message"] == "任务模式：准备打开下一位候选人"
    assert '"skip_reason": "详情页异常"' in log["payload"]
    assert '"next_candidate_index": 3' in log["payload"]


def test_finish_without_pause_marks_done(tmp_path) -> None:
    db, executor = make_executor(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)

    result = executor.finish_without_pause(task_id, TaskNotice(reason="结束", severity="info"))

    task = db.fetch_one("SELECT status FROM tasks WHERE id = ?", (task_id,))
    assert task["status"] == TaskStatus.DONE.value
    assert result.lines[1] == "原因：结束"


def test_cancel_task_marks_cancelled_without_manual_pause(tmp_path) -> None:
    db, executor = make_executor(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)

    result = executor.cancel_task(task_id, "用户点击终止")

    task = db.fetch_one("SELECT status, current_step, last_error FROM tasks WHERE id = ?", (task_id,))
    assert task["status"] == TaskStatus.CANCELLED.value
    assert task["current_step"] == TaskStep.CANCELLED.value
    assert task["last_error"] == "用户点击终止"
    assert result.lines[0] == "任务已终止"


def test_next_queued_task_id_delegates_runtime_order(tmp_path) -> None:
    db, executor = make_executor(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    first = db.add_task("任务1", job_id, account_id, sort_order=10)
    second = db.add_task("任务2", job_id, account_id, sort_order=20)

    assert executor.next_queued_task_id(first) == second
    assert executor.next_queued_task_id(second) is None
