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

echo "demo OK"
