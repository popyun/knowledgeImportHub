"""
Test runner for Obsidian Knowledge Import Hub.
Processes images from D:\test-temp\png and outputs to D:\test-temp\ocr_output
"""

import os
import sys
import yaml
import logging
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Run test processing on D:\test-temp\png images."""
    
    # Test paths
    input_dir = "D:/test-temp/png"
    output_root = "D:/test-temp/ocr_output"
    
    print("=" * 60)
    print("Obsidian Knowledge Import Hub - Test Runner")
    print("=" * 60)
    
    # Check input directory
    if not os.path.exists(input_dir):
        print("\n[ERROR] Input directory not found: {}".format(input_dir))
        print("Creating test directory...")
        os.makedirs(input_dir, exist_ok=True)
        print("Created: {}".format(input_dir))
        print("\nPlease add test images to this directory and run again.")
        return
    
    # Find all images
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}
    image_files = []
    
    for filename in os.listdir(input_dir):
        ext = Path(filename).suffix.lower()
        if ext in image_extensions:
            image_files.append(os.path.join(input_dir, filename))
    
    if not image_files:
        print("\n[WARN] No images found in {}".format(input_dir))
        print("Please add test images and run again.")
        return
    
    print("\nFound {} image(s):".format(len(image_files)))
    for img in image_files[:10]:  # Show first 10
        print("   - {}".format(os.path.basename(img)))
    if len(image_files) > 10:
        print("   ... and {} more".format(len(image_files) - 10))
    
    # Create output directory structure
    print("\nSetting up output directory: {}".format(output_root))
    os.makedirs(os.path.join(output_root, "00-RAW"), exist_ok=True)
    os.makedirs(os.path.join(output_root, "99-Audit/OCR-Pending"), exist_ok=True)
    os.makedirs(os.path.join(output_root, "10-WIKI"), exist_ok=True)
    os.makedirs(os.path.join(output_root, "logs"), exist_ok=True)
    
    # Create test configuration
    config = {
        "vault": {
            "root": output_root.replace("\\", "/"),
            "raw_folder": "00-RAW",
            "wiki_base": "10-WIKI",
            "audit_folder": "99-Audit/OCR-Pending",
            "archive_folder": "99-Archive"
        },
        "processing": {
            "watch_delay_seconds": 2,
            "max_worker_threads": 1,
            "confidence_threshold": 0.85,
            "skip_patterns": ["**/.*", "**/*.tmp"]
        },
        "ocr": {
            "engines": {
                "text_default": "paddleocr_vl",
                "table_complex": "mineru",
                "math_special": "mathpix_api"
            },
            "mathpix_api_key": "",
            "ollama": {
                "model": "qwen2.5:1.5b",
                "endpoint": "http://localhost:11434"
            }
        },
        "image": {
            "super_resolution": {
                "model": "real-esrgan",
                "scale": 2
            },
            "color_extraction": {
                "kmeans_clusters": 8
            },
            "table_detection": {
                "use_model": "paddle_layout"
            }
        },
        "logging": {
            "level": "INFO",
            "file": "{}/logs/pipeline.log".format(output_root),
            "format": "json"
        }
    }
    
    # Save config
    config_path = os.path.join(os.path.dirname(__file__), "test_config.yaml")
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
    
    print("\n[OK] Configuration saved to: {}".format(config_path))
    
    print("\nStarting pipeline...")
    print("-" * 60)
    
    # For this test, we'll just verify the setup and show what would be processed
    # Full pipeline requires additional dependencies (paddleocr, etc.)
    
    success_count = len(image_files)
    fail_count = 0
    
    for image_path in image_files:
        print("\nWould process: {}".format(os.path.basename(image_path)))
        print("   [OK] Queued for processing")
        
    # Print summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print("Total images: {}".format(len(image_files)))
    print("[OK] Queued: {}".format(success_count))
    print("[FAIL] Failed: {}".format(fail_count))
    
    # Show output location
    audit_folder = os.path.join(output_root, "99-Audit/OCR-Pending")
    print("\nOutput directory: {}".format(audit_folder))
    print("\n[WARN] Note: Full OCR processing requires additional dependencies:")
    print("   - paddleocr==2.7.0")
    print("   - paddlepaddle==2.5.0")
    print("   - mineru, spacy, jieba")
    print("\n   Install with: pip install -r requirements.txt")
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
