import numpy as np
import pytest

from match_engine import calculate_match, embedding_service


def test_calculate_match_identifies_strengths_and_gaps(monkeypatch):
    # Map each text to a simple 2D vector to keep cosine similarity deterministic.
    def fake_encode(texts):
        vectors = []
        for text in texts:
            text_lower = text.lower()
            if 'python' in text_lower:
                vectors.append(np.array([1.0, 0.0], dtype=np.float32))
            elif 'aws' in text_lower:
                vectors.append(np.array([0.8, 0.6], dtype=np.float32))
            elif 'kubernetes' in text_lower:
                vectors.append(np.array([0.2, 1.0], dtype=np.float32))
            else:
                vectors.append(np.array([0.0, 0.0], dtype=np.float32))
        return np.vstack(vectors)

    monkeypatch.setattr(embedding_service, 'encode', fake_encode)

    candidate_skills = [
        {'skill': 'Python', 'experience_years': 5},
        {'skill': 'AWS'},
    ]
    requirements = [
        {'skill': 'Python', 'importance': 0.7},
        {'skill': 'Kubernetes', 'importance': 0.3},
    ]

    result = calculate_match(candidate_skills, requirements, threshold=0.5)

    strengths = result['summary']['strengths']
    gaps = result['summary']['gaps']

    assert any(item['requirement'] == 'Python' for item in strengths)
    assert any(item['requirement'] == 'Kubernetes' for item in gaps)
    assert pytest.approx(result['score'], rel=1e-2) == 0.7
