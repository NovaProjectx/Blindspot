from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
import app.main as main
from app.main import app
from nova_shield import protect_image, verify_protected_image, frequency_audit, build_protection_hash, build_record_hash


def setup_tmp(monkeypatch, tmp_path):
    out = tmp_path / 'out'; jobs = out / 'jobs'; out.mkdir(); jobs.mkdir()
    monkeypatch.setattr(main, 'OUTPUT_DIR', out)
    monkeypatch.setattr(main, 'JOBS_DIR', jobs)
    monkeypatch.setattr(main, 'MANIFEST_PATH', out / 'manifest.jsonl')
    return out


def make_img(path: Path):
    img = Image.new('RGB', (160,120), (80,90,130))
    px=img.load()
    for y in range(img.height):
        for x in range(img.width): px[x,y]=((x*2+y)%255,(x+y*3)%255,(x*3+y)%255)
    img.save(path); return path


def test_protection_hash_deterministic_and_record_hash_changes():
    h1 = build_protection_hash(pixel_sha256='abc', seed=123, preset='shield')
    h2 = build_protection_hash(pixel_sha256='abc', seed=123, preset='shield')
    h3 = build_protection_hash(pixel_sha256='abcd', seed=123, preset='shield')
    assert h1 == h2
    assert h1 != h3
    r1 = {'protection_hash': h1, 'timestamp': 'one'}
    r2 = {'protection_hash': h1, 'timestamp': 'two'}
    assert build_record_hash(r1) != build_record_hash(r2)


def test_frequency_audit_and_endpoint(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    src = make_img(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='shield')
    fa = frequency_audit(src, res.output_path)
    assert set(fa['relative_change']) == {'low','mid','high'}
    c=TestClient(app)
    with src.open('rb') as fo, res.output_path.open('rb') as fp:
        r=c.post('/api/frequency-audit', files={'original':('art.png',fo,'image/png'), 'protected':('protected.png',fp,'image/png')})
    assert r.status_code == 200
    assert 'relative_change' in r.json()


def test_verify_reports_protection_hash_layer(tmp_path):
    src = make_img(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='shield')
    v = verify_protected_image(res.output_path, strict=True)
    assert v['protection_hash_valid'] is True
    assert v['layers']['protection_hash']['verified'] is True


def test_concept_aversion_layer(tmp_path):
    src = make_img(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='shield')
    v = verify_protected_image(res.output_path, strict=True)
    assert 'concept_aversion' in v['layers']
    assert isinstance(v['layers']['concept_aversion']['active'], bool)
    assert isinstance(v['layers']['concept_aversion']['verified'], bool)
    assert isinstance(v['layers']['concept_aversion']['score'], int)
