const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
};

export default async (req) => {
  if (req.method !== "GET") {
    return json({ ok: false, error: "Method not allowed" }, 405);
  }
  const url = new URL(req.url);
  const kind = (url.searchParams.get("kind") || "a").toLowerCase();
  const symbols = (url.searchParams.get("symbols") || "")
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean)
    .slice(0, 80);

  if (kind !== "a") {
    return json({ ok: false, error: "Only A-share live quotes are supported" }, 400);
  }
  if (!symbols.length || symbols.some((symbol) => !/^\d{6}$/.test(symbol))) {
    return json({ ok: false, error: "Invalid symbols" }, 400);
  }

  try {
    const quotes = await sinaAQuotes(symbols);
    return json({ ok: true, kind, source: "Sina realtime", quotes });
  } catch (error) {
    return json({ ok: false, error: error instanceof Error ? error.message : String(error) }, 502);
  }
};

export const config = {
  path: "/api/live-quotes",
  method: ["GET"],
};

async function sinaAQuotes(symbols) {
  const query = symbols.map((symbol) => `${isShanghai(symbol) ? "sh" : "sz"}${symbol}`).join(",");
  const response = await fetch(`https://hq.sinajs.cn/list=${query}`, {
    headers: {
      "User-Agent": "Mozilla/5.0 Catalyst 9:00",
      Referer: "https://finance.sina.com.cn",
      Accept: "*/*",
    },
  });
  if (!response.ok) {
    throw new Error(`upstream HTTP ${response.status}`);
  }
  const buffer = await response.arrayBuffer();
  const text = new TextDecoder("gb18030").decode(buffer);
  const quotes = [];
  const pattern = /var hq_str_(sh|sz)(\d{6})="([^"]*)";/g;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    const parts = match[3].split(",");
    if (parts.length < 32 || !parts[0]) continue;
    const prevClose = num(parts[2]);
    const price = num(parts[3]);
    quotes.push({
      symbol: match[2],
      name: parts[0],
      open: num(parts[1]),
      prev_close: prevClose,
      price,
      pct: price != null && prevClose ? round(((price / prevClose) - 1) * 100, 4) : null,
      high: num(parts[4]),
      low: num(parts[5]),
      volume: num(parts[8]),
      amount: num(parts[9]),
      date: parts[30] || "",
      time: parts[31] || "",
      source: "新浪实时",
    });
  }
  return quotes;
}

function isShanghai(symbol) {
  return ["5", "6", "9"].includes(String(symbol)[0]);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function round(value, digits) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
