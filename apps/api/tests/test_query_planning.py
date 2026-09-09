from science_buddy.services.query_planning import (
    DeterministicQueryPlanner,
    expand_plan_with_mesh,
)


def test_chinese_query_keeps_original_dense_and_builds_english_routes() -> None:
    plan = DeterministicQueryPlanner().plan("BRAF V600E 对乳头状甲状腺癌预后的影响")

    assert plan.language == "mixed"
    assert plan.english_query is not None
    assert "papillary thyroid carcinoma" in plan.english_query
    assert "prognosis" in plan.english_query
    assert plan.dense_queries[0] == (
        "dense_original",
        "BRAF V600E 对乳头状甲状腺癌预后的影响",
    )
    assert plan.dense_queries[1][0] == "dense_translated"
    assert "BRAF" in (plan.simple_query or "")


def test_identifier_intents_are_deterministic() -> None:
    planner = DeterministicQueryPlanner()

    assert planner.plan("PMID: 12345678").identifier_kind == "pmid"
    assert planner.plan("PMC1234567").identifier_kind == "pmcid"
    doi = planner.plan("https://doi.org/10.1000/TRACE.001")
    assert doi.identifier_kind == "doi"
    assert doi.identifier_value == "10.1000/trace.001"


def test_mesh_expansion_adds_only_lexically_overlapping_labels() -> None:
    plan = DeterministicQueryPlanner().plan("免疫治疗对甲状腺癌预后的影响")
    labels = [
        "Thyroid Neoplasms",
        "Immunotherapy",
        "Prognosis",
        "Unrelated Blood Coagulation",
    ]

    expanded = expand_plan_with_mesh(plan, labels, max_terms=4)

    assert expanded.english_query is not None
    assert "Thyroid Neoplasms" in expanded.english_query
    assert "prognosis" in expanded.english_query.casefold()
    assert "Unrelated" not in expanded.english_query
    assert any(
        expansion.authority == "project-mesh-v1" for expansion in expanded.expansions
    )
    assert expanded.dense_queries[-1][0] == "dense_mesh_expanded"


def test_mesh_expansion_is_noop_for_english_or_unmatched_labels() -> None:
    english_plan = DeterministicQueryPlanner().plan("thyroid carcinoma immunotherapy")
    assert expand_plan_with_mesh(english_plan, ["Thyroid Neoplasms"]) == english_plan

    mixed_plan = DeterministicQueryPlanner().plan("免疫治疗对甲状腺癌预后的影响")
    assert (
        expand_plan_with_mesh(mixed_plan, ["Unrelated Blood Coagulation"])
        == mixed_plan
    )
    assert expand_plan_with_mesh(mixed_plan, []) == mixed_plan


def test_mesh_expansion_respects_label_cap() -> None:
    plan = DeterministicQueryPlanner().plan("免疫治疗对甲状腺癌预后的影响")
    labels = ["Immune Checkpoint Inhibitors", "Prognosis", "Thyroid Neoplasms"]

    expanded = expand_plan_with_mesh(plan, labels, max_terms=1)

    assert expanded.english_query is not None
    assert "immune" in expanded.english_query.casefold()
    assert "checkpoint" in expanded.english_query.casefold()
    assert "Thyroid Neoplasms" not in expanded.english_query
    mesh_expansions = [
        expansion
        for expansion in expanded.expansions
        if expansion.authority == "project-mesh-v1"
    ]
    assert len(mesh_expansions) == 1
