"""AI Clipper: long videos to captioned 9:16 clips."""
import os as _os

# Hugging Face's Xet download protocol can stall forever on some connections (seen with
# the mlx-community Whisper models). Classic HTTP downloads are reliable; must be set
# before huggingface_hub is imported.
_os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

__version__ = "0.2.0"
