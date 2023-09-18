# Blip Diffusion

Blip Diffusion was proposed in [BLIP-Diffusion: Pre-trained Subject Representation for Controllable Text-to-Image Generation and Editing
](https://arxiv.org/abs/2305.14720) by Dongxu Li, Junnan Li, Steven C.H. Hoi


The abstract from the paper is:

*Subject-driven text-to-image generation models create novel renditions of an input subject based on text prompts. Existing models suffer from lengthy fine-tuning and difficulties preserving the subject fidelity. To overcome these limitations, we introduce BLIP-Diffusion, a new subject-driven image generation model that supports multimodal control which consumes inputs of subject images and text prompts. Unlike other subject-driven generation models, BLIP-Diffusion introduces a new multimodal encoder which is pre-trained to provide subject representation. We first pre-train the multimodal encoder following BLIP-2 to produce visual representation aligned with the text. Then we design a subject representation learning task which enables a diffusion model to leverage such visual representation and generates new subject renditions. Compared with previous methods such as DreamBooth, our model enables zero-shot subject-driven generation, and efficient fine-tuning for customized subject with up to 20x speedup. We also demonstrate that BLIP-Diffusion can be flexibly combined with existing techniques such as ControlNet and prompt-to-prompt to enable novel subject-driven generation and editing applications. *

The original codebase can be found at [salesforce/LAVIS](https://github.com/salesforce/LAVIS/tree/main/projects/blip-diffusion).


## BlipDiffusionPipeline
[[autodoc]] BlipDiffusionPipeline
    - all
    - __call__

## BlipDiffusionControlNetPipeline
[[autodoc]] BlipDiffusionControlNetPipeline
    - all
    - __call__