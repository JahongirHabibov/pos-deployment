"""Unit tests for the deployment-repo resolution that feeds the manifest fetch.

The installer prefills the IMAGE_* fields from manifest.json on the deployment
repo. Before this, the repo name came ONLY from an existing .env — so on a first
install (no .env yet) the field stayed empty, no fetch ran, and the release-tag
box never appeared. Run: python3 -m pytest test_deployment_repo.py
(or python3 test_deployment_repo.py for a plain assert run).
"""

from installer import (
    DEFAULT_DEPLOYMENT_REPO,
    _is_usable_repo,
    _manifest_url,
    _resolve_deployment_repo,
)


def test_default_repo_is_concrete():
    assert _is_usable_repo(DEFAULT_DEPLOYMENT_REPO)


def test_usable_repo_accepts_owner_slash_name():
    assert _is_usable_repo("JahongirHabibov/pos-deployment")
    assert _is_usable_repo("some-org/pos-deployment")
    assert _is_usable_repo("org.name/repo_name")


def test_usable_repo_rejects_env_example_placeholder():
    # .env.example historically shipped this; it has a slash but 404s on raw.
    assert not _is_usable_repo("<org>/pos-deployment")


def test_usable_repo_rejects_incomplete_values():
    assert not _is_usable_repo("")
    assert not _is_usable_repo("   ")
    assert not _is_usable_repo("pos-deployment")          # no owner
    assert not _is_usable_repo("org/repo/extra")          # too many segments
    assert not _is_usable_repo("org /repo")               # whitespace inside


def test_resolve_falls_back_to_default_on_empty():
    # First install: no .env at all → the field must still get a working repo.
    assert _resolve_deployment_repo("") == DEFAULT_DEPLOYMENT_REPO


def test_resolve_falls_back_to_default_on_placeholder():
    assert _resolve_deployment_repo("<org>/pos-deployment") == DEFAULT_DEPLOYMENT_REPO


def test_resolve_keeps_operator_value():
    assert _resolve_deployment_repo(" other-org/pos-deploy ") == "other-org/pos-deploy"


def test_manifest_url_is_cache_busted():
    # raw.githubusercontent.com serves cache-control: max-age=300 — without a
    # unique query the CDN can hand back a manifest up to 5 minutes stale.
    a = _manifest_url("org/repo", "main")
    b = _manifest_url("org/repo", "main")
    assert a.startswith(
        "https://raw.githubusercontent.com/org/repo/main/manifest.json?")
    assert a != b


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK — {len(fns)} tests passed")
    sys.exit(0)
