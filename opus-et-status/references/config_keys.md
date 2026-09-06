# Editable config keys

*Generated from `ALLOWLIST` in `scripts/config_edit.py` — the source of truth.*

Each key declares its file (**species** = the conf chosen with `--species-conf`;
**pipeline** = `pipeline.conf`, shared by every species) and the stage it governs.

**No path-valued keys are editable.** Neither are acquisition facts (`ANGPIX`,
`EXPOSURE`, `MAP_ANGPIX`, `TOMO_DIM_*`) nor namespace keys (`TM_LABEL`,
`OUTPUT_DIR`) whose change would orphan every existing output.

## Processing (pipeline conf)

| Key | Type | Constraint | Notes |
|---|---|---|---|
| `BINNING_FACTOR` | int | >= 1, <= 16 | Tilt-series binning for alignment/reconstruction. **⚠ Load-bearing: ALIGN_ANGPIX derives from it, the TM template and box sizes are built at this binning, and every existing tomogram was reconstructed with it. Changing it mid-run makes prior reconstructions inconsistent with new ones.** |
| `CTF_DEFOCUS_MAX` | float | > 0, <= 50 | CTF search max defocus (um). |
| `CTF_RANGE_MAX` | float | > 0, <= 50 | CTF fit resolution range max. |
| `CTF_WINDOW` | int | >= 64, <= 2048, multiple of 8 | CTF estimation window (px). |
| `CTF_GRID` | grid | `NxNxN`, e.g. `2x2x1` | CTF grid, e.g. 2x2x1. |
| `MOTION_GRID` | grid | `NxNxN`, e.g. `2x2x1` | Motion grid, e.g. 1x1x3. |
| `MIN_INTENSITY` | float | >= 0, <= 1 | Minimum tilt intensity to keep. |

## Template matching (species conf)

| Key | Type | Constraint | Notes |
|---|---|---|---|
| `DIAMETER` | float | >= 50, <= 2000 | Particle diameter in Angstrom. **⚠ TM_BOX_SIZE and SUBTOMO_BOX_SIZE are sized from DIAMETER (~ DIAMETER/0.75/pixel size). Changing it without updating both boxes leaves them inconsistent.** |
| `TM_BOX_SIZE` | int | >= 16, <= 256, even | TM template/mask box at ALIGN_ANGPIX; ~ round_to_even(DIAMETER / 0.75 / ALIGN_ANGPIX). **⚠ Should be a multiple of 8, preferably 16, for FFT efficiency -- but the sizing rule is round_to_even(DIAMETER / 0.75 / pixel size), so a multiple of 8 is a preference, not a hard rule.** |
| `MASK_RADIUS` | int | >= 1, <= 128 | TM sphere mask radius in pixels; MASK_RADIUS + MASK_SIGMA must be < TM_BOX_SIZE/2. |
| `MASK_SIGMA` | float | >= 0, <= 5 | TM mask soft-edge sigma in px; keep at 1 unless a softer edge is wanted. |
| `TEMPLATE_INVERT` | enum | one of 0 / 1 | Invert template contrast. |
| `TEMPLATE_MIRROR` | enum | one of 0 / 1 | Mirror the template (handedness). |
| `DEFAULT_Z_START` | int | >= 0, <= 2000 | Z index where the TM search region starts; must lie inside the tomogram. |
| `TM_SPLIT_X` | int | >= 1, <= 8 | TM job split along X. |
| `TM_SPLIT_Y` | int | >= 1, <= 8 | TM job split along Y. |
| `TM_SPLIT_Z` | int | >= 1, <= 8 | TM job split along Z. |

## Extraction (species conf)

| Key | Type | Constraint | Notes |
|---|---|---|---|
| `NUM_CANDIDATES` | int | >= 100, <= 20000 | Peaks per tomogram; 5000-10000 for crowded specimens, lower for sparse. |
| `EXTRACT_MASK_RADIUS` | int | >= 1, <= 128 | Non-max-suppression radius in pixels. **⚠ This is the non-max-suppression radius and is intentionally LARGER than the sphere MASK_RADIUS. Do not derive it from the mask formula: too small gives duplicate hits, too large drops adjacent particles.** |
| `MIN_SCORE` | float | >= -1, <= 1 | TM score cutoff (cross-correlation). |
| `MAX_TS` | int | >= 0, <= 10000 | Limit tilt series (0 = all). |

## Export (species conf)

| Key | Type | Constraint | Notes |
|---|---|---|---|
| `OUTPUT_ANGPIX` | float | > 0, <= 1000 | Exported subtomogram pixel size; must be >= raw ANGPIX. |
| `SUBTOMO_BOX_SIZE` | int | >= 32, <= 1024, even | Subtomogram box at OUTPUT_ANGPIX; ~ round_to_even(DIAMETER / 0.75 / OUTPUT_ANGPIX). **⚠ Should be a multiple of 8, preferably 16, for FFT efficiency -- but the sizing rule is round_to_even(DIAMETER / 0.75 / pixel size), so a multiple of 8 is a preference, not a hard rule.** |

## Training (species conf)

| Key | Type | Constraint | Notes |
|---|---|---|---|
| `TEMPLATERES` | int | >= 32, <= 512 | Decoder output size; ~ SUBTOMO_BOX_SIZE x DOWNSAMPLE_FRAC / 0.75. |
| `ZDIM` | int | >= 1, <= 16 | Latent dimension; 8 default, under 16. Raise for more heterogeneous datasets. |
| `ZAFFINEDIM` | int | >= 1, <= 16 | Affine/pose latent dimension; 6 typical. Same range as ZDIM. |
| `NUM_EPOCHS` | int | >= 20, <= 80 | Training epochs; 40 typical, useful range 20-80. Each epoch is expensive -- 40 ran for days on 4 GPUs. |
| `BATCH_SIZE` | int | >= 1, <= 512 | Batch size; GPU-memory bound. **⚠ Bounded by GPU memory, not by a fixed limit: what fits depends on SUBTOMO_BOX_SIZE, TEMPLATERES and the card. 12 fits a 104^3 box on 4 GPUs; too large simply OOMs at startup.** |
| `LEARNING_RATE` | float | >= 1e-06, <= 0.0005 | Adam learning rate; order of 1e-5. **⚠ Belongs in the 1e-5 range (4e-5 here). Orders of magnitude larger will not converge, and training fails silently -- it burns days of GPU and returns a useless model.** |
| `DOWNSAMPLE_FRAC` | float | > 0, <= 1 | Encoder downsample fraction (0 < x <= 1). |
| `BETA_CONTROL` | float | >= 0, <= 4 | KL beta control weight; 0.5 typical, should not exceed 4. |
| `LAMB` | float | >= 0, <= 2 | Regularisation weight; 0.5 typical, should stay under 2. |
| `BFACTOR` | float | >= 0, <= 10 | B-factor applied during training; 3.75 typical, under 10. |
| `VAL_FRAC` | float | >= 0, <= 0.5 | Fraction held out for validation; 0.1 typical. |

## Training mask (species conf)

| Key | Type | Constraint | Notes |
|---|---|---|---|
| `MASK_RADIUS_FRACTION` | float | > 0, <= 1 | Sphere radius as a fraction of min box dim / 2. |
| `MASK_SOFT_EDGE` | int | >= 0, <= 64 | Training mask soft edge in voxels. |

## Cross-key rules

- `OUTPUT_ANGPIX` >= `ANGPIX` — a finer value is invalid upsampling.
- `MASK_RADIUS + MASK_SIGMA` < `TM_BOX_SIZE`/2 — the soft edge must fit in the box.
- `EXTRACT_MASK_RADIUS` > `MASK_RADIUS` — it is the non-max-suppression radius.
- `DEFAULT_Z_START` < `TOMO_DIM_Z` / `BINNING_FACTOR` — it is a Z index in the
  binned reconstruction, so the tomogram's own depth is the bound.
- `TM_BOX_SIZE` x `ALIGN_ANGPIX` >= `DIAMETER`, and `SUBTOMO_BOX_SIZE` x
  `OUTPUT_ANGPIX` >= `DIAMETER` — a box must physically span the particle, the
  same rule `validate.sh` enforces. Editing `DIAMETER` re-checks both boxes, so
  raising it cannot silently leave them too small.

## Where these constraints come from

**Documented rules.** Box sizes follow `round_to_even(DIAMETER / 0.75 / pixel
size)`. Only the part that is physically hard — the box must span the particle —
is enforced, as a cross-key check that reports the recommended size; the 75%
occupancy target is a recommendation, and high-res or strong-CTF cases
legitimately use up to `DIAMETER / 0.5 / pixel size`. *Even* is enforced and
*multiple of 8 (preferably 16)* is a warning —
it is a SHOULD for FFT efficiency, and enforcing it would reject a legitimate
box such as 44. `MASK_SIGMA` stays near 1, `NUM_CANDIDATES` in the 5000–10000
band for crowded specimens, `TEMPLATERES` carries no evenness rule because none
is documented, and `MIN_SCORE` admits negatives because it is a
cross-correlation.

**Training ranges** come from practice, not a spec: learning rate in the 1e-5
order, epochs 20–80, `BETA_CONTROL` ≤ 4, `LAMB` < 2, `BFACTOR` < 10,
`ZDIM`/`ZAFFINEDIM` ≤ 16 (bound to shared constants). A wrong training value
does not fail loudly — it burns days of GPU and returns a useless model.

**Template-matching ceilings are judgement calls**, sized from the physics:
`DIAMETER` 50–1500 Å: the largest things routinely picked are the nuclear pore
(~1200 Å) and big viral capsids (~1300 Å), most targets being 200–600 Å;
TM runs on *binned* tomograms so `TM_BOX_SIZE` stays in the tens-to-low-hundreds; `MASK_RADIUS` ≤ 128 is
simply the largest that could satisfy the cross-key rule. `DEFAULT_Z_START` is
not given a meaningful static ceiling at all — its bound is the tomogram depth,
so it is checked against `TOMO_DIM_Z / BINNING_FACTOR` (500 slices here) and the
static value is only a fallback for when those are unset.

`BATCH_SIZE` is deliberately **not** tightly capped: it is bounded by GPU memory,
which depends on `SUBTOMO_BOX_SIZE`, `TEMPLATERES` and the card, so a fixed
ceiling would be a fiction. It warns instead, and fails loudly by OOMing.

A parametrized test asserts every value from a real run's confs is accepted, so
a constraint cannot silently reject reality. If a bound is too tight for real
work, widen it here rather than working around it.

## Derived guidance in the form

For any key with a documented formula the form shows the value that rule
gives for the *current* config, not just a numeric range — a static
`32 – 1024` says nothing about what to type, whereas
`~102 (up to ~152 for high-res), from 320 Å / 0.75 / OUTPUT_ANGPIX 4.2` does.
Computed by `recommend()` and returned as `recommended` from `/api/config`;
the raw range is still shown underneath, smaller.

## Rules applied to every write

Parse to the declared type -> regenerate the line, preserving its inline comment,
quoting style and integer-vs-decimal spelling -> timestamped backup -> write ->
run `validate.sh` -> roll back on failure. An edit while any job is
`RUNNING`/`PENDING` returns 409 unless `confirm_running` is set.
