import assert from "node:assert/strict";
import test from "node:test";

import {
  dayRangeUtc,
  estimateCostUsd,
  parseClientTokens,
  parseModelPrices,
  resolveClientId,
  validateEvent,
  weekRangeUtc,
} from "../src/usage.js";

test("parseClientTokens supports comma separated values", () => {
  assert.deepEqual(parseClientTokens("laptop=secret,desktop=other"), {
    laptop: "secret",
    desktop: "other",
  });
});

test("parseClientTokens supports JSON object", () => {
  assert.deepEqual(parseClientTokens('{"laptop":"secret"}'), {
    laptop: "secret",
  });
});

test("resolveClientId returns matching client", () => {
  assert.equal(resolveClientId({ laptop: "secret" }, "secret"), "laptop");
  assert.equal(resolveClientId({ laptop: "secret" }, "wrong"), null);
});

test("resolveClientId supports shared wildcard token", () => {
  assert.equal(resolveClientId({ "*": "shared-secret" }, "shared-secret"), "*");
});

test("resolveClientId prefers wildcard token only when it matches", () => {
  assert.equal(resolveClientId({ "*": "shared-secret", laptop: "device-secret" }, "device-secret"), "laptop");
  assert.equal(resolveClientId({ "*": "shared-secret", laptop: "device-secret" }, "shared-secret"), "*");
});

test("validateEvent rejects client mismatch", () => {
  const event = validEvent();
  event.client_id = "other";
  assert.equal(validateEvent("laptop", event), "client_id does not match token");
});

test("validateEvent allows any client id for shared wildcard token", () => {
  const event = validEvent();
  event.client_id = "client-b";
  assert.equal(validateEvent("*", event), null);
});

test("validateEvent accepts valid event", () => {
  assert.equal(validateEvent("laptop", validEvent()), null);
});

test("validateEvent accepts optional cached token fields", () => {
  const event = validEvent();
  event.cached_input_tokens = 40;
  event.non_cached_input_tokens = 60;
  event.reasoning_output_tokens = 10;
  assert.equal(validateEvent("laptop", event), null);
});

test("validateEvent rejects invalid optional cached token fields", () => {
  const event = validEvent();
  event.cached_input_tokens = "40";
  assert.equal(validateEvent("laptop", event), "cached_input_tokens must be integer");
});

test("dayRangeUtc handles Asia/Tokyo day boundary", () => {
  assert.deepEqual(dayRangeUtc("2026-05-06", "Asia/Tokyo"), {
    start: "2026-05-05T15:00:00.000Z",
    end: "2026-05-06T14:59:59.999Z",
  });
});

test("weekRangeUtc returns Monday to Sunday in timezone", () => {
  assert.deepEqual(weekRangeUtc("2026-05-07", "Asia/Tokyo"), {
    localStart: "2026-05-04",
    localEnd: "2026-05-10",
    start: "2026-05-03T15:00:00.000Z",
    end: "2026-05-10T14:59:59.999Z",
  });
});

test("parseModelPrices supports defaults and overrides", () => {
  const prices = parseModelPrices('{"gpt-test":{"input":1,"output":2}}');
  assert.deepEqual(prices["gpt-test"], { input: 1, output: 2 });
  assert.deepEqual(prices["gpt-5.5"], { input: 5, output: 30 });
  assert.deepEqual(prices["gpt-5.4-mini"], { input: 0.75, output: 4.5 });
  assert.deepEqual(prices["gpt-5.3-codex"], { input: 1.75, output: 14 });
  assert.deepEqual(prices["gpt-4.1-mini"], { input: 0.4, output: 1.6 });
});

test("estimateCostUsd calculates input and output cost per million tokens", () => {
  const prices = parseModelPrices('{"gpt-test":{"input":1,"output":2}}');
  assert.equal(estimateCostUsd(prices, "gpt-test", 1_000_000, 500_000), 2);
});

test("estimateCostUsd uses cached input price when configured", () => {
  const prices = parseModelPrices('{"gpt-test":{"input":1,"cached_input":0.25,"output":2}}');
  assert.equal(estimateCostUsd(prices, "gpt-test", 1_000_000, 500_000, 400_000), 1.7);
});

function validEvent() {
  return {
    event_id: "event-1",
    client_id: "laptop",
    session_id: "session-1",
    turn_id: "turn-1",
    timestamp: "2026-05-06T10:15:00Z",
    input_tokens: 100,
    output_tokens: 25,
    total_tokens: 125,
    model: "gpt-5.5",
    source: "codex-tui-log",
    schema_version: 1,
  };
}
