#!/usr/bin/env python3
"""Verify a canonical immutable PREPARE release before replica execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from safe_output import write_new_regular


REPOSITORY = "AlexanderLuzDH/perfect-digital-referee-custody"
CHAIN_HASH = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
GENESIS_TIME = 1692803367
PERIOD_SECONDS = 3
SUBJECTS = [f"subject-{index:02d}" for index in range(8)]
ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PREPARE_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-PREPARE\0"
VERIFICATION_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-PREPARE-VERIFICATION\0"
SOURCE_PATHS = (
    ".github/workflows/helios-synthetic-replicas.yml",
    "ceremony/ARTIFACT_DOWNLOAD_KAT.json",
    "ceremony/PROTOCOL.md",
    "ceremony/make_finalize_candidate.py",
    "ceremony/replica_ubuntu.py",
    "ceremony/replica_windows.py",
    "ceremony/safe_output.py",
    "ceremony/score_reveal.py",
    "ceremony/verify_finalize.py",
    "ceremony/verify_pair.py",
    "ceremony/verify_prepare.py",
)
DOMAINS = {
    "finalize_candidate": "JANUS-HELIOS-V5-SYNTHETIC-FINALIZE-CANDIDATE\\0",
    "finalize": "JANUS-HELIOS-V5-SYNTHETIC-FINALIZE\\0",
    "finalize_verification": "JANUS-HELIOS-V5-SYNTHETIC-FINALIZE-VERIFICATION\\0",
    "label_rank": "JANUS-HELIOS-V5-SYNTHETIC-LABEL-RANK\\0",
    "label_root": "JANUS-HELIOS-V5-SYNTHETIC-LABEL-ROOT\\0",
    "pair": "JANUS-HELIOS-V5-SYNTHETIC-PAIR\\0",
    "prediction": "JANUS-HELIOS-V5-SYNTHETIC-PREDICTION\\0",
    "prediction_root": "JANUS-HELIOS-V5-SYNTHETIC-PREDICTION-ROOT\\0",
    "prepare": "JANUS-HELIOS-V5-SYNTHETIC-PREPARE\\0",
    "receipt": "JANUS-HELIOS-V5-SYNTHETIC-REPLICA-RECEIPT\\0",
    "reveal": "JANUS-HELIOS-V5-SYNTHETIC-REVEAL\\0",
}


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def framed_hash(domain: bytes, *chunks: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(domain)
    for chunk in chunks:
        digest.update(len(chunk).to_bytes(8, "big"))
        digest.update(chunk)
    return digest.hexdigest()


def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def fetch_json(url: str, limit: int = 131072) -> tuple[Any, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "helios-v5-prepare-verifier/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("HTTP status")
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise RuntimeError("HTTP oversize")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates), raw


def round_time(round_number: int) -> int:
    return GENESIS_TIME + (round_number - 1) * PERIOD_SECONDS


def parse_utc(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("naive time")
    return int(parsed.astimezone(timezone.utc).timestamp())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-root", required=True)
    parser.add_argument("--prepare-sha256", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--executed-commit", required=True)
    parser.add_argument("--challenge-round", required=True, type=int)
    parser.add_argument("--reveal-round", required=True, type=int)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    if (
        ROOT_RE.fullmatch(arguments.prepare_root) is None
        or ROOT_RE.fullmatch(arguments.prepare_sha256) is None
        or COMMIT_RE.fullmatch(arguments.expected_commit) is None
        or COMMIT_RE.fullmatch(arguments.executed_commit) is None
        or arguments.expected_commit != arguments.executed_commit
        or not 1 <= arguments.challenge_round < arguments.reveal_round <= 2**53 - 1
    ):
        raise SystemExit("invalid arguments")
    output = Path(arguments.output)
    if output.parent != Path(".") or output.name != "PREPARE_VERIFICATION.json":
        raise SystemExit("invalid output")

    tag = "helios-v5-prepare-" + arguments.prepare_root
    encoded_tag = urllib.parse.quote(tag, safe="")
    release_url = (
        f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{encoded_tag}"
    )
    release, _release_raw = fetch_json(release_url)
    if (
        type(release) is not dict
        or release.get("tag_name") != tag
        or release.get("immutable") is not True
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("target_commitish") != arguments.expected_commit
        or type(release.get("assets")) is not list
        or len(release["assets"]) != 1
    ):
        raise RuntimeError("release")
    asset = release["assets"][0]
    if (
        asset.get("name") != "PREPARE.json"
        or asset.get("state") != "uploaded"
        or asset.get("digest") != "sha256:" + arguments.prepare_sha256
        or type(asset.get("size")) is not int
        or asset["size"] > 32768
    ):
        raise RuntimeError("asset metadata")
    prepare, prepare_raw = fetch_json(asset["browser_download_url"], 32768)
    if (
        type(prepare) is not dict
        or prepare_raw != canonical(prepare) + b"\n"
        or hashlib.sha256(prepare_raw).hexdigest() != arguments.prepare_sha256
    ):
        raise RuntimeError("prepare bytes")
    body = dict(prepare)
    supplied_root = body.pop("prepare_root", None)
    if supplied_root != arguments.prepare_root or supplied_root != framed_hash(
        PREPARE_DOMAIN, canonical(body)
    ):
        raise RuntimeError("prepare root")
    if set(body) != {
        "authority",
        "commit_sha1",
        "domains",
        "drand",
        "repository",
        "schema",
        "source_sha256",
        "subjects",
        "threshold_correct",
    }:
        raise RuntimeError("prepare shape")
    expected_authority = {
        "C1": "SYNTHETIC_EIGHT_SUBJECT_RELATION_ONLY",
        "C2": "GITHUB_IMMUTABLE_RELEASE_AND_TLS_RELAY_TRUST_ONLY",
        "C3": "GITHUB_DECLARED_JOB_LIMITS_ONLY",
        "C4": "PENDING_AUTHENTICATED_RUN_EVIDENCE",
        "model_science": "NONE",
        "real_labels": "NONE",
        "target": "NONE",
    }
    drand = body["drand"]
    if (
        body["schema"] != "janus.helios-v5.synthetic-prepare.v1"
        or body["repository"] != REPOSITORY
        or body["commit_sha1"] != arguments.expected_commit
        or body["subjects"] != SUBJECTS
        or body["domains"] != DOMAINS
        or body["threshold_correct"] != 6
        or body["authority"] != expected_authority
        or type(drand) is not dict
        or drand != {
            "chain_hash": CHAIN_HASH,
            "challenge_round": arguments.challenge_round,
            "challenge_scheduled_unix": round_time(arguments.challenge_round),
            "genesis_time": GENESIS_TIME,
            "period_seconds": PERIOD_SECONDS,
            "reveal_round": arguments.reveal_round,
            "reveal_scheduled_unix": round_time(arguments.reveal_round),
            "signature_authenticity": "UNVERIFIED_BLS_TLS_RELAY_QUORUM_ONLY",
        }
    ):
        raise RuntimeError("prepare semantics")
    source_hashes = {}
    root = Path(".").resolve()
    for relative in SOURCE_PATHS:
        path = root / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
            raise RuntimeError("source file")
        source_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if body["source_sha256"] != source_hashes:
        raise RuntimeError("source hashes")

    published_unix = parse_utc(release["published_at"])
    challenge_unix = round_time(arguments.challenge_round)
    if published_unix >= challenge_unix:
        raise RuntimeError("late prepare")
    receipt_body = {
        "asset_digest": asset["digest"],
        "asset_id": asset["id"],
        "asset_size": asset["size"],
        "challenge_scheduled_unix": challenge_unix,
        "commit_sha1": arguments.executed_commit,
        "prepare_margin_seconds": challenge_unix - published_unix,
        "prepare_root": arguments.prepare_root,
        "prepare_sha256": arguments.prepare_sha256,
        "published_at": release["published_at"],
        "published_unix": published_unix,
        "release_id": release["id"],
        "release_immutable": True,
        "reveal_scheduled_unix": round_time(arguments.reveal_round),
        "schema": "janus.helios-v5.synthetic-prepare-verification.v1",
        "tag": tag,
    }
    receipt = {
        **receipt_body,
        "verification_root": framed_hash(VERIFICATION_DOMAIN, canonical(receipt_body)),
    }
    write_new_regular(
        output,
        canonical(receipt) + b"\n",
        "PREPARE_VERIFICATION.json",
        8192,
    )
    print(canonical(receipt).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
