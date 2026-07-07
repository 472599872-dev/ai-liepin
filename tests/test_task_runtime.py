from liepin_agent.db import Database
from liepin_agent.task_engine import TaskEngine, TaskStatus
from liepin_agent.task_runtime import TaskNotice, TaskRuntime


def make_runtime(tmp_path):
    db = Database(tmp_path / "app.db")
    db.init()
    return db, TaskRuntime(db, TaskEngine(db))


def test_progress_counts_only_processed_candidates(tmp_path) -> None:
    db, runtime = make_runtime(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id, max_candidates=3)

    db.upsert_candidate({"job_id": job_id, "source_task_id": task_id, "profile_url": "list:1", "resume_status": "list_card"})
    db.upsert_candidate({"job_id": job_id, "source_task_id": task_id, "profile_url": "skip:1", "resume_status": "skipped"})
    fetched_id = db.upsert_candidate({"job_id": job_id, "source_task_id": task_id, "profile_url": "resume:1", "resume_status": "fetched"})
    scored_id = db.upsert_candidate({"job_id": job_id, "source_task_id": task_id, "profile_url": "score:1", "resume_status": "list_card"})
    db.add_score(scored_id, job_id, 82, ["Python"], [], [], "匹配")

    progress = runtime.progress(task_id)

    assert fetched_id > 0
    assert progress.max_candidates == 3
    assert progress.processed_candidates == 2
    assert progress.remaining_slots == 1
    assert not progress.reached_limit


def test_progress_can_target_greeted_candidates(tmp_path) -> None:
    db, runtime = make_runtime(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id, max_candidates=2, target_type="greeting")

    first_id = db.upsert_candidate({"job_id": job_id, "source_task_id": task_id, "profile_url": "resume:1", "resume_status": "fetched"})
    second_id = db.upsert_candidate({"job_id": job_id, "source_task_id": task_id, "profile_url": "resume:2", "resume_status": "fetched"})
    db.add_greeting_log(first_id, task_id, account_id, "hello", "followup_sent", False)
    db.add_greeting_log(first_id, task_id, account_id, "again", "continued_followup_sent", False)
    db.add_greeting_log(second_id, task_id, account_id, "draft", "generated", False)

    progress = runtime.progress(task_id)

    assert progress.target_type == "greeting"
    assert progress.processed_candidates == 2
    assert progress.greeted_candidates == 1
    assert progress.current_count == 1
    assert progress.remaining_slots == 1
    assert not progress.reached_limit
    assert not progress.should_limit_collected_cards


def test_next_queued_task_uses_sort_order_then_id(tmp_path) -> None:
    db, runtime = make_runtime(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    first = db.add_task("任务1", job_id, account_id, sort_order=10)
    second = db.add_task("任务2", job_id, account_id, sort_order=20)
    paused = db.add_task("任务3", job_id, account_id, sort_order=30)
    db.execute("UPDATE tasks SET status = ? WHERE id = ?", (TaskStatus.PAUSED_NEEDS_USER.value, paused))

    assert runtime.next_queued_task_id(first) == second
    assert runtime.next_queued_task_id(second) is None


def test_notice_and_finish_do_not_pause_task(tmp_path) -> None:
    db, runtime = make_runtime(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)

    runtime.finish_without_pause(
        task_id,
        TaskNotice(reason="没有下一页", detail="测试", action_hint="无需处理", severity="info"),
    )

    task = db.fetch_one("SELECT status, current_step FROM tasks WHERE id = ?", (task_id,))
    alert = db.fetch_one("SELECT reason, status FROM alerts WHERE task_id = ?", (task_id,))

    assert task["status"] == TaskStatus.DONE.value
    assert alert["reason"] == "没有下一页"
    assert alert["status"] == "open"


def test_mark_candidate_skipped_preserves_fetched_resume(tmp_path) -> None:
    db, runtime = make_runtime(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)
    list_id = db.upsert_candidate({"job_id": job_id, "source_task_id": task_id, "profile_url": "list:1", "resume_status": "list_card"})
    fetched_id = db.upsert_candidate({"job_id": job_id, "source_task_id": task_id, "profile_url": "resume:1", "resume_status": "fetched"})

    runtime.mark_candidate_skipped(list_id)
    runtime.mark_candidate_skipped(fetched_id)

    list_row = db.fetch_one("SELECT resume_status FROM candidates WHERE id = ?", (list_id,))
    fetched_row = db.fetch_one("SELECT resume_status FROM candidates WHERE id = ?", (fetched_id,))

    assert list_row["resume_status"] == "skipped"
    assert fetched_row["resume_status"] == "fetched"


def test_next_candidate_index_policy() -> None:
    assert TaskRuntime.next_candidate_index(0, None) == 1
    assert TaskRuntime.next_candidate_index(3, None) == 4
    assert TaskRuntime.next_candidate_index(1, 4) == 5
    assert TaskRuntime.next_candidate_index(9, 4) == 9
