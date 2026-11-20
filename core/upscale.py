import importlib.util
import sys
from pathlib import Path

import cv2
import torch
import numpy as np
from torchvision.transforms import functional as torchvision_functional

# BasicSR 1.4.2 imports a torchvision module removed from recent torchvision releases.
if importlib.util.find_spec("torchvision.transforms.functional_tensor") is None:
    sys.modules["torchvision.transforms.functional_tensor"] = torchvision_functional

from realesrgan import RealESRGANer
from basicsr.archs.rrdbnet_arch import RRDBNet

def build_upscaler(scale=2):
    model_path = Path(__file__).resolve().parents[1] / "models" / "RealESRGAN_x2plus.pth"
    if not model_path.is_file():
        raise FileNotFoundError(f"وزن Real-ESRGAN غير موجود: {model_path}")

    model = RRDBNet(
        num_in_ch=3, num_out_ch=3, num_feat=64,
        num_block=23, num_grow_ch=32, scale=scale
    )
    upsampler = RealESRGANer(
        scale=scale,
        model_path=str(model_path),
        model=model, 
        tile=400, 
        tile_pad=10,
        pre_pad=0, 
        half=torch.cuda.is_available(),
    )
    return upsampler


def build_face_restorer(scale=2):
    try:
        from gfpgan import GFPGANer
    except ImportError as error:
        raise RuntimeError(
            "حزمة GFPGAN غير مثبتة. ثبّتها باستخدام: pip install -r requirements.txt"
        ) from error

    model_path = Path(__file__).resolve().parents[1] / "models" / "GFPGANv1.4.pth"
    if not model_path.is_file():
        raise FileNotFoundError(
            "وزن GFPGAN غير موجود. ضع GFPGANv1.4.pth داخل مجلد models ثم أعد المحاولة."
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    return GFPGANer(
        model_path=str(model_path),
        upscale=scale,
        arch="clean",
        channel_multiplier=2,
        bg_upsampler=None,
        device=device,
    )