"""Disk-backed CVAT XML parser and distribution projections from SQLite index.

Consumes the indexed xml_nodes and entries tables created during Stage 4 archive
validation. Reconstructs identical DataFrames as the legacy byte-oriented
parsers while avoiding unbounded memory retention or repeated XML parsing.
"""
from __future__ import annotations

import io
import json
from pathlib import PurePosixPath
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def norm_path(s: str) -> str:
    """Normalize member path for path matching, preserving legacy semantics."""
    return str(s).replace("\\", "/").lstrip("./").lower()


def load_cvat_from_index(index_conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconstruct cvat_images and cvat_boxes DataFrames from SQLite index.
    
    Produces columns and rows identical to legacy load_cvat().
    """
    img_files = [r[0] for r in index_conn.execute("SELECT name FROM entries WHERE kind='image' ORDER BY id").fetchall()]
    exact = {norm_path(n): n for n in img_files}
    basename: dict[str, list[str]] = {}
    for n in img_files:
        basename.setdefault(PurePosixPath(norm_path(n)).name, []).append(n)

    images: list[dict] = []
    boxes: list[dict] = []
    image_rows = index_conn.execute("SELECT id, attributes FROM xml_nodes WHERE tag='image' ORDER BY id").fetchall()

    for idx, (img_node_id, attrs_json) in enumerate(image_rows):
        attrs = json.loads(attrs_json)
        image_id = attrs.get("id", str(idx))
        name = attrs.get("name", f"image_{idx}")
        path = exact.get(norm_path(name))
        if path is None:
            candidates = [v for k, v in exact.items() if k.endswith("/" + norm_path(name))]
            if len(candidates) == 1:
                path = candidates[0]
        if path is None:
            candidates = basename.get(PurePosixPath(norm_path(name)).name, [])
            if len(candidates) == 1:
                path = candidates[0]
        w = int(attrs.get("width", "0"))
        h = int(attrs.get("height", "0"))
        images.append(dict(image_id=image_id, image_name=name, image_path=path, width=w, height=h))

        box_rows = index_conn.execute(
            "SELECT id, attributes FROM xml_nodes WHERE parent=? AND tag='box' ORDER BY id",
            (img_node_id,)
        ).fetchall()
        for k, (_, box_attrs_json) in enumerate(box_rows):
            box_attrs = json.loads(box_attrs_json)
            try:
                coords = [float(box_attrs.get(p)) for p in ("xtl", "ytl", "xbr", "ybr")]
            except (ValueError, TypeError):
                coords = [float("nan")] * 4
            boxes.append(dict(
                image_id=image_id,
                image_name=name,
                box_id=f"{idx}:{k}",
                label=box_attrs.get("label", "unknown"),
                occluded=box_attrs.get("occluded", "0"),
                xtl=coords[0],
                ytl=coords[1],
                xbr=coords[2],
                ybr=coords[3]
            ))

    return pd.DataFrame(images), pd.DataFrame(boxes)


def parse_distribution_from_index(index_conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Reconstruct distribution images, boxes, and attribute names from SQLite index.
    
    Produces columns and rows identical to legacy parse_distribution().
    """
    images: list[dict] = []
    boxes: list[dict] = []
    attrs: set[str] = set()

    image_rows = index_conn.execute("SELECT id, attributes FROM xml_nodes WHERE tag='image' ORDER BY id").fetchall()
    for img_node_id, attrs_json in image_rows:
        im = json.loads(attrs_json)
        iid = str(im.get("id", ""))
        name = im.get("name", "")
        w = int(im.get("width", "0"))
        h = int(im.get("height", "0"))
        tags: dict[str, str] = {}

        tag_rows = index_conn.execute(
            "SELECT id, attributes FROM xml_nodes WHERE parent=? AND tag='tag' ORDER BY id",
            (img_node_id,)
        ).fetchall()
        for tag_id, tag_attrs_json in tag_rows:
            tag_attrs = json.loads(tag_attrs_json)
            key = tag_attrs.get("label", "")
            attr_rows = index_conn.execute(
                "SELECT attributes, text FROM xml_nodes WHERE parent=? AND tag='attribute' ORDER BY id",
                (tag_id,)
            ).fetchall()
            first_text = attr_rows[0][1] if attr_rows else None
            val = first_text or tag_attrs.get("value")
            for a_attrs_json, a_text in attr_rows:
                a_dict = json.loads(a_attrs_json)
                if a_dict.get("name") in ("weather", "timeofday"):
                    tags[a_dict.get("name")] = a_text
            if key in ("weather", "timeofday") and val:
                tags[key] = val
            if key == "scene_info":
                for a_attrs_json, a_text in attr_rows:
                    a_dict = json.loads(a_attrs_json)
                    tags[a_dict.get("name", "")] = a_text

        images.append(dict(
            image_id=iid,
            image_name=name,
            weather=tags.get("weather") or "missing",
            timeofday=tags.get("timeofday") or "missing"
        ))

        box_rows = index_conn.execute(
            "SELECT id, attributes FROM xml_nodes WHERE parent=? AND tag='box' ORDER BY id",
            (img_node_id,)
        ).fetchall()
        for idx, (box_id, box_attrs_json) in enumerate(box_rows):
            b = json.loads(box_attrs_json)
            attr_rows = index_conn.execute(
                "SELECT attributes, text FROM xml_nodes WHERE parent=? AND tag='attribute' ORDER BY id",
                (box_id,)
            ).fetchall()
            a = {json.loads(a_attrs_json).get("name", ""): a_text for a_attrs_json, a_text in attr_rows}
            attrs.update(a)
            try:
                x1, y1, x2, y2 = [float(b.get(k)) for k in ("xtl", "ytl", "xbr", "ybr")]
                valid = bool(x2 > x1 and y2 > y1 and w > 0 and h > 0)
                area = 100 * (x2 - x1) * (y2 - y1) / (w * h) if valid else float("nan")
            except (ValueError, TypeError):
                valid = False
                area = float("nan")

            boxes.append(dict(
                image_id=iid,
                image_name=name,
                box_id=f"{iid}:{idx}",
                label=b.get("label", "unknown"),
                occluded=b.get("occluded", "0"),
                valid_geometry=valid,
                area_pct=area,
                attributes=a
            ))

    return (
        pd.DataFrame(images, columns=["image_id", "image_name", "weather", "timeofday"]),
        pd.DataFrame(boxes, columns=["image_id", "image_name", "box_id", "label", "occluded", "valid_geometry", "area_pct", "attributes"]),
        sorted(attrs)
    )
