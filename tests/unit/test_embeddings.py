from agents.memory.embeddings import cosine_similarity


def test_cosine_self_similarity():
    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0


def test_cosine_orthogonal():
    assert cosine_similarity([1, 0, 0], [0, 1, 0]) == 0.0


def test_cosine_symmetric():
    a, b = [1, 2, 3], [4, 5, 6]
    assert cosine_similarity(a, b) == cosine_similarity(b, a)


def test_cosine_range():
    import random
    for _ in range(10):
        a = [random.random() for _ in range(16)]
        b = [random.random() for _ in range(16)]
        v = cosine_similarity(a, b)
        assert -1.0 <= v <= 1.0
