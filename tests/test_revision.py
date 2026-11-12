"""Regression tests for trainer revision tracking.

Plain pytest: the revision module has no Home Assistant imports, and
pytest_homeassistant_custom_component is not available in this repository.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "mww_trainer" / "revision.py"
)
_SPEC = importlib.util.spec_from_file_location("mww_trainer_revision", _MODULE_PATH)
revision = importlib.util.module_from_spec(_SPEC)
# dataclasses resolves annotations through sys.modules, so register first.
sys.modules[_SPEC.name] = revision
_SPEC.loader.exec_module(revision)

RevisionTracker = revision.RevisionTracker
normalise_hash = revision.normalise_hash
abbreviate = revision.abbreviate
verified_deployment = revision.verified_deployment
may_auto_install = revision.may_auto_install

# Realistic digests: 64 lowercase hex characters.
H_BASELINE = "342c5ec7ce3859607c37db82d273ab6d72af711b4f40a5c73dae9295c85e365a"
H_RELEASE = "94c3fdaf88b9a1e0ce94e4b0f8ce9f3a5a5c9a0e4c1a2b3d4e5f60718293a4b5"
H_OTHER = "0022446688aaccEE0022446688aaccee0022446688aaccee0022446688aaccee"


def tracker_with(*hashes):
    tracker = RevisionTracker()
    for digest in hashes:
        tracker.observe(digest)
    return tracker


# 1. First-time setup establishes a baseline and notifies nothing.
def test_first_discovery_is_a_baseline_without_notification():
    tracker = RevisionTracker()
    status = tracker.observe(H_BASELINE, revision="2026-11-11T04:36:30+00:00")

    assert status.baseline_established is True
    assert status.initial_adoption is True
    assert status.baseline_hash == H_BASELINE
    assert status.new_release is False
    assert status.has_update is False
    # A satellite running something else is not nagged on first adoption.
    assert tracker.status(installed_hash=H_OTHER).has_update is False


# 2. Compiled-only / unreadable fingerprint never enables automatic deployment.
def test_compiled_only_device_is_never_auto_updated():
    tracker = tracker_with(H_BASELINE, H_RELEASE)
    status = tracker.status(installed_hash="unknown")

    assert status.installed_hash is None
    assert status.device_matches_trainer is None
    assert status.has_update is False
    assert may_auto_install(status, automatic=True, device_ready=True) is False
    # Also true when the trainer revision itself is a new release.
    assert status.new_release is True


# 3. The same revision seen again after a restart is not a new release.
def test_unchanged_revision_across_restart_is_not_a_release():
    tracker = tracker_with(H_BASELINE)
    saved = tracker.to_dict()

    restarted = RevisionTracker.from_dict(saved)
    status = restarted.observe(H_BASELINE)

    assert status.new_release is False
    assert status.revision_count == 1
    assert restarted.status(installed_hash=H_BASELINE).has_update is False


# 4. A revision published after the baseline is a release when not installed.
def test_new_revision_is_an_update_when_not_installed():
    tracker = tracker_with(H_BASELINE)
    tracker.observe(H_RELEASE, revision="2026-11-12T01:00:00+00:00")

    status = tracker.status(installed_hash=H_BASELINE)

    assert status.new_release is True
    assert status.has_update is True
    assert may_auto_install(status, automatic=True) is True


# 5. Identical model bytes produce the same digest and never notify again.
def test_identical_bytes_never_notify_twice():
    tracker = tracker_with(H_BASELINE, H_RELEASE)
    before = tracker.to_dict()

    status = tracker.observe(H_RELEASE)

    assert status.new_release is True  # still the newest published revision
    assert tracker.to_dict() == before
    assert tracker.status(installed_hash=H_RELEASE).has_update is False


# 6. A satellite already running the newest revision is up to date.
def test_device_already_running_latest_has_no_update():
    tracker = tracker_with(H_BASELINE, H_RELEASE)
    status = tracker.status(installed_hash=H_RELEASE)

    assert status.device_matches_trainer is True
    assert status.up_to_date is True
    assert status.has_update is False
    assert may_auto_install(status, automatic=True) is False


# 7. Offline or unknown device is unknown, never "up to date".
def test_unknown_device_model_is_not_reported_up_to_date():
    tracker = tracker_with(H_BASELINE, H_RELEASE)
    status = tracker.status(installed_hash=None)

    assert status.installed_hash is None
    assert status.up_to_date is False
    assert status.device_matches_trainer is None
    assert status.has_update is False


# 8. A failed install is not recorded as the deployed model.
def test_failed_install_is_not_recorded():
    # Device still runs the baseline after reporting failure.
    assert verified_deployment(H_RELEASE, H_BASELINE) is None
    # Device reports no fingerprint at all.
    assert verified_deployment(H_RELEASE, "unknown") is None
    assert verified_deployment(H_RELEASE, None) is None
    assert verified_deployment(H_RELEASE, abbreviate(H_RELEASE)) is None


# 9. A successful install that matches exactly becomes up to date.
def test_successful_install_matching_hash_is_up_to_date():
    recorded = verified_deployment(H_RELEASE.upper(), H_RELEASE)

    assert recorded == H_RELEASE
    tracker = tracker_with(H_BASELINE, H_RELEASE)
    status = tracker.status(installed_hash=recorded)
    assert status.has_update is False
    assert status.up_to_date is True


# 10. Multiple satellites share one trainer ledger, per-device status differs.
def test_only_the_outdated_satellite_reports_an_update():
    tracker = tracker_with(H_BASELINE, H_RELEASE)

    kitchen = tracker.status(installed_hash=H_BASELINE)
    office = tracker.status(installed_hash=H_RELEASE)
    unknown = tracker.status(installed_hash=None)

    assert kitchen.has_update is True
    assert office.has_update is False
    assert unknown.has_update is False
    assert may_auto_install(kitchen, automatic=True) is True
    assert may_auto_install(office, automatic=True) is False


# 11. Automatic mode on initial setup must not deploy the baseline.
def test_auto_update_never_deploys_the_initial_baseline():
    tracker = RevisionTracker()
    status = tracker.observe(H_BASELINE)

    assert status.new_release is False
    assert may_auto_install(status, automatic=True, device_ready=True) is False
    # Not even for a satellite running an unrelated/compiled model.
    mismatched = tracker.status(installed_hash=H_OTHER)
    assert may_auto_install(mismatched, automatic=True, device_ready=True) is False


def test_auto_update_stops_after_repeated_failures():
    tracker = tracker_with(H_BASELINE, H_RELEASE)
    status = tracker.status(installed_hash=H_BASELINE)

    assert may_auto_install(status, automatic=True, attempts=0) is True
    assert may_auto_install(status, automatic=True, attempts=2) is True
    assert may_auto_install(status, automatic=True, attempts=3) is False
    assert may_auto_install(status, automatic=False) is False
    assert may_auto_install(status, automatic=True, device_ready=False) is False


# Hex ordering is not chronological ordering.
def test_revision_order_comes_from_observation_not_hex_ordering():
    # The newer revision sorts *before* the baseline as a hex string.
    baseline = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    newer = "0000000000000000000000000000000000000000000000000000000000000000"

    tracker = RevisionTracker()
    tracker.observe(baseline)
    status = tracker.observe(newer)
    assert status.new_release is True
    assert tracker.status(installed_hash=baseline).has_update is True

    # And a rollback to the baseline is not a fresh release either.
    rolled_back = tracker.observe(baseline)
    assert rolled_back.new_release is False
    assert tracker.status(installed_hash=newer).has_update is False


def test_hash_normalisation_rejects_abbreviations_and_junk():
    assert normalise_hash(H_BASELINE.upper()) == H_BASELINE
    assert normalise_hash("  " + H_BASELINE + "  ") == H_BASELINE
    assert normalise_hash(H_BASELINE[:12]) is None
    assert normalise_hash("unknown") is None
    assert normalise_hash("") is None
    assert normalise_hash(None) is None
    assert abbreviate(H_BASELINE) == H_BASELINE[:12]
    assert abbreviate(None) is None


def test_ledger_is_json_round_trip_safe():
    import json

    tracker = tracker_with(H_BASELINE)
    tracker.observe(H_RELEASE, revision="2026-11-12T01:00:00+00:00")
    restored = RevisionTracker.from_dict(json.loads(json.dumps(tracker.to_dict())))

    assert restored.seen_revisions == [H_BASELINE, H_RELEASE]
    assert restored.baseline_hash == H_BASELINE
    assert restored.status().trainer_revision == "2026-11-12T01:00:00+00:00"
    assert RevisionTracker.from_dict(None).status().has_update is False


def test_corrupt_state_does_not_invent_revisions():
    tracker = RevisionTracker({"seen_revisions": ["nonsense", H_BASELINE],
                               "latest_hash": "not-a-hash"})
    assert tracker.seen_revisions == [H_BASELINE]
    assert tracker.baseline_hash == H_BASELINE
    assert tracker.status().new_release is False
