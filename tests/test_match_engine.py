import numpy as np
import pytest

from match_engine import calculate_match, embedding_service
import os


def _fake_encode_simple(texts):
    # Provide deterministic orthogonal-ish embeddings; explicit vs inferred not distinguished here.
    vectors = []
    for i, _ in enumerate(texts):
        base = np.zeros(4, dtype=np.float32)
        base[i % 4] = 1.0
        vectors.append(base)
    return np.vstack(vectors) if vectors else np.zeros((0, 4), dtype=np.float32)


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


def test_inferred_ignored_by_default(monkeypatch):
    monkeypatch.setenv('USE_INFERRED_REQUIREMENTS', 'false')
    monkeypatch.setattr(embedding_service, 'encode', _fake_encode_simple)

    candidate = [{'skill': 'python'}]
    requirements = [
        {'skill': 'python', 'importance': 0.5, 'inferred': False},
        {'skill': 'golang', 'importance': 0.5, 'inferred': True},
    ]
    # Only explicit python matches lexically; inferred golang should not contribute when flag false.
    res = calculate_match(candidate, requirements, threshold=0.0)
    assert res['score'] == pytest.approx(0.5 / 0.5, rel=1e-3)  # explicit_weighted_sum / explicit_total_weight


def test_inferred_contributes_when_enabled(monkeypatch):
    monkeypatch.setenv('USE_INFERRED_REQUIREMENTS', 'true')
    monkeypatch.setattr(embedding_service, 'encode', _fake_encode_simple)

    candidate = [{'skill': 'python'}, {'skill': 'golang'}]
    requirements = [
        {'skill': 'python', 'importance': 0.5, 'inferred': False},
        {'skill': 'golang', 'importance': 0.5, 'inferred': True},
    ]
    # Both requirements match exactly; inferred will be subject to 20% cap.
    res = calculate_match(candidate, requirements, threshold=0.0)
    # Raw explicit = 0.5 / 1.0 = 0.5
    # Raw inferred = 0.5 / 1.0 = 0.5, cap = 0.2 * (0.5 + 0.5) = 0.2
    # Final = 0.5 + 0.2 = 0.7
    assert res['score'] == pytest.approx(0.7, rel=1e-3)
