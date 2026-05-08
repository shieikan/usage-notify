import {
  buildDailyReport,
  dayRangeUtc,
  parseClientTokens,
  resolveClientId,
  storeUsageEvents,
} from "./usage.js";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/healthz") {
      return json({ ok: true });
    }

    if (request.method === "POST" && url.pathname === "/v1/usage-events") {
      return handleUsageEvents(request, env);
    }

    if (request.method === "POST" && url.pathname === "/v1/reports/daily") {
      const token = bearerToken(request);
      if (!token || token !== env.ADMIN_TOKEN) {
        return json({ error: "unauthorized" }, 401);
      }
      const body = await request.json().catch(() => ({}));
      const reportDate = typeof body.date === "string" ? body.date : todayInTimezone(env.USAGE_NOTIFY_TIMEZONE || "Asia/Tokyo");
      const content = await buildDailyReport(
        env.DB,
        reportDate,
        env.USAGE_NOTIFY_TIMEZONE || "Asia/Tokyo",
        env.MODEL_PRICES_JSON || ""
      );
      if (body.send_discord === true) {
        await sendDiscord(env.DISCORD_WEBHOOK_URL, content);
      }
      return json({ content });
    }

    return json({ error: "not found" }, 404);
  },

  async scheduled(controller, env) {
    const timezone = env.USAGE_NOTIFY_TIMEZONE || "Asia/Tokyo";
    const offset = Number.parseInt(env.REPORT_DAYS_OFFSET || "0", 10);
    const reportDate = dateInTimezone(timezone, offset);
    const range = dayRangeUtc(reportDate, timezone);
    const scheduledFor = new Date(controller.scheduledTime || Date.now()).toISOString();

    const existing = await env.DB.prepare(
      `SELECT id FROM notification_runs
       WHERE report_scope = ? AND scope_client_id IS NULL AND period_type = ?
       AND period_start = ? AND period_end = ? AND scheduled_for = ?`
    )
      .bind("all_clients", "daily", range.start, range.end, scheduledFor)
      .first();
    if (existing) {
      return;
    }

    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO notification_runs
       (report_scope, scope_client_id, period_type, period_start, period_end, scheduled_for, status, created_at, updated_at)
       VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)`
    )
      .bind("all_clients", "daily", range.start, range.end, scheduledFor, "running", now, now)
      .run();

    try {
      const content = await buildDailyReport(env.DB, reportDate, timezone, env.MODEL_PRICES_JSON || "");
      await sendDiscord(env.DISCORD_WEBHOOK_URL, content);
      await env.DB.prepare(
        `UPDATE notification_runs SET status = ?, error = NULL, updated_at = ? WHERE scheduled_for = ?`
      )
        .bind("sent", new Date().toISOString(), scheduledFor)
        .run();
    } catch (error) {
      await env.DB.prepare(
        `UPDATE notification_runs SET status = ?, error = ?, updated_at = ? WHERE scheduled_for = ?`
      )
        .bind("failed", String(error).slice(0, 1000), new Date().toISOString(), scheduledFor)
        .run();
      throw error;
    }
  },
};

async function handleUsageEvents(request, env) {
  const token = bearerToken(request);
  if (!token) {
    return json({ error: "missing bearer token" }, 401);
  }

  const tokenMap = parseClientTokens(env.CLIENT_TOKENS || "");
  const clientId = resolveClientId(tokenMap, token);
  if (!clientId) {
    return json({ error: "invalid token" }, 403);
  }

  const payload = await request.json().catch(() => null);
  if (!payload || !Array.isArray(payload.events)) {
    return json({ error: "events must be an array" }, 400);
  }

  const result = await storeUsageEvents(env.DB, clientId, payload.events);
  return json(result, result.rejected.length > 0 ? 207 : 202);
}

function bearerToken(request) {
  const authorization = request.headers.get("Authorization") || "";
  const prefix = "Bearer ";
  if (!authorization.startsWith(prefix)) {
    return null;
  }
  return authorization.slice(prefix.length);
}

async function sendDiscord(webhookUrl, content) {
  if (!webhookUrl) {
    throw new Error("DISCORD_WEBHOOK_URL is not set");
  }
  const response = await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Discord webhook returned HTTP ${response.status}: ${detail}`);
  }
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function todayInTimezone(timezone) {
  return dateInTimezone(timezone, 0);
}

function dateInTimezone(timezone, offsetDays) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const now = new Date(Date.now() + offsetDays * 24 * 60 * 60 * 1000);
  return formatter.format(now);
}
