# HELIOS v5 operational synthetic ceremony

This protocol is a bounded dress rehearsal. It contains eight synthetic subject
IDs and no target, checkpoint, archive, model, or real labels.

## Frozen order

1. `PREPARE.json` binds this repository commit, both replica source hashes, the
   workflow hash, eight subject IDs, the drand quicknet chain hash, one future
   challenge round, one later reveal round, all hash domains, and a fixed
   success threshold.
2. PREPARE is published as an immutable GitHub release before the challenge
   round. Every hosted job downloads that exact asset, recomputes its root,
   checks release immutability and publication time, verifies all bound source
   hashes, checks out the exact bound commit, and denies a late or mismatched
   preparation.
3. After the challenge round, separately implemented Linux/`urllib` and
   Windows/`http.client` workers fetch the exact round from three HTTPS relays,
   require at least two identical responses, verify
   `randomness = SHA-256(signature)`, and independently calculate all eight
   predictions.
4. A third hosted job downloads the exact two workflow artifacts, reruns the
   pair checker, and constructs a candidate binding the run ID, attempt, commit,
   receipt hashes, pair root, and preparation receipt. A separate local
   controller then queries GitHub's run, job, runner-label, and artifact APIs,
   constructs `FINALIZE.json`, and publishes the exact receipts and verifier
   files with it as an immutable release before the reveal round.
5. Before scoring, `score_reveal.py` directly executes the live
   `verify_finalize.py`; it does not accept a caller-created verification
   receipt. The verifier downloads the immutable FINALIZE asset, recomputes its
   root, checks every sibling asset digest, re-queries the workflow, job,
   runner, and artifact identities, downloads each artifact archive by ID,
   validates an exact safe archive manifest and archive digest, and compares
   every contained file byte-for-byte with the immutable release assets. It
   denies publication at or after the scheduled reveal round. The reveal round
   then creates labels without a preexisting secret: each subject
   receives a rank hash over the reveal randomness and subject ID. Exactly the
   four lexicographically smallest `(rank hash, subject ID)` pairs receive label
   one; the other four receive label zero.
6. Scoring recomputes the complete confusion matrix, accuracy, and balanced
   accuracy. Six or more correct predictions is `PASS`; a lower valid score is
   `FAIL`, not a malformed transcript.
7. `REVEAL.json` is published as a third immutable release and the complete
   local evidence tree is frozen and independently reviewed.

Every generated file uses atomic exclusive creation in the current ceremony
directory. Existing files, links, reparse paths, non-regular destinations, and
post-open identity changes cause denial; no output is overwritten.

## Authority boundaries

Only a completed transcript that passes the external release-time and
workflow/job API checks is evidence of chronology or host/OS/implementation
diversity. Source fields and unkeyed receipt hashes alone grant no such
authority. GitHub hosts the repository, immutable releases, both ephemeral
runners, workflow logs, and artifacts, so even a passing transcript remains one
provider and one repository-administrator domain. The drand relays provide
future entropy, but these standard-library workers do not implement BLS
verification; they retain TLS/relay-quorum trust. Public release attestations
retain GitHub, Fulcio, and timestamp-authority trust.

No result from this ceremony grants target, extraction, deserialization, model,
real-label, backdoor, benignness, production, or scientific authority.
