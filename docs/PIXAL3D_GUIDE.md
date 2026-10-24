# Pixal3D Multi-View 3D Mesh Generation (ComfyUI)

## Overview
This setup runs **Tencent ARC's Pixal3D** multi-view 3D generation pipeline locally on your Apple Silicon Mac (M5) via **ComfyUI** with PyTorch Metal (MPS) hardware acceleration.

### How Pixal3D Multi-View Works
Instead of "guessing" hidden angles from a single picture, Pixal3D utilizes **pixel-aligned back-projection** across multiple perspectives. It lifts 2D image features directly into 3D voxel space, producing consistent geometry and realistic PBR textures on all sides.

---

## Quick Start

### 1. Launch ComfyUI
Run the startup script in terminal:
```bash
cd "/Users/linweizhi/Documents/CalHacks Project/ComfyUI"
./start_comfyui.sh
```
Open your browser to: **http://127.0.0.1:8188**

### 2. Load the Multi-View Workflow
You can load the workflow in two ways:
1. **From Templates:** Click **Templates** in the ComfyUI top menu -> **3D Model** -> **Pixal3D: Multi Views to 3D**.
2. **From File:** Click **Load** and select `user/default/workflows/3d_pixal3d_multi_views.json`.

---

## Input Image Guidelines (Best Results)

The `Pixal3DMultiViewConditioning` node uses a fixed orbit rig:
* **Angles:** Up to 4 orthogonal views:
  * **Front** ($0^\circ$)
  * **Left** ($90^\circ$)
  * **Back** ($180^\circ$)
  * **Right** ($270^\circ$)
  *(You can also feed fewer angles, e.g. Front + Back, or Front + Left + Back).*
* **Framing:**
  * Square aspect ratio ($1024 \times 1024$ recommended; ComfyUI auto-scales if needed).
  * The object should fill approximately $\sim 90\%$ of the frame height/width consistently across all views.
  * Transparent PNG or black background (the workflow also includes an integrated **BiRefNet** background removal node).

---

## Models Installed

All models are placed in `ComfyUI/models/`:
* **Diffusion Model (Fastest Quantized INT8):** `diffusion_models/pixal3d_multiview_int8_convrot.safetensors`
* **Vision Encoder:** `clip_vision/dino_v3_L_naf_fp32.safetensors`
* **Shape VAE:** `vae/trellis_2_shape_vae_bf16.safetensors`
* **Texture VAE:** `vae/trellis_2_texture_vae_bf16.safetensors`
* **Background Removal:** `background_removal/birefnet.safetensors`

---

## Performance & Optimization Notes (Apple Silicon M5)

1. **Quantization:** We configured the **INT8 ConvRot** checkpoint instead of the heavy BF16 checkpoint. This reduces memory usage by $50\%$ and accelerates inference by $\sim 40\%$.
2. **MPS Fallback:** `PYTORCH_ENABLE_MPS_FALLBACK=1` is configured inside `start_comfyui.sh` to ensure any specialized ops not yet ported to Metal seamlessly fallback to CPU without crashing.
3. **Memory Ceiling:** `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0` unlocks full utilization of your 32 GB unified memory.

---

## Exporting Your 3D Asset
* The end of the workflow contains **`SaveGLB`** and **`Preview3DAdvanced`** nodes.
* Outputs are saved in `ComfyUI/output/` as standard **`.glb`** files containing:
  * 3D Geometry / Topology
  * Diffuse / Albedo texture map
  * Normal maps & baked ambient occlusion
