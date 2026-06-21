#!/usr/bin/env python3
"""Backward-compatible entrypoint for the upgraded Nova Project Shield engine."""
from __future__ import annotations

import argparse
from pathlib import Path

from nova_shield import PRESETS, protect_folder, verify_protected_image


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create Nova Project protected PNG copies of artwork.")
    p.add_argument("--input", default="input_art", help="Folder containing original art.")
    p.add_argument("--output", default="output_protected", help="Folder for protected PNG copies.")
    p.add_argument("--preset", choices=["shield", "clean", "strong", "max", "paranoid", "fortress", "hidden", "standard", "maximum"], default="shield")
    p.add_argument("--true-prompt", default="", help="Optional prompt describing the real art style/content.")
    p.add_argument("--decoy-prompt", default="generic product photo, neutral studio lighting")
    p.add_argument("--no-label", action="store_true", help="Disable small corner label.")
    p.add_argument("--strict", action="store_true", help="Exit nonzero if any output fails strict verification.")
    p.add_argument("--visible-watermark", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--border", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args()


def normalize_preset(name: str) -> str:
    return "shield"


def main() -> int:
    args = parse_args()
    preset = normalize_preset(args.preset)
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = protect_folder(input_dir, output_dir, preset=preset, corner_label=not args.no_label)
    if not results:
        print(f"No art found yet. Put images in: {input_dir.resolve()}")
        return 0
    failures = 0
    for r in results:
        verification = verify_protected_image(r.output_path, strict=args.strict)
        status = "protected" if r.reached_threshold and verification.get("valid") else "needs stronger preset"
        if args.strict and not verification.get("valid"):
            failures += 1
        print(f"Protected: {r.output_path} score={r.protection_score}/100 quality={r.visual_quality_score}/100 attack={verification.get('attack_resistance_score')}/100 status={status} mode={r.mode}")
        for warning in r.warnings:
            print(f"  warning: {warning}")
    print(f"Done. Protected PNG copies are in: {output_dir.resolve()}")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
