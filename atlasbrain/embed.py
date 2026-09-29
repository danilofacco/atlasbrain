"""Embeddings 100% locais via fastembed (ONNX). O modelo é baixado uma vez e fica em cache."""

import threading

import numpy as np

from .config import EMBED_ENABLED, EMBED_MODEL, MODEL_CACHE

_model = None
_lock = threading.Lock()


def enabled() -> bool:
    return EMBED_ENABLED


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding

            MODEL_CACHE.mkdir(parents=True, exist_ok=True)
            _model = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(MODEL_CACHE))
        return _model


def embed(texts: list[str]) -> np.ndarray:
    model = _get_model()
    with _lock:
        vecs = np.array(list(model.embed(texts, batch_size=32)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return vecs / norms
