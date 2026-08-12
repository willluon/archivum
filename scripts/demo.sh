#!/usr/bin/env bash
# V0.1 kernel demo walkthrough (roadmap completion criterion).
# Requires: schema migrated, DATABASE_URL set (or local-dev default).
set -euo pipefail

WORK=$(mktemp -d)
printf 'SYNTHETIC DEMO PERMIT - fictional municipality of Exempla\n' > "$WORK/permit.pdf"
ROOT="demo-$(date +%s)"

archivum mkdir "/$ROOT"
archivum mkdir "/$ROOT/Inbox"
archivum mkdir "/$ROOT/Building"
archivum mkdir "/$ROOT/Building/Permits"

archivum ingest "$WORK/permit.pdf" "/$ROOT/Inbox" --mime application/pdf
ID1=$(archivum info "/$ROOT/Inbox/permit.pdf" | awk '/^id:/{print $2}')

archivum rename "/$ROOT/Inbox/permit.pdf" "BP-2026-1842.pdf"
archivum mv "/$ROOT/Inbox/BP-2026-1842.pdf" "/$ROOT/Building/Permits"

ID2=$(archivum info "/$ROOT/Building/Permits/BP-2026-1842.pdf" | awk '/^id:/{print $2}')
if [ "$ID1" != "$ID2" ]; then
    echo "FAIL: document identity changed across rename+move ($ID1 -> $ID2)"
    exit 1
fi
echo "identity preserved across rename + move: $ID1"

archivum verify "/$ROOT/Building/Permits/BP-2026-1842.pdf"

echo "--- audit trail ---"
archivum audit "/$ROOT/Building/Permits/BP-2026-1842.pdf"

echo "--- ls /$ROOT/Building/Permits ---"
archivum ls "/$ROOT/Building/Permits"

echo "=== V0.2: immutable version history (ADR-0007) ==="
printf 'SYNTHETIC CONTRACT v1 - fictional municipality of Exempla\n' > "$WORK/contract.pdf"
archivum ingest "$WORK/contract.pdf" "/$ROOT" --mime application/pdf
CID1=$(archivum info "/$ROOT/contract.pdf" | awk '/^id:/{print $2}')

printf 'SYNTHETIC CONTRACT v2 - revised terms\n' > "$WORK/contract.pdf"
archivum version-add "/$ROOT/contract.pdf" "$WORK/contract.pdf" \
    --mime application/pdf --note "second draft"

printf 'SYNTHETIC CONTRACT v3 - final terms\n' > "$WORK/contract.pdf"
archivum version-add "/$ROOT/contract.pdf" "$WORK/contract.pdf" \
    --mime application/pdf --note "third draft" --expect 2

archivum restore "/$ROOT/contract.pdf" 1

echo "--- version history ---"
archivum versions "/$ROOT/contract.pdf"

CID2=$(archivum info "/$ROOT/contract.pdf" | awk '/^id:/{print $2}')
if [ "$CID1" != "$CID2" ]; then
    echo "FAIL: document identity changed across versioning ($CID1 -> $CID2)"
    exit 1
fi

SHA_V1=$(archivum versions "/$ROOT/contract.pdf" | awk '$2=="v1"{print $5}')
SHA_V2=$(archivum versions "/$ROOT/contract.pdf" | awk '$2=="v2"{print $5}')
SHA_V4=$(archivum versions "/$ROOT/contract.pdf" | awk '$2=="v4"{print $5}')
CURRENT=$(archivum versions "/$ROOT/contract.pdf" | awk '$1=="*"{print $2}')
[ "$SHA_V1" = "$SHA_V4" ] || { echo "FAIL: restored v4 does not share v1 content"; exit 1; }
[ "$SHA_V1" != "$SHA_V2" ] || { echo "FAIL: v1 and v2 unexpectedly identical"; exit 1; }
[ "$CURRENT" = "v4" ] || { echo "FAIL: current is $CURRENT, expected v4"; exit 1; }
echo "identity stable ($CID1), v4 restored from v1, current is v4"

archivum verify "/$ROOT/contract.pdf"

echo "--- audit trail ---"
archivum audit "/$ROOT/contract.pdf"
N_CREATED=$(archivum audit "/$ROOT/contract.pdf" | grep -c DOCUMENT_VERSION_CREATED || true)
N_RESTORED=$(archivum audit "/$ROOT/contract.pdf" | grep -c DOCUMENT_VERSION_RESTORED || true)
[ "$N_CREATED" = "2" ] || { echo "FAIL: expected 2 DOCUMENT_VERSION_CREATED, got $N_CREATED"; exit 1; }
[ "$N_RESTORED" = "1" ] || { echo "FAIL: expected 1 DOCUMENT_VERSION_RESTORED, got $N_RESTORED"; exit 1; }

echo "=== V0.3: generic metadata schemas (ADR-0008/0009) ==="
archivum schema create "Building Permit" --description "Synthetic demo schema"
archivum schema field-add "Building Permit" permit_number text --label "Permit Number" --required
archivum schema field-add "Building Permit" property_address text --label "Property Address"
archivum schema field-add "Building Permit" issue_date date --label "Issue Date"
archivum schema field-add "Building Permit" estimated_cost decimal --label "Estimated Cost"
archivum schema publish "Building Permit"

archivum schema create "Invoice"
archivum schema field-add "Invoice" invoice_number text --required
archivum schema field-add "Invoice" vendor text
archivum schema field-add "Invoice" amount decimal
archivum schema field-add "Invoice" due_date date
archivum schema publish "Invoice"
archivum schema show "Building Permit"
archivum schema show "Invoice"

printf 'SYNTHETIC PERMIT DOCUMENT - fictional municipality of Exempla\n' > "$WORK/permit-meta.pdf"
archivum ingest "$WORK/permit-meta.pdf" "/$ROOT" --mime application/pdf
archivum metadata assign "/$ROOT/permit-meta.pdf" "Building Permit"
archivum metadata set "/$ROOT/permit-meta.pdf" permit_number "DEMO-2026-001"
archivum metadata set "/$ROOT/permit-meta.pdf" property_address "10 Example Street" \
    --origin extracted --source demo/extractor --confidence 0.91
archivum metadata set "/$ROOT/permit-meta.pdf" issue_date 2026-08-12
archivum metadata set "/$ROOT/permit-meta.pdf" estimated_cost 25000.00
archivum metadata verify "/$ROOT/permit-meta.pdf" property_address

echo "--- permit metadata ---"
archivum metadata show "/$ROOT/permit-meta.pdf"
ADDR_LINE=$(archivum metadata show "/$ROOT/permit-meta.pdf" | grep '^property_address')
echo "$ADDR_LINE" | grep -q 'origin=extracted' || { echo "FAIL: origin lost after verify"; exit 1; }
echo "$ADDR_LINE" | grep -q 'confidence=0.91' || { echo "FAIL: confidence lost after verify"; exit 1; }
echo "$ADDR_LINE" | grep -q 'verified=yes' || { echo "FAIL: verification missing"; exit 1; }
echo "machine provenance survived human verification"

printf 'SYNTHETIC INVOICE DOCUMENT - fictional vendor\n' > "$WORK/invoice.pdf"
archivum ingest "$WORK/invoice.pdf" "/$ROOT" --mime application/pdf
archivum metadata assign "/$ROOT/invoice.pdf" "Invoice"
archivum metadata set "/$ROOT/invoice.pdf" invoice_number "INV-0042"
archivum metadata set "/$ROOT/invoice.pdf" vendor "Exempla Office Supply Co"
archivum metadata set "/$ROOT/invoice.pdf" amount 199.99
archivum metadata set "/$ROOT/invoice.pdf" due_date 2026-09-01
echo "--- invoice metadata (same engine, different schema) ---"
archivum metadata show "/$ROOT/invoice.pdf"

# invalid typed write must be rejected with no mutation and no audit event
N_SET_BEFORE=$(archivum audit "/$ROOT/permit-meta.pdf" | grep -c METADATA_VALUE_SET || true)
if archivum metadata set "/$ROOT/permit-meta.pdf" estimated_cost banana 2>/dev/null; then
    echo "FAIL: 'banana' accepted for a decimal field"
    exit 1
fi
archivum metadata show "/$ROOT/permit-meta.pdf" | grep '^estimated_cost' | grep -q '25000.00' \
    || { echo "FAIL: estimated_cost mutated by rejected write"; exit 1; }
N_SET_AFTER=$(archivum audit "/$ROOT/permit-meta.pdf" | grep -c METADATA_VALUE_SET || true)
[ "$N_SET_BEFORE" = "$N_SET_AFTER" ] || { echo "FAIL: rejected write produced an audit event"; exit 1; }
echo "invalid write rejected: no mutation, no audit event"

echo "demo OK"
