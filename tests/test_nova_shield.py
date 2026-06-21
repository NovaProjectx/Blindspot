from pathlib import Path
from PIL import Image
from nova_shield import METADATA_POLICY_FIELDS, NOVA_METADATA, PRESETS, protect_image, verify_png_metadata, verify_protected_image, normalize_preset_name


def make_sample(path: Path, fmt: str = "PNG") -> Path:
    img = Image.new("RGB", (128, 96), (45, 80, 130))
    pix = img.load()
    for y in range(img.height):
        for x in range(img.width):
            pix[x, y] = ((x * 3) % 255, (y * 4) % 255, (x + y) % 255)
    img.save(path, format=fmt)
    return path


def test_single_mode_only():
    assert "shield" in PRESETS
    for old in ["clean", "strong", "max", "paranoid", "fortress", "blackout", "sentinel", "noai", "noai_strict"]:
        assert normalize_preset_name(old) == "shield"


def test_outputs_png_metadata_watermark_dct_and_score(tmp_path):
    src = make_sample(tmp_path / "dragon_art.png")
    result = protect_image(src, tmp_path / "out", preset_name="fortress")
    assert result.preset == "shield"
    assert result.output_path.exists()
    assert result.output_path.suffix == ".png"
    assert result.protection_score >= PRESETS["shield"].target_score - 2
    verified = verify_protected_image(result.output_path, strict=True)
    assert verified["metadata_valid"] is True
    assert verified["policy_valid"] is True
    assert verified["watermark_valid"] is True
    assert verified["dct_watermark_valid"] is True
    assert verified["provenance_valid"] is True
    with Image.open(result.output_path) as img:
        assert img.format == "PNG"
        assert img.text.get("NovaProject") == NOVA_METADATA
        assert img.text.get("AI-Training-Policy") == "NO AI TRAINING"
        assert img.text.get("DCT-Watermark") == "enabled"
        assert img.text.get("Manifest-Hash")


def test_reads_common_formats_first_frame(tmp_path):
    formats = [("jpg", "JPEG"), ("jfif", "JPEG"), ("webp", "WEBP"), ("bmp", "BMP"), ("tiff", "TIFF"), ("gif", "GIF")]
    for ext, fmt in formats:
        src = make_sample(tmp_path / f"sample.{ext}", fmt=fmt)
        result = protect_image(src, tmp_path / "out", preset_name="clean", corner_label=False)
        assert result.preset == "shield"
        assert result.output_path.exists()
        with Image.open(result.output_path) as img:
            assert img.format == "PNG"
            assert img.text.get("NovaProject") == NOVA_METADATA


def test_visual_quality_near_invisible(tmp_path):
    src = make_sample(tmp_path / "sample.png")
    result = protect_image(src, tmp_path / "out", preset_name="shield", corner_label=False)
    assert isinstance(result.reached_threshold, bool)
    assert result.visual_quality_score >= 88
