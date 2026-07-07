from liepin_agent.db import Database
from liepin_agent.task_engine import TaskEngine, TaskStatus, TaskStep


def make_engine(tmp_path):
    db = Database(tmp_path / "app.db")
    db.init()
    return db, TaskEngine(db)


def test_resume_refuses_terminal_task_status(tmp_path) -> None:
    db, engine = make_engine(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)
    engine.finish(task_id)

    resumed = engine.resume(task_id, TaskStep.APPLY_FILTERS)

    task = db.fetch_one("SELECT status, current_step FROM tasks WHERE id = ?", (task_id,))
    log = db.fetch_one("SELECT message, payload FROM execution_logs WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,))

    assert resumed is False
    assert task["status"] == TaskStatus.DONE.value
    assert task["current_step"] == TaskStep.DONE.value
    assert log["message"] == "任务不能从终态继续"


def test_resume_paused_task_sets_running_step(tmp_path) -> None:
    db, engine = make_engine(tmp_path)
    account_id = db.add_account("账号A", "a@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("任务A", job_id, account_id)
    db.execute(
        "UPDATE tasks SET status = ?, current_step = ? WHERE id = ?",
        (TaskStatus.PAUSED_NEEDS_USER.value, TaskStep.EXTRACT_RESUME.value, task_id),
    )

    resumed = engine.resume(task_id, TaskStep.SCORE)

    task = db.fetch_one("SELECT status, current_step FROM tasks WHERE id = ?", (task_id,))
    assert resumed is True
    assert task["status"] == TaskStatus.RUNNING.value
    assert task["current_step"] == TaskStep.SCORE.value
