from liepin_agent.jd_parser import SearchConditionDraft, _coerce_raw, build_rule_based_draft, draft_to_job_fields, normalize_draft


def test_normalize_draft_keeps_only_allowed_fixed_options() -> None:
    draft = normalize_draft(
        SearchConditionDraft(
            keyword_match="随便匹配",
            keywords=["Python", "Python", "仿真"],
            work_years=["3-5年", "未知年限"],
            education=["本科", "MBA"],
            active_days="昨天活跃",
            job_status=["在职，看看新机会", "错误状态"],
        )
    )

    assert draft.keyword_match == "包含任意关键词"
    assert draft.keywords == ["Python", "仿真"]
    assert draft.work_years == ["3-5年"]
    assert draft.education == ["本科"]
    assert draft.active_days == "30天内活跃"
    assert draft.job_status == ["在职，看看新机会"]


def test_rule_based_draft_maps_to_legacy_job_fields() -> None:
    draft = build_rule_based_draft(
        "数字孪生仿真工程师",
        "负责工业仿真、生产排程、Python、Omniverse 和 Isaac Sim。",
    )
    fields = draft_to_job_fields(draft)

    assert "工业仿真" in fields["keywords"]
    assert "Python" in fields["keywords"]
    assert "生产排程" in fields["must_have"]
    assert fields["experience"]
    assert fields["education"]


def test_coerce_qwen_alias_fields() -> None:
    raw = _coerce_raw(
        {
            "keyword_match_option": "包含全部关键词",
            "work_years": "5-10年",
            "education": "硕士",
            "school_tag": "211",
            "active_within_days": 7,
            "language": ["普通话", "英语"],
            "job_status": "在职，看看新机会",
            "job_hopping_constraint": "近5年不超过3段",
        }
    )

    assert raw["keyword_match"] == "包含全部关键词"
    assert raw["work_years"] == ["5-10年"]
    assert raw["education"] == ["硕士"]
    assert raw["school_tags"] == ["211"]
    assert raw["active_days"] == "7天内活跃"
    assert raw["languages"] == ["普通话", "英语"]
    assert raw["job_status"] == ["在职，看看新机会"]
    assert raw["job_hopping"] == "近5年不超过3段"
