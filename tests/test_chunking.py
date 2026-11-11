import pytest

from app.rag.chunking import chunk_text


def test_chunking_is_word_aware_and_overlaps():
    chunks = chunk_text('one two three four five six seven',4,2)
    assert [c.text for c in chunks] == ['one two three four','three four five six','five six seven']
    assert chunks[1].start_word == 2

def test_chunking_rejects_invalid_configuration():
    with pytest.raises(ValueError): chunk_text('hello',10,10)
    with pytest.raises(ValueError): chunk_text('hello',0,0)

def test_empty_text_has_no_chunks():
    assert chunk_text('   ',4,1) == []
