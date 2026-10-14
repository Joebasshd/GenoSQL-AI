from app import config

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
        _model = SentenceTransformer(config.EMBEDDING_MODEL)
    return _model


def embed_text(text: str) -> list:
    """
    Generate a normalised embedding vector for text.
    """
    return _get_model().encode(text, normalize_embeddings=True).tolist()


def to_pgvector(vector: list) -> str:
    """pgvector text literal, for binding as a parameter and casting with ::vector."""
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"
