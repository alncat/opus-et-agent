#!/usr/bin/env python3
"""Deterministic sphere-mask sizing for PyTOM template matching.

Measures a template's actual radial density and recommends a MASK_RADIUS /
MASK_SIGMA that encloses most of the density, replacing the nominal-diameter
guess in gen_sphere_mask.slurm. The recommendation is pixel-native
(create_mask.py works in pixels); pass angpix only to add a human-facing
radius-in-Angstrom diagnostic.

Pure numpy + mrcfile. matplotlib is imported lazily (only for --profile-png), so
this module imports and runs its core recommendation even where matplotlib is absent.
"""
import argparse
import json

import numpy as np

COM_WARN_PX = 3.0
NOISE_K = 3.0  # density mass counts only voxels above background + NOISE_K * noise_sigma


def load_volume(path):
    import mrcfile
    with mrcfile.open(path, permissive=True) as m:
        return np.asarray(m.data, dtype=np.float32)


def _radius_grid(shape, center=None):
    nz, ny, nx = shape
    if center is None:
        center = ((nz - 1) / 2.0, (ny - 1) / 2.0, (nx - 1) / 2.0)
    zz, yy, xx = np.ogrid[:nz, :ny, :nx]
    return np.sqrt((zz - center[0]) ** 2 + (yy - center[1]) ** 2 + (xx - center[2]) ** 2)


def _positive_mass(vol, background, noise_floor=0.0):
    """Background-subtracted density, oriented positive, clipped at 0.

    Voxels whose oriented signal is below `noise_floor` are dropped, so a diffuse
    low-value noise pedestal (which dominates the large outer shells by voxel count)
    is not counted as density.
    """
    signal = np.asarray(vol, dtype=np.float64) - background
    if np.maximum(signal, 0.0).sum() < np.maximum(-signal, 0.0).sum():
        signal = -signal
    mass = np.maximum(signal, 0.0)
    if noise_floor > 0.0:
        mass[signal < noise_floor] = 0.0
    return mass


def _background_noise(vol, shell_frac=0.9):
    """(median, std) of the outer spherical shell — the solvent background and its noise level."""
    vol = np.asarray(vol, dtype=np.float64)
    r = _radius_grid(vol.shape)
    half = min(vol.shape) / 2.0
    shell = r >= shell_frac * half
    if not shell.any():
        return float(np.median(vol)), float(np.std(vol))
    region = vol[shell]
    return float(np.median(region)), float(np.std(region))


def estimate_background(vol, shell_frac=0.9):
    """Median of the outer spherical shell (voxels at radius >= shell_frac * box half-width)."""
    return _background_noise(vol, shell_frac)[0]


def radial_mass_profile(vol, background=None, center=None, noise_k=NOISE_K):
    """Cumulative fraction of above-noise density mass vs. integer radius from the center."""
    vol = np.asarray(vol, dtype=np.float64)
    bg, sigma = _background_noise(vol)
    if background is None:
        background = bg
    mass = _positive_mass(vol, background, noise_floor=noise_k * sigma)
    r = _radius_grid(vol.shape, center=center)
    rint = np.floor(r).astype(int)
    shell_mass = np.bincount(rint.ravel(), weights=mass.ravel())
    # Label each cumulative bin by its UPPER edge: cumsum[k] is the mass within radius
    # < k+1, i.e. the fraction enclosed by a sphere of radius (k+1). Using the lower edge
    # would bias the enclosing radius ~1 px low for sharp-edged particles.
    radii = np.arange(1, len(shell_mass) + 1, dtype=float)
    total = shell_mass.sum()
    if total <= 0:
        return radii, np.zeros_like(shell_mass)
    return radii, np.cumsum(shell_mass) / total


def enclosing_radius(radii, cum_fraction, target):
    """Smallest radius at which the cumulative mass fraction first reaches `target`.

    Uses searchsorted (not np.interp) so a flat/saturated cum tail — cum == 1.0 for every
    radius past the structure's edge — cannot pull the answer out to the box corner.
    """
    radii = np.asarray(radii, dtype=float)
    cum_fraction = np.asarray(cum_fraction, dtype=float)
    idx = int(np.searchsorted(cum_fraction, target, side="left"))
    if idx <= 0:
        return float(radii[0])
    if idx >= len(cum_fraction):
        return float(radii[-1])
    f0, f1 = cum_fraction[idx - 1], cum_fraction[idx]
    r0, r1 = radii[idx - 1], radii[idx]
    if f1 <= f0:
        return float(r1)
    return float(r0 + (target - f0) / (f1 - f0) * (r1 - r0))


def center_of_mass_offset(vol, background=None, noise_k=NOISE_K):
    """Distance (px) from the above-noise mass centroid to the geometric box center."""
    vol = np.asarray(vol, dtype=np.float64)
    bg, sigma = _background_noise(vol)
    if background is None:
        background = bg
    mass = _positive_mass(vol, background, noise_floor=noise_k * sigma)
    total = mass.sum()
    if total <= 0:
        return 0.0
    nz, ny, nx = vol.shape
    zz, yy, xx = np.mgrid[:nz, :ny, :nx]
    cz = float((mass * zz).sum() / total)
    cy = float((mass * yy).sum() / total)
    cx = float((mass * xx).sum() / total)
    gz, gy, gx = (nz - 1) / 2.0, (ny - 1) / 2.0, (nx - 1) / 2.0
    return float(np.sqrt((cz - gz) ** 2 + (cy - gy) ** 2 + (cx - gx) ** 2))


def recommend_mask(vol, angpix=None, target=0.95, soft_frac=0.1, min_sigma=2.0, noise_k=NOISE_K):
    """Recommend MASK_RADIUS / MASK_SIGMA (px) enclosing `target` of the template's density."""
    vol = np.asarray(vol, dtype=np.float64)
    if not (vol.ndim == 3 and vol.shape[0] == vol.shape[1] == vol.shape[2]):
        raise ValueError(f"template must be a cubic 3D volume, got shape {vol.shape}")
    box_dim = int(vol.shape[0])
    box_limit = box_dim / 2.0  # create_mask.py requires radius + sigma < box_dim/2 (strict)

    background = estimate_background(vol)
    radii, cum = radial_mass_profile(vol, background=background, noise_k=noise_k)
    if cum.max() <= 0.0:
        raise ValueError(
            f"template has no density above the noise floor (noise_k={noise_k}); "
            f"check the map contrast or lower --noise-k")

    radius = enclosing_radius(radii, cum, target)
    mask_radius_px = int(round(radius))
    mask_sigma_px = int(max(float(min_sigma), float(round(soft_frac * radius))))

    warnings = []
    # Decide fit on the FINAL integers that create_mask.py will consume, not the floats.
    box_fits = (mask_radius_px + mask_sigma_px) < box_limit
    if not box_fits:
        mask_sigma_px = max(0, int(np.ceil(box_limit - mask_radius_px)) - 1)
        warnings.append(
            f"mask radius({mask_radius_px}) + soft edge exceeds box/2 ({box_limit:.0f}); "
            f"soft edge clamped to {mask_sigma_px} px — regenerate the template with a "
            f"larger box for a proper soft edge."
        )

    com_off = center_of_mass_offset(vol, background=background, noise_k=noise_k)
    if com_off > COM_WARN_PX:
        warnings.append(
            f"template density center-of-mass is {com_off:.1f} px off the box center; "
            f"the sphere mask is box-centered, so re-center the template."
        )

    enclosed = float(np.interp(mask_radius_px, radii, cum))
    result = {
        "mask_radius_px": mask_radius_px,
        "mask_sigma_px": mask_sigma_px,
        "enclosed_fraction": round(enclosed, 4),
        "com_offset_px": round(com_off, 2),
        "box_dim": box_dim,
        "box_fits": bool(box_fits),
        "warnings": warnings,
    }
    if angpix is not None:
        result["mask_radius_angstrom"] = round(mask_radius_px * float(angpix), 1)
    return result


def save_profile_png(radii, cum_fraction, radius, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5, 3.2))
    ax.plot(radii, cum_fraction, "-", color="tab:blue")
    ax.axvline(radius, color="tab:red", ls="--", label=f"radius={radius:.0f}px")
    ax.set_xlabel("radius (px)")
    ax.set_ylabel("cumulative density mass")
    ax.set_ylim(0, 1.02)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Recommend a PyTOM sphere-mask radius/sigma from a template's density.")
    ap.add_argument("template", help="template MRC (cubic box, at ALIGN_ANGPIX)")
    ap.add_argument("--target", type=float, default=0.95,
                    help="density-mass fraction to enclose (default 0.95)")
    ap.add_argument("--soft-frac", type=float, default=0.1,
                    help="soft edge as a fraction of the radius (default 0.1)")
    ap.add_argument("--min-sigma", type=float, default=2.0,
                    help="minimum soft-edge sigma in px (default 2)")
    ap.add_argument("--noise-k", type=float, default=NOISE_K,
                    help="drop voxels below background + noise_k*sigma before measuring density "
                         f"(default {NOISE_K}); 0 disables noise-floor thresholding")
    ap.add_argument("--angpix", type=float, default=None,
                    help="pixel size (A); adds mask_radius_angstrom to the report")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write the recommendation JSON to this path")
    ap.add_argument("--profile-png", default=None,
                    help="write the radial cumulative-mass profile PNG to this path")
    args = ap.parse_args()

    vol = load_volume(args.template)
    rec = recommend_mask(vol, angpix=args.angpix, target=args.target,
                         soft_frac=args.soft_frac, min_sigma=args.min_sigma,
                         noise_k=args.noise_k)
    print(json.dumps(rec, indent=2))
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(rec, f, indent=2)
    if args.profile_png:
        background = estimate_background(vol)
        radii, cum = radial_mass_profile(vol, background=background, noise_k=args.noise_k)
        save_profile_png(radii, cum, rec["mask_radius_px"], args.profile_png)
    return rec


if __name__ == "__main__":
    main()
