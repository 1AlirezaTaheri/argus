#!/bin/bash

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"
docker compose cp "$HERE/verify-target-detail-api.py" admin:/tmp/vta.py >/dev/null 2>&1
docker compose exec -T -w /app -e PYTHONPATH=/app admin python /tmp/vta.py 2>&1 \
  | grep -vE 'InsecureKeyLength|self\._jws|error reading bcrypt|most recent call|File "/usr/local|version = _bcrypt|AttributeError: module|StarletteDeprecation|from starlette'
exit "${PIPESTATUS[0]}"