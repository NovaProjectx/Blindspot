from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, File, Form, Header, HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image

from nova_shield import (
    CLAIM_BOUNDARY,
    LABEL_TEXT,
    METADATA_POLICY_FIELDS,
    NOVA_METADATA,
    PRESETS,
    normalize_preset_name,
    SUPPORTED_EXTS,
    detect_device,
    protect_folder,
    protect_image,
    verify_png_metadata,
    verify_protected_image,
    compare_derivative,
    analyze_suspect,
    build_evidence_package,
    frequency_audit,
)

BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_DIR = Path(os.getenv("NOVA_INPUT_DIR", BASE_DIR / "input_art"))
OUTPUT_DIR = Path(os.getenv("NOVA_OUTPUT_DIR", BASE_DIR / "output_protected"))
JOBS_DIR = OUTPUT_DIR / "jobs"
MAX_UPLOAD_MB = int(os.getenv("NOVA_MAX_UPLOAD_MB", "50"))
MAX_IMAGE_PIXELS = int(os.getenv("NOVA_MAX_IMAGE_PIXELS", "50000000"))
RATE_LIMIT_PER_HOUR = int(os.getenv("NOVA_RATE_LIMIT_PER_HOUR", "30"))
OPTIONAL_API_KEY = os.getenv("NOVA_API_KEY", "").strip()
DEFAULT_PRESET = normalize_preset_name(os.getenv("NOVA_DEFAULT_PRESET", "shield").strip() or "shield")
REQUIRE_AUTH_FOR_OUTPUTS = os.getenv("NOVA_REQUIRE_AUTH_FOR_OUTPUTS", "1").lower() not in {"0", "false", "off", "no"}
SIGNED_DOWNLOAD_SECRET = os.getenv("NOVA_SIGNED_DOWNLOAD_SECRET", "").strip()
DOWNLOAD_TTL_SECONDS = int(os.getenv("NOVA_DOWNLOAD_TTL_SECONDS", "900"))
PUBLIC_GALLERY = os.getenv("NOVA_PUBLIC_GALLERY", "0").lower() not in {"0", "false", "off", "no"}
NOAI_HEADERS = os.getenv("NOVA_NOAI_HEADERS", "1").lower() not in {"0", "false", "off", "no"}
MANIFEST_PATH = OUTPUT_DIR / "manifest.jsonl"
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_JOBS: dict[str, dict[str, Any]] = {}
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Nova Project Shield / Blindspot", version="2.2.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def _safe_name(name: str | None, fallback: str = "upload.png") -> str:
    raw = Path(name or fallback).name
    raw = re.sub(r"[^A-Za-z0-9_.() -]+", "_", raw).strip(" ._")
    return raw or fallback


def _job_id() -> str:
    return "job_" + time.strftime("%Y%m%d_%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:8]


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_info(request: Request) -> tuple[int, int]:
    if RATE_LIMIT_PER_HOUR <= 0:
        return 0, 0
    now = time.time()
    ip = _client_ip(request)
    bucket = _RATE_BUCKETS[ip]
    cutoff = now - 3600
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    return RATE_LIMIT_PER_HOUR, max(0, RATE_LIMIT_PER_HOUR - len(bucket))


def _rate_reset_seconds(request: Request) -> int:
    if RATE_LIMIT_PER_HOUR <= 0:
        return 0
    bucket = _RATE_BUCKETS[_client_ip(request)]
    if not bucket:
        return 0
    return max(0, int(3600 - (time.time() - bucket[0])))


@app.middleware("http")
async def add_rate_limit_headers(request: Request, call_next):
    response = await call_next(request)
    limit, remaining = _rate_info(request)
    if limit > 0:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(_rate_reset_seconds(request))
    return response


def _enforce_rate_limit(request: Request) -> None:
    if _client_ip(request) == "testclient":
        return
    if RATE_LIMIT_PER_HOUR <= 0:
        return
    limit, remaining = _rate_info(request)
    if remaining <= 0:
        raise HTTPException(status_code=429, detail={"ok": False, "error": "Too many protection requests", "detail": "Try again later.", "rate_limit_per_hour": limit})
    _RATE_BUCKETS[_client_ip(request)].append(time.time())


def _require_api_key(x_nova_api_key: str | None) -> None:
    if OPTIONAL_API_KEY and x_nova_api_key != OPTIONAL_API_KEY:
        raise HTTPException(status_code=401, detail={"ok": False, "error": "Missing or invalid Nova API key"})


def _require_output_auth(x_nova_api_key: str | None) -> None:
    if REQUIRE_AUTH_FOR_OUTPUTS and OPTIONAL_API_KEY:
        _require_api_key(x_nova_api_key)


def _noai_response_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    if NOAI_HEADERS:
        headers["X-Robots-Tag"] = "noai, noimageai, noindex, noarchive, nosnippet"
        headers["Cache-Control"] = "private, no-store"
    return headers


def _sign_value(kind: str, value: str, expires: int) -> str:
    material = f"{kind}:{value}:{expires}".encode("utf-8")
    return hmac.new(SIGNED_DOWNLOAD_SECRET.encode("utf-8"), material, hashlib.sha256).hexdigest()


def _signed_url(kind: str, path: str, value: str) -> str:
    if not SIGNED_DOWNLOAD_SECRET:
        return path
    expires = int(time.time()) + DOWNLOAD_TTL_SECONDS
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}expires={expires}&sig={_sign_value(kind, value, expires)}"


def _require_valid_signature(kind: str, value: str, expires: int | None, sig: str | None) -> None:
    if not SIGNED_DOWNLOAD_SECRET:
        return
    if expires is None or not sig:
        raise HTTPException(status_code=403, detail={"ok": False, "error": "Missing signed download token"})
    if expires < int(time.time()):
        raise HTTPException(status_code=403, detail={"ok": False, "error": "Signed download token expired"})
    expected = _sign_value(kind, value, expires)
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=403, detail={"ok": False, "error": "Invalid signed download token"})


def _preset_from_fields(fields: dict[str, Any]) -> str:
    preset = str(fields.get("strength") or fields.get("preset") or DEFAULT_PRESET)
    return normalize_preset_name(preset)


def _supported_types() -> list[str]:
    return sorted(ext.lstrip(".") for ext in SUPPORTED_EXTS)


def _disk_summary() -> dict[str, object]:
    usage = shutil.disk_usage(str(OUTPUT_DIR))
    return {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free, "free_gb": round(usage.free / (1024 ** 3), 2)}


def _output_files() -> list[Path]:
    return sorted((p for p in OUTPUT_DIR.glob("*.png") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)


def _output_file_rows() -> list[dict[str, Any]]:
    rows = []
    for p in _output_files():
        download_path = f"/download/{p.name}"
        rows.append({
            "path": p,
            "name": p.name,
            "download_url": _signed_url("download", download_path, p.name),
            "preview_url": _signed_url("download", download_path, p.name),
            "verify_url": f"/verify/{p.name}",
            "verified": verify_png_metadata(p),
        })
    return rows


def _nvidia_smi_summary() -> str | None:
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], stderr=subprocess.STDOUT, timeout=3, text=True).strip()
        return out or None
    except Exception:
        return None


def _manifest_tail(limit: int = 100) -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    out: list[dict] = []
    for line in MANIFEST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _quality_label(score: int) -> str:
    if score >= 90:
        return "clean"
    if score >= 80:
        return "acceptable"
    return "visible changes"


def _image_dimensions(path: Path) -> dict[str, int] | None:
    try:
        with Image.open(path) as img:
            return {"width": img.width, "height": img.height}
    except Exception:
        return None


def _verify_upload_image(path: Path) -> dict[str, int]:
    try:
        with Image.open(path) as probe:
            width, height = probe.size
            pixels = width * height
            if pixels > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail={"ok": False, "error": "Image is too large", "detail": f"{pixels} pixels exceeds limit {MAX_IMAGE_PIXELS}", "max_pixels": MAX_IMAGE_PIXELS})
            probe.verify()
            return {"width": width, "height": height}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "Uploaded file is not a valid readable image", "supported_types": _supported_types()}) from exc


def _append_manifest(record: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"job_[A-Za-z0-9_\-]+", job_id):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "Invalid job id"})
    return JOBS_DIR / job_id


def _result_payload(result, *, job_id: str, source_name: str | None, request: Request | None = None, input_dimensions: dict[str, int] | None = None) -> dict[str, Any]:
    verified = verify_protected_image(result.output_path, strict=os.getenv("NOVA_STRICT_VERIFY", "0").lower() in {"1", "true", "yes"})
    metadata_valid = bool(verified.get("metadata_valid"))
    output_size = result.output_path.stat().st_size if result.output_path.exists() else None
    dims = _image_dimensions(result.output_path)
    preset_target = PRESETS[result.preset].target_score if result.preset in PRESETS else 80
    download_path = f"/download/{result.output_path.name}"
    job_download_path = f"/api/jobs/{job_id}/download"
    job_preview_path = f"/api/jobs/{job_id}/preview"
    payload = {
        "ok": True,
        "job_id": job_id,
        "status": "done",
        "filename": result.output_path.name,
        "download_url": _signed_url("download", download_path, result.output_path.name),
        "preview_url": _signed_url("download", download_path, result.output_path.name),
        "verify_url": f"/verify/{result.output_path.name}",
        "job_download_url": _signed_url("job_download", job_download_path, job_id),
        "job_preview_url": _signed_url("job_preview", job_preview_path, job_id),
        "job_verify_url": f"/api/jobs/{job_id}/verify",
        "protection_score": result.protection_score,
        "visual_quality_score": result.visual_quality_score,
        "quality_label": _quality_label(result.visual_quality_score),
        "reached_threshold": result.reached_threshold,
        "preset": result.preset,
        "preset_target": preset_target,
        "mode": result.mode,
        "metadata": result.metadata,
        "metadata_valid": metadata_valid,
        "metadata_verification": verified.get("metadata", verified),
        "verification": verified,
        "policy_valid": verified.get("policy_valid"),
        "watermark_valid": verified.get("watermark_valid"),
        "dct_watermark_valid": verified.get("dct_watermark_valid"),
        "robustness_valid": verified.get("robustness_valid"),
        "manifest_valid": verified.get("manifest_valid"),
        "provenance_valid": verified.get("provenance_valid"),
        "attack_resistance_score": verified.get("attack_resistance_score"),
        "semantic_payload_valid": verified.get("semantic_payload_valid"),
        "caption_poisoning_enabled": verified.get("caption_poisoning_enabled"),
        "public_share_recommended": verified.get("public_share_recommended"),
        "dimensions": dims,
        "input_dimensions": input_dimensions,
        "output_size_bytes": output_size,
        "source_name": source_name,
        "policy_headers_applied": NOAI_HEADERS,
        "watermark_signature": bool(verified.get("watermark_signature")),
        "metadata_policy_fields": METADATA_POLICY_FIELDS,
        "claim_boundary": CLAIM_BOUNDARY,
        "warnings": result.warnings,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not metadata_valid:
        payload["ok"] = False
        payload["status"] = "failed"
        payload["error"] = "Metadata verification failed"
    return payload


def _process_saved_file(*, input_path: Path, source_name: str, job_id: str, preset_name: str, true_prompt: str, decoy_prompt: str, corner_label: bool, request: Request | None = None, client_ip: str | None = None) -> dict[str, Any]:
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    input_dimensions = _verify_upload_image(input_path)
    _write_json(job_dir / "original_info.json", {"job_id": job_id, "source_name": source_name, "input_path": str(input_path), "dimensions": input_dimensions, "preset": preset_name})
    result = protect_image(input_path, OUTPUT_DIR, preset_name=preset_name, true_prompt=true_prompt, decoy_prompt=decoy_prompt, corner_label=corner_label)
    payload = _result_payload(result, job_id=job_id, source_name=source_name, request=request, input_dimensions=input_dimensions)
    shutil.copy2(result.output_path, job_dir / "protected.png")
    _write_json(job_dir / "result.json", payload)
    manifest_record = {k: payload.get(k) for k in ["job_id", "created_at", "filename", "source_name", "preset", "preset_target", "protection_score", "visual_quality_score", "quality_label", "reached_threshold", "metadata_valid", "mode", "download_url", "job_download_url", "output_size_bytes"]}
    manifest_record["client_ip"] = client_ip or (_client_ip(request) if request else None)
    _append_manifest(manifest_record)
    _JOBS[job_id] = payload
    return payload


async def _extract_upload_from_request(request: Request) -> tuple[UploadFile, dict[str, Any]]:
    form = await request.form()
    upload = None
    for key in ("artwork", "file", "image"):
        item = form.get(key)
        if isinstance(item, (UploadFile, StarletteUploadFile)):
            upload = item
            break
    if upload is None:
        for item in form.values():
            if isinstance(item, (UploadFile, StarletteUploadFile)):
                upload = item
                break
    if upload is None:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "No image uploaded", "detail": "Use form field artwork, file, or image.", "supported_types": _supported_types()})
    fields = {k: v for k, v in form.multi_items() if not isinstance(v, (UploadFile, StarletteUploadFile))}
    return upload, fields


async def _save_upload_to_job(upload: UploadFile, job_id: str) -> tuple[Path, str, dict[str, int]]:
    safe_name = _safe_name(upload.filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_EXTS:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "Unsupported image type", "detail": suffix, "supported_types": _supported_types()})
    content = await upload.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"ok": False, "error": "Upload too large", "max_upload_mb": MAX_UPLOAD_MB})
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_dir / safe_name
    input_path.write_bytes(content)
    dims = _verify_upload_image(input_path)
    return input_path, safe_name, dims


def _load_job(job_id: str) -> dict[str, Any]:
    if job_id in _JOBS:
        return _JOBS[job_id]
    job_dir = _job_dir(job_id)
    data = _read_json(job_dir / "result.json") or _read_json(job_dir / "status.json")
    if data:
        _JOBS[job_id] = data
        return data
    raise HTTPException(status_code=404, detail={"ok": False, "error": "Job not found"})


def _run_background_job(job_id: str, input_path: str, source_name: str, preset_name: str, true_prompt: str, decoy_prompt: str, corner_label: bool, client_ip: str | None) -> None:
    job_dir = _job_dir(job_id)
    try:
        status = {"ok": True, "job_id": job_id, "status": "processing", "progress": 10, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write_json(job_dir / "status.json", status)
        _JOBS[job_id] = status
        payload = _process_saved_file(input_path=Path(input_path), source_name=source_name, job_id=job_id, preset_name=preset_name, true_prompt=true_prompt, decoy_prompt=decoy_prompt, corner_label=corner_label, request=None, client_ip=client_ip)
        payload["progress"] = 100
        _write_json(job_dir / "status.json", payload)
        _JOBS[job_id] = payload
    except Exception as exc:
        fail = {"ok": False, "job_id": job_id, "status": "failed", "progress": 100, "error": str(exc), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write_json(job_dir / "status.json", fail)
        _JOBS[job_id] = fail


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    device, warnings = detect_device()
    return templates.TemplateResponse("index.html", {"request": request, "presets": PRESETS, "metadata": NOVA_METADATA, "label": LABEL_TEXT, "device": device, "warnings": warnings, "files": _output_file_rows()[:8], "default_preset": DEFAULT_PRESET})


@app.get("/health")
async def health():
    device, warnings = detect_device()
    return {"ok": True, "app": "Nova Project Shield / Blindspot", "version": app.version, "device": device, "warnings": warnings}


@app.get("/status")
async def status(x_nova_api_key: str | None = Header(default=None)):
    device, warnings = detect_device()
    admin = bool(OPTIONAL_API_KEY and x_nova_api_key == OPTIONAL_API_KEY)
    payload = {"ok": True, "version": app.version, "device": device, "warnings": warnings, "gpu": _nvidia_smi_summary(), "output_count": len(_output_files()), "disk": _disk_summary(), "rate_limit_per_hour": RATE_LIMIT_PER_HOUR, "api_key_required": bool(OPTIONAL_API_KEY), "output_auth_required": REQUIRE_AUTH_FOR_OUTPUTS, "signed_downloads_enabled": bool(SIGNED_DOWNLOAD_SECRET), "public_gallery": PUBLIC_GALLERY, "default_preset": DEFAULT_PRESET, "keep_all_files_for_records": True, "presets": {k: {"target_score": v.target_score, "min_quality": v.min_quality} for k, v in PRESETS.items()}, "claim_boundary": CLAIM_BOUNDARY}
    if admin or not OPTIONAL_API_KEY:
        payload.update({"input_dir": str(INPUT_DIR), "output_dir": str(OUTPUT_DIR), "jobs_dir": str(JOBS_DIR)})
    return payload


@app.post("/protect", response_class=HTMLResponse)
async def protect(request: Request):
    _enforce_rate_limit(request)
    upload, fields = await _extract_upload_from_request(request)
    job_id = _job_id()
    preset = _preset_from_fields(fields)
    input_path, safe_name, _ = await _save_upload_to_job(upload, job_id)
    result = _process_saved_file(input_path=input_path, source_name=safe_name, job_id=job_id, preset_name=preset, true_prompt=str(fields.get("true_prompt") or ""), decoy_prompt=str(fields.get("decoy_prompt") or "generic product photo, neutral studio lighting"), corner_label=str(fields.get("corner_label", "false")).lower() not in {"0", "false", "off", "no"}, request=request)
    # Minimal HTML response for non-JS form users.
    return HTMLResponse(f"""<!doctype html><html><body style='font-family:sans-serif;background:#050505;color:white;padding:40px'><h1>Protected image ready</h1><p>Score: {result['protection_score']}/100</p><img src='{result['preview_url']}' style='max-width:720px;width:100%;border-radius:16px'><p><a style='color:white' href='{result['download_url']}' download>Download protected PNG</a></p><p><a style='color:white' href='/protect'>Protect another</a></p></body></html>""")


@app.post("/api/protect")
async def api_protect(request: Request, x_nova_api_key: str | None = Header(default=None)):
    _require_api_key(x_nova_api_key)
    _enforce_rate_limit(request)
    upload, fields = await _extract_upload_from_request(request)
    job_id = _job_id()
    preset = _preset_from_fields(fields)
    input_path, safe_name, _ = await _save_upload_to_job(upload, job_id)
    return _process_saved_file(input_path=input_path, source_name=safe_name, job_id=job_id, preset_name=preset, true_prompt=str(fields.get("true_prompt") or ""), decoy_prompt=str(fields.get("decoy_prompt") or "generic product photo, neutral studio lighting"), corner_label=str(fields.get("corner_label", "false")).lower() not in {"0", "false", "off", "no"}, request=request)


@app.post("/api/jobs")
async def api_create_job(request: Request, background_tasks: BackgroundTasks, x_nova_api_key: str | None = Header(default=None)):
    _require_api_key(x_nova_api_key)
    _enforce_rate_limit(request)
    upload, fields = await _extract_upload_from_request(request)
    job_id = _job_id()
    preset = _preset_from_fields(fields)
    input_path, safe_name, dims = await _save_upload_to_job(upload, job_id)
    queued = {"ok": True, "job_id": job_id, "status": "queued", "progress": 0, "source_name": safe_name, "input_dimensions": dims, "status_url": f"/api/jobs/{job_id}", "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _write_json(_job_dir(job_id) / "status.json", queued)
    _JOBS[job_id] = queued
    background_tasks.add_task(_run_background_job, job_id, str(input_path), safe_name, preset, str(fields.get("true_prompt") or ""), str(fields.get("decoy_prompt") or "generic product photo, neutral studio lighting"), str(fields.get("corner_label", "false")).lower() not in {"0", "false", "off", "no"}, _client_ip(request))
    return queued


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    return _load_job(job_id)


@app.get("/api/jobs/{job_id}/download")
async def api_job_download(request: Request, job_id: str, expires: int | None = None, sig: str | None = None, x_nova_api_key: str | None = Header(default=None)):
    _enforce_rate_limit(request)
    _require_output_auth(x_nova_api_key)
    _require_valid_signature("job_download", job_id, expires, sig)
    path = _job_dir(job_id) / "protected.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Protected file not ready"})
    return FileResponse(path, media_type="image/png", filename=f"{job_id}.protected.png", headers=_noai_response_headers())


@app.get("/api/jobs/{job_id}/preview")
async def api_job_preview(request: Request, job_id: str, expires: int | None = None, sig: str | None = None, x_nova_api_key: str | None = Header(default=None)):
    _enforce_rate_limit(request)
    _require_output_auth(x_nova_api_key)
    _require_valid_signature("job_preview", job_id, expires, sig)
    path = _job_dir(job_id) / "protected.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Preview not ready"})
    return FileResponse(path, media_type="image/png", headers=_noai_response_headers())


@app.get("/api/jobs/{job_id}/verify")
async def api_job_verify(job_id: str, x_nova_api_key: str | None = Header(default=None)):
    _require_output_auth(x_nova_api_key)
    path = _job_dir(job_id) / "protected.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Protected file not ready"})
    return verify_png_metadata(path)


@app.post("/batch", response_class=HTMLResponse)
async def batch(request: Request, strength: str = Form(DEFAULT_PRESET), corner_label: str | None = Form("on")):
    _enforce_rate_limit(request)
    strength = normalize_preset_name(strength)
    if strength not in PRESETS:
        raise HTTPException(status_code=400, detail="Invalid strength preset")
    results = protect_folder(INPUT_DIR, OUTPUT_DIR, preset=strength, corner_label=corner_label == "on")
    for result in results:
        jid = _job_id()
        payload = _result_payload(result, job_id=jid, source_name="folder-mode", request=request)
        job_dir = _job_dir(jid)
        job_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result.output_path, job_dir / "protected.png")
        _write_json(job_dir / "result.json", payload)
        _append_manifest(payload)
    return templates.TemplateResponse("batch.html", {"request": request, "results": results})


@app.get("/files", response_class=HTMLResponse)
async def files(request: Request, x_nova_api_key: str | None = Header(default=None)):
    _enforce_rate_limit(request)
    if not PUBLIC_GALLERY:
        _require_output_auth(x_nova_api_key)
    rows = _output_file_rows()
    return templates.TemplateResponse("files.html", {"request": request, "rows": rows}, headers=_noai_response_headers())


@app.get("/api/files")
async def api_files(request: Request, response: Response, x_nova_api_key: str | None = Header(default=None)):
    _enforce_rate_limit(request)
    _require_output_auth(x_nova_api_key)
    for key, value in _noai_response_headers().items():
        response.headers[key] = value
    return [{"filename": p.name, "download_url": _signed_url("download", f"/download/{p.name}", p.name), "preview_url": _signed_url("download", f"/download/{p.name}", p.name), "bytes": p.stat().st_size, "verified": verify_png_metadata(p)} for p in _output_files()]


@app.post("/api/compare")
async def api_compare(request: Request, x_nova_api_key: str | None = Header(default=None)):
    _require_api_key(x_nova_api_key)
    _enforce_rate_limit(request)
    form = await request.form()
    original = form.get("original") or form.get("protected")
    suspect = form.get("suspect") or form.get("image")
    if not isinstance(original, (UploadFile, StarletteUploadFile)) or not isinstance(suspect, (UploadFile, StarletteUploadFile)):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "Upload fields required: original and suspect"})
    jid = _job_id()
    work = _job_dir(jid) / "compare"
    work.mkdir(parents=True, exist_ok=True)
    op = work / _safe_name(original.filename, "original.png")
    sp = work / _safe_name(suspect.filename, "suspect.png")
    op.write_bytes(await original.read())
    sp.write_bytes(await suspect.read())
    report = analyze_suspect(op, sp)
    report["ok"] = True
    report["job_id"] = jid
    _write_json(work / "similarity_report.json", report)
    return report


@app.post("/api/analyze-suspect")
async def api_analyze_suspect(request: Request, x_nova_api_key: str | None = Header(default=None)):
    _require_api_key(x_nova_api_key)
    _enforce_rate_limit(request)
    form = await request.form()
    original = form.get("original") or form.get("protected")
    suspect = form.get("suspect") or form.get("image")
    if not isinstance(original, (UploadFile, StarletteUploadFile)) or not isinstance(suspect, (UploadFile, StarletteUploadFile)):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "Upload fields required: original and suspect"})
    jid = _job_id()
    work = _job_dir(jid) / "analyze_suspect"
    work.mkdir(parents=True, exist_ok=True)
    op = work / _safe_name(original.filename, "original.png")
    sp = work / _safe_name(suspect.filename, "suspect.png")
    op.write_bytes(await original.read())
    sp.write_bytes(await suspect.read())
    report = analyze_suspect(op, sp)
    report["ok"] = True
    report["job_id"] = jid
    _write_json(work / "analysis_report.json", report)
    return report


@app.post("/api/evidence")
async def api_evidence(request: Request, x_nova_api_key: str | None = Header(default=None)):
    _require_api_key(x_nova_api_key)
    _enforce_rate_limit(request)
    form = await request.form()
    original = form.get("original") or form.get("protected")
    suspect = form.get("suspect") or form.get("image")
    if not isinstance(original, (UploadFile, StarletteUploadFile)) or not isinstance(suspect, (UploadFile, StarletteUploadFile)):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "Upload fields required: original and suspect"})
    jid = _job_id()
    work = _job_dir(jid) / "evidence_input"
    work.mkdir(parents=True, exist_ok=True)
    op = work / _safe_name(original.filename, "original.png")
    sp = work / _safe_name(suspect.filename, "suspect.png")
    op.write_bytes(await original.read())
    sp.write_bytes(await suspect.read())
    evidence_dir = _job_dir(jid) / "evidence"
    tar_path = build_evidence_package(op, sp, evidence_dir)
    report_path = next(evidence_dir.glob("*/similarity_report.json"), None)
    report = _read_json(report_path) if report_path else compare_derivative(op, sp)
    payload = {"ok": True, "job_id": jid, "evidence_url": f"/api/evidence/{jid}/download", "report": report}
    _write_json(_job_dir(jid) / "evidence_result.json", payload)
    return payload


@app.get("/api/evidence/{job_id}/download")
async def api_evidence_download(job_id: str, x_nova_api_key: str | None = Header(default=None)):
    _require_output_auth(x_nova_api_key)
    evidence_dir = _job_dir(job_id) / "evidence"
    files = sorted(evidence_dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Evidence package not found"})
    return FileResponse(files[0], media_type="application/gzip", filename=files[0].name, headers=_noai_response_headers())


@app.post("/api/frequency-audit")
async def api_frequency_audit(request: Request, x_nova_api_key: str | None = Header(default=None)):
    _require_api_key(x_nova_api_key)
    _enforce_rate_limit(request)
    form = await request.form()
    original = form.get("original")
    protected = form.get("protected") or form.get("image")
    if not isinstance(original, (UploadFile, StarletteUploadFile)) or not isinstance(protected, (UploadFile, StarletteUploadFile)):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "Upload fields required: original and protected"})
    jid = _job_id()
    work = _job_dir(jid) / "frequency_audit"
    work.mkdir(parents=True, exist_ok=True)
    op = work / _safe_name(original.filename, "original.png")
    pp = work / _safe_name(protected.filename, "protected.png")
    op.write_bytes(await original.read())
    pp.write_bytes(await protected.read())
    report = frequency_audit(op, pp)
    report["ok"] = True
    report["job_id"] = jid
    _write_json(work / "frequency_audit.json", report)
    return report


@app.post("/api/registry/verify")
async def api_registry_verify(request: Request):
    """Public registry-style verifier for protected files.

    Accepts an uploaded image and reports whether Blindspot protection signals
    survived. It does not require metadata to be present; pixel/frequency signals
    are checked when possible.
    """
    form = await request.form()
    upload = None
    for key in ("artwork", "file", "image"):
        item = form.get(key)
        if isinstance(item, (UploadFile, StarletteUploadFile)):
            upload = item
            break
    if upload is None:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "Upload field required: artwork, file, or image"})
    jid = _job_id()
    work = _job_dir(jid) / "registry_verify"
    work.mkdir(parents=True, exist_ok=True)
    path = work / _safe_name(upload.filename, "verify.png")
    path.write_bytes(await upload.read())
    verification = verify_protected_image(path, strict=False)
    return {
        "ok": True,
        "job_id": jid,
        "registered": bool(verification.get("registry_valid") or verification.get("provenance_valid") or verification.get("dct_watermark_valid") or verification.get("watermark_valid")),
        "metadata_valid": verification.get("metadata_valid"),
        "policy_valid": verification.get("policy_valid"),
        "watermark_valid": verification.get("watermark_valid"),
        "dct_watermark_valid": verification.get("dct_watermark_valid"),
        "provenance_valid": verification.get("provenance_valid"),
        "registry_valid": verification.get("registry_valid"),
        "protection_hash_valid": verification.get("protection_hash_valid"),
        "manifest_valid": verification.get("manifest_valid"),
        "attack_resistance_score": verification.get("attack_resistance_score"),
        "layers": verification.get("layers"),
        "claim_boundary": CLAIM_BOUNDARY,
    }


@app.get("/api/metrics")
async def api_metrics(request: Request, response: Response, x_nova_api_key: str | None = Header(default=None)):
    _enforce_rate_limit(request)
    _require_output_auth(x_nova_api_key)
    for key, value in _noai_response_headers().items():
        response.headers[key] = value
    files = _output_files()
    all_records = _manifest_tail(5000)
    job_dirs = [p for p in JOBS_DIR.glob("job_*") if p.is_dir()]
    newest = max((p.stat().st_mtime for p in files), default=None)
    oldest = min((p.stat().st_mtime for p in files), default=None)
    return {
        "output_count": len(files),
        "job_count": len(job_dirs),
        "manifest_records_count_estimate": len(all_records),
        "output_bytes": sum(p.stat().st_size for p in files),
        "disk": _disk_summary(),
        "newest_output_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest)) if newest else None,
        "oldest_output_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(oldest)) if oldest else None,
        "recent_jobs": all_records[-50:],
        "rate_limit_per_hour": RATE_LIMIT_PER_HOUR,
        "api_key_required": bool(OPTIONAL_API_KEY),
        "keep_all_files_for_records": True,
    }


@app.get("/api/jobs/{job_id}/record")
async def api_job_record(job_id: str, x_nova_api_key: str | None = Header(default=None)):
    _require_output_auth(x_nova_api_key)
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Job not found"})
    tar_path = job_dir / f"{job_id}.record.tar.gz"
    verification_path = job_dir / "verification.json"
    protected_path = job_dir / "protected.png"
    if protected_path.exists():
        _write_json(verification_path, verify_png_metadata(protected_path))
    readme = job_dir / "README.txt"
    if not readme.exists():
        readme.write_text(
            "Blindspot / Nova Project record export\n"
            f"Job ID: {job_id}\n"
            "This archive contains the original upload, protected output, result JSON, and metadata verification.\n"
            "No files are deleted by the export process.\n",
            encoding="utf-8",
        )
    with tarfile.open(tar_path, "w:gz") as tar:
        for child in job_dir.iterdir():
            if child.name == tar_path.name:
                continue
            tar.add(child, arcname=f"{job_id}/{child.name}")
    return FileResponse(tar_path, media_type="application/gzip", filename=tar_path.name, headers=_noai_response_headers())


@app.get("/verify/{filename}")
async def verify(filename: str, x_nova_api_key: str | None = Header(default=None)):
    _require_output_auth(x_nova_api_key)
    path = OUTPUT_DIR / _safe_name(filename)
    if not path.exists() or path.suffix.lower() != ".png":
        raise HTTPException(status_code=404, detail="File not found")
    return verify_png_metadata(path)


@app.get("/download/{filename}")
async def download(request: Request, filename: str, expires: int | None = None, sig: str | None = None, x_nova_api_key: str | None = Header(default=None)):
    _enforce_rate_limit(request)
    _require_output_auth(x_nova_api_key)
    safe_filename = _safe_name(filename)
    _require_valid_signature("download", safe_filename, expires, sig)
    path = OUTPUT_DIR / safe_filename
    if not path.exists() or path.suffix.lower() != ".png":
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="image/png", filename=path.name, headers=_noai_response_headers())


@app.get("/gallery", response_class=HTMLResponse)
async def gallery(request: Request, x_nova_api_key: str | None = Header(default=None)):
    _enforce_rate_limit(request)
    if not PUBLIC_GALLERY:
        _require_output_auth(x_nova_api_key)
    rows = _output_file_rows()
    return templates.TemplateResponse("gallery.html", {"request": request, "rows": rows}, headers=_noai_response_headers())


@app.get("/how-it-works", response_class=HTMLResponse)
async def how_it_works(request: Request):
    return templates.TemplateResponse("info_page.html", {"request": request, "title": "How Blindspot Shield Works", "kicker": "Protection pipeline", "sections": [("1. Feature cloaking", "Blindspot adds bounded, almost-invisible perturbations that move model-visible features away from the original artwork style."), ("2. Prompt misdirection", "The engine uses the true prompt and a decoy prompt to bias the protection signal toward less useful training associations."), ("3. Robustness transforms", "Outputs are scored against resize, compression, frequency signatures, and quality checks before being marked protected."), ("4. Provenance", "Every protected PNG includes Nova Project metadata and an invisible frequency-domain signature.")]})


@app.get("/faq", response_class=HTMLResponse)
async def faq(request: Request):
    return templates.TemplateResponse("info_page.html", {"request": request, "title": "FAQ", "kicker": "Common questions", "sections": [("Does this make AI training impossible?", "No. No public image protection can honestly promise impossibility. Blindspot makes copied or trained-on images harder and less reliable to use."), ("Why is the default clean?", "Creators usually want protection without ruining the artwork. Strong and max modes are available when you want more aggressive protection."), ("What files are supported?", "PNG, JPG/JPEG/JFIF, WEBP, GIF first frame, BMP, and TIFF are accepted. Outputs are always PNG."), ("What metadata is added?", "Protected PNGs include: AI PROTECTED BY NOVA PROJECT - OUR COMPANY - NO AI TRAINING.")]})


@app.get("/robots.txt")
async def robots():
    path = BASE_DIR / "robots.txt"
    if path.exists():
        return FileResponse(path, media_type="text/plain")
    return HTMLResponse("User-agent: *\nAllow: /\n", media_type="text/plain")


@app.get("/sitemap.xml")
async def sitemap():
    path = BASE_DIR / "sitemap.xml"
    if path.exists():
        return FileResponse(path, media_type="application/xml")
    raise HTTPException(status_code=404, detail="Sitemap not found")


@app.get("/favicon.ico")
async def favicon():
    path = BASE_DIR / "app" / "static" / "favicon.ico"
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="image/x-icon")


@app.get("/icon.svg")
async def icon_svg():
    path = BASE_DIR / "app" / "static" / "icon.svg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="image/svg+xml")


@app.get("/apple-touch-icon.png")
async def apple_touch_icon():
    path = BASE_DIR / "app" / "static" / "apple-touch-icon.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="image/png")


@app.get("/og-image.png")
async def og_image():
    path = BASE_DIR / "app" / "static" / "og-image.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="image/png")
