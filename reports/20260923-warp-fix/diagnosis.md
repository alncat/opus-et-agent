# WARP sampling diagnosis and stock-script repair — 2026-09-23

Remote project: `super:/work/home/nifengyun/work/cmc/20260907/`.
Inspected and patched the stock pipeline scripts; no processing job was submitted,
restarted, or cancelled. Existing settings, reconstructions, particle exports,
training configuration and training results were not edited.

## Verified diagnosis

- Frame-series settings: `PixelSize=1.1845`, `BinTimes=1`.
- Original tilt-series settings: `PixelSize=2.369`, `BinTimes=0`.
- Rebuilt v2 settings: `PixelSize=1.1845`, `BinTimes=1`, raw dimensions
  `11520x8184x2000`. The reconstruction directory contains 42 MRC volumes.
- Tomo22 XML records 300 kV in the original tree and 200 kV in v2.
- The rebuilt ribo STAR contains 21,000 particle rows, all with 200 kV voltage.
  The startup log records 5 Å sampling and the v2 STAR/export directory.
- Tomo44 has a single tilt (45 degrees) in both trees. The original handedness
  log reports `Math.Sign` rejecting NaN while processing this series, then the
  stock shell script assumes flip. This is a failed check, not positive evidence
  for a flip.
- Installed source `WarpTools/Commands/Tiltseries/DefocusHandTiltseries.cs`
  reads individual tilt movies' `GridCTFDefocus`; it requires local frame CTF,
  not a preceding `ts_ctf` run. Therefore moving the check after tilt-series CTF
  is not the appropriate general fix. The patched script retains check → set →
  tilt-series CTF and stops on failed or inconclusive checks.

The reported volume correlations and centring statistics were supplied in the
handoff; they were not recomputed in this task. They do not by themselves
establish particle purity or the absolute hand of a final map.

## Installed changes

Local source directory: `/Users/zhenweiluo/work/tomo/opus-et-warp/`.
Remote destination: project `pipeline/scripts/`.

1. `warp_tiltseries_setup.slurm` reads raw pixel size and bin exponent from the
   actual frame settings, passes both to WARP, and scales auto-detected average
   dimensions back to raw pixels. Incompatible existing settings are rejected.
2. `warp_update_tomo_dims.slurm` uses `ALIGN_ANGPIX / raw_pixel_size` to convert
   dimensions. It edits only X/Y/Z, preserves all other settings, and saves a
   unique backup. It cannot silently reset binning or processing paths.
3. `warp_ts_ctf.slurm` checks sampling, requires explicit `CTF_VOLTAGE`, passes
   voltage and Cs, and stops on failed, NaN, or ambiguous hand checks. Optional
   `CTF_HAND=keep` preserves an intentional choice; `flip` and `noflip` are
   explicit assignments. Default `auto` never guesses after an error.
4. `warp_settings.py` provides the shared XML validation and dimension update.

The remote project already declares `CTF_VOLTAGE=200` and `CTF_CS=2.7`; its
configuration was preserved. Local example configuration and gotchas documentation
were updated. Remote scripts were patched from their own originals to retain
project-specific differences, rather than replaced wholesale by local versions.

Original remote scripts and SHA-256 manifest are backed up at:
`/work/home/nifengyun/work/cmc/20260907/pipeline/script_backups/warp_sampling_fix_20260923_150117/`.

The isolated historical `warp_rebuild_v2.slurm` was not changed or rerun. Its
recorded commands remain provenance, not the corrected stock workflow. Existing
legacy trees still contain the original sampling error; patched scripts reject
that mismatch rather than relabel old data. Other species were not re-exported.

## Validation

- 111 local checks passed: geometry/orchestration regressions and script contracts.
- 22 regressions passed against the exact staged remote script versions.
- Bash syntax checks and `git diff --check` passed.
- Installation checked original hashes before writing, backed up originals, and
  verified installed SHA-256 hashes (see `manifest.json` and `remote.patch`).
- The deployed helper ran successfully in the remote `warp_build` Python
  environment, accepting v2 sampling and rejecting the old sampling.
- Dimension updating succeeded on a temporary copy of the actual v2 XML only;
  real project settings were not modified.
- Tests mock WarpTools. No new GPU reconstruction or CTF fit was run as part of
  script validation.

## Training snapshot

At approximately 15:02 China time, job **186317** was RUNNING on `g01n01`.
The startup log identifies the code entry point as
`/work/home/nifengyun/soft/test/opusTomo/cryodrgn/commands/train_tomo_dist.py`,
using the v2 ribo STAR, four GPUs, 40 requested epochs, and output
`opuset/ribo_v2/z8`. This verifies the startup path, not an immutable source hash.

Latest completed validation: epoch **18** at 15:00:44; validation gen_loss
`-0.00118198`, SNR2 `0.039747`. Matching `weights.17.pkl` and `z.17.pkl`
exist (zero-based checkpoint index). Inspected stderr contained warnings but
no traceback. Training progress and finite losses do not establish successful
ribosome recovery; pick quality and absolute handedness remain unverified here.

## Follow-up review and repair (16:29 China time)

The review was correct about the three phase scripts drifting after tilt-series
settings changed to raw sampling. `ts_stack`, `ts_import_alignments`, and
`ts_reconstruct` now use the same configured `ALIGN_ANGPIX`. Setup verifies that
this value is a whole-number multiple of the frame-series working pixel size.
For a new configuration `ANGPIX` means raw detector sampling; the historical
20260907 configuration retains its working-pixel value and derives 4.738 Å.
Those conventions are safe because the phase scripts now use only the explicit
alignment target. A blanket `PixelSize × 2^BinTimes × BINNING_FACTOR` formula
would double-bin a raw-pixel configuration.

The AreTomo `_ali.mrc` files inspected from this project have 9.476 Å voxels.
This is twice the nominal 4.738 Å stack/alignment sampling, but
`-OutBin 1` is a multiplier of one and did not cause the doubling. The current
`_ali.mrc` files were modified after the original AreTomo alignment/projection
files; provenance for that later resampling is incomplete. The dimension updater
now checks that voxel headers are consistent across volumes and converts from
the measured voxel size. `validate.sh` checks physical dimensions with one
volume voxel of rounding tolerance. On the rebuilt settings it accepts
11520 × 8184 × 2000 against header-derived 11520 × 8192 × 2000.
`phases.md`, `quickstart.md`, and the validation messages were corrected to
avoid treating voxel counts as necessarily equal across the two volumes.

WARP source confirms `AreAnglesInverted` changes the Z/defocus value but not
XY density placement. The earlier suggestion that `noflip` itself Z-mirrors
the WARP reconstruction was incorrect. Direct AreTomo/WARP correlation checks
the imported alignment and angle-negation geometry; it is not a test of the
CTF hand flag. The shell check now reports how to handle an invalid one-tilt
series and matches `NaN` as a word, avoiding harmless substrings.

The follow-up changed nine remote pipeline files. Originals and checksums are
at `/work/home/nifengyun/work/cmc/20260907/pipeline/script_backups/warp_sampling_followup_20260923_162636/`.
A final validation-message correction was backed up at
`/work/home/nifengyun/work/cmc/20260907/pipeline/script_backups/validate_followup_20260923_162925/`.
The tested remote diff and source/destination hashes are in
`remote_followup.patch` and `followup_manifest.json`. `validate.sh`'s final
message SHA-256 is `3b2387df333d15df12d6c2ee55b791d6b2089c5b0092c8f2f69ed5f75189d837`.

Validation: 117 local tests and 28 tests against staged remote scripts passed;
all changed Bash files passed syntax checks. The deployed helper accepted every
available AreTomo volume header in this project and the rebuilt settings.
These checks did not perform a fresh reconstruction or independent density
correlation.

Training job 186317 subsequently completed all 40 epochs according to its
run log (`Finsihed in 2:04:02.689422` at 16:08:22). Paired
`weights.39.pkl` and `z.39.pkl` exist. The job is no longer in `squeue`;
`sacct` did not return a record, so the scheduler exit code was not confirmed.
The training log ended with normal completion and stderr contained only the
observed PyTorch meshgrid warnings.

## Correction: AreTomo `-OutBin 1` (source and run audit)

Installed AreTomo2 source initializes `m_fOutBin` to 1.0, calculates output
pixel size as `m_fPixelSize * m_fOutBin`, and calculates reconstructed Z as
`m_iVolZ / m_fOutBin`. Its help calls 1 the default. Thus `-OutBin 1` means
**no additional binning**, unlike WARP `--bin 1` (2×).

The original tomo22 AreTomo log records a 2880 × 2046 input stack,
`-VolZ 500`, and `-OutBin 1`. The current input stack is 2880 wide; the
current `_ali.mrc` is 1440 × 1024 × 250. The `_ali.mrc` modification time is
September 11, while the projection and alignment files were written on
September 8. The current 2× reduction was therefore a later modification;
we have not identified its exact command. Both current stack and volume MRC
headers show ~9.476 Å despite different X/Y dimensions, so comparing those
headers alone did not establish the cause. This corrects the earlier attribution
to `-OutBin 1`.

The updated guard accepts a consistent measured voxel size across `_ali.mrc`
files and checks physical dimensions against the raw WARP settings. It does not
infer voxel size from the `-OutBin` flag.

The original tomo22 projection products strengthen this distinction:
`_ali_projXY.mrc` is 2880 × 2046 and `_ali_projXZ.mrc` is 2880 × 500,
matching the original AreTomo input width and requested `-VolZ 500`.
Both projections were written September 8; the current 1440 × 1024 × 250
`_ali.mrc` was modified September 11. The current input stack and reduced
volume both report 9.476 Å in their MRC headers, so the stack header alone
cannot explain the physical scale. The exact Sep 11 transformation remains
unidentified.

The incorrect `2^OutBin` guard was removed from the local and remote
`warp_settings.py`, `warp_update_tomo_dims.slurm`, and `validate.sh`; the
AreTomo script's `OutBin` description was corrected to a multiplier. The
four remote originals and hashes are at
`/work/home/nifengyun/work/cmc/20260907/pipeline/script_backups/aretomo_outbin_correction_20260923_164045/`.
The change is recorded in `outbin_correction.patch` and
`outbin_correction_manifest.json`. All 117 local tests and 28 staged remote
script tests passed. The deployed header check passed on every available
`_ali.mrc` volume against the rebuilt WARP settings.
