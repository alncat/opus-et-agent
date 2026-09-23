#!/usr/bin/env python3
"""Image-based geometry QC for Gate 1: do the raw 0-degree tilt, the AreTomo
(template-matching) volume and the WARP reconstruction show the same field of
view at the same physical scale?

In 2026-09 every numeric check passed (dimensions, headers, sampling) while
WARP reconstructed a 2x-magnified central half of every tomogram; only a
side-by-side look at the images revealed it. This check compares image
content in physical units and fails on a scale error, poor agreement, or a
Z-mirror between the two volumes.

Scale convention: `scale` is how much the tested image is magnified relative
to the AreTomo volume (2.0 = it shows the central half at twice the size).
Agreement is |r|: a raw image and a reconstruction can legitimately have
opposite contrast (dense material is dark in the raw tilt, bright in these
AreTomo volumes), so the sign is reported as `contrast`, not judged.

Exit status: 0 pass, 1 fail, 2 could not run.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
import warp_settings  # noqa: E402

GRID = 256          # longest side of the common comparison grid, in pixels
MAX_BIN_SIDE = 400  # volumes are block-averaged to about this size first
MARGIN = 0.03       # ignore this fraction of each source border (AreTomo edge rows,
                    # detector borders); on real data they dominated the correlation


# --- I/O ------------------------------------------------------------------

def _voxel(m, path):
    v = [float(m.voxel_size.x), float(m.voxel_size.y), float(m.voxel_size.z)]
    if any(not math.isfinite(x) or x <= 0 for x in v):
        raise ValueError('{}: invalid voxel size {} in header'.format(path, v))
    return v


def block_mean(img, b):
    ny, nx = img.shape[0] // b, img.shape[1] // b
    return img[:ny * b, :nx * b].reshape(ny, b, nx, b).mean(axis=(1, 3))


def read_volume(path):
    """Block-averaged volume (z, y, x) and its binned voxel size in A."""
    import mrcfile
    with mrcfile.mmap(path, mode='r', permissive=True) as m:
        vx, vy, vz = _voxel(m, path)
        if not (math.isclose(vx, vy, rel_tol=1e-3) and math.isclose(vx, vz, rel_tol=1e-3)):
            raise ValueError('{}: anisotropic voxel size {}'.format(path, (vx, vy, vz)))
        data = m.data
        if data is None:
            raise ValueError('{}: unreadable or truncated MRC'.format(path))
        if data.ndim != 3:
            raise ValueError('{}: expected a 3D volume'.format(path))
        b = max(1, int(round(max(data.shape[1:]) / MAX_BIN_SIDE)))
        nz, ny, nx = (s // b for s in data.shape)
        out = np.empty((nz, ny, nx), np.float32)
        for k in range(nz):  # slab by slab: volumes can be several GB
            slab = np.asarray(data[k * b:(k + 1) * b, :ny * b, :nx * b], dtype=np.float32)
            out[k] = slab.reshape(b, ny, b, nx, b).mean(axis=(0, 2, 4))
    return out, vx * b


def read_image(path, pixel):
    """2D image block-averaged to at most ~1000 px, and its binned pixel size.
    WARP averages carry no pixel size, so it comes from the frame settings."""
    import mrcfile
    with mrcfile.mmap(path, mode='r', permissive=True) as m:
        if m.data is None:
            raise ValueError('{}: unreadable or truncated MRC'.format(path))
        data = np.asarray(m.data, dtype=np.float32)
    if data.ndim == 3 and data.shape[0] == 1:
        data = data[0]
    if data.ndim != 2:
        raise ValueError('{}: expected a single 2D image'.format(path))
    b = max(1, int(round(max(data.shape) / 1000)))
    return block_mean(data, b), pixel * b


def tilt0_from_tomostar(tomostar, frame_settings):
    """(average path, working pixel A, tilt angle) of the tilt nearest 0 deg."""
    cols, rows = [], []
    for line in Path(tomostar).read_text().splitlines():
        s = line.strip()
        if s.startswith('_'):
            cols.append(s.split()[0])
        elif s and cols and not s.startswith(('data_', 'loop_', '#')):
            rows.append(s.split())
    name, angle = cols.index('_wrpMovieName'), cols.index('_wrpAngleTilt')
    row = min(rows, key=lambda r: abs(float(r[angle])))
    pixel, binning = warp_settings.sampling(frame_settings)
    folder = warp_settings.processing_folder(frame_settings)
    average = folder / 'average' / (Path(row[name]).stem + '.mrc')
    return average, pixel * 2 ** binning, float(row[angle])


# --- image operations -----------------------------------------------------

def sample(img, pixel, shape, grid_pixel, scale=1.0, angle=0.0, flip=False, margin=0.0):
    """Place `img` (pixel size `pixel`) on a centred grid, magnified by `scale`,
    rotated by `angle` degrees and optionally mirrored in x. Returns the image
    and a mask of grid points that fell inside the source, excluding a border
    of `margin` (fraction of each source dimension)."""
    gy, gx = np.indices(shape, dtype=np.float64)
    u = (gx - (shape[1] - 1) / 2) * grid_pixel / scale
    v = (gy - (shape[0] - 1) / 2) * grid_pixel / scale
    t = math.radians(angle)
    x = math.cos(t) * u + math.sin(t) * v
    y = -math.sin(t) * u + math.cos(t) * v
    if flip:
        x = -x
    sx = x / pixel + (img.shape[1] - 1) / 2
    sy = y / pixel + (img.shape[0] - 1) / 2
    my, mx = margin * (img.shape[0] - 1), margin * (img.shape[1] - 1)
    inside = ((sx >= mx) & (sx <= img.shape[1] - 1 - mx) &
              (sy >= my) & (sy <= img.shape[0] - 1 - my))
    out = ndimage.map_coordinates(img, [sy, sx], order=1, mode='constant', cval=0.0)
    return out.astype(np.float32), inside


def prefilter(img, pixel, grid_pixel):
    """Anti-alias before resampling to the coarser grid."""
    sigma = 0.5 * grid_pixel / pixel
    return ndimage.gaussian_filter(img, sigma) if sigma > 0.5 else img


def bandpass(img, mask, band=(1.0, 12.0)):
    """Band-pass on the grid (Gaussian sigmas in px), normalised inside the mask. Outliers are
    clipped and the outside is filled with the mean first, so neither a few
    extreme pixels nor the mask boundary can dominate the correlation."""
    w = mask.astype(bool)
    if w.sum() < 16:
        return np.zeros(img.shape, np.float32)
    lo, hi = np.percentile(img[w], [0.5, 99.5])
    x = np.where(w, np.clip(img, lo, hi), img[w].mean())
    b = ndimage.gaussian_filter(x, band[0]) - ndimage.gaussian_filter(x, band[1])
    b = b - b[w].mean()
    return np.where(w, b / (b[w].std() + 1e-12), 0.0).astype(np.float32)


def correlate(a, b, mask, max_shift):
    """Pearson r of largest magnitude (either contrast) at the best shift
    within +/- max_shift grid pixels."""
    w = ndimage.gaussian_filter(mask.astype(np.float32), 3.0) * mask
    if w.sum() < 0.1 * mask.size:
        return -1.0, (0, 0)
    a, b = a * w, b * w
    shape = [2 * n for n in a.shape]
    xc = np.fft.irfft2(np.fft.rfft2(a, shape) * np.conj(np.fft.rfft2(b, shape)), shape)
    xc = np.fft.fftshift(xc)
    cy, cx = shape[0] // 2, shape[1] // 2
    win = xc[cy - max_shift:cy + max_shift + 1, cx - max_shift:cx + max_shift + 1]
    iy, ix = np.unravel_index(np.argmax(np.abs(win)), win.shape)
    norm = math.sqrt(float((a * a).sum()) * float((b * b).sum())) + 1e-12
    return float(win[iy, ix]) / norm, (int(iy - max_shift), int(ix - max_shift))


class Matcher:
    """Compare a tested image against the AreTomo projection on one grid."""

    def __init__(self, ref, ref_pixel, test, test_pixel, grid=GRID, max_shift_frac=0.1,
                 band=(1.0, 12.0)):
        self.band = band
        field = max(ref.shape) * ref_pixel
        self.grid_pixel = field / grid
        self.shape = tuple(int(math.ceil(n * ref_pixel / self.grid_pixel)) for n in ref.shape)
        self.ref = prefilter(ref, ref_pixel, self.grid_pixel)
        self.ref_pixel = ref_pixel
        t, self.test_mask = sample(prefilter(test, test_pixel, self.grid_pixel),
                                   test_pixel, self.shape, self.grid_pixel, margin=MARGIN)
        self.test = t
        self.max_shift = max(2, int(max_shift_frac * max(self.shape)))

    def score(self, scale, angle=0.0, flip=False):
        # Magnifying the reference by `scale` reproduces a test image that is
        # itself magnified by `scale`.
        r, rmask = sample(self.ref, self.ref_pixel, self.shape, self.grid_pixel, scale, angle, flip,
                          margin=MARGIN)
        mask = rmask & self.test_mask
        return correlate(bandpass(r, mask, self.band), bandpass(self.test, mask, self.band),
                         mask, self.max_shift)

    def search(self, scales, angles=(0.0,), flips=(False,)):
        best = None
        for s in scales:
            for a in angles:
                for f in flips:
                    r, shift = self.score(s, a, f)
                    if best is None or abs(r) > best['r']:
                        best = dict(r=abs(r), contrast=1 if r >= 0 else -1, scale=float(s),
                                    angle=float(a), flip=bool(f),
                                    shift_A=[round(d * self.grid_pixel, 1) for d in shift])
        return best


def log_range(lo, hi, n):
    return np.exp(np.linspace(math.log(lo), math.log(hi), n))


# --- checks ---------------------------------------------------------------

def compare_volumes(ali, ali_pixel, warp, warp_pixel):
    m = Matcher(ali.sum(0), ali_pixel, warp.sum(0), warp_pixel)
    best = m.search(log_range(0.4, 2.5, 61))
    best = m.search(best['scale'] * log_range(0.94, 1.06, 13))
    unit_r, _ = m.score(1.0)
    return dict(best, r_at_scale_1=abs(unit_r))


def mirror_test(ali, ali_pixel, warp, warp_pixel):
    """3D band-passed r of WARP vs the AreTomo volume and its Z-mirror, on the
    AreTomo grid in physical coordinates (as proj_compare.py did)."""
    zi, yi, xi = np.indices(ali.shape, dtype=np.float64)
    coords = [(g - (n - 1) / 2) * ali_pixel / warp_pixel + (w - 1) / 2
              for g, n, w in zip((zi, yi, xi), ali.shape, warp.shape)]
    inside = np.all([(c >= 0) & (c <= w - 1) for c, w in zip(coords, warp.shape)], axis=0)
    for g, n in zip((zi, yi, xi), ali.shape):  # drop the AreTomo border, as in 2D
        inside &= (g >= MARGIN * (n - 1)) & (g <= (1 - MARGIN) * (n - 1))
    w_on_ali = ndimage.map_coordinates(warp, coords, order=1, mode='constant')

    def bp(v):
        lo, hi = np.percentile(v[inside], [0.5, 99.5])
        v = np.where(inside, np.clip(v, lo, hi), v[inside].mean())
        b = ndimage.gaussian_filter(v, 1.0) - ndimage.gaussian_filter(v, 8.0)
        b = b[inside]
        return (b - b.mean()) / (b.std() + 1e-12)
    w = bp(w_on_ali)
    return dict(r_direct=float((bp(ali) * w).mean()),
                r_z_mirrored=float((bp(ali[::-1].copy()) * w).mean()),
                overlap_fraction=float(inside.mean()))


def compare_tilt0(ali_projection, ali_pixel, tilt0, tilt0_pixel, axis_angle=None):
    # Coarse pose search: a broader band keeps thin features (membranes, the
    # lamella edge) correlated between search steps; the fine pass sharpens it.
    coarse = Matcher(ali_projection, ali_pixel, tilt0, tilt0_pixel, grid=128, band=(2.0, 16.0))
    angles = np.arange(0.0, 360.0, 3.0)
    best = coarse.search(log_range(0.4, 2.5, 41), angles, (False, True))
    fine = Matcher(ali_projection, ali_pixel, tilt0, tilt0_pixel)
    best = fine.search(best['scale'] * log_range(0.94, 1.06, 13),
                       best['angle'] + np.arange(-3.0, 3.01, 0.5), (best['flip'],))
    unit_r, _ = fine.score(1.0, best['angle'], best['flip'])
    return dict(best, r_at_scale_1=abs(unit_r), tomostar_axis_angle=axis_angle)


# --- report ---------------------------------------------------------------

def verdict(report, scale_tol, min_r, min_r_tilt0, mirror_margin):
    problems = []
    tol = math.log(1 + scale_tol)

    def judge(result, what, floor):
        # A scale is only meaningful when the images actually matched.
        if result['r'] < floor:
            problems.append('{} does not match the AreTomo projection at any scale/rotation tried '
                            '(best |r|={:.2f} < {:.2f}; its {:.2f}x scale estimate is unreliable)'
                            .format(what, result['r'], floor, result['scale']))
        elif abs(math.log(result['scale'])) > tol:
            problems.append('{} is magnified {:.2f}x relative to the AreTomo volume (shows {:.0%} '
                            'of the field)'.format(what, result['scale'], min(1.0, 1 / result['scale'])))
    judge(report['warp_vs_aretomo'], 'WARP reconstruction', min_r)
    mt = report.get('z_mirror')
    if mt and abs(mt['r_z_mirrored']) > abs(mt['r_direct']) - mirror_margin:
        problems.append('WARP volume matches the Z-mirrored AreTomo volume (|r|={:.2f}) about as '
                        'well as the direct one (|r|={:.2f})'.format(abs(mt['r_z_mirrored']),
                                                                   abs(mt['r_direct'])))
    if report.get('tilt0_vs_aretomo'):
        judge(report['tilt0_vs_aretomo'], '0-degree tilt', min_r_tilt0)
    report['problems'] = problems
    report['pass'] = not problems
    return report


def montage(path, ali, ali_pixel, warp, warp_pixel, tilt0, tilt0_pixel, report):
    m = Matcher(ali.sum(0), ali_pixel, warp.sum(0), warp_pixel)
    panels = [('AreTomo / TM volume (Z sum)', ali.sum(0), ali_pixel, 0.0, False),
              ('WARP reconstruction (Z sum), nominal scale', warp.sum(0), warp_pixel, 0.0, False)]
    t = report.get('tilt0_vs_aretomo')
    if tilt0 is not None:
        # The fit maps AreTomo onto the tilt by R(angle)*mirror; its inverse is
        # R(-angle), or R(angle)*mirror again when mirrored.
        rot = t['angle'] if t['flip'] else -t['angle']
        panels.append(('Raw {:+.0f} deg tilt in the AreTomo frame, nominal scale'.format(
            report['tilt0_angle']), tilt0, tilt0_pixel, rot, t['flip']))

    def display_image(img, pixel, rot, flip):
        g, inside = sample(prefilter(img, pixel, m.grid_pixel), pixel, m.shape, m.grid_pixel,
                           margin=MARGIN)
        if rot or flip:
            g, inside = sample(g, m.grid_pixel, m.shape, m.grid_pixel, 1.0, rot, flip)
        b = bandpass(g, inside)  # exactly what the correlation compared
        lo, hi = np.percentile(b[inside], [1, 99]) if inside.any() else (0, 1)
        return np.clip((b - lo) / (hi - lo + 1e-9), 0, 1) * inside

    def show(ax, title, img, pixel, rot, flip):
        ax.imshow(display_image(img, pixel, rot, flip), cmap='gray', origin='lower')
        ax.set_title(title, fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])

    v = report['warp_vs_aretomo']
    head = '{}  {}  |  WARP scale {:.2f}x r={:.2f}'.format(
        report['tilt_series'], 'PASS' if report['pass'] else 'FAIL', v['scale'], v['r'])
    if t:
        head += '  |  0-deg tilt scale {:.2f}x r={:.2f}'.format(t['scale'], t['r'])
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        # Pillow is enough to keep the required side-by-side evidence available
        # in lean environments that have no plotting stack.
        from PIL import Image, ImageDraw
        w, h = m.shape[1], m.shape[0]
        canvas = Image.new('RGB', (len(panels) * (w + 16) + 16, h + 100), 'white')
        draw = ImageDraw.Draw(canvas)
        draw.text((12, 8), head, fill='black' if report['pass'] else 'firebrick')
        for i, (title, img, pixel, rot, flip) in enumerate(panels):
            tile = (np.flipud(display_image(img, pixel, rot, flip)) * 255).astype(np.uint8)
            canvas.paste(Image.fromarray(tile).convert('RGB'), (16 + i * (w + 16), 72))
            draw.text((16 + i * (w + 16), 48), title[:35], fill='black')
        canvas.save(path)
    else:
        fig, axes = plt.subplots(1, len(panels), figsize=(5 * len(panels), 4.6))
        for ax, panel in zip(np.atleast_1d(axes), panels):
            show(ax, *panel)
        fig.suptitle(head + ('\n' + '; '.join(report['problems']) if report['problems'] else ''),
                     fontsize=10, color='black' if report['pass'] else 'firebrick')
        fig.tight_layout()
        fig.savefig(path, dpi=90)
        plt.close(fig)
    return str(path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--ali', required=True, help='AreTomo _ali.mrc (the template-matching volume)')
    p.add_argument('--warp', required=True, help='WARP reconstruction MRC')
    p.add_argument('--tomostar', help='tilt series .tomostar, to find the 0-degree tilt average')
    p.add_argument('--frame-settings', help='warp_frameseries.settings (average folder and pixel size)')
    p.add_argument('--out-prefix', required=True, help='writes <prefix>_geometry.json and .png')
    p.add_argument('--scale-tol', type=float, default=0.05)
    # Calibrated on 20260907 (4 tilt series): correct pairs |r| 0.85-0.89 (WARP) and
    # 0.72-0.73 (raw tilt); unmatched images ~0.14.
    p.add_argument('--min-r', type=float, default=0.4, help='WARP vs AreTomo projection')
    p.add_argument('--min-r-tilt0', type=float, default=0.35, help='0-degree tilt vs AreTomo projection')
    p.add_argument('--mirror-margin', type=float, default=0.1)
    a = p.parse_args()
    if bool(a.tomostar) != bool(a.frame_settings):
        p.error('--tomostar and --frame-settings go together')
    name = Path(a.ali).name.replace('_ali.mrc', '')
    try:
        ali, ali_pixel = read_volume(a.ali)
        warp, warp_pixel = read_volume(a.warp)
        report = dict(tilt_series=name, ali=a.ali, warp=a.warp,
                      ali_voxel_A=ali_pixel, warp_voxel_A=warp_pixel,
                      warp_vs_aretomo=compare_volumes(ali, ali_pixel, warp, warp_pixel))
        if abs(math.log(report['warp_vs_aretomo']['scale'])) <= math.log(1 + a.scale_tol):
            report['z_mirror'] = mirror_test(ali, ali_pixel, warp, warp_pixel)
        tilt0 = tilt0_pixel = None
        if a.tomostar:
            path, frame_pixel, angle = tilt0_from_tomostar(a.tomostar, a.frame_settings)
            tilt0, tilt0_pixel = read_image(path, frame_pixel)
            report.update(tilt0=str(path), tilt0_angle=angle, tilt0_pixel_A=tilt0_pixel)
            report['tilt0_vs_aretomo'] = compare_tilt0(ali.sum(0), ali_pixel, tilt0, tilt0_pixel)
    except (OSError, ValueError, KeyError, IndexError) as e:
        print('ERROR: {}: {}'.format(name, e), file=sys.stderr)
        return 2
    verdict(report, a.scale_tol, a.min_r, a.min_r_tilt0, a.mirror_margin)
    os.makedirs(os.path.dirname(os.path.abspath(a.out_prefix)), exist_ok=True)
    report['png'] = montage(a.out_prefix + '_geometry.png', ali, ali_pixel, warp, warp_pixel,
                            tilt0, tilt0_pixel, report)
    Path(a.out_prefix + '_geometry.json').write_text(json.dumps(report, indent=1))
    v = report['warp_vs_aretomo']
    line = '{} {}: WARP scale {:.2f}x r={:.2f} (r at 1x {:.2f})'.format(
        name, 'PASS' if report['pass'] else 'FAIL', v['scale'], v['r'], v['r_at_scale_1'])
    if report.get('z_mirror'):
        line += '; Z direct {:.2f} vs mirrored {:.2f}'.format(
            report['z_mirror']['r_direct'], report['z_mirror']['r_z_mirrored'])
    if report.get('tilt0_vs_aretomo'):
        t = report['tilt0_vs_aretomo']
        line += '; 0-deg tilt scale {:.2f}x r={:.2f}'.format(t['scale'], t['r'])
    print(line)
    for problem in report['problems']:
        print('  - ' + problem)
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
