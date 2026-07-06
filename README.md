# Obsidian Knowledge Import Hub

English | [中文](README.zh-CN.md)

A production-ready OCR-to-Obsidian import system that automatically processes
images containing tables, mixed languages, and special characters, publishing
structured Markdown notes into an Obsidian vault.

## Features

- **Automatic File Watching**: Monitors the RAW folder for new images
- **Multi-Engine OCR**: Routes to PaddleOCR / MinerU / Mathpix based on content type
- **Layout-Aware Reconstruction**: Rebuilds titles, reading-order regions, and tables from OCR blocks
- **Table Reconstruction**: Preserves cell colors and generates tables (Markdown and HTML with `bgcolor`)
- **Tiered Table Enhancement (Plan A)**: An optional, review-only pass re-recognizes low-confidence table regions using a host-selected backend (`gridboost` / `vision` / `manual`); off by default and never replaces the primary output
- **Noise Filtering**: Editor toolbars / PPT headers / footers are moved to a review block with reasons, not discarded
- **LLM Post-Correction**: Uses a local Ollama model for OCR error correction
- **Smart Linking**: Auto-generates wiki-links to existing Obsidian notes
- **Persistent Queue**: SQLite-based task queue with resume capability
- **Structured Logging**: JSON logs for debugging and monitoring

## Requirements

- Windows, Python 3.11
- Pinned OCR stack (do not change; other versions cause ABI import errors):

```text
paddleocr==2.7.3
paddlepaddle==2.6.2
numpy==1.26.4
opencv-python==4.6.0.66
opencv-contrib-python==4.6.0.66
```

The tiered enhancement (Plan A) is optional. The `vision` tier additionally needs a local Ollama vision model (e.g. `ollama pull qwen2.5vl:3b`); it is only selected when the host profile detects an accelerator or ample free RAM. On CPU-only hosts the profiler picks `gridboost` (pure OpenCV preprocessing) and enhancement stays off unless enabled.

## Installation

### 1. Install Python dependencies

```bash
cd knowledge_import_hub
pip install -r requirements.txt
```

### 2. Install Ollama (for LLM correction)

```powershell
# Windows
winget install Ollama.Ollama
```

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

### 3. Pull the LLM model

```bash
ollama pull qwen2.5:1.5b
```

## Configuration

Edit `config.yaml`:

```yaml
vault:
  root: "D:/test-temp/ocr_output"        # Obsidian vault root
  raw_folder: "00-RAW"                    # Folder watched for new images
  audit_folder: "99-Audit/OCR-Pending"   # Where processed notes are published

processing:
  max_worker_threads: 2                   # Parallel processing threads
  confidence_threshold: 0.85              # Below this triggers LLM correction

ocr:
  ollama:
    endpoint: "http://localhost:11434"
    model: "qwen2.5:1.5b"
  table_structure:
    enhance_on_low_quality: false   # Plan A master switch (off by default)
    backend: ""                     # "" = auto-select from host profile
    vision_model: "qwen2.5vl:3b"    # model used by the vision tier
    vision_timeout: 180
```

## Tiered Table Enhancement (Plan A)

For colored/borderless slides where the geometric reconstruction produces a low-confidence table, an optional enhancement pass can re-recognize just that region and attach the result as a review-only comparison block. It is **off by default** and **never replaces** the primary output, so enabling it can only add information (zero regression on the main rendering).

On first run the pipeline probes host capability once and caches the result to `host_profile.local.json`, mapping it to one of three tiers:

- `vision`: an accelerator (CUDA/MPS) or ample free RAM plus a local Ollama vision model is available; the cropped region is transcribed by the vision model (e.g. `qwen2.5vl:3b`).
- `gridboost`: CPU-only host with PaddleOCR available (the common case); the region is decolorized/binarized and virtual grid lines are drawn from OCR word boxes before PP-Structure re-recognition.
- `manual`: PP-Structure unavailable or resources too low; no enhancement, a low-confidence warning is emitted for human review.

Enable it in `config.yaml` with `ocr.table_structure.enhance_on_low_quality: true`. Set `backend` to force a tier (`vision` / `gridboost` / `manual` / `ppstructure`); leave it empty to auto-select from the cached profile. Re-probe with `python test_snapshot.py` after deleting `host_profile.local.json`.

> Why enhancement is off by default and which approaches were tried and rolled back or shelved (whole-page VLM rebuild, PP-Structure/gridboost, per-region rebuild) is documented in [PROJECT_HISTORY.md](PROJECT_HISTORY.md).

## Usage

### Start the watcher

```bash
python main.py
```

This monitors the RAW folder, processes new images, and publishes notes to the
audit folder.

### Process specific files

```bash
python main.py --once "D:/test-temp/png/image1.jpg" "D:/test-temp/png/image2.jpg"
```

> Note: on success `main.py --once` may still exit with code 1; trust the
> `Published note` log line as the source of truth.

### Check queue status

```bash
python main.py --status
```

### Use a custom config

```bash
python main.py --config /path/to/config.yaml
```

## Testing

### Unit tests

```bash
pytest tests/ -q
```

### Snapshot testing (iteration archiving and comparison)

`test_snapshot.py` archives each test run so results can be compared across
iterations. This is the recommended way to verify layout changes.

```bash
# Run OCR on samples, archive a timestamped snapshot, and auto-compare
# against the previous one:
python test_snapshot.py run

# Reuse cached OCR blocks (skip slow OCR, layout-only iteration):
python test_snapshot.py run --use-cache

# Run on specific images only:
python test_snapshot.py run --images "D:/test-temp/png/a.jpg" "D:/test-temp/png/b.jpg"

# Compare the two most recent snapshots (no OCR):
python test_snapshot.py compare

# List archived snapshots:
python test_snapshot.py list
```

Each snapshot stores per-image Markdown plus a `manifest.json` of metrics
(chars, lines, table rows, external tables, OCR blocks, confidence). Comparison
ignores the front-matter `date:` line and reports unchanged / changed / added /
removed items with a unified diff for changed files. Snapshots default to
`D:/test-temp/ocr_output/_snapshots` (outside the repository).

## Project Structure

```text
knowledge_import_hub/
- config.yaml                 # Configuration
- main.py                     # Entry point
- watcher.py                  # File system watcher
- queue_manager.py            # SQLite task queue
- run_test.py                 # Setup checker (see test_snapshot.py for real runs)
- test_snapshot.py            # Snapshot testing / iteration comparison
- host_profile.local.json     # Generated on first run; cached host tier (git-ignored)
- processors/
  - base.py                   # Abstract base handler
  - image_handler.py          # Pipeline orchestrator
  - preprocessor.py           # Image enhancement (Unicode-safe image read)
  - color_extractor.py        # Table color extraction
  - ocr_router.py             # OCR engine selection (PaddleOCR + PP-Structure)
  - post_corrector.py         # LLM correction
  - table_builder.py          # Fallback HTML table generation
  - host_profiler.py            # First-run host capability probe + tier cache (Plan A)
  - table_enhancer.py           # Pluggable enhancement backends (gridboost/vision/manual)
  - markdown_generator.py     # Layout reconstruction and Markdown assembly
- publishers/
  - obsidian_publisher.py     # Note publishing
- linkers/
  - entity_linker.py          # Link candidate generation
  - disambiguator.py          # Link scoring
- utils/
  - file_utils.py             # File operations
  - log_setup.py              # Logging setup
  - progress.py               # Progress tracking
- tests/                      # Test suite
- requirements.txt            # Dependencies
- README.md                   # This file (English)
- README.zh-CN.md             # Chinese version
```

## Workflow

1. **Image Detection**: watcher detects a new image in the RAW folder
2. **Queue Addition**: task added to the SQLite queue with a SHA-256 hash
3. **Preprocessing**: document detection, perspective correction, color extraction
4. **Content Classification**: table / text / mixed
5. **OCR Processing**: routes to the appropriate engine
6. **Post-Correction**: LLM corrects low-confidence text
7. **Layout & Tables**: reconstructs regions and tables (colors preserved)
8. **Optional Enhancement (Plan A)**: when enabled, low-confidence table regions are re-recognized by the host-selected backend and attached as a review-only comparison block
9. **Markdown Generation**: assembles a note with YAML front matter
10. **Entity Linking**: generates wiki-link candidates
11. **Publishing**: writes the note to the audit folder for review

## Output Format

```yaml
---
title: "Extracted Title"
date: 2026-05-11
page: 34
tags: ["ocr/pending", "ocr/table"]
status: pending
source: "[[00-RAW/original.jpg]]"
ocr_confidence: 0.87
---

# Extracted Title

Reconstructed body text and Markdown tables...

<!-- Filtered non-content (nav bars / headers / footers) - review before archiving -->
<!-- Link Candidates (for review) -->
```

## Troubleshooting

### numpy / ABI import error

Keep `numpy==1.26.4`. Other versions raise
`numpy.core.multiarray failed to import`.

### Non-ASCII (Chinese) image paths fail to read

Fixed: `preprocessor.py` reads images via `np.fromfile` + `cv2.imdecode`
instead of `cv2.imread`, which cannot open non-ASCII paths on Windows.

### Ollama connection failed

```bash
ollama list      # check it is running
ollama serve     # restart
```

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `pytest tests/ -q`
4. Submit a pull request
