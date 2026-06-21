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


def test_health_and_status():
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    s = c.get("/status")
    assert s.status_code == 200
    assert "claim_boundary" in s.json()


def test_api_protect_upload(monkeypatch, tmp_path):
    use_temp_outputs(monkeypatch, tmp_path)
    sample = tmp_path / "sample.png"
    Image.new("RGB", (96, 72), (90, 80, 120)).save(sample)
    c = TestClient(app)
    with sample.open("rb") as f:
        r = c.post("/api/protect", files={"artwork": ("sample.png", f, "image/png")}, data={"strength": "clean"})
    assert r.status_code == 200
    data = r.json()
    assert data["protection_score"] >= 80
    assert data["filename"].endswith(".png")
    verify = c.get(f"/verify/{data['filename']}")
    assert verify.status_code == 200
    assert verify.json()["valid"] is True
