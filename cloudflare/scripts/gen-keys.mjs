// Prints the four secrets the Worker needs. Nothing is written to disk: pipe each
// value into `wrangler secret put NAME` (see docs/how-to/get-phone-reminders.md).
import { webcrypto, randomBytes } from "node:crypto";

const b64url = (buf) => Buffer.from(buf).toString("base64url");

const { publicKey, privateKey } = await webcrypto.subtle.generateKey(
  { name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
const jwk = await webcrypto.subtle.exportKey("jwk", privateKey);
// VAPID's public key is the raw uncompressed point: 0x04 || x || y
const raw = new Uint8Array(await webcrypto.subtle.exportKey("raw", publicKey));

console.log(`VAPID_PUBLIC_KEY=${b64url(raw)}`);
console.log(`VAPID_PRIVATE_KEY=${jwk.d}`);
console.log(`SYNC_TOKEN=${b64url(randomBytes(32))}`);
console.log(`SUBSCRIBE_TOKEN=${b64url(randomBytes(32))}`);
