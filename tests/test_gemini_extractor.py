import json
from unittest.mock import MagicMock, patch

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
