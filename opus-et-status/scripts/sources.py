"""Pure parsers: raw command output -> structured data.

No I/O, no SSH, no clock. Everything here is testable from recorded fixtures.
"""
from __future__ import annotations

import json
import math
import re
import shlex
import sys
from dataclasses import dataclass, replace
from pathlib import Path

# Reuse the conductor's parsers rather than writing a second implementation of
# the same formats -- two parsers for one format will drift. The aggregation in
# phase_status_from_validate (summing multiple phase_completion rows per phase)
# is subtle and must not be duplicated.
_CONDUCTOR_SCRIPTS = Path(__file__).resolve().parents[2] / "opus-et-conductor" / "scripts"
sys.path.insert(0, str(_CONDUCTOR_SCRIPTS))
import run_state as _run_state  # noqa: E402

# Pipe-delimited so parsing is deterministic regardless of name/field widths.
# NOTE: `squeue --me` needs SLURM >= 20.02 and is rejected outright by older
# clusters ("unrecognized option '--me'"), so select the user explicitly.
# whoami is used rather than $USER because $USER is not reliably set in a
# non-interactive ssh session.
# %Z is the directory the job was submitted from -- the only thing SLURM knows
# that ties a job to one run. Without it the panel shows every job on the
# account: on this cluster all 14 running jobs belonged to other directories.
SQUEUE_ARGV = ["sh", "-c",
               'squeue -u "$(whoami)" --noheader -o "%i|%j|%T|%M|%P|%R|%Z"']
SACCT_ARGV = [
    "sacct", "--noheader", "-P", "-X",
    "-o", "JobID,JobName,State,Elapsed,Partition,NodeList,AllocTRES",
]

_GPU_RE = re.compile(r"gres/gpu[=:](\d+)")

_ASSIGN = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$')

# Summarise every tomostar in one remote call. The tilt column is read from the
# STAR header (`_wrpAngleTilt #N`) rather than hardcoded, since field order is
# not guaranteed. Emits: name|n_tilts|min_tilt|max_tilt|max_dose
_TILT_AWK = r'''
    /^_wrpAngleTilt/ { split($2,a,"#"); col=a[2]+0; next }
    /^_wrpDose/      { split($2,a,"#"); dcol=a[2]+0; next }
    $1 ~ /^_/ || $1 ~ /^data_/ || $1 ~ /^loop_/ || NF<3 { next }
    col>0 {
      t=$(col)+0; n++;
      if (n==1 || t<mn) mn=t;
      if (n==1 || t>mx) mx=t;
      if (dcol>0) { d=$(dcol)+0; if (n==1 || d>md) md=d }
    }
    END { if (n>0) { fn=F; sub(/.*\//,"",fn); sub(/\.tomostar$/,"",fn);
          printf "%s|%d|%.2f|%.2f|%.1f\n", fn, n, mn, mx, md } }
'''


def tilt_series_cmd(tomostar_dir):
    """One command summarising every tilt series in the tomostar dir."""
    script = (
        'for f in "$1"/*.tomostar; do [ -e "$f" ] || continue; '
        'awk -v F="$f" ' + shlex.quote(_TILT_AWK) + ' "$f"; done'
    )
    return ["sh", "-c", script, "_", tomostar_dir]


# QC images the pipeline already produced. Only these directories are scanned,
# and the resulting listing is the allowlist for serving images.
QC_DIRS = ["qc", "gate1_qc", "gate2_qc", "gate3_qc", "gate4_qc", "qc_ondemand"]


def qc_list_cmd(work_dir):
    dirs = " ".join(shlex.quote(d) for d in QC_DIRS)
    # Nested folders are where this run keeps Gate 2 overlays
    # (`qc/gate2_j360/`, `qc/j360_picks/`). A top-level glob missed them.
    script = (
        'cd "$1" || exit 0; for d in ' + dirs + '; do '
        '[ -d "$d" ] || continue; '
        'find "$d" -maxdepth 3 -type f -name "*.png" ! -path "*/.*" 2>/dev/null; '
        'done'
    )
    return ["sh", "-c", script, "_", work_dir]


def parse_qc_list(text):
    """Relative paths only. Anything absolute, escaping, or outside the
    known QC directories is dropped: this listing is the allowlist used to
    serve image bytes."""
    allowed = set(QC_DIRS)
    out = []
    for line in text.splitlines():
        rel = line.strip().lstrip("./")
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            continue
        if rel.split("/", 1)[0] not in allowed:
            continue
        if not rel.lower().endswith(".png"):
            continue
        out.append(rel)
    return out


_SLICE_RE = re.compile(r"^(?P<stem>.+)_(?P<view>xy|xz)$", re.I)
_HAND_RE = re.compile(r"^handedness[_-](?P<stem>.+)$", re.I)
_SLAB_RE = re.compile(
    r"^(?P<stem>.+?)(?:_overlay)?_slab(?P<slab>\d+)_(?P<variant>all|topN)$", re.I)
_OVERLAY_RE = re.compile(r"^(?P<stem>.+)_overlay$", re.I)
_MONTAGES_RE = re.compile(r"^(?P<stem>.+)_slabs$", re.I)
# `gate2_qc` is a directory, not a species named "qc".
_GATE_DIR_RE = re.compile(r"^gate(\d+)(?:_(?!qc$)(?P<sp>.+))?$", re.I)
_NOISE_SUF = ("_overlay", "_picks", "_slabs")


def _norm(name):
    return name.replace("_", "").replace("-", "").upper()


def _match_tomo(stem, known):
    """Resolve a filename stem to a known tilt series, returning
    (tomo, leading_species_or_None). Directories disagree on separators --
    gate2_qc writes TS028 where the tomostar says TS_028 -- so compare
    normalised, then split the raw stem where its tail matches."""
    target = _norm(stem)
    best = None
    for k in known:
        nk = _norm(k)
        if target == nk or target.endswith(nk):
            if best is None or len(_norm(best)) < len(nk):
                best = k
    if best is None:
        return None, None
    if target == _norm(best):
        return best, None
    for i in range(1, len(stem)):
        if _norm(stem[i:]) == _norm(best):
            return best, (stem[:i].strip("_-") or None)
    return best, None


# Generates missing thumbnails on the cluster and streams them back base64 in
# ONE call. Full QC pngs are ~2 MB; a 280 px JPEG is ~1-2% of that. Colour is
# kept so pick overlays stay pick overlays, not grey smudges.
_THUMB_PY = r"""
import base64, os, sys
from PIL import Image
os.makedirs("qc_thumbs", exist_ok=True)
for line in sys.stdin:
    rel = line.strip()
    if not rel:
        continue
    out = os.path.join("qc_thumbs", rel.replace("/", "__") + ".280.jpg")
    if not os.path.exists(out):
        try:
            im = Image.open(rel)
            im.thumbnail((280, 280))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(out, "JPEG", quality=78)
        except Exception:
            continue
    try:
        with open(out, "rb") as fh:
            sys.stdout.write(rel + "\t" + base64.b64encode(fh.read()).decode() + "\n")
    except Exception:
        pass
"""


def thumb_script(work_dir):
    """Shell body; feed the wanted relative paths on stdin."""
    return f"cd {shlex.quote(work_dir)} && python3 -c {shlex.quote(_THUMB_PY)}"


def parse_thumbs(text):
    """``rel<TAB>base64`` lines -> {rel: data-uri}."""
    out = {}
    for line in text.splitlines():
        rel, _, b64 = line.partition("\t")
        if rel and b64:
            out[rel] = "data:image/jpeg;base64," + b64.strip()
    return out


# Particle counts at each pipeline stage, per species, in one call. A STAR
# data row is any non-empty line that is not a header, comment or loop marker.
_COUNT = ('awk \'/^[[:space:]]*(data_|loop_|#|_)/{next} NF>2{n++} END{print n+0}\' ')

_FUNNEL_SH = (
    'cd "$1" || exit 0; '
    'for d in template_matching/*/warp_star; do [ -d "$d" ] || continue; '
    '  label=${d#template_matching/}; label=${label%/warp_star}; '
    '  tot=0; for f in "$d"/*_warp.star; do [ -e "$f" ] || continue; '
    '    tot=$((tot+$(' + _COUNT + '"$f"))); done; '
    '  echo "$label|picks||$tot"; '
    '  e="warp_tiltseries/${label}_matching.star"; '
    '  [ -e "$e" ] && echo "$label|exported||$(' + _COUNT + '"$e")"; '
    '  for s in opuset/"$label"/*/sel_*.star; do [ -e "$s" ] || continue; '
    '    echo "$label|selected|$(basename "$(dirname "$s")")/$(basename "$s")|'
    '$(' + _COUNT + '"$s")"; done; '
    'done'
)


def funnel_cmd(work_dir):
    return ["sh", "-c", _FUNNEL_SH, "_", work_dir]


def parse_funnel(text):
    """`label|stage|detail|count` -> {label: {picks, exported, selected:[...]}}"""
    out = {}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 4:
            continue
        label, stage, detail, raw = parts
        try:
            n = int(raw)
        except ValueError:
            continue
        entry = out.setdefault(label, {"picks": None, "exported": None, "selected": []})
        if stage == "selected":
            entry["selected"].append({"name": detail, "count": n})
        elif stage in entry:
            entry[stage] = n
    for entry in out.values():
        entry["selected"].sort(key=lambda s: -s["count"])
    return out


# M writes the FSC curve itself (<species>_fsc.star), so resolution needs no
# recomputation -- only the 0.143 crossing of the phase-randomization-corrected
# column, which is the gold-standard number.
_M_SH = (
    'cd "$1"/m 2>/dev/null || exit 0; '
    'for d in species/*/; do [ -d "$d" ] || continue; '
    '  lab=$(basename "$d"); lab=${lab%_*}; '
    '  f="$d${lab}_fsc.star"; [ -e "$f" ] || continue; '
    '  awk -v L="$lab" \'/^[[:space:]]*(data_|loop_|#|_)/{next} '
    'NF>=5{print L"|fsc|"$1"|"$2"|"$3"|"$4"|"$5}\' "$f"; '
    'done; '
    'for pop in *.population; do [ -e "$pop" ] || continue; '
    '  awk \'/<LastRefinementOptions>/{f=1;next} /<\\/LastRefinementOptions>/{f=0} f\' "$pop" '
    '  | sed -nE \'s/.*Name="([^"]+)" Value="([^"]+)".*/opts|\\1|\\2/p\'; '
    'done'
)


def m_cmd(work_dir):
    return ["sh", "-c", _M_SH, "_", work_dir]


def fsc_resolution(rows, column="corrected", threshold=0.143):
    """Resolution where the FSC curve drops through `threshold`.

    Rows run coarse -> fine, so the crossing is between the last point above
    the threshold and the first below it; interpolate linearly in frequency
    (1/resolution), which is where the curve is close to straight.
    """
    prev = None
    for r in rows:
        res, val = r.get("resolution"), r.get(column)
        if res is None or val is None or res == float("inf"):
            continue
        if val < threshold:
            if prev is None:
                return res
            r0, v0 = prev
            if v0 == val:
                return res
            f0, f1 = 1.0 / r0, 1.0 / res
            frac = (v0 - threshold) / (v0 - val)
            return 1.0 / (f0 + frac * (f1 - f0))
        prev = (res, val)
    return None


def parse_m(text):
    """-> {species: {fsc: [...], resolution: {...}}, "_options": {...}}"""
    out, opts = {}, {}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if parts[0] == "opts" and len(parts) == 3:
            opts[parts[1]] = parts[2]
            continue
        if len(parts) != 7 or parts[1] != "fsc":
            continue
        label = parts[0]
        try:
            nums = [float(x) for x in parts[2:]]
        except ValueError:
            continue
        # The first row is the DC term with resolution Infinity: not plottable,
        # and non-finite floats are not representable in JSON.
        if not math.isfinite(nums[0]):
            continue
        out.setdefault(label, {"fsc": []})["fsc"].append({
            "resolution": nums[0], "unmasked": nums[1],
            "randomized": nums[2], "corrected": nums[3], "masked": nums[4]})
    for entry in out.values():
        entry["resolution"] = {
            col: fsc_resolution(entry["fsc"], col)
            for col in ("corrected", "masked", "unmasked")}
    if opts:
        out["_options"] = opts
    return out


def disk_cmd(work_dir):
    return ["df", "-Pk", "--", work_dir]


def parse_df(text):
    """`df -Pk` -> {total_gb, used_gb, avail_gb, pct_used}. POSIX -P keeps each
    filesystem on one line, so a long device name cannot wrap and shift the
    columns."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    parts = lines[-1].split()
    if len(parts) < 5:
        return None
    try:
        total, used, avail = (int(parts[-5]), int(parts[-4]), int(parts[-3]))
    except ValueError:
        return None
    pct = int(parts[-2].rstrip("%")) if parts[-2].rstrip("%").isdigit() else None
    gb = lambda kb: round(kb / 1048576.0, 1)
    return {"total_gb": gb(total), "used_gb": gb(used),
            "avail_gb": gb(avail), "pct_used": pct}


# --- frame / tilt-series quality ------------------------------------------
# `WarpTools filter_quality --settings ... --histograms` histograms exactly the
# fields WARP caches in processed_items.json, so read that cache directly. Two
# reasons not to shell out to WarpTools: it is a .NET binary whose GC cannot
# reserve its address space under the login node's `ulimit -v` (it dies with
# "Failed to create CoreCLR"), and the same numbers otherwise live in ~200 MB of
# per-movie XML where this is 100 KB of JSON.
_FRAMES_SH = (
    'cd "$1" || exit 0; '
    'for d in warp_frameseries warp_tiltseries; do '
    '  f="$d/processed_items.json"; [ -e "$f" ] || continue; '
    '  echo "@@$d"; cat "$f"; echo; '
    'done'
)

_FRAMES_MARKERS = ("@@warp_frameseries", "@@warp_tiltseries")

# (key, WARP's abbreviation, label, unit). Astigmatism has no single field: WARP
# stores the two components AsX/AsY, whose magnitude is the DefocusDelta the
# per-movie XML records.
FRAME_METRICS = (
    ("defocus", "Def", "Defocus", "um"),
    ("astigmatism", None, "Astigmatism", "um"),
    ("resolution", "Rsn", "CTF resolution", "A"),
    ("phase", "Phs", "Phase shift", ""),
    ("motion", "Mtn", "Motion", "A"),
    ("junk", "Jnk", "Masked", "%"),
    ("particles", "Ptc", "Particles", ""),
)


def frames_cmd(work_dir):
    return ["sh", "-c", _FRAMES_SH, "_", work_dir]


def _num(value):
    """WARP writes `null` for a metric it never measured; keep that distinct
    from 0, which is a real measurement."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def histogram(values, bins=24):
    """-> {bins: [{lo, hi, n}], n, min, max, median}, or None if no values."""
    vals = sorted(v for v in (_num(x) for x in values) if v is not None)
    if not vals:
        return None
    lo, hi, n = vals[0], vals[-1], len(vals)
    mid = n // 2
    median = vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0
    if hi == lo:
        counts = [{"lo": lo, "hi": hi, "n": n}]
    else:
        step = (hi - lo) / bins
        raw = [0] * bins
        for v in vals:
            # The maximum sits exactly on the top edge and would index one past
            # the last bin.
            raw[min(bins - 1, int((v - lo) / step))] += 1
        counts = [{"lo": lo + i * step, "hi": lo + (i + 1) * step, "n": c}
                  for i, c in enumerate(raw)]
    return {"bins": counts, "n": n, "min": lo, "max": hi, "median": median}


def _split_markers(text):
    out, current = {}, None
    for line in text.splitlines():
        if line.strip() in _FRAMES_MARKERS:
            current = line.strip()[2:]
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _load(chunk):
    try:
        data = json.loads(chunk)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _basename(path):
    return str(path or "").rsplit("/", 1)[-1]


def parse_frames(text):
    """WARP's quality cache -> histograms per metric plus per-tilt-series rows.

    Frames are grouped by the tilt series that lists them in its own `Tlts`
    array rather than by splitting their names, so any naming convention works.
    """
    chunks = _split_markers(text)
    movies = _load(chunks.get("warp_frameseries", ""))
    series_items = _load(chunks.get("warp_tiltseries", ""))

    frames, by_file = [], {}
    for item in movies:
        if not isinstance(item, dict):
            continue
        ax, ay = _num(item.get("AsX")), _num(item.get("AsY"))
        row = {
            "name": _basename(item.get("Path")),
            "defocus": _num(item.get("Def")),
            "astigmatism": (math.hypot(ax, ay) if ax is not None and ay is not None
                            else None),
            "resolution": _num(item.get("Rsn")),
            "phase": _num(item.get("Phs")),
            "motion": _num(item.get("Mtn")),
            "junk": _num(item.get("Jnk")),
            "particles": _num(item.get("Ptc")),
            "series": None,
        }
        frames.append(row)
        by_file.setdefault(row["name"], row)

    series = []
    for item in series_items:
        if not isinstance(item, dict):
            continue
        name = _basename(item.get("Path"))
        for suffix in (".tomostar", ".settings"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        # One CTF resolution per tilt, in acquisition order, so a gap stays a
        # gap: null keeps the remaining tilts at their true position.
        track = []
        for tilt in item.get("Tlts") or []:
            row = by_file.get(_basename(tilt))
            if row is None:
                track.append(None)
                continue
            row["series"] = name
            track.append(row["resolution"])
        series.append({
            "name": name,
            "n_tilts": len(item.get("Tlts") or []),
            "tilt_min": _num(item.get("MinTilt")),
            "tilt_max": _num(item.get("MaxTilt")),
            "defocus_min": _num(item.get("MinDefocus")),
            "defocus_mean": _num(item.get("MeanDefocus")),
            "defocus_max": _num(item.get("MaxDefocus")),
            "astigmatism": _num(item.get("Astigmatism")),
            "resolution": _num(item.get("CtfResolution")),
            "inclination": _num(item.get("CtfInclination")),
            "axis": _num(item.get("MeanAxis")),
            "max_shift": max([v for v in (_num(item.get("MaxShiftX")),
                                          _num(item.get("MaxShiftY")))
                              if v is not None] or [None]),
            "track": track,
        })
    series.sort(key=lambda s: s["name"])

    metrics, skipped = [], []
    for key, _abbrev, label, unit in FRAME_METRICS:
        hist = histogram(f[key] for f in frames)
        entry = {"key": key, "label": label, "unit": unit}
        if hist is None:
            skipped.append(dict(entry, note="not recorded for this dataset"))
        elif hist["min"] == hist["max"]:
            skipped.append(dict(entry, note="every frame: %g%s"
                                % (hist["min"], (" " + unit) if unit else "")))
        else:
            metrics.append(dict(entry, **hist))

    # Worst CTF resolution is the number that decides whether a tilt is worth
    # keeping, so name the offenders rather than only histogramming them.
    ranked = sorted((f for f in frames if f["resolution"] is not None),
                    key=lambda f: -f["resolution"])
    worst = [{"name": f["name"], "series": f["series"],
              "resolution": f["resolution"], "defocus": f["defocus"]}
             for f in ranked[:8]]

    return {"n_frames": len(frames), "n_series": len(series),
            "metrics": metrics, "skipped": skipped,
            "worst": worst, "series": series,
            # Keyed by movie name so the mdoc's acquisition order can be joined
            # onto it; build_status uses this and drops it from the payload.
            "by_name": {f["name"]: f["resolution"] for f in frames
                        if f["resolution"] is not None}}


# --- template-matching score distributions ---------------------------------
# The scores live ONLY here: neither the PyTOM star files nor WARP's *_warp.star
# carry them, so the number Gate 2 turns on is invisible without this file.
# Scores are binned on the cluster over the fixed [0, 1] domain of a correlation
# coefficient -- no two-pass range scan, and 100 counts crosses the wire instead
# of 5000 floats per tilt series.
TM_BINS = 100

_TM_SH = (
    'cd "$1" || exit 0; '
    'for d in template_matching/*/particles; do [ -d "$d" ] || continue; '
    '  label=${d#template_matching/}; label=${label%/particles}; '
    '  for f in "$d"/*_particles.xml; do [ -e "$f" ] || continue; '
    '    ts=$(basename "$f"); ts=${ts%_particles.xml}; '
    '    log="$d/${ts}_extraction.log"; cap=""; msv=""; '
    '    if [ -e "$log" ]; then '
    '      cap=$(sed -n \'s/.*--numberCandidates \\([0-9][0-9]*\\).*/\\1/p\' "$log" | head -1); '
    '    fi; '
    '    grep -o \'Score Type="[^"]*" Value="[^"]*"\' "$f" '
    '    | sed \'s/Score Type="//; s/" Value="/ /; s/"$//\' '
    '    | awk -v L="$label" -v T="$ts" -v C="$cap" -v NB=' + str(TM_BINS) + ' \'{ '
    '        ty=$1; v=$2+0; n++; s+=v; '
    '        if(n==1||v<mn)mn=v; if(n==1||v>mx)mx=v; '
    '        b=int(v*NB); if(b<0)b=0; if(b>=NB)b=NB-1; h[b]++; } '
    '      END { if(n>0){ out=""; for(i=0;i<NB;i++) out=out (i?",":"") (h[i]+0); '
    '        printf "%s|%s|%d|%.6f|%.6f|%.6f|%s|%s|%s\\n", '
    '          L,T,n,mn,mx,s/n,ty,C,out; } }\'; '
    '  done; '
    'done'
)


def tm_scores_cmd(work_dir):
    return ["sh", "-c", _TM_SH, "_", work_dir]


def parse_tm_scores(text):
    """-> {species: {series: [...], n, min, max, mean, bins, capped}}

    Each series carries `capped`: True when extractCandidates stopped at
    --numberCandidates, so the low end of that distribution is a cut-off rather
    than a floor; False when it found fewer than the cap; None when its log
    cannot be trusted to describe the file.
    """
    out = {}
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 9:
            continue
        (label, ts, raw_n, raw_mn, raw_mx, raw_mean,
         score_type, raw_cap, raw_bins) = parts
        try:
            n = int(raw_n)
            mn, mx, mean = float(raw_mn), float(raw_mx), float(raw_mean)
            bins = [int(x) for x in raw_bins.split(",")]
        except ValueError:
            continue
        if not all(math.isfinite(v) for v in (mn, mx, mean)):
            continue
        # extractCandidates writes its log once and PyTOM may be re-run
        # without rewriting it: one series here holds 8000 particles against a
        # log that says 5000, and the XML is a day newer. More particles than
        # the logged cap means the log does not describe this file, so the cap
        # is reported as unknown rather than wrong.
        cap = _int_or_none(raw_cap)
        if cap is not None and n > cap:
            cap, capped = None, None
        else:
            capped = None if cap is None else n == cap
        sp = out.setdefault(label, {"series": [], "bins": [0] * len(bins),
                                    "n": 0, "min": None, "max": None,
                                    "score_type": score_type})
        sp["series"].append({"name": ts, "n": n, "min": mn, "max": mx,
                             "mean": mean, "bins": bins, "cap": cap,
                             "capped": capped})
        if len(bins) == len(sp["bins"]):
            sp["bins"] = [a + b for a, b in zip(sp["bins"], bins)]
        sp["n"] += n
        sp["min"] = mn if sp["min"] is None else min(sp["min"], mn)
        sp["max"] = mx if sp["max"] is None else max(sp["max"], mx)
    for sp in out.values():
        sp["series"].sort(key=lambda s: s["name"])
        # Whether the low end of a distribution is a real floor or the point
        # extractCandidates stopped counting is read from the log it wrote, not
        # guessed from round numbers -- this run used 600, 800, 5000, 6000 and
        # 8000 for different series.
        sp["n_capped"] = sum(1 for s in sp["series"] if s["capped"])
        sp["n_unknown_cap"] = sum(1 for s in sp["series"] if s["capped"] is None)
        sp["mean"] = (sum(s["mean"] * s["n"] for s in sp["series"]) / sp["n"]
                      if sp["n"] else None)
    return out


def _int_or_none(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


# --- acquisition order and dose --------------------------------------------
# SerialEM ZValue is acquisition order, TiltAngle is geometry: in a
# dose-symmetric scheme they disagree. ExposureDose is often left at 0, so
# the reliable per-tilt accumulated dose is WARP's tomostar `_wrpDose`
# (already cumulative e-/Å²). Tomostar rows are typically geometric, so
# acquisition order is recovered by sorting on `_wrpDose`.
_MDOC_LOOP = (
    'for f in *.mdoc; do [ -e "$f" ] || continue; '
    '  awk -v T="${f%.mdoc}" \''
    '    /^\\[ZValue/ { split($0,a,"="); z=a[2]+0; seen=1; next } '
    '    seen && /^(TiltAngle|ExposureDose|SubFramePath|DateTime|StagePosition)[ \\t]*=/ '
    '      { v=$0; sub(/^[^=]*=[ \\t]*/,"",v); printf "%s|%d|%s|%s\\n", T, z, $1, v }\' '
    '  "$f"; '
    'done'
)
_MDOC_SH = 'cd "$1"/mdoc 2>/dev/null || exit 0; ' + _MDOC_LOOP

# Emits ts|movie|tilt|cum_dose. Column indices come from the STAR header.
_TOMOSTAR_DOSE_AWK = r'''
    /^_wrpMovieName/ { split($2,a,"#"); mcol=a[2]+0; next }
    /^_wrpAngleTilt/ { split($2,a,"#"); tcol=a[2]+0; next }
    /^_wrpDose/      { split($2,a,"#"); dcol=a[2]+0; next }
    $1 ~ /^_/ || $1 ~ /^data_/ || $1 ~ /^loop_/ { next }
    mcol>0 && dcol>0 && NF>=mcol && NF>=dcol {
      movie=$(mcol); sub(/.*\//,"",movie);
      tilt=(tcol>0 && NF>=tcol ? $(tcol)+0 : 0);
      dose=$(dcol)+0;
      printf "%s|%s|%.4f|%.4f\n", T, movie, tilt, dose
    }
'''


def mdoc_cmd(work_dir):
    return ["sh", "-c", _MDOC_SH, "_", work_dir]


def acquisition_cmd(work_dir):
    """Mdoc fields, then tomostar `_wrpDose` rows, in one remote call."""
    script = (
        '{ cd "$1"/mdoc 2>/dev/null && ' + _MDOC_LOOP + '; }; '
        'echo ---STAR---; '
        'for f in "$1"/tomostar/*.tomostar; do [ -e "$f" ] || continue; '
        '  T=$(basename "$f" .tomostar); '
        '  awk -v T="$T" ' + shlex.quote(_TOMOSTAR_DOSE_AWK) + ' "$f"; '
        'done'
    )
    return ["sh", "-c", script, "_", work_dir]


def _movie_stem(path):
    """SubFramePath may be a Windows path from the microscope PC."""
    name = str(path or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name


def _movie_stem_key(name):
    base = _movie_stem(name)
    return base.rsplit(".", 1)[0] if "." in base else base


def _series_stem(name):
    n = str(name or "").rsplit("/", 1)[-1]
    return n[:-4] if n.endswith(".mrc") else n


def parse_mdoc(text):
    """-> {series: {tilts: [{order, tilt, dose, cum_dose, movie}], ...}}"""
    raw = {}
    for line in text.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        ts, z, key, value = parts
        try:
            order = int(z)
        except ValueError:
            continue
        raw.setdefault(ts, {}).setdefault(order, {})[key] = value.strip()

    out = {}
    for ts, entries in raw.items():
        tilts, cum = [], 0.0
        for order in sorted(entries):
            e = entries[order]
            dose = _num(_float_or_none(e.get("ExposureDose")))
            cum += dose or 0.0
            stage = [_float_or_none(x) for x in (e.get("StagePosition") or "").split()]
            tilts.append({
                "order": order,
                "tilt": _float_or_none(e.get("TiltAngle")),
                "dose": dose,
                "cum_dose": round(cum, 4),
                "movie": _movie_stem(e.get("SubFramePath")),
                "time": e.get("DateTime") or None,
                "stage": [s for s in stage[:2] if s is not None] or None,
            })
        if not tilts:
            continue
        angles = [t["tilt"] for t in tilts if t["tilt"] is not None]
        # Dose-symmetric acquisition revisits both sides as it goes, so the
        # tilt angle keeps changing sign; a continuous tilt series does not.
        flips = sum(1 for a, b in zip(angles, angles[1:]) if (a < 0) != (b < 0))
        drift = None
        stages = [t["stage"] for t in tilts if t["stage"]]
        if len(stages) > 1:
            x0, y0 = stages[0]
            drift = max(math.hypot(x - x0, y - y0) for x, y in stages)
        out[ts] = {
            "tilts": tilts,
            "n": len(tilts),
            "total_dose": round(cum, 3),
            "scheme": "dose-symmetric" if flips > len(angles) / 3 else "continuous",
            "stage_drift": round(drift, 4) if drift is not None else None,
        }
    return out


def _scheme_from_angles(angles):
    flips = sum(1 for a, b in zip(angles, angles[1:]) if (a < 0) != (b < 0))
    return "dose-symmetric" if flips > len(angles) / 3 else "continuous"


def parse_tomostar_dose(text):
    """-> same shape as parse_mdoc. `_wrpDose` is already cumulative."""
    raw = {}
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 4:
            continue
        ts, movie, tilt_s, dose_s = parts
        cum = _float_or_none(dose_s)
        if not ts or cum is None:
            continue
        raw.setdefault(ts, []).append({
            "movie": _movie_stem(movie),
            "tilt": _float_or_none(tilt_s),
            "cum_dose": round(cum, 4),
        })
    out = {}
    for ts, rows in raw.items():
        # STAR order is typically geometric; acquisition order is dose.
        rows.sort(key=lambda r: (r["cum_dose"],
                                 r["tilt"] if r["tilt"] is not None else 0.0))
        prev, tilts = 0.0, []
        for i, r in enumerate(rows):
            tilts.append({
                "order": i,
                "tilt": r["tilt"],
                "dose": round(r["cum_dose"] - prev, 4),
                "cum_dose": r["cum_dose"],
                "movie": r["movie"],
                "time": None,
                "stage": None,
            })
            prev = r["cum_dose"]
        if not tilts:
            continue
        angles = [t["tilt"] for t in tilts if t["tilt"] is not None]
        out[ts] = {
            "tilts": tilts,
            "n": len(tilts),
            "total_dose": round(tilts[-1]["cum_dose"], 3),
            "scheme": _scheme_from_angles(angles),
            "stage_drift": None,
        }
    return out


def merge_acquisition(mdoc, tomostar):
    """Prefer tomostar `_wrpDose` when mdoc ExposureDose is missing or zero."""
    mdoc_by = {_series_stem(k): v for k, v in (mdoc or {}).items()}
    star_by = {_series_stem(k): v for k, v in (tomostar or {}).items()}
    out = {}
    for key in sorted(set(mdoc_by) | set(star_by)):
        m, s = mdoc_by.get(key), star_by.get(key)
        mdoc_dead = (not m) or (m.get("total_dose") or 0) == 0
        if s and mdoc_dead:
            merged = dict(s)
            if m:
                merged["stage_drift"] = m.get("stage_drift")
            out[key] = merged
        elif m:
            out[key] = m
        elif s:
            out[key] = s
    return out


def parse_acquisition(text):
    mdoc_text, _, star_text = (text or "").partition("---STAR---")
    return merge_acquisition(parse_mdoc(mdoc_text), parse_tomostar_dose(star_text))


def _float_or_none(text):
    try:
        return float(str(text).split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def join_dose(acquisition, by_name):
    """Acquisition order carries the accumulated dose; the frame cache carries
    the CTF resolution. Joined on the movie name, they give resolution against
    dose -- the radiation-damage curve -- rather than against tilt angle."""
    exact = dict(by_name or {})
    stems = {}
    for k, v in exact.items():
        stems.setdefault(_movie_stem_key(k), v)
    out = {}
    for ts, entry in (acquisition or {}).items():
        points = []
        for t in entry.get("tilts", []):
            movie = t.get("movie") or ""
            if not movie:
                continue
            res = exact.get(movie)
            if res is None:
                res = exact.get(_movie_stem(movie))
            if res is None:
                res = stems.get(_movie_stem_key(movie))
            if res is None:
                continue
            points.append({"cum_dose": t["cum_dose"], "tilt": t["tilt"],
                           "resolution": res, "order": t["order"]})
        if points:
            out[ts] = {"points": points, "total_dose": entry.get("total_dose"),
                       "scheme": entry.get("scheme"),
                       "stage_drift": entry.get("stage_drift")}
    return out


# --- tilt-series alignment and geometry ------------------------------------
# AreTomo's .aln holds the coarse alignment; WARP's per-series XML holds the
# handedness flag and the fitted specimen plane. Both are per tilt series, so
# they travel in one call.
_ALIGN_SH = (
    'cd "$1" || exit 0; '
    'for d in warp_tiltseries/tiltstack/*/; do [ -d "$d" ] || continue; '
    '  ts=$(basename "$d"); '
    '  for f in "$d"*.aln; do [ -e "$f" ] || continue; '
    '    awk -v T="$ts" \'/^#/ { if ($0 ~ /DarkFrame/) dark++; next } '
    '      NF>=10 { printf "%s|aln|%d|%s|%s|%s|%s\\n", T, $1, $2, $4, $5, $10 } '
    '      END { printf "%s|dark|%d\\n", T, dark+0 }\' "$f"; '
    '    break; '
    '  done; '
    '  x="warp_tiltseries/${ts}.xml"; [ -e "$x" ] && '
    '    printf "%s|xml|%s\\n" "$ts" "$(grep -o \'<TiltSeries [^>]*\' "$x" | head -1)"; '
    'done'
)


def align_cmd(work_dir):
    return ["sh", "-c", _ALIGN_SH, "_", work_dir]


_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_align(text):
    """-> {series: {axis, max_shift_px, dark, inverted, track}}

    AreTomo shifts are in pixels of the aligned (binned) stack, not angstrom;
    the panel says so rather than implying a physical unit it cannot know.
    The XML's PlaneNormal is deliberately not surfaced: its inclination is the
    CtfInclination the frames panel already shows.
    """
    out = {}
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        ts, kind = parts[0], parts[1]
        entry = out.setdefault(ts, {"track": [], "axis": None, "dark": 0,
                                    "inverted": None})
        if kind == "aln" and len(parts) == 7:
            try:
                _sec, rot, tx, ty, tilt = (int(parts[2]), float(parts[3]),
                                           float(parts[4]), float(parts[5]),
                                           float(parts[6]))
            except ValueError:
                continue
            if not all(math.isfinite(v) for v in (rot, tx, ty, tilt)):
                continue
            entry["axis"] = rot
            entry["track"].append({"tilt": tilt, "shift": math.hypot(tx, ty)})
        elif kind == "dark" and len(parts) == 3:
            try:
                entry["dark"] = int(parts[2])
            except ValueError:
                pass
        elif kind == "xml":
            attrs = dict(_ATTR_RE.findall("|".join(parts[2:])))
            inverted = attrs.get("AreAnglesInverted")
            if inverted is not None:
                entry["inverted"] = inverted.strip().lower() == "true"
    for entry in out.values():
        shifts = [p["shift"] for p in entry["track"]]
        entry["max_shift_px"] = round(max(shifts), 2) if shifts else None
        entry["n_tilts"] = len(entry["track"])
    return out


_VARREF_RE = re.compile(r"^\$\{?([A-Za-z_]\w*)\}?$")
_PRODUCT_RE = re.compile(r"\$\{?([A-Za-z_]\w*)\}?\s*\*\s*\$\{?([A-Za-z_]\w*)\}?")


def resolve_derived(raw, values, depth=0):
    """Compute a conf value that is derived from other conf values.

    Deliberately NOT a shell evaluator. These files get `source`d, so a
    read-only viewer must never execute them; only two shapes are recognised
    and both are plain arithmetic on values already parsed:

      VAR="$OTHER"                                  -> the other value
      VAR="$(awk ... $A * $B ...)"                  -> A x B   (ALIGN_ANGPIX)

    Anything else returns None and the caller keeps showing the literal
    expression, which is honest about what is not understood.
    """
    if not raw or "$" not in raw or depth > 4:
        return None
    m = _VARREF_RE.match(raw.strip())
    if m:
        other = values.get(m.group(1))
        if not other:
            return None
        return other if "$" not in other else resolve_derived(other, values, depth + 1)
    m = _PRODUCT_RE.search(raw)
    if m:
        a, b = values.get(m.group(1)), values.get(m.group(2))
        try:
            return f"{float(a) * float(b):g}"
        except (TypeError, ValueError):
            return None
    return None


def qc_section(directory):
    """Map a path's parent onto a pipeline check (qc / gate1_qc / ...).

    Nested folders such as `qc/gate2_j360/` are Gate 2, not reconstruction.
    """
    parts = [p for p in (directory or "").split("/") if p]
    if not parts:
        return "qc"
    top = parts[0]
    if top in ("gate1_qc", "gate2_qc", "gate3_qc", "gate4_qc", "qc_ondemand"):
        return top
    for p in parts:
        m = _GATE_DIR_RE.match(p)
        if m:
            return f"gate{int(m.group(1))}_qc"
        if "pick" in p.lower():
            return "gate2_qc"
    return top if top in QC_DIRS else "qc"


def _species_from_dir(directory):
    for p in (directory or "").split("/"):
        m = _GATE_DIR_RE.match(p)
        if m and m.group("sp"):
            return m.group("sp")
        if p.lower().endswith("_picks") and len(p) > 6:
            return p[:-6]
    return None


def _strip_noise(stem):
    """Peel `_overlay` / `_top200` / `_slabs` off so the tomogram can match."""
    s = stem
    while True:
        n = s
        lower = s.lower()
        for suf in _NOISE_SUF:
            if lower.endswith(suf):
                s = s[:-len(suf)]
                lower = s.lower()
        s2, nsub = re.subn(r"_top\d+$", "", s, flags=re.I)
        s = s2
        if s == n and nsub == 0:
            return s


def _humanize(stem):
    s = stem
    s = re.sub(r"all_ts", "all-series", s, flags=re.I)
    s = re.sub(r"template_check", "template", s, flags=re.I)
    s = re.sub(r"mask_coverage(?:_review)?", "mask coverage", s, flags=re.I)
    s = re.sub(r"median_slab", "median slab", s, flags=re.I)
    s = re.sub(r"_top(\d+)", r" top-\1", s, flags=re.I)
    s = re.sub(r"_+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _resolve_tomo(stem, known):
    """Match a stem against known tilt series. If the listing has not
    arrived yet, believe the filename so a slice is still labelled
    `XY slice` rather than a humanized leftover."""
    tomo, species = _match_tomo(stem, known)
    if tomo or known:
        return tomo, species
    return stem, None


def parse_qc_name(rel, known_tomos=()):
    """Best-effort structure for a QC image path, for grouping in the UI."""
    directory, _, fname = rel.rpartition("/")
    stem = fname[:-4] if fname.lower().endswith(".png") else fname
    dir_sp = _species_from_dir(directory)
    out = {"path": rel, "dir": directory or ".", "file": fname,
           "section": qc_section(directory),
           "tomo": None, "species": dir_sp, "kind": "other",
           "slab": None, "variant": None, "label": _humanize(stem)}

    def finish(**kw):
        out.update(kw)
        if not out.get("species"):
            out["species"] = dir_sp
        return out

    m = _HAND_RE.match(stem)
    if m:
        tomo, _ = _resolve_tomo(m.group("stem"), known_tomos)
        return finish(kind="handedness", tomo=tomo, label="handedness")
    m = _SLAB_RE.match(stem)
    if m:
        tomo, species = _resolve_tomo(_strip_noise(m.group("stem")), known_tomos)
        variant = m.group("variant")
        return finish(kind="overlay", tomo=tomo, species=species,
                      slab=int(m.group("slab")), variant=variant,
                      label=f"slab {int(m.group('slab'))} \u00b7 "
                            + ("all picks" if variant.lower() == "all" else "top-N"))
    m = _OVERLAY_RE.match(stem)
    if m:
        tomo, species = _resolve_tomo(_strip_noise(m.group("stem")), known_tomos)
        return finish(kind="overlay", tomo=tomo, species=species, label="picks")
    m = _MONTAGES_RE.match(stem)
    if m:
        tomo, species = _resolve_tomo(_strip_noise(m.group("stem")), known_tomos)
        top = re.search(r"top(\d+)", stem, re.I)
        return finish(kind="overlay", tomo=tomo, species=species,
                      variant="topN" if top else None,
                      label=(f"top-{top.group(1)} slabs" if top else "slabs"))
    m = _SLICE_RE.match(stem)
    if m:
        core = m.group("stem")
        zval = None
        zm = re.search(r"_z(\d+)$", core)
        if zm:
            zval = int(zm.group(1))
            core = core[:zm.start()]
        tomo, _ = _resolve_tomo(_strip_noise(core), known_tomos)
        view = m.group("view").upper()
        label = (f"{view} slice" + (f" \u00b7 z={zval}" if zval is not None else "")
                 if tomo else f"{_humanize(core)} \u00b7 {view}")
        return finish(kind="slice", tomo=tomo, slab=zval, label=label)
    tomo, species = _match_tomo(_strip_noise(stem), known_tomos)
    if tomo:
        return finish(tomo=tomo, species=species)
    return out


def parse_tilt_series(text):
    out = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 5:
            continue
        name, n, mn, mx, dose = parts
        try:
            out.append(TiltSeries(name, int(n), float(mn), float(mx), float(dose)))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class Job:
    job_id: str
    name: str
    state: str
    elapsed: str
    partition: str
    nodelist: str
    gpu: int | None = None      # allocated GPUs (sacct AllocTRES); squeue has none
    workdir: str | None = None  # squeue %Z; sacct does not report it here


@dataclass(frozen=True)
class TiltSeries:
    name: str
    n_tilts: int
    min_tilt: float
    max_tilt: float
    max_dose: float


@dataclass(frozen=True)
class ConfigLine:
    key: str
    value: str
    comment: str
    lineno: int


def parse_squeue(text):
    jobs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue
        workdir = parts[6] if len(parts) > 6 else ""
        jobs.append(Job(*parts[:6], workdir=workdir or None))
    return jobs


def belongs_to_run(job, work_dir):
    """A job belongs to this run when it was submitted from the run directory
    or somewhere under it. An unknown workdir is never claimed: an older
    squeue without %Z should show nothing rather than everything."""
    wd = (getattr(job, "workdir", None) or "").rstrip("/")
    root = str(work_dir or "").rstrip("/")
    if not wd or not root:
        return False
    return wd == root or wd.startswith(root + "/")


# The run's own logs/ is the record of which jobs belong to it: SLURM writes
# `<name>_<jobid>.out`, so the directory listing IS the job list, and it
# survives sacct having no accounting data at all -- which is the case on this
# cluster, where sacct returns zero rows for any query.
_RUN_JOBS_SH = r'''
cd "$1" || exit 0
ls -1 logs 2>/dev/null | awk '/_[0-9]+\.(out|err)$/ {
    b = $0; ext = b; sub(/.*\./, "", ext); sub(/\.[^.]*$/, "", b)
    id = b; sub(/.*_/, "", id); name = b; sub(/_[0-9]+$/, "", name)
    printf "L|%s|%s|%s\n", id, name, ext }'
ids=$(ls -1 logs 2>/dev/null | awk '/_[0-9]+\.(out|err)$/ {
        b = $0; sub(/\.[^.]*$/, "", b); sub(/.*_/, "", b); print b }' |
      sort -un | tail -500 | tr "\n" "," | sed "s/,$//")
[ -n "$ids" ] && sacct --noheader -P -X -S 2020-01-01 -j "$ids" \
    -o JobID,JobName,State,Elapsed,Partition,NodeList,AllocTRES 2>/dev/null |
    sed "s/^/S|/"
exit 0
'''


def run_jobs_cmd(work_dir):
    return ["sh", "-c", _RUN_JOBS_SH, "_", work_dir]


def parse_run_jobs(text):
    """-> {"logs": [{job_id, name, streams}], "sacct": [Job, ...]}"""
    logs, rows = {}, []
    for line in text.splitlines():
        if line.startswith("L|"):
            parts = line.split("|")
            if len(parts) != 4 or not parts[1].isdigit():
                continue
            entry = logs.setdefault(parts[1], {"job_id": parts[1],
                                               "name": parts[2], "streams": []})
            if parts[3] not in entry["streams"]:
                entry["streams"].append(parts[3])
        elif line.startswith("S|"):
            rows.append(line[2:])
    for entry in logs.values():
        entry["streams"].sort()
    return {"logs": sorted(logs.values(), key=lambda r: int(r["job_id"])),
            "sacct": parse_sacct("\n".join(rows))}


def parse_sacct(text):
    jobs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        gpu = None
        if len(parts) > 6:
            m = _GPU_RE.search(parts[6])
            if m:
                gpu = int(m.group(1))
        jobs.append(Job(*(p.strip() for p in parts[:6]), gpu=gpu))
    return jobs


def merge_jobs(live, historical):
    """squeue forgets a job the moment it finishes, so sacct supplies history.
    A job present in both is reported with its live squeue state -- but squeue
    carries no GPU allocation, so a live row missing it takes the historic
    value (otherwise running jobs silently drop out of the GPU-hours total)."""
    hist = {j.job_id: j for j in historical}
    merged = dict(hist)
    merged.update({j.job_id: j for j in live})
    out = []
    for j in merged.values():
        if j.gpu is None and j.job_id in hist and hist[j.job_id].gpu is not None:
            j = replace(j, gpu=hist[j.job_id].gpu)
        out.append(j)
    return sorted(out, key=lambda j: j.job_id)


def read_run_state(json_text):
    return json.loads(json_text)


def phase_completion(validate_json):
    return _run_state.phase_status_from_validate(validate_json)


def parse_config(text):
    """Parse shell-style KEY=VALUE lines, keeping the inline comment.

    Only live assignments count; a commented-out line is not a key.
    """
    out = {}
    for lineno, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("#"):
            continue
        m = _ASSIGN.match(line)
        if not m:
            continue
        key, rest = m.group(1), m.group(2)
        comment = ""
        hash_at = rest.find("#")
        if hash_at != -1:
            comment = rest[hash_at:].strip()
            rest = rest[:hash_at]
        value = rest.strip().strip('"').strip("'")
        out[key] = ConfigLine(key=key, value=value, comment=comment, lineno=lineno)
    return out


# ---------------------------------------------------------------------------
# Inventory: which pipeline artifacts exist per tilt series. One shell call
# flags every stage; the listing answers "which TS is holding up the next
# phase" without clicking through anything.
# ---------------------------------------------------------------------------

INVENTORY_COLS = ["stack", "aligned", "recon", "tm", "export"]


def inventory_cmd(work_dir):
    """One command flagging stack/align/recon/TM/export per tomostar.

    Emits ``name|stack|aligned|recon|tm|export`` with 0/1 flags. TM matches
    any species label; export greps the tilt series in every matching STAR.
    """
    script = (
        'cd "$1" || exit 0; '
        'for f in tomostar/*.tomostar; do [ -e "$f" ] || continue; '
        'ts=${f##*/}; ts=${ts%.tomostar}; '
        'd=warp_tiltseries/tiltstack/$ts; '
        'a=0; [ -e "$d/$ts.st" ] && a=1; '
        'b=0; [ -e "$d/${ts}_ali.mrc" ] && b=1; '
        'c=0; set -- warp_tiltseries/reconstruction/${ts}_*Apx.mrc; '
        '[ -e "$1" ] && c=1; '
        'e=0; set -- template_matching/*/warp_star/${ts}_warp.star; '
        '[ -e "$1" ] && e=1; '
        'g=0; for m in warp_tiltseries/*_matching.star; do '
        # A bare substring match would let TS_01 claim rows of TS_010 or
        # TS_011, so the name must be followed by a non-digit or line end.
        '[ -e "$m" ] || continue; '
        'grep -qE "${ts}([^0-9]|\\$)" "$m" && { g=1; break; }; done; '
        "printf '%s|%d|%d|%d|%d|%d\\n' \"$ts\" \"$a\" \"$b\" \"$c\" \"$e\" \"$g\"; "
        'done'
    )
    return ["sh", "-c", script, "_", work_dir]


def parse_inventory(text):
    """``name|stack|aligned|recon|tm|export`` rows -> flag dicts."""
    out = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 6:
            continue
        name, flags = parts[0], parts[1:]
        if not name:
            continue
        row = {"name": name}
        for col, flag in zip(INVENTORY_COLS, flags):
            row[col] = flag == "1"
        out.append(row)
    return out


def parse_elapsed(text):
    """squeue/sacct elapsed (``12:34``, ``1:12:03``, ``2-03:04:05``) -> seconds."""
    s = str(text).strip()
    if not s or s in ("INVALID", "UNLIMITED"):
        return None
    days = 0
    if "-" in s:
        d, _, s = s.partition("-")
        try:
            days = int(d)
        except ValueError:
            return None
    try:
        secs = 0
        for part in s.split(":"):
            secs = secs * 60 + int(part)
    except ValueError:
        return None
    return days * 86400 + secs


# ---------------------------------------------------------------------------
# Training: OPUS-ET (cryodrgn train_tomo_dist) writes checkpoints as
# weights.<N>.pkl under its output dir (opuset/<label>/z<ZDIM> by default)
# and per-epoch loss either to loss.txt there or to the slurm .out log.
# The remote scan emits RUN/COUNT/RAW lines; the RAW payload is parsed
# tolerantly here so an upstream format change degrades to no curve, never
# to a wrong one.
# ---------------------------------------------------------------------------

def training_cmd(work_dir):
    script = (
        'cd "$1" || exit 0; '
        'find opuset* -type f -name "weights.*.pkl" 2>/dev/null | '
        'while IFS= read -r w; do echo "${w%/*}"; done | sort | uniq | head -12 | '
        'while IFS= read -r d; do '
        'echo "RUN $d"; '
        "n=0; for w in \"$d\"/weights.*.pkl; do [ -e \"$w\" ] && n=$((n+1)); done; "
        'echo "COUNT $d $n"; '
        # Checkpoint mtimes give epochs/hour, so a running job can show an ETA
        # against the Epochs count the log header declares.
        "for w in \"$d\"/weights.*.pkl; do [ -e \"$w\" ] || continue; "
        'e=${w##*/}; e=${e#weights.}; e=${e%.pkl}; '
        'echo "MTIME $d $e $(stat -c %Y "$w" 2>/dev/null)"; done; '
        'if [ -f "$d/loss.txt" ]; then '
        'head -400 "$d/loss.txt" | while IFS= read -r l; '
        'do echo "RAW $d $l"; done; '
        'else '
        # Anchored on the script's own "Output: <dir>" echo and the path END:
        # a bare substring would let opuset/ribo/z8 claim logs belonging to
        # z8_angpix337_OLD or z8_expanded, and warm-start --load lines name
        # other runs' paths too.
        'log=$(grep -Eils "Output: .*${d}\\$" '
        'logs/train_opuset_*.out logs/train_opuset_*.err 2>/dev/null | sort | tail -1); '
        'if [ -n "$log" ]; then '
        'echo "LOG $d $log"; '
        # The header the trainer echoes before starting: config for the
        # overview, inputs for the about section.
        "awk -v d=\"$d\" "
        "'/^[[:space:]]*(Epochs|Batch size|Learning rate|zdim|Output|STAR|Poses"
        "|Mask|Split|Warm-start|ANGPIX|Tilt range|Tilt step):/ "
        '{ gsub(/^[[:space:]]+/, ""); print "PARAM", d, $0 }\' "$log" | head -14; '
        # Slurm logs are tqdm progress spam: thousands of \\r-separated updates
        # per epoch, each with batch-level metrics. Split CRs, then AVERAGE the
        # batch values per epoch -- a single batch loss is one noisy draw out
        # of thousands (spread 0 to -0.0085 in a real run), which made a
        # steadily falling run look flat. A metric the trainer did not print
        # that epoch is emitted as "-" so the columns cannot silently misalign.
        "tr '\\r' '\\n' < \"$log\" | awk -v d=\"$d\" '"
        "/[Ee]poch/ && /loss=/ { "
        "ep = \"\"; lo = \"\"; be = \"\"; mu = \"\"; sn = \"\"; st = \"\"; "
        "if (match($0, /[Ee]poch[: ]*\\[[0-9]+/)) "
        '{ ep = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", ep) } '
        "else if (match($0, /[Ee]poch[: ]*[0-9]+/)) "
        '{ ep = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", ep) } '
        "if (match($0, /loss=[-+0-9.eE]+/)) "
        "{ lo = substr($0, RSTART + 5, RLENGTH - 5) } "
        "if (match($0, /beta=[-+0-9.eE]+/)) "
        "{ be = substr($0, RSTART + 5, RLENGTH - 5) } "
        "if (match($0, /mu=[-+0-9.eE]+/)) "
        "{ mu = substr($0, RSTART + 3, RLENGTH - 3) } "
        "if (match($0, /snr=[-+0-9.eE]+/)) "
        "{ sn = substr($0, RSTART + 4, RLENGTH - 4) } "
        "if (match($0, /std=[-+0-9.eE]+/)) "
        "{ st = substr($0, RSTART + 4, RLENGTH - 4) } "
        "if (ep != \"\" && lo != \"\") { "
        "if (lo != \"\") { sum_l[ep] += lo; n_l[ep]++ } "
        "if (be != \"\") { sum_b[ep] += be; n_b[ep]++ } "
        "if (mu != \"\") { sum_m[ep] += mu; n_m[ep]++ } "
        "if (sn != \"\") { sum_s[ep] += sn; n_s[ep]++ } "
        "if (st != \"\") { sum_t[ep] += st; n_t[ep]++ } } } "
        "END { for (e in n_l) print \"RAW\", d, e, "
        "(n_l[e]?sum_l[e]/n_l[e]:\"-\"), (n_b[e]?sum_b[e]/n_b[e]:\"-\"), "
        "(n_m[e]?sum_m[e]/n_m[e]:\"-\"), (n_s[e]?sum_s[e]/n_s[e]:\"-\"), "
        "(n_t[e]?sum_t[e]/n_t[e]:\"-\") }' | sort -n -k3; "
        'fi; fi; done'
    )
    return ["sh", "-c", script, "_", work_dir]


_EPOCH_RE = re.compile(r"epoch\D*(\d+)", re.I)
# Only = : and spaces may sit between "loss" and the value -- \\D would eat a
# leading minus sign and flip the number's sign.
_LOSSVAL_RE = re.compile(r"loss[ =:]{0,3}(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", re.I)


def _parse_loss_line(rest):
    m = _EPOCH_RE.search(rest)
    v = _LOSSVAL_RE.search(rest)
    if m and v:
        return int(m.group(1)), float(v.group(1))
    parts = rest.split()
    if len(parts) == 2 and parts[0].isdigit():
        try:
            return int(parts[0]), float(parts[1])
        except ValueError:
            return None, None
    return None, None


def parse_training(text):
    """RUN/COUNT/RAW/PARAM/MTIME/LOG lines -> run dicts.

    points carry {epoch, loss} and, where the trainer printed them, beta, mu,
    snr and std. params holds the trainer's echoed header (epochs, batch size,
    learning rate, zdim, output, inputs); mtimes maps checkpoint epoch ->
    unix time, which the UI turns into an ETA.
    """
    runs, order = {}, []
    for line in text.splitlines():
        if line.startswith("RUN "):
            d = line[4:].strip()
            if d and d not in runs:
                runs[d] = {"dir": d, "weights": 0, "points": []}
                order.append(d)
        elif line.startswith("COUNT "):
            _, d, n = line.split(" ", 2)
            if d in runs and n.strip().isdigit():
                runs[d]["weights"] = int(n)
        elif line.startswith("PARAM "):
            _, d, rest = line.split(" ", 2)
            if d in runs:
                key, _, val = rest.partition(":")
                if key.strip():
                    runs[d].setdefault("params", {})[key.strip()] = val.strip()
        elif line.startswith("MTIME "):
            parts = line.split(" ")
            if (len(parts) == 4 and parts[1] in runs
                    and parts[2].isdigit() and parts[3].isdigit()):
                runs[parts[1]].setdefault("mtimes", {})[int(parts[2])] = int(parts[3])
        elif line.startswith("LOG "):
            _, d, path = line.split(" ", 2)
            if d in runs:
                runs[d]["log"] = path.strip()
        elif line.startswith("RAW "):
            _, d, rest = line.split(" ", 2)
            if d not in runs:
                continue
            pt = _parse_point(rest)
            if pt is not None:
                runs[d]["points"].append(pt)
    return [runs[d] for d in order]


def _parse_point(rest):
    """New wire format first (epoch loss [beta mu snr std], '-' = absent);
    the regex fallback keeps loss.txt rows and any older log shape alive."""
    t = rest.split()
    if t and t[0].isdigit() and len(t) >= 2:
        try:
            pt = {"epoch": int(t[0]), "loss": float(t[1])}
        except ValueError:
            return None
        for name, v in zip(("beta", "mu", "snr", "std"), t[2:6]):
            if v not in ("", "-"):
                try:
                    pt[name] = float(v)
                except ValueError:
                    pass
        return pt
    epoch, loss = _parse_loss_line(rest)
    if epoch is None:
        return None
    return {"epoch": epoch, "loss": loss}


# ---------------------------------------------------------------------------
# Tomogram reconstruction headers: MRC dims + voxel size read straight from
# the first 52 bytes of every reconstruction. This automates the Phase-5
# sanity check opus-et-warp documents -- AreTomo and WARP dims must agree,
# and every series must agree with every other -- instead of trusting that
# someone ran headerPyTom on two files once.
# ---------------------------------------------------------------------------

def recon_cmd(work_dir):
    # WARP names reconstructions <TS>_<angpix>Apx.mrc -- the pixel size sits
    # before "Apx", so the glob is *Apx.mrc and the name strip peels the
    # shortest _*Apx.mrc suffix.
    script = (
        'cd "$1" || exit 0; '
        'for f in warp_tiltseries/reconstruction/*Apx.mrc; do '
        '[ -e "$f" ] || continue; '
        'b=${f##*/}; ts=${b%_*Apx.mrc}; '
        'dim=$(dd if="$f" bs=4 skip=0 count=3 2>/dev/null | od -An -t d4 '
        '| tr -s " \\n" " "); '
        'cell=$(dd if="$f" bs=4 skip=10 count=3 2>/dev/null | od -An -t f4 '
        '| tr -s " \\n" " "); '
        'echo "RECON $ts $dim $cell"; done'
    )
    return ["sh", "-c", script, "_", work_dir]


def parse_recon(text):
    """RECON lines -> [{name, nx, ny, nz, voxel_a, dims_consistent}].

    MRC ``cella`` is the cell EDGE in angstrom, not the voxel: voxel = cella
    divided by the header's own grid count, which is the number headerPyTom's
    ``spacing`` prints.
    """
    out = []
    for line in text.splitlines():
        if not line.startswith("RECON "):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        try:
            nx, ny, nz = int(parts[2]), int(parts[3]), int(parts[4])
            cella_x = float(parts[5])
            if nx <= 0 or cella_x <= 0:
                continue
            out.append({"name": parts[1], "nx": nx, "ny": ny, "nz": nz,
                        "voxel_a": round(cella_x / nx, 3)})
        except ValueError:
            continue
    dims = {(r["nx"], r["ny"], r["nz"]) for r in out}
    for r in out:
        r["dims_consistent"] = len(dims) == 1
    return out


# ---------------------------------------------------------------------------
# Runs index: sibling run directories (those holding a .opus_run_state.json)
# with a phase rollup, so one-run-per-instance dashboards stay findable.
# ---------------------------------------------------------------------------

def runs_cmd(parent):
    script = (
        'cd "$1" || exit 0; '
        'for f in */.opus_run_state.json; do [ -e "$f" ] || continue; '
        'd=${f%/*}; echo "RUNJSON $d"; cat "$f"; echo; echo "ENDRUN"; done'
    )
    return ["sh", "-c", script, "_", parent]


def parse_runs(text):
    """RUNJSON/ENDRUN blocks -> [{dir, done, total, waiting}]."""
    out, cur, buf = [], None, []

    def flush():
        if cur is None:
            return
        try:
            rs = json.loads("\n".join(buf))
        except ValueError:
            rs = {}
        phases = rs.get("phases") or {}
        waiting = sorted(
            (str(p) for p, e in phases.items() if (e or {}).get("status") == "checkpoint"),
            key=lambda p: (int(re.sub(r"\D.*", "", p) or 0), p),
        )
        done = sum(1 for e in phases.values() if (e or {}).get("status") == "done")
        out.append({"dir": cur, "done": done, "total": len(phases),
                    "waiting": waiting})

    for line in text.splitlines():
        if line.startswith("RUNJSON "):
            flush()
            cur, buf = line[8:].strip(), []
        elif line.strip() == "ENDRUN":
            flush()
            cur, buf = None, []
        elif cur is not None:
            buf.append(line)
    flush()
    return out
