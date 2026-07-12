#!/usr/bin/env python3
"""Masked gold-standard FSC between two independent half-maps (Gate-4 QC).

A fixed-mode run produces two independent half-maps; the Fourier Shell
Correlation (FSC) between them is the standard resolution estimate. A soft
*sphere* mask, however, induces a spurious high-frequency FSC rise (the sphere
itself is band-limited and correlates with itself at high frequency), which
inflates the apparent first-crossing resolution. Masking with a mask that
follows the actual density (see gen_mask_from_map.molecule_mask) removes most
of that artifact. For the residual, `fsc_corrected` applies the RELION
high-resolution noise-substitution correction: phases beyond a cutoff are
randomized, the same mask re-applied, and the mask-only correlation subtracted
off — this is on by default in the CLI (`--no-phase-randomize` to skip). The
corrected 0.143 resolution is the honest number; the high-frequency detail is
not needed here anyway, since only a low-resolution map is imported into M,
which refines from there.

The numerical core (fsc_curve, resolution_at, phase_randomize, fsc_corrected)
is numpy-only and unit-tested;
mrcfile and matplotlib stay lazy inside the I/O / rendering helpers. If no
mask is supplied on the CLI, one is derived on the fly from the half-average
via gen_mask_from_map.molecule_mask (imported, not reimplemented).

Convention: `resolution_at` reports the resolution at the FIRST frequency the
curve drops below the given threshold (linear-interpolated), *not* the last.
A curve that dips below threshold and then rises again at higher frequency
(mask artifact, or noise past Nyquist) must not be reported as higher
resolution than the first crossing.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_to_template as ct
import gen_mask_from_map as gm


# ----------------------------------------------------------------------------
# Numerical core (numpy only)
# ----------------------------------------------------------------------------
def fsc_curve(vol_a, vol_b, mask=None):
    """Fourier Shell Correlation between two same-shape cubic volumes.

    Returns (freq_cyc_per_px, fsc): 1-D ndarrays indexed by integer radial
    shell (0 = DC ... N//2 = Nyquist), freq in cycles/pixel. Shells with no
    voxels (shouldn't happen for a cubic volume, but guarded) are NaN.
    """
    a = np.asarray(vol_a, np.float64)
    b = np.asarray(vol_b, np.float64)
    if mask is not None:
        m = np.asarray(mask, np.float64)
        a = a * m
        b = b * m

    N = a.shape[0]
    Fa = np.fft.fftshift(np.fft.fftn(a))
    Fb = np.fft.fftshift(np.fft.fftn(b))

    c = N // 2
    zz, yy, xx = np.indices(a.shape)
    r = np.sqrt((zz - c) ** 2 + (yy - c) ** 2 + (xx - c) ** 2)
    r_int = np.round(r).astype(int)
    max_r = N // 2

    freqs = np.arange(0, max_r + 1) / float(N)
    fsc = np.full(max_r + 1, np.nan, np.float64)

    for i in range(max_r + 1):
        shell = r_int == i
        if not shell.any():
            continue
        fa = Fa[shell]
        fb = Fb[shell]
        num = np.sum(fa * np.conj(fb)).real
        denom = np.sqrt(np.sum(np.abs(fa) ** 2) * np.sum(np.abs(fb) ** 2))
        fsc[i] = num / denom if denom > 0 else np.nan

    return freqs, fsc


def resolution_at(freq_cyc_per_px, fsc, threshold, apix):
    """Resolution (Angstrom) at the FIRST frequency `fsc` drops below
    `threshold`, linear-interpolated between the bracketing shells.

    Angstrom = apix / freq. If the curve never drops below threshold,
    returns the resolution at the highest valid frequency (best achievable
    given the data) rather than raising.
    """
    freq = np.asarray(freq_cyc_per_px, np.float64)
    fsc = np.asarray(fsc, np.float64)
    valid = ~np.isnan(fsc)
    freq = freq[valid]
    fsc = fsc[valid]

    if freq.size == 0:
        return float("inf")

    below = fsc < threshold
    if not below.any():
        f_last = freq[-1]
        return apix / f_last if f_last > 0 else float("inf")

    idx = int(np.argmax(below))
    if idx == 0:
        # already below threshold at the first (lowest) frequency sampled
        f0 = freq[0]
        return apix / f0 if f0 > 0 else float("inf")

    f0, f1 = freq[idx - 1], freq[idx]
    v0, v1 = fsc[idx - 1], fsc[idx]
    if v1 == v0:
        f_cross = f0
    else:
        t = (threshold - v0) / (v1 - v0)
        f_cross = f0 + t * (f1 - f0)

    return apix / f_cross if f_cross > 0 else float("inf")


# ----------------------------------------------------------------------------
# Phase-randomization mask correction (RELION high-res noise substitution)
# ----------------------------------------------------------------------------
def _negate_index(arr):
    """`arr` reindexed at -k, i.e. FFT index i -> (N-i) % N along every axis.
    For a Hermitian-symmetric array this maps each frequency to its conjugate
    partner."""
    out = arr
    for ax in range(arr.ndim):
        out = np.roll(np.flip(out, axis=ax), 1, axis=ax)
    return out


def phase_randomize(vol, rand_freq, seed=0):
    """Return a copy of `vol` with Fourier phases beyond `rand_freq`
    (cycles/pixel) replaced by random phases; amplitudes preserved exactly.

    The random phases are made antisymmetric under k -> -k, so the modified
    spectrum stays Hermitian and the inverse transform is real with the same
    amplitude spectrum as the input (exact power preservation, unlike a naive
    rfftn phase scramble which re-symmetrizes the Nyquist/DC planes and leaks
    power). Shells at or below `rand_freq` keep their original coefficients, so
    the low-resolution signal is untouched; beyond it only the phases scramble.
    """
    v = np.asarray(vol, np.float64)
    N = v.shape[0]
    F = np.fft.fftn(v)
    fz = np.fft.fftfreq(N)[:, None, None]
    fy = np.fft.fftfreq(N)[None, :, None]
    fx = np.fft.fftfreq(N)[None, None, :]
    fr = np.sqrt(fz ** 2 + fy ** 2 + fx ** 2)
    beyond = fr > rand_freq  # symmetric under k -> -k, since fr is

    rng = np.random.RandomState(seed)
    phi = rng.uniform(-np.pi, np.pi, size=F.shape)
    phi_sym = 0.5 * (phi - _negate_index(phi))  # antisymmetric: phi(-k) = -phi(k)

    F_rand = np.where(beyond, np.abs(F) * np.exp(1j * phi_sym), F)
    return np.fft.ifftn(F_rand).real


def _first_shell_below(fsc, threshold):
    """Index of the first shell (skipping DC) where `fsc` drops below
    `threshold`, ignoring NaN shells. Returns None if it never does."""
    for i in range(1, len(fsc)):
        if not np.isnan(fsc[i]) and fsc[i] < threshold:
            return i
    return None


def fsc_corrected(vol_a, vol_b, mask, apix, rand_res=None, rand_thresh=0.8,
                  seed=0):
    """Phase-randomized mask correction (RELION high-resolution noise
    substitution).

    A mask multiplies both half-maps by the same envelope, which correlates
    them at high frequency and inflates the masked FSC. To measure that
    artifact, we randomize the phases of both halves beyond a cutoff frequency,
    re-apply the SAME mask, and take their FSC (`fsc_random`) — this is pure
    mask-induced correlation, since the true high-frequency signal has been
    destroyed. The corrected curve removes it:

        corrected = (fsc_masked - fsc_random) / (1 - fsc_random)   (beyond cutoff)
        corrected =  fsc_masked                                    (at/below cutoff)

    The cutoff `rand_freq` (cycles/pixel) is either derived from `rand_res`
    (Angstrom) or auto-picked as the first shell where the masked FSC drops
    below `rand_thresh` (0.8) — a frequency where there is still real signal,
    so randomizing beyond it does not touch the resolvable core.

    Returns a dict: freq, fsc_masked, fsc_random, fsc_corrected, rand_freq.
    """
    freq, fsc_masked = fsc_curve(vol_a, vol_b, mask=mask)

    if rand_res is not None and rand_res > 0:
        rand_freq = apix / float(rand_res)
    else:
        idx = _first_shell_below(fsc_masked, rand_thresh)
        # fall back to a low-resolution cutoff (¼ Nyquist) if the masked FSC
        # never drops below the threshold (e.g. identical inputs)
        rand_freq = float(freq[idx]) if idx is not None else float(freq[-1]) / 4.0

    ra = phase_randomize(vol_a, rand_freq, seed=seed)
    rb = phase_randomize(vol_b, rand_freq, seed=seed + 1)
    _, fsc_random = fsc_curve(ra, rb, mask=mask)

    fsc_corr = fsc_masked.copy()
    beyond = freq > rand_freq
    denom = 1.0 - fsc_random
    with np.errstate(invalid="ignore", divide="ignore"):
        corrected = (fsc_masked - fsc_random) / denom
    safe = beyond & ~np.isnan(fsc_random) & (np.abs(denom) > 1e-3)
    fsc_corr[safe] = corrected[safe]
    fsc_corr = np.clip(fsc_corr, -1.0, 1.0)

    return {
        "freq": freq,
        "fsc_masked": fsc_masked,
        "fsc_random": fsc_random,
        "fsc_corrected": fsc_corr,
        "rand_freq": rand_freq,
    }


# ----------------------------------------------------------------------------
# I/O + rendering (lazy heavy imports)
# ----------------------------------------------------------------------------
def render_fsc(freq, fsc, apix, res143, res5, out_png, title="",
               fsc_random=None, fsc_corrected=None, rand_freq=None):
    """FSC-vs-spatial-frequency plot with 0.143/0.5 threshold lines.

    If `fsc_corrected` is supplied, the plot overlays the phase-randomized
    correction: the raw masked FSC (light), the phase-randomized FSC (dotted,
    the mask-only correlation), the corrected FSC (bold), and a vertical marker
    at the randomization cutoff. `res143`/`res5` are then the corrected-curve
    resolutions.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = freq / float(apix)  # cycles / Angstrom
    fig, ax = plt.subplots(figsize=(6.5, 4.5))

    if fsc_corrected is not None:
        ax.plot(x, fsc, "-o", ms=2, color="0.6", lw=1, label="FSC (masked)")
        if fsc_random is not None:
            ax.plot(x, fsc_random, ":", color="darkorange", lw=1,
                    label="FSC (phase-rand.)")
        ax.plot(x, fsc_corrected, "-o", ms=3, color="black",
                label="FSC (corrected)")
        if rand_freq is not None:
            ax.axvline(rand_freq / float(apix), color="green", ls=":", lw=1,
                       label="randomize beyond")
    else:
        ax.plot(x, fsc, "-o", ms=3, color="black", label="FSC")

    ax.axhline(0.143, color="crimson", ls="--", lw=1, label="0.143")
    ax.axhline(0.5, color="steelblue", ls="--", lw=1, label="0.5")
    ax.set_xlabel("spatial frequency (1/Å)")
    ax.set_ylabel("FSC")
    ax.set_ylim(-0.05, 1.05)
    ax.set_xlim(left=0)

    label = "corrected FSC" if fsc_corrected is not None else "FSC"
    default_title = (f"{label}   0.143 -> {res143:.2f} Å   |   0.5 -> {res5:.2f} Å\n"
                     "(a rise after the first sub-threshold crossing is a mask artifact, "
                     "not real signal)")
    ax.set_title(title or default_title, fontsize=10)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def _fmt(v):
    return "" if v is None or np.isnan(v) else f"{v:.4f}"


def _write_tsv(path, freq, fsc, apix, fsc_random=None, fsc_corrected=None):
    with open(path, "w") as f:
        cols = ["freq_cyc_per_px", "resolution_A", "fsc"]
        if fsc_corrected is not None:
            cols += ["fsc_random", "fsc_corrected"]
        f.write("\t".join(cols) + "\n")
        for i, (fr, fs) in enumerate(zip(freq, fsc)):
            res = apix / fr if fr > 0 else float("inf")
            row = [f"{fr:.6f}", f"{res:.3f}", _fmt(fs)]
            if fsc_corrected is not None:
                fr_rand = fsc_random[i] if fsc_random is not None else None
                row += [_fmt(fr_rand), _fmt(fsc_corrected[i])]
            f.write("\t".join(row) + "\n")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--half1", required=True, help="independent half-map 1 (MRC)")
    ap.add_argument("--half2", required=True, help="independent half-map 2 (MRC)")
    ap.add_argument("--apix", type=float, required=True, help="pixel size (Angstrom)")
    ap.add_argument("--mask", default="",
                    help="mask MRC (default: derive one from the half-average via "
                         "gen_mask_from_map.molecule_mask)")
    ap.add_argument("-o", "--out-prefix", required=True)
    ap.add_argument("--no-phase-randomize", dest="phase_randomize",
                    action="store_false",
                    help="skip the phase-randomization mask correction "
                         "(report the raw masked FSC only)")
    ap.add_argument("--rand-res", type=float, default=None,
                    help="randomize phases beyond this resolution (Å); default "
                         "auto-picks the first shell where masked FSC < 0.8")
    ap.add_argument("--seed", type=int, default=0,
                    help="RNG seed for phase randomization (reproducibility)")
    args = ap.parse_args()

    v1, _ = ct.load_mrc(args.half1)
    v2, _ = ct.load_mrc(args.half2)
    apix = args.apix

    if args.mask:
        mask, _ = ct.load_mrc(args.mask)
    else:
        avg = (v1.astype(np.float64) + v2.astype(np.float64)) / 2.0
        mask = gm.molecule_mask(avg)

    freq, fsc = fsc_curve(v1, v2, mask=mask)
    res143_m = resolution_at(freq, fsc, 0.143, apix)
    res5_m = resolution_at(freq, fsc, 0.5, apix)

    tsv = args.out_prefix + "_fsc.tsv"
    png = args.out_prefix + "_fsc.png"

    if args.phase_randomize:
        r = fsc_corrected(v1, v2, mask, apix, rand_res=args.rand_res,
                          seed=args.seed)
        fsc_c = r["fsc_corrected"]
        res143 = resolution_at(freq, fsc_c, 0.143, apix)
        res5 = resolution_at(freq, fsc_c, 0.5, apix)
        _write_tsv(tsv, freq, fsc, apix, fsc_random=r["fsc_random"],
                   fsc_corrected=fsc_c)
        render_fsc(freq, fsc, apix, res143, res5, png,
                   fsc_random=r["fsc_random"], fsc_corrected=fsc_c,
                   rand_freq=r["rand_freq"])
        print(f"wrote {tsv}")
        print(f"wrote {png}")
        print(f"phase-randomized beyond {apix / r['rand_freq']:.2f} A "
              f"({r['rand_freq']:.4f} cyc/px)")
        print(f"FSC=0.143 (masked / corrected): {res143_m:.2f} / {res143:.2f} A")
        print(f"FSC=0.5   (masked / corrected): {res5_m:.2f} / {res5:.2f} A")
        print("Report the corrected 0.143 resolution; it strips the mask-induced "
              "high-frequency correlation.")
    else:
        res143, res5 = res143_m, res5_m
        _write_tsv(tsv, freq, fsc, apix)
        render_fsc(freq, fsc, apix, res143, res5, png)
        print(f"wrote {tsv}")
        print(f"wrote {png}")
        print(f"FSC=0.143 resolution (first crossing): {res143:.2f} A")
        print(f"FSC=0.5   resolution (first crossing): {res5:.2f} A")
        print("Note: any FSC rise after the first sub-threshold crossing is a "
              "mask artifact, not real high-resolution signal.")


if __name__ == "__main__":
    main()
