#!/usr/bin/env bash
set -euo pipefail
BASE="${1:-https://blindspot.novaproject.cloud}"
echo "Checking $BASE"
for path in /health /status /api/files /api/metrics; do
  printf '%-16s ' "$path"
  curl -fsS -o /tmp/blindspot_check.out -w '%{http_code} %{content_type}\n' "$BASE$path"
done
python3 - <<'PY'
import json, urllib.request
url='https://blindspot.novaproject.cloud/status'
data=json.load(urllib.request.urlopen(url, timeout=10))
print('device=', data.get('device'), 'gpu=', data.get('gpu'), 'outputs=', data.get('output_count'))
PY
