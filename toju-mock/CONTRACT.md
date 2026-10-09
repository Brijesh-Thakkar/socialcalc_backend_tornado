# toju-mock — the mocked toju.network API contract

**This is a MOCK.** `toju-mock` is a local stand-in for the real `toju.network`
(Storacha-Solana-Sdk) server. It does **no** real IPFS pinning, **no** Solana RPC and
**no** payment. It exists only so the Tornado ↔ toju-sidecar integration can be built and
tested with no hosted backend, no accounts and no money (option C in `MENTOR_FEEDBACK.md`).

The contract below is **derived from the SDK and server source, with file:line cites** — not
guessed. Paths are in the `Storacha-Solana-Sdk` repo unless noted. The Node contract test
(`toju-sidecar/test/contract.test.js`) runs the **real** `@toju.network/sol@1.0.0` against a
recording server and fails loudly if the SDK stops sending exactly these requests.

## Endpoints the SDK actually calls (mocked here)

| # | Method · Path | Request (from SDK) | Response fields the SDK reads | SDK cite | Server cite | Mock impl |
|---|---|---|---|---|---|---|
| 1 | `GET /pricing/quote?size&duration` | query `size` (bytes), `duration` (days) | `quote.totalCost` (lamports) | `packages/sol/src/client.ts:143-150` | `server/src/controllers/pricing.controller.ts:13-37`; shape `server/src/types.ts` `QuoteOutput` | `index.js` `GET /pricing/quote` |
| 2 | `GET /pricing/sol` | – | `price` (USD number) | `packages/sol/src/client.ts:240-243` | `pricing.controller.ts:39-51` | `GET /pricing/sol` |
| 3 | `POST /upload/deposit` | **multipart**: `file` (one per file), `duration` (seconds), `publicKey` (base58), optional `userEmail`, `directoryName` | `cid`, `instructions[0]` = `{programId, keys[{pubkey,isSigner,isWritable}], data(base64)}` | `packages/sol/src/payment.ts:36-64` | `server/src/controllers/upload.controller.ts:162-315`; instruction shape `solana.controller.ts:51-61` | `POST /upload/deposit` |
| 4 | `POST /upload/confirm` | **json**: `cid`, `transactionHash` | `url`, `message` | `packages/sol/src/payment.ts:122-153` | `upload.controller.ts:581-635` | `POST /upload/confirm` |
| 5 | `GET /upload/history?userAddress&page&limit` | query `userAddress`, `page`, `limit` | `data` (**must be an array**), `next` | `packages/sol/src/upload-history.ts:326-372` | `server/src/routes/upload.route.ts:37` | `GET /upload/history` |
| 6 | `GET /storage/renewal-cost?cid&duration` | query `cid`, `duration` | `costInLamports`, `costInSOL`, `newExpirationDate`, … | `packages/sol/src/payment.ts:193-208` | `server/src/controllers/storage.controller.ts:92-99` | `GET /storage/renewal-cost` |
| 7 | `POST /storage/renew` | **json**: `cid`, `duration`, `publicKey` | `instructions[]` | `packages/sol/src/payment.ts:240-255` | `storage.controller.ts:167-170` | `POST /storage/renew` |
| 8 | `POST /storage/confirm-renewal` | **json**: `cid`, `duration`, `transactionHash` | `url` | `packages/sol/src/payment.ts:278-289` | `storage.controller.ts` | `POST /storage/confirm-renewal` |

Endpoints 1–5 are exercised by the sidecar's upload/status flow and are asserted by the
contract test. 6–8 (renewal) are implemented for completeness but are not wired into the
Tornado routes (Tornado exposes save / retrieve / status only).

## Endpoints NOT in the SDK surface (mock-defined)

The SDK has **no retrieve function** — retrieval is by the IPFS gateway URL that
`/upload/confirm` returns (real server: `${PINATA_GATEWAY}/ipfs/<cid>`,
`server/src/services/storage/pinata.service.ts:14`). The mock therefore defines:

| Method · Path | Purpose | Returns |
|---|---|---|
| `GET /ipfs/:cid` and `/ipfs/:cid/:name` | gateway retrieve (byte-identical to the uploaded bytes) | raw bytes + stored `Content-Type` |
| `GET /status/:cid` | convenience status lookup used by the sidecar | `{cid, active, expiresAt, txHash, size, mocked}` |
| `GET /health` | liveness | `{status:"ok", mode:"MOCK-BACKED"}` |

The sidecar's `GET /status/:cid` is driven through the SDK's `getUserUploadHistory`
(endpoint #5), then falls through to the mock-defined status shape above.

## Known fakery (how the mock differs from the real server, on purpose)

- **CID** is `"bafkmock" + sha256(bytes)[:52]` (deterministic, obviously fake). The real
  server computes a real IPFS CIDv1 via Pinata/ipfs-car.
- **Instruction** is a single no-op System-Program instruction whose only signer is the
  payer, so the sidecar's ephemeral keypair can sign and `Transaction.serialize()` succeeds.
  The real instruction is the Anchor `create_deposit` call to program
  `CSXnfQsFWxdPB5pnS73TQDA6ivK6kcFnRwtt6TgFquxH`.
- **Pricing** is a flat `1000 lamports/byte/day`; SOL price is a flat `$150`. The real server
  reads a DB config row and the live Pyth SOL/USD feed.
- **No chain, no pinning, no payment.** `transactionHash` is accepted without verification —
  which, notably, mirrors the *real* server's behaviour (`upload.controller.ts:581`, see
  AUDIT_2.md §5 Storacha #1).

## When the SDK changes

If `@toju.network/sol` is bumped and `toju-sidecar/test/contract.test.js` fails, the SDK's
wire contract changed. Update `toju-mock/index.js` and this file **deliberately** to match the
new requests, bump the pinned SDK version in `toju-sidecar/package.json`, and re-run the
contract test until green.
