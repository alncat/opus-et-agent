"""Thon-ring view: WARP's per-movie CTF fit, re-drawn from what WARP saved.

WARP stores, per movie, the radially averaged spectrum (`PS1D`, x in cycles/px,
background already subtracted), the fitted amplitude envelope
(`SimulatedScale`), the fitted CTF parameters, and a 2D power spectrum
(`powerspectrum/<movie>.mrc`, a half-plane with zero frequency at the bottom
centre). The fit curve is |CTF(f)| x envelope, exactly as WARP's GUI draws it
(`Movie.GetSimulated1D`, `CTF.Get1D`); the 2D rings follow `CTF.Get2D`.

Standard library only, like the rest of the dashboard server.
"""
import array
import base64
import math
import struct
import sys
import zlib
import xml.etree.ElementTree as ET


def parse_tomostar_tilts(text):
    """-> [(movie stem, tilt angle)] in file (acquisition-list) order."""
    cols, rows = [], []
    for line in (text or '').splitlines():
        s = line.strip()
        if s.startswith('_'):
            cols.append(s.split()[0])
        elif s and cols and not s.startswith(('data_', 'loop_', '#')):
            rows.append(s.split())
    if '_wrpMovieName' not in cols or '_wrpAngleTilt' not in cols:
        return []
    name, angle = cols.index('_wrpMovieName'), cols.index('_wrpAngleTilt')
    out = []
    for r in rows:
        if len(r) > max(name, angle):
            stem = r[name].replace('\\', '/').rsplit('/', 1)[-1].rsplit('.', 1)[0]
            out.append((stem, float(r[angle])))
    return out


def _pairs(text):
    out = []
    for item in (text or '').strip().split(';'):
        if '|' in item:
            x, y = item.split('|', 1)
            try:
                out.append((float(x), float(y)))
            except ValueError:
                pass
    return out


def parse_movie_ctf(xml_text):
    """Fitted CTF, 1D spectrum, envelope and fit range from a WARP movie XML."""
    root = ET.fromstring(xml_text)
    ctf = {p.get('Name'): p.get('Value') for p in root.findall('./CTF/Param')}
    opt = {p.get('Name'): p.get('Value') for p in root.findall('./OptionsCTF/Param')}

    def num(d, key, default):
        try:
            return float(d.get(key, default))
        except (TypeError, ValueError):
            return default
    params = {
        'defocus': num(ctf, 'Defocus', 0.0),            # um, underfocus positive
        'delta': num(ctf, 'DefocusDelta', 0.0),         # um, astigmatism
        'angle': num(ctf, 'DefocusAngle', 0.0),         # degrees
        'voltage': num(ctf, 'Voltage', 300.0),          # kV
        'cs': num(ctf, 'Cs', 2.7),                      # mm
        'amplitude': num(ctf, 'Amplitude', 0.07),
        'phase': num(ctf, 'PhaseShift', 0.0),           # fraction of pi
        'bfactor': num(ctf, 'Bfactor', 0.0),
        'pixel': num(ctf, 'PixelSize', 1.0),            # A, the spectrum's sampling
    }
    rmin, rmax = num(opt, 'RangeMin', 0.0), num(opt, 'RangeMax', 0.0)
    # RangeMin/Max are fractions of Nyquist (0.5 cycles/px).
    fit = [rmin / (2 * params['pixel']), rmax / (2 * params['pixel'])] if rmax > rmin > 0 else None
    res = root.get('CTFResolutionEstimate')
    return {
        'params': params,
        'window': int(num(opt, 'Window', 512)),
        'ps1d': _pairs(root.findtext('PS1D')),
        'scale': _pairs(root.findtext('SimulatedScale')),
        'fit_range': fit,                               # [f_lo, f_hi] in 1/A
        'resolution_estimate': float(res) if res not in (None, '') else None,
    }


def _ks(p):
    voltage = p['voltage'] * 1e3
    lam = 12.2643247 / math.sqrt(voltage * (1.0 + voltage * 0.978466e-6))
    cs = p['cs'] * 1e7
    return math.pi * lam, math.pi * 0.5 * cs * lam ** 3, p['bfactor'] * 0.25


def ctf_1d(p, freq):
    """WARP CTF.Get1D at spatial frequency `freq` (1/A), mean defocus, signed."""
    k1, k2, k4 = _ks(p)
    amp = p['amplitude']
    r2 = freq * freq
    arg = k1 * (-p['defocus'] * 1e4) * r2 + k2 * r2 * r2 - p['phase'] * math.pi
    value = amp * math.cos(arg) - math.sqrt(1 - amp * amp) * math.sin(arg)
    if k4:
        value *= math.exp(k4 * r2)
    return value


def _interp(knots, x):
    if not knots:
        return 1.0
    if x <= knots[0][0]:
        return knots[0][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y1
    return knots[-1][1]


def curves(fit):
    """-> [[freq 1/A, measured, fitted]] on WARP's PS1D grid."""
    p = fit['params']
    out = []
    for x, y in fit['ps1d']:
        f = x / p['pixel']                               # PS1D x is cycles/px
        out.append([f, y, abs(ctf_1d(p, f)) * _interp(fit['scale'], x)])
    return out


def zeros(p, fmax, step=2e-5):
    """Spatial frequencies (1/A) where the mean-defocus CTF crosses zero."""
    out, prev, f = [], ctf_1d(p, 1e-4), 1e-4
    while f < fmax:
        f += step
        cur = ctf_1d(p, f)
        if (cur > 0) != (prev > 0):
            out.append(f - step * cur / (cur - prev) if cur != prev else f)
        prev = cur
    return out


def read_mrc_2d(data):
    """First section of a mode-2 MRC as (nx, ny, array('f'))."""
    nx, ny, _nz, mode = struct.unpack('<4i', data[:16])
    if mode != 2:
        raise ValueError('power spectrum MRC mode %d, expected 2 (float32)' % mode)
    ext = struct.unpack('<i', data[92:96])[0]
    start = 1024 + max(ext, 0)
    values = array.array('f')
    values.frombytes(data[start:start + nx * ny * 4])
    if sys.byteorder != 'little':
        values.byteswap()
    if len(values) != nx * ny:
        raise ValueError('truncated power spectrum')
    return nx, ny, values


def png_gray(width, height, pixels):
    """8-bit greyscale PNG from a flat bytes-like of width*height values."""
    raw = b''.join(b'\x00' + bytes(pixels[y * width:(y + 1) * width]) for y in range(height))

    def chunk(tag, body):
        return (struct.pack('>I', len(body)) + tag + body
                + struct.pack('>I', zlib.crc32(tag + body) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def spectrum_png(mrc_bytes, fit, fmax=None):
    """Half-plane PNG: measured spectrum on the left, fitted CTF^2 on the right,
    cropped to `fmax` (1/A; default the fit's upper limit or Nyquist)."""
    nx, ny, values = read_mrc_2d(mrc_bytes)
    p, window = fit['params'], fit['window'] or nx
    nyquist = 0.5 / p['pixel']
    fmax = min(fmax or (fit['fit_range'] or [0, nyquist])[1] or nyquist, nyquist)
    rpx = max(8, min(ny - 1, int(round(fmax * window * p['pixel']))))
    cx, cy = nx // 2, ny - 1
    k1, k2, _ = _ks(p)
    k3 = math.atan(p['amplitude'] / math.sqrt(1 - p['amplitude'] ** 2))
    phase = p['phase'] * math.pi
    dz0, ddz = -p['defocus'] * 1e4, -p['delta'] * 1e4 * 0.5
    ang0 = math.radians(p['angle'])
    lo_f = (fit['fit_range'] or [fmax * 0.15, fmax])[0]
    # Contrast from the fitted ring zone only: the masked corners hold huge values.
    zone = []
    for row in range(cy - rpx, cy + 1):
        for col in range(cx - rpx, cx):
            f = math.hypot(col - cx, cy - row) / (window * p['pixel'])
            if lo_f <= f <= fmax:
                zone.append(values[row * nx + col])
    zone.sort()
    lo = zone[int(0.02 * (len(zone) - 1))] if zone else 0.0
    hi = zone[int(0.98 * (len(zone) - 1))] if zone else 1.0
    span = (hi - lo) or 1.0
    width, height = 2 * rpx, rpx
    out = bytearray(width * height)
    for j in range(height):
        row = cy - rpx + 1 + j
        dy = cy - row
        for i in range(width):
            dx = i - rpx
            r = math.hypot(dx, dy)
            if r > rpx:
                continue
            if dx < 0:
                v = (values[row * nx + cx + dx] - lo) / span
            else:
                f = r / (window * p['pixel'])
                # The stored half-plane's y axis runs opposite to WARP's angle
                # convention (verified by correlating fits against spectra).
                a = math.atan2(-dy, dx)
                dz = dz0 + ddz * math.cos(2 * (a - ang0))
                v = math.sin(k1 * dz * f * f + k2 * f ** 4 - phase - k3) ** 2
            out[j * width + i] = int(255 * min(1.0, max(0.0, v)))
    return png_gray(width, height, out), fmax


def thon_payload(xml_text, mrc_bytes=None):
    fit = parse_movie_ctf(xml_text)
    p = fit['params']
    data = curves(fit)
    fmax = min(0.5 / p['pixel'], (fit['fit_range'] or [0, 0.5 / p['pixel']])[1] * 1.05)
    out = {
        'params': p,
        'fit_range': fit['fit_range'],
        'resolution_estimate': fit['resolution_estimate'],
        'curves': [c for c in data if c[0] <= fmax],
        'zeros': zeros(p, fmax),
        'image': None,
    }
    if mrc_bytes:
        # Rings in a single low-dose tilt fade well before the fit limit; 8 A
        # keeps them readable. The 1D plot covers the whole fit range.
        png, img_fmax = spectrum_png(mrc_bytes, fit, fmax=min(fmax, 1 / 8.0))
        out['image'] = base64.b64encode(png).decode('ascii')
        out['image_fmax'] = img_fmax
    return out
