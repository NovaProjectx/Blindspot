# Blindspot

Blindspot is a Python utility and lightweight web app to protect images from being scraped or used for training AI models. It applies low-amplitude, high-frequency mathematical noise (perturbations) that disrupt AI training pipelines while keeping the image clean for human viewers. It also embeds robust metadata (PNG chunks) with explicit legal restrictions, and includes forensic tools to help prove ownership if your art has been scraped.

## Features

- **Adversarial Noise**: Multiple presets (`invisible`, `balanced`, `shield`, `maximum`) using low-amplitude high-frequency perturbations. Optional PyTorch/OpenCLIP support for more advanced text-image misdirection.
- **Metadata Protection**: Injects custom iTXt/tEXt chunks in PNGs declaring non-license for AI dataset use.
- **Forensic Auditing**: Utilities to compare suspicious images against original files, analyze changes using discrete cosine transform (DCT) frequency mapping, and build a tarball evidence package.
- **Web App**: Optional FastAPI interface for drag-and-drop processing and a lightweight output gallery.
- **Flexible Execution**: Works out-of-the-box on CPU, with optional GPU (CUDA) acceleration via Docker.

## Setup

### Docker (Recommended)

To run the web app with CPU fallbacks:

```bash
docker-compose up -d --build
```

To run with NVIDIA GPU acceleration:

```bash
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

The web panel will be available at `http://localhost:8080`.

### Native Setup (Local Python)

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the FastAPI development server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## CLI Usage

You can use the core python script directly without running the web app:

```bash
source .venv/bin/activate
python3 nova_shield.py --help
```

### Examples

**Protect an image (file or folder):**
```bash
python3 nova_shield.py --input path/to/art.jpg --output path/to/output.png --preset balanced
```

**Verify a protected PNG's metadata:**
```bash
python3 nova_shield.py --verify path/to/output.png
```

**Generate a forensic evidence package against a scraped/altered derivative:**
This generates a structured `.tar.gz` package showing mathematical comparisons, SSIM, and metadata mismatch to help build legal cases.
```bash
python3 nova_shield.py --evidence path/to/original.jpg path/to/suspect.png
```

## Web API Endpoints

If running the server, you can process images programmatically:

```bash
curl -X POST -F "file=@my_art.jpg" -F "preset=shield" http://localhost:8080/api/protect --output protected.png
```

- `/` - Main drag-and-drop web UI
- `/status` - JSON metadata about server queue and CUDA status
- `/files` - Browser gallery of output files

## Directory Layout

- `app/` - FastAPI web backend and static gallery templates
- `tests/` - Automated validation test suite
- `scripts/` - Helper scripts (NVIDIA toolkit setup, backups, restart service)
- `nova_shield.py` - Core CLI engine and PIL-based perturbation algorithms

## License

MIT License. See the `LICENSE` file for details.
