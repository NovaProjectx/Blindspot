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


def make_img(path: Path, fmt: str):
    img = Image.new('RGB', (96, 72), (80, 120, 160))
    img.save(path, format=fmt)
    return path


def test_common_formats(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    cases = [('png','PNG'), ('jpg','JPEG'), ('jpeg','JPEG'), ('jfif','JPEG'), ('webp','WEBP'), ('bmp','BMP'), ('tiff','TIFF'), ('gif','GIF')]
    c = TestClient(app)
    for ext, fmt in cases:
        p = make_img(tmp_path / f'sample.{ext}', fmt)
        with p.open('rb') as f:
            r = c.post('/api/protect', files={'artwork': (p.name, f, 'image/'+ext)}, data={'strength':'clean'})
        assert r.status_code == 200, (ext, r.text)
        data = r.json()
        assert data['metadata_valid'] is True
        assert data['download_url']


def test_api_key_required(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(main, 'OPTIONAL_API_KEY', 'secret-test-key')
    p = make_img(tmp_path / 'sample.png', 'PNG')
    c = TestClient(app)
    with p.open('rb') as f:
        r = c.post('/api/protect', files={'artwork': ('sample.png', f, 'image/png')})
    assert r.status_code == 401
    with p.open('rb') as f:
        r = c.post('/api/protect', headers={'X-Nova-Api-Key': 'secret-test-key'}, files={'artwork': ('sample.png', f, 'image/png')})
    assert r.status_code == 200
    monkeypatch.setattr(main, 'OPTIONAL_API_KEY', '')


def test_rate_limit_headers():
    c = TestClient(app)
    r = c.get('/status')
    assert 'X-RateLimit-Limit' in r.headers
    assert 'X-RateLimit-Remaining' in r.headers
    assert 'X-RateLimit-Reset' in r.headers


def test_job_record_export_endpoint(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    p = make_img(tmp_path / 'sample.png', 'PNG')
    c = TestClient(app)
    with p.open('rb') as f:
        r = c.post('/api/protect', files={'artwork': ('sample.png', f, 'image/png')})
    assert r.status_code == 200
    jid = r.json()['job_id']
    rec = c.get(f'/api/jobs/{jid}/record')
    assert rec.status_code == 200
    assert rec.content[:2] == b'\x1f\x8b'


def test_output_endpoints_apply_noai_headers(monkeypatch, tmp_path):
    out = setup_tmp(monkeypatch, tmp_path)
    p = make_img(tmp_path / 'sample.png', 'PNG')
    c = TestClient(app)
    with p.open('rb') as f:
        r = c.post('/api/protect', files={'artwork': ('sample.png', f, 'image/png')}, data={'strength': 'clean'})
    filename = r.json()['filename']
    download = c.get(f'/download/{filename}')
    assert download.status_code == 200
    assert 'noai' in download.headers['X-Robots-Tag']
    assert download.headers['Cache-Control'] == 'private, no-store'
    api_files = c.get('/api/files')
    assert api_files.status_code == 200
    assert 'noimageai' in api_files.headers['X-Robots-Tag']


def test_output_listing_requires_api_key_when_configured(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(main, 'OPTIONAL_API_KEY', 'secret-test-key')
    monkeypatch.setattr(main, 'REQUIRE_AUTH_FOR_OUTPUTS', True)
    c = TestClient(app)
    assert c.get('/api/files').status_code == 401
    assert c.get('/api/files', headers={'X-Nova-Api-Key': 'secret-test-key'}).status_code == 200
    monkeypatch.setattr(main, 'OPTIONAL_API_KEY', '')


def test_signed_download_rejects_expired_and_tampered_tokens(monkeypatch, tmp_path):
    setup_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(main, 'SIGNED_DOWNLOAD_SECRET', 'download-secret')
    p = make_img(tmp_path / 'sample.png', 'PNG')
    c = TestClient(app)
    with p.open('rb') as f:
        r = c.post('/api/protect', files={'artwork': ('sample.png', f, 'image/png')}, data={'strength': 'clean'})
    data = r.json()
    assert c.get(f"/download/{data['filename']}").status_code == 403
    assert c.get(data['download_url']).status_code == 200
    assert c.get(data['download_url'].replace('sig=', 'sig=x')).status_code == 403
    expired_sig = main._sign_value('download', data['filename'], 1)
    assert c.get(f"/download/{data['filename']}?expires=1&sig={expired_sig}").status_code == 403
    monkeypatch.setattr(main, 'SIGNED_DOWNLOAD_SECRET', '')
