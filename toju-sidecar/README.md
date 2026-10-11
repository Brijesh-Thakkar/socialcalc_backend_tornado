# toju-sidecar — **MOCK-BACKED**

A thin HTTP sidecar that wraps **`@toju.network/sol`** (the Storacha-Solana-Sdk) so Tornado can
save a SocialCalc sheet to IPFS and pay for storage with SOL — without importing the SDK or
talking to Solana itself. Tornado calls this sidecar over HTTP (`handlers/toju.py`), exactly
like the meshkit and fastapi-interop sidecars.

## ⚠️ MOCK-BACKED

The real toju.network API is suspended and the on-chain program is not on devnet
(see `AUDIT_2.md §1`), so there is no live backend to sign real transactions against. By
default this sidecar runs `TOJU_MODE=mock`: it supplies a **fake Solana connection and an
ephemeral keypair signer**, so the SDK's real `upload`/`confirm`/`quote`/`history` HTTP calls
execute end-to-end against the local **`toju-mock`** with **no real Solana RPC, no real IPFS,
and no money**. The HTTP contract exercised is identical to the real backend's
(see `../toju-mock/CONTRACT.md`), so switching to a real backend later is just config.

## Endpoints (called by Tornado)

| Method · Path | Body / params | Returns |
|---|---|---|
| `POST /upload` | json `{sheet, fname?, durationDays?}` | `{cid, url, signature, success, estimate, mocked}` |
| `GET /retrieve/:cid` | – | raw stored bytes (byte-identical to what was saved) |
| `GET /status/:cid` | – | `{cid, active, expiresAt, txHash, mocked}` |
| `GET /health` | – | `{status, mode, backend, network}` |

Every request except `/health` must carry `X-Toju-Token: <TOJU_SHARED_SECRET>`; missing/wrong → **401**.

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `TOJU_SIDECAR_PORT` | `5056` | listen port |
| `TOJU_API_URL` | `http://localhost:5057` | backend the SDK calls (the mock) |
| `TOJU_SHARED_SECRET` | – | shared secret, must match Tornado's `TOJU_SHARED_SECRET` |
| `TOJU_MODE` | `mock` | `mock` = offline; `real` = talk to a real backend (future) |
| `SOLANA_NETWORK` | `devnet` | **devnet only**; the sidecar **refuses to start on mainnet** unless `ALLOW_MAINNET=true` (never set it) |

## Run locally

```bash
npm install
TOJU_SHARED_SECRET=dev-secret node index.js          # start toju-mock on :5057 first
```

The pinned SDK version is in `package.json` (`@toju.network/sol` exact). If you bump it, run the
contract test (`npm test`) and update `../toju-mock` + `CONTRACT.md` until it passes.
