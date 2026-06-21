#!/usr/bin/env python3
"""Export a Blindspot job record without deleting anything.
Works outside the project venv and does not need write access to the job folder.
"""
from __future__ import annotations
import argparse, json, shutil, tarfile, tempfile, time
from pathlib import Path
try:
    from PIL import Image
except Exception:
    Image = None

ROOT = Path('/home/azureuser/Blindspot')
OUTPUT = ROOT / 'output_protected'
JOBS = OUTPUT / 'jobs'


def verify_png(path: Path) -> dict:
    info = {'filename': path.name, 'exists': path.exists(), 'valid': False}
    if not path.exists(): return info
    if Image is None:
        info['warning'] = 'Pillow not available; run inside .venv for full PNG metadata verification'
        return info
    try:
        with Image.open(path) as img:
            text = getattr(img, 'text', {}) or {}
            info.update({
                'is_png': img.format == 'PNG',
                'nova_metadata_present': bool(text.get('NovaProject')),
                'no_ai_training_present': text.get('AI-Training-Policy') == 'NO AI TRAINING',
                'protection_score': text.get('ProtectionScore'),
                'visual_quality_score': text.get('VisualQualityScore'),
                'preset': text.get('Preset'),
            })
            info['valid'] = bool(info['is_png'] and info['nova_metadata_present'] and info['no_ai_training_present'])
    except Exception as e:
        info['error'] = str(e)
    return info


def copy_tree_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('job_id')
    ap.add_argument('--dest', default=str(ROOT / 'backups'))
    args = ap.parse_args()
    job = JOBS / args.job_id
    if not job.is_dir():
        raise SystemExit(f'Job not found: {job}')
    dest = Path(args.dest); dest.mkdir(parents=True, exist_ok=True)
    out = dest / f'{args.job_id}.record.tar.gz'
    with tempfile.TemporaryDirectory(prefix='blindspot_export_') as td:
        stage = Path(td) / args.job_id
        copy_tree_contents(job, stage)
        (stage / 'verification.json').write_text(json.dumps(verify_png(stage / 'protected.png'), indent=2), encoding='utf-8')
        (stage / 'README.txt').write_text(
            'Blindspot / Nova Project record export\n'
            f'Job ID: {args.job_id}\n'
            f'Exported: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}\n'
            'Contains original upload, protected output, result JSON, and verification.\n'
            'No files were deleted.\n', encoding='utf-8')
        with tarfile.open(out, 'w:gz') as tar:
            tar.add(stage, arcname=args.job_id)
    print(out)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
