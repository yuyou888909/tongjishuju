const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=60, stale-while-revalidate=240",
};

export default async (req) => {
  if (req.method !== "GET") {
    return json({ ok: false, error: "Method not allowed" }, 405);
  }
  const url = new URL(req.url);
  const kind = (url.searchParams.get("kind") || "a").toLowerCase();
  const symbol = (url.searchParams.get("symbol") || "").trim().toUpperCase();
  const limit = clampInt(Number(url.searchParams.get("limit") || 120), 20, 180);

  if (!/^[A-Z0-9._-]{1,16}$/.test(symbol)) {
    return json({ ok: false, error: "Invalid symbol" }, 400);
  }

  try {
    const kline = kind === "us" ? await usKline(symbol, limit) : await aKline(symbol, limit);
    return json({ ok: true, kind, symbol, source: kind === "us" ? "Yahoo chart" : "Eastmoney kline", kline });
  } catch (error) {
    return json({ ok: false, error: error instanceof Error ? error.message : String(error) }, 502);
  }
};

export const config = {
  path: "/api/live-kline",
  method: ["GET"],
};

async function aKline(symbol, limit) {
  const code = symbol.replace(/\D/g, "").padStart(6, "0");
  const secid = `${code.startsWith("6") || code.startsWith("5") || code.startsWith("9") ? "1" : "0"}.${code}`;
  const beg = yyyymmdd(daysAgo(260));
  const params = new URLSearchParams({
    secid,
    fields1: "f1,f2,f3,f4,f5,f6",
    fields2: "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    klt: "101",
    fqt: "1",
    beg,
    end: "20500101",
  });
  const payload = await fetchJson(`https://push2his.eastmoney.com/api/qt/stock/kline/get?${params.toString()}`);
  const rows = payload?.data?.klines || [];
  return rows.slice(-limit).map((line) => {
    const parts = String(line).split(",");
    return {
      date: parts[0],
      open: num(parts[1]),
      close: num(parts[2]),
      high: num(parts[3]),
      low: num(parts[4]),
      volume: num(parts[5]),
      amount: num(parts[6]),
      pct: num(parts[8]),
      turnover_rate: num(parts[10]),
    };
  });
}

async function usKline(symbol, limit) {
  const period1 = Math.floor(daysAgo(280).getTime() / 1000);
  const period2 = Math.floor((Date.now() + 24 * 60 * 60 * 1000) / 1000);
  const params = new URLSearchParams({
    period1: String(period1),
    period2: String(period2),
    interval: "1d",
    events: "history",
    includeAdjustedClose: "true",
  });
  const payload = await fetchJson(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?${params.toString()}`);
  const result = payload?.chart?.result?.[0];
  const timestamps = result?.timestamp || [];
  const quote = result?.indicators?.quote?.[0] || {};
  const adjclose = result?.indicators?.adjclose?.[0]?.adjclose || quote.close || [];
  const rows = timestamps
    .map((ts, index) => ({
      date: new Date(ts * 1000).toISOString().slice(0, 10),
      open: num(quote.open?.[index]),
      high: num(quote.high?.[index]),
      low: num(quote.low?.[index]),
      close: num(adjclose[index] ?? quote.close?.[index]),
      volume: num(quote.volume?.[index]),
    }))
    .filter((item) => Number.isFinite(item.close));
  for (let index = 1; index < rows.length; index += 1) {
    rows[index].pct = rows[index - 1].close ? ((rows[index].close / rows[index - 1].close) - 1) * 100 : null;
  }
  return rows.slice(-limit);
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: {
      "User-Agent": "Mozilla/5.0 Catalyst 9:00",
      Accept: "application/json,text/plain,*/*",
    },
  });
  if (!response.ok) {
    throw new Error(`upstream HTTP ${response.status}`);
  }
  return response.json();
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.round(n * 10000) / 10000 : null;
}

function daysAgo(days) {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000);
}

function yyyymmdd(date) {
  const y = date.getUTCFullYear();
  const m = String(date.getUTCMonth() + 1).padStart(2, "0");
  const d = String(date.getUTCDate()).padStart(2, "0");
  return `${y}${m}${d}`;
}

function clampInt(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, Math.round(value)));
}
