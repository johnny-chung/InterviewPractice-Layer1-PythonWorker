import types

from parsers.resume_parser import resume_parser


def test_resume_parser_gemini_merge(monkeypatch):
    """Gemini extracted skills should merge & dedupe with matcher skills."""
    fake_module = types.SimpleNamespace()
    fake_module.is_enabled = lambda: True
    fake_module.extract_technologies = lambda text: [
        {"skill": "python", "importance": 1.0},  # overlap
        {"skill": "rust", "importance": 0.8},    # new
    ]

    from parsers import resume_parser as rp_mod
    monkeypatch.setattr(rp_mod, 'gemini_client', fake_module, raising=True)

    sample = (
        'SUMMARY\nEngineer experienced in Python systems.\nSKILLS\nPython, AWS\nEXPERIENCE\nBuilt tooling in Python.'
    )
    result = resume_parser.parse(sample.encode('utf-8'), 'resume.txt', 'text/plain')
    skills = {s['skill']: s for s in result['skills']}
    assert 'python' in skills, 'baseline skill present'
    assert 'rust' in skills, 'gemini-added skill present'
    assert 'gemini' in skills['rust']['source'], 'gemini provenance captured'
    assert 'matcher' in skills['python']['source'], 'matcher provenance for baseline skill'
    # Overlap should have both sources
    assert 'gemini' in skills['python']['source'], 'overlap should show gemini augment'
    assert result['statistics']['skills_gemini'] == 2