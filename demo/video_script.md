# Demo Video — Script

**Working title:** *Conductor — an agent you supervise, not a script you babysit*
**Length:** **≤ 3:00** (hard cap — submission requirement).
**Format:** **primarily screen recordings** — Claude Code *being the conductor* (the functions, live at the gates) + the ChimeraX result animations. Light narration, **minimal produced graphics** (title cards are hard to make — avoid them; let the screen do the talking).
**Emphasis:** the **functions**, the **results**, and **why they matter** — not agent trivia.

**The spine — the gates.** The five **gates** are the agent's checkpoints: at each one it runs the
pipeline, computes the QC evidence, and **hands the decision to a human**. That checkpoint rhythm
*is* the story. We ride it end to end — reconstruct → pick → classify → measure → refine — and land
on the result: two molecules resolved and mapped back inside the cell.

> **This is the single script** — story + shot list + the copy-paste prompt for every scene + how to
> capture. The only external reference is the deeper replay mechanics (why replay is safe, staging a
> gate) in [`opus-et-conductor/references/demo_recording.md`](../opus-et-conductor/references/demo_recording.md).

---

## Shot list (≤ 3:00)

`LIVE` = screen-record Claude Code fresh (real capture); `B-ROLL` = pre-rendered cut-in, narrate over.

| # | Time | Beat (gate) | On screen | Capture / asset |
|---|------|-------------|-----------|-----------------|
| 0 | 0:00–0:15 | **Hand-off** | open on the terminal — the problem in one line, then you hand the whole pipeline over | `LIVE` terminal (Scene 0 prompt) |
| 1 | 0:15–0:50 | **Gate 1 — alignment QC** | parallel per-tomogram QC → 10/10 verdicts → slices + handedness → you approve | `LIVE` (Scene 1 prompt) + `gate1_alignment/` |
| 2 | 0:50–1:18 | **Gate 2 — particle picking (two species)** | template matching finds the molecules; QC **discriminates good vs poor tomograms**; the *same tools* find a second species (FAS) | `B-ROLL` `gate2_ribosome_picks/` + `gate2_fas_picks/` |
| 3 | 1:18–2:05 | **Gate 3 — state selection** (peak) | 20 states → four converging signals → confident core + honest flag → you decide | `LIVE` (Scene 3 prompt) + `gate3_states/` |
| 4 | 2:05–2:32 | **Gate 4 + 5 — resolution + joint refinement** | honest FSC (mask + phase-randomization); then **both species → one M population** (multi-particle refinement) | `B-ROLL` `gate4_resolution/` + joint `warp_m_refine` log |
| 5 | 2:32–2:55 | **Results — maps + molecules in the cell** (payoff) | ribosome **7.76 Å** + FAS **13.88 Å** (rotating maps) → both mapped back to every pose in the tomogram (*molecular sociology*) | `B-ROLL` `finale/` — `m_refined_fsc.png`, `m_refined_*_spin.mp4`, `finale_insitu.mp4`, `insitu_TS029.mp4` |
| 6 | 2:55–3:00 | **Why it matters** (close) | in-cell structural biology, agent-driven + human-gated | `B-ROLL` — hold on the finale + spoken close (no title card) |

**Hard cap ≤ 3:00.** The two **LIVE** gates (1 and 3) are the spine — never cut them. If a capture
runs long, compress Scene 2 or 4 first; don't starve the Scene-5 results.

---

## The script

Each scene below carries its own **copy-paste prompt** (in a code block) — read the scene, paste
its prompt into a fresh Claude Code session, record. In every prompt, replace **`<RUN_DIR>`** with
your processing dir on the cluster (the run dir), and `super` with your SSH alias. The **live
captures** are the opening hand-off (Scene 0) and the two spine gates (Scene 1, Scene 3); Scenes 2
and 4 carry *optional* live prompts.

### Scene 0 — Hand-off (0:00–0:15) · LIVE
**On screen:** open **straight on the terminal** — you type the hand-off; Claude Code preflights the
toolchain, auto-detects the parameters, writes and validates `pipeline.conf`, and fires the first
job (against the already-computed run it instead reports progress on disk and heads to the first
checkpoint — either reads as a genuine hand-off). No intro graphic; let the terminal open the film.
**Narration:**
> "Cryo-electron tomography turns raw movies of a cell into molecular maps *inside* that cell.
> Normally it's a week of babysitting a dozen tools. Here — you hand it off."
**Capture:** LIVE — fresh Claude Code session. **Paste:**
```
I have a cryo-ET dataset to process end-to-end — raw tilt-series through to in-cell molecular
maps, on the `super` cluster (SSH alias: super); frames + acquisition MDOCs are staged under
<RUN_DIR>. Use the OPUS-ET conductor: preflight the toolchain and SLURM partitions, auto-detect
the acquisition parameters, write and validate pipeline.conf, and begin the reconstruction
pipeline. Track progress in .opus_run_state.json, pause at each scientific checkpoint for my
sign-off, and self-correct known failures. Narrate as you go.
```

### Scene 1 — Gate 1: alignment QC (0:15–0:50) · LIVE · SPINE
**On screen:** Claude Code reaches the first checkpoint — reconstruction done, one QC agent per
tomogram (parallel), central slices + a WARP↔AreTomo handedness check. It prints "10/10 good — keep
which?"; you approve.
**Narration:**
> "First checkpoint. It doesn't ask you to trust it — it shows you: every tomogram reconstructed,
> one QC agent each, a handedness check so downstream poses aren't mirrored. Ten of ten. You sign off."
**Capture:** LIVE — fresh Claude Code session, replay mode. **Paste:**
```
I'm resuming a cryo-ET run with the OPUS-ET conductor. The run is at <RUN_DIR> on the
`super` cluster (SSH alias: super), already computed through reconstruction, template
matching, OPUS-ET and M — nothing needs recomputing.

We're screen-recording a demo, so run in REPLAY MODE: load the run state from disk and do
NOT submit any SLURM jobs — if a phase's outputs already exist, treat it as done.
Fast-forward to the alignment-QC checkpoint (Gate 1), run its per-tomogram slice-preview QC
on the existing tomograms, and present the keep/discard decision to me. If an expected
output is genuinely missing, stop and tell me — don't launch anything.
```

### Scene 2 — Gate 2: particle picking, two species (0:50–1:18) · checkpoint
**On screen:** template matching lays picks over the tomogram; the QC **discriminates** — on a good
tomogram picks blanket the ribosome-rich cytoplasm; on a poor one (TS_041) the *top* picks land on a
carbon/ice edge, a clear "exclude this one" signal. Then the same three tools, a new config, **no
code change**, find a second, far rarer machine — **fatty-acid synthase** — at **0.97 recall**.
**Narration:**
> "Next checkpoint: it finds the molecules. Template matching, scored — and the QC tells good
> tomograms from bad: here the picks follow the cell; there, the strongest hits sit on an ice edge,
> so that tomogram is out. Then the same tools, a new config, no new code — and it finds a second,
> much rarer machine, at ninety-seven-percent recall."
**Capture:** B-ROLL — `qc/gate2_ribosome_picks/TS028_good_all-picks.png`,
`TS041_poor_top200_on-artifacts.png` (the discriminator), `qc/gate2_fas_picks/TS028_fas_recall0.989_all.png`.
*Optional LIVE replay* — fresh session, replay mode. **Paste:**
```
I'm resuming a cryo-ET run with the OPUS-ET conductor. The run is at <RUN_DIR> on the
`super` cluster (SSH alias: super), already computed through reconstruction, template
matching, OPUS-ET and M — nothing needs recomputing.

We're screen-recording a demo, so run in REPLAY MODE: load the run state from disk and do
NOT submit any SLURM jobs — if a phase's outputs already exist, treat it as done.
Fast-forward to the picks-QC checkpoint (Gate 2), run the ribosome + FAS overlay QC and the
tm_eval_agreement scores on the existing picks, and present them to me. If an expected output
is genuinely missing, stop and tell me — don't launch anything.
```

### Scene 3 — Gate 3: compositional-state selection (1:18–2:05) · LIVE · PEAK
**On screen:** 20 k-means states. One correlation-to-template score would mislead — a low-res
template loves a smooth blob and ranks the *sharpest* maps dead last. So the agent brings **four**
measurements, mostly template-free, and shows where they agree and where they fight; it names the
confident core (k17/18/19) and flags the one ambiguous cluster instead of hiding it. Then it steps
back — you decide.
**Narration:**
> "The payoff checkpoint. Which of these twenty blobs are real ribosomes? One score would mislead
> you — a low-res template loves a smooth blob, and ranks the sharpest maps last. So it brings four
> measurements, mostly template-free, and shows you where they agree and where they fight. It names
> the confident core, flags the ambiguous cluster — instead of hiding it. Then it steps back. You decide."
**Capture:** LIVE — fresh session, replay mode. **Paste:**
```
I'm resuming a cryo-ET run with the OPUS-ET conductor. The run is at <RUN_DIR> on the
`super` cluster (SSH alias: super), already computed through reconstruction, template
matching, OPUS-ET and M — nothing needs recomputing.

We're screen-recording a demo, so run in REPLAY MODE: load the run state from disk and do
NOT submit any SLURM jobs — if a phase's outputs already exist, treat it as done.
Fast-forward to the state-selection checkpoint (Gate 3), run the four-signal analysis
(template/consensus comparison, N×N map consistency, and the latent UMAP) on the existing
state maps, and present the selection to me. If an expected output is genuinely missing,
stop and tell me — don't launch anything.
```
Assets: `gate3_states/ribo_states_vs_template_montage.png`, `ribo_state_consistency_raw.png`,
`ribo_state_gallery_3d.png`, `ribo_latent_umap_states.png`.
**Honesty guardrail:** keep the "one score would mislead" and "flags the ambiguous cluster" lines — they're what make the science land.

### Scene 4 — Gate 4 + 5: resolution + joint refinement (2:05–2:32)
**On screen:** Gate 4 — a gold-standard half-map **FSC**, honest: the mask itself can fake signal, so
it randomizes the phases and subtracts that off (corrected 18.26 Å for the imported starting model);
the mask–density overlay *shows* the mask hugs the molecule. Gate 5 — both species go into **one M
population**, and multi-particle refinement solves the shared tilt-series model with every particle
from both: the abundant ribosomes anchor the geometry, the rare FAS rides along.
**Narration:**
> "Resolution, measured honestly — the mask can fake signal, so it randomizes the phases and subtracts
> that off. Then both molecules go into *one* refinement population, so the tilt-series model is solved
> with every particle from both — ribosomes anchor the geometry, and the rare species rides along."
**Capture:** B-ROLL — `gate4_resolution/ribo_fsc_corrected.png` + `*_mask_overlay.png`; the joint `warp_m_refine` log (per-species resolutions).
*Optional LIVE replay of Gate 4* — fresh session, replay mode. **Paste:**
```
I'm resuming a cryo-ET run with the OPUS-ET conductor. The run is at <RUN_DIR> on the
`super` cluster (SSH alias: super), already computed through reconstruction, template
matching, OPUS-ET and M — nothing needs recomputing.

We're screen-recording a demo, so run in REPLAY MODE: load the run state from disk and do
NOT submit any SLURM jobs — if a phase's outputs already exist, treat it as done.
Fast-forward to the resolution checkpoint (Gate 4). On the existing fixed-mode half-maps,
derive a molecule mask from the density (not a sphere), compute the gold-standard FSC with the
phase-randomization correction, and present the corrected 0.143 resolution + the FSC curve for
my sign-off. If an expected output is genuinely missing, stop and tell me — don't launch anything.
```
**Honesty guardrail:** quote the **corrected** FSC, and **per-species** resolutions from the log — never invent a combined number.

### Scene 5 — Results: maps + molecules back in the cell (2:32–2:55) · PAYOFF
**On screen:** the maps the whole pipeline was for — the **ribosome at 7.76 Å** (rRNA helices) and
**FAS at 13.88 Å** (its D3 barrel), rotating. Then both mapped **back into the tomogram** — every
particle's pose populated with its density: the two molecules in their real cellular arrangement.
**Narration:**
> "The result: a ribosome at seven-point-eight ångströms, and fatty-acid synthase at fourteen — its
> D-three barrel, resolved. Then both maps go back where they live — every molecule, at its pose,
> inside the cell. Molecular sociology, read straight off the data."
**Capture:** B-ROLL — `finale/m_refined_fsc.png` (the gold-standard FSC proving 7.76 / 13.88 Å) + `m_refined_maps.png` + `m_refined_ribo_spin.mp4` + `m_refined_fas_spin.mp4`
→ `finale_insitu.mp4` → `insitu_TS029.mp4` (the organelle-rich companion). Rendered locally in ChimeraX 1.10 + ArtiaX 0.7.0.

### Scene 6 — Why it matters (2:55–3:00) · close
**On screen:** no title card — just **hold on the last shot** (the in-cell finale still rolling, or
the conductor's terminal at the final checkpoint) and let one line land.
**Narration (5s — keep it to the tagline):**
> "State on disk. Gates for judgment. An agent you supervise — not a script you babysit."

*(The design principles — state-on-disk / gates-for-judgment / tools-test-covered — are already
shown by the film itself; spell them out only if your editor makes on-screen text trivial, else the
tagline alone closes it.)*

---

## How to shoot it

**What you're making:** a screen-recording of Claude Code *being the conductor* — the opening
hand-off and two gates (1 and 3) are live terminal captures; the rest is pre-rendered b-roll cut in
under narration. Everything heavy already ran; you're re-driving only the cheap part — reach a gate
→ run its QC on existing outputs → present the decision. Open **straight on the terminal**; no intro card.

**Prep (~15 min)**
- On the machine with cluster SSH (alias `super`) **and** the local ChimeraX renders. Recorder ≥1080p, mic tested, large terminal font, clean prompt.
- **Replay-mode safety — the one hard rule:** run the conductor in **REPLAY MODE** — it loads state from disk and **never submits a SLURM job**; if an output is missing it stops. Set `demo_replay: true` (or say "replay mode — recording"). The prompts below already enforce it.
- **Snapshots for re-takes:** for each live gate, `cp .opus_run_state.json .demo_snapshots/gateN.json` and clear that gate's approval so the conductor re-opens it; **restore before every take** (details: `demo_recording.md` → "Staging a gate").

**The LIVE captures** — each in a **fresh** Claude Code session (proves state lives on disk, not in chat):
- **Hand-off (Scene 0):** open on an empty terminal → paste **Scene 0's prompt** → it preflights, configures, and either fires the first job or reports progress and heads to Gate 1. Cut after ~15s.
- **Gate 1 (Scene 1):** restore the Gate-1 snapshot → fresh session → paste **Scene 1's prompt** → it fast-forwards, runs the per-tomogram QC, presents "10/10 good — keep which?" → answer **"keep all 10."**
- **Gate 3 (Scene 3):** restore the Gate-3 snapshot → fresh session → paste **Scene 3's prompt** → it presents the four-signal analysis + the honest flag → answer **keep k17/18/19.**
- Gates 2 and 4 have *optional* live prompts in their scenes if you want extra live coverage — not needed for the ≤3:00 cut.

**Verify the b-roll exists** (all in `demo/qc/`): `gate1_alignment/`;
`gate2_ribosome_picks/` + `gate2_fas_picks/`; `gate3_states/`; `gate4_resolution/`; `finale/` (`m_refined_fsc.png`,
`m_refined_maps.png`, `m_refined_ribo_spin.mp4`, `m_refined_fas_spin.mp4`, `finale_insitu.mp4`, `insitu_TS029.mp4`).
(`pipeline_strip.png` is no longer the opener — the terminal hand-off is — but it's still a fine optional cutaway.)

**Assemble & finish**
- Cut the two live gates as the spine; drop the b-roll around them; lay the narration under (draft in [`captions.srt`](captions.srt) — re-voice in your own words).
- Watch once: **≤ 3:00**, audio clean, no cluster credentials/usernames visible in the terminal captures.
- Export; add the final video link to your hackathon submission.
- Re-take anytime: restore snapshot → fresh session → repeat (replay mode never submits; state is snapshot-reversible).

---

## Production notes
- **Asset status:** Gate-1/2/3/4 stills are **final** (58k expanded run: k17/18/19 core, 14,797 particles, corrected FSC 18.26 Å; FAS overall recall 0.973). The **joint M refinement** converged (FAS 25.6 → 13.88 Å, ribosome 7.76 Å) and the **maps + in-cell scenes** are rendered (`qc/finale/`). The only remaining item is **narration** for the final cut.
- **Production kit (in `demo/`):** [`stage_gate.py`](stage_gate.py) — snapshot/reopen a gate + enable replay for a clean take; `restore` between takes. [`captions.srt`](captions.srt) — the narration as subtitles, timed to this storyboard (a draft — retime to your recorded pacing).
