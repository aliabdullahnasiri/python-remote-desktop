"""Application defaults and capture settings."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CaptureSettings:
    """Low-bandwidth screen capture profile."""

    max_fps: int = 15
    max_width: int = 1280
    max_height: int = 720
    jpeg_quality: int = 80

    def validate(self) -> None:
        if not 1 <= self.max_fps <= 15:
            raise ValueError("max_fps must be between 1 and 15 for this profile.")
        if self.max_width < 1 or self.max_height < 1:
            raise ValueError("Maximum dimensions must be positive.")
        if not 1 <= self.jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be between 1 and 95.")
