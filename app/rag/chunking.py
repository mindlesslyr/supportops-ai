from pydantic import BaseModel


class TextChunk(BaseModel):
    index: int
    text: str
    start_word: int
    end_word: int


def chunk_text(text: str, chunk_size: int = 120, overlap: int = 20) -> list[TextChunk]:
    """Split text on words with a validated sliding overlap."""
    if chunk_size <= 0:
        raise ValueError('chunk_size must be positive')
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError('overlap must be >= 0 and smaller than chunk_size')
    words = text.split()
    if not words:
        return []
    chunks: list[TextChunk] = []
    step = chunk_size - overlap
    for index, start in enumerate(range(0, len(words), step)):
        end = min(start + chunk_size, len(words))
        chunks.append(TextChunk(index=index, text=' '.join(words[start:end]), start_word=start, end_word=end))
        if end == len(words):
            break
    return chunks
