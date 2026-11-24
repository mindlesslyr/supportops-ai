import pytest

from app.rag.chunking import chunk_text


def test_chunking_is_word_aware_and_preserves_overlap():
    chunks = chunk_text(
        "one two three four five six seven eight nine",
        chunk_size=5,
        overlap=1,
    )

    assert [chunk.text for chunk in chunks] == [
        "one two three four five",
        "five six seven eight nine",
    ]
    assert [(chunk.start_word, chunk.end_word) for chunk in chunks] == [(0, 5), (4, 9)]


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (10, -1), (10, 10), (10, 11)],
)
def test_chunking_rejects_invalid_configuration(chunk_size, overlap):
    with pytest.raises(ValueError):
        chunk_text("hello", chunk_size, overlap)


def test_empty_text_has_no_chunks():
    assert chunk_text("   ", chunk_size=4, overlap=1) == []
