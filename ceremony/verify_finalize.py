#!/usr/bin/env python3
"""Verify immutable FINALIZE publication, workflow jobs, and artifact custody."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY = "AlexanderLuzDH/perfect-digital-referee-custody"
GENESIS_TIME = 1692803367
PERIOD_SECONDS = 3
ROOT_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
FINALIZE_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-FINALIZE\0"
VERIFICATION_DOMAIN = b"JANUS-HELIOS-V5-SYNTHETIC-FINALIZE-VERIFICATION\0"
JOB_NAMES = ("ubuntu-replica", "windows-replica", "pair-finalizer")
ARTIFACT_NAMES = (
    "helios-replica-ubuntu",
    "helios-replica-windows",
    "helios-finalize-candidate",
)
SIBLING_NAMES = (
    "FINALIZE_CANDIDATE.json",
    "PAIR_VERIFICATION.json",
    "PREPARE_VERIFICATION.json",
    "REPLICA_UBUNTU.json",
    "REPLICA_WINDOWS.json",
)
ARTIFACT_ENTRIES = {
    "helios-replica-ubuntu": ("REPLICA_UBUNTU.json",),
    "helios-replica-windows": ("REPLICA_WINDOWS.json",),
    "helios-finalize-candidate": (
        "FINALIZE_CANDIDATE.json",
        "PAIR_VERIFICATION.json",
        "PREPARE_VERIFICATION.json",
    ),
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


def request_headers(accept: str, user_agent: str) -> dict[str, str]:
    headers = {
        "Accept": accept,
        "User-Agent": user_agent,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def fetch_bytes(
    url: str,
    limit: int,
    accept: str = "application/octet-stream",
) -> bytes:
    request = urllib.request.Request(
        url,
        headers=request_headers(accept, "helios-v5-finalize-verifier/1"),
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("HTTP status")
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise RuntimeError("HTTP oversize")
    return raw


def fetch_json(url: str, limit: int = 262144) -> tuple[Any, bytes]:
    raw = fetch_bytes(url, limit, "application/vnd.github+json")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates), raw


def artifact_files(artifact: dict[str, Any]) -> dict[str, bytes]:
    artifact_id = artifact["id"]
    url = (
        f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/"
        f"{artifact_id}/zip"
    )
    archive_raw = fetch_bytes(url, 1048576)
    digest = artifact.get("digest")
    if (
        type(digest) is not str
        or digest != "sha256:" + hashlib.sha256(archive_raw).hexdigest()
    ):
        raise RuntimeError("artifact archive digest")
    expected_names = ARTIFACT_ENTRIES[artifact["name"]]
    result: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive_raw), "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if (
            len(names) != len(set(names))
            or tuple(sorted(names)) != tuple(sorted(expected_names))
        ):
            raise RuntimeError("artifact archive manifest")
        for info in infos:
            unix_type = (info.external_attr >> 16) & 0o170000
            if (
                info.is_dir()
                or info.flag_bits & 0x1
                or info.file_size > 65536
                or info.compress_size > 1048576
                or info.compress_type not in {
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                }
                or unix_type == 0o120000
            ):
                raise RuntimeError("unsafe artifact entry")
            with archive.open(info, "r") as source:
                raw = source.read(65537)
            if len(raw) != info.file_size or len(raw) > 65536:
                raise RuntimeError("artifact entry size")
            result[info.filename] = raw
    return result


def parse_utc(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("naive time")
    return int(parsed.astimezone(timezone.utc).timestamp())


def selected_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "completed_at": job["completed_at"],
        "conclusion": job["conclusion"],
        "id": job["id"],
        "labels": job["labels"],
        "name": job["name"],
        "runner_group_name": job["runner_group_name"],
        "runner_name": job["runner_name"],
        "started_at": job["started_at"],
    }


def selected_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "created_at": artifact["created_at"],
        "digest": artifact["digest"],
        "expired": artifact["expired"],
        "expires_at": artifact["expires_at"],
        "id": artifact["id"],
        "name": artifact["name"],
        "size_in_bytes": artifact["size_in_bytes"],
        "workflow_run_id": artifact["workflow_run"]["id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finalize-root", required=True)
    parser.add_argument("--finalize-sha256", required=True)
    parser.add_argument("--prepare-root", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--reveal-round", required=True, type=int)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    if (
        ROOT_RE.fullmatch(arguments.finalize_root) is None
        or ROOT_RE.fullmatch(arguments.finalize_sha256) is None
        or ROOT_RE.fullmatch(arguments.prepare_root) is None
        or COMMIT_RE.fullmatch(arguments.commit) is None
        or not 1 <= arguments.reveal_round <= 2**53 - 1
    ):
        raise SystemExit("invalid arguments")
    output = Path(arguments.output)
    if output.parent != Path(".") or output.name != "FINALIZE_VERIFICATION.json":
        raise SystemExit("invalid output")

    tag = "helios-v5-finalize-" + arguments.finalize_root
    release_url = (
        "https://api.github.com/repos/"
        + REPOSITORY
        + "/releases/tags/"
        + urllib.parse.quote(tag, safe="")
    )
    release, _release_raw = fetch_json(release_url)
    if (
        type(release) is not dict
        or release.get("tag_name") != tag
        or release.get("immutable") is not True
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("target_commitish") != arguments.commit
        or type(release.get("assets")) is not list
        or len(release["assets"]) != 6
    ):
        raise RuntimeError("release")
    assets_by_name = {asset.get("name"): asset for asset in release["assets"]}
    if set(assets_by_name) != {"FINALIZE.json", *SIBLING_NAMES}:
        raise RuntimeError("release assets")
    final_asset = assets_by_name["FINALIZE.json"]
    if (
        final_asset.get("digest") != "sha256:" + arguments.finalize_sha256
        or final_asset.get("state") != "uploaded"
        or type(final_asset.get("size")) is not int
        or final_asset["size"] > 65536
    ):
        raise RuntimeError("finalize asset")
    finalize, finalize_raw = fetch_json(final_asset["browser_download_url"], 65536)
    if (
        type(finalize) is not dict
        or finalize_raw != canonical(finalize) + b"\n"
        or hashlib.sha256(finalize_raw).hexdigest() != arguments.finalize_sha256
    ):
        raise RuntimeError("finalize bytes")
    body = dict(finalize)
    supplied_root = body.pop("finalize_root", None)
    if supplied_root != arguments.finalize_root or supplied_root != framed_hash(
        FINALIZE_DOMAIN, canonical(body)
    ):
        raise RuntimeError("finalize root")
    if set(body) != {
        "authority",
        "challenge",
        "commit_sha1",
        "finalize_candidate",
        "jobs",
        "pair",
        "prepare",
        "published_sibling_assets",
        "repository",
        "schema",
        "workflow",
        "workflow_artifacts",
    }:
        raise RuntimeError("finalize shape")
    if (
        body["schema"] != "janus.helios-v5.synthetic-finalize.v1"
        or body["repository"] != REPOSITORY
        or body["commit_sha1"] != arguments.commit
        or body["prepare"].get("root") != arguments.prepare_root
        or body["prepare"].get("reveal_round") != arguments.reveal_round
        or body["prepare"].get("reveal_scheduled_unix")
        != GENESIS_TIME + (arguments.reveal_round - 1) * PERIOD_SECONDS
        or body["authority"] != {
            "C1": "PENDING_REVEAL_SCORE",
            "C2": "GITHUB_IMMUTABLE_FINALIZE_AND_API_JOB_BINDING",
            "C3": "GITHUB_JOB_METADATA_ONLY",
            "C4": "BOUNDED_GITHUB_HOSTED_LINUX_WINDOWS_IMPLEMENTATION_DIVERSITY_SAME_PROVIDER_ADMIN",
            "model_science": "NONE",
            "real_labels": "NONE",
            "target": "NONE",
        }
    ):
        raise RuntimeError("finalize semantics")
    workflow = body["workflow"]
    if (
        type(workflow) is not dict
        or set(workflow) != {
            "conclusion", "event", "head_sha", "id", "path", "run_attempt"
        }
        or workflow["head_sha"] != arguments.commit
        or workflow["event"] != "workflow_dispatch"
        or workflow["conclusion"] != "success"
        or workflow["path"] != ".github/workflows/helios-synthetic-replicas.yml"
        or type(workflow["id"]) is not int
        or type(workflow["run_attempt"]) is not int
    ):
        raise RuntimeError("workflow fields")

    api_base = f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{workflow['id']}"
    run, _run_raw = fetch_json(api_base)
    selected_run = {
        "conclusion": run.get("conclusion"),
        "event": run.get("event"),
        "head_sha": run.get("head_sha"),
        "id": run.get("id"),
        "path": run.get("path"),
        "run_attempt": run.get("run_attempt"),
    }
    if selected_run != workflow:
        raise RuntimeError("workflow API mismatch")
    jobs_response, _jobs_raw = fetch_json(api_base + "/jobs?per_page=100")
    jobs = [
        selected_job(job)
        for job in jobs_response.get("jobs", [])
        if job.get("name") in JOB_NAMES
    ]
    jobs.sort(key=lambda item: JOB_NAMES.index(item["name"]))
    if jobs != body["jobs"] or [job["name"] for job in jobs] != list(JOB_NAMES):
        raise RuntimeError("job API mismatch")
    expected_labels = {
        "ubuntu-replica": "ubuntu-24.04",
        "windows-replica": "windows-2025",
        "pair-finalizer": "ubuntu-24.04",
    }
    if (
        any(job["conclusion"] != "success" for job in jobs)
        or len({job["id"] for job in jobs}) != 3
        or any(
            type(job["runner_name"]) is not str or not job["runner_name"]
            for job in jobs
        )
        or len({job["runner_name"] for job in jobs}) != 3
        or any(
            type(job["runner_group_name"]) is not str
            or not job["runner_group_name"]
            for job in jobs
        )
        or any(
            expected_labels[job["name"]] not in job["labels"]
            for job in jobs
        )
        or any(
            parse_utc(job["started_at"]) > parse_utc(job["completed_at"])
            for job in jobs
        )
    ):
        raise RuntimeError("job semantic mismatch")
    artifacts_response, _artifacts_raw = fetch_json(api_base + "/artifacts?per_page=100")
    artifacts = [
        selected_artifact(artifact)
        for artifact in artifacts_response.get("artifacts", [])
        if artifact.get("name") in ARTIFACT_NAMES
    ]
    artifacts.sort(key=lambda item: ARTIFACT_NAMES.index(item["name"]))
    if (
        artifacts != body["workflow_artifacts"]
        or [artifact["name"] for artifact in artifacts] != list(ARTIFACT_NAMES)
        or len({artifact["id"] for artifact in artifacts}) != 3
        or any(artifact["expired"] is not False for artifact in artifacts)
        or any(artifact["workflow_run_id"] != workflow["id"] for artifact in artifacts)
        or any(
            type(artifact["digest"]) is not str
            or ROOT_RE.fullmatch(artifact["digest"].removeprefix("sha256:")) is None
            for artifact in artifacts
        )
    ):
        raise RuntimeError("artifact API mismatch")
    siblings = body["published_sibling_assets"]
    if type(siblings) is not dict or set(siblings) != set(SIBLING_NAMES):
        raise RuntimeError("sibling map")
    for name in SIBLING_NAMES:
        expected = siblings[name]
        asset = assets_by_name[name]
        if (
            type(expected) is not dict
            or set(expected) != {"sha256", "size"}
            or asset.get("digest") != "sha256:" + expected["sha256"]
            or asset.get("size") != expected["size"]
            or asset.get("state") != "uploaded"
        ):
            raise RuntimeError("sibling asset mismatch")
    published_bytes = {}
    for name in SIBLING_NAMES:
        raw = fetch_bytes(assets_by_name[name]["browser_download_url"], 65536)
        if (
            len(raw) != siblings[name]["size"]
            or hashlib.sha256(raw).hexdigest() != siblings[name]["sha256"]
        ):
            raise RuntimeError("sibling bytes")
        published_bytes[name] = raw
    artifact_bytes = {}
    for artifact in artifacts:
        artifact_bytes.update(artifact_files(artifact))
    if set(artifact_bytes) != set(SIBLING_NAMES):
        raise RuntimeError("artifact entry union")
    for name in SIBLING_NAMES:
        if artifact_bytes[name] != published_bytes[name]:
            raise RuntimeError("artifact release byte mismatch")

    published_unix = parse_utc(release["published_at"])
    reveal_unix = GENESIS_TIME + (arguments.reveal_round - 1) * PERIOD_SECONDS
    if published_unix >= reveal_unix:
        raise RuntimeError("late finalize")
    verification_body = {
        "finalize_asset_id": final_asset["id"],
        "finalize_margin_seconds": reveal_unix - published_unix,
        "finalize_root": arguments.finalize_root,
        "finalize_sha256": arguments.finalize_sha256,
        "jobs_bound": len(jobs),
        "published_at": release["published_at"],
        "published_unix": published_unix,
        "release_id": release["id"],
        "release_immutable": True,
        "reveal_scheduled_unix": reveal_unix,
        "schema": "janus.helios-v5.synthetic-finalize-verification.v1",
        "tag": tag,
        "workflow_artifacts_bound": len(artifacts),
        "workflow_entries_byte_equal": len(artifact_bytes),
        "workflow_run_id": workflow["id"],
    }
    verification = {
        **verification_body,
        "verification_root": framed_hash(
            VERIFICATION_DOMAIN, canonical(verification_body)
        ),
    }
    output.write_bytes(canonical(verification) + b"\n")
    print(canonical(verification).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
