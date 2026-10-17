import io
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import zipfile

import numpy as np
from PIL import Image
import pytest

from datasets.archive import chunks, inspect, Member
from datasets.limits import Limits, ValidationError
from datasets.validate import digest_file, validate_archive, validate_image
from datasets.xmlsafe import parse_xml
from tests.fixtures import make_zip, XML


def make_minimal_image_png(width=16, height=16, color=(100, 100, 100)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def test_inspect_and_chunks_valid_zip(tmp_path):
    archive_path = tmp_path / "valid.zip"
    make_zip(archive_path)
    limits = Limits()
    with archive_path.open("rb") as f:
        members, size = inspect(f, limits)
        assert size == archive_path.stat().st_size
        assert len(members) == 3
        names = [m.name for m in members]
        assert "annotations.xml" in names
        assert "images/flat.png" in names
        assert "images/texture.png" in names

        # Verify chunks decompression and CRC
        counters = [0]
        for m in members:
            decompressed = b"".join(chunks(f, m, limits, counters))
            assert len(decompressed) == m.expanded


def test_inspect_rejects_undersized_file(tmp_path):
    small = tmp_path / "small.zip"
    small.write_bytes(b"PK\x05\x06" + b"\x00" * 10)
    with small.open("rb") as f, pytest.raises(ValidationError):
        inspect(f, Limits())


def test_inspect_rejects_missing_eocd(tmp_path):
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"\x00" * 100)
    with corrupt.open("rb") as f, pytest.raises(ValidationError):
        inspect(f, Limits())


def test_inspect_rejects_archive_size_limit(tmp_path):
    archive_path = tmp_path / "valid.zip"
    make_zip(archive_path)
    with archive_path.open("rb") as f, pytest.raises(ValidationError) as exc:
        inspect(f, Limits(archive_bytes=100))
    assert "ARCHIVE_SIZE_LIMIT" in str(exc.value)


def test_inspect_rejects_entry_count_limit(tmp_path):
    archive_path = tmp_path / "valid.zip"
    make_zip(archive_path)
    with archive_path.open("rb") as f, pytest.raises(ValidationError) as exc:
        inspect(f, Limits(entries=1))
    assert "DIRECTORY_RESOURCE_LIMIT" in str(exc.value)


def test_inspect_rejects_unsafe_traversal_path(tmp_path):
    bad = tmp_path / "traversal.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../escaped.txt", b"data")
    with bad.open("rb") as f, pytest.raises(ValidationError) as exc:
        inspect(f, Limits())
    assert "UNSAFE_MEMBER_PATH" in str(exc.value)


def test_inspect_rejects_absolute_path(tmp_path):
    bad = tmp_path / "absolute.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("/etc/passwd", b"root:x:0:0")
    with bad.open("rb") as f, pytest.raises(ValidationError) as exc:
        inspect(f, Limits())
    assert "UNSAFE_MEMBER_PATH" in str(exc.value)


def test_inspect_rejects_member_collision(tmp_path):
    bad = tmp_path / "collision.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("test.txt", b"one")
        z.writestr("test.txt", b"two")
    with bad.open("rb") as f, pytest.raises(ValidationError) as exc:
        inspect(f, Limits())
    assert "MEMBER_COLLISION" in str(exc.value)


def test_inspect_rejects_file_directory_collision(tmp_path):
    bad = tmp_path / "fdir.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("foo", b"file content")
        z.writestr("foo/bar.txt", b"nested")
    with bad.open("rb") as f, pytest.raises(ValidationError) as exc:
        inspect(f, Limits())
    assert "FILE_DIRECTORY_COLLISION" in str(exc.value)


def test_inspect_rejects_compression_ratio_limit(tmp_path):
    bad = tmp_path / "ratio.zip"
    zeros = b"\x00" * 500000
    with zipfile.ZipFile(bad, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", zeros)
    with bad.open("rb") as f, pytest.raises(ValidationError) as exc:
        inspect(f, Limits(ratio=5))
    assert "COMPRESSION_RATIO_LIMIT" in str(exc.value)


def test_chunks_rejects_actual_expansion_limit(tmp_path):
    archive_path = tmp_path / "valid.zip"
    make_zip(archive_path)
    limits = Limits(entry_bytes=100)
    with archive_path.open("rb") as f:
        # inspect passes if we pass generous limits to inspect
        members, _ = inspect(f, Limits(entry_bytes=50000))
        counters = [0]
        with pytest.raises(ValidationError) as exc:
            for m in members:
                for _ in chunks(f, m, limits, counters):
                    pass
        assert "ACTUAL_EXPANSION_LIMIT" in str(exc.value)


def test_xmlsafe_cvat_parsing(tmp_path):
    db_path = tmp_path / "nodes.db"
    db = sqlite3.connect(db_path)
    db.executescript("CREATE TABLE xml_nodes (id INTEGER PRIMARY KEY,parent INTEGER,tag TEXT,attributes TEXT,text TEXT);")
    counts = parse_xml(io.BytesIO(XML.encode("utf-8")), db, Limits())
    db.commit()
    assert counts["image"] == 3
    assert counts["box"] == 5
    assert counts["tag"] == 2
    row_count = db.execute("SELECT COUNT(*) FROM xml_nodes").fetchone()[0]
    assert row_count == counts["nodes"]
    db.close()


def test_xmlsafe_forbids_dtd(tmp_path):
    dtd_xml = b'<!DOCTYPE foo [<!ELEMENT foo ANY>]><annotations><image id="1" name="a.png" width="10" height="10"/></annotations>'
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE xml_nodes (id INTEGER PRIMARY KEY,parent INTEGER,tag TEXT,attributes TEXT,text TEXT);")
    with pytest.raises(ValidationError) as exc:
        parse_xml(io.BytesIO(dtd_xml), db, Limits())
    assert "XML_DTD_OR_ENTITY_FORBIDDEN" in str(exc.value)


def test_xmlsafe_forbids_entity_expansion(tmp_path):
    entity_xml = b'<!DOCTYPE foo [<!ENTITY test "hacked">]><annotations><image id="1" name="&test;.png" width="10" height="10"/></annotations>'
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE xml_nodes (id INTEGER PRIMARY KEY,parent INTEGER,tag TEXT,attributes TEXT,text TEXT);")
    with pytest.raises(ValidationError) as exc:
        parse_xml(io.BytesIO(entity_xml), db, Limits())
    assert "XML_DTD_OR_ENTITY_FORBIDDEN" in str(exc.value)


def test_xmlsafe_rejects_duplicate_image_id():
    dup_xml = b'<annotations><image id="1" name="a.png" width="10" height="10"/><image id="1" name="b.png" width="10" height="10"/></annotations>'
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE xml_nodes (id INTEGER PRIMARY KEY,parent INTEGER,tag TEXT,attributes TEXT,text TEXT);")
    with pytest.raises(ValidationError) as exc:
        parse_xml(io.BytesIO(dup_xml), db, Limits())
    assert "DUPLICATE_CVAT_IMAGE_ID" in str(exc.value)


def test_xmlsafe_rejects_missing_box_coordinates():
    bad_box = b'<annotations><image id="1" name="a.png" width="10" height="10"><box label="car" xtl="0" ytl="0"/></image></annotations>'
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE xml_nodes (id INTEGER PRIMARY KEY,parent INTEGER,tag TEXT,attributes TEXT,text TEXT);")
    with pytest.raises(ValidationError) as exc:
        parse_xml(io.BytesIO(bad_box), db, Limits())
    assert "INVALID_CVAT_BOX" in str(exc.value)


def test_validate_archive_success(tmp_path):
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    work = tmp_path / "work"
    work.mkdir()
    limits = Limits()
    manifest = validate_archive(archive_path, work, "test-ds-1", limits)
    assert manifest["dataset_id"] == "test-ds-1"
    assert manifest["validated"] is True
    assert manifest["entries"] == 3
    assert manifest["images"] == 2
    assert manifest["annotations"] is True
    assert (work / "manifest.json").exists()
    assert (work / "index.sqlite3").exists()

    # Verify index integrity
    conn = sqlite3.connect(work / "index.sqlite3")
    entry_rows = conn.execute("SELECT id, name, kind, width, height FROM entries").fetchall()
    assert len(entry_rows) == 3
    by_name = {r[1]: r for r in entry_rows}
    assert by_name["annotations.xml"][2] == "xml"
    assert by_name["images/flat.png"][2] == "image" and by_name["images/flat.png"][3] == 64 and by_name["images/flat.png"][4] == 64
    assert by_name["images/texture.png"][2] == "image" and by_name["images/texture.png"][3] == 64 and by_name["images/texture.png"][4] == 64
    conn.close()


def test_validate_archive_image_only(tmp_path):
    archive_path = tmp_path / "image_only.zip"
    make_zip(archive_path, image_only=True)
    work = tmp_path / "work_img"
    work.mkdir()
    manifest = validate_archive(archive_path, work, "test-ds-img", Limits())
    assert manifest["annotations"] is False
    assert manifest["images"] == 2


def test_validate_archive_no_images(tmp_path):
    archive_path = tmp_path / "no_images.zip"
    with zipfile.ZipFile(archive_path, "w") as z:
        z.writestr("annotations.xml", XML)
    work = tmp_path / "work_no_img"
    work.mkdir()
    with pytest.raises(ValidationError) as exc:
        validate_archive(archive_path, work, "test-ds-fail", Limits())
    assert "NO_IMAGES" in str(exc.value)


def test_validate_archive_ambiguous_annotations(tmp_path):
    archive_path = tmp_path / "multi_xml.zip"
    with zipfile.ZipFile(archive_path, "w") as z:
        z.writestr("images/a.png", make_minimal_image_png())
        z.writestr("annotations.xml", XML)
        z.writestr("sub/annotations.xml", XML)
    work = tmp_path / "work_multi"
    work.mkdir()
    with pytest.raises(ValidationError) as exc:
        validate_archive(archive_path, work, "test-ds-multi", Limits())
    assert "AMBIGUOUS_ANNOTATIONS" in str(exc.value)


def test_validate_archive_pixel_limit(tmp_path):
    archive_path = tmp_path / "big_img.zip"
    with zipfile.ZipFile(archive_path, "w") as z:
        z.writestr("images/big.png", make_minimal_image_png(width=100, height=100))
    work = tmp_path / "work_pixel"
    work.mkdir()
    with pytest.raises(ValidationError) as exc:
        validate_archive(archive_path, work, "test-ds-pixel", Limits(image_pixels=5000))
    assert "IMAGE_PIXEL_LIMIT" in str(exc.value)
