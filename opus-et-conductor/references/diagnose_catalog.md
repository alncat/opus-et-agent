# Self-Correction Catalog (seed — full loop is M2)

Parse `logs/*.err`. Auto-fix only known-and-safe; else escalate with the diagnosis.

| Symptom in log | Diagnosis | Fix |
|----------------|-----------|-----|
| `GLIBCXX_… not found` | missing lib path | set `LD_LIBRARY_PATH=${CONDA_LIB}:${WARP_DIR}`, resubmit |
| `OutOfMemory` / OOM killer | mem/perdevice too high | `--mem`≥256G, `--perdevice 1`, resubmit |
| CTF `range_max > Nyquist` | range exceeds 2×angpix | reduce `--range_max` to ≥2×ANGPIX |
| AreTomo vs WARP dims mismatch | Phase 3.5 skipped | re-run Phase 3.5 then Phase 4 |
| AreTomo rejects tilt angles | sign/order convention | `awk '{print -$1}'` negation |
| SLURM "invalid partition" | wrong partition name | ESCALATE (never guess) |
| `AssertionError: Images must be cubic` (OPUS-ET train) | training-mask template was a `*_ctf_*` 3D-CTF volume (half-Fourier → non-cubic D×D×D/2+1); NOT a float16 mis-read — `dsdsh create_mask` reads float16 fine | mask-template `find` must exclude `*_ctf_*` and `*_average*` (fixed in `train_opuset.slurm`) |
| Export upsamples / `OUTPUT_ANGPIX < ANGPIX` | exporting finer than the raw acquisition (invalid) | set `OUTPUT_ANGPIX ≥ raw ANGPIX`; box = `DIAMETER/0.75/OUTPUT_ANGPIX`, even |
| Asymmetric-tilt tomograms pick poorly | missing-wedge `Angle1`/`Angle2` reversed by an ordering swap | `gen_tm_jobs`: `Angle1=90-|WARP max|`, `Angle2=90-|WARP min|`, NO swap (WARP tilt = −IMOD; PyTOM angles are directional). See memory `pytom-warp-wedge-convention` |
| OPUS-ET map soft / low-res, **no error (silent)** | training `--angpix` was the raw tilt-series `ANGPIX` (3.37), not the subtomo `OUTPUT_ANGPIX` (4.2). OPUS-ET computes the **CTF** from `--angpix`, so a ~25% wrong pixel size mis-scales every CTF zero → corrupted reconstruction. Bypassed the STAR auto-detect because `pipeline.conf` sets `ANGPIX` (non-empty) | training scripts set `ANGPIX="${OUTPUT_ANGPIX:-}"` (subtomo pixel size = STAR `rlnDetectorPixelSize`); regenerate the pose pkl `--Apix` at the same value; delete any stale pose pkl first. See memory `opus-et-angpix-ctf` |
| Fixed-mode torchrun: **all ranks exit 1, `.out` shows only "FAILED (exit code 1)" with no traceback** | stale pose pkl. `train_opuset_fixed.slurm` built the pose pkl only `if [ ! -f ]`, so re-running for a NEW selection at the same `TM_LABEL` path silently reused the PREVIOUS selection's pkl (different particle count) → `PoseTracker.load` asserts `Input rotations have shape (Nold,3,3) but expected (Nnew,3,3)`. That traceback goes to the `.err` **HEAD**, not stdout | **Read the `.err` HEAD first** on any "all ranks exit 1, no stdout traceback". Fix (in script): regenerate the pose pkl AND drop `deep_split.pkl` whenever the star is newer than them |
| `RuntimeError: std::bad_alloc` in pytom `extractCandidates` (even with `--mem=64G`; on login AND compute) | missing `ulimit -v unlimited` in a hand-written slurm — inherited `RLIMIT_AS` blocks allocating the score/mask volumes (e.g. 960×928×460). Dims were fine (auto-margin=size, positive), so it's RLIMIT, not negative dims | add `ulimit -v unlimited` to any custom slurm running `extractCandidates` / allocating tomogram-sized volumes (canonical train/select scripts already do) |
| `sacct` returns nothing / job looks "vanished" | accounting is **not configured on this cluster** — every `sacct` query is silently empty | use `squeue -j <ids>` for running/pending state and the job LOGS (`logs/*_<jobid>.out` / `.err`) for completed results + exit status; never rely on `sacct` here |

Source of truth: `opus-et-warp/references/gotchas.md` + SKILL.md "Critical Issues".
