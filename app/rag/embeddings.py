MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Lazy loading: module and model are only imported/loaded on first use
_model = None


def _get_model():
    """
    Load the model on first use and cache it.
    Imports sentence_transformers lazily to avoid slow import at module load time.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_text(text: str) -> list:
    """
    Generate embedding vector for text.
    """
    model = _get_model()
    vector = model.encode(text)

    return vector.tolist()