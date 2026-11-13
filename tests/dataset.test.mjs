import test from "node:test";
import assert from "node:assert/strict";
import { loadCardDetails } from "../core/dataset.js";

test("loadCardDetails merges shards in manifest order", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  const responses = new Map([
    ["./data/card_details_manifest.json", ["card_details_002.json", "card_details_001.json"]],
    ["./data/card_details_002.json", [{ index: 0, name: "first" }]],
    ["./data/card_details_001.json", [{ index: 1, name: "second" }]],
  ]);
  const requested = [];
  globalThis.fetch = async (url) => {
    requested.push(url);
    return {
      ok: true,
      json: async () => responses.get(url),
    };
  };

  assert.deepEqual(await loadCardDetails(), [
    { index: 0, name: "first" },
    { index: 1, name: "second" },
  ]);
  assert.equal(requested[0], "./data/card_details_manifest.json");
});

test("loadCardDetails rejects invalid manifest entries", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ["../outside.json"],
  });

  await assert.rejects(loadCardDetails(), /Invalid card details manifest/);
});
