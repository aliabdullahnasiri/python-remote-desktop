import pytest

from rdesktop.capture.screen import fit_dimensions
from rdesktop.config import CaptureSettings


def test_fit_dimensions_preserves_aspect_ratio():
    assert fit_dimensions(1920, 1080, 1280, 720) == (1280, 720)


def test_fit_dimensions_does_not_upscale():
    assert fit_dimensions(640, 480, 1280, 720) == (640, 480)


def test_fit_dimensions_handles_portrait():
    assert fit_dimensions(1080, 1920, 1280, 720) == (405, 720)


@pytest.mark.parametrize(
    "args",
    [
        (0, 100, 1280, 720),
        (100, 100, 0, 720),
    ],
)
def test_fit_dimensions_rejects_invalid_dimensions(args):
    with pytest.raises(ValueError):
        fit_dimensions(*args)


def test_low_bandwidth_profile_defaults():
    settings = CaptureSettings()
    settings.validate()
    assert settings.max_fps == 15
    assert settings.max_width == 1280
    assert settings.max_height == 720
