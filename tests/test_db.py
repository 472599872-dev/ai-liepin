from liepin_agent.db import Database, loads


def test_database_core_workflow(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.init()

    account_id = db.add_account("账号A", "user@example.com")
    job_id = db.add_job(
        title="高级 Java 后端",
        jd="Java Spring Boot Redis",
        keywords=["Java"],
        must_have=["Java", "Spring Boot"],
        nice_to_have=["Redis"],
        reject_keywords=["应届"],
    )
    task_id = db.add_task("搜索 Java", job_id, account_id)
    candidate_id = db.upsert_candidate(
        {
            "job_id": job_id,
            "source_account_id": account_id,
            "source_task_id": task_id,
            "name": "张**",
            "profile_url": "https://h.liepin.com/resume/showresumedetail/?x=1",
            "resume_status": "fetched",
        }
    )
    snapshot_id = db.add_snapshot(
        {
            "candidate_id": candidate_id,
            "job_id": job_id,
            "account_id": account_id,
            "task_id": task_id,
            "url": "https://h.liepin.com/resume/showresumedetail/?x=1",
            "text_length": 2000,
            "line_count": 120,
            "matched_sections": ["工作经历", "项目经历"],
            "resume_text": "Java Spring Boot Redis",
        }
    )
    db.add_score(candidate_id, job_id, 88, ["Java"], [], [], "匹配")

    row = db.fetch_one("SELECT * FROM candidates WHERE id = ?", (candidate_id,))

    assert account_id > 0
    assert task_id > 0
    assert snapshot_id > 0
    assert row["score"] == 88
    assert loads(row["matched_keywords"], []) == ["Java"]


def test_task_result_filter_flags_are_persisted(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.init()

    account_id = db.add_account("账号A", "user@example.com")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task(
        "搜索任务",
        job_id,
        account_id,
        hide_viewed=True,
        hide_contacted=True,
        hide_contact_info=False,
    )

    row = db.fetch_one("SELECT hide_viewed, hide_contacted, hide_contact_info FROM tasks WHERE id = ?", (task_id,))
    assert row["hide_viewed"] == 1
    assert row["hide_contacted"] == 1
    assert row["hide_contact_info"] == 0

    db.update_task(
        task_id,
        name="搜索任务2",
        job_id=job_id,
        account_id=account_id,
        max_candidates=20,
        target_type="resume",
        hide_viewed=False,
        hide_contacted=False,
        hide_contact_info=True,
        auto_greet=False,
        dry_run=True,
        use_ai_scoring=True,
        schedule_text="",
        greet_min_score=None,
        age_min=None,
        age_max=None,
        priority=100,
        retry_limit=1,
        retry_interval_sec=60,
    )
    row = db.fetch_one("SELECT hide_viewed, hide_contacted, hide_contact_info FROM tasks WHERE id = ?", (task_id,))
    assert row["hide_viewed"] == 0
    assert row["hide_contacted"] == 0
    assert row["hide_contact_info"] == 1


def test_delete_account_soft_deletes_and_keeps_history(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    db.init()

    account_id = db.add_account("账号A", "user@example.com")
    db.update_account(account_id, "账号A", "user@example.com", "secret")
    job_id = db.add_job("岗位A", "JD", [], [], [], [])
    task_id = db.add_task("搜索任务", job_id, account_id)
    candidate_id = db.upsert_candidate(
        {
            "job_id": job_id,
            "source_account_id": account_id,
            "source_task_id": task_id,
            "name": "张**",
            "profile_url": "https://h.liepin.com/resume/showresumedetail/?x=2",
        }
    )

    counts = db.account_reference_counts(account_id)
    assert counts["任务"] == 1
    assert counts["候选人"] == 1

    db.delete_account(account_id)

    account = db.fetch_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
    task = db.fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    candidate = db.fetch_one("SELECT * FROM candidates WHERE id = ?", (candidate_id,))

    assert account is not None
    assert account["deleted_at"]
    assert account["enabled"] == 0
    assert account["username"] == ""
    assert account["password"] == ""
    assert task is not None
    assert candidate is not None
    assert candidate["source_account_id"] == account_id
