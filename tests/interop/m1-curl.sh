#!/usr/bin/env bash
# M1 smoke test of the Tornado <-> fastapi-interop endpoints with the real fixtures.
# Needs: curl, python3 (stdlib only). Optional: pdftotext.
# Usage: tests/interop/m1-curl.sh [BASE_URL]      (default http://localhost:8080; stack must be running)
set -u
BASE="${1:-${BASE_URL:-http://localhost:8080}}"
HERE="$(cd "$(dirname "$0")" && pwd)"
FX="$HERE/../e2e/fixtures/interop"
OUT="$(mktemp -d)"; JAR="$OUT/jar.txt"
pass=0; fail=0
ok()   { echo "PASS  $*"; pass=$((pass+1)); }
bad()  { echo "FAIL  $*"; fail=$((fail+1)); }
check(){ if [ "$1" = "$2" ]; then ok "$3 ($1)"; else bad "$3: expected $2, got $1"; fi; }
jget() { python3 -c "import sys,json;d=json.load(open(sys.argv[1]));print(eval(sys.argv[2]))" "$1" "$2"; }

echo "== fixtures"; (cd "$FX" && sha256sum -c SHA256SUMS --quiet) && ok "fixture checksums" || bad "fixture checksums"

echo "== auth"
check "$(curl -s -o /dev/null -w '%{http_code}' -F upload=@"$FX/invoice.xlsx" "$BASE/interop/import")" 401 "anonymous import is rejected"
U="curl.$$.$RANDOM@example.com"
curl -s -c "$JAR" -b "$JAR" -o /dev/null -d "email=$U&password=Passw0rd!&repassword=Passw0rd!" "$BASE/register"
check "$(curl -s -c "$JAR" -b "$JAR" -o /dev/null -w '%{http_code}' -d "email=$U&password=Passw0rd!" "$BASE/login")" 302 "login"

echo "== health"
curl -s -b "$JAR" -o "$OUT/h.json" "$BASE/interop/health"
check "$(jget "$OUT/h.json" "d['sidecar']['engine']")" python "sidecar engine"

echo "== import"
for spec in invoice.xls:xls:3 invoice.xlsx:xlsx:3 customers.csv:csv:1 table.html:html:1; do
  IFS=: read -r f fmt n <<<"$spec"
  code=$(curl -s -b "$JAR" -o "$OUT/imp_$f.json" -w '%{http_code}' -F upload=@"$FX/$f" "$BASE/interop/import")
  check "$code" 200 "import $f"
  check "$(jget "$OUT/imp_$f.json" "d['format']+':'+str(len(d['sheets']))")" "$fmt:$n" "$f format:sheets"
done
python3 - "$OUT/imp_invoice.xlsx.json" <<'PY' && ok "xlsx savestr: B2, merged A1:F1, formula D14" || bad "xlsx savestr content"
import json,sys
d=json.load(open(sys.argv[1])); s=json.loads(d["savestr"])["sheetArr"]["sheet1"]["sheetstr"]["savestr"]
assert "cell:B2:t:INV-2026-0042" in s and "colspan:6" in s and ":D12+D13:" in s, "content missing"
PY
python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['savestr'],end='')" "$OUT/imp_invoice.xlsx.json" > "$OUT/invoice.savestr"
python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['savestr'],end='')" "$OUT/imp_customers.csv.json" > "$OUT/customers.savestr"

echo "== export (file is uploaded to S3 before the reply) then GET twice (both app containers)"
for type in xlsx xls csv html pdf; do
  code=$(curl -s -b "$JAR" -o "$OUT/exp_$type.json" -w '%{http_code}' --data-urlencode "type=$type" --data-urlencode "content@$OUT/invoice.savestr" "$BASE/interop/export")
  check "$code" 200 "export $type"
  url=$(jget "$OUT/exp_$type.json" "d['url']")
  s1=$(curl -s -o "$OUT/dl_1.$type" -w '%{http_code}' "$url"); s2=$(curl -s -o "$OUT/dl_2.$type" -w '%{http_code}' "$url")
  check "$s1/$s2" 200/200 "download $type"; cmp -s "$OUT/dl_1.$type" "$OUT/dl_2.$type" && ok "$type bytes identical on both fetches" || bad "$type bytes differ"
done
python3 - "$OUT" <<'PY' && ok "xlsx/xls are valid containers; csv/html contain the invoice" || bad "export content"
import sys,zipfile
o=sys.argv[1]
z=zipfile.ZipFile(f"{o}/dl_1.xlsx"); assert z.testzip() is None and "xl/workbook.xml" in z.namelist()
assert open(f"{o}/dl_1.xls","rb").read(8)==b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
assert "INV-2026-0042" in open(f"{o}/dl_1.csv",encoding="utf-8").read()
h=open(f"{o}/dl_1.html",encoding="utf-8").read(); assert "₹9,374.68" in h and 'colspan="6"' in h
assert open(f"{o}/dl_1.pdf","rb").read(4)==b"%PDF"
PY
if command -v pdftotext >/dev/null; then
  pdftotext -layout "$OUT/dl_1.pdf" - | grep -q "INV-2026-0042" && ok "pdf text has INV-2026-0042" || bad "pdf text"
else echo "SKIP  pdftotext not installed"; fi

echo "== csv keeps empty cells (columns do not shift)"
curl -s -b "$JAR" -o "$OUT/exp_csv2.json" --data-urlencode "type=csv" --data-urlencode "content@$OUT/customers.savestr" "$BASE/interop/export"
curl -s -o "$OUT/customers_out.csv" "$(jget "$OUT/exp_csv2.json" "d['url']")"
python3 - "$FX/customers.csv" "$OUT/customers_out.csv" <<'PY' && ok "customers.csv: 251 rows, every row has 6 columns" || bad "csv columns"
import csv,sys
a=list(csv.reader(open(sys.argv[1],encoding="utf-8",newline=""))); b=list(csv.reader(open(sys.argv[2],encoding="utf-8",newline="")))
assert len(a)==len(b)==251 and all(len(r)==6 for r in b)
PY

echo "== error mapping"
check "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' --data-urlencode type=ods --data-urlencode 'content={}' "$BASE/interop/export")" 400 "unsupported type"
head -c 300 /dev/urandom > "$OUT/broken.xls"
check "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' -F upload=@"$OUT/broken.xls" "$BASE/interop/import")" 422 "corrupt file"
check "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/interop/export?fname=../../etc/passwd")" 400 "path traversal key"
echo "(502/504 mapping needs the sidecar stopped/paused: covered by tests/e2e/interop-fastapi.spec.ts)"

echo; echo "passed=$pass failed=$fail   (files in $OUT)"
[ "$fail" -eq 0 ]
