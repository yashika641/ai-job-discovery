from jobscraper.text_match import contains_term


def test_matches_whole_word():
    assert contains_term("rag", "Experience with RAG pipelines") is True
    assert contains_term("hr", "HR Manager") is True


def test_does_not_match_inside_an_unrelated_word():
    assert contains_term("rag", "average salary") is False
    assert contains_term("rag", "cloud storage") is False
    assert contains_term("gpt", "familiar with ChatGPT") is False
    assert contains_term("sales", "Salesforce Administrator") is False
    assert contains_term("hr", "Chris Smith") is False


def test_matches_simple_plural():
    assert contains_term("agent", "our AI agents work today") is True


def test_matches_multi_word_phrase():
    assert contains_term("account executive", "Senior Account Executive") is True
    assert contains_term("account executive", "Accounting Executive Assistant") is False


def test_case_insensitive():
    assert contains_term("Python", "requires PYTHON experience") is True


def test_blank_term_never_matches():
    assert contains_term("", "anything") is False
    assert contains_term("   ", "anything") is False
