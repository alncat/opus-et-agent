# Hackathon Submission Tracker

**Living doc** — updated as the project progresses so the final submission is a polish pass, not a scramble. Three required deliverables below.

Project: **OPUS-ET-AGENT** — an agentic data-processing pipeline for cryo-ET. Claude Code drives the reconstruction toolchain (WARP → AreTomo2 → PyTOM → OPUS-ET → M) end-to-end with human checkpoints, then visualizes the refined molecules back inside the cell (ChimeraX/ArtiaX).

---

## Deliverable 1 — Public work link (repo / notebook / write-up)

Target: a GitHub repo, public or access-granted for judging.

- Source: this repository (git-initialized, branch `main`).
- What's in it:
  - `opus-et-conductor` + `opus-et-visualize` — **built this hackathon** (orchestration + in-cell viz).
  - `opus-et-warp` + `opus-et-analysis` — the tool-layer skills, **substantially extended this hackathon** with new phase scripts (`analyze_opuset`, `select_state`) and TDD analysis tools: `tm_auto_mask`, `tm_picks_overlay`, `tm_eval_agreement`, `compare_to_template` (16 tests), `state_consistency` (9 tests).
  - `demo/` — a **curated results bundle** (Gate-1 alignment QC, Gate-2 ribosome + FAS picks QC, Gate-3 four-signal state selection) with a self-contained README telling the scientific story.
  - The design spec + implementation plan (authored via Claude Code's brainstorming + writing-plans skills), and the M1 conductor test suite (23 tests) plus the per-tool analysis suites — all green (187 tests total).
- Checklist before publishing:
  - [ ] Create the GitHub repo; make public or grant judges access.
  - [x] Scrub sensitive data — **verified clean**: no cluster username, absolute HPC/home path, personal path, session-scratch path, credential, key, or email in any tracked file. Environment facts live in Claude Code memory, **outside** the repo; run-dir / repo / scratch paths use placeholders (`<RUN_DIR>` / `<REPO_ROOT>` / `<SCRATCH>`).
  - [ ] Decide what's public (the pre-existing `opus-et-warp`/`opus-et-analysis` skills are yours — your call whether to include).
  - [x] Judge-facing README: problem, approach, what Claude built, how to run (`README.md` — verified all four present + architecture diagram + EMPIAR link).
  - [ ] `git remote add origin …` + `git push`.

## Deliverable 2 — Demo video (≤ 3 minutes)

**Approach (brainstormed + approved): "two gates as the spine."** Make the video
*interaction-forward*, not just results-forward — the audience watches a human drive a
multi-day HPC pipeline by conversation and watches Claude Code **stop itself at each
scientific gate** to run QC and hand the judgment call back. Polished narrated arc with
**real Claude Code terminal captures** embedded at the interaction/gate beats
(live-record a conductor session replayed against already-computed results so a gate
re-fires in seconds). Gates 1 (alignment QC) and 2/3 (picks + state selection) are the
anchor moments — all capturable now; the in-cell finale slots in once M refinement lands.

Six-beat structure, ≤ 3:00 hard cap (canonical storyboard: `demo/video_script.md`):

0. **0:00–0:18 — Problem + hand-off.** Cryo-ET reaches high resolution *and* in-cell context, but the pipeline is many manual, cluster-heavy, error-prone steps — plain-language intent → the conductor runs preflight, writes `pipeline.conf`, fires the first SLURM job. *(how we interact with Claude Code)*
1. **0:18–1:00 — Gate 1: alignment QC (SPINE).** The conductor **auto-reaches** alignment QC in replay mode, **fans out one agent per tomogram** (parallel Workflow), reports "10/10 good," plus a WARP↔AreTomo handedness check, pauses; human approves. *(Claude Code reaching each QC check on its own.)*
2. **1:00–1:28 — The agent reasons (merged interlude).** Two quick proofs it reasons about the pipeline, not just commands: it catches the **silent CTF pixel-size bug** (OPUS-ET about to train at the raw 3.37 Å instead of the correct 4.2 Å subtomogram size — no error message, just corrupted CTF), fixes and re-runs; then a **pick-more optimization** — re-picks only the cells that hit the 5,000-particle cap and warm-starts OPUS-ET from a mid-training checkpoint instead of a full retrain, folding in ~8,000 more particles (50k → 58k).
3. **1:28–2:12 — Gate 3: state selection (SPINE / PEAK).** OPUS-ET's latent space clustered into 20 k-means states; the agent shows **four converging signals** (template-CC — which is biased toward smooth blobs and ranks the sharpest maps worst; 3D resolution; latent UMAP; N×N map-to-map consistency), names the confident high-res core, and **honestly flags the ambiguous cluster** instead of hiding it. Human keeps k17/18/19 = 14,797 particles.
4. **2:12–2:40 — Second species + joint refinement.** The **same three picks-QC tools**, a new species config, **zero code changes**, generalize to fatty-acid synthase (FAS, a D3-symmetric barrel, recall 0.973) with its own OPUS-ET heterogeneity run and its own Gate-3 pick. Then both molecules are imported into **one M population**, so refinement solves the shared tilt-series geometry/CTF using every particle from both species at once.
5. **2:40–2:55 — In-cell finale.** ArtiaX scene: the refined map(s) placed back at every particle pose inside the tomogram.
6. **2:55–3:00 — Close.** Three design-principle lines on a title card — state on disk not in chat, gates for judgment, tools test-covered.

- Assets in hand: [x] reconstruction + handedness slices (`demo/qc/gate1_alignment/`), [x] two-species picks overlays (`demo/qc/gate2_ribosome_picks`, `.../gate2_fas_picks`), [x] Gate-3 state gallery + consistency heatmap + UMAP (`demo/qc/gate3_states/`, incl. the 3D isosurface gallery `ribo_state_gallery_3d.png`) — **final** (the interactive `view_states.cxc` bundle is kept locally, not committed), [x] FAS second-species stills (`demo/qc/gate2_fas_picks/`, overall recall 0.973) — **final**.
- **How to record the live gates (skip heavy compute):** run the conductor in **replay mode** — outputs already on disk, so it fast-forwards to a gate, runs only the fast QC, and presents the checkpoint; the guardrail is *never `sbatch` during a recording*. Full staging + record runbook: `opus-et-conductor/references/demo_recording.md`. Gates 1–3 are recordable now.
- **Joint M refinement landed + converged:** six `warp_m_refine` passes on `population1` refined both species together — **FAS 25.6 → 13.88 Å** (16.79 → 16.15 → 14.60 → 14.13 → 13.92 → 13.88) and **ribosome 7.76 Å** (flat); passes 5–6 confirm convergence (Δ < 0.05 Å/pass). The ~12 Å FAS gain is the multi-particle-refinement payoff.
- **In-cell finale rendered** (locally, ChimeraX 1.10 + ArtiaX 0.7.0): 3,387 ribosomes + 95 FAS at their TS_028 poses, colored by species — the true molecular sociology (`demo/qc/finale/finale_insitu_still.png` + `finale_insitu.mp4`), plus the M-refined map pair (`m_refined_maps.png`).
- **Still pending:** [ ] narration.

## Deliverable 3 — How did you use Claude? (products + where they mattered)

**Primary product: Claude Code** (its skills system + subagent orchestration). Draft answer:

- **Design & planning.** The `brainstorming` skill turned a one-line idea into a validated design spec; `writing-plans` produced a bite-sized, test-driven implementation plan.
- **Multi-agent implementation & review.** With `subagent-driven-development`, a fresh Claude subagent implemented each task, a second reviewed spec-compliance + code quality, and a final whole-branch review (Opus) **caught a real correctness bug that the per-task reviews and all 22 passing tests missed** (duplicate phase-completion rows silently overwriting each other on the conductor's core data seam).
- **Claude Code *as* the product.** The conductor's "brain" is Claude Code itself driving reusable skills — the agent we built and the agent that built it are the same system.
- **Live HPC operations.** Claude Code SSH'd into the cluster, verified the full toolchain (WARP alncat fork confirmed by source inspection, the three conda envs, AreTomo2, GPU partitions), and diagnosed a WARP login-node out-of-memory (`0x8007000E` = 6 GB `ulimit -v` cap) — real environment engineering, not just code generation.
- **Reusable skills authored:** `opus-et-conductor` (orchestration: run-state, checkpoints, preflight, monitor/diagnose) and `opus-et-visualize` (in-cell ChimeraX/ArtiaX scenes), on top of the existing `opus-et-warp` / `opus-et-analysis`.
- **Multi-agent analysis fan-out.** For the Gate-3 state selection, Claude Code dispatched **three sonnet subagents in parallel** — each built and ran a different analysis on the real cluster maps (a template-free N×N map-to-map consistency table with tests, a ChimeraX 3D isosurface gallery, an enhanced per-particle UMAP) — then the parent reconciled their findings into one decision. Task-shaped parallelism, not one long serial chain.
- **Scientific bug-catching (crashes nothing, corrupts everything).** Human+agent scrutiny caught bugs that all-green tests would never surface: (1) OPUS-ET was fed the **raw tilt-series pixel size instead of the subtomogram pixel size**, which OPUS-ET uses to compute the **CTF** — silently corrupting the reconstruction; fixed, archived the flawed run, re-ran correctly. (2) A **missing-wedge Angle1/Angle2 sign bug** (WARP tilt = −IMOD tilt). This is the pipeline doing real cryo-EM QC, not just generating code.
- **Multi-signal scientific judgment, human-in-the-loop.** At Gate 3 the agent surfaced **four** signals for "which states are real," *showed* that the obvious metric (correlation to a template) was biased toward smooth blobs, used template-free signals to pinpoint the true high-resolution core, honestly flagged the one ambiguous cluster, and left the final scientific call to the human — the intended division of labour.
- **Generalization + joint multi-particle refinement.** Claude Code turned the **same three picks-QC tools** (`tm_auto_mask` → `tm_picks_overlay` → `tm_eval_agreement`) on a second, much rarer molecule — fatty-acid synthase, a D3-symmetric barrel — with only a new species config and **zero code changes**, reaching **0.973 overall recall**, its own OPUS-ET heterogeneity run, and its own Gate-3 state pick. It then imported **both species into one M population**, so refinement solves the **shared per-tilt-series geometry and CTF using every particle from both species at once**: the abundant ribosomes anchor the tilt-series model and the rarer FAS refines on top of it — **FAS gained ~12 Å (25.6 → 13.88 Å over six joint passes, converged) with the ribosome holding its ~7.8 Å plateau**, a proof this is a generalizable pipeline, not a one-molecule script.

_Where it mattered most:_ **driving the full multi-package pipeline to real results** — two species to high resolution, mapped back into the cell — with **human-gated scientific decisions** at each checkpoint (the **parallel multi-agent analysis** surfacing the Gate-3 evidence), on top of the **design → adversarial-review** rigor behind the build. Live cluster diagnosis + self-correction (the CTF pixel-size and missing-wedge fixes) kept it on the rails — but that's the safety net, not the headline; the headline is the science it delivered.

---

## Acknowledgements

- **Anthropic** — for sponsoring this hackathon and for Claude; Claude Code drove the pipeline end-to-end and built every tool in this repo.
- **MRICS** — for the HPC compute this work ran on.
- **J. Mahamid lab** — for the cryo-ET dataset ([EMPIAR-10988](https://www.ebi.ac.uk/empiar/EMPIAR-10988/)).

---

## Running log (raw material for the writeup)

- **2026-07-08** — Brainstormed idea → design spec; wrote M1 plan; executed 9 tasks via subagent-driven development (fresh implementer + spec/quality reviewer per task, final Opus whole-branch review caught 1 real bug); merged M1 to `main`, 22 tests green. Verified the full compute environment on the `super` cluster and diagnosed the WARP login-node OOM.
- **2026-07-08 (live run)** — Claude Code drove the `warp_DEF` ribosome dataset **end-to-end to reconstruction, fully autonomously**, on the HPC cluster: auto-detected acquisition params from an MDOC + frame header (single-frame MRC, 3.37 Å), deployed the pipeline, wrote+validated `pipeline.conf`, then submitted → monitored → verified **7 phases across all 10 tilt series** — frame-series CTF → tilt-series setup → stack export → **AreTomo alignment** → **dimension auto-correction (3.5: fixed a real 3708→3840 mismatch)** → alignment import → **CTF (auto-detected FLIP handedness) + reconstruction** — producing **10/10 tomograms (960×928×500 @ 13.48 Å)**. It then reached **Gate 1** and ran a **parallel multi-agent QC Workflow** (one Claude agent per tomogram judging XY/XZ slice previews): **all 10 judged "good", human kept all 10.** On a human follow-up it also generated an **AreTomo-vs-WARP handedness comparison** (slices at matched Z depths) and confirmed no chirality flip. **Demo gold:** real cellular tomograms with visible ribosome density + membranes, produced with zero manual pipeline babysitting; slice previews + handedness montage curated in `demo/qc/gate1_alignment/` (raw per-tomogram output stays local in the gitignored `gate1_qc/`). Session paused at Gate 1 (next: template matching / particle picking).
- **2026-07-09 (template matching → Gate 2 picks QC)** — Ran PyTOM template matching for ribosomes across all 10 tomograms and built two new TDD analysis tools: `tm_picks_overlay.py` (Gate-2 QC — picks overlaid on tomogram z-slabs, all-picks + top-N-by-score) and `tm_eval_agreement.py` (precision/recall/F1 vs a curated reference, reconciled in Å, per-tomogram). On the richest tomogram TM recovered **100% of the curated set (recall 1.000)**. Caught + fixed a real **missing-wedge bug** in `gen_tm_jobs`: the PyTOM Angle1/Angle2 are *directional* (must not be sorted), resolved with the key insight that **WARP's tilt angle is the negative of IMOD's**. Curated results in `demo/qc/`.
- **2026-07-09 (second species — FAS, generalization)** — With **zero code changes**, re-ran the same three species-agnostic tools on a **second, much sparser complex (fatty-acid synthase)** via a new `species_fas.conf` (`TM_LABEL` namespacing, per-species template/mask/wedge/candidate-cap). All 10 tomograms: **overall recall 0.973 (393/404)** vs the curated reference, with best-F1 score thresholds tightly clustered (0.21–0.28) — evidence the pipeline generalizes across species and tilt series.
- **2026-07-09 (OPUS-ET heterogeneity → Gate 3, four-signal state selection)** — Trained OPUS-ET on 50k ribosome subtomograms (latent dim 8, 40 epochs, 4 GPUs) and clustered the latent into 20 compositional-state maps (`analyze_opuset.slurm`). Built the Gate-3 "which states are real?" decision from **four signals**, using **parallel sonnet subagents** to build+run three of them concurrently: a template-free **N×N map-to-map consistency table** (`state_consistency.py`), a **20-state ChimeraX 3D isosurface gallery**, and an **enhanced per-particle UMAP** — alongside `compare_to_template.py`. Key scientific finding: a naive **correlation-to-template is biased** (a low-resolution template rewards smooth blobs), so the **template-free** signals (a tight UMAP island + a 0.94–0.96 consistency block) pinpoint the genuine high-resolution core, and the agent honestly flagged the one ambiguous/transitional cluster. The agent surfaced and reconciled the evidence; the human made the call. Formalized as `select_state.slurm`; assets in `demo/qc/gate3_states/`.
- **2026-07-09 (scientific bug catch — CTF pixel size)** — Setting up the high-res push, human+agent traced a subtle correctness bug that **crashes nothing but silently corrupts results**: OPUS-ET computes the **CTF from `--angpix`**, but training was fed the **raw tilt-series pixel size (3.37 Å)** instead of the **subtomogram pixel size (4.2 Å)** — mis-scaling every CTF zero. *How it snuck in:* the pipeline carries **two pixel sizes** (raw microscope `ANGPIX` vs the deliberately light-binned subtomo `OUTPUT_ANGPIX`); the scripts passed the raw one, and the STAR auto-detect that would have read the correct value never fired because `pipeline.conf` sets `ANGPIX` (config precedence bypassed the safety net) — and ~25% is small enough that the maps still looked like ribosomes. Fixed both training scripts (and the identically mis-scaled pose pickle) to use the subtomo angpix, **archived the flawed run** (`z8_angpix337_OLD`), and re-ran correctly. Root cause recorded in `opus-et-conductor/references/diagnose_catalog.md` + memory `opus-et-angpix-ctf`. The relative Gate-3 *method* still holds; the corrected run re-derives the selection before high-res averaging + M.
