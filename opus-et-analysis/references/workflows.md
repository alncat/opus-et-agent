# opus-et-analysis: workflows

Moved verbatim from SKILL.md so the skill body stays an overview; this is the reference.

## Common Workflows

### 1. Analyze Epoch (PCA + K-means)

Run PCA and kmeans clustering on a specific epoch:

```bash
dsdsh analyze <workdir> <epoch> <numpc> <numk>
```

Example:
```bash
dsdsh analyze . 39 10 20
```

Output: `analyze.39/` with `kmeans20/`, `pc1/` to `pc10/`, plots.

### 2. Generate Volumes for K-means Centers

The installed `dsdsh eval_vol` is **positional** and reads the centers `dsdsh analyze`
already wrote — no manual `--zfile` needed:

```bash
dsdsh eval_vol <resdir> <epoch> kmeans <numk> <apix>
# e.g.: dsdsh eval_vol . 39 kmeans 20 <apix>  -> analyze.39/kmeans20/reference<k>.mrc
```

Low-level equivalent (builds the z-file path yourself):
```bash
dsd eval_vol --load weights.<epoch>.pkl \
    -c config.pkl \
    -o kmeans_volumes \
    --zfile analyze.<epoch>/kmeans<numk>/centers.txt \
    --Apix <apix> \
    --prefix kmeans_cluster
```

### 3. Generate Volumes for Principal Components

```bash
dsdsh eval_vol <resdir> <epoch> pc <numpc> <apix>
# e.g.: dsdsh eval_vol . 39 pc 3 <apix>  -> analyze.39/pc<i>/
```

Low-level equivalent:
```bash
dsd eval_vol --load weights.<epoch>.pkl \
    -c config.pkl \
    -o pc_volumes/pc<N> \
    --zfile analyze.<epoch>/pc<N>/z_pc.txt \
    --Apix <apix> \
    --prefix pc<N>
```

### 4. Create Star Files for Clusters

Parse poses and split by kmeans cluster labels:

```bash
# First extract config to get correct D and Apix values
python <skill-dir>/scripts/extract_config.py config.pkl

# Then parse with correct box size (D-1 from lattice_args)
# Use original pixel size: config['model_args']['Apix'] * config['dataset_args']['downfrac']
dsd parse_pose_star <particles.star> \
    -D <effective_box_size> \
    --Apix <original_apix> \
    --labels analyze.<epoch>/kmeans<numk>/labels.pkl \
    --outdir <outdir>
```

**Use specific epoch poses:** To use poses from a specific epoch (e.g., `pose.29.pkl`) instead of the original star file poses:

```bash
dsd parse_pose_star <particles.star> \
    -D <effective_box_size> \
    --Apix <original_apix> \
    --poses pose.<epoch>.pkl \
    --labels analyze.<epoch>/kmeans<numk>/labels.pkl \
    --outdir <outdir>
```

**Critical:** 
- The effective box size is `lattice_args['D'] - 1`, not the raw D value.
- The original pixel size is `config['model_args']['Apix'] * config['dataset_args']['downfrac']`. Do not use the raw `config['model_args']['Apix']` for `parse_pose_star` — that is the training-effective pixel size.

### 5. Combine Star Files

Merge multiple cluster star files:

```bash
# Two files
dsdsh combine_star pre9.star pre10.star combined.star

# Multiple files (chain commands)
dsdsh combine_star pre9.star pre10.star temp1.star
dsdsh combine_star temp1.star pre11.star temp2.star
dsdsh combine_star temp2.star pre12.star combined_9_10_11_12.star
```

### 6. Generate Pose Pickle for Combined/Any Star File

Convert a star file to pose pickle format:

```bash
dsd parse_pose_star <starfile> \
    -D <effective_box_size> \
    --Apix <original_apix> \
    -o <output_pose.pkl>
```

Example for combined clusters:
```bash
dsd parse_pose_star kmeans_pose/combined_9_10_11_12.star \
    -D <effective_box_size> \
    --Apix <original_apix> \
    -o kmeans_pose/combined_9_10_11_12_pose.pkl
```

### 7. Check Overlap Between Star Files

Use the skill's `exclude_stars.py` to check overlap between two star files based on 3D coordinates (within 136 Å threshold):

```bash
python <skill-dir>/scripts/exclude_stars.py <reference.star> <query.star>
```

Pixel size is auto-detected from `config.pkl` in the current directory.

Output:
- Prints overlap statistics for each micrograph
- Generates `<query>_exclude.star` with non-overlapping particles

Example workflow to check overlap of all cluster star files with a test set:
```bash
for f in kmeans_pose/pre*.star; do
    echo "=== Checking overlap for $f ==="
    python <skill-dir>/scripts/exclude_stars.py test_set.star "$f"
done
```

## Complete Workflow Example

Full pipeline from analysis to combined pose generation:

```bash
# 0. Extract config values (original_Apix, effective_box_size)
python <skill-dir>/scripts/extract_config.py config.pkl

# 1. Analyze epoch
dsdsh analyze . 39 10 20

# 2. Generate volumes for kmeans centers (positional dsdsh eval_vol — no manual --zfile)
dsdsh eval_vol . 39 kmeans 20 <original_apix>

# 3. Create star files for all clusters
dsd parse_pose_star <particles.star> -D <effective_box_size> --Apix <original_apix> \
    --labels analyze.39/kmeans20/labels.pkl --outdir kmeans_pose

# 4. Combine specific clusters
dsdsh combine_star kmeans_pose/pre9.star kmeans_pose/pre10.star temp.star
dsdsh combine_star temp.star kmeans_pose/pre11.star temp2.star
dsdsh combine_star temp2.star kmeans_pose/pre12.star \
    kmeans_pose/combined_9_10_11_12.star

# 5. Generate pose pickle for combined clusters
dsd parse_pose_star kmeans_pose/combined_9_10_11_12.star \
    -D <effective_box_size> --Apix <original_apix> -o kmeans_pose/combined_9_10_11_12_pose.pkl
```

## Directory Structure Convention

After analysis:
```
.
├── analyze.<epoch>/
│   ├── kmeans<numk>/
│   │   ├── centers.txt      # Latent codes for cluster centers
│   │   ├── centers.pkl      # Numpy array of centers
│   │   ├── labels.pkl       # Cluster assignment for each particle
│   │   ├── centers_ind.txt  # Particle indices closest to each center
│   │   └── pre<N>.star      # Star files per cluster
│   ├── pc<N>/
│   │   └── z_pc.txt         # Latent codes along PC trajectory
│   └── *.png                # Visualization plots
├── kmeans_volumes/          # Generated cluster center volumes
├── pc_volumes/              # Generated PC trajectory volumes
│   ├── pc1/
│   ├── pc2/
│   └── ...
└── kmeans_pose/             # Star files split by cluster
```

### 8. Deformation Analysis

When the model is trained with deformation/warp parameters (e.g., for rigid body motion), the `analyze` command outputs both conformation latent space (`analyze.<epoch>/`) and deformation latent space (`defanalyze.<epoch>/`) results in one shot:

```bash
dsdsh analyze <workdir> <epoch> <numpc> <numk>
```

Example:
```bash
dsdsh analyze . 39 10 30
```

Output:
- `analyze.39/` - Full conformation space (zdim-dimensional, e.g., 8-dim for the default ZDIM=8)
- `defanalyze.39/` - Deformation parameter space (config-dependent dimensions, e.g., 4-dim for 2-body deformation)

Both directories contain similar structures (`kmeans<numk>/`, `pc<N>/`, plots).

### 9. Generate Deformation Volumes Along PCs

Generate volumes with rigid body deformation along principal components:

```bash
# Step 1: Create template z-file from k-means cluster
cat > template_z17.txt << 'EOF'
2.718417 1.193066 1.568582 0.121429 0.286391 -4.490979 0.128237 0.110625 -0.278317 1.854208 -1.253814 0.234952
0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0
EOF

# Step 2: Generate deformation volumes
dsd eval_vol --load weights.<epoch>.pkl \
    -c config.pkl \
    -o defanalyze_volumes/pc<N> \
    --deform \
    --masks <path_to_mask_params.pkl> \
    --template-z template_z17.txt \
    --template-z-ind 0 \
    --zfile defanalyze.<epoch>/pc<N>/z_pc.txt \
    --Apix <apix> \
    --prefix reference
```

**Key parameters:**
- `--deform`: Enable deformation mode
- `--masks`: Path to `mask_params.pkl` containing rigid body definitions
- `--template-z`: Text file with base conformation z-values (N-dim from analyze, 2D format: rows × zdim)
- `--template-z-ind`: Index of template to use (0 for first row)
- `defanalyze.<epoch>/pc<N>/z_pc.txt`: Deformation parameters (M-dimensional, from defanalyze)

**Note on dimensions:** The template z-values and deformation z-values have different dimensions:
- Template (from analyze): Matches `config['model_args']['zdim']` (check with `extract_config.py`)
- Deformation (from defanalyze): Matches number of deformation parameters (typically num_bodies × 2 for rotation+translation)

### 10. Create Template from K-means Cluster

Extract a k-means center as template for deformation analysis:

```python
import pickle
import numpy as np

# Load from analyze (non-deformation) results
centers = pickle.load(open('analyze.<epoch>/kmeans<numk>/centers.pkl', 'rb'))
center_17 = centers[17]

# Save as 2D array (required format for --template-z)
np.savetxt('template_z17.txt', center_17.reshape(1, -1), fmt='%.6f')
```

**Important:** The template-z file must be 2D (rows × zdim). For a single template, save as (1, zdim) array where zdim matches your model configuration.

### 11. Analyze Mask Parameters

Inspect rigid body definitions in `mask_params.pkl`:

```python
import torch

m = torch.load('mask_params.pkl', map_location='cpu')
print('Keys:', list(m.keys()))
# Output: ['in_relatives', 'com_bodies', 'orient_bodies', 
#          'rotate_directions', 'radii_bodies', 'principal_axes']

# Check number of bodies
print('Number of bodies:', m['com_bodies'].shape[0])
print('COM of bodies:', m['com_bodies'])
print('Principal axes:', m['principal_axes'])
```

## Complete Deformation Workflow Example

Full pipeline for generating deformation volumes along PCs:

```bash
# 0. Extract config values
python <skill-dir>/scripts/extract_config.py config.pkl
# Note: zdim, original_Apix values

# 1. Run analysis (generates both analyze.39/ and defanalyze.39/)
dsdsh analyze . 39 10 30

# 2. Extract k-means center 17 as template (using Python)
#    Use the zdim from extract_config.py output
python3 << 'PYEOF'
import pickle
import numpy as np
centers = pickle.load(open('analyze.39/kmeans30/centers.pkl', 'rb'))
center_17 = centers[17]
zdim = len(center_17)
with open('template_z17.txt', 'w') as f:
    f.write(' '.join([f'{v:.6f}' for v in center_17]) + '\n')
    f.write(' '.join(['0.0'] * zdim) + '\n')
PYEOF

# 3. Generate deformation volumes for each PC
for pc in pc1 pc2 pc3 pc4; do
    mkdir -p defanalyze.39_volumes/$pc
    dsd eval_vol --load weights.39.pkl -c config.pkl \
        -o defanalyze.39_volumes/$pc \
        --deform --masks ../mask_params.pkl \
        --template-z template_z17.txt --template-z-ind 0 \
        --zfile defanalyze.39/$pc/z_pc.txt \
        --Apix <original_apix> --prefix reference
done
```

## Key Differences: analyze vs defanalyze Outputs

| Aspect | `analyze.<epoch>/` | `defanalyze.<epoch>/` |
|--------|-------------------|----------------------|
| Purpose | Full composition latent space | Deformation parameter latent space |
| z-dim | Model zdim (from config) | conformational zdim |
| Use with | Standard eval_vol | eval_vol --deform |
| Template needed | No | Yes (from analyze k-means) |

**Note:** Both are generated by a single `dsdsh analyze` command when the model has deformation parameters.

**Dimensionality Reference:**
```bash
# Check your model's zdim
python <skill-dir>/scripts/extract_config.py config.pkl
# Look for: zdim = config['model_args']['zdim']

```

