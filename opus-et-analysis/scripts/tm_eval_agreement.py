#!/usr/bin/env python3
"""Score TM candidate picks against a trusted reference set (precision/recall/F1).

Reads two RELION/WARP star files, converts both to Angstroms using each file's own
pixel size (so a bin4 candidate set and a bin1 reference reconcile), matches candidates
to references one-to-one within a distance radius in descending-score order, and reports
a precision/recall/F1 curve over score thresholds plus the best-F1 operating point.

Recall is the headline and F1 ranks parameter sets: the reference is a curated/selected
subset, so precision is a lower bound (a real particle absent from the reference counts
as a false positive). See design spec sections 3 and 5.1.
"""
import argparse
import json
import re

import numpy as np
import pandas as pd
import starfile

DEFAULT_RADIUS_A = 136.0
SCORE_COLS = ["rlnAutopickFigureOfMerit", "rlnScore", "rlnLCCmax",
              "ccc", "score", "FLCF", "pytom_score"]


def _norm_tomo(name):
    base = str(name).rsplit("/", 1)[-1]
    base = re.sub(r"\.(tomostar|mrc|star)$", "", base)
    base = re.sub(r"_[0-9.]+Apx$", "", base)
    return base


def _read_block(path):
    data = starfile.read(path)
    if isinstance(data, pd.DataFrame):
        return data
    for block in data.values():
        if hasattr(block, "columns") and "rlnCoordinateX" in block.columns:
            return block
    raise ValueError(f"no particle block with rlnCoordinateX in {path}")


def _detect_score_col(df):
    for c in SCORE_COLS:
        if c in df.columns:
            return c
    return None


def read_star_coords(path, tomo=None, score_col=None, angpix=None, require_score=False):
    """Return a DataFrame[tomo, x_A, y_A, z_A, score] in Angstroms for one (or all) tomograms.

    require_score=True (for candidate stars) raises if no score column is found; the
    reference star legitimately has none, so it is read with require_score=False.
    """
    df = _read_block(path).copy()
    df["_tomo"] = df["rlnMicrographName"].map(_norm_tomo)
    if tomo is not None:
        df = df[df["_tomo"] == _norm_tomo(tomo)]
    df = df.reset_index(drop=True)

    if "rlnPixelSize" in df.columns:
        pix = df["rlnPixelSize"].to_numpy(float)
    elif angpix is not None:
        pix = float(angpix)
    else:
        raise ValueError(f"{path}: no rlnPixelSize column; pass angpix")

    out = pd.DataFrame({
        "tomo": df["_tomo"].to_numpy(),
        "x_A": df["rlnCoordinateX"].to_numpy(float) * pix,
        "y_A": df["rlnCoordinateY"].to_numpy(float) * pix,
        "z_A": df["rlnCoordinateZ"].to_numpy(float) * pix,
    })
    if score_col is None:
        score_col = _detect_score_col(df)
    if score_col is not None and score_col in df.columns:
        out["score"] = df[score_col].to_numpy(float)
    elif require_score:
        raise ValueError(
            f"{path}: no score column found (looked for {SCORE_COLS}); "
            f"available columns: {list(df.columns)}; pass score_col / --score-col")
    else:
        out["score"] = np.nan
    return out


def greedy_match(cand_xyz, ref_xyz, radius_A):
    """Assign each candidate (in row order) to the nearest unused reference within radius_A.

    One-to-one. Order-dependent: earlier candidates claim references first, so passing
    candidates in descending-score order yields standard detection-PR assignment.
    Returns (n_matched, cand_idx, ref_idx) with indices into the input arrays.
    """
    cand = np.asarray(cand_xyz, dtype=float)
    ref = np.asarray(ref_xyz, dtype=float)
    n_ref = len(ref)
    if len(cand) == 0 or n_ref == 0:
        return 0, np.array([], int), np.array([], int)
    used = np.zeros(n_ref, dtype=bool)
    r2 = radius_A * radius_A
    mc, mr = [], []
    for i in range(len(cand)):
        d2 = ((ref - cand[i]) ** 2).sum(axis=1)
        d2 = np.where(used, np.inf, d2)
        j = int(np.argmin(d2))
        if d2[j] <= r2:
            used[j] = True
            mc.append(i)
            mr.append(j)
    return len(mc), np.array(mc, int), np.array(mr, int)


def _metrics(tp, fp, n_ref, thr):
    fn = n_ref - tp
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / n_ref if n_ref > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"threshold": (None if thr is None else float(thr)), "tp": int(tp), "fp": int(fp),
            "fn": int(fn), "precision": precision, "recall": recall, "f1": f1}


def _label_tp(cand_df, ref_df, radius_A):
    """Match candidates to references WITHIN each tomogram; return (scores_desc, is_tp_desc, n_ref).

    Grouping by 'tomo' prevents a candidate in one tilt series from matching a reference in
    another (both cover a similar coordinate range). Frames without a 'tomo' column are
    treated as a single group. Candidates are ordered by descending score so higher-
    confidence picks claim references first (order-dependent greedy_match).
    """
    cand = (cand_df.dropna(subset=["score"])
            .sort_values("score", ascending=False, kind="stable").reset_index(drop=True))
    n_ref = len(ref_df)
    is_tp = np.zeros(len(cand), dtype=bool)
    if "tomo" in cand.columns and "tomo" in ref_df.columns:
        groups = set(cand["tomo"].unique()) | set(ref_df["tomo"].unique())
    else:
        groups = {None}
    for g in groups:
        if g is None:
            cidx = np.arange(len(cand))
            rsub = ref_df
        else:
            cidx = np.where((cand["tomo"] == g).to_numpy())[0]
            rsub = ref_df[ref_df["tomo"] == g]
        if len(cidx) == 0:
            continue
        cxyz = cand.iloc[cidx][["x_A", "y_A", "z_A"]].to_numpy(float)
        rxyz = rsub[["x_A", "y_A", "z_A"]].to_numpy(float)
        _, mc, _ = greedy_match(cxyz, rxyz, radius_A)
        if len(mc):
            is_tp[cidx[mc]] = True
    return cand["score"].to_numpy(float), is_tp, n_ref


def agreement_at_threshold(cand_df, ref_df, radius_A, thr):
    """Precision/recall/F1 for candidates with score >= thr, matched per-tomogram to the reference."""
    _, is_tp, n_ref = _label_tp(cand_df[cand_df["score"] >= thr], ref_df, radius_A)
    tp = int(is_tp.sum())
    return _metrics(tp, len(is_tp) - tp, n_ref, thr)


def agreement_curve(cand_df, ref_df, radius_A, thresholds=None):
    """Precision/recall/F1 over score thresholds. One point per distinct score if thresholds is None.

    Labels every candidate once (per-tomogram, descending-score order — prefix-consistent
    with agreement_at_threshold), then sweeps thresholds via cumulative TP/FP. With no
    candidates but a non-empty reference, returns a single all-missed point (fn = n_ref).
    """
    scores, is_tp, n_ref = _label_tp(cand_df, ref_df, radius_A)
    if len(scores) == 0:
        return [_metrics(0, 0, n_ref, None)]
    cum_tp = np.cumsum(is_tp)
    cum_fp = np.cumsum(~is_tp)

    def _point(k, thr):  # k = number of candidates with score >= thr (top-k in desc order)
        tp = int(cum_tp[k - 1]) if k > 0 else 0
        fp = int(cum_fp[k - 1]) if k > 0 else 0
        return _metrics(tp, fp, n_ref, thr)

    curve = []
    if thresholds is None:
        i, n = 0, len(scores)
        while i < n:
            j = i
            while j + 1 < n and scores[j + 1] == scores[i]:
                j += 1
            curve.append(_point(j + 1, scores[i]))
            i = j + 1
    else:
        for thr in thresholds:
            curve.append(_point(int((scores >= thr).sum()), thr))
    return curve


def best_f1(curve):
    """The curve point with the highest F1 (ties broken toward higher recall)."""
    if not curve:
        return _metrics(0, 0, 0, None)
    return max(curve, key=lambda p: (p["f1"], p["recall"]))


def main():
    ap = argparse.ArgumentParser(
        description="Score TM candidate picks against a reference set (precision/recall/F1, matched in A).")
    ap.add_argument("--candidate", required=True, help="candidate pick star (coords + score)")
    ap.add_argument("--reference", required=True, help="trusted reference pick star")
    ap.add_argument("--tomo", default=None, help="tomogram id to score, e.g. TS_034 (default: all)")
    ap.add_argument("--radius-A", type=float, default=DEFAULT_RADIUS_A,
                    help=f"match distance in Angstroms (default {DEFAULT_RADIUS_A})")
    ap.add_argument("--score-col", default=None, help="candidate score column (default: auto-detect)")
    ap.add_argument("--coords-angpix", type=float, default=None,
                    help="candidate pixel size if the star has no rlnPixelSize")
    ap.add_argument("--ref-angpix", type=float, default=None,
                    help="reference pixel size if the star has no rlnPixelSize")
    ap.add_argument("--json", dest="json_out", default=None, help="write the JSON summary here")
    args = ap.parse_args()

    cand = read_star_coords(args.candidate, tomo=args.tomo, score_col=args.score_col,
                            angpix=args.coords_angpix, require_score=True)
    ref = read_star_coords(args.reference, tomo=args.tomo, angpix=args.ref_angpix)
    curve = agreement_curve(cand, ref, args.radius_A)
    result = {
        "tomo": args.tomo, "radius_A": args.radius_A,
        "n_candidate": int(len(cand)), "n_reference": int(len(ref)),
        "best": best_f1(curve),
    }
    print(json.dumps(result, indent=2))
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(result, f, indent=2)
    return result


if __name__ == "__main__":
    main()
