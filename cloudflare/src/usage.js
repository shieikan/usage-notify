export function parseClientTokens(raw) {
  const trimmed = raw.trim();
  if (!trimmed) {
    return {};
  }
  if (trimmed.startsWith("{")) {
    return JSON.parse(trimmed);
  }

  const tokens = {};
  for (const pair of trimmed.split(",")) {
    const [clientId, ...rest] = pair.split("=");
    const token = rest.join("=");
    if (clientId && token) {
      tokens[clientId.trim()] = token.trim();
    }
  }
  return tokens;
}

export function resolveClientId(tokenMap, token) {
  if (typeof tokenMap["*"] === "string" && constantTimeEqual(tokenMap["*"], token)) {
    return "*";
  }
  for (const [clientId, clientToken] of Object.entries(tokenMap)) {
    if (clientId === "*") {
      continue;
    }
    if (constantTimeEqual(clientToken, token)) {
      return clientId;
    }
  }
  return null;
}

export async function storeUsageEvents(db, authenticatedClientId, events) {
  const accepted = [];
  const duplicates = [];
  const rejected = [];
  const receivedAt = new Date().toISOString();

  for (const event of events) {
    const error = validateEvent(authenticatedClientId, event);
    const eventId = String(event?.event_id || "");
    if (error) {
      rejected.push({ event_id: eventId, error });
      continue;
    }

    try {
      await db.prepare(
        `INSERT INTO usage_events (
          client_id, event_id, session_id, turn_id, occurred_at, received_at,
          input_tokens, cached_input_tokens, non_cached_input_tokens,
          output_tokens, reasoning_output_tokens, total_tokens,
          model, source, schema_version, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
        .bind(
          event.client_id,
          event.event_id,
          event.session_id,
          event.turn_id,
          event.timestamp,
          receivedAt,
          event.input_tokens,
          optionalInteger(event.cached_input_tokens),
          optionalInteger(event.non_cached_input_tokens),
          event.output_tokens,
          optionalInteger(event.reasoning_output_tokens),
          event.total_tokens,
          event.model || null,
          event.source,
          event.schema_version,
          JSON.stringify(event)
        )
        .run();
      accepted.push(eventId);
    } catch (error) {
      if (isUniqueConstraintError(error)) {
        duplicates.push(eventId);
      } else {
        rejected.push({ event_id: eventId, error: String(error).slice(0, 1000) });
      }
    }
  }

  return { accepted, duplicates, rejected };
}

export function validateEvent(authenticatedClientId, event) {
  if (!event || typeof event !== "object") {
    return "event must be object";
  }

  const required = [
    "event_id",
    "client_id",
    "session_id",
    "turn_id",
    "timestamp",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "source",
    "schema_version",
  ];
  for (const key of required) {
    if (!(key in event)) {
      return `missing ${key}`;
    }
  }
  if (authenticatedClientId !== "*" && event.client_id !== authenticatedClientId) {
    return "client_id does not match token";
  }
  for (const key of ["input_tokens", "output_tokens", "total_tokens", "schema_version"]) {
    if (!Number.isInteger(event[key])) {
      return `${key} must be integer`;
    }
  }
  for (const key of ["cached_input_tokens", "non_cached_input_tokens", "reasoning_output_tokens"]) {
    if (key in event && event[key] !== null && !Number.isInteger(event[key])) {
      return `${key} must be integer`;
    }
  }
  return null;
}

export async function buildDailyReport(db, reportDate, timezone, modelPricesJson = "") {
  const modelPrices = parseModelPrices(modelPricesJson);
  const range = dayRangeUtc(reportDate, timezone);
  const weekRange = weekRangeUtc(reportDate, timezone);
  const total = await db.prepare(
    `SELECT
      COALESCE(SUM(input_tokens), 0) AS input_tokens,
      COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
      COALESCE(SUM(non_cached_input_tokens), 0) AS non_cached_input_tokens,
      COALESCE(SUM(output_tokens), 0) AS output_tokens,
      COALESCE(SUM(reasoning_output_tokens), 0) AS reasoning_output_tokens,
      COALESCE(SUM(total_tokens), 0) AS total_tokens,
      COUNT(DISTINCT session_id) AS sessions
     FROM usage_events
     WHERE occurred_at >= ? AND occurred_at <= ?`
  )
    .bind(range.start, range.end)
    .first();

  const byClientModelCost = await db.prepare(
    `SELECT
       client_id,
       COALESCE(model, 'unknown') AS model,
       SUM(input_tokens) AS input_tokens,
       SUM(cached_input_tokens) AS cached_input_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(total_tokens) AS total_tokens
     FROM usage_events
     WHERE occurred_at >= ? AND occurred_at <= ?
     GROUP BY client_id, model
     ORDER BY client_id ASC, total_tokens DESC`
  )
    .bind(range.start, range.end)
    .all();

  const byClient = summarizeClientCosts(byClientModelCost.results, modelPrices);

  const byModel = await db.prepare(
    `SELECT COALESCE(model, 'unknown') AS model, SUM(total_tokens) AS total_tokens
     FROM usage_events
     WHERE occurred_at >= ? AND occurred_at <= ?
     GROUP BY model
     ORDER BY total_tokens DESC`
  )
    .bind(range.start, range.end)
    .all();

  const byClientModel = await db.prepare(
    `SELECT client_id, COALESCE(model, 'unknown') AS model, SUM(total_tokens) AS total_tokens
     FROM usage_events
     WHERE occurred_at >= ? AND occurred_at <= ?
     GROUP BY client_id, model
     ORDER BY client_id ASC, total_tokens DESC`
  )
    .bind(range.start, range.end)
    .all();

  const weeklyByClient = await db.prepare(
    `SELECT client_id, SUM(total_tokens) AS total_tokens
     FROM usage_events
     WHERE occurred_at >= ? AND occurred_at <= ?
     GROUP BY client_id`
  )
    .bind(weekRange.start, weekRange.end)
    .all();
  const weeklyShareByClient = weeklyShareMap(weeklyByClient.results);
  const dayCost = byClient.reduce((sum, row) => sum + row.cost_usd, 0);

  const lines = [
    "Codex usage report",
    "",
    `Period: ${reportDate} ${timezone}`,
    `Total: ${formatNumber(total.total_tokens)} tokens`,
    `Input: ${formatNumber(total.input_tokens)}`,
    `Cached input: ${formatNumber(total.cached_input_tokens)}`,
    `Output: ${formatNumber(total.output_tokens)}`,
    `Reasoning output: ${formatNumber(total.reasoning_output_tokens)}`,
    `Estimated API usage: ${formatUsd(dayCost)}`,
    `Sessions: ${formatNumber(total.sessions)}`,
    `Week: ${weekRange.localStart} - ${weekRange.localEnd}`,
    "",
    "By client (daily tokens / estimated cost / weekly share):",
    ...formatClientCostRows(byClient, weeklyShareByClient),
    "",
    "By model:",
    ...formatRows(byModel.results, "model"),
    "",
    "By client and model:",
    ...formatClientModelRows(byClientModel.results),
  ];
  return lines.join("\n");
}

export function dayRangeUtc(reportDate, timezone) {
  const start = zonedDateToUtc(`${reportDate}T00:00:00`, timezone);
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000 - 1);
  return {
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

export function weekRangeUtc(reportDate, timezone) {
  const localNoon = new Date(`${reportDate}T12:00:00.000Z`);
  const parts = datePartsInTimezone(localNoon, timezone);
  const localDate = new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
  const dayOfWeek = localDate.getUTCDay();
  const daysSinceMonday = (dayOfWeek + 6) % 7;
  const monday = new Date(localDate.getTime() - daysSinceMonday * 24 * 60 * 60 * 1000);
  const sunday = new Date(monday.getTime() + 6 * 24 * 60 * 60 * 1000);
  const localStart = isoDate(monday);
  const localEnd = isoDate(sunday);
  const start = zonedDateToUtc(`${localStart}T00:00:00`, timezone);
  const end = new Date(zonedDateToUtc(`${localEnd}T00:00:00`, timezone).getTime() + 24 * 60 * 60 * 1000 - 1);
  return {
    localStart,
    localEnd,
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

export function parseModelPrices(raw) {
  const trimmed = String(raw || "").trim();
  if (!trimmed) {
    return DEFAULT_MODEL_PRICES;
  }
  const parsed = JSON.parse(trimmed);
  return { ...DEFAULT_MODEL_PRICES, ...parsed };
}

export function estimateCostUsd(modelPrices, model, inputTokens, outputTokens, cachedInputTokens = 0) {
  const price = modelPrices[model] || modelPrices[String(model).toLowerCase()];
  if (!price) {
    return 0;
  }
  const cachedInput = Math.max(0, Number(cachedInputTokens || 0));
  const totalInput = Number(inputTokens || 0);
  const uncachedInput = Math.max(0, totalInput - cachedInput);
  const inputCost = price.cached_input === undefined
    ? (totalInput / 1_000_000) * Number(price.input || 0)
    : (uncachedInput / 1_000_000) * Number(price.input || 0)
      + (cachedInput / 1_000_000) * Number(price.cached_input || 0);
  return inputCost
    + (Number(outputTokens || 0) / 1_000_000) * Number(price.output || 0);
}

function zonedDateToUtc(localIso, timezone) {
  const naiveUtc = new Date(`${localIso}.000Z`);
  const values = datePartsInTimezone(naiveUtc, timezone);
  const asIfUtc = Date.UTC(
    values.year,
    values.month - 1,
    values.day,
    values.hour,
    values.minute,
    values.second
  );
  const offset = asIfUtc - naiveUtc.getTime();
  return new Date(naiveUtc.getTime() - offset);
}

function datePartsInTimezone(date, timezone) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
  };
}

function summarizeClientCosts(rows, modelPrices) {
  const clients = new Map();
  for (const row of rows || []) {
    const client = row.client_id;
    const current = clients.get(client) || {
      client_id: client,
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
      cost_usd: 0,
    };
    current.input_tokens += Number(row.input_tokens || 0);
    current.output_tokens += Number(row.output_tokens || 0);
    current.total_tokens += Number(row.total_tokens || 0);
    current.cost_usd += estimateCostUsd(
      modelPrices,
      row.model,
      row.input_tokens,
      row.output_tokens,
      row.cached_input_tokens
    );
    clients.set(client, current);
  }
  return [...clients.values()].sort((a, b) => b.total_tokens - a.total_tokens);
}

function optionalInteger(value) {
  return Number.isInteger(value) ? value : null;
}

function weeklyShareMap(rows) {
  const total = (rows || []).reduce((sum, row) => sum + Number(row.total_tokens || 0), 0);
  const shares = new Map();
  for (const row of rows || []) {
    shares.set(row.client_id, total > 0 ? Number(row.total_tokens || 0) / total : 0);
  }
  return shares;
}

function formatRows(rows, labelKey) {
  if (!rows || rows.length === 0) {
    return ["- none: 0"];
  }
  return rows.map((row) => `- ${row[labelKey]}: ${formatNumber(row.total_tokens)}`);
}

function formatClientCostRows(rows, weeklyShareByClient) {
  if (!rows || rows.length === 0) {
    return ["- none: 0 / $0.00 / 0.0%"];
  }
  return rows.map((row) => {
    const share = weeklyShareByClient.get(row.client_id) || 0;
    return `- ${row.client_id}: ${formatNumber(row.total_tokens)} / ${formatUsd(row.cost_usd)} / ${formatPercent(share)}`;
  });
}

function formatClientModelRows(rows) {
  if (!rows || rows.length === 0) {
    return ["- none: 0"];
  }
  return rows.map((row) => `- ${row.client_id} / ${row.model}: ${formatNumber(row.total_tokens)}`);
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("en-US");
}

function formatUsd(value) {
  return `$${Number(value || 0).toFixed(4)}`;
}

function formatPercent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function isUniqueConstraintError(error) {
  const message = String(error).toLowerCase();
  return message.includes("unique") || message.includes("constraint");
}

function constantTimeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") {
    return false;
  }
  if (a.length !== b.length) {
    return false;
  }
  let result = 0;
  for (let index = 0; index < a.length; index += 1) {
    result |= a.charCodeAt(index) ^ b.charCodeAt(index);
  }
  return result === 0;
}

const DEFAULT_MODEL_PRICES = {
  "gpt-5.5": { input: 5, output: 30 },
  "gpt-5.4-pro": { input: 30, output: 180 },
  "gpt-5.4": { input: 2.5, output: 15 },
  "gpt-5.4-mini": { input: 0.75, output: 4.5 },
  "gpt-5.4-nano": { input: 0.2, output: 1.25 },
  "gpt-5.3-codex": { input: 1.75, output: 14 },
  "gpt-5.3-chat-latest": { input: 1.75, output: 14 },
  "gpt-5.2": { input: 1.75, output: 14 },
  "gpt-5.2-codex": { input: 1.75, output: 14 },
  "gpt-5.2-chat-latest": { input: 1.75, output: 14 },
  "gpt-5.1": { input: 1.25, output: 10 },
  "gpt-5.1-codex": { input: 1.25, output: 10 },
  "gpt-5.1-codex-max": { input: 1.25, output: 10 },
  "gpt-5": { input: 1.25, output: 10 },
  "gpt-5-codex": { input: 1.25, output: 10 },
  "gpt-5-mini": { input: 0.25, output: 2 },
  "gpt-5-nano": { input: 0.05, output: 0.4 },
  "gpt-4.1": { input: 2, output: 8 },
  "gpt-4.1-mini": { input: 0.4, output: 1.6 },
  "gpt-4.1-nano": { input: 0.1, output: 0.4 },
  "gpt-4o-mini": { input: 0.15, output: 0.6 },
  "o4-mini": { input: 1.1, output: 4.4 },
};
