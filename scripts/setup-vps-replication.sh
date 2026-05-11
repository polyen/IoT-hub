#!/usr/bin/env bash
# One-time VPS setup: create logical replication subscription from edge PG.
# Run on VPS after edge PG has wal_level=logical and events_pub created.
set -euo pipefail

EDGE_PG_HOST="${EDGE_PG_HOST:?Set EDGE_PG_HOST}"
EDGE_PG_USER="${EDGE_PG_USER:-iothub}"
EDGE_PG_PASS="${EDGE_PG_PASS:?Set EDGE_PG_PASS}"
EDGE_PG_DB="${EDGE_PG_DB:-iothub}"
VPS_PG_DB="${VPS_PG_DB:-iothub}"

psql -U postgres -d "${VPS_PG_DB}" <<SQL
CREATE SUBSCRIPTION events_sub
CONNECTION 'host=${EDGE_PG_HOST} user=${EDGE_PG_USER} password=${EDGE_PG_PASS} dbname=${EDGE_PG_DB} sslmode=require'
PUBLICATION events_pub
WITH (copy_data = true);
SQL

echo "[DONE] Subscription events_sub created on VPS"
