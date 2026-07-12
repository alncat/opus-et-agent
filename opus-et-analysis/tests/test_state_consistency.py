import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import state_consistency as sc


# --------------------------------------------------------- consistency_matrix
def test_consistency_matrix_symmetric():
    rng = np.random.RandomState(0)
    maps = [rng.rand(6, 6, 6).astype(np.float32) for _ in range(5)]
    mat = sc.consistency_matrix(maps)
    assert mat.shape == (5, 5)
    assert np.allclose(mat, mat.T)


def test_consistency_matrix_diagonal_is_one():
    rng = np.random.RandomState(1)
    maps = [rng.rand(6, 6, 6).astype(np.float32) for _ in range(4)]
    mat = sc.consistency_matrix(maps)
    assert np.diag(mat) == pytest.approx(np.ones(4), abs=1e-6)


def test_consistency_matrix_identical_maps_are_fully_correlated():
    rng = np.random.RandomState(2)
    a = rng.rand(8, 8, 8).astype(np.float32)
    maps = [a, a.copy(), a.copy()]
    mat = sc.consistency_matrix(maps)
    off_diag = mat[~np.eye(3, dtype=bool)]
    assert off_diag == pytest.approx(1.0, abs=1e-6)


def test_consistency_matrix_two_groups_within_beats_across():
    rng = np.random.RandomState(3)
    box = 10
    base_a = rng.rand(box, box, box).astype(np.float32)
    base_b = rng.rand(box, box, box).astype(np.float32)  # unrelated field
    noise = 0.05

    group_a = [base_a + noise * rng.rand(box, box, box).astype(np.float32) for _ in range(3)]
    group_b = [base_b + noise * rng.rand(box, box, box).astype(np.float32) for _ in range(3)]
    maps = group_a + group_b  # indices 0,1,2 = group A; 3,4,5 = group B

    mat = sc.consistency_matrix(maps)

    within_a = [mat[i, j] for i in range(3) for j in range(3) if i < j]
    within_b = [mat[i, j] for i in range(3, 6) for j in range(3, 6) if i < j]
    across = [mat[i, j] for i in range(3) for j in range(3, 6)]

    assert np.mean(within_a) > np.mean(across)
    assert np.mean(within_b) > np.mean(across)
    assert min(within_a) > max(across)
    assert min(within_b) > max(across)


def test_consistency_matrix_mask_restricts_correlation_region():
    box = 8
    rng = np.random.RandomState(4)
    a = rng.rand(box, box, box).astype(np.float32)
    b = a.copy()
    mask = np.zeros((box, box, box), np.float32)
    mask[2:6, 2:6, 2:6] = 1.0
    outside = mask == 0
    # corrupt b everywhere OUTSIDE the mask
    b[outside] = rng.rand(int(outside.sum())).astype(np.float32) * 100

    mat_masked = sc.consistency_matrix([a, b], mask=mask)
    mat_unmasked = sc.consistency_matrix([a, b], mask=None)

    assert mat_masked[0, 1] == pytest.approx(1.0, abs=1e-6)
    assert mat_unmasked[0, 1] < 0.9


def test_consistency_matrix_lowpass_requires_apix():
    rng = np.random.RandomState(5)
    maps = [rng.rand(6, 6, 6).astype(np.float32) for _ in range(2)]
    with pytest.raises(ValueError):
        sc.consistency_matrix(maps, lowpass_A=20.0, apix=None)


def test_consistency_matrix_lowpass_runs_and_stays_symmetric():
    rng = np.random.RandomState(6)
    maps = [rng.rand(12, 12, 12).astype(np.float32) for _ in range(3)]
    mat = sc.consistency_matrix(maps, lowpass_A=30.0, apix=3.37)
    assert mat.shape == (3, 3)
    assert np.allclose(mat, mat.T)
    assert np.diag(mat) == pytest.approx(np.ones(3), abs=1e-6)


# ------------------------------------------------------------- order_by_linkage
def test_order_by_linkage_groups_contiguous():
    rng = np.random.RandomState(7)
    box = 10
    base_a = rng.rand(box, box, box).astype(np.float32)
    base_b = rng.rand(box, box, box).astype(np.float32)
    noise = 0.05

    group_a_idx = [0, 1, 2]
    group_b_idx = [3, 4, 5]
    group_a = [base_a + noise * rng.rand(box, box, box).astype(np.float32) for _ in group_a_idx]
    group_b = [base_b + noise * rng.rand(box, box, box).astype(np.float32) for _ in group_b_idx]
    maps = group_a + group_b

    mat = sc.consistency_matrix(maps)
    order = sc.order_by_linkage(mat)

    assert sorted(order) == list(range(6))  # a genuine permutation

    rank = {idx: pos for pos, idx in enumerate(order)}
    ranks_a = sorted(rank[i] for i in group_a_idx)
    ranks_b = sorted(rank[i] for i in group_b_idx)
    # each group occupies a contiguous run of positions in the leaf order
    assert ranks_a[-1] - ranks_a[0] == len(ranks_a) - 1
    assert ranks_b[-1] - ranks_b[0] == len(ranks_b) - 1


def test_order_by_linkage_trivial_sizes():
    assert sc.order_by_linkage(np.array([[1.0]])) == [0]
    assert sorted(sc.order_by_linkage(np.eye(2))) == [0, 1]
