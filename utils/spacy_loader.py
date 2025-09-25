"""Shared spaCy pipeline loader with graceful fallback."""

import logging
import spacy

_nlp = None

def get_nlp():
    """Return a cached spaCy pipeline, adding a sentencizer when model is missing."""
    global _nlp
    if _nlp is not None:
        return _nlp
    try:
        _nlp = spacy.load('en_core_web_sm')
    except OSError:
        logging.getLogger(__name__).warning(
            "spaCy model 'en_core_web_sm' not found. Falling back to blank English pipeline."
        )
        _nlp = spacy.blank('en')
        if 'sentencizer' not in _nlp.pipe_names:
            _nlp.add_pipe('sentencizer')
    return _nlp
