from liepin_agent.task_workflow import decide_condition_apply_validation


def test_condition_validation_allows_soft_failures() -> None:
    result = decide_condition_apply_validation(
        target_keys=["keywords", "position_keywords", "age_min", "age_max", "recruit_type", "active_days"],
        applied_keys=["keywords", "position_keywords", "age_min", "age_max", "active_days"],
        has_apply_error=False,
    )

    assert result.validation_passed is True
    assert result.validation_status == "部分通过"
    assert result.blocking_failed_target_keys == []
    assert result.soft_failed_target_keys == ["recruit_type"]


def test_condition_validation_blocks_core_failures() -> None:
    result = decide_condition_apply_validation(
        target_keys=["keywords", "position_keywords", "age_min", "age_max", "recruit_type"],
        applied_keys=["keywords", "age_min", "age_max", "recruit_type"],
        has_apply_error=False,
    )

    assert result.validation_passed is False
    assert result.validation_status == "未通过"
    assert result.blocking_failed_target_keys == ["position_keywords"]


def test_condition_validation_blocks_when_nothing_was_applied() -> None:
    result = decide_condition_apply_validation(
        target_keys=["recruit_type", "active_days"],
        applied_keys=[],
        has_apply_error=False,
    )

    assert result.validation_passed is False
    assert result.failed_target_keys == ["recruit_type", "active_days"]
