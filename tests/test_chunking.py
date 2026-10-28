from transcriber.chunking import plan_chunks


def test_short_recording_is_one_chunk():
    assert plan_chunks(300.0, []) == [(0.0, 300.0)]


def test_recording_at_max_tail_is_one_chunk():
    assert plan_chunks(660.0, []) == [(0.0, 660.0)]


def test_hard_cuts_without_silences():
    assert plan_chunks(1500.0, []) == [(0.0, 600.0), (600.0, 1200.0), (1200.0, 1500.0)]


def test_cuts_at_middle_of_silence_inside_window():
    assert plan_chunks(1000.0, [(570.0, 580.0)]) == [(0.0, 575.0), (575.0, 1000.0)]


def test_silence_crossing_window_end_is_clipped():
    # 590-620 is clipped to 590-600, so the cut lands at 595, inside the window.
    assert plan_chunks(1000.0, [(590.0, 620.0)]) == [(0.0, 595.0), (595.0, 1000.0)]


def test_picks_longest_silence_in_window():
    silences = [(545.0, 546.0), (560.0, 563.0), (590.0, 591.0)]
    assert plan_chunks(1000.0, silences)[0] == (0.0, 561.5)


def test_ignores_silences_outside_window():
    assert plan_chunks(1000.0, [(100.0, 110.0), (700.0, 710.0)]) == [(0.0, 600.0), (600.0, 1000.0)]


def test_windows_are_relative_to_previous_cut():
    silences = [(570.0, 580.0), (1150.0, 1152.0)]
    assert plan_chunks(1800.0, silences) == [(0.0, 575.0), (575.0, 1151.0), (1151.0, 1800.0)]


def test_custom_parameters():
    assert plan_chunks(25.0, [], target=10.0, window=3.0, max_tail=12.0) == [
        (0.0, 10.0), (10.0, 20.0), (20.0, 25.0)
    ]
