"""Brain Embeddings - Real semantic embeddings using sentence-transformers.

Uses sentence-transformers/all-MiniLM-L6-v2 (384-dim, ~80MB, runs locally).
Falls back to TF-IDF if model cannot be loaded.

Embeddings are stored as JSON arrays in the embeddings table.
Cosine similarity is computed in Python (fine for small-to-medium databases).
"""

import json
import math
import os
import logging
from typing import List, Optional

logger = logging.getLogger("brain-embeddings")

# Lazy-loaded model
_model = None
_tokenizer = None
_embedding_dim = 384  # all-MiniLM-L6-v2 output dimension


def _load_model():
    """Lazy-load the sentence-transformers model."""
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    try:
        import torch
        from transformers import AutoTokenizer, AutoModel

        model_name = os.environ.get(
            "BRAIN_EMBEDDING_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        )
        logger.info(f"Loading embedding model: {model_name}")
        _tokenizer = AutoTokenizer.from_pretrained(model_name)
        _model = AutoModel.from_pretrained(model_name)
        _model.eval()
        logger.info("Embedding model loaded successfully")
        return _model, _tokenizer

    except Exception as e:
        logger.warning(f"Could not load sentence-transformers model: {e}")
        logger.warning("Falling back to TF-IDF embeddings")
        _model = "tfidf_fallback"
        _tokenizer = None
        return _model, _tokenizer


def embed_text(text: str) -> List[float]:
    """Embed a single text string into a vector.

    Returns a list of floats (384 dimensions for all-MiniLM-L6-v2).
    Falls back to TF-IDF if model unavailable.
    """
    model, tokenizer = _load_model()

    if model == "tfidf_fallback":
        return _tfidf_embed(text)

    import torch

    inputs = tokenizer(
        text, padding=True, truncation=True, return_tensors="pt", max_length=512
    )
    with torch.no_grad():
        outputs = model(**inputs)
        # Mean pooling
        embedding = outputs.last_hidden_state.mean(dim=1)
    return embedding[0].tolist()


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed multiple texts efficiently."""
    model, tokenizer = _load_model()

    if model == "tfidf_fallback":
        return [_tfidf_embed(t) for t in texts]

    import torch

    inputs = tokenizer(
        texts, padding=True, truncation=True, return_tensors="pt", max_length=512
    )
    with torch.no_grad():
        outputs = model(**inputs)
        embeddings = outputs.last_hidden_state.mean(dim=1)
    return embeddings.tolist()


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not vec_a or not vec_b:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tfidf_embed(text: str) -> List[float]:
    """Simple TF-IDF fallback embedding (fixed dimension for compatibility).

    This is a placeholder that returns a deterministic vector based on
    character n-grams. Not as good as real embeddings but functional.
    """
    import re

    tokens = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
    if not tokens:
        return [0.0] * _embedding_dim

    # Simple hash-based projection to fixed dimension
    vector = [0.0] * _embedding_dim
    for token in tokens:
        h = abs(hash(token)) % _embedding_dim
        vector[h] += 1.0

    # Normalize
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]
    return vector


def embedding_dim() -> int:
    """Return the embedding dimension."""
    return _embedding_dim
