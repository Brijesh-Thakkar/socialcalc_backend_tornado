# toju-mock — **MOCK-BACKED** stand-in for the toju.network server

A local server that implements **exactly** the HTTP API that `@toju.network/sol` calls, so the
Tornado ↔ toju-sidecar integration can be built and tested with **no hosted backend, no accounts
and no money** (option C in `MENTOR_FEEDBACK.md`).

**This is NOT the real toju.network backend.** It does no IPFS pinning, no Solana RPC and no
payment. It stores uploaded bytes in memory and returns deterministic, obviously-fake CIDs
(`bafkmock…`). Do not deploy it.

- **The contract it mocks (with SDK + server file:line cites): [`CONTRACT.md`](./CONTRACT.md).**
- The Node contract test (`../toju-sidecar/test/contract.test.js`) runs the **real** SDK against a
  recording server and fails loudly if the SDK's requests stop matching this mock.

## Endpoints

SDK-driven: `POST /upload/deposit`, `POST /upload/confirm`, `GET /pricing/quote`,
`GET /pricing/sol`, `GET /upload/history`, and the renewal trio (`/storage/*`).
Mock-defined (retrieval is not part of the SDK surface): `GET /ipfs/:cid[/:name]` (byte-identical
retrieve), `GET /status/:cid`, `GET /health`. Full table in `CONTRACT.md`.

## Run

```bash
npm install
TOJU_MOCK_PORT=5057 node index.js
```
