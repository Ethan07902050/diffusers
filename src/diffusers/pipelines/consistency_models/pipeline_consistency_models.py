import inspect
from typing import List, Optional, Tuple, Union, Callable

import torch

from ...models import UNet2DConditionModel
from ...schedulers import KarrasDiffusionSchedulers
from ...utils import randn_tensor
from ..pipeline_utils import DiffusionPipeline, ImagePipelineOutput


def append_dims(x, target_dims):
    """Appends dimensions to the end of a tensor until it has target_dims dimensions."""
    dims_to_append = target_dims - x.ndim
    if dims_to_append < 0:
        raise ValueError(
            f"input has {x.ndim} dims but target_dims is {target_dims}, which is less"
        )
    return x[(...,) + (None,) * dims_to_append]


class ConsistencyModelPipeline(DiffusionPipeline):
    r"""
    TODO
    """
    def __init__(self, unet: UNet2DConditionModel, scheduler: KarrasDiffusionSchedulers) -> None:
        super().__init__()

        self.register_modules(
            unet=unet,
            scheduler=scheduler,
        )

        # Need to handle boundary conditions (e.g. c_skip, c_out, etc.) somewhere.
    
    # Copied from diffusers.pipelines.stable_diffusion.pipeline_stable_diffusion.StableDiffusionPipeline.prepare_extra_step_kwargs
    def prepare_extra_step_kwargs(self, generator, eta):
        # prepare extra kwargs for the scheduler step, since not all schedulers have the same signature
        # eta (η) is only used with the DDIMScheduler, it will be ignored for other schedulers.
        # eta corresponds to η in DDIM paper: https://arxiv.org/abs/2010.02502
        # and should be between [0, 1]

        accepts_eta = "eta" in set(inspect.signature(self.scheduler.step).parameters.keys())
        extra_step_kwargs = {}
        if accepts_eta:
            extra_step_kwargs["eta"] = eta

        # check if the scheduler accepts generator
        accepts_generator = "generator" in set(inspect.signature(self.scheduler.step).parameters.keys())
        if accepts_generator:
            extra_step_kwargs["generator"] = generator
        return extra_step_kwargs
    
    def get_sigma_min_max_from_scheduler(self):
        # Get sigma_min, sigma_max in original sigma space, not Karras sigma space
        # (e.g. not exponentiated by 1 / rho)
        if hasattr(self.scheduler, "sigma_min"):
            sigma_min = self.scheduler.sigma_min
            sigma_max = self.scheduler.sigma_max
        elif hasattr(self.scheduler, "sigmas"):
            # Karras-style scheduler e.g. (EulerDiscreteScheduler, HeunDiscreteScheduler)
            # Get sigma_min, sigma_max before they're converted into Karras sigma space by set_timesteps
            # TODO: Karras schedulers are inconsistent about how they initialize sigmas in __init__
            # For example, EulerDiscreteScheduler gets sigmas in original sigma space, but HeunDiscreteScheduler
            # initializes it through set_timesteps, which potentially leaves the sigmas in Karras sigma space.
            # TODO: For example, in EulerDiscreteScheduler, a value of 0.0 is appended to the sigmas whern initialized
            # in __init__. But wouldn't we usually want sigma_min to be a small positive number, following the
            # consistency models paper?
            # See e.g. https://github.com/openai/consistency_models/blob/main/scripts/launch.sh#L13
            sigma_min = self.scheduler.sigmas[-1].item()
            sigma_max = self.scheduler.sigmas[0].item()
        else:
            raise ValueError(
                f"Scheduler {self.scheduler.__class__} does not have sigma_min or sigma_max."
            )
        return sigma_min, sigma_max
    
    def get_sigmas_from_scheduler(self):
        if hasattr(self.scheduler, "sigmas"):
            # e.g. HeunDiscreteScheduler
            sigmas = self.scheduler.sigmas
        elif hasattr(self.scheduler, "schedule"):
            # e.g. KarrasVeScheduler
            sigmas = self.scheduler.schedule
        else:
            raise ValueError(
                f"Scheduler {self.scheduler.__class__} does not have sigmas."
            )
        return sigmas
    
    def get_scalings(self, sigma, sigma_data: float = 0.5):
        c_skip = sigma_data**2 / (sigma**2 + sigma_data**2)
        c_out = sigma * sigma_data / (sigma**2 + sigma_data**2) ** 0.5
        c_in = 1 / (sigma**2 + sigma_data**2) ** 0.5
        return c_skip, c_out, c_in
    
    def get_scalings_for_boundary_condition(sigma, sigma_min, sigma_data: float = 0.5):
        # sigma_min should be in original sigma space, not in karras sigma space
        # (e.g. not exponentiated by 1 / rho)
        c_skip = sigma_data**2 / (
            (sigma - sigma_min) ** 2 + sigma_data**2
        )
        c_out = (
            (sigma - sigma_min)
            * sigma_data
            / (sigma**2 + sigma_data**2) ** 0.5
        )
        c_in = 1 / (sigma**2 + sigma_data**2) ** 0.5
        return c_skip, c_out, c_in
    
    def denoise(self, x_t, sigma, sigma_min, sigma_data: float = 0.5, clip_denoised=True):
        """
        Run the consistency model forward...?
        """
        # sigma_min should be in original sigma space, not in karras sigma space
        # (e.g. not exponentiated by 1 / rho)
        c_skip, c_out, c_in = [
            append_dims(x, x_t.ndim)
            for x in self.get_scalings_for_boundary_condition(sigma, sigma_min, sigma_data=sigma_data)
        ]
        rescaled_t = 1000 * 0.25 * torch.log(sigma + 1e-44)
        model_output = self.unet(c_in * x_t, rescaled_t).sample
        denoised = c_out * model_output + c_skip * x_t
        if clip_denoised:
            denoised = denoised.clamp(-1, 1)
        return model_output, denoised
    
    def to_d(x, sigma, denoised):
        """Converts a denoiser output to a Karras ODE derivative."""
        return (x - denoised) / append_dims(sigma, x.ndim)
    
    @torch.no_grad()
    def __call__(
        self,
        batch_size: int = 1,
        num_inference_steps: int = 40,
        clip_denoised: bool = True,
        sigma_data: float = 0.5,
        eta: float = 0.0,
        generator: Optional[Union[torch.Generator, List[torch.Generator]]] = None,
        output_type: Optional[str] = "pil",
        return_dict: bool = True,
        callback: Optional[Callable[[int, int, torch.FloatTensor], None]] = None,
        callback_steps: int = 1,
    ):
        r"""
        Args:
            batch_size (`int`, *optional*, defaults to 1):
                The number of images to generate.
            eta (`float`, *optional*, defaults to 0.0):
                Corresponds to parameter eta (η) in the DDIM paper: https://arxiv.org/abs/2010.02502. Only applies to
                [`schedulers.DDIMScheduler`], will be ignored for others.
            generator (`torch.Generator`, *optional*):
                One or a list of [torch generator(s)](https://pytorch.org/docs/stable/generated/torch.Generator.html)
                to make generation deterministic.
            output_type (`str`, *optional*, defaults to `"pil"`):
                The output format of the generate image. Choose between
                [PIL](https://pillow.readthedocs.io/en/stable/): `PIL.Image.Image` or `np.array`.
            return_dict (`bool`, *optional*, defaults to `True`):
                Whether or not to return a [`~pipelines.ImagePipelineOutput`] instead of a plain tuple.
        Returns:
            [`~pipelines.ImagePipelineOutput`] or `tuple`: [`~pipelines.utils.ImagePipelineOutput`] if `return_dict` is
            True, otherwise a `tuple. When returning a tuple, the first element is a list with the generated images.
        """
        img_size = img_size = self.unet.config.sample_size
        shape = (batch_size, 3, img_size, img_size)
        device = self.device

        # 1. Sample image latents x_0 ~ N(0, sigma_0^2 * I)
        sample = randn_tensor(shape, generator=generator, device=device) * self.scheduler.init_noise_sigma

        # 2. Set timesteps and get sigmas
        # Get sigma_min, sigma_max in original sigma space (not Karras sigma space)
        sigma_min, sigma_max = self.get_sigma_min_max_from_scheduler()
        self.scheduler.set_timesteps(num_inference_steps)
        timesteps = self.scheduler.timesteps

        # Now get Karras sigma schedule (which I think the original implementation always uses)
        # See https://github.com/openai/consistency_models/blob/main/cm/karras_diffusion.py#L376
        sigmas = self.get_sigmas_from_scheduler()
        
        # 3. Prepare extra step kwargs. TODO: Logic should ideally just be moved out of the pipeline
        extra_step_kwargs = self.prepare_extra_step_kwargs(generator, eta)

        # 4. Denoising loop
        # TODO: hack, is there a better way to identify schedulers that implement the stochastic iterative sampling
        # similar to stochastic_iterative_sampler in the original code?
        if hasattr(self.scheduler, "add_noise_to_input"):
            # 4.1 Consistency Model Stochastic Iterative Scheduler (multi-step sampling)
            # First evaluate the consistency model. This will be the output sample if num_inference_steps == 1
            # TODO: not all schedulers have an index_for_timestep method (e.g. KarrasVeScheduler)
            step_idx = self.scheduler.index_for_timestep(timesteps[0])
            sigma = sigmas[step_idx]
            _, sample = self.denoise(sample, sigma_min, sigma_data=sigma_data, clip_denoised=clip_denoised)

            # If num_inference_steps > 1, perform multi-step sampling (stochastic_iterative_sampler)
            # Alternate adding noise and evaluating the consistency model on the noised input
            for i, t in self.progress_bar(enumerate(self.scheduler.timesteps[1:])):
                step_idx = self.scheduler.index_for_timestep(t)
                sigma = sigmas[step_idx]
                sigma_prev = sigmas[step_idx - 1]
                sample_hat, sigma_hat = self.scheduler.add_noise_to_input(sample, sigma, generator=generator)[0]

                model_output, denoised = self.denoise(
                    sample_hat, sigma, sigma_min, sigma_data=sigma_data, clip_denoised=clip_denoised
                )

                sample = self.scheduler.step(denoised, sigma_hat, sigma_prev, sample_hat).prev_sample
        elif hasattr(self.scheduler, "sigmas"):
            # 4.2 Karras-style scheduler in sigma space (e.g. HeunDiscreteScheduler)
            # TODO: warmup steps logic correct?
            num_warmup_steps = len(timesteps) - num_inference_steps * self.scheduler.order
            with self.progress_bar(total=num_inference_steps) as progress_bar:
                for i, t in enumerate(timesteps):
                    step_idx = self.scheduler.index_for_timestep(t)
                    sigma = self.scheduler.sigmas[step_idx]
                    # TODO: handle class labels?
                    # TODO: check shapes, might need equivalent of s_in in original code
                    # See e.g. https://github.com/openai/consistency_models/blob/main/cm/karras_diffusion.py#L510
                    model_output, denoised = self.denoise(
                        sample, sigma, sigma_min, sigma_data=sigma_data, clip_denoised=clip_denoised
                    )

                    # Karras-style schedulers already convert to a ODE derivative inside step()
                    sample = self.scheduler.step(denoised, t, sample, **extra_step_kwargs).prev_sample

                    # Note: differs from callback support in original code
                    # See e.g. https://github.com/openai/consistency_models/blob/main/cm/karras_diffusion.py#L459
                    # call the callback, if provided
                    if i == len(timesteps) - 1 or ((i + 1) > num_warmup_steps and (i + 1) % self.scheduler.order == 0):
                        progress_bar.update()
                        if callback is not None and i % callback_steps == 0:
                            callback(i, t, sample)
        else:
            raise ValueError(
                f"Scheduler {self.scheduler.__class__} is not compatible with consistency models."
            )
        
        # 5. Post-process image sample
        sample = (sample / 2 + 0.5).clamp(0, 1)
        sample = sample.cpu().permute(0, 2, 3, 1).numpy()

        if output_type == "pil":
            sample = self.numpy_to_pil(sample)
        
        if not return_dict:
            return (sample,)
        
        # TODO: Offload to cpu?

        return ImagePipelineOutput(images=sample)