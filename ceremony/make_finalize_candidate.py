#!/usr/bin/env python3
"""Construct a workflow-bound candidate for later immutable FINALIZE publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from verify_pair import (
    PAIR_DOMAIN,
    canonical,
    check_receipt,
    framed_hash,
    read_receipt,
)
from safe_output import write_new_regular


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
        or pair.get("pair_root") != framed_hash(PAIR_DOMAIN, canonical(pair.get("pair")))
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
    check_receipt(
        ubuntu,
        prepare["prepare_root"],
        ubuntu["challenge"]["round"],
        "ubuntu-urllib-v1",
        "linux",
        arguments.commit,
        arguments.run_id,
        arguments.run_attempt,
        "ubuntu-replica",
        hashlib.sha256(prepare_raw).hexdigest(),
    )
    check_receipt(
        windows,
        prepare["prepare_root"],
        windows["challenge"]["round"],
        "windows-http-client-v1",
        "windows",
        arguments.commit,
        arguments.run_id,
        arguments.run_attempt,
        "windows-replica",
        hashlib.sha256(prepare_raw).hexdigest(),
    )
    expected_pair_body = {
        "challenge": ubuntu["challenge"],
        "prediction_root": ubuntu["prediction_root"],
        "prepare_root": prepare["prepare_root"],
        "prepare_verification_sha256": hashlib.sha256(prepare_raw).hexdigest(),
        "ubuntu_receipt_sha256": hashlib.sha256(ubuntu_raw).hexdigest(),
        "windows_receipt_sha256": hashlib.sha256(windows_raw).hexdigest(),
        "workflow": {
            "commit_sha1": arguments.commit,
            "run_attempt": arguments.run_attempt,
            "run_id": arguments.run_id,
        },
    }
    if pair.get("pair") != expected_pair_body:
        raise RuntimeError("pair body mismatch")
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
    write_new_regular(
        output,
        canonical(value) + b"\n",
        "FINALIZE_CANDIDATE.json",
        16384,
    )
    print(canonical(value).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
