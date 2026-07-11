"""Unit tests for the pure manifest → IMAGE_* mapping helpers in installer.py.

These cover only the Tk-free functions (_image_tag, _manifest_service_rows) that
drive the "auto-set latest release tags" feature. Run: python3 -m pytest test_manifest_rows.py
(or python3 test_manifest_rows.py for a plain assert run).
"""

from installer import _image_tag, _manifest_service_rows, _MANIFEST_FIELDS


def _manifest(**services):
    return {"updated_at": "2026-07-10T21:46:21Z", "services": services}


def test_image_tag_splits_last_colon():
    assert _image_tag("ghcr.io/ns/pos-backend:0.5.11") == "0.5.11"


def test_image_tag_registry_port_unaffected():
    assert _image_tag("registry.local:5000/ns/pos-backend:1.2.3") == "1.2.3"


def test_image_tag_no_tag_returns_ref():
    assert _image_tag("ghcr.io/ns/pos-backend") == "ghcr.io/ns/pos-backend"


def test_rows_full_ref_and_tag():
    rows = _manifest_service_rows(_manifest(
        backend={"image": "ghcr.io/ns/pos-backend:0.5.11"},
    ))
    assert rows == [{
        "service": "backend",
        "field": "image_backend",
        "image": "ghcr.io/ns/pos-backend:0.5.11",
        "tag": "0.5.11",
    }]


def test_rows_preserve_manifest_fields_order():
    # Provide services out of order; result must follow _MANIFEST_FIELDS order.
    rows = _manifest_service_rows(_manifest(
        backup={"image": "ghcr.io/ns/pos-backup:1"},
        backend={"image": "ghcr.io/ns/pos-backend:1"},
        frontend={"image": "ghcr.io/ns/pos-frontend:1"},
    ))
    assert [r["service"] for r in rows] == ["backend", "frontend", "backup"]
    assert list(_MANIFEST_FIELDS).index("backend") == 0


def test_rows_skip_missing_and_empty():
    rows = _manifest_service_rows(_manifest(
        backend={"image": "ghcr.io/ns/pos-backend:1"},
        frontend={"image": ""},            # empty image → skipped
        updater={"version": "v1"},         # no image key → skipped
        # image-service / backup absent → skipped
    ))
    assert [r["service"] for r in rows] == ["backend"]


def test_rows_empty_manifest():
    assert _manifest_service_rows({}) == []
    assert _manifest_service_rows({"services": "nonsense"}) == []


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
    sys.exit(0)
