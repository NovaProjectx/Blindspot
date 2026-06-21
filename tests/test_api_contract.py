from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image
import app.main as main
from app.main import app


def setup_tmp(monkeypatch, tmp_path):
    out = tmp_path / 'out'; jobs = out / 'jobs'; out.mkdir(); jobs.mkdir()
    monkeypatch.setattr(main, 'OUTPUT_DIR', out)
    monkeypatch.setattr(main, 'JOBS_DIR', jobs)
    monkeypatch.setattr(main, 'MANIFEST_PATH', out / 'manifest.jsonl')
    return out


def sample(tmp_path):
    p = tmp_path / 'sample.jpg'
    Image.new('RGB', (128, 96), (100, 50, 150)).save(p)
    return p


def post_with_field(field, path):
    c = TestClient(app)
    with path.open('rb') as f:
        return c.post('/api/protect', files={field: ('sample.jpg', f, 'image/jpeg')}, data={'strength': 'clean'})


def test_accepts_artwork_file_and_image_fields(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    p = sample(tmp_path)
    for field in ['artwork', 'file', 'image']:
        r = post_with_field(field, p)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data['ok'] is True
        assert data['job_id'].startswith('job_')
        assert data['download_url'].startswith('/download/')
        assert data['preview_url'].startswith('/download/')
        assert data['verify_url'].startswith('/verify/')
        assert data['metadata_valid'] is True
        assert data['preset_target'] >= 80


def test_async_job_api(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    p = sample(tmp_path)
    c = TestClient(app)
    with p.open('rb') as f:
        r = c.post('/api/jobs', files={'artwork': ('sample.jpg', f, 'image/jpeg')}, data={'strength': 'clean'})
    assert r.status_code == 200, r.text
    jid = r.json()['job_id']
    status = c.get(f'/api/jobs/{jid}')
    assert status.status_code == 200
    # TestClient runs background tasks before returning response.
    assert status.json()['status'] in {'queued', 'processing', 'done'}
    # If done, download and verify are available.
    if status.json()['status'] == 'done':
        assert c.get(f'/api/jobs/{jid}/download').status_code == 200
        assert c.get(f'/api/jobs/{jid}/verify').json()['valid'] is True


def test_bad_upload_error_contract(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    bad = tmp_path / 'bad.png'; bad.write_text('nope')
    c = TestClient(app)
    with bad.open('rb') as f:
        r = c.post('/api/protect', files={'file': ('bad.png', f, 'image/png')})
    assert r.status_code == 400
    assert r.json()['detail']['ok'] is False
    assert 'supported_types' in r.json()['detail']
