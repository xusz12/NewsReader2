import { createECDH, ECDH } from "node:crypto";
import { readFileSync } from "node:fs";
import { generateRequestDetails, generateVAPIDKeys } from "web-push";

function run(operation, input) {
  switch (operation) {
    case "check":
      return true;
    case "keys":
      return generateVAPIDKeys();
    case "validate":
      try {
        ECDH.convertKey(Buffer.from(input.key, "base64url"), "prime256v1");
        return true;
      } catch {
        return false;
      }
    case "encrypt": {
      const key = createECDH("prime256v1");
      key.setPrivateKey(Buffer.from(input.privateKey, "base64url"));
      const request = generateRequestDetails(input.subscription, JSON.stringify(input.payload), {
        vapidDetails: {
          subject: input.subject,
          publicKey: key.getPublicKey().toString("base64url"),
          privateKey: input.privateKey,
        },
        TTL: input.ttl,
        topic: input.payload.tag,
        contentEncoding: "aes128gcm",
      });
      return { headers: request.headers, body: request.body.toString("base64") };
    }
    default:
      throw new Error("unknown_operation");
  }
}

try {
  const result = run(process.argv[2], JSON.parse(readFileSync(0, "utf8")));
  process.stdout.write(JSON.stringify(result));
} catch {
  process.exitCode = 1;
}
