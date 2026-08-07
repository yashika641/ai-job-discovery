import json
from unittest.mock import MagicMock, patch

from jobscraper.db import Database
from jobscraper.gemini_extractor import extract_keywords
from jobscraper.models import Job


def _job(job_hash: str, description: str = "Some JD text.") -> Job:
    job = Job(
        company_name="Acme",
        title="AI Engineer",
        apply_url=f"https://acme.com/{job_hash}",
        source="test",
        description=description,
    )
    job.job_hash = job_hash
    return job


def _mock_response(payload: list[dict]) -> MagicMock:
    response = MagicMock()
    response.text = json.dumps(payload)
    return response


def test_no_op_when_api_key_missing():
    jobs = [_job("h1")]
    extract_keywords(api_key=None, model="gemini-2.0-flash", jobs=jobs)
    assert jobs[0].jd_keywords == []


def test_skips_jobs_with_no_description():
    jobs = [_job("h1", description="   ")]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
        extract_keywords(api_key="fake-key", model="gemini-2.0-flash", jobs=jobs)
    mock_client_cls.assert_not_called()
    assert jobs[0].jd_keywords == []


def test_assigns_ranked_keywords_from_a_successful_response():
    jobs = [_job("h1"), _job("h2")]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.return_value = _mock_response(
            [
                {"id": "h1", "skills": ["Python", "RAG", "LangChain"]},
                {"id": "h2", "skills": ["Kubernetes", "Go"]},
            ]
        )
        extract_keywords(api_key="fake-key", model="gemini-2.0-flash", jobs=jobs, batch_size=10)

    assert jobs[0].jd_keywords == ["Python", "RAG", "LangChain"]
    assert jobs[1].jd_keywords == ["Kubernetes", "Go"]
    mock_client.models.generate_content.assert_called_once()


def test_batches_requests_according_to_batch_size():
    jobs = [_job(f"h{i}") for i in range(5)]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.side_effect = [
            _mock_response([{"id": f"h{i}", "skills": ["Python"]} for i in range(2)]),
            _mock_response([{"id": f"h{i}", "skills": ["Python"]} for i in range(2, 4)]),
            _mock_response([{"id": "h4", "skills": ["Python"]}]),
        ]
        extract_keywords(api_key="fake-key", model="gemini-2.0-flash", jobs=jobs, batch_size=2)

    assert mock_client.models.generate_content.call_count == 3
    assert all(job.jd_keywords == ["Python"] for job in jobs)


def test_malformed_response_leaves_jobs_with_empty_keywords_and_does_not_raise():
    jobs = [_job("h1")]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        response = MagicMock()
        response.text = "not valid json"
        mock_client.models.generate_content.return_value = response
        extract_keywords(api_key="fake-key", model="gemini-2.0-flash", jobs=jobs)

    assert jobs[0].jd_keywords == []


def test_api_exception_leaves_jobs_with_empty_keywords_and_does_not_raise():
    jobs = [_job("h1")]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.side_effect = RuntimeError("network down")
        extract_keywords(api_key="fake-key", model="gemini-2.0-flash", jobs=jobs)

    assert jobs[0].jd_keywords == []


def test_job_missing_from_response_keeps_empty_keywords():
    jobs = [_job("h1"), _job("h2")]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.return_value = _mock_response(
            [{"id": "h1", "skills": ["Python"]}]
        )
        extract_keywords(api_key="fake-key", model="gemini-2.0-flash", jobs=jobs)

    assert jobs[0].jd_keywords == ["Python"]
    assert jobs[1].jd_keywords == []


def test_throttles_to_stay_under_requests_per_minute():
    jobs = [_job("h1"), _job("h2")]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls, patch(
        "jobscraper.gemini_extractor.time.sleep"
    ) as mock_sleep:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.side_effect = [
            _mock_response([{"id": "h1", "skills": ["Python"]}]),
            _mock_response([{"id": "h2", "skills": ["Go"]}]),
        ]
        extract_keywords(
            api_key="fake-key",
            model="gemini-2.0-flash",
            jobs=jobs,
            batch_size=1,
            requests_per_minute=1,
        )

    # Second batch exceeds the 1-request-per-minute budget, so the limiter
    # must have slept before firing it.
    assert mock_sleep.called
    assert jobs[0].jd_keywords == ["Python"]
    assert jobs[1].jd_keywords == ["Go"]


def test_retries_on_rate_limit_error_then_succeeds():
    jobs = [_job("h1")]
    rate_limit_error = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
    rate_limit_error.code = 429
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls, patch(
        "jobscraper.gemini_extractor.time.sleep"
    ) as mock_sleep:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.side_effect = [
            rate_limit_error,
            _mock_response([{"id": "h1", "skills": ["Python"]}]),
        ]
        extract_keywords(
            api_key="fake-key", model="gemini-2.0-flash", jobs=jobs, max_retries=3
        )

    assert jobs[0].jd_keywords == ["Python"]
    assert mock_client.models.generate_content.call_count == 2
    assert mock_sleep.called  # backed off before the retry


def test_non_rate_limit_error_is_not_retried():
    jobs = [_job("h1")]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.side_effect = RuntimeError("network down")
        extract_keywords(
            api_key="fake-key", model="gemini-2.0-flash", jobs=jobs, max_retries=3
        )

    assert jobs[0].jd_keywords == []
    mock_client.models.generate_content.assert_called_once()


def test_stops_calling_gemini_once_daily_budget_is_spent():
    jobs = [_job("h1"), _job("h2")]
    with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.models.generate_content.return_value = _mock_response(
            [{"id": "h1", "skills": ["Python"]}]
        )
        extract_keywords(
            api_key="fake-key",
            model="gemini-2.0-flash",
            jobs=jobs,
            batch_size=1,
            requests_per_day=1,
        )

    mock_client.models.generate_content.assert_called_once()
    assert jobs[0].jd_keywords == ["Python"]
    assert jobs[1].jd_keywords == []  # left for the plain substring fallback


def test_daily_budget_persists_across_calls_via_db(tmp_path):
    db = Database(tmp_path / "test.db")
    try:
        with patch("jobscraper.gemini_extractor.genai.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.models.generate_content.return_value = _mock_response(
                [{"id": "h1", "skills": ["Python"]}]
            )
            extract_keywords(
                api_key="fake-key",
                model="gemini-2.0-flash",
                jobs=[_job("h1")],
                requests_per_day=1,
                db=db,
            )

            second_run_jobs = [_job("h2")]
            extract_keywords(
                api_key="fake-key",
                model="gemini-2.0-flash",
                jobs=second_run_jobs,
                requests_per_day=1,
                db=db,
            )

        # Budget was already spent by the first call, so the second (a
        # separate extract_keywords call, simulating a second run the same
        # day) must not call Gemini again.
        mock_client.models.generate_content.assert_called_once()
        assert second_run_jobs[0].jd_keywords == []
    finally:
        db.close()
