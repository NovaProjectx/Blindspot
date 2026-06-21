from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image
import app.main as main
from app.main import app
from nova_shield import protect_image, verify_protected_image


def setup_tmp(monkeypatch, tmp_path):
    out = tmp_path / 'out'; jobs = out / 'jobs'; out.mkdir(); jobs.mkdir()
    monkeypatch.setattr(main, 'OUTPUT_DIR', out)
    monkeypatch.setattr(main, 'JOBS_DIR', jobs)
    monkeypatch.setattr(main, 'MANIFEST_PATH', out / 'manifest.jsonl')
    return out


def make_img(path: Path):
    img = Image.new('RGB', (160, 120), (70, 90, 140))
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            px[x,y] = ((x*3+y)%255, (x+y*2)%255, (x*2+y)%255)
    img.save(path)
    return path


def test_layer_report_and_registry_record(tmp_path):
    src = make_img(tmp_path/'art.png')
    res = protect_image(src, tmp_path/'out', preset_name='shield')
    v = verify_protected_image(res.output_path, strict=True)
    assert v['layers']['metadata_policy']['verified'] is True
    assert v['layers']['spatial_watermark']['verified'] is True
    assert v['layers']['dct_watermark']['verified'] is True
    assert v['layers']['provenance']['verified'] is True
    assert v['layers']['registry']['verified'] is True
    assert v['layers']['clip_attack']['active'] is False


def test_registry_verify_endpoint(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    src = make_img(tmp_path/'art.png')
    c = TestClient(app)
    with src.open('rb') as f:
        r = c.post('/api/protect', files={'artwork': ('art.png', f, 'image/png')})
    assert r.status_code == 200, r.text
    fn = r.json()['filename']
    path = main.OUTPUT_DIR / fn
    with path.open('rb') as f:
        vr = c.post('/api/registry/verify', files={'image': (fn, f, 'image/png')})
    assert vr.status_code == 200, vr.text
    data = vr.json()
    assert data['registered'] is True
    assert data['dct_watermark_valid'] is True
    assert 'layers' in data
