from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main
from app.main import app


def use_temp_outputs(monkeypatch, tmp_path):
    out = tmp_path / 'outputs'
    out.mkdir()
    monkeypatch.setattr(main, 'OUTPUT_DIR', out)
    monkeypatch.setattr(main, 'MANIFEST_PATH', out / 'manifest.jsonl')
    return out


def test_metrics_endpoint_has_disk_info(monkeypatch, tmp_path):
    use_temp_outputs(monkeypatch, tmp_path)
    c = TestClient(app)
    r = c.get('/api/metrics')
    assert r.status_code == 200
    data = r.json()
    assert 'disk' in data
    assert 'free_bytes' in data['disk']
    assert 'recent_jobs' in data


def test_invalid_upload_rejected(monkeypatch, tmp_path):
    use_temp_outputs(monkeypatch, tmp_path)
    bad = tmp_path / 'fake.png'
    bad.write_text('not an image')
    c = TestClient(app)
    with bad.open('rb') as f:
        r = c.post('/api/protect', files={'artwork': ('fake.png', f, 'image/png')}, data={'strength': 'clean'})
    assert r.status_code == 400


def test_protect_writes_manifest(monkeypatch, tmp_path):
    use_temp_outputs(monkeypatch, tmp_path)
    sample = tmp_path / 'manifest_sample.png'
    Image.new('RGB', (96, 72), (30, 60, 90)).save(sample)
    c = TestClient(app)
    with sample.open('rb') as f:
        r = c.post('/api/protect', files={'artwork': ('manifest_sample.png', f, 'image/png')}, data={'strength': 'clean'})
    assert r.status_code == 200
    assert main.MANIFEST_PATH.exists()
    assert main.MANIFEST_PATH.read_text(errors='ignore').count('\n') >= 1
