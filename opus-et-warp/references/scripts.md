# opus-et-warp: scripts

Moved verbatim from SKILL.md so the skill body stays an overview; this is the reference.

## SLURM Scripts Reference

| Script | Purpose | Memory |
|--------|---------|--------|
| `warp_frameseries_import.slurm` | Frame series settings + link + CTF | 128 GB |
| `warp_tiltseries_setup.slurm` | Tilt series settings + MDOC import | 128 GB |
| `warp_export_stacks.slurm` | Export tilt stacks for alignment | 64 GB |
| `warp_aretomo_align_negate.slurm` | AreTomo2 with angle negation | 32 GB |
| `warp_update_tomo_dims.slurm` | Re-run `create_settings` with UNBINNED dims from `_ali.mrc` (Phase 3.5) | 16 GB |
| `warp_import_alignments.slurm` | Import .xf/.tlt into WARP | 64 GB |
| `warp_ts_ctf.slurm` | Defocus handedness check + CTF | 256 GB |
| `warp_ts_reconstruct.slurm` | Tomogram reconstruction | 256 GB |
| `gen_sphere_mask.slurm` | Sphere mask for TM | 16 GB |
| `gen_template_from_mrc.slurm` | Generate PyTom TM template from user MRC map | 32 GB |
| `gen_tm_jobs_aretomo.slurm` | Generate PyTOM job XMLs | 16 GB |
| `run_tm_sequential.slurm` | Run TM jobs sequentially | 256 GB |
| `extract_tm_candidates_parallel.slurm` | Extract particles (parallel) | 256 GB |
| `convert_to_star.slurm` | PyTOM XML → per-tilt-series STAR (canonical; wraps PyTom `convert.py`) | 64 GB |
| `convert_pytom_to_warp.slurm` | PyTOM STAR → WARP format | 32 GB |
| `warp_export_particles.slurm` | Export subtomograms | 128 GB |
| `gen_training_mask.slurm` | Training mask | 16 GB |
| `train_opuset.slurm` | Train OPUS-ET heterogeneity model (4 GPUs) | 256 GB |
| `train_opuset_fixed.slurm` | Train OPUS-ET fixed-mode (encoder-free, homogeneous average; run twice on even/odd splits to produce M half-maps) | 256 GB |
| `warp_m_setup.slurm` | M refinement: create_population + create_source (one-shot) — `references/advanced.md` | 16 GB |
| `warp_m_create_species.slurm` | M refinement: create_species (per species) | 64 GB |
| `warp_m_update_mask.slurm` | M refinement: update_mask (between passes only — needs a completed refine first) | 32 GB |
| `warp_m_refine.slurm` | M refinement: MCore pass (iterative — re-submit each iteration) | 256 GB |
| `warp_m_export.slurm` | M refinement: re-export refined particles for one species at a time via ts_export_particles | 128 GB |

For `warp_m_update_mask.slurm` and `warp_m_export.slurm`, `SPECIES_BASE` may be either the original species base name or the full hashed folder name. If `m/species/<species>_*` matches multiple folders, use the full folder name, e.g. `SPECIES_BASE="ribosome_1199b7f2"`, so one species is selected unambiguously.

**All scripts** source `pipeline.conf` (and `species.conf` for Phase 6+). Fill the config files — not individual scripts. Run `validate.sh` before submitting to catch any remaining placeholders or path issues.

**Cluster-specific SLURM directives (also do this for every script).** Scripts
ship with generic `--partition`, `--gres`, and `--time` defaults. Before the
first submission on a new cluster:

- For scripts with no `--gres=gpu:*`, replace `--partition=normal` with `--partition=$CPU_PARTITION`. This includes CPU-only scripts such as `convert_*`, `gen_*`, `extract_*`, `warp_tiltseries_setup`, `warp_m_setup`, `warp_m_create_species`, `warp_m_update_mask`, `warp_update_tomo_dims`, and `warp_import_alignments`.
- For any script that has `--gres=gpu:*`, use `--partition=$GPU_PARTITION` even if the shipped partition is `normal`. This includes `warp_frameseries_import.slurm` and `warp_export_stacks.slurm`.
- Replace the shipped GPU request (`--gres=gpu:1`, `--gres=gpu:4`, etc.) with the cluster-specific GPU request and adjust `--nproc_per_node` / `--num-gpus` in the training scripts to match `$NUM_GPUS`.
- `--time` defaults are guesses (12h CTF, 24h recon, 48h TM, 120h training). If the user knows their cluster's max time or expected scale (e.g. 200 TS vs 20), bump or lower accordingly.

If `sinfo -s` lists no partition called `normal` or `gpu`, **do not submit** with the shipped defaults — SLURM will reject the job immediately.

**Placeholders are caught by `validate.sh`** (see `references/quickstart.md` § Pre-flight Validation). It scans `pipeline.conf`, `species.conf`, and all SLURM scripts for `<placeholder>` tokens and `/path/to/` defaults.

---

## File Organization

Skill deployment files (shipped with the skill):
```
scripts/                        # SLURM phase scripts (one per phase/sub-phase) + manifest.yml
                                #   + Python helpers: tm_auto_mask.py, extract_candidates_parallel.py, format_slurm.py
references/                     # advanced.md, gotchas.md, phases.md, workflow.md
tests/                          # pytest — test_tm_auto_mask.py
validate.sh                     # Phase-aware pre-flight check (reads scripts/manifest.yml)
pipeline.example.conf           # Copy to pipeline.conf and edit
species.example.conf            # Copy to species.conf and edit (one per species)
pipeline_flowchart.html         # Visual reference
LICENSE
```

Pipeline runtime output under WORK_DIR:
```
WORK_DIR/
├── logs/                       # SLURM .out / .err files
├── mdoc/                      # MDOC files
├── tomostar/                  # Canonical tilt series list (*.tomostar)
├── warp_frameseries/          # Phase 1
│   └── average/               # Linked or motion-corrected tilt images
├── warp_tiltseries/           # Phases 2–5, 7
│   ├── tiltstack/TS_XXX/      # Exported stacks + AreTomo output
│   │   ├── TS_XXX.st          # Tilt stack
│   │   ├── TS_XXX.rawtlt      # Raw angles
│   │   ├── TS_XXX_neg.rawtlt  # Negated (for AreTomo)
│   │   ├── TS_XXX_ali.mrc     # AreTomo tomogram
│   │   ├── TS_XXX.xf          # Transforms (for WARP)
│   │   └── TS_XXX.tlt         # Refined angles (for WARP)
│   ├── reconstruction/        # CTF-corrected tomograms (TS_XXX_<apix>Apx.mrc)
│   ├── subtomo/               # Phase 7: exported subtomograms for OPUS-ET
│   │   └── TS_XXX/*.mrc        #   per-tilt-series subtomograms (at OUTPUT_ANGPIX)
│   ├── <TM_LABEL>_matching.star   # Phase 7 export STAR (warp_export_particles)
│   └── <species>_matching_refined.star  # M export STAR, one per species (warp_m_export)
├── templates/                  # Template generation + masks (Phase 6/8)
│   ├── <TM_LABEL>_tm.mrc        #   template at ALIGN_ANGPIX (gen_template_from_mrc)
│   ├── <TM_LABEL>_mask.mrc      #   TM sphere mask (gen_sphere_mask)
│   └── <TM_LABEL>_training_mask.mrc  # training mask (gen_training_mask)
├── template_matching/
│   └── <TM_LABEL>/            # One namespace per species/template
│       ├── jobs/              # PyTOM job XMLs + scores/angles
│       ├── particles/         # Extracted particle XMLs
│       ├── star_files/        # Per-tilt-series PyTom STAR files
│       └── warp_star/         # WARP-compatible STAR files
├── m/                         # Optional MTools / M refinement workspace
│   ├── <population>.population # Created by warp_m_setup.slurm
│   ├── <source>.source        # MTools data source (warp_m_setup.slurm)
│   └── species/
│       └── <species>_<hash>/  # Created by warp_m_create_species.slurm
│           ├── <species>.species
│           ├── *_particles.star
│           ├── *_relion.star  # Produced by warp_m_export.slurm before export
│           ├── <species>_half1.mrc      # M-refined half-maps (warp_m_refine → MCore)
│           ├── <species>_half2.mrc
│           └── <species>_filtsharp.mrc  # sharpened combined map (used for the in-cell render)
└── opuset/                     # Phase 8: OPUS-ET training + heterogeneity analysis
    └── <TM_LABEL>/               #   namespaced by species
        ├── z<ZDIM>/             #   heterogeneity training (e.g. z8; warm-start variant: z8_expanded)
        │   ├── weights.*.pkl
        │   ├── z.*.pkl
        │   ├── config.pkl
        │   ├── analyze.<epoch>/kmeans<numk>/   # Gate-3 states: labels.pkl, center maps, umap.pkl
        │   └── sel_<TM_LABEL>.star             # Gate-3 selected state (select_state.slurm)
        ├── fixed_subset<N>/     #   fixed-mode half-map reconstruction (N = 1, 2)
        │   ├── weights.*.pkl
        │   ├── config.pkl
        │   └── tmp0.mrc         #   the half-map reconstruction
        ├── half1.mrc            #   ← fixed_subset1/tmp0.mrc (prepare_m_halfmaps.slurm)
        ├── half2.mrc            #   ← fixed_subset2/tmp0.mrc
        └── halfmap_mask.mrc     #   molecule mask for M (optional)
```

---

