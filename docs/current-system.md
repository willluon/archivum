# The predecessor: Permit Scanning Assist

archivum descends from a capture tool I built and ran in production during a
Building Department internship. This page records what that system is and
which of its lessons archivum inherits. The tool itself lives in a separate
private repository and contains real municipal data; per the
[data policy](data-policy.md), none of that data appears here.

## What it is

A Python/tkinter desktop application that sits between office scanners and a
commercial ECM (Laserfiche):

```text
scanner output folders  →  watcher (with write-completion detection)
  →  staging copy (originals never modified)
  →  per-page classification (native text → fast OCR sweep → orientation fixes)
  →  extraction cascade across document tiers, best-source-wins merge
  →  vision-model fallback for handwriting (result cache keyed by page-image hash)
  →  validation against county reference data (repair, corroborate, or reject)
  →  human verification UI (every field labeled with its source)
  →  batch merge + standardized naming  →  manual filing into the ECM
```

Roughly 3,400 lines, deliberately a single file, with a functional core
(extraction, validation, persistence helpers) that keeps ~100 unit tests
fast and GUI-free. SQLite full-text archive of everything scanned; sanitized
telemetry with a test-pinned privacy schema; an evaluation harness scoring
extraction stages against human-confirmed ground truth.

## Lessons archivum inherits

**Provenance and confidence are the metadata model.** Every extracted field
carries its source, and sources are ranked: manual entry > authoritative
reference data > native text > machine OCR > vision model > weak fallback
tiers. Low-rank sources may fill gaps but never displace better sources;
validation can overrule rank. This generalizes into archivum's
`MetadataValue` provenance/confidence design.

**Human-in-the-loop is architecture.** The tool never pretends certainty:
uncertain values are flagged for verification, provably-wrong values are
dropped rather than displayed ("blank beats wrong"), and a human confirm
gate stands before anything becomes final. archivum makes that gate the
formal boundary between derived and canonical metadata.

**Validation against reference data beats better OCR.** The single biggest
accuracy win was not a better model — it was reconciling extracted values
against an authoritative parcel database that could repair damaged reads,
corroborate uncertain ones, and reject impossible ones. archivum models
validators as first-class processors.

**Derived results cache by content hash.** Vision-model outputs are cached
keyed by the page image's hash — deterministic, deduplicating, free on
repeat. The same pattern serves any expensive processor.

**Search is optional; documents are not.** The tool's FTS index is external-
content with a plain-scan fallback; losing it loses nothing. archivum
promotes this into the explicit canonical-vs-derived contract.

**Failures must become state, not silence.** As a never-crash-mid-scan desk
tool, the predecessor swallows exceptions broadly — the right call there,
and a hidden-failure factory anywhere else. archivum records failure as job
state with retries and dead-lettering.

## What archivum deliberately does not inherit

- **Path-as-identity.** The filename *is* the document's identity there;
  renaming is the write operation. archivum's founding principle is the
  opposite (permanent UUIDs, ADR-0003/0005).
- **UI-coupled orchestration.** The processing pipeline runs inside the GUI
  class on ad-hoc threads. archivum separates services, jobs, and clients.
- **Schema evolution without migrations,** five ad-hoc stores, and
  single-user assumptions baked into every layer.

## Future relationship

The tool keeps working exactly as it does today. At roadmap V0.7 it gains an
optional "submit to archivum" path — becoming the first real capture client
of the platform, demonstrated with synthetic documents. Its permit
extraction engine is the intended first pluggable implementation of the
generalized extraction framework (post-V1.0).
