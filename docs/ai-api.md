# Proposal: a unified AI API for the SocialCalc Tornado backend

Status: **draft for discussion, design only, nothing is implemented.**

## 0. Why

Three AI clients exist and each expects a different server contract; no server in our stack implements any of them:

| Client | Contract it calls |
|---|---|
| SocialCalc AI framework (`AgentModal`) | `POST /agent/socialcalc/test` → `{type, actions[] / message}` |
| Android invoice app | `POST /api/edit-invoice/session`, `/chat`, `GET\|DELETE /api/edit-invoice/session/{id}` → `{session_id, message, cell_updates, token_count}` |
| invoice-agent web frontend | `POST /api/generate-invoice`, `/api/chat`, `/api/session/*` |

Proposal: **one versioned API under `/api/v1/ai/` on Tornado**, same login as the existing `/api/*` routes, backed by **one internal `ai-gateway` service** that is the only thing holding LLM credentials. Old paths are served by thin shims (section 4) until each client moves.

## 1. API

Common to all endpoints:

- **Auth:** the signed `user` cookie used by `/api/login` and `/api/sheets` (`credentials: "include"`); native or non-browser clients may send `Authorization: Bearer <token>` once Tornado issues tokens (open question Q5). No cookie/token → `401`. The user id always comes from the verified session, never from the request.
- **Format:** `Content-Type: application/json` (else `415`), UTF-8, errors always `{"error": {"code": "<machine_code>", "message": "<human text>", "request_id": "<id>"}}`. Every response carries `X-Request-Id`.
- **Size limits (defaults, env-tunable):** request body 256 KB; `prompt` ≤ 4,000 chars; sheet text sent as context ≤ 200 KB; reply ≤ 8,000 output tokens. Larger → `413`.
- **Rate / spend limits (defaults):** 20 requests/min and 200 requests/day per user, plus a monthly token budget per user (Q2). Exceeded → `429` with `Retry-After` (rate) or `403 quota_exceeded` (budget).
- **Shared error codes:**

| HTTP | `error.code` | Meaning |
|---|---|---|
| 400 | `bad_request` | invalid JSON / schema violation |
| 401 | `unauthenticated` | no valid session |
| 403 | `quota_exceeded` | per-user token budget used up |
| 404 | `sheet_not_found` | `sheet` does not exist for this user |
| 413 | `too_large` | body, prompt or context over limit |
| 415 | `unsupported_media_type` | not JSON |
| 422 | `unusable_model_output` | model reply failed validation after retries |
| 429 | `rate_limited` | request-rate limit |
| 502 | `ai_unavailable` | gateway unreachable or provider error |
| 504 | `ai_timeout` | gateway or provider timed out |

Sheets are referenced either by `sheet` (name of a sheet owned by the caller, loaded server-side from the same store as `/api/sheets`) or by inline `context` (the open editor's state; nothing is read or written server-side). Exactly one of the two must be present.

### 1.1 `POST /api/v1/ai/chat`: chat with a sheet
Request:
```json
{ "conversation_id": "optional string", "message": "string",
  "sheet": "Invoice1", "context": null, "mode": "suggest | apply" }
```
Response `200`:
```json
{ "conversation_id": "c_123", "message": "assistant text",
  "cell_updates": [ {"sheet": "Invoice", "cell": "B2", "value": "Acme", "formula": null} ],
  "applied": false, "usage": {"input_tokens": 0, "output_tokens": 0}, "model": "string" }
```
`mode: suggest` (default) returns `cell_updates` for the client to show or apply; `apply` (requires `sheet`) writes them to the stored sheet and returns `applied: true`. Conversation history lives server-side, keyed by `(user, conversation_id)`.

### 1.2 `POST /api/v1/ai/invoice`: generate or edit an invoice
Request:
```json
{ "prompt": "string", "mode": "generate | edit", "sheet": "Invoice1", "context": null,
  "template": "optional template id", "save_as": "optional new sheet name" }
```
Response `200`:
```json
{ "sheet": "Invoice1", "format": "msc-json",
  "data": "<MSC JSON envelope as a string>", "cell_updates": [], "saved": true,
  "reasoning": "short text", "usage": {"input_tokens": 0, "output_tokens": 0}, "model": "string" }
```
`generate` without `sheet` returns a new workbook (and stores it when `save_as` is given). `edit` returns the full updated workbook plus the cell-level diff. Output is validated against the SocialCalc parser before it is returned; a failure after bounded retries is `422`.

### 1.3 `POST /api/v1/ai/explain`: explain a sheet
Request: `{ "sheet": "…" | "context": {…}, "style": "summary | email", "language": "en" }`.
Response: `{ "message": "text (≤ 300 words)", "usage": {…}, "model": "…" }`. Read-only.

### 1.4 `POST /api/v1/ai/formula`: formula help
Request: `{ "question": "sum column C where B is 'paid'", "sheet": "…", "context": null, "cell": "D13" }`.
Response: `{ "formula": "=SUMIF(B2:B20,\"paid\",C2:C20)", "explanation": "text", "warnings": ["function X not supported by the SocialCalc engine"], "usage": {…} }`. Formulas are checked against the functions the SocialCalc engine supports before being returned.

### 1.5 `GET /api/v1/ai/usage`
Returns the caller's remaining budget: `{ "period": "2026-10", "limit_tokens": 0, "used_tokens": 0, "requests_today": 0 }`.

Versioning: breaking changes get `/api/v2/`; `/api/v1/` additions are backward-compatible only. `GET /api/v1/ai/health` (no auth) reports whether the gateway is reachable.

## 2. Architecture

```
Browser / app ──cookie──▶ nginx ──▶ Tornado  /api/v1/ai/*   (auth, limits, sheet load/save, error mapping)
                                       │  AsyncHTTPClient, X-AI-Token (shared secret), X-Request-Id
                                       ▼
                                  ai-gateway   (compose service, internal network only)
                                       │  holds ALL LLM credentials; prompts, tool loop, validation
                                       ▼
                       Bedrock | OpenAI | Anthropic | local (OpenAI-compatible)
```

- **Same pattern as the interop sidecar** (`handlers/interop.py`): handlers in both `cloudmain.py` and `cloudmain-dev.py`, `AsyncHTTPClient` with connect/read timeouts, shared-secret header, `expose` only (no published port), compose `healthcheck` + `depends_on: service_healthy`, and CI that skips only this service for fork PRs.
- **Error mapping:** gateway unreachable / connection refused / 599 → `502 ai_unavailable`; timeout → `504 ai_timeout`; gateway 4xx/5xx are translated to the codes in section 1, never passed through raw.
- **Pluggable provider:** `AI_PROVIDER=bedrock|openai|anthropic|local` plus `AI_MODEL`, and the provider's own variables (for example `AWS_REGION`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_BASE_URL` for local models). Keys exist **only** as environment variables of the `ai-gateway` container, never in the repo, image, Tornado, or any client. CI uses a mock provider (`AI_PROVIDER=mock`) so tests need no keys.
- **Nothing calls an LLM from the browser.** Clients talk only to Tornado; the frontend bundle contains no provider key, URL, or SDK.
- **Where limits live:** auth, rate limit and spend accounting are enforced in Tornado (state in the existing store) *and* the gateway enforces hard caps per request (tokens, timeout, retries) so a bug in one layer cannot cause unbounded spend.
- **Sheet access:** Tornado loads and saves sheets for the authenticated user and passes the content to the gateway; the gateway never receives a user-chosen storage path or id.

## 3. Migration of the existing clients

| Client | Old endpoint | New endpoint | Minimal client change |
|---|---|---|---|
| Framework `AgentModal` | `POST /agent/socialcalc/test` (`prompt`, `context`) | `POST /api/v1/ai/chat` (`message`, `context`, `mode: suggest`) or `/explain` for summary/email | Set the `apiEndpoint` prop and map `cell_updates` → `SET_CELLS` actions. **Shim (until then):** Tornado serves `/agent/socialcalc/test`, translating to `/chat` or `/explain` and returning the old `{type, actions[]}` / `{success, actions}` shape, 401/403 semantics unchanged. |
| Android app | `POST /api/edit-invoice/session` | `POST /api/v1/ai/chat` (first call creates the conversation) | Replace the hard-coded `localhost:8000` base with the Tornado base URL; `session_id` → `conversation_id`. |
| | `POST /api/edit-invoice/chat` → `{session_id, message, cell_updates{addr:val}, token_count}` | `POST /api/v1/ai/chat` with `sheet` | Map `cell_updates` array ↔ object. **Shim:** same-shape adapter at `/api/edit-invoice/*`. |
| | `GET\|DELETE /api/edit-invoice/session/{id}` | `GET\|DELETE /api/v1/ai/conversations/{id}` | path change only |
| invoice-agent frontend | `POST /api/generate-invoice` | `POST /api/v1/ai/invoice` (`mode: generate`) | change service base URL and field names; send cookie (`credentials: "include"`) |
| | `POST /api/chat`, `/api/session/*` | `POST /api/v1/ai/chat`, `/api/v1/ai/conversations/*` | as above |
| | `POST /api/generate` (`savestr`, `mode`) | `POST /api/v1/ai/invoice` | response is the MSC envelope, so the separate `convert-to-json` step goes away |

Shims are plain Tornado handlers that only translate shapes, live in one file (`handlers/ai_compat.py`), carry a `Deprecation` response header, and are removed once the client has migrated. Proposal: honour the framework and Android contracts via shims, and drop the third (see Q4).

## 4. Where socialcalc-mcp fits

socialcalc-mcp becomes the **toolset of the ai-gateway's agent loop**, not a separate user-facing service. The gateway runs the model with tool use; each tool call goes to the MCP server, whose tools **call Tornado's existing APIs instead of the local filesystem**:

| MCP tool intent | Tornado call |
|---|---|
| list / open a workbook | `GET /api/sheets`, `GET /api/sheets/<name>` |
| create / save a workbook | `PUT /api/sheets/<name>` |
| delete | `DELETE /api/sheets/<name>` |
| import / export (xlsx, csv) | the existing interop endpoints |

The MCP server is run **stateless**: the workbook arrives with the request (or is fetched through the calls above under the *caller's* session), operations happen in memory, and the result is returned. It has no filesystem tools, no path arguments, no HTTP listener outside the compose network, and acts with the end user's identity, so it can never see another user's sheets. Cell-level and batch tools (`set_cell`, `batch_update_cells`, `build_sheet`, `insert_table`) replace whole-sheet rewrites, which cuts tokens and makes `cell_updates` exact. Recalculation stays with SocialCalc/Tornado, not MCP.

## 5. Security requirements

1. **Per-user auth on every call.** No anonymous AI endpoint, including the gateway: it accepts only requests carrying the shared-secret header from Tornado and is not published outside the compose network.
2. **`userId` is server-side only.** It is derived from the verified cookie/token; any `userId`, `user`, bucket, path or key in a request body or query is ignored (or rejected with `400`). Storage keys are built from the session user and a validated sheet name.
3. **Per-user spend limits:** request rate, daily request cap and monthly token budget (section 1), counted from the provider's reported usage; hard per-request caps for tokens, retries and timeout in the gateway.
4. **CORS:** an explicit origin allow-list (`ALLOWED_ORIGINS`); never `*` together with credentials; cross-site cookies only with `COOKIE_SAMESITE=None` + `COOKIE_SECURE=true`.
5. **Secrets:** LLM keys only in the gateway's environment or the deployment's secret store; never logged, never returned in errors, never in client bundles, never committed. Logs record `request_id`, user, endpoint, token counts and status, not prompts or sheet contents by default.
6. **Input and output hygiene:** size limits as above; model output is validated before it is stored or returned; sheet content is treated as untrusted text in prompts (it cannot change tool permissions); a model can only act on the caller's own sheets.
7. **No HTML or URL fetching by the model** and no server-side rendering of client-supplied HTML in this API.
8. **Errors:** machine codes and a request id, never raw exception text or provider messages.

## 6. Open questions (for the mentor)

1. **Provider and keys.** Which provider is the default (Bedrock, OpenAI, Anthropic, a local model)? Who owns the account and how do keys reach the deployment (env file, secret manager)? Is a local model required for offline or no-cost demos?
2. **Budget per user.** What monthly token or cost cap per user and what daily request cap? Are there tiers (anonymous, free, paid)? What does the UI show at 80% and 100%?
3. **Streaming or not.** Proposal: v1 is non-streaming JSON with a 60 s timeout; add SSE (`text/event-stream`) on `/chat` in a later minor version if the UX needs it. Is a first version without streaming acceptable?
4. **Which legacy contracts to keep.** Shim the framework and Android contracts only, or also the invoice-agent frontend's?
5. **Native/non-browser auth.** Cookie only, or issue bearer tokens for the Android app?
6. **Conversation retention.** How long are chat histories stored, and may prompts be logged for debugging?
7. **Where does `invoice-agent`'s template library and prompt material live,** inside the gateway (proposed) or as a separate service?

## 7. Suggested order

1. Gateway skeleton with the mock provider + Tornado handler, `/health`, 502/504 mapping, tests in CI (no keys).
2. `/chat` and `/explain` with one real provider, spend limits, `/usage`.
3. MCP-as-tools against Tornado's `/api/sheets`, then `/invoice` and `/formula`.
4. Shims, then client migrations one at a time.
