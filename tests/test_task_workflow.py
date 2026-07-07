from liepin_agent.task_workflow import (
    WorkflowAction,
    decide_card_collection_validation,
    decide_detail_validation,
    decide_greeting_result,
    decide_open_candidate_result,
    decide_page_turn_result,
    decide_resume_validation,
)


def test_open_candidate_out_of_range_turns_page_in_task_mode() -> None:
    decision = decide_open_candidate_result(
        {"clicked": False, "reason": "target_index_out_of_range", "cardCount": 30},
        task_mode=True,
    )

    assert decision.action == WorkflowAction.PAGE_NEXT
    assert decision.payload["card_count"] == 30


def test_open_candidate_failure_skips_only_in_task_mode() -> None:
    task_decision = decide_open_candidate_result({"clicked": False, "reason": "no_card"}, task_mode=True)
    manual_decision = decide_open_candidate_result({"clicked": False, "reason": "no_card"}, task_mode=False)

    assert task_decision.action == WorkflowAction.SKIP_CANDIDATE
    assert manual_decision.action == WorkflowAction.PAUSE_FOR_MANUAL


def test_detail_validation_task_mode_continues_or_skips() -> None:
    assert decide_detail_validation(is_detail=True, task_mode=True, current_url="u").action == WorkflowAction.CONTINUE
    assert decide_detail_validation(is_detail=False, task_mode=True, current_url="u").action == WorkflowAction.SKIP_CANDIDATE


def test_resume_validation_scores_only_when_auto_advance() -> None:
    auto = decide_resume_validation(validation_passed=True, auto_advance=True, completeness="高", warnings=[])
    manual = decide_resume_validation(validation_passed=True, auto_advance=False, completeness="高", warnings=[])
    failed = decide_resume_validation(validation_passed=False, auto_advance=True, completeness="低", warnings=["列表页"])

    assert auto.action == WorkflowAction.SCORE_RESUME
    assert manual.action == WorkflowAction.PAUSE_FOR_MANUAL
    assert failed.action == WorkflowAction.SKIP_CANDIDATE


def test_greeting_result_auto_trigger_advances_only_when_some_action_succeeded() -> None:
    done = decide_greeting_result(
        auto_trigger=True,
        opening_selected=False,
        opening_sent=True,
        followup_filled=False,
        followup_sent=False,
        followup_already_exists=False,
    )
    failed = decide_greeting_result(
        auto_trigger=True,
        opening_selected=False,
        opening_sent=False,
        followup_filled=False,
        followup_sent=False,
        followup_already_exists=False,
    )
    manual = decide_greeting_result(
        auto_trigger=False,
        opening_selected=False,
        opening_sent=False,
        followup_filled=False,
        followup_sent=False,
        followup_already_exists=False,
    )

    assert done.action == WorkflowAction.CONTINUE
    assert failed.action == WorkflowAction.SKIP_CANDIDATE
    assert manual.action == WorkflowAction.PAUSE_FOR_MANUAL


def test_page_turn_decision_finishes_on_no_next_or_no_change() -> None:
    assert decide_page_turn_result(clicked_next=False).action == WorkflowAction.FINISH_TASK
    assert decide_page_turn_result(clicked_next=True, changed=True).action == WorkflowAction.CONTINUE
    assert decide_page_turn_result(clicked_next=True, changed=False, retries_left=0).action == WorkflowAction.FINISH_TASK
    assert decide_page_turn_result(clicked_next=True, changed=False, retries_left=3).action == WorkflowAction.RETRY_OPEN


def test_card_collection_validation() -> None:
    assert decide_card_collection_validation(has_error=False, saved_count=1).action == WorkflowAction.CONTINUE
    assert decide_card_collection_validation(has_error=True, saved_count=1).action == WorkflowAction.FINISH_TASK
    assert decide_card_collection_validation(has_error=False, saved_count=0).action == WorkflowAction.FINISH_TASK
    assert (
        decide_card_collection_validation(has_error=False, saved_count=10, result_count_number=3).action
        == WorkflowAction.FINISH_TASK
    )
