# Demo Recording — replay a gate without the heavy compute

Goal: screen-record Claude Code *being* the conductor — reaching a scientific gate,
running its QC, and handing the decision back to the human — **without waiting on the
multi-hour SLURM jobs**. The pipeline's heavy steps (frame CTF, AreTomo, reconstruction,
template matching, OPUS-ET training) already ran; their outputs are on disk. Recording is
just re-driving the *cheap* part: reach a gate → run the fast QC on the existing outputs →
present the checkpoint → capture the human's answer.

## Why this works
The per-phase loop already **skips submission when a phase's outputs exist** (validate.sh
reports it done → advance, no `sbatch`). So a run whose outputs are complete "fast-forwards"
to the next unapproved gate and stops there to ask. The QC itself is seconds–minutes
(slice preview, picks overlay, state consistency/gallery), not hours.

## Record in a FRESH session (required, on the same machine)
Do the recording in a **new Claude Code session**, not a continuation of a working session:
- It **demonstrates the core design** — that pipeline state lives in `.opus_run_state.json`
  + files on disk, not in conversation. A cold-start session that reads the state and
  fast-forwards to a gate proves this on camera; a continued session would be contaminated
  by prior context and wouldn't genuinely re-derive from disk.
- Clean transcript on screen, and repeatable re-takes (reset the snapshot → new session).
- Same machine/environment (needs cluster SSH + the run-state file); the working session
  stays untouched — state and outputs persist on disk.

**Timing:** **all gates are recordable now.** The corrected + **expanded (58k pick-more) run**
is complete, Gate-3 re-selection (k17/18/19) + Gate-4 fixed-mode/FSC are final, and the joint
two-species M refinement has **converged** (FAS 13.88 Å, ribosome 7.76 Å) with the in-cell
ArtiaX finale + M-refined map showcase rendered (`demo/qc/finale/`). So the recorded states and
numbers now match the final data for every gate. Narrative/scene order:
[`demo/video_script.md`](../../demo/video_script.md) (the ≤3:00 gate-driven script + shot list).

**Beats beyond the live gates** — the video's non-gate beats are the opening **hand-off**
(Scene 0, a live terminal capture) and the **results / finale b-roll** (Scene 5): the M-refined
maps and the in-cell molecular-sociology render, cut in under narration. See
[`demo/video_script.md`](../../demo/video_script.md) for the full gate-driven shot list.

## Replay mode — the one behavioral change (guardrail)
When recording, run the conductor in **replay mode**. Set `demo_replay: true` in
`.opus_run_state.json` (or just tell the conductor "replay mode — recording"). In replay mode:

1. **NEVER `sbatch`.** Every upstream phase's outputs must already exist. If an output is
   missing, **STOP** with a clear message ("replay: phase N outputs missing; cannot record
   without them") — do NOT submit. This is the guardrail: a live recording must never kick
   off a 6-hour job.
2. Derive state from disk (validate.sh), fast-forward to the target gate, run **only** the
   gate's QC on the existing outputs, and present the checkpoint exactly as in a real run.
3. Everything the audience sees is genuine — real state load, real QC on real data, a real
   question. Only the heavy SLURM steps are short-circuited (their outputs pre-exist).

## Staging a gate (so the conductor stops there)
1. Confirm the target gate's upstream outputs exist on disk (they do for Gates 1–4).
2. Make the gate **unapproved** so the conductor re-opens it: in `.opus_run_state.json`
   clear that gate's approval (e.g. `gates.gate1.status` back to `pending`/unset
   `approved`). Keep a per-gate snapshot of the state file so you can reset and re-take:
   `cp .opus_run_state.json .demo_snapshots/gate1.json` (and restore before each take).
3. Optionally set `demo_replay: true`.

## Record procedure
1. Restore the gate snapshot; start the screen recorder.
2. In a fresh Claude Code session, give the conductor the natural human intent
   ("resume the run" / "continue"). It loads state → sees the upstream phases done →
   fast-forwards → reaches the gate → runs the QC → presents "N/N good — keep which?".
3. Answer as the human (approve / choose) on camera.
4. Stop; trim to the beat.

## Recordable now (outputs already on disk)
- **Gate 1 — alignment QC** (hero): central-slice previews + handedness montage exist; the
  parallel per-tomogram QC Workflow (one agent per tomogram) re-fires in seconds.
- **Gate 2 — picks QC**: ribosome + FAS overlays + `tm_eval_agreement` numbers exist.

## Also recordable now (final assets on disk)
- **Gate 3 — state selection** (richest interaction beat): the four-signal assets — template/
  consensus montage, N×N consistency heatmap, ChimeraX state gallery, UMAP — are final from the
  58k expanded run (k17/18/19, 14,797 particles).
- **Gate 4 — resolution sign-off**: gold-standard half-maps → molecule mask (+ overlay QC) →
  phase-randomized FSC (18.26 Å corrected) for the k17/18/19 selection.
- **Gate 5 / finale** (non-terminal b-roll, already rendered — not a live replay). All in
  `demo/qc/finale/` (recipes in `demo/render_commands.md`); cut in as needed:
  - `finale_insitu.mp4` / `finale_insitu_still.png` — two-species in-cell ArtiaX scene (TS_028,
    3,387 ribosomes + 95 FAS), hero aesthetic (tilt + silhouettes, gentle **rock**, not a turntable).
  - `insitu_TS029_cell.png` / `insitu_TS029.mp4` — **cellular-context companion** (TS_029): ribosomes
    excluding a membrane-bound organelle — the strongest "back in the cell" shot.
  - `m_refined_fsc.png` — the gold-standard FSC (phase-randomization corrected) that backs the
    resolutions: ribosome 0.143 at 7.76 Å, FAS at 13.88 Å (`build_m_fsc.py` from the M `_fsc.star`).
  - `m_refined_maps.png` + `m_refined_ribo_spin.mp4` / `m_refined_fas_spin.mp4` — the M-refined maps,
    same-scale still + rotating showcases (ribosome 7.76 Å rRNA helices, FAS 13.88 Å D3 barrel).
  - `fas_raw_gallery.png` / `fas_raw_context.png` / `fas_TS028_scan.mp4` — the **raw-density FAS
    reveal** (`particle_gallery.py`): the picks ring-marked on the untouched reconstruction, the
    honest "is it really there?" beat for the rare species.
  - `pipeline_strip.png` (in `demo/qc/`, not `finale/`) — the whole-arc overview figure.

## Prompts to type (copy-paste per take)

> **Exact paths matter.** The run dir holds multiple ribosome OPUS-ET runs (`z8`,
> `z8_expanded`, `z8_angpix337_OLD`) and two Gate-4 half-map sets (`fixed_subset1/2` = the
> final **k17/18/19** selection, `baseline_k6-8-9-10` = an earlier pick). The Gate-2/3/4
> prompts below name the **exact** dirs so a live take never grabs the wrong run. Also: the
> `.opus_run_state.json` is anchored at Gate 1 (Gates 2–4 were run ad-hoc, not through the
> conductor loop), so for those gates the conductor runs the QC **directly on the named
> outputs** rather than fast-forwarding a fully-recorded state — still genuine QC on real data.

### A. Initialize / hand-off (opening beat — start the run)
A genuine cold kickoff: preflight → config → first jobs. Records the "you hand it off" beat.
To show a *true* cold start, point it at a fresh processing dir (raw frames + MDOCs staged,
nothing computed yet) and cut after the first submission — the preflight + config + submit is
~20–40 s; the heavy compute is not part of the shot. Against the existing dir it will instead
report progress already on disk and head to the first open gate.

```
I have a cryo-ET dataset to process end-to-end — raw tilt-series through to in-cell
molecular maps. It's on the `super` cluster (SSH alias: super); the frames and
acquisition MDOCs are staged under <RUN_DIR>.

Use the OPUS-ET conductor to drive it. Start with preflight — discover the toolchain
(the WARP fork, the conda envs, AreTomo2 / PyTOM / OPUS-ET) and the SLURM
partitions, and flag anything missing. Then set up the run: auto-detect the acquisition
parameters, write and validate pipeline.conf, and begin the reconstruction pipeline
(WARP frame-series CTF → tilt-series setup → AreTomo alignment → CTF + reconstruction).
Track progress in .opus_run_state.json, pause at each scientific checkpoint for my
sign-off, and self-correct known failures. Narrate what you're doing as you go.
```

### B. Gate 1 — alignment QC (hero, recordable now)
```
I'm resuming a cryo-ET run with the OPUS-ET conductor. The run is at
<RUN_DIR> on the `super` cluster (SSH alias: super),
already computed through reconstruction and template matching — nothing needs
recomputing.

We're screen-recording a demo, so run in REPLAY MODE: load the run state from
disk and do NOT submit any SLURM jobs — if a phase's outputs already exist,
treat it as done. Fast-forward to the alignment-QC checkpoint (Gate 1), run its
per-tomogram slice-preview QC on the existing tomograms, and present the
keep/discard decision to me. If an expected output is genuinely missing, stop
and tell me — don't launch anything.
```

### C. Gate 2 — picks QC (recordable now)
Same opening two sentences as B, then:
```
...Fast-forward to the picks-QC checkpoint (Gate 2), run the ribosome + FAS
overlay QC and the tm_eval_agreement scores on the existing picks — ribosome
`template_matching/ribo/particles/*.xml`, FAS `template_matching/fas/particles/*.xml`
(under the run dir) — and present them to me. If an expected output is genuinely
missing, stop and tell me.
```

### D. Gate 3 — state selection (recordable now)
Same opening two sentences as B, then:
```
...Fast-forward to the state-selection checkpoint (Gate 3), run the four-signal
analysis (template/consensus comparison, N×N map consistency, and the latent
UMAP) on the existing state maps in `opuset/ribo/z8_expanded/analyze.35/kmeans20`
(the 20 k-means maps of the 58k expanded run — use exactly this dir, NOT the
other `z8` / `z8_angpix337_OLD` runs), and present the selection to me. If an
expected output is genuinely missing, stop and tell me.
```

### E. Gate 4 — resolution sign-off (recordable now)
Same opening two sentences as B, then:
```
...Fast-forward to the resolution checkpoint (Gate 4). On the existing fixed-mode
half-maps `opuset/ribo/fixed_subset1/half1.mrc` + `opuset/ribo/fixed_subset2/half2.mrc`
(the k17/18/19 selection at 4.2 Å/px — NOT `baseline_k6-8-9-10`), derive a molecule
mask from the density (not a sphere), compute the gold-standard FSC with the
phase-randomization correction, and present the corrected 0.143 resolution + the FSC
curve for my sign-off before M refinement. If an expected output is genuinely missing,
stop and tell me.
```

### Autonomous variant (more impressive, less controlled)
Drop the "fast-forward to <gate>" clause and use *"pick up where the run left off, reach the
next open checkpoint, run its QC, and present the decision"* — the conductor decides which
gate it's at.

## Notes
- Keep the real narration tight; the checkpoint text the conductor prints is the script.
- Do NOT delete the archived flawed run or the real outputs to "clean up" for a take — stage
  via the snapshot + gate-approval reset, never by removing computed results.
- Record in a fresh session (see above); the working session stays untouched.
