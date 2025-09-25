from parsers.job_parser import job_parser


def test_parse_job_text_generates_requirements():
    job_text = (
        'We are looking for a software engineer with strong Python and AWS '
        'experience. Familiarity with Docker and CI/CD pipelines is a plus.'
    )
    result = job_parser.parse(
        data=None,
        text=job_text,
        filename=None,
        mime_type='text/plain',
        title='Software Engineer'
    )

    requirement_skills = {req['skill'] for req in result['requirements']}
    assert 'python' in requirement_skills, 'Python requirement should be detected'
    assert result['summary']['requirements_count'] >= 1, 'Summary should include requirement count'
