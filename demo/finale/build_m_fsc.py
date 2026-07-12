"""M-refined resolution figure — the gold-standard FSC that backs the headline
7.76 Å (ribosome) / 13.88 Å (FAS) numbers. Run from a dir holding the two WARP/M
FSC stars synced from the cluster (m/species/<hash>/<species>_fsc.star):
    ribo_fsc.star, fas_fsc.star
Writes m_refined_fsc.png + m_refined_fsc.tsv (copy into demo/qc/finale/).

The plotted curve is _wrpFSCCorrected — the phase-randomization-corrected FSC, the
same honest basis as the Gate-4 fixed-mode curve. The reported resolution is the
FSC=0.143 crossing, interpolated from the curve (not asserted).
"""
import sys
import numpy as np
import starfile
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

THRESH = 0.143
SPECIES = [
    ("Ribosome", "ribo_fsc.star", "#5B8DEF"),   # cornflower blue (matches the in-cell render)
    ("FAS",      "fas_fsc.star",  "#E0A81C"),    # gold
]


def load_fsc(path):
    df = starfile.read(path)
    cols = {c.lower(): c for c in df.columns}
    rc = next(c for k, c in cols.items() if "resolution" in k)
    cc = next(c for k, c in cols.items() if "fsccorrected" in k)
    res = np.array([np.inf if str(x).lower() == "infinity" else float(x) for x in df[rc]], float)
    fsc = np.asarray(df[cc], float)
    keep = np.isfinite(res) & (res > 0)
    res, fsc = res[keep], fsc[keep]
    order = np.argsort(1.0 / res)          # ascending spatial frequency
    return res[order], fsc[order]


def crossing(res, fsc, thresh=THRESH):
    """Interpolated resolution where the corrected FSC first drops below `thresh`."""
    freq = 1.0 / res
    for i in range(1, len(fsc)):
        if fsc[i] < thresh <= fsc[i - 1]:
            f = freq[i - 1] + (thresh - fsc[i - 1]) * (freq[i] - freq[i - 1]) / (fsc[i] - fsc[i - 1])
            return 1.0 / f
    return None


def main():
    curves, tsv_rows, fmax = [], [], 0.0
    for label, path, color in SPECIES:
        res, fsc = load_fsc(path)
        cr = crossing(res, fsc)
        curves.append((label, res, fsc, color, cr))
        fmax = max(fmax, (1.0 / res).max())
        for r, f in zip(res, fsc):
            tsv_rows.append((label, r, f))

    fig, ax = plt.subplots(figsize=(8, 5), dpi=200)
    ax.axhline(THRESH, ls="--", lw=1, color="0.55")
    ax.text(0.002, THRESH + 0.02, "0.143", color="0.4", fontsize=9)

    for label, res, fsc, color, cr in curves:
        freq = 1.0 / res
        ax.plot(freq, fsc, lw=2.3, color=color,
                label=f"{label} — {cr:.2f} Å" if cr else label)
        if cr:
            fc = 1.0 / cr
            ax.plot([fc], [THRESH], "o", color=color, ms=6, zorder=5)
            ax.vlines(fc, -0.055, THRESH, color=color, ls=":", lw=1)
            ax.annotate(f"{cr:.2f} Å", (fc, -0.055), textcoords="offset points",
                        xytext=(0, -3), ha="center", va="top", color=color, fontsize=9,
                        fontweight="bold", zorder=6,
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))

    # resolution-labelled x ticks
    res_ticks = [r for r in (100, 50, 30, 20, 15, 12, 10, 8, 7) if (1.0 / r) <= fmax * 1.02]
    ax.set_xticks([1.0 / r for r in res_ticks])
    ax.set_xticklabels([f"{r:g}" for r in res_ticks])
    ax.set_xlim(0, fmax * 1.02)
    ax.set_ylim(-0.16, 1.02)
    ax.set_xlabel("Resolution (Å)")
    ax.set_ylabel("Fourier Shell Correlation (corrected)")
    ax.set_title("Joint M-refined resolution — gold-standard FSC\n(phase-randomization corrected)", fontsize=11)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper right", frameon=False)
    fig.tight_layout()
    fig.savefig("m_refined_fsc.png", bbox_inches="tight")
    print("wrote m_refined_fsc.png")

    with open("m_refined_fsc.tsv", "w") as fh:
        fh.write("species\tresolution_A\tfsc_corrected\n")
        for label, r, f in tsv_rows:
            fh.write(f"{label}\t{r:.4f}\t{f:.6f}\n")
    print("wrote m_refined_fsc.tsv")
    for label, _, _, _, cr in curves:
        print(f"  {label}: FSC=0.143 at {cr:.2f} Å")


if __name__ == "__main__":
    sys.exit(main())
