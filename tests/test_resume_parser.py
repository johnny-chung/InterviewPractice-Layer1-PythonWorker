import pytest

from parsers.resume_parser import resume_parser


def test_parse_resume_extracts_skills_and_sections():
    sample_text = (
        'SUMMARY\n'
        'Experienced engineer with cloud background.\n'
        'SKILLS\n'
        'Python, AWS, CI/CD\n'
        'EXPERIENCE\n'
        'Built distributed systems using Python and AWS.\n'
    )
    result = resume_parser.parse(sample_text.encode('utf-8'), 'resume.txt', 'text/plain')

    assert 'SKILLS' in result['sections'], 'Expected SKILLS section to be detected'
    skill_names = {s['skill'] for s in result['skills']}
    assert 'python' in skill_names, 'Python should be extracted as a skill'
    assert result['statistics']['tokens'] > 0, 'Token count should be greater than zero'
