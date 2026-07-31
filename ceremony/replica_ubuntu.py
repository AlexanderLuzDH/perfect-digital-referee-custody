#!/usr/bin/env python3
"""Pure-standard-library HELIOS v5 synthetic Ubuntu replica."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from safe_output import write_new_regular


CHAIN_HASH = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
RELAYS = (
    "https://api.drand.sh",
    "https://api2.drand.sh",
    "https://api3.drand.sh",
)
SUBJECTS = tuple(f"subject-{index:02d}" for index in range(8))
ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PREDICTION_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-PREDICTION\0"
PREDICTION_ROOT_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-PREDICTION-ROOT\0"
RECEIPT_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-REPLICA-RECEIPT\0"
PREPARE_VERIFICATION_DOMAIN = (
    b"JANUS-HELIOS-V5-SYNTHETIC-PREPARE-VERIFICATION\0"
)


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


def fetch_one(base: str, round_number: int) -> dict[str, Any]:
    url = f"{base}/{CHAIN_HASH}/public/{round_number}"
    request = urllib.request.Request(url, headers={"User-Agent": "helios-v5-ubuntu/1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("relay status")
        raw = response.read(4097)
    if len(raw) > 4096:
        raise RuntimeError("relay oversize")
    value = json.loads(raw.decode("ascii"))
    if type(value) is not dict or set(value) != {"round", "randomness", "signature"}:
        raise RuntimeError("relay shape")
    if value["round"] != round_number:
        raise RuntimeError("relay round")
    randomness = value["randomness"]
    signature = value["signature"]
    if (
        type(randomness) is not str
        or type(signature) is not str
        or not ROOT_RE.fullmatch(randomness)
        or re.fullmatch(r"[0-9a-f]{96}", signature) is None
    ):
        raise RuntimeError("relay encoding")
    if hashlib.sha256(bytes.fromhex(signature)).hexdigest() != randomness:
        raise RuntimeError("relay derivation")
    return {
        "base": base,
        "randomness": randomness,
        "round": round_number,
        "signature": signature,
    }


def fetch_quorum(round_number: int) -> tuple[str, str, list[dict[str, Any]]]:
    observations: list[dict[str, Any]] = []
    for attempt in range(6):
        observations = []
        for base in RELAYS:
            try:
                observations.append(fetch_one(base, round_number))
            except Exception:
                pass
        groups: dict[tuple[str, str], int] = {}
        for item in observations:
            key = (item["randomness"], item["signature"])
            groups[key] = groups.get(key, 0) + 1
        winners = [key for key, count in groups.items() if count >= 2]
        if len(winners) == 1:
            randomness, signature = winners[0]
            return randomness, signature, observations
        if attempt != 5:
            time.sleep(5)
    raise RuntimeError("relay quorum")


def build_receipt(
    prepare_root: str,
    round_number: int,
    prepare_verification_sha256: str,
    commit_sha1: str,
    run_id: str,
    run_attempt: int,
) -> dict[str, Any]:
    randomness, signature, observations = fetch_quorum(round_number)
    predictions = []
    for subject in SUBJECTS:
        digest = framed_hash(
            PREDICTION_DOMAIN,
            prepare_root.encode("ascii"),
            randomness.encode("ascii"),
            subject.encode("ascii"),
        )
        predictions.append({"prediction": int(digest[-1], 16) & 1, "subject": subject})
    prediction_root = framed_hash(PREDICTION_ROOT_DOMAIN, canonical(predictions))
    body = {
        "challenge": {
            "chain_hash": CHAIN_HASH,
            "randomness": randomness,
            "round": round_number,
            "signature": signature,
        },
        "implementation": "ubuntu-urllib-v1",
        "os_family": "linux",
        "prediction_root": prediction_root,
        "predictions": predictions,
        "prepare_root": prepare_root,
        "prepare_verification_sha256": prepare_verification_sha256,
        "relay_observations": observations,
        "schema": "janus.helios-v5.synthetic-replica-receipt.v1",
        "workflow": {
            "commit_sha1": commit_sha1,
            "job": "ubuntu-replica",
            "run_attempt": run_attempt,
            "run_id": run_id,
        },
    }
    return {**body, "receipt_root": framed_hash(RECEIPT_DOMAIN, canonical(body))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-root", required=True)
    parser.add_argument("--prepare-verification", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--challenge-round", required=True, type=int)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    if (
        ROOT_RE.fullmatch(arguments.prepare_root) is None
        or COMMIT_RE.fullmatch(arguments.commit) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", arguments.run_id) is None
        or not 1 <= arguments.run_attempt <= 1000
    ):
        raise SystemExit("invalid identity")
    if arguments.challenge_round < 1 or arguments.challenge_round > 2**53 - 1:
        raise SystemExit("invalid challenge round")
    output = Path(arguments.output)
    if output.name != "REPLICA_UBUNTU.json" or output.parent != Path("."):
        raise SystemExit("invalid output")
    verification_path = Path(arguments.prepare_verification)
    if (
        verification_path.parent != Path(".")
        or verification_path.name != "PREPARE_VERIFICATION.json"
        or not verification_path.is_file()
        or verification_path.is_symlink()
        or verification_path.stat().st_size > 8192
    ):
        raise SystemExit("invalid prepare verification")
    verification_raw = verification_path.read_bytes()
    verification = json.loads(verification_raw.decode("ascii"))
    verification_body = dict(verification) if type(verification) is dict else {}
    verification_root = verification_body.pop("verification_root", None)
    if (
        type(verification) is not dict
        or verification_raw != canonical(verification) + b"\n"
        or verification.get("prepare_root") != arguments.prepare_root
        or verification.get("commit_sha1") != arguments.commit
        or verification.get("release_immutable") is not True
        or verification_root != framed_hash(
            PREPARE_VERIFICATION_DOMAIN, canonical(verification_body)
        )
    ):
        raise SystemExit("invalid prepare verification")
    raw = canonical(build_receipt(
        arguments.prepare_root,
        arguments.challenge_round,
        hashlib.sha256(verification_raw).hexdigest(),
        arguments.commit,
        arguments.run_id,
        arguments.run_attempt,
    )) + b"\n"
    write_new_regular(output, raw, "REPLICA_UBUNTU.json", 16384)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
