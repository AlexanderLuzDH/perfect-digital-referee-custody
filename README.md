# perfect-digital-referee-custody

Hash-only immutable custody records for proof-carrying digital-referee
research; no model bytes or real labels.

`ceremony/` and the pinned workflow contain the model-free HELIOS v5 synthetic
ceremony replicas. They process exactly eight synthetic subject identifiers,
one previously committed drand quicknet round, and one frozen PREPARE root.
They never receive or read a target, checkpoint, archive, model, or real label.

The Linux and Windows replicas are separately implemented with Python's
standard library. Agreement is evidence of implementation/OS/host diversity,
but both hosted runners and this repository remain inside GitHub's
administrative domain. The scripts validate `randomness = SHA-256(signature)`
and require agreement from at least two HTTPS relays; they do not implement BLS
signature verification.
