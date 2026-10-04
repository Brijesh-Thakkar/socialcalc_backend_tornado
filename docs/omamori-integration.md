# Omamori x402 firewall integration

SocialCalc can sell a **sheet export** to AI agents over [x402](https://docs.x402.org/)
(HTTP `402 Payment Required` + USDC on Base Sepolia). The paying agent never holds a
signing key: it asks the [Omamori](https://github.com/seetadev/yakusoku) pre-signature
firewall to sign, and the firewall only signs a payment that matches what a human
authorized. Every firewall stage is fail-closed.

Tornado never imports Omamori's Bun/TypeScript code. The firewall is an optional Docker
Compose sidecar that Tornado calls over HTTP with `AsyncHTTPClient`, the same pattern as
the meshkit (5050) and Kubo (5051) sidecars.

This is a minimal vertical slice: the 402 challenge, the firewall decision and every
fail-closed path work end to end; **settlement does not** (see [Blocked](#next-steps--blocked)).

## Architecture

```
 AI agent (holds only a yk_ mandate key, never a signing key)
   │ 1. GET /x402/sheet/<id>/export ─────────────► nginx ─► Tornado (app1|app2)
   │ ◄──────────── 402 + PAYMENT-REQUIRED (x402 v2, built from env, deterministic)
   │
   │ 2. POST /omamori/sign  {resourceUrl, paymentRequiredHeader, intentId, context}
   ▼    Authorization: Bearer yk_...
 nginx ─► Tornado OmamoriSignHandler
            │  size/JSON checks → SSRF guard (path must be /x402/sheet/<id>/export)
            │  forwards ONLY Authorization, rebuilt resourceUrl on OMAMORI_RESOURCE_BASE_URL
            ▼
          omamori-firewall:4001/sign      (internal to app_network; no host port)
            │  idempotency → policy → merchant → funding → provenance
            │  → Intercepta → Jev → World ID gate        (any error ⇒ refuse/ask_human)
            │
            │  merchant stage self-fetch:
            └──► GET http://nginx/x402/sheet/<id>/export ─► Tornado (same 402, byte-identical)
          ◄── {verdict, reason, receiptId, paymentSignature?}
 Tornado maps the verdict to an HTTP status (table below) ─► agent
```

## Endpoints

### `GET /x402/sheet/<id>/export`

`<id>` must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` (no existing handler validates sheet
names, so the export defines its own allow-list). The sheet is the file
`home/<OMAMORI_SELLER_USER>/<id>` in SocialCalc storage.

| Case | Status | Body |
|---|---|---|
| Omamori not configured (payTo / seller / price invalid) | 503 | `{"error":"omamori_not_configured"}` |
| Storage lookup raised | 503 | `{"error":"storage_unavailable"}` |
| No such sheet (checked **before** any challenge) | 404 | `{"error":"sheet_not_found"}` |
| `PAYMENT-SIGNATURE` or `X-PAYMENT` header present | 501 | `{"error":"settlement_disabled", ...}` — content is never served |
| Otherwise | 402 | `PAYMENT-REQUIRED` header + the same object as JSON |

The challenge has the exact field set and order `@x402/express` emits (checked against
Omamori's store's real decoded header): `x402Version, error, resource{url, description,
mimeType}, accepts[{scheme, network, amount, asset, payTo, maxTimeoutSeconds, extra}],
extensions{payment-identifier}`, compact JSON, base64. It depends only on the sheet id and
env (`resource.url` comes from `OMAMORI_PUBLIC_BASE_URL`, never the request's Host), so
app1, app2, nginx, the firewall's internal fetch and a public tunnel all serve
byte-identical headers. The price is `OMAMORI_EXPORT_PRICE`; clients cannot influence it.

```
$ curl -si http://localhost:8080/x402/sheet/demo-budget/export | grep -i payment-required | cut -d' ' -f2 | base64 -d
{"x402Version":2,"error":"Payment required","resource":{"url":"http://localhost:8080/x402/sheet/demo-budget/export",
 "description":"SocialCalc sheet export","mimeType":""},"accepts":[{"scheme":"exact","network":"eip155:84532",
 "amount":"10000","asset":"0x036CbD53842c5426634e7929541eC2318f3dCF7e","payTo":"0x…","maxTimeoutSeconds":60,
 "extra":{"name":"USDC","version":"2"}}],"extensions":{"payment-identifier":{…}}}
```

### `POST /omamori/sign`

Request (`Content-Type: application/json`, ≤ 64 KB, `Authorization: Bearer yk_…`):

```json
{
  "intentId": "intent_…",
  "resourceUrl": "https://<public host>/x402/sheet/demo-budget/export",
  "paymentRequiredHeader": "<PAYMENT-REQUIRED value from the 402>",
  "context": {
    "userRequest": "Export my demo-budget sheet",
    "justification": "Paying the export fee the server asked for.",
    "untrustedContent": [{"source": "sheet-page", "text": "…"}]
  },
  "purchaseRef": "optional"
}
```

Only `intentId`, `paymentRequiredHeader`, `context`, `purchaseRef` and the **rebuilt**
`resourceUrl` are forwarded; everything else is dropped.

#### Status mapping (fail-closed)

| Outcome | Status | Body |
|---|---|---|
| Firewall `verdict: "pay"` **and** a non-empty `paymentSignature` string | 200 | `{verdict:"pay", reason, receiptId, paymentSignature}` |
| Firewall `verdict: "pay"` without a signature | 502 | `{verdict:"refuse", reason:"malformed_firewall_response"}` |
| Firewall `verdict: "ask_human"` | 202 | `{verdict:"ask_human", reason, receiptId, approval:{verificationUri, userCode, expiresAt}}` |
| Firewall `verdict: "refuse"` | 403 | `{verdict:"refuse", reason, receiptId}` |
| Firewall 400 / 401 / 403 | same | `{verdict:"refuse", reason:<firewall error>}` |
| Firewall unreachable (connection refused, DNS, 599) | 502 | `{verdict:"refuse", reason:"firewall_unreachable"}` |
| Tornado's own client timeout (`HTTPTimeoutError`, `OMAMORI_TIMEOUT_S`) | 504 | `{verdict:"refuse", reason:"firewall_timeout"}` |
| Any other status, non-JSON, unknown verdict, missing `reason`/`receiptId` | 502 | `{verdict:"refuse", reason:"firewall_error" \| "malformed_firewall_response"}` |
| Body > 64 KB | 413 | `{verdict:"refuse", reason:"request_too_large"}` |
| Not `application/json` / invalid JSON / bad field types | 400 | `{verdict:"refuse", reason:"content_type_must_be_json" \| "invalid_json" \| …}` |
| No `Authorization` header | 401 | `{verdict:"refuse", reason:"missing_authorization"}` (firewall not called) |
| `resourceUrl` not allowed (SSRF guard) | 400 | `{verdict:"refuse", reason:"resource_not_allowed"}` (firewall not called) |

`refuse` is 403, not 402, because 402 already means "payment required" on the export.

## Security notes

- **Internal-only firewall.** `omamori-firewall` uses `expose: 4001`, never `ports:`; host
  port 4001 stays free (it is IPFS's default swarm port). Agents reach it only through
  `POST /omamori/sign`.
- **Only `/sign` is proxied.** `/control`, `/intents`, `/receipts`, `/events`, `/dashboard`
  and `/dev/*` are not reachable through Tornado.
- **Header stripping.** Only `Authorization` (plus Tornado's own `Content-Type` and a
  fixed `User-Agent`) is sent to the firewall. Client headers such as `x-yakusoku-admin`,
  `Cookie` or `X-Forwarded-For` are dropped, so the firewall's loopback-admin routes
  can't be triggered.
- **SSRF guard.** The firewall's merchant stage fetches `resourceUrl` itself and does not
  block private IPs, so Tornado never forwards the agent's URL. Only the path is used, it
  must be exactly `/x402/sheet/<id>/export` in raw form (any `%`, backslash, dot segment,
  whitespace or other character is rejected, never normalised), and the URL sent is
  rebuilt as `OMAMORI_RESOURCE_BASE_URL + path`. The agent's scheme, host, port, userinfo,
  query and fragment are ignored. Unit tests cover traversal (`../`, `%2e%2e`), double
  encoding, internal hosts (minio, kubo, 127.0.0.1, 169.254.169.254, the firewall itself)
  and userinfo tricks, each with zero firewall calls.
- **No secrets in logs.** `Authorization` and `paymentSignature` are never logged (a unit
  test asserts this); log lines carry only verdict, receiptId and reason.
- **Keys stay in the sidecar.** Firewall keys live in `docker/omamori/.env` (gitignored)
  and are loaded only by `omamori-firewall`, not by app1/app2 (which load the root `.env`).
- **Fail-closed everywhere.** No sidecar, no config, storage errors, timeouts and
  malformed answers all end in `refuse`/503, never `pay` and never content.
- The firewall's own admin event stream (`/events?admin=1`) is gated only by a loopback
  check plus a query parameter, not the admin header — another reason it must stay
  internal and unproxied.

## How to run

```bash
# 1. Firewall keys (names in docker/omamori/.env.example). For a local demo only
#    FIREWALL_PRIVATE_KEY is needed: use a throwaway, UNFUNDED Base Sepolia key.
cp docker/omamori/.env.example docker/omamori/.env

# 2. App settings in the root .env (names in .env.example):
#    OMAMORI_PAYTO_ADDRESS=0x…  OMAMORI_SELLER_USER=<seller email>
#    OMAMORI_EXPORT_PRICE=10000 (USDC atomic units)  OMAMORI_PUBLIC_BASE_URL=http://localhost:8080

# 3. Start with the profile (plain `docker compose up` never starts the sidecar)
docker compose --profile omamori up -d --build

# 4. Demo and tests
scripts/omamori-demo.sh             # BASE_URL / SHEET / DC env overrides available
pip install -r requirements-dev.txt && python -m pytest -v
```

The Omamori source is fetched at build time from GitHub's tarball of a pinned commit
(`OMAMORI_REPO=https://github.com/seetadev/yakusoku`,
`OMAMORI_REF=d12e66afd47819000abe17f2e49115e872806121`, upstream `main` verified with
`git ls-remote`); override with `docker compose build --build-arg OMAMORI_REF=<sha>`.
Only the firewall workspace is installed (72 packages).

### Environment variables

| Variable | Service | Default | Notes |
|---|---|---|---|
| `FIREWALL_PRIVATE_KEY` | omamori-firewall | — | Required to boot. Throwaway/unfunded for demos; never commit |
| `INTERCEPTA_API_KEY` | omamori-firewall | — | Missing ⇒ Intercepta stage asks a human |
| `TYPESAFE_API_KEY` | omamori-firewall | — | Missing ⇒ Jev stage asks a human |
| `WORLD_CLIENT_ID`, `WORLD_CLIENT_SECRET` | omamori-firewall | — | Missing ⇒ any "ask a human" ends in refuse |
| `BASE_SEPOLIA_RPC_URL` | omamori-firewall | public RPC | Needed for signing (`DOMAIN_SEPARATOR` read) |
| `OMAMORI_FIREWALL_URL` | app1/app2 | `http://omamori-firewall:4001` | |
| `OMAMORI_TIMEOUT_S` | app1/app2 | `15` | Client timeout ⇒ 504 |
| `OMAMORI_PAYTO_ADDRESS` | app1/app2 | **none** | Unset ⇒ 503 `omamori_not_configured` |
| `OMAMORI_SELLER_USER` | app1/app2 | **none** | Account whose sheets are for sale; unset ⇒ 503 |
| `OMAMORI_EXPORT_PRICE` | app1/app2 | `10000` | USDC atomic units (0.01 USDC) |
| `OMAMORI_RESOURCE_BASE_URL` | app1/app2 | `http://nginx` | What the firewall self-fetches |
| `OMAMORI_PUBLIC_BASE_URL` | app1/app2 | `http://localhost:8080` | `resource.url` in the challenge |

## Demo results (keyless, 2026-10-04)

`scripts/omamori-demo.sh` against `docker compose --profile omamori up`, with only a
throwaway unfunded `FIREWALL_PRIVATE_KEY` (no Intercepta, TypeSafe or World ID keys) on a
network that blocks Base Sepolia RPC. Result: **10 passed, 0 failed.**

| # | Step | HTTP | Actual verdict / reason |
|---|---|---|---|
| 0 | Demo sheet saved via storage API | — | present |
| 1 | Firewall health (inside network) | 200 | `{"ok":true}` |
| 2 | 402 challenge | 402 | decodes: x402Version 2, `eip155:84532`, amount `10000`, Base Sepolia USDC |
| 3 | Legit export, $1 mandate | 403 | refuse — `could not start World ID approval: device_authorization request failed: missing WORLD_CLIENT_ID` |
| 4 | Tampered `payTo` | 403 | refuse — `merchant: payee_mismatch: agent forwarded payTo 0x…dEaD, the merchant's own 402 names 0x…` |
| 5 | Over budget (0.001 USDC mandate) | 403 | refuse — `amount 10000 exceeds remaining budget 1000` |
| 6 | Recipient injected in untrusted text (own mandate) | 403 | refuse — `provenance: recipient address only appears in untrusted content (sheet-page), never in the signed request` |
| 7 | Unknown agent key | 401 | refuse — `unauthorized` |
| 8 | SSRF (`resourceUrl` = firewall `/control`) | 400 | refuse — `resource_not_allowed`; firewall `/sign` call count unchanged (5 → 5) |
| 9 | Firewall stopped | 502 | refuse — `firewall_unreachable`; restarted healthy afterwards |

Receipt timelines (read inside the firewall container) show the merchant stage
**accepted Tornado's own challenge** — it fetched `http://nginx/x402/sheet/demo-budget/export`
itself and the requirement matched:

```
legit export (step 3)              injected recipient (step 6)
  idempotency  pass                  idempotency  pass
  policy       pass                  policy       pass
  merchant     pass                  merchant     pass
  funding      pass                  funding      pass
  provenance   pass                  provenance   refuse  recipient only in untrusted content
  intercepta   ask_human  Intercepta not configured
  jev          ask_human  Jev unavailable — fail closed: No API key was provided
  world_id     refuse     could not start World ID approval: … missing WORLD_CLIENT_ID
```

`funding` passes without chain access on the wallet-mandate path; it is signing that
needs the RPC. Without keys the firewall never reaches `ask_human` as a final verdict:
the World ID gate cannot start, so it refuses.

Without the profile (what CI runs) the routes fail closed: `GET /x402/...` → 503
`omamori_not_configured`, `POST /omamori/sign` → 502 `firewall_unreachable`.

## Next steps / blocked

- **`pay` and a real `ask_human` (202)** need World ID for Agents sandbox credentials
  (`WORLD_CLIENT_ID/SECRET` + World App) **and** a network that can reach Base Sepolia:
  signing reads USDC's `DOMAIN_SEPARATOR()` over RPC, which this development network
  blocks (TLS resets to every public Base Sepolia RPC).
- **`TYPESAFE_API_KEY`** (Jev) and **`INTERCEPTA_API_KEY`** turn the two `ask_human`
  stages into real pass/refuse judgments.
- **Settlement is not implemented**: the export answers `501 settlement_disabled` to a
  paid retry and never serves content. Next: verify/settle via an x402 facilitator, then
  return the sheet (e.g. CSV/XLSX via `excelinterop`).
- Real mandates should come from World ID account intents (bound to the merchant origin)
  rather than the demo's wallet mandates from `dev-intent`.
- `feature/intercepta-x402-screening` is **superseded** for payment screening: Omamori
  already runs Intercepta as a fail-closed stage, while that branch's
  `RiskProfileHandler` **fails open by default** (returns a mock `PASS` on any error
  unless `INTERCEPTA_FAIL_CLOSED=true`) and its Node `src/` middleware is not wired in.
  It is left untouched.
- Existing storage behaviour worth knowing: `getItem()` returns `None` on any S3 error,
  so a storage outage looks like "sheet not found" (404) rather than 503;
  `cloud.storage.storage.getFile()` prints whole items to stdout (the export uses
  `getFileRaw()` to avoid that).
