"""Match scoring helpers shared by BullMQ workers and API tests."""

from __future__ import annotations

import logging
import os
from typing import Dict, List

import numpy as np

from embeddings import embedding_service

logger = logging.getLogger(__name__)


def _normalize_skills(items: List[Dict], key: str) -> List[str]:
    """Lower-case skills/requirements and fall back to name field."""
    texts = []
    for item in items:
        value = item.get(key) or item.get('name')
        if value:
            texts.append(value.lower())
    return texts


def _cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Return cosine similarity matrix, guarding against empty inputs."""
    if a.size == 0 or b.size == 0:
        # Ensure downstream consumers see the correct matrix shape even when empty.
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.float32)
    # Pre-normalise rows so the dot product is cosine similarity.
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-8)
    return np.dot(a_norm, b_norm.T)


def calculate_match(candidate_skills: List[Dict], requirements: List[Dict], threshold: float = 0.5) -> Dict:
    """Compute weighted coverage summary with explicit vs inferred cap.

        Behavior additions:
        - Exact lexical match gating (existing logic) retained.
        - If both explicit and inferred requirements exist, inferred requirements may
            contribute at most 20% of the final score mass. This is achieved by computing
            raw weighted sums separately (explicit vs inferred) then blending:
                     final_score = explicit_score + min(inferred_score, 0.2 * (explicit_score + inferred_score))
            followed by re-normalization to keep scale in [0,1]. Implementation uses
            weight partitioning to avoid double scaling.
        - If only inferred or only explicit requirements exist, normal behavior applies.
    """
    if not requirements:
        # No requirements means no meaningful score; return empty scaffolding.
        return {
            'score': 0.0,
            'summary': {
                'overall_match_score': 0.0,
                'strengths': [],
                'gaps': [],
                'details': [],
            }
        }

    # New behavior toggle: by default inferred requirements are ignored for final score.
    raw_flag = os.getenv('USE_INFERRED_REQUIREMENTS', 'false').strip().lower()
    use_inferred = raw_flag in {'1', 'true', 'yes', 'on'}

    # Convert raw dicts into token lists we can embed.
    requirement_texts = _normalize_skills(requirements, 'skill')
    skill_texts = _normalize_skills(candidate_skills, 'skill')

    # Generate dense vectors for both sets (SBERT when available, hash fallback otherwise).
    requirement_vectors = embedding_service.encode(requirement_texts)
    skill_vectors = embedding_service.encode(skill_texts)

    # Similarity matrix rows represent requirements, columns represent candidate skills.
    similarity = _cosine_similarity_matrix(requirement_vectors, skill_vectors)

    strengths = []  # Requirements covered above the match threshold.
    gaps = []       # Requirements that remain unmet or weakly covered.
    details = []    # Full per-requirement breakdown returned to the client.
    explicit_weighted_sum = 0.0
    inferred_weighted_sum = 0.0
    explicit_total_weight = 0.0
    inferred_total_weight = 0.0

    for idx, requirement in enumerate(requirements):
        weight = float(requirement.get('importance') or 0.5)
        if requirement.get('inferred'):
            inferred_total_weight += weight
        else:
            explicit_total_weight += weight
        # Pull the similarity row for the current requirement (handles empty matrices).
        row = similarity[idx] if similarity.size else np.zeros(len(skill_texts))
        if row.size:
            best_idx = int(np.argmax(row))
            best_sim = float(row[best_idx])
            matched_skill = skill_texts[best_idx] if skill_texts else None
        else:
            best_sim = 0.0
            matched_skill = None

        # Enforce exact lexical match when deciding coverage to avoid unrelated high-sim matches.
        req_norm = (requirement.get('skill') or requirement.get('name') or '').lower()
        if matched_skill and req_norm and matched_skill == req_norm:
            effective_sim = best_sim
        else:
            # Treat as uncovered for scoring/summary purposes
            effective_sim = 0.0

        # Persist the per-requirement view for API consumers using the effective similarity
        detail = {
            'requirement': requirement.get('skill'),
            'importance': weight,
            'similarity': round(effective_sim, 3),
            'matched_skill': matched_skill,
            'inferred': requirement.get('inferred', False),
        }
        details.append(detail)

        if effective_sim >= threshold:
            strengths.append(detail)
            if requirement.get('inferred'):
                if use_inferred:
                    inferred_weighted_sum += weight * effective_sim
            else:
                explicit_weighted_sum += weight * effective_sim
        else:
            gaps.append(detail)
    # Combine explicit + (optionally) inferred ensuring inferred <= 20% cap when enabled.
    if use_inferred:
        total_weight = explicit_total_weight + inferred_total_weight
        if total_weight == 0:
            overall_score = 0.0
        else:
            raw_explicit = explicit_weighted_sum / total_weight if total_weight else 0.0
            raw_inferred = inferred_weighted_sum / total_weight if total_weight else 0.0
            cap = 0.2 * (raw_explicit + raw_inferred)
            capped_inferred = min(raw_inferred, cap)
            overall_score = raw_explicit + capped_inferred
    else:
        # Ignore inferred entirely: denominator uses only explicit weight (avoid zero div).
        total_weight = explicit_total_weight or 1.0
        overall_score = (explicit_weighted_sum / total_weight) if total_weight else 0.0

    # Round values the same way we present in the API to keep consistency for clients.
    return {
        'score': round(overall_score, 3),
        'summary': {
            'overall_match_score': round(overall_score, 3),
            'strengths': strengths,
            'gaps': gaps,
            'details': details,
        }
    }
