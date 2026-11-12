"""Phase 3: verify frame sizing and local H.264 MP4 output."""

from pathlib import Path

import pytest
from PIL import Image

from rdesktop.codec.video_encoder import H264Encoder


def test_rejects_invalid_config(tmp_path: Path):
    with pytest.raises(ValueError, match="FPS"):
        H264Encoder(tmp_path / "test.mp4", fps=0)
    with pytest.raises(ValueError, match="Bitrate"):
        H264Encoder(tmp_path / "test.mp4", bitrate=10)
    with pytest.raises(ValueError, match=".mp4"):
        H264Encoder(tmp_path / "test.avi")


def test_h264_mp4_is_decodable(tmp_path: Path):
    av = pytest.importorskip("av", reason="PyAV is required for the integration test")
    try:
        av.Codec("libx264", "w")
    except av.codec.codec.UnknownCodecError:
        pytest.skip("libx264 not available in this PyAV build")

    out = tmp_path / "test.mp4"
    with H264Encoder(out, fps=15, bitrate=500_000) as encoder:
        for i in range(15):
            image = Image.new("RGB", (321, 181), (i * 9, 55, 160))
            encoder.write(image)
        assert encoder.frames_written == 15
        assert encoder.width == 320
        assert encoder.height == 180

    assert out.is_file() and out.stat().st_size > 0
    with av.open(str(out)) as video:
        assert video.streams.video[0].codec_context.name == "h264"
        assert video.streams.video[0].width == 320
        assert video.streams.video[0].height == 180
        frames = list(video.decode(video=0))
        assert len(frames) == 15
