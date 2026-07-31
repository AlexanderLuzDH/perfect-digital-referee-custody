#!/usr/bin/env python3
"""Delayed synthetic-label reveal and exact scorer for HELIOS v5."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from verify_pair import (
    CHAIN_HASH,
    SUBJECTS,
    canonical,
    check_receipt,
    framed_hash,
    read_receipt,
)
from safe_output import write_new_regular


ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
RELAYS = (
    "https://api.drand.sh",
    "https://api2.drand.sh",
    "https://api3.drand.sh",
)
LABEL_RANK_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-LABEL-RANK\0"
LABEL_ROOT_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-LABEL-ROOT\0"
REVEAL_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-REVEAL\0"
FINALIZE_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-FINALIZE\0"
FINALIZE_VERIFICATION_DOMAIN = (
    b"JANUS-HELIOS-V5-SYNTHETIC-FINALIZE-VERIFICATION\0"
)
FINALIZE_CANDIDATE_DOMAIN = (
    b"JANUS-HELIOS-V5-SYNTHETIC-FINALIZE-CANDIDATE\0"
)
PREPARE_VERIFICATION_DOMAIN = (
    b"JANUS-HELIOS-V5-SYNTHETIC-PREPARE-VERIFICATION\0"
)


def strict_json(path: Path, limit: int) -> tuple[dict[str, Any], bytes]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("bad JSON file")
    raw = path.read_bytes()
    value = json.loads(raw.decode("ascii"))
    if type(value) is not dict or raw != canonical(value) + b"\n":
        raise ValueError("non-canonical JSON")
    return value, raw


def fetch_one(base: str, round_number: int) -> dict[str, Any]:
    url = f"{base}/{CHAIN_HASH}/public/{round_number}"
    request = urllib.request.Request(url, headers={"User-Agent": "helios-v5-reveal/1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("relay status")
        raw = response.read(4097)
    if len(raw) > 4096:
        raise RuntimeError("relay oversize")
    value = json.loads(raw.decode("ascii"))
    if type(value) is not dict or set(value) != {"round", "randomness", "signature"}:
        raise RuntimeError("relay shape")
    randomness = value["randomness"]
    signature = value["signature"]
    if (
        value["round"] != round_number
        or type(randomness) is not str
        or type(signature) is not str
        or ROOT_RE.fullmatch(randomness) is None
        or re.fullmatch(r"[0-9a-f]{96}", signature) is None
        or hashlib.sha256(bytes.fromhex(signature)).hexdigest() != randomness
    ):
        raise RuntimeError("relay content")
    return {
        "base": base,
        "randomness": randomness,
        "round": round_number,
        "signature": signature,
    }


def fetch_quorum(round_number: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    for attempt in range(6):
        observations = []
        for base in RELAYS:
            try:
                observations.append(fetch_one(base, round_number))
            except Exception:
                pass
        counts: dict[tuple[str, str], int] = {}
        for observation in observations:
            key = (observation["randomness"], observation["signature"])
            counts[key] = counts.get(key, 0) + 1
        winners = [key for key, count in counts.items() if count >= 2]
        if len(winners) == 1:
            randomness, signature = winners[0]
            return {
                "chain_hash": CHAIN_HASH,
                "randomness": randomness,
                "round": round_number,
                "signature": signature,
            }, observations
        if attempt != 5:
            time.sleep(5)
    raise RuntimeError("reveal quorum")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ubuntu", required=True)
    parser.add_argument("--windows", required=True)
    parser.add_argument("--finalize", required=True)
    parser.add_argument("--finalize-candidate", required=True)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--prepare-verification", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    destination = Path(arguments.output)
    if destination.parent != Path(".") or destination.name != "REVEAL.json":
        raise SystemExit("invalid output")

    finalize, finalize_raw = strict_json(Path(arguments.finalize), 65536)
    candidate, candidate_raw = strict_json(
        Path(arguments.finalize_candidate), 16384
    )
    pair, pair_raw = strict_json(Path(arguments.pair), 4096)
    prepare_verification, prepare_verification_raw = strict_json(
        Path(arguments.prepare_verification), 8192
    )
    finalize_body = dict(finalize)
    finalize_root = finalize_body.pop("finalize_root", None)
    if (
        ROOT_RE.fullmatch(finalize_root or "") is None
        or finalize_root != framed_hash(FINALIZE_DOMAIN, canonical(finalize_body))
        or finalize.get("schema") != "janus.helios-v5.synthetic-finalize.v1"
    ):
        raise SystemExit("invalid finality")
    prepare = finalize["prepare"]
    workflow = finalize["workflow"]
    prepare_root = prepare.get("root")
    prepare_verification_sha256 = prepare.get("verification_sha256")
    challenge_round = finalize.get("challenge", {}).get("round")
    reveal_round = prepare.get("reveal_round")
    commit_sha1 = finalize.get("commit_sha1")
    if (
        ROOT_RE.fullmatch(prepare_root or "") is None
        or ROOT_RE.fullmatch(prepare_verification_sha256 or "") is None
        or re.fullmatch(r"[0-9a-f]{40}", commit_sha1 or "") is None
        or type(challenge_round) is not int
        or type(reveal_round) is not int
        or not 1 <= challenge_round < reveal_round <= 2**53 - 1
    ):
        raise SystemExit("invalid finality binding")

    verifier_path = Path(__file__).resolve().with_name("verify_finalize.py")
    verifier_argv = [
        str(verifier_path),
        "--finalize-root",
        finalize_root,
        "--finalize-sha256",
        hashlib.sha256(finalize_raw).hexdigest(),
        "--prepare-root",
        prepare_root,
        "--commit",
        commit_sha1,
        "--reveal-round",
        str(reveal_round),
        "--output",
        "FINALIZE_VERIFICATION.json",
    ]
    prior_argv = sys.argv
    try:
        sys.argv = verifier_argv
        try:
            runpy.run_path(str(verifier_path), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise RuntimeError("live finalize verification failed") from exc
    finally:
        sys.argv = prior_argv
    verification, verification_raw = strict_json(
        Path("FINALIZE_VERIFICATION.json"), 8192
    )
    verification_body = dict(verification)
    verification_root = verification_body.pop("verification_root", None)
    if (
        verification.get("schema")
        != "janus.helios-v5.synthetic-finalize-verification.v1"
        or verification.get("finalize_root") != finalize_root
        or verification.get("finalize_sha256")
        != hashlib.sha256(finalize_raw).hexdigest()
        or verification.get("release_immutable") is not True
        or verification.get("published_unix") >= prepare.get("reveal_scheduled_unix")
        or verification_root != framed_hash(
            FINALIZE_VERIFICATION_DOMAIN, canonical(verification_body)
        )
    ):
        raise SystemExit("invalid live finality")

    ubuntu, ubuntu_raw = read_receipt(Path(arguments.ubuntu))
    windows, windows_raw = read_receipt(Path(arguments.windows))
    siblings = finalize.get("published_sibling_assets", {})
    if (
        siblings.get("REPLICA_UBUNTU.json") != {
            "sha256": hashlib.sha256(ubuntu_raw).hexdigest(),
            "size": len(ubuntu_raw),
        }
        or siblings.get("REPLICA_WINDOWS.json") != {
            "sha256": hashlib.sha256(windows_raw).hexdigest(),
            "size": len(windows_raw),
        }
        or siblings.get("FINALIZE_CANDIDATE.json") != {
            "sha256": hashlib.sha256(candidate_raw).hexdigest(),
            "size": len(candidate_raw),
        }
        or siblings.get("PAIR_VERIFICATION.json") != {
            "sha256": hashlib.sha256(pair_raw).hexdigest(),
            "size": len(pair_raw),
        }
        or siblings.get("PREPARE_VERIFICATION.json") != {
            "sha256": hashlib.sha256(prepare_verification_raw).hexdigest(),
            "size": len(prepare_verification_raw),
        }
    ):
        raise SystemExit("receipt publication binding")
    candidate_body = dict(candidate)
    candidate_root = candidate_body.pop("candidate_root", None)
    prepare_verification_body = dict(prepare_verification)
    prepare_verification_root = prepare_verification_body.pop(
        "verification_root", None
    )
    if (
        candidate_root != framed_hash(
            FINALIZE_CANDIDATE_DOMAIN, canonical(candidate_body)
        )
        or finalize.get("finalize_candidate") != {
            "candidate_root": candidate_root,
            "sha256": hashlib.sha256(candidate_raw).hexdigest(),
        }
        or pair.get("schema")
        != "janus.helios-v5.synthetic-pair-verification.v1"
        or pair.get("verdict") != "PASS_EXACT_DUAL_REPLICA_AGREEMENT"
        or finalize.get("pair") != {
            "pair_root": pair.get("pair_root"),
            "prediction_root": pair.get("prediction_root"),
            "verification_sha256": hashlib.sha256(pair_raw).hexdigest(),
        }
        or hashlib.sha256(prepare_verification_raw).hexdigest()
        != prepare_verification_sha256
        or prepare_verification.get("prepare_root") != prepare_root
        or prepare_verification.get("commit_sha1") != commit_sha1
        or prepare_verification.get("release_immutable") is not True
        or prepare_verification_root != framed_hash(
            PREPARE_VERIFICATION_DOMAIN, canonical(prepare_verification_body)
        )
        or candidate.get("prepare_root") != prepare_root
        or candidate.get("commit_sha1") != commit_sha1
        or candidate.get("challenge") != finalize.get("challenge")
        or candidate.get("pair_root") != pair.get("pair_root")
        or candidate.get("prediction_root") != pair.get("prediction_root")
        or candidate.get("receipts") != {
            "ubuntu_sha256": hashlib.sha256(ubuntu_raw).hexdigest(),
            "windows_sha256": hashlib.sha256(windows_raw).hexdigest(),
        }
        or candidate.get("workflow") != {
            "run_attempt": workflow["run_attempt"],
            "run_id": str(workflow["id"]),
        }
    ):
        raise SystemExit("published chain binding")
    check_receipt(
        ubuntu,
        prepare_root,
        challenge_round,
        "ubuntu-urllib-v1",
        "linux",
        commit_sha1,
        str(workflow["id"]),
        workflow["run_attempt"],
        "ubuntu-replica",
        prepare_verification_sha256,
    )
    check_receipt(
        windows,
        prepare_root,
        challenge_round,
        "windows-http-client-v1",
        "windows",
        commit_sha1,
        str(workflow["id"]),
        workflow["run_attempt"],
        "windows-replica",
        prepare_verification_sha256,
    )
    if (
        ubuntu["challenge"] != windows["challenge"]
        or ubuntu["predictions"] != windows["predictions"]
        or ubuntu["challenge"] != finalize["challenge"]
        or ubuntu["prediction_root"] != finalize["pair"]["prediction_root"]
        or pair["prediction_root"] != ubuntu["prediction_root"]
    ):
        raise RuntimeError("replica disagreement")

    reveal_beacon, observations = fetch_quorum(reveal_round)
    ranks = [
        (
            framed_hash(
                LABEL_RANK_DOMAIN,
                reveal_beacon["randomness"].encode("ascii"),
                subject.encode("ascii"),
            ),
            subject,
        )
        for subject in SUBJECTS
    ]
    positive_subjects = {subject for _rank, subject in sorted(ranks)[:4]}
    labels = [
        {"label": int(subject in positive_subjects), "subject": subject}
        for subject in SUBJECTS
    ]
    predictions = ubuntu["predictions"]
    tp = tn = fp = fn = 0
    rows = []
    for prediction, label in zip(predictions, labels):
        if prediction["subject"] != label["subject"]:
            raise RuntimeError("subject disagreement")
        predicted = prediction["prediction"]
        actual = label["label"]
        if predicted == 1 and actual == 1:
            tp += 1
        elif predicted == 0 and actual == 0:
            tn += 1
        elif predicted == 1 and actual == 0:
            fp += 1
        else:
            fn += 1
        rows.append({"label": actual, "prediction": predicted, "subject": label["subject"]})
    correct = tp + tn
    body = {
        "challenge": ubuntu["challenge"],
        "confusion": {"fn": fn, "fp": fp, "tn": tn, "tp": tp},
        "finalize_root": finalize_root,
        "finalize_verification_sha256": hashlib.sha256(verification_raw).hexdigest(),
        "labels": labels,
        "labels_root": framed_hash(LABEL_ROOT_DOMAIN, canonical(labels)),
        "outcome": "PASS" if correct >= 6 else "FAIL",
        "predictions_root": ubuntu["prediction_root"],
        "prepare_root": prepare_root,
        "reveal_beacon": reveal_beacon,
        "reveal_relay_observations": observations,
        "rows": rows,
        "schema": "janus.helios-v5.synthetic-reveal.v1",
        "score": {
            "accuracy_denominator": 8,
            "accuracy_numerator": correct,
            "balanced_accuracy_denominator": 8,
            "balanced_accuracy_numerator": correct,
            "threshold_correct": 6,
        },
    }
    value = {**body, "reveal_root": framed_hash(REVEAL_DOMAIN, canonical(body))}
    write_new_regular(destination, canonical(value) + b"\n", "REVEAL.json", 32768)
    print(canonical({
        "outcome": value["outcome"],
        "reveal_root": value["reveal_root"],
        "score": value["score"],
    }).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
