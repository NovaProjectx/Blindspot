from pathlib import Path
from PIL import Image
from nova_shield import protect_image, verify_protected_image, verify_robustness


def make_img(path: Path):
    img = Image.new('RGB', (180, 128), (70, 90, 150))
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            if (x-90)**2 + (y-58)**2 < 44**2:
                px[x,y] = ((x*2)%255, (y*3)%255, ((x+y)*2)%255)
    img.save(path)
    return path


def test_legacy_presets_are_shield_not_visible_payload(tmp_path):
    src = make_img(tmp_path/'art.png')
    for alias in ['noai', 'sentinel', 'blackout', 'paranoid', 'fortress']:
        res = protect_image(src, tmp_path/'out', preset_name=alias)
        v = verify_protected_image(res.output_path, strict=True)
        assert res.preset == 'shield'
        assert v['metadata_valid'] is True
        assert v['dct_watermark_valid'] is True
        assert v['semantic_payload_valid'] is False
        assert v['visual_quality_score'] >= 88


def test_robustness_for_shield(tmp_path):
    src = make_img(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='shield')
    rb = verify_robustness(res.output_path)
    assert rb['checked'] is True
    assert rb['passed'] >= 3
