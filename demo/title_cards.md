# Title Cards & Lower-Thirds — copy sheet

Ready-to-typeset text for the editor. **Lower-thirds** are small labels that appear as each
scene starts (keep to ≤2 lines, on screen ~3–4 s). **Cards** are full-frame title/end graphics.
Scene numbers + timings match [`video_script.md`](video_script.md).

---

## Opening card  (Scene 0, 0:00)
> # CONDUCTOR
> ### an agent you supervise, not a script you babysit
>
> *Raw cryo-ET tilt-series → in-cell molecular maps — driven end-to-end, paused at every
> scientific checkpoint.*

Optional under-title chip: `WARP · AreTomo2 · PyTOM · OPUS-ET · M`

---

## Lower-thirds  (one per scene)

| Scene | Time | Label (line 1) | Sub (line 2, optional) |
|-------|------|----------------|------------------------|
| 0 | 0:00 | **Hand-off** | you give it a cluster path and an intent |
| 1 | 0:18 | **Gate 1 · Alignment QC** | 10/10 tomograms · one QC agent each · handedness checked |
| 2 | 1:00 | **The Agent Reasons** | silent CTF pixel-size catch (3.37 → 4.2 Å) · then +8k particles, warm-started |
| 3 | 1:28 | **Gate 3 · State Selection** | four converging signals — the agent surfaces, you decide |
| 4 | 2:12 | **Second Species + Joint Refinement** | FAS: 0.97 recall · D3 barrel · both molecules in one M population |
| 5 | 2:40 | **In-Cell Finale** | corrected FSC 18.26 Å · the refined molecules, back inside the cell |

---

## End card  (Scene 6, 2:55–3:00)
Three principles, revealed one line at a time (sync to captions 25–26):

> **State on disk, not in chat** — a cold session re-derives the whole run from `.opus_run_state.json`.
> **Gates for judgment** — the agent runs the QC; the human makes the scientific calls.
> **Tools, test-covered** — every QC tool built here is TDD'd, not vibes.

Then the sign-off card:

> ## An agent you supervise —
> ## not a script you babysit.

---

## Optional credit chip  (over the end card or a brief outro)
> Tools built this project (all test-covered):
> `tm_auto_mask` · `tm_eval_agreement` · `tm_picks_overlay` · `compare_to_template` ·
> `state_consistency` · `state_tomo_stats` · `gen_mask_from_map` · `compute_fsc`

---

### Style notes
- One typeface throughout; lower-thirds bottom-left, ~40% width, semi-transparent slab so
  terminal text stays legible behind them.
- Don't overlap a lower-third with the moment the human types the gate decision — that beat
  should be clean.
- Numbers are final for Gates 1–3 and FAS (58k training, k17/18/19 core = 14,797 particles,
  corrected FSC 18.26 Å; FAS overall recall 0.973) — safe to lock those graphics. Only the
  Scene 5 in-cell ArtiaX finale still needs re-rendering, and the joint two-species M
  refinement is running now — don't quote a combined resolution number until that log lands.
