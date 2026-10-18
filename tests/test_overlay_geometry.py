"""Preview-only geometry regressions using a tiny real image and archive."""
from copy import deepcopy
import io
import xml.etree.ElementTree as ET
import zipfile

import cv2
import numpy as np
import pytest

from core.pipeline import _overlay_coordinates, get_annotated_preview_image
from backend.integrated_api import PREFIX, DATASET_PREFIX
from tests.test_integrated_api import integrated_api, auth, upload_fixture_via_api


def box(coords=(10, 10, 40, 40), label=""):
    return {"label": label, **dict(zip(("xtl", "ytl", "xbr", "ybr"), coords))}


@pytest.fixture
def preview_archive():
    image = np.full((64, 64, 3), 100, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return image, encoded.tobytes()


def archive_reader(image_bytes):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("images/test.png", image_bytes)
    buffer.seek(0)
    return {"archive": buffer}


@pytest.mark.parametrize("coords,expected", [
    ((10, 10, 40, 40), (10, 10, 40, 40)),
    (("10.4", "10.6", "40.4", "40.6"), (10, 11, 40, 41)),
    ((-20, -10, 100, 80), (0, 0, 63, 63)),
    ((-1e300, -1e300, 1e300, 1e300), (0, 0, 63, 63)),
    ((20, 10, 10, 40), None), ((10, 40, 40, 10), None),
    ((10, 10, 10, 40), None), ((10, 10, 40, 10), None),
    ((-100, -100, -10, -10), None), ((100, 100, 200, 200), None),
    ((10.1, 10.1, 10.2, 10.2), None),
])
def test_overlay_coordinates_clip_or_skip(coords, expected):
    assert _overlay_coordinates(box(coords), 64, 64) == expected


@pytest.mark.parametrize("key", ["xtl", "ytl", "xbr", "ybr"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"),
                                   "NaN", "+Infinity", "-Infinity", None, "bad", 10**400])
def test_nonfinite_and_unconvertible_coordinates_are_skipped(key, value):
    item = box()
    item[key] = value
    assert _overlay_coordinates(item, 64, 64) is None


@pytest.mark.parametrize("key", ["xtl", "ytl", "xbr", "ybr"])
def test_missing_coordinate_is_skipped(key):
    item = box()
    del item[key]
    assert _overlay_coordinates(item, 64, 64) is None


def test_mixed_boxes_render_valid_pixels_and_keep_class_text(preview_archive):
    image, raw = preview_archive
    valid = box((10.4, 10.4, 40.4, 40.4), "car")
    invalid = [box((v, 10, 40, 40)) for v in (float("nan"), float("inf"), -float("inf"), 10**400)]
    invalid += [box((40, 10, 10, 40)), box((100, 100, 200, 200)),
                {"xtl": 10}, box((None, 10, 40, 40)), box(("bad", 10, 40, 40))]
    actual = get_annotated_preview_image(archive_reader(raw), "images/test.png", [*invalid, valid])
    expected = image.copy()
    cv2.rectangle(expected, (10, 10), (40, 40), (0, 255, 0), 2)
    cv2.putText(expected, "car", (10, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    np.testing.assert_array_equal(actual, expected)


def test_invalid_boxes_do_not_draw_and_clipped_boxes_stay_in_image(preview_archive):
    image, raw = preview_archive
    invalid = [box((30, 10, 10, 40)), box((10, 10, 10, 40)), box((-100, -100, -1, -1))]
    np.testing.assert_array_equal(
        get_annotated_preview_image(archive_reader(raw), "images/test.png", invalid), image)
    clipped = get_annotated_preview_image(
        archive_reader(raw), "images/test.png", [box((-1e300, -1e300, 1e300, 1e300))])
    np.testing.assert_array_equal(clipped[0, 30], [0, 255, 0])
    np.testing.assert_array_equal(clipped[63, 30], [0, 255, 0])
    np.testing.assert_array_equal(clipped[30, 30], image[30, 30])


def test_preview_does_not_mutate_source_annotations(preview_archive):
    _, raw = preview_archive
    items = [box((-10, -10, 100, 100), "car"), box(("Infinity", 10, 40, 40))]
    original = deepcopy(items)
    get_annotated_preview_image(archive_reader(raw), "images/test.png", items)
    assert items == original


def test_real_preview_request_survives_bad_boxes(integrated_api, preview_archive):
    client, _, owners = integrated_api
    _, raw = preview_archive
    root = ET.Element("annotations")
    image = ET.SubElement(root, "image", id="0", name="images/test.png", width="64", height="64")
    items = [box(label="car"), box(("NaN", 10, 40, 40)), box(("Infinity", 10, 40, 40)),
             box(("-Infinity", 10, 40, 40)), box((40, 10, 10, 40)),
             box((-1e300, -1e300, 1e300, 1e300)), box(("bad", 10, 40, 40))]
    for item in items:
        ET.SubElement(image, "box", **{key: str(value) for key, value in item.items()})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("images/test.png", raw)
        archive.writestr("annotations.xml", ET.tostring(root))
    session = upload_fixture_via_api(client, owners, buffer.getvalue())
    headers = auth(owners, session)
    response = client.post(f"{PREFIX}/{session['upload_id']}/validate", headers=headers)
    assert response.status_code == 200 and response.json()["ready"]
    dataset = response.json()["dataset_id"]
    preview = client.get(f"{DATASET_PREFIX}/{dataset}/preview", headers=headers,
                         params={"image_path": "images/test.png", "annotated": True})
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    pixels = cv2.imdecode(np.frombuffer(preview.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert pixels.shape == (64, 64, 3)
    np.testing.assert_array_equal(pixels[30, 10], [0, 255, 0])
    np.testing.assert_array_equal(pixels[0, 30], [0, 255, 0])
