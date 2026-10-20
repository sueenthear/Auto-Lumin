"""Per-process audio capture and voice-template matching."""

from .loopback import ProcessLoopback, check_supported
from .manager import AudioManager, default_template
from .template import VoiceMatcher, load_voice, save_voice, trim_voice_samples

__all__ = ["AudioManager", "ProcessLoopback", "VoiceMatcher", "check_supported", "default_template", "load_voice", "save_voice", "trim_voice_samples"]
