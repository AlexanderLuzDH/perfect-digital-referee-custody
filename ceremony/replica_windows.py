#!/usr/bin/env python3
"""Independent pure-standard-library HELIOS v5 synthetic Windows replica."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import time
from pathlib import Path

from safe_output import write_new_regular


CHAIN = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
HOSTS = ("api.drand.sh", "api2.drand.sh", "api3.drand.sh")
HEX64 = re.compile("[0-9a-f]{64}")
HEX96 = re.compile("[0-9a-f]{96}")
HEX40 = re.compile("[0-9a-f]{40}")
PREPARE_VERIFICATION_DOMAIN = (
    b"JANUS-HELIOS-V5-SYNTHETIC-PREPARE-VERIFICATION\0"
)


def encode_json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def framed(domain, chunks):
    state = hashlib.sha256()
    state.update(domain)
    for part in chunks:
        state.update(len(part).to_bytes(8, byteorder="big", signed=False))
        state.update(part)
    return state.hexdigest()


def query(host, requested_round):
    connection = http.client.HTTPSConnection(host, timeout=15)
    route = "/" + CHAIN + "/public/" + str(requested_round)
    try:
        connection.request("GET", route, headers={"User-Agent": "helios-v5-windows/1"})
        response = connection.getresponse()
        payload = response.read(4097)
        status = response.status
    finally:
        connection.close()
    if status != 200 or len(payload) > 4096:
        raise RuntimeError("bad relay response")
    record = json.loads(payload.decode("ascii"))
    if sorted(record.keys()) != ["randomness", "round", "signature"]:
        raise RuntimeError("bad relay fields")
    if record["round"] != requested_round:
        raise RuntimeError("wrong relay round")
    random_text = record["randomness"]
    signature_text = record["signature"]
    if not isinstance(random_text, str) or HEX64.fullmatch(random_text) is None:
        raise RuntimeError("bad randomness")
    if not isinstance(signature_text, str) or HEX96.fullmatch(signature_text) is None:
        raise RuntimeError("bad signature")
    derived = hashlib.sha256(bytearray.fromhex(signature_text)).hexdigest()
    if derived != random_text:
        raise RuntimeError("bad signature hash")
    return {
        "base": "https://" + host,
        "randomness": random_text,
        "round": requested_round,
        "signature": signature_text,
    }


def obtain(requested_round):
    observations = []
    for attempt in range(6):
        observations.clear()
        for host in HOSTS:
            try:
                observations.append(query(host, requested_round))
            except Exception:
                continue
        counts = {}
        for observation in observations:
            pair = (observation["randomness"], observation["signature"])
            counts[pair] = counts.setdefault(pair, 0) + 1
        accepted = [pair for pair in counts if counts[pair] >= 2]
        if len(accepted) == 1:
            return accepted[0][0], accepted[0][1], list(observations)
        if attempt < 5:
            time.sleep(5)
    raise RuntimeError("no unique relay quorum")


def construct(
    preparation,
    requested_round,
    prepare_verification_sha256,
    commit_sha1,
    run_id,
    run_attempt,
):
    random_text, signature_text, observations = obtain(requested_round)
    predictions = []
    for number in range(8):
        subject = "subject-" + format(number, "02d")
        digest = framed(
            b"JANUS-HELIOS-V5-SYNTHETIC-PREDICTION\0",
            [
                preparation.encode("ascii"),
                random_text.encode("ascii"),
                subject.encode("ascii"),
            ],
        )
        predictions.append({"prediction": int(digest[63], 16) % 2, "subject": subject})
    prediction_root = framed(
        b"JANUS-HELIOS-V5-SYNTHETIC-PREDICTION-ROOT\0", [encode_json(predictions)]
    )
    unsigned = {
        "challenge": {
            "chain_hash": CHAIN,
            "randomness": random_text,
            "round": requested_round,
            "signature": signature_text,
        },
        "implementation": "windows-http-client-v1",
        "os_family": "windows",
        "prediction_root": prediction_root,
        "predictions": predictions,
        "prepare_root": preparation,
        "prepare_verification_sha256": prepare_verification_sha256,
        "relay_observations": observations,
        "schema": "janus.helios-v5.synthetic-replica-receipt.v1",
        "workflow": {
            "commit_sha1": commit_sha1,
            "job": "windows-replica",
            "run_attempt": run_attempt,
            "run_id": run_id,
        },
    }
    unsigned["receipt_root"] = framed(
        b"JANUS-HELIOS-V5-SYNTHETIC-REPLICA-RECEIPT\0", [encode_json(unsigned)]
    )
    return unsigned


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-root", required=True)
    parser.add_argument("--prepare-verification", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--challenge-round", type=int, required=True)
    parser.add_argument("--output", required=True)
    options = parser.parse_args()
    if (
        HEX64.fullmatch(options.prepare_root) is None
        or HEX40.fullmatch(options.commit) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", options.run_id) is None
        or not 1 <= options.run_attempt <= 1000
    ):
        raise SystemExit("invalid identity")
    if not 1 <= options.challenge_round <= 2**53 - 1:
        raise SystemExit("invalid challenge round")
    destination = Path(options.output)
    if destination.parent != Path(".") or destination.name != "REPLICA_WINDOWS.json":
        raise SystemExit("invalid output")
    verification_path = Path(options.prepare_verification)
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
    verification_body = dict(verification) if isinstance(verification, dict) else {}
    verification_root = verification_body.pop("verification_root", None)
    if (
        not isinstance(verification, dict)
        or verification_raw != encode_json(verification) + b"\n"
        or verification.get("prepare_root") != options.prepare_root
        or verification.get("commit_sha1") != options.commit
        or verification.get("release_immutable") is not True
        or verification_root != framed(
            PREPARE_VERIFICATION_DOMAIN, [encode_json(verification_body)]
        )
    ):
        raise SystemExit("invalid prepare verification")
    raw = encode_json(construct(
        options.prepare_root,
        options.challenge_round,
        hashlib.sha256(verification_raw).hexdigest(),
        options.commit,
        options.run_id,
        options.run_attempt,
    )) + b"\n"
    write_new_regular(destination, raw, "REPLICA_WINDOWS.json", 16384)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
