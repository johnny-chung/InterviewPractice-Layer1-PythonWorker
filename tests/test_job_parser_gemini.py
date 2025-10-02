import types
from parsers.job_parser import job_parser

# We monkeypatch the gemini_client module attributes used inside job_parser.
# This avoids requiring the real google-generativeai package or an API key.

def test_job_parser_gemini_merge(monkeypatch):
    # Simulate gemini enabled
    fake_module = types.SimpleNamespace()
    fake_module.is_enabled = lambda: True
    # Gemini returns one overlapping skill (python) and one new (falcon)
    fake_module.extract_technologies = lambda text: [
        {"skill": "python", "importance": 1.0},
        {"skill": "falcon", "importance": 0.8},
    ]

    # Patch the imported gemini_client symbol inside the job_parser module
    from parsers import job_parser as jp_mod
    monkeypatch.setattr(jp_mod, 'gemini_client', fake_module, raising=True)

    sample_text = "We are looking for a Python engineer. Nice to have: Falcon framework experience."
    result = job_parser.parse(
        data=None,
        text=sample_text,
        filename=None,
        mime_type=None,
        title="Software Engineer",
    )

    skills = {r['skill']: r for r in result['requirements']}
    # 'python' should exist (from dictionary baseline) but not duplicated.
    assert 'python' in skills
    # New gemini-provided 'falcon' should be present with provided importance.
    assert 'falcon' in skills
    assert skills['falcon']['importance'] == 0.8
    # Ensure neither is marked inferred.
    assert not skills['python'].get('inferred')
    assert not skills['falcon'].get('inferred')
