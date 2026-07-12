# OPUS-ET-AGENT — agentic cryo-ET pipeline

A supervised-autonomy **conductor** (Claude Code) that drives the cryo-ET reconstruction
pipeline end-to-end — **WARP → AreTomo2 → PyTOM → OPUS-ET → M** — pausing at each scientific
**gate** for a human sign-off, self-correcting known failures, and mapping the refined molecules
back **inside the cell** (ChimeraX/ArtiaX). Built on top of a set of great cryo-ET data-processing
software — **WARP/M**, **AreTomo2**, **PyTOM**, **OPUS-ET**, **ChimeraX**, and **ArtiaX**.

## The task — and why it matters

**Cryo-electron tomography (cryo-ET)** is the only technique that resolves macromolecules at
near-molecular detail *inside intact cells* — structural biology *in situ*, where you see not just
a molecule's shape but **where it sits and who it sits with** ("molecular sociology"). Getting
there means turning raw tilt-series — dose-fractionated movies of a frozen cell tilted in the
microscope — into 3D density maps of the molecules within: motion/CTF correction, tilt-series
alignment, tomogram reconstruction, particle picking, per-particle heterogeneity analysis, and
multi-particle refinement. That pipeline is long and fragile, spans half a dozen specialist
packages, and is gated by expert judgment at nearly every step — which is why in-cell structural
biology stays expensive and low-throughput.

**This project puts an AI agent in the driver's seat.** Claude Code runs the whole chain
end-to-end with *supervised autonomy*: it discovers the toolchain, configures and submits the
cluster jobs, runs the QC at each stage, and — crucially — **stops at each scientific checkpoint
and hands the decision to a human**, backing that decision with the evidence it computed. The
payoff is in the results: on a real dataset ([EMPIAR-10988](https://www.ebi.ac.uk/empiar/EMPIAR-10988/))
it drove **two** molecular species to high resolution at once — the **ribosome to 7.76 Å** and
**fatty-acid synthase to 13.88 Å** — and mapped both back into the cell as *molecular sociology*.
Why it matters: a weeks-long, expert-only workflow becomes a reproducible, test-covered,
agent-driven one that still keeps the scientist in command of every call that matters.

**The whole arc at a glance** — raw movies → tomogram → picks → OPUS-ET states → M-refined maps →
the two molecules mapped back into the cell:

![pipeline overview](demo/qc/pipeline_strip.png)

## The gates

The agent runs the judgment-support work; the human holds the decision.

1. **Gate 1 — alignment QC** — one QC agent per tomogram (parallel), reconstruction slices + a
   WARP↔AreTomo handedness check.
2. **Gate 2 — picks QC** — template-matching picks scored + overlaid; runs on **two species**
   (ribosome and FAS) from one reconstruction set.
3. **Gate 3 — state selection** — OPUS-ET latent states judged by four converging, mostly
   template-free signals (the sharpest maps are the ones a naive template score ranks worst).
4. **Gate 4 — resolution** — gold-standard half-map FSC with the phase-randomization correction,
   plus a **mask–density overlay** that *shows* the mask wraps the molecule without clipping.
5. **Gate 5 — joint M refinement** — both species in one M population; multi-particle refinement
   solves the shared tilt-series model with every particle at once (**FAS 25.6 → 13.88 Å**).

## Components

- **opus-et-warp** — reconstruction engine (WARP / AreTomo2 / PyTOM / OPUS-ET / M), SLURM.
- **opus-et-analysis** — interpretation + QC tools (mask/FSC, k-means states, map consistency, pose parsing).
- **opus-et-conductor** — orchestration brain: run-state, checkpoints, monitor/diagnose loop.
- **opus-et-visualize** — in-cell ChimeraX/ArtiaX scenes (each map placed at every pose, colored by state/species).

## Architecture

Supervised autonomy: the conductor runs the machinery and computes the evidence; the scientist
makes every judgment call, at five gates.

```mermaid
flowchart LR
    H(["Scientist<br/>intent in · decisions at the gates"])

    subgraph COND["Claude Code — the Conductor (opus-et-conductor)"]
      direction TB
      PRE["preflight<br/>discover toolchain / envs / partitions"]
      STATE[".opus_run_state.json<br/>phases + gate approvals"]
      LOOP["per-phase loop<br/>configure → sbatch → monitor → validate → self-correct"]
      PRE --> STATE --> LOOP
    end

    subgraph TOOLS["Tool-layer skills it drives"]
      direction TB
      WARP["opus-et-warp<br/>WARP · AreTomo2 · PyTOM · OPUS-ET · M"]
      ANA["opus-et-analysis<br/>QC · masks/FSC · k-means states · picks eval"]
      VIZ["opus-et-visualize<br/>ChimeraX · ArtiaX in-cell scenes"]
    end

    CLUSTER[("HPC cluster<br/>SLURM · GPU")]

    H -->|dataset + intent| COND
    COND -->|drives| TOOLS
    WARP -->|SLURM jobs| CLUSTER
    CLUSTER -->|"tomograms, maps, metadata"| ANA
    ANA -->|QC evidence| COND
    COND ==>|"gate: keep / choose?"| H
    H ==>|sign-off| COND
    VIZ -->|molecules back in the cell| H
```

**Gates:** 1 alignment QC · 2 picks QC · 3 state selection · 4 resolution · 5 joint-M refinement.

## Results & demo

- **[demo/README.md](demo/README.md)** — the curated results bundle (Gates 1–5), figure by figure.
- **[demo/video_script.md](demo/video_script.md)** — the ≤3:00 demo-video **script** (story + shot list + how to capture, in one doc).
- **[SUBMISSION.md](SUBMISSION.md)** — hackathon deliverables tracker.

## Development

Every QC/analysis/viz tool is test-covered (TDD) — **187 tests**, all green:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

## Acknowledgements

- **Anthropic** — for sponsoring this hackathon and for Claude; Claude Code drove the entire
  pipeline and built every tool here.
- **MRICS** — for the HPC compute this work ran on.
- **J. Mahamid lab** — for the cryo-ET dataset ([EMPIAR-10988](https://www.ebi.ac.uk/empiar/EMPIAR-10988/)).
