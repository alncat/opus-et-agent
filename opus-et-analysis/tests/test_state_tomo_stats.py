import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import state_tomo_stats as sts


# ------------------------------------------------------------- per_tomo_stats
def test_per_tomo_stats_basic_counts_and_fractions():
    tomo = np.array(["TS_A"] * 4 + ["TS_B"] * 6)
    cluster = np.array([1, 1, 2, 3, 5, 5, 5, 6, 6, 7])
    selected = [1, 2]

    stats = sts.per_tomo_stats(tomo, cluster, selected)
    by_tomo = {s["tomo"]: s for s in stats}

    assert by_tomo["TS_A"] == {
        "tomo": "TS_A", "n_total": 4, "n_selected": 3, "frac_selected": 0.75,
    }
    assert by_tomo["TS_B"] == {
        "tomo": "TS_B", "n_total": 6, "n_selected": 0, "frac_selected": 0.0,
    }


def test_per_tomo_stats_sorted_by_n_selected_desc():
    # TS_C has the most selected picks, then TS_A, then TS_B (zero selected).
    tomo = np.array(
        ["TS_A"] * 3 + ["TS_B"] * 3 + ["TS_C"] * 5
    )
    cluster = np.array(
        [1, 1, 9] +      # TS_A: 2 selected
        [9, 9, 9] +      # TS_B: 0 selected
        [1, 1, 1, 1, 2]  # TS_C: 5 selected
    )
    selected = [1, 2]

    stats = sts.per_tomo_stats(tomo, cluster, selected)

    assert [s["tomo"] for s in stats] == ["TS_C", "TS_A", "TS_B"]
    assert [s["n_selected"] for s in stats] == [5, 2, 0]


def test_per_tomo_stats_tie_break_by_tomo_id_ascending():
    tomo = np.array(["TS_B"] * 2 + ["TS_A"] * 2)
    cluster = np.array([1, 1, 1, 1])  # both tomograms fully selected -> tie on n_selected
    selected = [1]

    stats = sts.per_tomo_stats(tomo, cluster, selected)

    assert [s["tomo"] for s in stats] == ["TS_A", "TS_B"]


def test_per_tomo_stats_all_selected_gives_frac_one():
    tomo = np.array(["TS_A"] * 5)
    cluster = np.array([1, 1, 1, 1, 1])
    stats = sts.per_tomo_stats(tomo, cluster, [1])
    assert stats == [{"tomo": "TS_A", "n_total": 5, "n_selected": 5, "frac_selected": 1.0}]


def test_per_tomo_stats_empty_selection_is_all_zero():
    tomo = np.array(["TS_A"] * 3 + ["TS_B"] * 2)
    cluster = np.array([1, 2, 3, 4, 5])

    stats = sts.per_tomo_stats(tomo, cluster, [])

    assert all(s["n_selected"] == 0 for s in stats)
    assert all(s["frac_selected"] == 0.0 for s in stats)
    by_tomo = {s["tomo"]: s for s in stats}
    assert by_tomo["TS_A"]["n_total"] == 3
    assert by_tomo["TS_B"]["n_total"] == 2


def test_per_tomo_stats_zero_selected_tomogram_among_others():
    # TS_A and TS_C have some selected picks; TS_B has none at all.
    tomo = np.array(["TS_A"] * 2 + ["TS_B"] * 2 + ["TS_C"] * 2)
    cluster = np.array([1, 2, 9, 9, 1, 9])
    selected = [1, 2]

    stats = sts.per_tomo_stats(tomo, cluster, selected)
    by_tomo = {s["tomo"]: s for s in stats}

    assert by_tomo["TS_B"]["n_selected"] == 0
    assert by_tomo["TS_B"]["frac_selected"] == 0.0
    assert by_tomo["TS_B"]["n_total"] == 2
    assert by_tomo["TS_A"]["n_selected"] == 2
    assert by_tomo["TS_C"]["n_selected"] == 1


def test_per_tomo_stats_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        sts.per_tomo_stats(np.array(["TS_A"] * 3), np.array([1, 2]), [1])


def test_per_tomo_stats_single_tomogram_no_selection_match():
    tomo = np.array(["TS_A"] * 4)
    cluster = np.array([3, 4, 5, 6])
    stats = sts.per_tomo_stats(tomo, cluster, [1, 2])
    assert stats == [{"tomo": "TS_A", "n_total": 4, "n_selected": 0, "frac_selected": 0.0}]
