from jobscraper.config import ProfileConfig, RolesConfig
from jobscraper.models import Job
from jobscraper.ranking import (
    _extract_min_years_required,
    _match_skills,
    _score_yoe,
    cap_per_company,
    extract_required_years,
    rank_jobs,
    score_job,
)

PROFILE = ProfileConfig(
    max_years_experience=3,
    preferred_countries=["India", "Remote"],
    remote_preference="strong",
    preferred_keywords=["python", "llm", "rag", "pytorch"],
)
ROLES = RolesConfig(include=["AI Engineer", "Machine Learning Engineer"])


def test_perfect_match_scores_five_stars():
    job = Job(
        company_name="Acme",
        title="AI Engineer",
        apply_url="https://acme.com/1",
        source="test",
        location="Remote, India",
        remote=True,
        description="Use Python, LLM, RAG and PyTorch. Requires 1-2 years of experience.",
    )
    result = score_job(job, PROFILE, ROLES)
    assert result.stars == 5
    assert "AI Engineer" in result.reason or "title matches" in result.reason
    assert result.required_years_experience == 1
    assert result.has_stack_overlap is True


def test_weak_match_scores_low():
    job = Job(
        company_name="Acme",
        title="AI Engineer",
        apply_url="https://acme.com/2",
        source="test",
        location="Germany",
        remote=False,
        description="Requires 8+ years of experience with obscure tools.",
    )
    result = score_job(job, PROFILE, ROLES)
    assert result.stars <= 2
    assert result.required_years_experience == 8


def test_higher_score_ranks_above_lower():
    strong = Job(
        company_name="Acme", title="AI Engineer", apply_url="u1", source="test",
        location="Remote, India", remote=True, description="Python LLM RAG PyTorch 0-2 years",
    )
    weak = Job(
        company_name="Acme", title="Machine Learning Engineer", apply_url="u2", source="test",
        location="Germany", remote=False, description="10+ years required",
    )
    strong_result = score_job(strong, PROFILE, ROLES)
    weak_result = score_job(weak, PROFILE, ROLES)
    assert strong_result.score > weak_result.score


def test_extract_min_years_from_range_takes_lower_bound():
    assert _extract_min_years_required("Requires 3-5 years of experience") == 3


def test_extract_min_years_from_plus_form():
    assert _extract_min_years_required("5+ years in ML") == 5


def test_extract_min_years_from_single_number():
    assert _extract_min_years_required("2 years of Python experience") == 2


def test_extract_min_years_from_yrs_abbreviation():
    assert _extract_min_years_required("Requires 2 yrs of experience") == 2


def test_extract_min_years_from_yr_abbreviation():
    assert _extract_min_years_required("Requires 2 yr experience") == 2


def test_extract_min_years_from_yoe_shorthand():
    assert _extract_min_years_required("3 YOE required") == 3
    assert _extract_min_years_required("3+ yoe") == 3


def test_extract_min_years_from_hyphenated_adjective_form():
    assert _extract_min_years_required("3-year experience requirement") == 3


def test_extract_min_years_from_minimum_at_least_phrasing():
    assert _extract_min_years_required("minimum of 2 years experience") == 2
    assert _extract_min_years_required("at least 2 years of experience") == 2


def test_extract_min_years_avoids_false_positives():
    assert _extract_min_years_required("Series Y funding round") is None
    assert _extract_min_years_required("Python 3.9+ required") is None
    assert _extract_min_years_required("team of 25 engineers") is None


def test_extract_min_years_returns_none_when_not_mentioned():
    assert _extract_min_years_required("Great team, competitive pay") is None


def test_yoe_at_or_under_threshold_scores_favorably():
    score, reason, required = _score_yoe("requires 2-3 years of experience", max_years_experience=3)
    assert score == 10.0
    assert "within your 3-yr preference" in reason
    assert required == 2


def test_yoe_over_threshold_scores_low():
    score, reason, required = _score_yoe("requires 7+ years of experience", max_years_experience=3)
    assert score == 2.0
    assert "above your 3-yr preference" in reason
    assert required == 7


def test_yoe_exactly_at_threshold_is_still_favorable():
    score, _, required = _score_yoe("3 years of experience required", max_years_experience=3)
    assert score == 10.0
    assert required == 3


def test_yoe_score_decays_on_a_gradient_rather_than_a_flat_drop():
    score_4yr, _, _ = _score_yoe("requires 4 years of experience", max_years_experience=3)
    score_6yr, _, _ = _score_yoe("requires 6 years of experience", max_years_experience=3)
    score_12yr, _, _ = _score_yoe("requires 12 years of experience", max_years_experience=3)
    assert score_4yr > score_6yr > score_12yr
    assert score_12yr == 1.0  # floors out rather than going negative


def test_yoe_not_mentioned_is_neutral_not_penalized():
    score, reason, required = _score_yoe("great benefits, flexible hours", max_years_experience=3)
    assert score == 5.0
    assert reason == ""
    assert required is None


def test_rank_jobs_sets_required_years_and_stack_overlap_on_the_job_object():
    favorable = Job(
        company_name="Acme", title="AI Engineer", apply_url="u1", source="test",
        description="Python and LLM experience. 1-2 years of experience.",
    )
    over_experienced = Job(
        company_name="Acme", title="AI Engineer", apply_url="u2", source="test",
        description="8+ years of experience with Python.",
    )
    no_stack_overlap = Job(
        company_name="Acme", title="AI Engineer", apply_url="u3", source="test",
        description="1 year of experience with unrelated tools.",
    )
    ranked = rank_jobs([favorable, over_experienced, no_stack_overlap], PROFILE, ROLES)
    by_url = {j.apply_url: j for j in ranked}

    assert by_url["u1"].required_years_experience == 1
    assert by_url["u1"].has_stack_overlap is True

    assert by_url["u2"].required_years_experience == 8
    assert by_url["u2"].has_stack_overlap is True

    assert by_url["u3"].required_years_experience == 1
    assert by_url["u3"].has_stack_overlap is False


def test_extract_required_years_wraps_the_private_helper_for_a_job():
    job = Job(
        company_name="Acme", title="AI Engineer", apply_url="u1", source="test",
        description="Requires 4+ years of experience.",
    )
    assert extract_required_years(job) == 4


def test_extract_required_years_none_when_not_mentioned():
    job = Job(company_name="Acme", title="AI Engineer", apply_url="u1", source="test")
    assert extract_required_years(job) is None


def test_match_skills_uses_gemini_keywords_when_present_ranked_by_importance():
    job = Job(
        company_name="Acme", title="AI Engineer", apply_url="u1", source="test",
        description="unrelated text that would not match via substring scan",
        jd_keywords=["LLM fine-tuning", "Kubernetes", "RAG pipelines", "Python scripting"],
    )
    matched = _match_skills(job, PROFILE, job.searchable_text())
    matched_names = [name for name, _ in matched]
    # "Kubernetes" isn't in PROFILE.preferred_keywords so it's excluded;
    # the rest match and keep their 1-indexed position in jd_keywords.
    assert matched == [("LLM fine-tuning", 1), ("RAG pipelines", 3), ("Python scripting", 4)]
    assert "Kubernetes" not in matched_names


def test_match_skills_does_not_false_positive_inside_unrelated_words():
    # Regression: plain substring matching used to match "rag" inside
    # "average" and "gpt" inside "chatgpt".
    job = Job(
        company_name="Acme", title="Senior Accountant", apply_url="u1", source="test",
        description="Familiarity with ChatGPT is a plus. Track average tax savings.",
    )
    matched = _match_skills(job, PROFILE, job.searchable_text())
    assert matched == []


def test_match_skills_falls_back_to_static_scan_when_no_jd_keywords():
    job = Job(
        company_name="Acme", title="AI Engineer", apply_url="u1", source="test",
        description="Python and RAG experience required.",
    )
    matched = _match_skills(job, PROFILE, job.searchable_text())
    assert {name for name, _ in matched} == {"python", "rag"}


def test_gemini_keyword_match_outweighs_a_lower_ranked_one_in_scoring():
    top_ranked = Job(
        company_name="Acme", title="AI Engineer", apply_url="u1", source="test",
        jd_keywords=["python", "unrelated a", "unrelated b", "unrelated c"],
    )
    low_ranked = Job(
        company_name="Acme", title="AI Engineer", apply_url="u2", source="test",
        jd_keywords=["unrelated a", "unrelated b", "unrelated c", "unrelated d", "unrelated e", "python"],
    )
    top_result = score_job(top_ranked, PROFILE, ROLES)
    low_result = score_job(low_ranked, PROFILE, ROLES)
    assert top_result.score > low_result.score


def test_cap_per_company_keeps_best_n_and_preserves_order():
    jobs = [
        Job(company_name="Reddit", title=f"ML Engineer {i}", apply_url=f"u{i}", source="test", rank_score=100 - i)
        for i in range(8)
    ] + [
        Job(company_name="OpenAI", title="AI Engineer", apply_url="u-openai", source="test", rank_score=95),
    ]
    jobs.sort(key=lambda j: j.rank_score, reverse=True)

    capped = cap_per_company(jobs, max_per_company=5)

    reddit_jobs = [j for j in capped if j.company_name == "Reddit"]
    assert len(reddit_jobs) == 5
    assert [j.rank_score for j in reddit_jobs] == sorted((j.rank_score for j in reddit_jobs), reverse=True)
    assert any(j.company_name == "OpenAI" for j in capped)
    assert len(capped) == 6
