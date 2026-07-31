# HELIOS v5 operational synthetic ceremony

This protocol is a bounded dress rehearsal. It contains eight synthetic subject
IDs and no target, checkpoint, archive, model, or real labels.

## Frozen order

1. `PREPARE.json` binds this repository commit, both replica source hashes, the
   workflow hash, eight subject IDs, the drand quicknet chain hash, one future
   challenge round, one later reveal round, all hash domains, and a fixed
   success threshold.
2. PREPARE is published as an immutable GitHub release before the challenge
   round.
3. After the challenge round, separately implemented Linux/`urllib` and
   Windows/`http.client` workers fetch the exact round from three HTTPS relays,
   require at least two identical responses, verify
   `randomness = SHA-256(signature)`, and independently calculate all eight
   predictions.
4. The exact receipts, workflow run and job identities, and agreement root are
   published as immutable `FINALIZE.json` before the reveal round.
5. The reveal round creates labels without a preexisting secret: each subject
   receives a rank hash over the reveal randomness and subject ID. Exactly the
   four lexicographically smallest `(rank hash, subject ID)` pairs receive label
   one; the other four receive label zero.
6. Scoring recomputes the complete confusion matrix, accuracy, and balanced
   accuracy. Six or more correct predictions is `PASS`; a lower valid score is
   `FAIL`, not a malformed transcript.
7. `REVEAL.json` is published as a third immutable release and the complete
   local evidence tree is frozen and independently reviewed.

## Authority boundaries

This is real chronology and real host/OS/implementation diversity for a
synthetic relation. GitHub hosts the repository, immutable releases, both
ephemeral runners, workflow logs, and artifacts, so it remains one provider and
one repository-administrator domain. The drand relays provide future entropy,
but these standard-library workers do not implement BLS verification; they
retain TLS/relay-quorum trust. Public release attestations retain GitHub,
Fulcio, and timestamp-authority trust.

No result from this ceremony grants target, extraction, deserialization, model,
real-label, backdoor, benignness, production, or scientific authority.
