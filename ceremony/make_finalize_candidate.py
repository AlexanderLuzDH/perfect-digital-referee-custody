#!/usr/bin/env python3
"""Construct a workflow-bound candidate for later immutable FINALIZE publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from verify_pair import canonical, framed_hash, read_receipt


ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-FINALIZE-CANDIDATE\0"


def strict_json(path: Path, limit: int) -> tuple[dict[str, Any], bytes]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("bad JSON file")
    raw = path.read_bytes()
    value = json.loads(raw.decode("ascii"))
    if type(value) is not dict or raw != canonical(value) + b"\n":
        raise ValueError("non-canonical JSON")
    return value, raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ubuntu", required=True)
    parser.add_argument("--windows", required=True)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--prepare-verification", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    if (
        COMMIT_RE.fullmatch(arguments.commit) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", arguments.run_id) is None
        or not 1 <= arguments.run_attempt <= 1000
    ):
        raise SystemExit("invalid workflow identity")
    output = Path(arguments.output)
    if output.parent != Path(".") or output.name != "FINALIZE_CANDIDATE.json":
        raise SystemExit("invalid output")
    ubuntu, ubuntu_raw = read_receipt(Path(arguments.ubuntu))
    windows, windows_raw = read_receipt(Path(arguments.windows))
    pair, pair_raw = strict_json(Path(arguments.pair), 4096)
    prepare, prepare_raw = strict_json(Path(arguments.prepare_verification), 8192)
    if (
        pair.get("schema") != "janus.helios-v5.synthetic-pair-verification.v1"
        or pair.get("verdict") != "PASS_EXACT_DUAL_REPLICA_AGREEMENT"
        or pair.get("prediction_root") != ubuntu.get("prediction_root")
        or ubuntu.get("predictions") != windows.get("predictions")
        or ubuntu.get("workflow") != {
            "commit_sha1": arguments.commit,
            "job": "ubuntu-replica",
            "run_attempt": arguments.run_attempt,
            "run_id": arguments.run_id,
        }
        or windows.get("workflow") != {
            "commit_sha1": arguments.commit,
            "job": "windows-replica",
            "run_attempt": arguments.run_attempt,
            "run_id": arguments.run_id,
        }
        or prepare.get("commit_sha1") != arguments.commit
        or prepare.get("prepare_root") != ubuntu.get("prepare_root")
        or prepare.get("prepare_root") != windows.get("prepare_root")
        or not ROOT_RE.fullmatch(prepare.get("verification_root", ""))
    ):
        raise RuntimeError("binding mismatch")
    body = {
        "challenge": ubuntu["challenge"],
        "commit_sha1": arguments.commit,
        "pair_root": pair["pair_root"],
        "pair_verification_sha256": hashlib.sha256(pair_raw).hexdigest(),
        "prediction_root": pair["prediction_root"],
        "prepare_root": prepare["prepare_root"],
        "prepare_verification_sha256": hashlib.sha256(prepare_raw).hexdigest(),
        "receipts": {
            "ubuntu_sha256": hashlib.sha256(ubuntu_raw).hexdigest(),
            "windows_sha256": hashlib.sha256(windows_raw).hexdigest(),
        },
        "schema": "janus.helios-v5.synthetic-finalize-candidate.v1",
        "workflow": {
            "run_attempt": arguments.run_attempt,
            "run_id": arguments.run_id,
        },
    }
    value = {**body, "candidate_root": framed_hash(DOMAIN, canonical(body))}
    output.write_bytes(canonical(value) + b"\n")
    print(canonical(value).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
