// Official TypeScript fetch client, with HTTP carried over private test pipes.
// No network transport or persistent key is used; payment proofs stay in the pipes.
import { createInterface } from "node:readline";
import { x402Client } from "@x402/core/client";
import { ExactEvmScheme } from "@x402/evm/exact/client";
import {
  appendPaymentIdentifierToExtensions,
  generatePaymentId,
} from "@x402/extensions/payment-identifier";
import { wrapFetchWithPayment } from "@x402/fetch";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";

const reader = createInterface({ input: process.stdin, crlfDelay: Infinity });
const lines = reader[Symbol.asyncIterator]();
const config = JSON.parse((await lines.next()).value);
const client = new x402Client().register(
  "eip155:84532",
  new ExactEvmScheme(privateKeyToAccount(generatePrivateKey())),
);
const paymentId = generatePaymentId();
client.onBeforePaymentCreation(({ paymentRequired }) => {
  appendPaymentIdentifierToExtensions(paymentRequired.extensions, paymentId);
});
let requests = 0;
const bridge = async (input, init) => {
  const request = new Request(input, init);
  requests++;
  process.stdout.write(
    JSON.stringify({
      type: "request",
      url: request.url,
      method: request.method,
      headers: Object.fromEntries(request.headers),
    }) + "\n",
  );
  const response = JSON.parse((await lines.next()).value);
  return new Response(JSON.stringify(response.body), {
    status: response.status,
    headers: response.headers,
  });
};
try {
  const response = await wrapFetchWithPayment(bridge, client)(config.url);
  const body = await response.json();
  process.stdout.write(
    JSON.stringify({
      type: "done",
      status: response.status,
      requests,
      receipt: body.receipt,
      settlementHeader: response.headers.has("PAYMENT-RESPONSE"),
    }) + "\n",
  );
} catch {
  process.stdout.write(JSON.stringify({ type: "error" }) + "\n");
  process.exitCode = 1;
} finally {
  reader.close();
}
