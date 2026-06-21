from pathlib import Path
from PIL import Image, ImageEnhance
from fastapi.testclient import TestClient

from app.main import app
from nova_shield import compare_derivative, build_evidence_package, protect_image, verify_protected_image


def make_img(path: Path, color=(120,90,160)):
    img = Image.new('RGB', (160, 120), color)
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            if (x-80)**2 + (y-60)**2 < 40**2:
                px[x,y] = ((x*2)%255, (y*3)%255, ((x+y)*2)%255)
    img.save(path)
    return path


def test_compare_derivative_and_evidence(tmp_path):
    original = make_img(tmp_path/'original.png')
    suspect_img = Image.open(original).resize((140, 105)).resize((160,120))
    suspect = tmp_path/'suspect.png'
    ImageEnhance.Color(suspect_img).enhance(1.2).save(suspect)
    report = compare_derivative(original, suspect)
    assert report['derivative_score'] > 60
    pkg = build_evidence_package(original, suspect, tmp_path/'evidence')
    assert pkg.exists() and pkg.suffix == '.gz'


def test_blackout_semantic_layer_and_verify(tmp_path):
    src = make_img(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='blackout')
    v = verify_protected_image(res.output_path, strict=True)
    assert v['metadata_valid'] is True
    assert v['watermark_valid'] is True
    assert v['visual_quality_score'] >= 70


def test_compare_api(tmp_path):
    original = make_img(tmp_path/'original.png')
    suspect = make_img(tmp_path/'suspect.png', color=(121,91,161))
    c=TestClient(app)
    with original.open('rb') as fo, suspect.open('rb') as fs:
        r=c.post('/api/compare', files={'original':('original.png',fo,'image/png'), 'suspect':('suspect.png',fs,'image/png')})
    assert r.status_code == 200, r.text
    assert 'derivative_score' in r.json()


def test_evidence_api(tmp_path):
    original = make_img(tmp_path/'original.png')
    suspect = make_img(tmp_path/'suspect.png', color=(121,91,161))
    c=TestClient(app)
    with original.open('rb') as fo, suspect.open('rb') as fs:
        r=c.post('/api/evidence', files={'original':('original.png',fo,'image/png'), 'suspect':('suspect.png',fs,'image/png')})
    assert r.status_code == 200, r.text
    assert r.json()['evidence_url'].startswith('/api/evidence/')
