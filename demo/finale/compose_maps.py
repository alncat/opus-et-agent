"""Compose the same-scale M-refined maps figure + strip panel from one render
(maps_samescale.png: both maps, one camera, straight-on). Labels auto-align to
the blue (ribosome) and gold (FAS) centroids."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np

# nicer typography (matches the pipeline strip); DejaVu fallback covers the → glyph
plt.rcParams["font.family"] = ["Helvetica Neue", "Helvetica", "DejaVu Sans"]

img = mpimg.imread("maps_samescale.png")          # RGBA, origin upper
# trim transparent margins (tighten the two maps into frame)
_a = img[..., 3] > 0.02
_ys, _xs = np.where(_a)
_m = 20
img = img[max(_ys.min() - _m, 0):_ys.max() + _m, max(_xs.min() - _m, 0):_xs.max() + _m]
H, W = img.shape[:2]
r, g, b, a = (img[..., i] for i in range(4))
op = a > 0.5
ribo = op & (b > r) & (b > 0.4)                   # cornflower blue
fas = op & (r > 0.55) & (g > 0.45) & (b < 0.45)   # gold
xr = np.where(ribo.any(0))[0]
xf = np.where(fas.any(0))[0]
fr = (xr.mean() / W) if xr.size else 0.30         # centroid x-fraction
ff = (xf.mean() / W) if xf.size else 0.72
print(f"ribo x-frac {fr:.2f}  fas x-frac {ff:.2f}")


def draw(ax):
    ax.imshow(img); ax.axis("off")
    for xf_, name, res in [(fr, "Ribosome", "7.76 Å"), (ff, "FAS", "13.88 Å · D3")]:
        ax.text(xf_, -0.02, f"{name}\n{res}", transform=ax.transAxes, ha="center",
                va="top", fontsize=15, color="#c0392b", fontweight="bold", linespacing=1.4)


# full figure with Title-Case heading + caption
fig = plt.figure(figsize=(11.5, 5.6))
ax = fig.add_axes([0.03, 0.24, 0.94, 0.62])
draw(ax)
fig.text(0.5, 0.95, "M Refinement — Joint Multi-Particle, One Population",
         ha="center", fontsize=18, fontweight="bold")
fig.text(0.5, 0.05,
         "Refined together in one M population, at the same scale — "
         "FAS 25.6 → 13.88 Å, ribosome ~7.8 Å (plateau).",
         ha="center", fontsize=11, color="#333")
fig.savefig("m_refined_maps.png", dpi=150, facecolor="white")
print("wrote m_refined_maps.png")

# strip panel — maps + small labels, no title
figp = plt.figure(figsize=(6.6, 3.6))
axp = figp.add_axes([0.01, 0.12, 0.98, 0.86])
axp.imshow(img); axp.axis("off")
for xf_, txt in [(fr, "ribosome  7.76 Å"), (ff, "FAS  13.88 Å · D3")]:
    axp.text(xf_, -0.02, txt, transform=axp.transAxes, ha="center", va="top",
             fontsize=12, color="#c0392b", fontweight="bold")
figp.savefig("../demo/qc/pipeline_panels/panel_maps.png", dpi=150, facecolor="white")
print("wrote panel_maps.png")
