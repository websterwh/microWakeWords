"""Trainer model revision tracking.

The SHA-256 of the published ``.tflite`` is the only identity of a trained model
revision. A SHA-256 digest is not an ordered version number, so availability is
decided by what the trainer has published since the Integration first saw this
trainer/model pair, never by comparing digests with each other.

This module deliberately has no Home Assistant imports so the decision logic can
be exercised by plain pytest.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

HASH_LENGTH = 64
DISPLAY_LENGTH = 12
MAX_AUTO_ATTEMPTS = 3
_HEX_DIGITS = frozenset("0123456789abcdef")


def normalise_hash(value: Any) -> str | None:
    """Return a lowercase 64-character hex digest, or None if it is not one."""
    if not isinstance(value, str):
        return None
    digest = value.strip().lower()
    if len(digest) != HASH_LENGTH or any(ch not in _HEX_DIGITS for ch in digest):
        return None
    return digest


def abbreviate(digest: Any, length: int = DISPLAY_LENGTH) -> str | None:
    """Shorten a digest for display only. Never compare abbreviated digests."""
    digest = normalise_hash(digest)
    if digest is None:
        return None
    return digest[:max(1, min(int(length), HASH_LENGTH))]


def verified_deployment(expected_hash: Any, reported_hash: Any) -> str | None:
    """Return the digest worth recording after an install, else None.

    A satellite that reports a different digest, or no digest at all (compiled
    fallback or older firmware), must never be recorded as having the model.
    """
    expected = normalise_hash(expected_hash)
    reported = normalise_hash(reported_hash)
    if expected is None or reported is None:
        return None
    return expected if reported == expected else None


@dataclass(frozen=True)
class RevisionStatus:
    """One trainer revision, seen from one satellite's point of view."""

    trainer_hash: str | None = None
    trainer_revision: str | None = None
    trainer_run_id: str | None = None
    baseline_hash: str | None = None
    installed_hash: str | None = None
    new_release: bool = False
    baseline_established: bool = False
    revision_count: int = 0

    @property
    def initial_adoption(self) -> bool:
        """The current revision is the baseline, not an announced release."""
        return bool(
            self.trainer_hash and self.baseline_hash
            and self.trainer_hash == self.baseline_hash
        )

    @property
    def device_matches_trainer(self) -> bool | None:
        """True/False when the satellite reports a digest, None when unknown."""
        if not self.trainer_hash or not self.installed_hash:
            return None
        return self.trainer_hash == self.installed_hash

    @property
    def up_to_date(self) -> bool:
        """Proof of an up-to-date satellite: an exact digest match."""
        return self.device_matches_trainer is True

    @property
    def has_update(self) -> bool:
        """A *new* trainer revision the satellite is not already running.

        A satellite whose installed model is unknown is never reported as
        having an update available; the update entity shows an unknown state
        instead of claiming either "up to date" or "update available".
        """
        return bool(
            self.new_release
            and self.trainer_hash
            and self.installed_hash
            and self.installed_hash != self.trainer_hash
        )

    def with_device(self, installed_hash: Any) -> "RevisionStatus":
        return replace(self, installed_hash=normalise_hash(installed_hash))


class RevisionTracker:
    """Ledger of the trainer revisions observed for one trainer URL + model."""

    def __init__(self, state: dict | None = None):
        state = state if isinstance(state, dict) else {}
        seen: list[str] = []
        for value in state.get("seen_revisions") or []:
            digest = normalise_hash(value)
            if digest and digest not in seen:
                seen.append(digest)
        self.seen_revisions = seen
        self.baseline_hash = normalise_hash(state.get("baseline_hash")) or (seen[0] if seen else None)
        self.revisions: dict[str, dict] = {}
        saved_revisions = state.get("revisions")
        if isinstance(saved_revisions, dict):
            for key, meta in saved_revisions.items():
                digest = normalise_hash(key)
                if digest and isinstance(meta, dict):
                    self.revisions[digest] = {
                        "revision": meta.get("revision"),
                        "run_id": meta.get("run_id"),
                        "first_seen": meta.get("first_seen"),
                    }
        latest = normalise_hash(state.get("latest_hash"))
        if latest and latest not in self.seen_revisions:
            # Repair a partially written ledger rather than losing the revision.
            self.seen_revisions.append(latest)
        self.latest_hash = latest or (self.seen_revisions[-1] if self.seen_revisions else None)
        self.established_at = state.get("established_at")

    def observe(self, trainer_hash: Any, revision: Any = None,
                run_id: Any = None, now: str | None = None) -> RevisionStatus:
        """Record a trainer revision and report whether it is a new release."""
        digest = normalise_hash(trainer_hash)
        if digest is None:
            return self.status()
        stamp = now or datetime.now(timezone.utc).isoformat()
        initial_adoption = False
        if not self.seen_revisions:
            # First revision ever seen is the baseline, not a release to install.
            self.seen_revisions = [digest]
            self.baseline_hash = digest
            self.established_at = stamp
            initial_adoption = True
        elif digest not in self.seen_revisions:
            self.seen_revisions.append(digest)
        meta = self.revisions.setdefault(digest, {"revision": None, "run_id": None, "first_seen": None})
        if not meta.get("first_seen"):
            meta["first_seen"] = stamp
        if revision is not None:
            meta["revision"] = str(revision)
        if run_id is not None:
            meta["run_id"] = str(run_id)
        self.latest_hash = digest
        return self.status()

    def status(self, installed_hash: Any = None) -> RevisionStatus:
        digest = self.latest_hash
        meta = self.revisions.get(digest) if digest else None
        meta = meta or {}
        # Position in the observation history, not digest ordering: a rollback
        # to the baseline revision is never advertised as a new release.
        new_release = bool(
            digest and digest in self.seen_revisions
            and self.seen_revisions.index(digest) > 0
        )
        return RevisionStatus(
            trainer_hash=digest,
            trainer_revision=meta.get("revision"),
            trainer_run_id=meta.get("run_id"),
            baseline_hash=self.baseline_hash,
            installed_hash=normalise_hash(installed_hash),
            new_release=new_release,
            baseline_established=bool(self.baseline_hash),
            revision_count=len(self.seen_revisions),
        )

    def to_dict(self) -> dict:
        return {
            "baseline_hash": self.baseline_hash,
            "seen_revisions": list(self.seen_revisions),
            "latest_hash": self.latest_hash,
            "revisions": {
                digest: dict(meta) for digest, meta in self.revisions.items()
            },
            "established_at": self.established_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RevisionTracker":
        return cls(data if isinstance(data, dict) else {})


def may_auto_install(status: RevisionStatus, *, automatic: bool,
                     device_ready: bool = True, attempts: int = 0,
                     max_attempts: int = MAX_AUTO_ATTEMPTS) -> bool:
    """Automatic deployment gate.

    Only a revision published after the established baseline is deployable, so
    the initial adoption baseline can never be installed automatically even
    when the satellite currently runs a different (or compiled-only) model.
    """
    if not automatic or not device_ready:
        return False
    if not status.has_update:
        return False
    return attempts < max_attempts
