"""Worker-side archive integrity, guarded image decode and disk index construction."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import warnings

from datasets.archive import inspect, chunks
from datasets.limits import Limits, ValidationError
from datasets.xmlsafe import parse_xml
from storage.files import regular, sync_file, sync_dir

IMAGES = {".jpg":"JPEG", ".jpeg":"JPEG", ".png":"PNG", ".bmp":"BMP", ".webp":"WEBP"}


def digest_file(path, block=65536):
    regular(path)
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while piece := file.read(block):
            digest.update(piece)
    return digest.hexdigest()


def validate_image(path, suffix, limits):
    from PIL import Image, UnidentifiedImageError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error",Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width,height = image.size
                if (width <= 0 or height <= 0 or width > limits.image_dimension or height > limits.image_dimension
                        or width*height > limits.image_pixels):
                    raise ValidationError("IMAGE_PIXEL_LIMIT")
                if image.format != IMAGES[suffix] or getattr(image,"n_frames",1) != 1:
                    raise ValidationError("UNSUPPORTED_IMAGE_FORMAT")
                image.verify()
            # verify() is not a complete pixel decode. Reopen under process memory
            # and per-image wall-time guards, and reject decoder errors explicitly.
            with Image.open(path) as image:
                image.load()
        return width,height
    except ValidationError:
        raise
    except (Image.DecompressionBombWarning,Image.DecompressionBombError):
        raise ValidationError("IMAGE_PIXEL_LIMIT") from None
    except Exception:
        raise ValidationError("IMAGE_DECODE_FAILED") from None


def validate_archive(payload: Path, work: Path, dataset_id: str, limits: Limits, emit=lambda message: None):
    regular(payload)
    before = payload.stat()
    if before.st_size > limits.archive_bytes:
        raise ValidationError("ARCHIVE_SIZE_LIMIT")
    archive_sha = digest_file(payload,limits.block_bytes)
    index = work / "index.sqlite3"
    regular(index)
    if index.exists():
        raise ValidationError("WORKSPACE_NOT_EMPTY")
    db = sqlite3.connect(index)
    try:
        db.execute("PRAGMA page_size=4096")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=EXTRA")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA cache_size=-2048")
        db.execute(f"PRAGMA max_page_count={max(1,limits.index_bytes//4096)}")
        db.executescript("CREATE TABLE entries (id INTEGER PRIMARY KEY,name TEXT UNIQUE,kind TEXT,expanded INTEGER,compressed INTEGER,width INTEGER,height INTEGER);"
                         "CREATE TABLE xml_nodes (id INTEGER PRIMARY KEY,parent INTEGER,tag TEXT,attributes TEXT,text TEXT);")
        counters, image_count, annotation_count = [0],0,0
        xml_counts = {"image":0,"box":0,"tag":0,"nodes":0}
        with payload.open("rb") as source:
            members,size = inspect(source,limits)
            for ordinal, member in enumerate(members):
                suffix = Path(member.name).suffix.lower()
                kind = "directory" if member.directory else "image" if suffix in IMAGES else "xml" if suffix == ".xml" else "other"
                if kind == "image" and member.expanded > limits.image_bytes:
                    raise ValidationError("IMAGE_BYTE_LIMIT")
                if kind == "xml" and (member.expanded > limits.xml_bytes or member.key.split("/")[-1] != "annotations.xml"):
                    raise ValidationError("XML_SIZE_OR_NAME_LIMIT")
                if kind == "xml":
                    annotation_count += 1
                    if annotation_count > 1:
                        raise ValidationError("AMBIGUOUS_ANNOTATIONS")
                spool = work / "entry.tmp"
                if kind in {"image","xml"}:
                    regular(spool)
                    with spool.open("xb") as output:
                        for block in chunks(source,member,limits,counters):
                            output.write(block)
                else:
                    for _ in chunks(source,member,limits,counters):
                        pass
                width = height = None
                if kind == "image":
                    emit({"phase":"decode_start"})
                    width,height = validate_image(spool,suffix,limits)
                    emit({"phase":"decode_end"})
                    image_count += 1
                if kind == "xml":
                    with spool.open("rb") as file:
                        xml_counts = parse_xml(file,db,limits)
                if kind in {"image","xml"}:
                    spool.unlink()
                db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?)",
                           (ordinal,member.name,kind,member.expanded,member.compressed,width,height))
                db.commit()
        if image_count == 0:
            raise ValidationError("NO_IMAGES")
        after = payload.stat()
        if (before.st_size,before.st_mtime_ns,before.st_ino) != (after.st_size,after.st_mtime_ns,after.st_ino):
            raise ValidationError("PAYLOAD_CHANGED")
        emit({"phase":"indexing"})
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValidationError("INDEX_INTEGRITY_FAILED")
        db.commit()
    finally:
        db.close()
    if index.stat().st_size > limits.index_bytes:
        raise ValidationError("INDEX_SIZE_LIMIT")
    with index.open("r+b") as file:
        sync_file(file)
    manifest = {"version":1,"dataset_id":dataset_id,"archive_bytes":size,"archive_sha256":archive_sha,
                "index_sha256":digest_file(index),"entries":len(members),"images":image_count,
                "expanded_bytes":counters[0],"annotations":bool(annotation_count),"xml_records":xml_counts,
                "limits":asdict(limits),"validated":True}
    raw = json.dumps(manifest,sort_keys=True).encode()
    if len(raw) > 65536:
        raise ValidationError("MANIFEST_SIZE_LIMIT")
    with (work / "manifest.json").open("xb") as file:
        file.write(raw)
        sync_file(file)
    sync_dir(work)
    return manifest
