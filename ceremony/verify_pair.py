#!/usr/bin/env python3
"""Strict offline checker for a pair of HELIOS v5 synthetic receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


CHAIN_HASH = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
SUBJECTS = tuple(f"subject-{index:02d}" for index in range(8))
ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PREDICTION_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-PREDICTION\0"
PREDICTION_ROOT_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-PREDICTION-ROOT\0"
RECEIPT_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-REPLICA-RECEIPT\0"
PAIR_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-PAIR\0"


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


def read_receipt(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError("bad receipt file")
    raw = path.read_bytes()
    value = json.loads(raw.decode("ascii"), object_pairs_hook=no_duplicates)
    if type(value) is not dict or raw != canonical(value) + b"\n":
        raise ValueError("non-canonical receipt")
    return value, raw


def check_receipt(
    value: dict[str, Any],
    prepare_root: str,
    challenge_round: int,
    implementation: str,
    os_family: str,
    commit_sha1: str,
    run_id: str,
    run_attempt: int,
    job: str,
    prepare_verification_sha256: str,
) -> None:
    if set(value) != {
        "challenge",
        "implementation",
        "os_family",
        "prediction_root",
        "predictions",
        "prepare_root",
        "prepare_verification_sha256",
        "receipt_root",
        "relay_observations",
        "schema",
        "workflow",
    }:
        raise ValueError("receipt shape")
    if value["schema"] != "janus.helios-v5.synthetic-replica-receipt.v1":
        raise ValueError("receipt schema")
    if value["implementation"] != implementation or value["os_family"] != os_family:
        raise ValueError("replica identity")
    if value["prepare_root"] != prepare_root:
        raise ValueError("prepare binding")
    if value["prepare_verification_sha256"] != prepare_verification_sha256:
        raise ValueError("prepare verification binding")
    if value["workflow"] != {
        "commit_sha1": commit_sha1,
        "job": job,
        "run_attempt": run_attempt,
        "run_id": run_id,
    }:
        raise ValueError("workflow binding")
    challenge = value["challenge"]
    if (
        type(challenge) is not dict
        or set(challenge) != {"chain_hash", "randomness", "round", "signature"}
        or challenge["chain_hash"] != CHAIN_HASH
        or challenge["round"] != challenge_round
        or ROOT_RE.fullmatch(challenge["randomness"]) is None
        or re.fullmatch(r"[0-9a-f]{96}", challenge["signature"]) is None
        or hashlib.sha256(bytes.fromhex(challenge["signature"])).hexdigest()
        != challenge["randomness"]
    ):
        raise ValueError("challenge")
    observations = value["relay_observations"]
    if type(observations) is not list or not 2 <= len(observations) <= 3:
        raise ValueError("observations")
    bases = set()
    agreeing = 0
    for observation in observations:
        if (
            type(observation) is not dict
            or set(observation) != {"base", "randomness", "round", "signature"}
            or observation["base"] not in {
                "https://api.drand.sh",
                "https://api2.drand.sh",
                "https://api3.drand.sh",
            }
            or observation["base"] in bases
            or observation["round"] != challenge_round
            or type(observation["randomness"]) is not str
            or ROOT_RE.fullmatch(observation["randomness"]) is None
            or type(observation["signature"]) is not str
            or re.fullmatch(r"[0-9a-f]{96}", observation["signature"]) is None
            or hashlib.sha256(bytes.fromhex(observation["signature"])).hexdigest()
            != observation["randomness"]
        ):
            raise ValueError("observation")
        bases.add(observation["base"])
        if (
            observation["randomness"] == challenge["randomness"]
            and observation["signature"] == challenge["signature"]
        ):
            agreeing += 1
    if agreeing < 2:
        raise ValueError("relay quorum")
    predictions = value["predictions"]
    if type(predictions) is not list or len(predictions) != 8:
        raise ValueError("prediction count")
    expected = []
    for subject in SUBJECTS:
        digest = framed_hash(
            PREDICTION_DOMAIN,
            prepare_root.encode("ascii"),
            challenge["randomness"].encode("ascii"),
            subject.encode("ascii"),
        )
        expected.append({"prediction": int(digest[-1], 16) & 1, "subject": subject})
    if predictions != expected:
        raise ValueError("predictions")
    if value["prediction_root"] != framed_hash(
        PREDICTION_ROOT_DOMAIN, canonical(expected)
    ):
        raise ValueError("prediction root")
    body = dict(value)
    receipt_root = body.pop("receipt_root")
    if receipt_root != framed_hash(RECEIPT_DOMAIN, canonical(body)):
        raise ValueError("receipt root")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ubuntu", required=True)
    parser.add_argument("--windows", required=True)
    parser.add_argument("--prepare-root", required=True)
    parser.add_argument("--prepare-verification-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--challenge-round", required=True, type=int)
    arguments = parser.parse_args()
    if (
        ROOT_RE.fullmatch(arguments.prepare_root) is None
        or ROOT_RE.fullmatch(arguments.prepare_verification_sha256) is None
        or COMMIT_RE.fullmatch(arguments.commit) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", arguments.run_id) is None
        or not 1 <= arguments.run_attempt <= 1000
    ):
        raise SystemExit("invalid identity")
    ubuntu, ubuntu_raw = read_receipt(Path(arguments.ubuntu))
    windows, windows_raw = read_receipt(Path(arguments.windows))
    check_receipt(
        ubuntu,
        arguments.prepare_root,
        arguments.challenge_round,
        "ubuntu-urllib-v1",
        "linux",
        arguments.commit,
        arguments.run_id,
        arguments.run_attempt,
        "ubuntu-replica",
        arguments.prepare_verification_sha256,
    )
    check_receipt(
        windows,
        arguments.prepare_root,
        arguments.challenge_round,
        "windows-http-client-v1",
        "windows",
        arguments.commit,
        arguments.run_id,
        arguments.run_attempt,
        "windows-replica",
        arguments.prepare_verification_sha256,
    )
    if ubuntu["challenge"] != windows["challenge"]:
        raise ValueError("challenge disagreement")
    if ubuntu["predictions"] != windows["predictions"]:
        raise ValueError("prediction disagreement")
    pair = {
        "challenge": ubuntu["challenge"],
        "prediction_root": ubuntu["prediction_root"],
        "prepare_root": arguments.prepare_root,
        "prepare_verification_sha256": arguments.prepare_verification_sha256,
        "ubuntu_receipt_sha256": hashlib.sha256(ubuntu_raw).hexdigest(),
        "windows_receipt_sha256": hashlib.sha256(windows_raw).hexdigest(),
        "workflow": {
            "commit_sha1": arguments.commit,
            "run_attempt": arguments.run_attempt,
            "run_id": arguments.run_id,
        },
    }
    result = {
        "pair_root": framed_hash(PAIR_DOMAIN, canonical(pair)),
        "prediction_root": ubuntu["prediction_root"],
        "schema": "janus.helios-v5.synthetic-pair-verification.v1",
        "verdict": "PASS_EXACT_DUAL_REPLICA_AGREEMENT",
    }
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
