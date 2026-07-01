# Obsidian Knowledge Import Hub

A production-ready OCR-to-Obsidian import system that automatically processes images containing tables, mixed languages, and special characters, publishing structured Markdown notes into an Obsidian vault.

## Features

- **Automatic File Watching**: Monitors RAW folder for new images
- **Multi-Engine OCR**: Routes to PaddleOCR, MinerU, or Mathpix based on content type
- **Table Reconstruction**: Preserves cell colors and generates HTML tables with `bgcolor`
- **LLM Post-Correction**: Uses local Ollama for OCR error correction
- **Smart Linking**: Auto-generates wiki-links to existing Obsidian notes
- **Persistent Queue**: SQLite-based task queue with resume capability
- **Structured Logging**: JSON logs for debugging and monitoring

## Installation

### 1. Install Python Dependencies

```bash
cd knowledge_import_hub
pip install -r requirements.txt
```

### 2. Install Ollama (for LLM correction)

**Windows:**
```powershell
winget install Ollama.Ollama
```

**macOS:**
```bash
brew install ollama
```

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### 3. Pull LLM Model

```bash
ollama pull qwen2.5:1.5b
```

### 4. Install Optional Dependencies

**For super-resolution (Real-ESRGAN):**
```bash
pip install realesrgan
```

**For Chinese text processing:**
```bash
pip install jieba
```

## Configuration

Edit `config.yaml` to set up your environment:

```yaml
vault:
  root: "D:/test-temp/ocr_output"  # Your Obsidian vault root
  raw_folder: "00-RAW"              # Folder to watch for new images
  audit_folder: "99-Audit/OCR-Pending"  # Where processed notes go

processing:
  max_worker_threads: 2             # Parallel processing threads
  confidence_threshold: 0.85        # Below this triggers LLM correction

ocr:
  ollama:
    endpoint: "http://localhost:11434"
    model: "qwen2.5:1.5b"
```

## Usage

### Start the Watcher

```bash
python main.py
```

This will:
1. Start monitoring the RAW folder
2. Process new images automatically
3. Publish notes to the audit folder

### Process Specific Files

```bash
python main.py --once D:/test-temp/png/image1.png D:/test-temp/png/image2.png
```

### Check Queue Status

```bash
python main.py --status
```

### With Custom Config

```bash
python main.py --config /path/to/config.yaml
```

## Testing

### Run Test Suite

```bash
cd knowledge_import_hub
pytest tests/ -v
```

### Run Specific Tests

```bash
pytest tests/test_pipeline.py::TestTableBuilder -v
pytest tests/test_pipeline.py::TestEntityLinker -v
```

### End-to-End Test

```bash
# Create test vault structure
mkdir -p D:/test-temp/ocr_output/{00-RAW,99-Audit/OCR-Pending,10-WIKI}

# Copy test images
cp D:/test-temp/png/*.png D:/test-temp/ocr_output/00-RAW/

# Run processing
python main.py --once D:/test-temp/ocr_output/00-RAW/*.png

# Check output
ls D:/test-temp/ocr_output/99-Audit/OCR-Pending/
```

## Project Structure

```
knowledge_import_hub/
©À©¤©¤ config.yaml              # Configuration
©À©¤©¤ main.py                  # Entry point
©À©¤©¤ watcher.py               # File system watcher
©À©¤©¤ queue_manager.py         # SQLite task queue
©À©¤©¤ processors/
©¦   ©À©¤©¤ base.py              # Abstract base handler
©¦   ©À©¤©¤ image_handler.py     # Pipeline orchestrator
©¦   ©À©¤©¤ preprocessor.py      # Image enhancement
©¦   ©À©¤©¤ color_extractor.py   # Table color extraction
©¦   ©À©¤©¤ ocr_router.py        # OCR engine selection
©¦   ©À©¤©¤ post_corrector.py    # LLM correction
©¦   ©À©¤©¤ table_builder.py     # HTML table generation
©¦   ©¸©¤©¤ markdown_generator.py # Markdown assembly
©À©¤©¤ publishers/
©¦   ©¸©¤©¤ obsidian_publisher.py # Note publishing
©À©¤©¤ linkers/
©¦   ©À©¤©¤ entity_linker.py     # Link candidate generation
©¦   ©¸©¤©¤ disambiguator.py     # Link scoring
©À©¤©¤ utils/
©¦   ©À©¤©¤ file_utils.py        # File operations
©¦   ©À©¤©¤ log_setup.py         # Logging setup
©¦   ©¸©¤©¤ progress.py          # Progress tracking
©À©¤©¤ tests/                   # Test suite
©À©¤©¤ requirements.txt         # Dependencies
©¸©¤©¤ README.md                # This file
```
## Workflow

1. **Image Detection**: Watcher detects new image in RAW folder
2. **Queue Addition**: Task added to SQLite queue with SHA-256 hash
3. **Preprocessing**: Document detection, perspective correction, color extraction
4. **Content Classification**: Determines if image contains tables, text, or mixed
5. **OCR Processing**: Routes to appropriate engine (PaddleOCR/MinerU/Mathpix)
6. **Post-Correction**: LLM corrects low-confidence text
7. **Table Building**: Reconstructs HTML tables with original colors
8. **Markdown Generation**: Assembles note with YAML front matter
9. **Entity Linking**: Generates wiki-link candidates
10. **Publishing**: Writes note to audit folder for review

## Output Format

Generated notes include:

```yaml
---
title: "Extracted Title"
date: 2026-05-11
tags: ["ocr/pending", "ocr/table"]
status: pending
source: "[[00-RAW/original.png]]"
ocr_confidence: 0.87
---

Extracted text content...

## Tables

<table border="1">
<tr><td bgcolor="#FF0000">Cell 1</td><td bgcolor="#00FF00">Cell 2</td></tr>
</table>

<!-- Link Candidates (for review) -->
<!-- LINK: Machine Learning -> [[Machine Learning]] (conf: 0.95) -->
<!-- LINK: AI -> [[AI]] (conf: 0.75) -->
```

## Troubleshooting

### PaddleOCR Not Found

```bash
pip install paddleocr==2.7.0 paddlepaddle==2.5.0
```

### Ollama Connection Failed

```bash
# Check Ollama is running
ollama list

# Restart Ollama
ollama serve
```

### Queue Stuck

```bash
# Check queue status
python main.py --status

# Reset queue (delete database)
rm vault/.obsidian/ocr_queue.db
```

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `pytest tests/ -v`
4. Submit pull request

## Support

For issues and questions, please open a GitHub issue.
