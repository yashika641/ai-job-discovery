from jobscraper.models import Job
from jobscraper.pipeline import (
    _location_qualifies,
    _not_recommended_reason,
    _qualifies_for_recommendation,
    _within_experience_cap,
)

HUB_CITIES = ["Gurgaon", "Gurugram", "Noida"]


def _job(
    remote=True,
    location="",
    description="",
    rank_score=80.0,
):
    return Job(
        company_name="Acme",
        title="AI Engineer",
        apply_url="u1",
        source="test",
        remote=remote,
        location=location,
        description=description,
        rank_score=rank_score,
    )


# -- _within_experience_cap ------------------------------------------------


def test_within_experience_cap_when_yoe_not_mentioned():
    job = _job(description="Great team, competitive pay.")
    assert _within_experience_cap(job, max_years=5) is True


def test_within_experience_cap_when_at_the_limit():
    job = _job(description="Requires 5 years of experience.")
    assert _within_experience_cap(job, max_years=5) is True


def test_outside_experience_cap_when_above_the_limit():
    job = _job(description="Requires 7+ years of experience.")
    assert _within_experience_cap(job, max_years=5) is False


# -- _location_qualifies ----------------------------------------------------


def test_location_qualifies_remote_india():
    job = _job(remote=True, location="Remote, India")
    assert _location_qualifies(job, HUB_CITIES) is True


def test_location_qualifies_remote_worldwide():
    job = _job(remote=True, location="Worldwide")
    assert _location_qualifies(job, HUB_CITIES) is True


def test_location_qualifies_remote_unspecified():
    job = _job(remote=True, location="Remote")
    assert _location_qualifies(job, HUB_CITIES) is True


def test_location_disqualifies_remote_restricted_to_another_region():
    job = _job(remote=True, location="Remote (US Only)")
    assert _location_qualifies(job, HUB_CITIES) is False


def test_location_disqualifies_remote_region_without_the_word_only():
    # Regression: previously only "X only" phrasing was caught, so a listing
    # like this slipped through as if it were open to India.
    job = _job(remote=True, location="Remote (United States | Canada)")
    assert _location_qualifies(job, HUB_CITIES) is False


def test_location_qualifies_when_india_listed_alongside_other_regions():
    job = _job(remote=True, location="Remote - India, US, or Worldwide")
    assert _location_qualifies(job, HUB_CITIES) is True


def test_location_disqualifies_remote_city_with_no_country_named():
    # Regression: a country-name blacklist misses bare city names entirely
    # ("Remote (New York)", "SF Office" tagged remote=True in real data) --
    # any location detail beyond the bare word "remote" now needs an
    # explicit India/worldwide match instead of relying on recognizing the
    # specific non-India place.
    for loc in ["Remote (New York)", "Remote (San Francisco)", "SF Office", "Austin, TX"]:
        job = _job(remote=True, location=loc)
        assert _location_qualifies(job, HUB_CITIES) is False, loc


def test_location_qualifies_remote_indian_city_without_the_word_india():
    job = _job(remote=True, location="Remote, Bangalore")
    assert _location_qualifies(job, HUB_CITIES) is True


def test_location_qualifies_onsite_in_gurgaon():
    job = _job(remote=False, location="Gurgaon, India")
    assert _location_qualifies(job, HUB_CITIES) is True


def test_location_qualifies_hybrid_in_noida():
    job = _job(remote=False, location="Noida (Hybrid)")
    assert _location_qualifies(job, HUB_CITIES) is True


def test_location_disqualifies_onsite_in_other_indian_city():
    job = _job(remote=False, location="Bangalore, India")
    assert _location_qualifies(job, HUB_CITIES) is False


def test_location_disqualifies_onsite_non_hub_non_remote():
    job = _job(remote=False, location="Berlin, Germany")
    assert _location_qualifies(job, HUB_CITIES) is False


# -- _qualifies_for_recommendation / _not_recommended_reason ---------------
# By the point these run, experience/location/skill-overlap have already
# been hard-filtered upstream in run_pipeline -- only score matters here.


def test_qualifies_when_score_at_or_above_threshold():
    job = _job(rank_score=70.0)
    assert _qualifies_for_recommendation(job, min_score=70.0) is True


def test_disqualified_when_score_below_threshold():
    job = _job(rank_score=69.9)
    assert _qualifies_for_recommendation(job, min_score=70.0) is False


def test_not_recommended_reason_reports_the_score_gap():
    job = _job(rank_score=40.0)
    reason = _not_recommended_reason(job, min_score=70.0)
    assert reason == "score 40 below your 70-point recommendation threshold"
