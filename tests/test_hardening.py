import io
from pathlib import Path
from PIL import Image, PngImagePlugin

from nova_shield import protect_image, verify_protected_image, verify_robustness, verify_png_metadata, build_provenance_record


def sample(path: Path):
    img = Image.new('RGB', (180, 128), (40, 80, 120))
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            px[x,y] = ((x*3+y)%255, (x+y*2)%255, (x*2+y*3)%255)
    img.save(path)
    return path


def test_fortress_output_has_manifest_policy_watermark_and_quality(tmp_path):
    src = sample(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='fortress')
    assert res.visual_quality_score >= 88
    v = verify_protected_image(res.output_path, strict=True)
    assert v['metadata_valid'] is True
    assert v['policy_valid'] is True
    assert v['watermark_valid'] is True
    assert v['robustness_valid'] is True
    assert v['manifest_valid'] is True
    assert v['provenance_valid'] is True
    assert v['attack_resistance_score'] >= 90
    with Image.open(res.output_path) as img:
        assert img.text.get('Manifest-Hash')


def test_metadata_stripped_copy_still_passes_robustness_watermark(tmp_path):
    src = sample(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='fortress')
    rb = verify_robustness(res.output_path)
    assert rb['valid'] is True
    assert rb['variants']['metadata_stripped']['watermark_valid'] is True
    assert rb['variants']['resized']['watermark_valid'] is True
    assert rb['variants']['recompressed']['watermark_valid'] is True


def test_tampered_output_fails_provenance(tmp_path):
    src = sample(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='fortress')
    img = Image.open(res.output_path).convert('RGB')
    img.putpixel((0,0), (255,0,0))
    tampered = tmp_path/'out'/'tampered.png'
    img.save(tampered)
    v = verify_protected_image(tampered, strict=True)
    assert v['provenance_valid'] is False
    assert v['valid'] is False


def test_png_metadata_wrapper_compatible(tmp_path):
    src = sample(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='paranoid')
    m = verify_png_metadata(res.output_path)
    assert m['valid'] is True
    assert m['manifest_hash']


def test_build_provenance_record_public_function(tmp_path):
    src = sample(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='clean')
    rec = build_provenance_record(res.output_path, source_filename='art.png', preset='clean', protection_score=res.protection_score, visual_quality_score=res.visual_quality_score, seed=123, policy_fields={'AI-Training-Policy':'NO AI TRAINING'})
    assert rec['output_sha256']
    assert rec['manifest_hash']
