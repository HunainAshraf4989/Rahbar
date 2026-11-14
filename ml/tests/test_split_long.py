import numpy as np

from ml.asr.split_long import choose_cut, viterbi


def test_viterbi_recovers_token_positions():
    tokens = [1, 2, 2, 1]
    frames = [0, 1, 1, 0, 2, 0, 2, 2, 1, 0]  # the repeated token needs a blank between
    emission = np.full((len(frames), 3), -10.0)
    emission[np.arange(len(frames)), frames] = -0.1
    path, score = viterbi(emission, tokens)
    assert path.tolist() == [0, 1, 1, 2, 3, 4, 5, 5, 7, 8]
    assert score > -0.2


def test_cut_is_the_longest_pause_that_keeps_both_parts_within_30s():
    times = [(0.0, 4.0), (4.1, 9.0), None, (11.0, 20.0), (20.2, 28.5), (32.5, 34.0)]
    last_word, when, pause = choose_cut(times, duration=35.0)
    assert (last_word, when, pause) == (2, 10.0, 2.0)  # the 4 s pause at 30.5 s is too late
    assert choose_cut([(0.0, 2.0), (33.0, 35.0)], duration=35.0) is not None
    assert choose_cut([(0.0, 1.0), (1.2, 3.0), (3.1, 35.0)], duration=35.0) is None
