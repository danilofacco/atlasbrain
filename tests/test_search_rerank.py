import numpy as np

from atlasbrain.search import Searcher, _rerank_top_three


def test_second_stage_only_reorders_top_three_and_preserves_exact():
    results = [
        {'id': 1, 'score': .06, 'exato': True},
        {'id': 2, 'score': .05, 'exato': False},
        {'id': 3, 'score': .048, 'exato': False},
        {'id': 4, 'score': .047, 'exato': False},
    ]
    _rerank_top_three(results, np.array([1, 2, 3, 4]), np.array([.1, .2, .9, 1.0]))
    assert [result['id'] for result in results] == [1, 3, 2, 4]


def test_second_stage_requires_complete_code_vectors(indexado):
    _, con = indexado
    searcher = Searcher(con)
    assert not searcher._has_complete_code_vectors()
