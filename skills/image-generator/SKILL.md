---
name: image-generator
description: Use this whenever you need to generate images from text prompts locally on Apple Silicon. Prefer over external APIs for batch or private generation.
type: procedure
role: media
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use
- Generating any image from a text description (editorial banners, illustrations, concept art, photos).
- When the user wants local/private image generation without external APIs.
- Running on Apple Silicon Macs with MPS support.

## Prerequisites

Run this preflight before the steps below. If anything is missing, surface the install hint and stop.

```bash
command -v python3 >/dev/null || { echo "missing: python3"; exit 1; }
```

This skill has an isolated Python environment managed by Nexus. After calling `skill_view(name="image-generator")`, use the `python.path` from the response — it includes `torch`, `diffusers`, and `Pillow`.

## Steps

1. **Run the generation script using the skill's Python:**

```bash
"$SKILL_PYTHON" -c "
import torch, os, sys
from diffusers import ErnieImagePipeline

print('Loading model...', flush=True)
pipe = ErnieImagePipeline.from_pretrained(
    'Baidu/ERNIE-Image-Turbo',
    torch_dtype=torch.bfloat16,
).to('mps')

print('Generating image...', flush=True)
image = pipe(
    prompt='<PROMPT HERE>',
    height=<HEIGHT>,
    width=<WIDTH>,
    num_inference_steps=8,
    guidance_scale=1.0,
).images[0]

output = os.path.expanduser('<OUTPUT PATH>')
image.save(output)
print(f'SAVED: {output}', flush=True)

from PIL import Image; import numpy as np
arr = np.array(image)
print(f'Size: {image.size}, Min: {arr.min()}, Max: {arr.max()}, Mean: {arr.mean():.1f}, Unique: {len(np.unique(arr))}', flush=True)
" > /tmp/ernie_gen.log 2>&1 &
```

2. **Poll for completion** -- use `sleep 90 && tail -5 /tmp/ernie_gen.log` then check if the output file exists. Generation takes ~1m20s-1m40s on MPS depending on resolution.

3. **Verify the image** -- check Min>0 and Max>0 to confirm it's not black. If all zeros, see Gotchas.

## Recommended resolutions (model-native)
- `1024x1024` -- square
- `848x1264` -- portrait
- `1264x848` -- landscape
- `768x1376` -- tall portrait
- `896x1200` -- portrait
- `1376x768` -- ~16:9 landscape (banners)
- `1200x896` -- landscape

Stick to these resolutions for best quality. Arbitrary sizes may produce artifacts.

## Parameters
- `num_inference_steps`: **8** (Turbo model, do not increase beyond ~12)
- `guidance_scale`: **1.0** (not 7.5 -- this is a distilled model)
- `torch_dtype`: **torch.bfloat16** (critical -- see Gotchas)

## Gotchas

- **float16 produces black images on MPS.** Always use `torch.bfloat16`. This was the #1 issue during testing.
- **GGUF files don't work on Mac.** The `unsloth/ERNIE-Image-Turbo-GGUF` files are for ComfyUI-GGUF which requires CUDA. Use `Baidu/ERNIE-Image-Turbo` via diffusers instead.
- **Python 3.9 is too old** for latest diffusers. Use a venv with Python 3.12+.
- **Timeout**: Generation takes ~80s-100s. Always run with `nohup` in background, then poll. Do NOT run inline with default 60s timeout.
- **Warning "invalid value encountered in cast"** with float16 = black output. Switch to bfloat16.
- **Warnings about "Unrecognized keys in rope_parameters"** and "Expected types for text_encoder"** are harmless -- ignore them.
- **Memory**: Model is ~8B params, ~16GB on disk. Ensure sufficient free RAM (32GB+ recommended) and disk space.
- **No need for HuggingFace token** -- the model is public (apache-2.0). Token only needed for gated models.
