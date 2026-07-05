"""
Test snapshot tool for the OCR -> Markdown pipeline.

Purpose (iteration workflow):
  Each test run archives the rendered Markdown for a set of sample images into a
  timestamped snapshot directory, together with per-image metrics. A later run
  can then be diffed against the previous snapshot so regressions or
  improvements between iterations are explicit and reviewable.

Usage (PowerShell):
  # Run OCR on samples, archive a new snapshot, and auto-compare with the
  # previous one:
  python test_snapshot.py run

  # Run on specific images only:
  python test_snapshot.py run --images "D:\test-temp\png\a.jpg" "D:\...\b.jpg"

  # Reuse cached OCR blocks (skip slow OCR when the cache exists):
  python test_snapshot.py run --use-cache

  # Compare the two most recent snapshots (no OCR):
  python test_snapshot.py compare

  # Compare two explicit snapshots:
  python test_snapshot.py compare --old <name> --new <name>

  # List archived snapshots:
  python test_snapshot.py list

Notes:
  - OCR is slow (~60-100s/image). --use-cache reuses per-image OCR block JSON
    stored under <archive>/_cache so layout-only iterations are fast.
  - Front-matter "date:" lines are ignored when diffing so day rollovers do not
    show up as content changes.
"""

import argparse
import datetime as _dt
import difflib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402

DEFAULT_INPUT_DIR = r"D:\test-temp\png"
DEFAULT_ARCHIVE = r"D:\test-temp\ocr_output\_snapshots"
CACHE_DIRNAME = "_cache"
CONFIG_PATH = "config.yaml"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}


def _load_config():
    with io.open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _list_images(input_dir):
    files = []
    for name in sorted(os.listdir(input_dir)):
        if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
            files.append(os.path.join(input_dir, name))
    return files


def _strip_volatile(text):
    """Drop lines that legitimately change between runs (e.g. the date)."""
    return [ln for ln in text.splitlines() if not ln.startswith("date:")]


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(text)


def _snapshot_dirs(archive):
    if not os.path.isdir(archive):
        return []
    dirs = [
        d for d in os.listdir(archive)
        if d != CACHE_DIRNAME and os.path.isdir(os.path.join(archive, d))
        and os.path.exists(os.path.join(archive, d, "manifest.json"))
    ]
    return sorted(dirs)


def _load_manifest(archive, snap):
    path = os.path.join(archive, snap, "manifest.json")
    return json.load(io.open(path, encoding="utf-8"))


def _run_ocr(image_path, handler, cache_dir, use_cache):
    """Return corrected_result for an image, using/refreshing the block cache."""
    name = os.path.splitext(os.path.basename(image_path))[0]
    cache_path = os.path.join(cache_dir, name + ".json")
    if use_cache and os.path.exists(cache_path):
        return json.load(io.open(cache_path, encoding="utf-8")), True
    result = handler.process(image_path)
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "processing failed")
    corrected = result["ocr_result"]
    os.makedirs(cache_dir, exist_ok=True)
    json.dump(corrected, io.open(cache_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    return corrected, False


def _metrics(name, markdown, corrected):
    md = markdown or ""
    return {
        "image": name,
        "chars": len(md),
        "lines": md.count("\n") + 1,
        "table_rows": md.count("| --- |"),
        "external_tables": len(corrected.get("tables", []) or []),
        "ocr_blocks": len(corrected.get("blocks", []) or []),
        "confidence": round(float(corrected.get("confidence", 0) or 0), 4),
    }


def cmd_run(args):
    from processors.image_handler import ImageHandler
    from processors.markdown_generator import MarkdownGenerator

    config = _load_config()
    archive = args.archive
    cache_dir = os.path.join(archive, CACHE_DIRNAME)
    os.makedirs(cache_dir, exist_ok=True)

    if args.images:
        images = list(args.images)
    else:
        images = _list_images(args.input_dir)
    if not images:
        print("[ERROR] no images found in", args.input_dir)
        return 1

    handler = None
    generator = MarkdownGenerator(config)

    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snap_dir = os.path.join(archive, stamp)
    os.makedirs(snap_dir, exist_ok=True)

    manifest = {
        "snapshot": stamp,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "input_dir": args.input_dir,
        "image_count": len(images),
        "items": [],
    }

    print("=" * 64)
    print("Test snapshot:", stamp, "| images:", len(images))
    print("=" * 64)

    for idx, image_path in enumerate(images, 1):
        name = os.path.splitext(os.path.basename(image_path))[0]
        try:
            if handler is None and not (args.use_cache and os.path.exists(
                    os.path.join(cache_dir, name + ".json"))):
                handler = ImageHandler(config)
                handler.initialize()
            corrected, cached = _run_ocr(image_path, handler, cache_dir, args.use_cache)
            markdown = generator.process(corrected, image_path, link_candidates=[])
            _write(os.path.join(snap_dir, name + ".md"), markdown)
            m = _metrics(name, markdown, corrected)
            m["cached"] = cached
            manifest["items"].append(m)
            print("[%2d/%2d] %s  chars=%d tables=%d conf=%.3f%s"
                  % (idx, len(images), name, m["chars"], m["table_rows"],
                     m["confidence"], " (cache)" if cached else ""))
        except Exception as exc:  # noqa: BLE001
            manifest["items"].append({"image": name, "error": str(exc)})
            print("[%2d/%2d] %s  ERROR: %s" % (idx, len(images), name, exc))

    if handler is not None:
        handler.cleanup()

    json.dump(manifest, io.open(os.path.join(snap_dir, "manifest.json"), "w",
              encoding="utf-8"), ensure_ascii=False, indent=2)
    _write(os.path.join(archive, "latest.txt"), stamp + "\n")
    print("\nSnapshot archived at:", snap_dir)

    # Auto-compare with the previous snapshot.
    snaps = _snapshot_dirs(archive)
    if len(snaps) >= 2:
        print()
        _compare(archive, snaps[-2], snaps[-1])
    else:
        print("\n(no previous snapshot to compare; this is the baseline)")
    return 0


def _compare(archive, old, new):
    old_dir = os.path.join(archive, old)
    new_dir = os.path.join(archive, new)
    old_mf = {i["image"]: i for i in _load_manifest(archive, old)["items"]}
    new_mf = {i["image"]: i for i in _load_manifest(archive, new)["items"]}

    print("=" * 64)
    print("Compare:  OLD", old, " ->  NEW", new)
    print("=" * 64)

    names = sorted(set(old_mf) | set(new_mf))
    added, removed, changed, unchanged = [], [], [], []
    for name in names:
        old_md_path = os.path.join(old_dir, name + ".md")
        new_md_path = os.path.join(new_dir, name + ".md")
        has_old = os.path.exists(old_md_path)
        has_new = os.path.exists(new_md_path)
        if has_new and not has_old:
            added.append(name)
            continue
        if has_old and not has_new:
            removed.append(name)
            continue
        old_lines = _strip_volatile(_read(old_md_path))
        new_lines = _strip_volatile(_read(new_md_path))
        if old_lines == new_lines:
            unchanged.append(name)
        else:
            changed.append(name)

    print("unchanged: %d | changed: %d | added: %d | removed: %d"
          % (len(unchanged), len(changed), len(added), len(removed)))

    for name in added:
        print("\n[ADDED]   ", name)
    for name in removed:
        print("\n[REMOVED] ", name)

    for name in changed:
        print("\n" + "-" * 64)
        print("[CHANGED] ", name)
        om, nm = old_mf.get(name, {}), new_mf.get(name, {})
        print("  metrics: chars %s->%s | table_rows %s->%s | conf %s->%s"
              % (om.get("chars"), nm.get("chars"),
                 om.get("table_rows"), nm.get("table_rows"),
                 om.get("confidence"), nm.get("confidence")))
        old_lines = _strip_volatile(_read(os.path.join(old_dir, name + ".md")))
        new_lines = _strip_volatile(_read(os.path.join(new_dir, name + ".md")))
        diff = difflib.unified_diff(old_lines, new_lines, lineterm="", n=1)
        for line in diff:
            print("  " + line)

    return 0


def cmd_compare(args):
    snaps = _snapshot_dirs(args.archive)
    if len(snaps) < 2 and not (args.old and args.new):
        print("[ERROR] need at least two snapshots to compare; have:", snaps)
        return 1
    old = args.old or snaps[-2]
    new = args.new or snaps[-1]
    if old not in snaps or new not in snaps:
        print("[ERROR] unknown snapshot. available:", snaps)
        return 1
    return _compare(args.archive, old, new)


def cmd_list(args):
    snaps = _snapshot_dirs(args.archive)
    if not snaps:
        print("(no snapshots under", args.archive, ")")
        return 0
    print("Snapshots under", args.archive)
    for snap in snaps:
        try:
            mf = _load_manifest(args.archive, snap)
            n = mf.get("image_count", len(mf.get("items", [])))
            errs = sum(1 for i in mf.get("items", []) if "error" in i)
            print("  %s  images=%s errors=%d" % (snap, n, errs))
        except Exception:  # noqa: BLE001
            print("  %s  (manifest unreadable)" % snap)
    return 0


def main():
    parser = argparse.ArgumentParser(description="OCR test snapshot / compare tool")
    parser.add_argument("--archive", default=DEFAULT_ARCHIVE,
                        help="snapshot archive root (default: %s)" % DEFAULT_ARCHIVE)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run pipeline, archive a snapshot, auto-compare")
    p_run.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
    p_run.add_argument("--images", nargs="*", help="explicit image paths")
    p_run.add_argument("--use-cache", action="store_true",
                       help="reuse cached OCR blocks when present")
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="compare two snapshots")
    p_cmp.add_argument("--old")
    p_cmp.add_argument("--new")
    p_cmp.set_defaults(func=cmd_compare)

    p_ls = sub.add_parser("list", help="list archived snapshots")
    p_ls.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
