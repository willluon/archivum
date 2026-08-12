# Data policy: no real municipal data

archivum is inspired by work I did during an internship at a municipal
Building Department, where the predecessor capture tool ran against real
permit records. This repository is public. The line is:

**Experience in; data out.**

## Never enters this repository

- Scanned documents, or images/excerpts of them, from the internship
- Any database produced there (document archive, county parcel extracts,
  scan history, telemetry) or rows exported from them
- Real permit numbers, parcel identifiers, property addresses, owner or
  applicant names, or any value transcribed from a real municipal record
- Reference datasets assembled for that deployment (street lists, parcel
  data), even where the upstream source is public — the demo corpus uses a
  fictional municipality instead

## Fine to use

- Knowledge of document *layouts* and workflows — what fields a permit
  form carries, how a filing workflow runs, what failure modes OCR has.
  That is learned experience, not data.
- Design lessons and measured conclusions described in general terms
  (see `current-system.md`)
- Synthetic demo documents: invented municipality, invented streets,
  invented permit numbers, invented names. Realistic in structure,
  fictional in content.

## Enforcement

- The demo corpus is generated, and its generator is committed — provenance
  of every sample document is inspectable.
- Anything resembling a real record in a PR or commit is treated as an
  incident: history gets rewritten, not just tipped.
