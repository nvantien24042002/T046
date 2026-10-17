# Stage 5: Core Pipeline Adaptation

Date: 2026-10-16 (Asia/Saigon). This checkpoint documents the **disk-backed pipeline adaptation**, integrating core QA/QC processing with `DatasetManager.reader` without UI modification or premature service exposure. Stop for review before Stage 6.

## Scope and architecture

Stage 5 adapts the repository's core processing logic to consume validated datasets directly through the leased reader context (`DatasetManager.reader()`), eliminating large whole-archive and uncompressed dataset accumulation in memory.

All existing Stage 1/2 prototype files, Stage 3 durable transport files, Stage 4 validation and dataset manager files, and legacy baseline source files remain strictly preserved and bit-for-bit unchanged. Preservation is enforced by:
- `tests/baseline/source_hashes.json`
- `tests/stage3/protected_sources.json`
- `tests/stage4/protected_sources.json`
- `tests/stage5/protected_sources.json`

No commits, pushes, or branch modifications were performed.

### Deliverables in `core/` and `tests/`

| File | Responsibility |
| --- | --- |
| `core/cvat_parser.py` | Extracts and projects CVAT XML records from the SQLite index (`xml_nodes` and `entries`). Constructs identical `(cvat_images, cvat_boxes)` and `(distribution_images, distribution_boxes, attrs)` DataFrames as legacy `load_cvat` and `parse_distribution` without reparsing XML or scanning archive files. |
| `core/image_quality.py` | Standalone Image Quality module. Provides `analyze_image`, `estimate_noise` (MAD on low-gradient Sobel regions), and `run_image_quality` with streaming iteration over indexed images from a seekable archive. Peak RAM is strictly bounded to one single decoded image at a time, avoiding `image_bytes` retention in scalar result DataFrames. |
| `core/label_audit.py` | Standalone Label Check A+B audit module. Provides `iou`, `visual_metrics`, and `run_label_audit` iterating over boxes, opening and decoding only the single referenced image from the archive on-demand, and calculating geometry checks and visual signal checks with exact baseline status and priority semantics. |
| `core/pipeline.py` | End-to-end adapter executing complete dataset processing (`execute_pipeline`) over an active `DatasetManager.reader` context (`summary`, `archive`, `index`). Provides bounded single-image preview (`get_preview_image`) and annotated box drawing (`get_annotated_preview_image`) with size guard protection. |
| `tests/stage5/protected_sources.json` | Manifest recording SHA-256 digests of all 11 Stage 4 protected source files. |
| `tests/test_stage5_preservation.py` | Automated preservation test for Stage 4 source files. |
| `tests/test_core_pipeline.py` | Comprehensive test suite verifying 100% mathematical and schema parity against baseline `expected.json`, bounded memory behavior, image-only dataset handling, and preview generation. |

## Memory and resource management

1. **No full archive accumulation in RAM**:
   - The reader's `archive` handle is an open seekable binary file (`payload.part.open("rb")`).
   - `zipfile.ZipFile(archive)` opens the central directory in O(1) memory without reading file member data into RAM.
2. **Bounded per-image decoding**:
   - In `run_image_quality` and `run_label_audit`, member bytes are read and decoded with `cv2.imdecode` only for the current image. The decoded NumPy array and raw buffer are released on each iteration before proceeding to the next image.
   - Result dictionaries omit raw `image_bytes`, preventing multi-gigabyte DataFrame memory inflation.
3. **Zero XML re-parsing overhead**:
   - CVAT XML hierarchy and attributes are retrieved directly from SQLite `xml_nodes` and `entries` tables built once during Stage 4 validation.
4. **Reliable resource closure**:
   - `DatasetManager.reader()` context manager ensures that the SQLite index connection and payload file handle are closed immediately upon context exit, releasing reader leases.
