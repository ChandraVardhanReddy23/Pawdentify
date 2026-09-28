# backend/app/preprocessing.py
"""
Framework-aware image preprocessing for dog breed classification.

WHY TWO PREPROCESSING PATHS?
-----------------------------
TensorFlow/Keras EfficientNetV2 models expect pixels scaled to [-1, 1] via
``tf.keras.applications.efficientnet_v2.preprocess_input``, delivered in NHWC
order (batch, height, width, channels).

PyTorch/timm EfficientNetV2 models expect ImageNet-standard normalization:
pixels scaled to [0, 1] then per-channel normalized with
mean=[0.485, 0.456, 0.406] and std=[0.229, 0.224, 0.225], delivered in NCHW
order (batch, channels, height, width).

Using the wrong normalization produces silently wrong predictions — the model
loads fine and returns confidences, but they're meaningless.  This module
routes to the correct path based on the model type so the caller doesn't need
to worry about it.
"""

from io import BytesIO
from pathlib import Path
from typing import Tuple

from PIL import Image
import numpy as np

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------
_TF_IMG_SIZE = (320, 320)  # default for Keras EfficientNetV2-B2

# ImageNet defaults used by timm — overridden at runtime from model.default_cfg
# when available, so these are just safe fallbacks.
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Cached torch model config (populated by load_model_from_path)
_torch_model_cfg: dict | None = None


# ===========================  TF preprocessing  ===========================

def preprocess_image_tf(image: Image.Image) -> np.ndarray:
    """
    Preprocess for a **Keras / TensorFlow** EfficientNetV2 model.

    Returns float32 array of shape ``(1, H, W, 3)`` (NHWC) after applying
    ``tf.keras.applications.efficientnet_v2.preprocess_input`` which scales
    pixels to [-1, 1].
    """
    import tensorflow as tf
    from tensorflow.keras.applications.efficientnet_v2 import preprocess_input

    if not isinstance(image, Image.Image):
        raise TypeError("Expected PIL.Image.Image")

    image = image.resize(_TF_IMG_SIZE, Image.BILINEAR)
    arr = np.asarray(image).astype("float32")  # 0..255, HWC

    if arr.ndim == 2:  # grayscale → RGB
        arr = np.stack((arr,) * 3, axis=-1)

    arr = np.expand_dims(arr, axis=0)  # (1, H, W, 3)
    arr = preprocess_input(arr)  # scales to [-1, 1]
    return arr


# =========================  Torch preprocessing  =========================

def _resolve_torch_cfg() -> Tuple[Tuple[int, int], np.ndarray, np.ndarray]:
    """
    Return ``(input_size_hw, mean, std)`` for the current torch model.

    Uses the cached ``_torch_model_cfg`` (set during ``load_model_from_path``)
    when available; otherwise falls back to ImageNet defaults at 260×260 (the
    timm default for ``tf_efficientnetv2_b2``).
    """
    if _torch_model_cfg is not None:
        # input_size is (C, H, W) in timm
        c, h, w = _torch_model_cfg.get("input_size", (3, 260, 260))
        mean = np.array(_torch_model_cfg.get("mean", _IMAGENET_MEAN), dtype=np.float32)
        std = np.array(_torch_model_cfg.get("std", _IMAGENET_STD), dtype=np.float32)
        return (h, w), mean, std

    return (260, 260), _IMAGENET_MEAN.copy(), _IMAGENET_STD.copy()


def preprocess_image_torch(image: Image.Image) -> np.ndarray:
    """
    Preprocess for a **PyTorch / timm** model.

    Returns float32 array of shape ``(1, 3, H, W)`` (NCHW) normalized with
    the model's own mean/std (read from ``model.default_cfg`` at load time).
    """
    if not isinstance(image, Image.Image):
        raise TypeError("Expected PIL.Image.Image")

    img_size, mean, std = _resolve_torch_cfg()

    image = image.resize(img_size, Image.BILINEAR)
    arr = np.asarray(image).astype("float32") / 255.0  # scale to [0, 1]

    if arr.ndim == 2:  # grayscale → RGB
        arr = np.stack((arr,) * 3, axis=-1)

    # Per-channel ImageNet normalization: (pixel - mean) / std
    arr = (arr - mean) / std

    # HWC → CHW, then add batch dim → (1, 3, H, W)
    arr = np.transpose(arr, (2, 0, 1))
    arr = np.expand_dims(arr, axis=0)
    return arr


# =======================  Public entry points  ============================

def preprocess_image(image: Image.Image, framework: str = "tf") -> np.ndarray:
    """
    Resize and normalize *image* for the given *framework* (``"tf"`` or ``"torch"``).

    Delegates to ``preprocess_image_tf`` or ``preprocess_image_torch``.
    """
    if framework == "torch":
        return preprocess_image_torch(image)
    return preprocess_image_tf(image)


def preprocess_image_bytes(file_bytes: bytes, framework: str = "tf") -> np.ndarray:
    """
    Convenience wrapper: decode raw bytes from an upload and return a
    model-ready array for the specified *framework*.
    """
    img = Image.open(BytesIO(file_bytes)).convert("RGB")
    return preprocess_image(img, framework=framework)


# ===========================  Prediction  =================================

def _is_probabilities(arr: np.ndarray, tol: float = 1e-3) -> bool:
    """Return True if *arr* looks like a probability distribution."""
    if np.any(arr < -tol):
        return False
    if np.any(arr > 1.0 + tol):
        return False
    return abs(float(arr.sum()) - 1.0) <= tol


def predict_top(model, img_array: np.ndarray, framework: str | None = None):
    """
    Run inference and return ``(top_index, top_probability)``.

    *framework* can be ``"tf"`` or ``"torch"``.  When ``None`` the function
    infers it from the model type (``tf.keras.Model`` → tf, else torch).

    For TF models the raw output is used directly (typically softmax).
    For torch/timm models the raw logits are converted via softmax.
    """
    import tensorflow as tf

    if framework is None:
        framework = "tf" if isinstance(model, tf.keras.Model) else "torch"

    if framework == "tf":
        raw = model.predict(img_array, verbose=0)
        preds = np.asarray(raw).squeeze()
    else:
        import torch

        model.eval()
        with torch.inference_mode():
            # img_array is already NCHW float32 from preprocess_image_torch
            input_tensor = torch.from_numpy(img_array)
            raw = model(input_tensor)
        preds = raw.detach().cpu().numpy().squeeze()

    # Scalar edge-case
    if preds.ndim == 0:
        preds = np.array([float(preds)])

    # Convert logits → probabilities when needed
    if _is_probabilities(preds):
        probs = preds.astype(float)
    else:
        # Numerically stable softmax
        maxv = np.max(preds)
        exp = np.exp(preds - maxv)
        probs = exp / np.sum(exp)

    top_idx = int(np.argmax(probs))
    top_prob = float(probs[top_idx])
    return top_idx, top_prob


# ===========================  Model loading  ==============================

# Mapping from classifier in_features → timm model name for EfficientNetV2 variants.
# Each variant has a unique final channel count, so we can auto-detect.
_EFFV2_VARIANTS = {
    1280: "tf_efficientnetv2_b0",
    1280: "tf_efficientnetv2_b1",  # b0 and b1 share 1280 — see fallback below
    1408: "tf_efficientnetv2_b2",
    1536: "tf_efficientnetv2_b3",
    1792: "tf_efficientnetv2_s",
    2560: "tf_efficientnetv2_m",
    3840: "tf_efficientnetv2_l",
}

# More precise lookup: try b1 first (it's more common at 1280 than b0)
_EFFV2_BY_FEATURES = {
    1408: "tf_efficientnetv2_b2",
    1536: "tf_efficientnetv2_b3",
    1792: "tf_efficientnetv2_s",
    2560: "tf_efficientnetv2_m",
    3840: "tf_efficientnetv2_l",
}


def _detect_effv2_variant(checkpoint: dict) -> str:
    """
    Inspect the checkpoint's ``classifier.weight`` shape to figure out which
    EfficientNetV2 variant was used during training.

    Falls back to ``tf_efficientnetv2_b2`` if detection fails.
    """
    if "classifier.weight" in checkpoint:
        num_classes, in_features = checkpoint["classifier.weight"].shape
        variant = _EFFV2_BY_FEATURES.get(in_features)
        if variant:
            print(f"[preprocessing] Auto-detected model variant: {variant} "
                  f"(classifier in_features={in_features}, num_classes={num_classes})")
            return variant
        # 1280 could be b0 or b1 — try b1 first (more common in fine-tuning)
        if in_features == 1280:
            print(f"[preprocessing] classifier in_features=1280 → trying tf_efficientnetv2_b1")
            return "tf_efficientnetv2_b1"

    print("[preprocessing] Could not detect variant from checkpoint, defaulting to tf_efficientnetv2_b2")
    return "tf_efficientnetv2_b2"


def load_model_from_path(model_path: str):
    """
    Load a model from *model_path*.

    Supports:
    * ``.keras`` / ``.h5`` → TensorFlow Keras model
    * ``.pth`` / ``.pt``   → PyTorch timm EfficientNetV2 (variant auto-detected
      from the checkpoint's classifier weight shape)

    For timm models the ``default_cfg`` is cached so that
    ``preprocess_image_torch`` can read the correct input size, mean, and std
    at inference time without the caller having to pass them explicitly.
    """
    global _torch_model_cfg

    p = Path(model_path)
    if not p.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")

    try:
        if p.suffix.lower() in {".pth", ".pt"}:
            import timm
            import torch

            checkpoint = torch.load(str(p), map_location="cpu", weights_only=True)

            # Auto-detect the correct architecture variant
            variant = _detect_effv2_variant(checkpoint)
            num_classes = checkpoint["classifier.weight"].shape[0] if "classifier.weight" in checkpoint else 120

            model = timm.create_model(
                variant,
                pretrained=False,
                num_classes=num_classes,
            )
            model.load_state_dict(checkpoint)
            model.eval()

            # Cache the model's expected preprocessing config
            _torch_model_cfg = getattr(model, "default_cfg", None) or {}
            print(f"[preprocessing] Torch model loaded.  variant={variant}, "
                  f"default_cfg input_size={_torch_model_cfg.get('input_size', 'N/A')}, "
                  f"mean={_torch_model_cfg.get('mean', 'N/A')}, "
                  f"std={_torch_model_cfg.get('std', 'N/A')}")
            return model

        import tensorflow as tf
        return tf.keras.models.load_model(str(p))

    except Exception as e:
        raise RuntimeError(f"Error loading model from {model_path}: {e}")

