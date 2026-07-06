# Project Summary: Obsidian Knowledge Import Hub

## Status

Image OCR-to-Markdown pipeline is operational and iterating. The current focus
(per the project scope) is the **image OCR path**: turning slide/screenshot
images into layout-faithful Markdown notes. Other router branches (MinerU /
Mathpix) remain placeholders and are out of scope for now.

- Tests: `pytest tests/ -q` => **59 passed** (15 test classes).
- Docs are kept in sync with code here and in `README.md` / `README.zh-CN.md`.
- Detailed per-iteration change log lives in `TASK_STATUS.md`.

## Architecture (current)

The pipeline is a linear orchestration with an optional, review-only
enhancement branch (Plan A). `ImageHandler` owns the sequence; the host tier is
resolved once at startup and cached.

```text
                          File Watcher (watchdog)
                                   |
                          Queue Manager (SQLite, SHA-256 dedup, resume)
                                   |
                          Worker Thread -> ImageHandler.process()
                                   |
   Step 1  Preprocessor        document detect / perspective / super-res / CLAHE / denoise
   Step 2  OCR Router          content classify -> PaddleOCR-VL (+ PP-Structure table)
   Step 3  Post-Corrector      local Ollama LLM fixes low-confidence text (numbers preserved)
   Step 4  Table Builder       fallback HTML table (with cell colors) when PP-Structure absent
   Step 5  Markdown Generator  LAYOUT RECONSTRUCTION -> Markdown note (front matter + body)
   Step 5b Table Enhancer      [optional/off] re-recognize low-confidence table regions,
                               attach a review-only comparison block (never replaces output)
                                   |
                          Entity Linker + Disambiguator (wiki-link candidates)
                                   |
                          Obsidian Publisher (writes note to audit folder)
```

Host capability is profiled once at first run by `host_profiler` and cached to
`host_profile.local.json` (git-ignored). The cached tier selects which
enhancement backend `table_enhancer` uses if enhancement is enabled.

```text
host_profiler.load_or_create_profile()  (first run only; --rescan to rebuild)
        |
        v
   decide_tier()
        |-- vision     : accelerator (CUDA/MPS) or ample free RAM + local VLM  -> VisionLocalBackend
        |-- gridboost  : CPU-only but PaddleOCR available (this host)          -> GridBoostBackend
        |-- manual     : no PP-Structure / low resources                       -> no enhancement
```

## Modules

### Entry / runtime
- `main.py` - CLI entry (watch mode, `--once`, `--status`, `--config`).
- `watcher.py` - watchdog-based RAW-folder watcher.
- `queue_manager.py` - SQLite persistent task queue (SHA-256 dedup, retries, resume).
- `test_snapshot.py` - iteration snapshot archiving + cross-run diff (the real test harness).
- `run_test.py` - environment/setup checker.

### Processors (`processors/`)
- `base.py` - abstract handler base.
- `image_handler.py` - pipeline orchestrator (Steps 1-5b); builds host profile + enhancer at init.
- `preprocessor.py` - image enhancement; Unicode-safe read (`np.fromfile` + `cv2.imdecode`).
- `color_extractor.py` - K-Means (LAB) cell color extraction.
- `ocr_router.py` - engine routing; PaddleOCR 2.7.3 + PP-Structure/table.
- `post_corrector.py` - Ollama LLM post-correction of low-confidence text.
- `table_builder.py` - fallback HTML table reconstruction with `bgcolor`.
- `markdown_generator.py` - layout reconstruction + Markdown assembly (see below).
- `host_profiler.py` - first-run host capability probe + tier decision, cached to disk.
- `table_enhancer.py` - pluggable enhancement backends + comparable quality score `S_e`.

### Publishers / Linkers / Utils
- `publishers/obsidian_publisher.py` - note publishing to the vault.
- `linkers/entity_linker.py`, `linkers/disambiguator.py` - wiki-link candidate generation + scoring.
- `utils/file_utils.py`, `utils/log_setup.py`, `utils/progress.py` - file ops, JSON logging, progress.

### Tests (`tests/test_pipeline.py`)
15 classes / 59 tests, including `TestTitleExtraction`, `TestTableQuality`,
`TestTableEnhancer`, `TestHostProfiler`, `TestGridBoost`, `TestVisionLocalBackend`,
`TestImageHandlerPipeline`, `TestPPStructureParsing`.

## Layout reconstruction (markdown_generator)

This is the heart of the image path and the most evolved component versus the
original design. Beyond simple assembly it now performs:

- **Title extraction** from visual features (font size / position), not fixed
  keywords; written to both front-matter `title` and a body `# heading`. When no
  strong candidate exists it falls back to a length-limited summary title and
  records a review note.
- **Reading-order regions**: split into top-to-bottom visual bands, then into
  columns only when a band looks table-like; the first paragraph stays whole
  (not split left/right).
- **Region sub-headings**: color/emphasis-marked region labels are emitted as
  `> ` highlighted sub-headings.
- **Tables**: contiguous grid runs become Markdown tables; stacked adjacent
  tables are split by the widest vertical gutter into independent tables; side
  notes are separated out of table cells; empty columns are dropped.
- **Table quality gate** (`_table_quality`): a geometry-only score in [0,1]
  (fill / alignment / row-count stability / column collision / wide-table
  penalty). Below `_TABLE_QUALITY_MIN` (0.62) the region is flagged as a
  low-confidence table (the trigger for optional Plan A enhancement).
- **Noise filtering**: editor toolbars / PPT headers-footers are moved to a
  bottom review block with per-item reasons (not discarded); page numbers are
  extracted to front-matter `page` for sequential archiving.

## Plan A: tiered table enhancement (optional, review-only)

Design principle: **purely additive, never replaces the primary output**, and
**off by default** (`ocr.table_structure.enhance_on_low_quality: false`).

- `host_profiler` probes once and caches the tier; `table_enhancer` exposes a
  pluggable backend interface (`run(crop, region, region_blocks, offset_xy) -> html`).
- Backends:
  - `PPStructureBackend` - PP-Structure on the raw crop.
  - `GridBoostBackend` - decolorize/binarize + virtual grid lines (from OCR word
    boxes) then PP-Structure; pure OpenCV, no new model.
  - `VisionLocalBackend` - offline local VLM via Ollama (`qwen2.5vl:3b`),
    transcribes the crop to an HTML table; degrades to `None` if unavailable.
- Enhanced results are scored (`enhanced_quality`, `S_e`) and attached under the
  low-confidence warning as a `> [!tip]` comparison block. Adoption stays in
  "compare" mode this round (review-only); the geometric main output and its
  position are unchanged.

### Verified constraints (why review-only)
On the colored financial-slide corpus, neither PP-Structure/gridboost nor the
local 3B VLM reliably beats the geometric output across all images: the small
VLM helps table-heavy low-confidence regions (e.g. one matrix slide `S_b` 0.43
-> `S_e` 0.86) but silently drops content on wide/dense tables, and a
whole-page rebuild experiment was validated and then rolled back because a
coverage guard could not guarantee "better or unchanged" on already-correct
slides. Hence enhancement is kept additive, review-only, and default-off, with
byte-for-byte regression parity when disabled.

## Configuration highlights (`config.yaml`)

- `ocr.paddleocr` - detection/recognition thresholds for PaddleOCR.
- `ocr.table_structure.enhance_on_low_quality` - Plan A master switch (default false).
- `ocr.table_structure.backend` - force a tier, or empty to auto-select from the profile.
- `ocr.table_structure.vision_model` / `vision_timeout` - vision-tier settings.
- `ocr.ollama` - endpoint + text-correction model (`qwen2.5:1.5b`).

## Pinned dependencies (do not change)

```text
paddleocr==2.7.3
paddlepaddle==2.6.2
numpy==1.26.4
opencv-python==4.6.0.66
opencv-contrib-python==4.6.0.66
```

Optional: a local Ollama vision model (`ollama pull qwen2.5vl:3b`) for the
`vision` tier; only selected on capable hosts.

## Known limitations / out of scope

- MinerU and Mathpix router branches are placeholders (image OCR path is the
  current scope).
- Side-by-side merged wide tables with no geometric gutter (e.g. one CSR-weight
  slide) still merge under pure geometry; distinguishing them needs semantics,
  not geometry, and is deferred.
- The `vision` tier is slow on CPU-only hosts (minutes per region); it targets
  capable machines and is not force-run here.

## License

MIT License - see `README.md`.

---

**Scope**: Image OCR -> layout-faithful Markdown (Phase: image pipeline + Plan A tiered enhancement).
**Tests**: 59 passed. **Detailed change log**: `TASK_STATUS.md`.
