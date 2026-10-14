"""Thon-ring view: WARP's per-movie CTF fit re-drawn from its saved data."""
import json
import struct
import sys
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import poller as poller_mod
import status_server
import thon

# Fitted CTF of a real 20260907 tilt (MITO-260903-1-tomo_0644, 2-7 um refit).
PARAMS = dict(defocus=4.146373, delta=0.268983830047127, angle=12.0, voltage=200.0,
              cs=2.7, amplitude=0.07, phase=0.0, bfactor=0.0, pixel=2.369)


def movie_xml(p=PARAMS, n=256, noise=0.0):
    """A WARP movie XML whose PS1D is the |CTF| its own parameters predict."""
    ps1d = ';'.join('%g|%g' % (i / (2.0 * n), abs(thon.ctf_1d(p, i / (2.0 * n) / p['pixel'])) * 100 + noise)
                    for i in range(n))
    scale = ';'.join('%g|100' % (i / 40.0) for i in range(21))
    ctf = ''.join('<Param Name="%s" Value="%s" />' % kv for kv in (
        ('Defocus', p['defocus']), ('DefocusDelta', p['delta']), ('DefocusAngle', p['angle']),
        ('Voltage', p['voltage']), ('Cs', p['cs']), ('Amplitude', p['amplitude']),
        ('PhaseShift', p['phase']), ('Bfactor', p['bfactor']), ('PixelSize', p['pixel'])))
    opts = ('<Param Name="Window" Value="512" /><Param Name="RangeMin" Value="%s" />'
            '<Param Name="RangeMax" Value="%s" />' % (2 * p['pixel'] / 30, 2 * p['pixel'] / 5))
    return ('<Movie CTFResolutionEstimate="4.9"><CTF>%s</CTF><OptionsCTF>%s</OptionsCTF>'
            '<PS1D>%s</PS1D><SimulatedScale>%s</SimulatedScale></Movie>' % (ctf, opts, ps1d, scale))


def mrc_bytes(nx=512, ny=256, value=1.0):
    header = bytearray(1024)
    struct.pack_into('<4i', header, 0, nx, ny, 1, 2)
    return bytes(header) + struct.pack('<%df' % (nx * ny), *([value] * (nx * ny)))


def test_parse_tomostar_keeps_order_and_strips_paths():
    text = ('data_\n\nloop_\n_wrpMovieName #1\n_wrpAngleTilt #2\n'
            '  ../warp_frameseries/mov_0670.tif   39.00\n'
            '  C:\\\\scope\\\\mov_0644.tif  -0.00\n')
    assert thon.parse_tomostar_tilts(text) == [('mov_0670', 39.0), ('mov_0644', -0.0)]


def test_zeros_match_the_measured_minima_of_a_real_tilt():
    # These zeros coincided with the minima of that tilt's measured PS1D.
    got = [round(1 / z, 1) for z in thon.zeros(PARAMS, 1 / 13.0)[:5]]
    assert got == [32.6, 22.9, 18.7, 16.2, 14.4]


def test_fit_range_and_curves_follow_warp_units():
    fit = thon.parse_movie_ctf(movie_xml())
    assert [round(1 / f, 3) for f in fit['fit_range']] == [30.0, 5.0]
    rows = thon.curves(fit)
    # PS1D x is cycles/px: the last point is just below Nyquist, 1/(2*pixel).
    assert rows[-1][0] == pytest.approx((255 / 512) / 2.369)
    # Measured was synthesised from the same CTF with scale 100 (written with 6
    # significant figures): the fit reproduces it to within that rounding.
    assert max(abs(m - s) for _, m, s in rows) < 0.1


def test_payload_image_is_a_png_cropped_to_8_angstrom():
    out = thon.thon_payload(movie_xml(), mrc_bytes())
    import base64
    png = base64.b64decode(out['image'])
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    width, height = struct.unpack('>II', png[16:24])
    assert out['image_fmax'] == pytest.approx(1 / 8.0)
    assert height == round(512 * 2.369 / 8) and width == 2 * height
    assert out['resolution_estimate'] == 4.9 and out['zeros']


def test_non_float_spectrum_is_rejected():
    bad = bytearray(mrc_bytes())
    struct.pack_into('<i', bad, 12, 1)
    with pytest.raises(ValueError, match='mode 1'):
        thon.read_mrc_2d(bytes(bad))


class FakeClient:
    def __init__(self, files):
        self.files, self.read = files, []

    def read_file(self, path):
        self.read.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def read_bytes(self, path):
        self.read.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


def fetcher():
    star = ('data_\n\nloop_\n_wrpMovieName #1\n_wrpAngleTilt #2\n'
            '  ../warp_frameseries/mov_1.tif  30.0\n  ../warp_frameseries/mov_2.tif  -0.5\n')
    client = FakeClient({'/run/tomostar/TS_1.tomostar': star,
                         '/run/fs/mov_1.xml': movie_xml(), '/run/fs/mov_2.xml': movie_xml(),
                         '/run/fs/powerspectrum/mov_2.mrc': mrc_bytes()})
    return status_server.make_thon_fetch(client, '/run', lambda: {'TS_1'}, lambda: '/run/fs'), client


def test_fetch_defaults_to_the_tilt_nearest_zero_and_lists_tilts_by_angle():
    fetch, _ = fetcher()
    out = fetch('TS_1')
    assert out['movie'] == 'mov_2' and out['angle'] == -0.5 and out['image']
    assert [t['angle'] for t in out['tilts']] == [-0.5, 30.0]
    # A missing 2D spectrum still gives the 1D fit.
    assert fetch('TS_1', 'mov_1')['image'] is None


@pytest.mark.parametrize('series,movie', [('TS_2', None), ('../etc', None),
                                          ('TS_1', 'mov_9'), ('TS_1', '../../passwd')])
def test_fetch_refuses_unknown_series_and_movies(series, movie):
    fetch, client = fetcher()
    with pytest.raises(ValueError):
        fetch(series, movie)
    assert not any('passwd' in p or 'etc' in p or 'mov_9' in p for p in client.read)


def test_thon_endpoint_reports_bad_requests_as_400():
    fetch, _ = fetcher()
    p = poller_mod.Poller({}, {})
    handler = status_server.make_handler(p, None, 'tok', thon_fetch=fetch)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % httpd.server_address[1]
    try:
        with urllib.request.urlopen(base + '/api/thon?series=TS_1') as r:
            assert json.loads(r.read())['movie'] == 'mov_2'
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + '/api/thon?series=TS_9')
        assert exc.value.code == 400
    finally:
        httpd.shutdown()
