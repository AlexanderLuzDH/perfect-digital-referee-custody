#!/usr/bin/env python3
"""Delayed synthetic-label reveal and exact scorer for HELIOS v5."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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


ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
RELAYS = (
    "https://api.drand.sh",
    "https://api2.drand.sh",
    "https://api3.drand.sh",
)
LABEL_RANK_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-LABEL-RANK\0"
LABEL_ROOT_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-LABEL-ROOT\0"
REVEAL_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-REVEAL\0"


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
    parser.add_argument("--prepare-root", required=True)
    parser.add_argument("--finalize-root", required=True)
    parser.add_argument("--challenge-round", required=True, type=int)
    parser.add_argument("--reveal-round", required=True, type=int)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    if (
        ROOT_RE.fullmatch(arguments.prepare_root) is None
        or ROOT_RE.fullmatch(arguments.finalize_root) is None
        or not 1 <= arguments.challenge_round < arguments.reveal_round <= 2**53 - 1
    ):
        raise SystemExit("invalid binding")
    destination = Path(arguments.output)
    if destination.parent != Path(".") or destination.name != "REVEAL.json":
        raise SystemExit("invalid output")

    ubuntu, _ubuntu_raw = read_receipt(Path(arguments.ubuntu))
    windows, _windows_raw = read_receipt(Path(arguments.windows))
    check_receipt(
        ubuntu,
        arguments.prepare_root,
        arguments.challenge_round,
        "ubuntu-urllib-v1",
        "linux",
    )
    check_receipt(
        windows,
        arguments.prepare_root,
        arguments.challenge_round,
        "windows-http-client-v1",
        "windows",
    )
    if (
        ubuntu["challenge"] != windows["challenge"]
        or ubuntu["predictions"] != windows["predictions"]
    ):
        raise RuntimeError("replica disagreement")

    reveal_beacon, observations = fetch_quorum(arguments.reveal_round)
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
        "finalize_root": arguments.finalize_root,
        "labels": labels,
        "labels_root": framed_hash(LABEL_ROOT_DOMAIN, canonical(labels)),
        "outcome": "PASS" if correct >= 6 else "FAIL",
        "predictions_root": ubuntu["prediction_root"],
        "prepare_root": arguments.prepare_root,
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
    destination.write_bytes(canonical(value) + b"\n")
    print(canonical({
        "outcome": value["outcome"],
        "reveal_root": value["reveal_root"],
        "score": value["score"],
    }).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
