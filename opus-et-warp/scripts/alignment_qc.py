#!/usr/bin/env python3
"""Image-based QC of an AreTomo tilt-series alignment (Phase 3c).

AreTomo2 reports no residual for a global alignment (`NumPatches = 0`), and
its shift track alone cannot tell a real stage jump from a lost track: on the
20260907 data tilts jumped by up to 860 px and were aligned correctly. This
check applies the alignment AreTomo wrote (`TS.xf`, IMOD convention, the file
WARP imports in Phase 4) to the exported stack and measures, as IMOD's
tiltxcorr does, how far each tilt still sits from its angle neighbour after
cosine-stretching the lower tilt.

A rigid specimen leaves two harmless patterns in those residuals: a height
offset of the specimen from the tomogram's central plane (z_c sin(dt) per
pair) and a tilt-axis/scale term. Per-tilt errors are solved from all pairs,
the rigid part (a cos t + b sin t) is removed, and what remains is judged:
- `local error`: a tilt displaced from the smooth track of its neighbours,
  the signature of a failed or lost alignment for that tilt;
- `drift`: the smooth remainder across the series (reported, warned on);
- low neighbour correlation: an empty, dark or contaminated tilt whose
  measurement is unreliable (warned on, not judged).

Units: pixels of the working grid (the stack block-averaged to about 9.5 A)
are converted with the stack pixel size from the configuration
(ALIGN_ANGPIX); the stack header is only cross-checked, since WARP headers
have been wrong on this project.

Exit status: 0 pass, 1 fail, 2 could not run.
"""
import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy import ndimage

TARGET_PX = 9.5             # A, working pixel of the check
BAND = (400.0, 40.0)        # A, band-pass for the correlation
CROP = 0.15                 # ignore this fraction of each border
LOCAL_FAIL_A = 30.0         # a tilt this far off its neighbours' track fails
LOCAL_WARN_A = 15.0
DRIFT_WARN_A = 25.0         # smooth remainder, largest |value|
LOW_CC_FRACTION = 0.5       # neighbour CC below this x nearby tilts': empty/dark tilt
MIN_SIGNIFICANCE = 12.0     # correlation peak height over the map's noise, robust SDs.
                            # Synthetic pairs below ~9 gave random shifts, above ~13
                            # were right to ~1 px; 20260907 pairs scored 28-280.


# --- I/O ------------------------------------------------------------------

def read_xf(path):
    rows = [line.split() for line in Path(path).read_text().splitlines() if line.strip()]
    out = np.array([[float(v) for v in r] for r in rows])
    if out.ndim != 2 or out.shape[1] != 6:
        raise ValueError('{}: expected 6 columns per line (IMOD xf)'.format(path))
    return out


def read_tilts(path):
    return np.array([float(line.split()[0]) for line in Path(path).read_text().splitlines()
                     if line.strip()])


def read_aln(path):
    """AreTomo .aln -> {'patches': int|None, 'axis': float|None, 'shifts': [(tilt, tx, ty)]}."""
    out = {'patches': None, 'axis': None, 'shifts': []}
    for line in Path(path).read_text().splitlines():
        s = line.strip()
        if s.startswith('# NumPatches'):
            try:
                out['patches'] = int(s.split('=')[1])
            except (IndexError, ValueError):
                pass
        elif s and not s.startswith('#'):
            p = s.split()
            if len(p) >= 10:
                try:
                    out['axis'] = float(p[1])
                    out['shifts'].append((float(p[9]), float(p[3]), float(p[4])))
                except ValueError:
                    pass
    return out


def block_mean(img, b):
    ny, nx = img.shape[0] // b, img.shape[1] // b
    return img[:ny * b, :nx * b].reshape(ny, b, nx, b).mean(axis=(1, 3))


def read_stack(path, b):
    """Block-averaged sections and the header voxel size (A)."""
    import mrcfile
    with mrcfile.mmap(path, mode='r', permissive=True) as m:
        if m.data is None or m.data.ndim not in (2, 3):
            raise ValueError('{}: expected a readable image stack'.format(path))
        header = float(m.voxel_size.x)
        data = m.data if m.data.ndim == 3 else m.data[None]      # a single tilt
        return [block_mean(np.asarray(data[k], np.float32), b)
                for k in range(data.shape[0])], header


# --- geometry ---------------------------------------------------------------

def _centre(shape):
    return np.array([(shape[0] - 1) / 2.0, (shape[1] - 1) / 2.0])


def apply_xf(img, row, b=1, order=1):
    """Aligned image from a raw section and its IMOD xf line
    (A11 A12 A21 A22 DX DY): raw x maps to A x + d about the centre, as
    newstack does. `b` is the block size applied to `img` (shifts are in
    stack pixels)."""
    a = np.asarray(row[:4], float).reshape(2, 2)
    d = np.asarray(row[4:], float) / b
    arc = np.array([[a[1, 1], a[1, 0]], [a[0, 1], a[0, 0]]])     # (y, x) order
    m = np.linalg.inv(arc)
    c = _centre(img.shape)
    off = c - m @ (c + d[::-1])
    return ndimage.affine_transform(img, m, offset=off, order=order, cval=0.0)


def stretch_x(img, s, order=1):
    """Scale by `s` perpendicular to the (vertical) tilt axis, about the centre."""
    m = np.diag([1.0, 1.0 / s])
    c = _centre(img.shape)
    return ndimage.affine_transform(img, m, offset=c - m @ c, order=order, cval=0.0)


def bandpass(img, px, low=BAND[0], high=BAND[1]):
    fy = np.fft.fftfreq(img.shape[0])[:, None] / px
    fx = np.fft.rfftfreq(img.shape[1])[None, :] / px
    f = np.hypot(fx, fy)
    filt = (1 - np.exp(-(f * low) ** 2)) * np.exp(-(f * high) ** 2)
    return np.fft.irfft2(np.fft.rfft2(img) * filt, s=img.shape)


def _parabola(m, c, p):
    den = m - 2 * c + p
    return 0.5 * (m - p) / den if den else 0.0


def peak_significance(cc, py, px, guard=6):
    """How far the peak stands above the correlation map's noise floor, in
    robust standard deviations (the map away from the peak)."""
    mask = np.ones(cc.shape, bool)
    mask[max(0, py - guard):py + guard + 1, max(0, px - guard):px + guard + 1] = False
    rest = cc[mask]
    med = float(np.median(rest))
    mad = 1.4826 * float(np.median(np.abs(rest - med)))
    return (float(cc[py, px]) - med) / mad if mad > 0 else float('inf')


def pair_shift(a, b, maxshift):
    """(dy, dx, cc, at_edge, significance): `b` equals `a` moved by (dy, dx)."""
    cc = np.fft.irfft2(np.conj(np.fft.rfft2(a)) * np.fft.rfft2(b), s=a.shape)
    cc = np.fft.fftshift(cc) / (math.sqrt(float((a * a).sum() * (b * b).sum())) + 1e-12)
    cy, cx = a.shape[0] // 2, a.shape[1] // 2
    sub = cc[cy - maxshift:cy + maxshift + 1, cx - maxshift:cx + maxshift + 1]
    iy, ix = np.unravel_index(int(np.argmax(sub)), sub.shape)
    at_edge = iy in (0, sub.shape[0] - 1) or ix in (0, sub.shape[1] - 1)
    py, px = iy + cy - maxshift, ix + cx - maxshift
    dy = py - cy + _parabola(cc[py - 1, px], cc[py, px], cc[py + 1, px])
    dx = px - cx + _parabola(cc[py, px - 1], cc[py, px], cc[py, px + 1])
    return float(dy), float(dx), float(cc[py, px]), bool(at_edge), peak_significance(cc, py, px)


def prepare(img, row, b, px):
    """Aligned, band-passed section and its valid-data mask."""
    valid = apply_xf(np.ones_like(img), row, b, order=1) > 0.999
    out = apply_xf(img, row, b)
    out = np.where(valid, out - out[valid].mean(), 0.0)
    return bandpass(out, px), valid


def measure_pairs(prepared, tilts, keep, px):
    """Residual shift of each angle-neighbour pair among the `keep` sections."""
    shape = prepared[0][0].shape
    y0, x0 = int(shape[0] * CROP), int(shape[1] * CROP)
    win = np.outer(np.hanning(shape[0] - 2 * y0), np.hanning(shape[1] - 2 * x0))
    # The whole map: a tilt the alignment lost by a few hundred A must show up
    # as a clear, displaced peak (and fail), not as "no peak" (only a warning).
    maxshift = min(shape[0] - 2 * y0, shape[1] - 2 * x0) // 2 - 2
    order = sorted(keep, key=lambda k: (tilts[k], k))
    pairs = []
    for i, j in zip(order[:-1], order[1:]):
        lo, hi = (i, j) if abs(tilts[i]) <= abs(tilts[j]) else (j, i)
        s = math.cos(math.radians(tilts[hi])) / math.cos(math.radians(tilts[lo]))
        img_lo, ok_lo = prepared[lo]
        img_hi, ok_hi = prepared[hi]
        a = stretch_x(img_lo, s)
        ok = (stretch_x(ok_lo.astype(np.float32), s) > 0.999) & ok_hi
        ok = ndimage.binary_erosion(ok, iterations=4)
        w = (ok[y0:shape[0] - y0, x0:shape[1] - x0] * win).astype(np.float64)
        if w.sum() < 0.05 * win.sum():
            pairs.append({'lo': int(lo), 'hi': int(hi), 'dx': None, 'dy': None, 'cc': 0.0,
                          'at_edge': False, 'significance': 0.0,
                          'overlap': float(w.sum() / win.sum())})
            continue
        dy, dx, cc, edge, sig = pair_shift(a[y0:shape[0] - y0, x0:shape[1] - x0] * w,
                                      img_hi[y0:shape[0] - y0, x0:shape[1] - x0] * w, maxshift)
        pairs.append({'lo': int(lo), 'hi': int(hi), 'dx': dx, 'dy': dy, 'cc': cc,
                      'at_edge': edge, 'significance': round(sig, 1),
                      'overlap': float(w.sum() / win.sum())})
    return pairs, maxshift


def solve_errors(pairs, tilts):
    """Per-tilt position errors (working px) from pair residuals, rigid part removed.

    x: pair residual = e_hi - s e_lo + z_c sin(t_hi - t_lo) / cos(t_lo); any
    e = a cos t + b sin t is indistinguishable from a rigid specimen (b is the
    specimen height z_c), so that part is fitted and removed.
    y (along the tilt axis): pair residual = f_hi - f_lo; the mean is removed.
    """
    n = len(tilts)
    used = [p for p in pairs if p['dx'] is not None]
    t = np.radians(np.asarray(tilts, float))
    ax, ay = np.zeros((len(used), n)), np.zeros((len(used), n))
    dx, dy = np.zeros(len(used)), np.zeros(len(used))
    for k, p in enumerate(used):
        s = math.cos(t[p['hi']]) / math.cos(t[p['lo']])
        ax[k, p['hi']], ax[k, p['lo']], dx[k] = 1.0, -s, p['dx']
        ay[k, p['hi']], ay[k, p['lo']], dy[k] = 1.0, -1.0, p['dy']
    ex = np.linalg.lstsq(ax, dx, rcond=None)[0] if used else np.zeros(n)
    ey = np.linalg.lstsq(ay, dy, rcond=None)[0] if used else np.zeros(n)
    g = np.stack([np.cos(t), np.sin(t)], axis=1)
    coef = np.linalg.lstsq(g, ex, rcond=None)[0]
    return ex - g @ coef, ey - ey.mean(), float(coef[1])


def local_errors(values, tilts, reach=3):
    """Departure of each tilt from the line through its neighbours.

    The line is a repeated-median fit through the `2*reach` nearest tilts in
    angle order (excluding the tilt itself; one-sided at the ends), so one or
    two bad neighbours do not drag it: a tilt off by E scores E, its
    neighbours about 0, and a smooth track 0 everywhere."""
    order = list(np.argsort(tilts, kind='stable'))
    t = np.asarray(tilts, float)
    v = np.asarray(values, float)
    out = np.zeros(len(v))
    if len(v) < 3:
        return out
    n = len(order)
    width = min(2 * reach, n - 1)
    for pos, k in enumerate(order):
        start = min(max(0, pos - reach), n - 1 - width)
        near = [order[p] for p in range(start, start + width + 1) if p != pos][:width]
        # Siegel repeated median: robust to up to half of the neighbours
        inner = [np.median([(v[j] - v[i]) / (t[j] - t[i]) for j in near if t[j] != t[i]])
                 for i in near if any(t[j] != t[i] for j in near)]
        slope = float(np.median(inner)) if inner else 0.0
        intercept = float(np.median([v[i] - slope * t[i] for i in near]))
        out[k] = v[k] - (intercept + slope * t[k])
    return out


# --- verdict ------------------------------------------------------------------

def _nearby_median(values, tilts, k, reach=3):
    """Median of `values` over the 2*reach tilts nearest to k in angle order."""
    order = list(np.argsort(tilts, kind='stable'))
    pos, n = order.index(k), len(order)
    width = min(2 * reach, n - 1)
    start = min(max(0, pos - reach), n - 1 - width)
    return float(np.median([values[order[p]] for p in range(start, start + width + 1) if p != pos]))


def analyse(sections, xf, tilts, stack_angpix, block=1, header_angpix=None, aln=None):
    """`sections` are the stack block-averaged by `block`; `stack_angpix` is
    the stack's own pixel size."""
    n = len(sections)
    if not (len(xf) == n == len(tilts)):
        raise ValueError('stack has {} sections, xf {} lines, tilt file {} angles'.format(
            n, len(xf), len(tilts)))
    if n < 3:
        raise ValueError('{} tilt(s): too few to check an alignment'.format(n))
    b = int(block)
    px = stack_angpix * b
    prepared = [prepare(sections[k], xf[k], b, px) for k in range(n)]
    pairs, maxshift = measure_pairs(prepared, tilts, range(n), px)
    ccs = [p['cc'] for p in pairs if p['dx'] is not None]
    med_cc = float(np.median(ccs)) if ccs else 0.0
    best_cc, best_sig = [0.0] * n, [0.0] * n
    for p in pairs:
        for k in (p['lo'], p['hi']):
            best_cc[k] = max(best_cc[k], p['cc'])
            best_sig[k] = max(best_sig[k], p['significance'])
    # A tilt that matches neither neighbour (empty, dark, contaminated) gives
    # random shifts that would break the chain for everything beyond it:
    # set it aside and compare its neighbours directly.
    # Judged against nearby tilts, not the series median: correlation falls
    # steadily with tilt, and a faint high tilt is not an empty one.
    low_set = {k for k in range(n)
               if best_cc[k] < LOW_CC_FRACTION * _nearby_median(best_cc, tilts, k)
               or best_sig[k] < MIN_SIGNIFICANCE}
    keep = [k for k in range(n) if k not in low_set]
    if low_set and len(keep) >= 3:
        pairs, maxshift = measure_pairs(prepared, tilts, keep, px)
    else:
        keep = list(range(n))
    sub = np.asarray(tilts, float)[keep]
    remap = {k: i for i, k in enumerate(keep)}
    local_pairs = [dict(p, lo=remap[p['lo']], hi=remap[p['hi']]) for p in pairs]
    rx_k, ry_k, zc = solve_errors([p for p in local_pairs
                                   if p['dx'] is None or p['significance'] >= MIN_SIGNIFICANCE], sub)
    lx_k, ly_k = local_errors(rx_k, sub), local_errors(ry_k, sub)
    rx, ry, lx, ly = (np.full(n, np.nan) for _ in range(4))
    rx[keep], ry[keep], lx[keep], ly[keep] = rx_k, ry_k, lx_k, ly_k

    problems, warnings = [], []
    rows = []
    for k in np.argsort(tilts, kind='stable'):
        low = k not in remap
        local = 0.0 if low else math.hypot(lx[k], ly[k]) * px
        rows.append({'section': int(k), 'tilt': float(tilts[k]),
                     'local_error_A': None if low else round(local, 1),
                     'x_A': None if low else round(float(rx[k]) * px, 1),
                     'y_A': None if low else round(float(ry[k]) * px, 1),
                     'cc': round(best_cc[k], 3), 'low_signal': bool(low)})
        label = '{:+.1f}'.format(float(tilts[k]))
        if low:
            warnings.append('tilt {}: no reliable match with its neighbours (correlation {:.2f}, '
                            'nearby tilts {:.2f}; peak {:.0f} SD over noise): empty, dark or '
                            'contaminated, its error is not judged'.format(
                                label, best_cc[k], _nearby_median(best_cc, tilts, k), best_sig[k]))
        elif local > LOCAL_FAIL_A:
            problems.append('tilt {}: {:.0f} A off its neighbours (limit {:.0f} A)'.format(
                label, local, LOCAL_FAIL_A))
        elif local > LOCAL_WARN_A:
            warnings.append('tilt {}: {:.0f} A off its neighbours'.format(label, local))
    for p in pairs:
        if p['dx'] is not None and p['significance'] < MIN_SIGNIFICANCE:
            warnings.append('tilts {:+.1f}/{:+.1f}: weak correlation peak ({:.0f} SD); '
                            'shift unreliable'.format(tilts[p['lo']], tilts[p['hi']],
                                                      p['significance']))
        elif p['at_edge']:
            problems.append('tilts {:+.1f}/{:+.1f}: no correlation peak within +-{:.0f} A '
                            '(alignment lost)'.format(tilts[p['lo']], tilts[p['hi']], maxshift * px))
        if p['dx'] is None:
            warnings.append('tilts {:+.1f}/{:+.1f}: too little overlap to compare'.format(
                tilts[p['lo']], tilts[p['hi']]))
    drift = float(np.nanmax(np.abs(np.concatenate([rx, ry])))) * px
    if drift > DRIFT_WARN_A:
        warnings.append('smooth drift up to {:.0f} A across the series (rigid specimen '
                        'removed); consider local/patch alignment'.format(drift))
    if header_angpix and not math.isclose(header_angpix, stack_angpix, rel_tol=0.01):
        warnings.append('stack header says {:.3f} A/px, configuration {:.3f} A/px; '
                        'distances use the configuration'.format(header_angpix, stack_angpix))
    if aln and aln.get('patches') == 0:
        warnings.append('global alignment only (NumPatches = 0)')
    judged = [r['local_error_A'] for r in rows if not r['low_signal']]
    return {
        'pass': not problems,
        'problems': problems,
        'warnings': warnings,
        'n_tilts': n,
        'stack_angpix': stack_angpix,
        'header_angpix': header_angpix,
        'working_pixel_A': round(px, 3),
        'specimen_height_A': round(zc * px, 1),
        'max_local_error_A': round(max(judged), 1) if judged else None,
        'median_local_error_A': round(float(np.median(judged)), 1) if judged else None,
        'max_drift_A': round(drift, 1),
        'local_fail_A': LOCAL_FAIL_A,
        'local_warn_A': LOCAL_WARN_A,
        'median_cc': round(med_cc, 3),
        'tilt_axis': aln.get('axis') if aln else None,
        'tilts': rows,
        'pairs': [dict(p, tilt_lo=float(tilts[p['lo']]), tilt_hi=float(tilts[p['hi']])) for p in pairs],
    }


def plot(result, path, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = result['tilts']
    t = [r['tilt'] for r in rows]
    fig, ax = plt.subplots(3, 1, figsize=(7, 7.5), sharex=True)
    judged = [r for r in rows if not r['low_signal']]
    colors = ['#d62728' if r['local_error_A'] > LOCAL_FAIL_A else '#ff7f0e'
              if r['local_error_A'] > LOCAL_WARN_A else '#1f77b4' for r in judged]
    ax[0].bar([r['tilt'] for r in judged], [r['local_error_A'] for r in judged], width=2.0,
              color=colors)
    for r in rows:
        if r['low_signal']:
            ax[0].axvline(r['tilt'], color='#bbbbbb', lw=4, alpha=0.5)
    ax[0].axhline(LOCAL_FAIL_A, color='#d62728', lw=0.8, ls='--')
    ax[0].axhline(LOCAL_WARN_A, color='#ff7f0e', lw=0.8, ls=':')
    ax[0].set_ylabel('local error (A)')
    ax[0].set_title('{} - {}'.format(title, 'PASS' if result['pass'] else 'FAIL'))
    tj = [r['tilt'] for r in judged]
    ax[1].plot(tj, [r['x_A'] for r in judged], 'o-', ms=3, label='across axis (rigid part removed)')
    ax[1].plot(tj, [r['y_A'] for r in judged], 's-', ms=3, label='along axis')
    ax[1].axhline(0, color='k', lw=0.5)
    ax[1].set_ylabel('residual (A)')
    ax[1].legend(fontsize=8)
    ax[2].plot(t, [r['cc'] for r in rows], 'o-', ms=3, color='#2ca02c')
    ax[2].axhline(LOW_CC_FRACTION * result['median_cc'], color='#888888', lw=0.8, ls='--')
    ax[2].set_ylabel('neighbour CC')
    ax[2].set_xlabel('tilt angle (deg)')
    fig.text(0.01, 0.005, 'specimen height {:+.0f} A; working pixel {:.2f} A'.format(
        result['specimen_height_A'], result['working_pixel_A']), fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or '').split('\n')[0])
    ap.add_argument('--stack', required=True, help='exported tilt stack TS.st')
    ap.add_argument('--xf', required=True, help='AreTomo alignment TS.xf (IMOD convention)')
    ap.add_argument('--tilts', required=True, help='tilt angles in stack order (TS.rawtlt)')
    ap.add_argument('--angpix', type=float, required=True, help='stack pixel size, ALIGN_ANGPIX')
    ap.add_argument('--aln', help='AreTomo TS.st.aln (optional, for axis/patch info)')
    ap.add_argument('--out-prefix', required=True)
    args = ap.parse_args(argv)
    name = Path(args.out_prefix).name
    try:
        b = max(1, int(round(TARGET_PX / args.angpix)))
        sections, header = read_stack(args.stack, b)
        result = analyse(sections, read_xf(args.xf), read_tilts(args.tilts), args.angpix, b,
                         header, read_aln(args.aln) if args.aln else None)
    except (OSError, ValueError) as exc:
        print('{}: could not run: {}'.format(name, exc))
        return 2
    result.update(tilt_series=name, stack=str(args.stack), xf=str(args.xf))
    Path(args.out_prefix + '_alignment.json').write_text(json.dumps(result, indent=1))
    try:
        plot(result, args.out_prefix + '_alignment.png', name)
    except ImportError:
        print('{}: matplotlib missing, no PNG'.format(name))
    print('{}: {}  max local {} A, drift {} A, height {:+.0f} A, median CC {}'.format(
        name, 'pass' if result['pass'] else 'FAIL', result['max_local_error_A'],
        result['max_drift_A'], result['specimen_height_A'], result['median_cc']))
    for p in result['problems']:
        print('  FAIL: ' + p)
    for w in result['warnings']:
        print('  warn: ' + w)
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
