"""Brain Embeddings - Real semantic embeddings using sentence-transformers.

Uses sentence-transformers/all-MiniLM-L6-v2 (384-dim, ~80MB, runs locally).
Falls back to TF-IDF if model cannot be loaded.

Embeddings are stored as JSON arrays in the embeddings table.
Cosine similarity is computed in Python (fine for small-to-medium databases).
"""

import json
import math
import os
import re
import hashlib
import logging
from typing import List, Optional

logger = logging.getLogger("brain-embeddings")

# Lazy-loaded model
_model = None
_tokenizer = None
_embedding_dim = 384  # all-MiniLM-L6-v2 output dimension
_model_version = None  # Track which model produced embeddings


def _load_model():
    """Lazy-load the sentence-transformers model."""
    global _model, _tokenizer, _model_version
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
        _model_version = model_name
        logger.info("Embedding model loaded successfully")
        return _model, _tokenizer

    except Exception as e:
        logger.warning(f"Could not load sentence-transformers model: {e}")
        logger.warning("Falling back to TF-IDF embeddings")
        _model = "tfidf_fallback"
        _tokenizer = None
        _model_version = "tfidf_fallback_v1"
        return _model, _tokenizer


def get_embedding_version() -> str:
    """Return the embedding model version currently in use."""
    _load_model()
    return _model_version


def _mean_pool_with_attention_mask(outputs, attention_mask):
    """Mean pooling yang memperhitungkan attention mask (tidak include padding).

    Sebaliknya mean pooling sederhana (mean(dim=1)) memberikan bobot sama
    pada token padding, yang menurunkan kualitas embedding untuk teks pendek.
    """
    import torch

    token_embeddings = outputs.last_hidden_state  # (batch, seq_len, dim)
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(
        token_embeddings.size()
    ).float()
    sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, 1)
    sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)
    return sum_embeddings / sum_mask


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
        # Attention-mask-aware mean pooling
        embedding = _mean_pool_with_attention_mask(outputs, inputs["attention_mask"])
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
        # Attention-mask-aware mean pooling
        embeddings = _mean_pool_with_attention_mask(
            outputs, inputs["attention_mask"]
        )
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


def _stable_hash(token: str, dim: int) -> int:
    """Stable hash untuk TF-IDF fallback (deterministik lintas restart).

    Python's built-in hash() di-randomisasi per restart (PYTHONHASHSEED),
    sehingga tidak aman untuk embedding yang disimpan di database.
    Menggunakan hashlib.md5 sebagai pengganti.
    """
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:4], "little") % dim


def _tfidf_embed(text: str) -> List[float]:
    """Simple TF-IDF fallback embedding (fixed dimension for compatibility).

    Uses stable hash (md5) so vectors are deterministic across restarts.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
    if not tokens:
        return [0.0] * _embedding_dim

    # Stable hash-based projection to fixed dimension
    vector = [0.0] * _embedding_dim
    for token in tokens:
        h = _stable_hash(token, _embedding_dim)
        vector[h] += 1.0

    # Normalize
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]
    return vector


def embedding_dim() -> int:
    """Return the embedding dimension."""
    return _embedding_dim
