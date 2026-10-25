from itertools import batched


def test_grouping():
    groups = list(batched([1, 2, 3], 2))
    assert len(groups) == 2
