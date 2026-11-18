const HEADERS = ["Команда", "Имя", "Фамилия", "Username", "Telegram ID"];
const SQUAD_COUNT = 10;
const CAPACITY_PER_SQUAD = 10;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function htmlPage(message = "") {
  return new Response(`<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Настройка бота отрядов</title><body style="font:16px system-ui;max-width:480px;margin:60px auto;padding:20px"><h2>Подключение Telegram-бота</h2><p>${message}</p><form method="post" action="/setup"><label>Пароль настройки</label><br><input name="password" type="password" required style="padding:12px;width:100%;box-sizing:border-box;margin:12px 0"><button style="padding:12px 18px">Подключить бота</button></form></body></html>`, {
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function base64url(input) {
  const bytes = typeof input === "string" ? new TextEncoder().encode(input) : input;
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function pemToBytes(pem) {
  const body = pem.replace(/-----BEGIN PRIVATE KEY-----/g, "").replace(/-----END PRIVATE KEY-----/g, "").replace(/\s/g, "");
  const binary = atob(body);
  return Uint8Array.from(binary, c => c.charCodeAt(0));
}

let cachedGoogleToken = null;
let cachedGoogleTokenUntil = 0;

async function googleAccessToken(env) {
  if (cachedGoogleToken && Date.now() < cachedGoogleTokenUntil) return cachedGoogleToken;
  const service = JSON.parse(env.GOOGLE_SERVICE_ACCOUNT_JSON);
  const now = Math.floor(Date.now() / 1000);
  const header = base64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claim = base64url(JSON.stringify({
    iss: service.client_email,
    scope: "https://www.googleapis.com/auth/spreadsheets",
    aud: "https://oauth2.googleapis.com/token",
    iat: now,
    exp: now + 3600,
  }));
  const unsigned = header + "." + claim;
  const key = await crypto.subtle.importKey(
    "pkcs8",
    pemToBytes(service.private_key),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(unsigned));
  const assertion = unsigned + "." + base64url(new Uint8Array(signature));
  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion,
    }),
  });
  const data = await response.json();
  if (!response.ok || !data.access_token) throw new Error("Google auth failed: " + JSON.stringify(data));
  cachedGoogleToken = data.access_token;
  cachedGoogleTokenUntil = Date.now() + Math.max(60, data.expires_in - 120) * 1000;
  return cachedGoogleToken;
}

async function sheetsRequest(env, range, method = "GET", body = null) {
  const token = await googleAccessToken(env);
  const url = "https://sheets.googleapis.com/v4/spreadsheets/" +
    encodeURIComponent(env.GOOGLE_SHEET_ID || "1o5tdrhdUfkDuugVi8FqnbNp_0eyzsUNIaOeE2nbiLeg") +
    "/values/" + encodeURIComponent(range);
  const response = await fetch(method === "POST" ? url + ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS" : url, {
    method,
    headers: {
      authorization: "Bearer " + token,
      "content-type": "application/json",
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error("Google Sheets API error: " + JSON.stringify(data));
  return data;
}

async function readRecords(env) {
  const data = await sheetsRequest(env, "A:E");
  const values = data.values || [];
  if (!values.length) {
    await sheetsRequest(env, "A1:E1", "PUT", { values: [HEADERS] });
    return [];
  }
  if (values[0].slice(0, 5).join("|") !== HEADERS.join("|")) {
    throw new Error("Google Sheet header mismatch. Expected A:E: " + HEADERS.join(", "));
  }
  return values.slice(1).map((row, i) => {
    const r = [...row, "", "", "", "", ""].slice(0, 5);
    return {
      row: i + 2,
      squad: String(r[0] || "").trim(),
      first_name: String(r[1] || "").trim(),
      last_name: String(r[2] || "").trim(),
      username: String(r[3] || "").trim(),
      telegram_id: String(r[4] || "").trim(),
    };
  }).filter(r => [r.squad, r.first_name, r.last_name, r.username, r.telegram_id].some(Boolean));
}

async function appendRecord(env, user, squadName) {
  await sheetsRequest(env, "A:E", "POST", {
    majorDimension: "ROWS",
    values: [[squadName, user.first_name || "", user.last_name || "", user.username || "", String(user.id)]],
  });
}

async function clearRecords(env) {
  const token = await googleAccessToken(env);
  const sheetId = env.GOOGLE_SHEET_ID || "1o5tdrhdUfkDuugVi8FqnbNp_0eyzsUNIaOeE2nbiLeg";
  const response = await fetch("https://sheets.googleapis.com/v4/spreadsheets/" + encodeURIComponent(sheetId) + "/values/A2:E:clear", {
    method: "POST",
    headers: { authorization: "Bearer " + token, "content-type": "application/json" },
    body: "{}",
  });
  const data = await response.json();
  if (!response.ok) throw new Error("Google Sheets clear error: " + JSON.stringify(data));
}

function getSquads(env) {
  return Array.from({ length: SQUAD_COUNT }, (_, i) => {
    const n = i + 1;
    return {
      number: n,
      name: env["SQUAD_" + n + "_NAME"] || "Отряд " + n,
      invite_link: (env["SQUAD_" + n + "_LINK"] || "").trim(),
    };
  });
}

function getCounts(records, squads) {
  const counts = Object.fromEntries(squads.map(s => [s.name, 0]));
  for (const r of records) if (Object.hasOwn(counts, r.squad)) counts[r.squad]++;
  return counts;
}
function availableSquads(records, squads) {
  const counts = getCounts(records, squads);
  return squads.filter(s => s.invite_link && counts[s.name] < CAPACITY_PER_SQUAD);
}
function esc(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function isAdmin(user, env) {
  return !!env.ADMIN_TELEGRAM_ID && String(user.id) === String(env.ADMIN_TELEGRAM_ID);
}

async function telegram(env, method, payload = {}) {
  const response = await fetch("https://api.telegram.org/bot" + env.BOT_TOKEN + "/" + method, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!data.ok) throw new Error("Telegram " + method + " failed: " + JSON.stringify(data));
  return data.result;
}
async function answer(env, chatId, text, extra = {}) {
  return telegram(env, "sendMessage", { chat_id: chatId, text, ...extra });
}
async function edit(env, chatId, messageId, text, extra = {}) {
  return telegram(env, "editMessageText", { chat_id: chatId, message_id: messageId, text, ...extra });
}
async function callbackAnswer(env, callbackId) {
  return telegram(env, "answerCallbackQuery", { callback_query_id: callbackId });
}
function startKeyboard() {
  return { inline_keyboard: [[
    { text: "Да", callback_data: "choice:yes" },
    { text: "Нет", callback_data: "choice:no" },
  ]] };
}
function squadKeyboard(squads, counts) {
  const rows = squads.map(s => [{
    text: `🏕 ${s.name} · ${counts[s.name]}/${CAPACITY_PER_SQUAD}`,
    callback_data: "choose:" + s.number,
  }]);
  rows.push([{ text: "⬅️ Назад", callback_data: "choice:back" }]);
  return { inline_keyboard: rows };
}
async function sendAssignment(env, chatId, squad, already = false) {
  const linkLine = squad.invite_link
    ? `🔗 <a href="${esc(squad.invite_link)}">Войти в чат отряда</a>`
    : "⚠️ Ссылка на чат пока не настроена. Обратись к организатору.";
  const heading = already ? "✅ Ты уже закреплён за своим отрядом:" : "🎉 Твой отряд:";
  await answer(env, chatId,
    `${heading}\n\n🏕 <b>${esc(squad.name)}</b>\n\n${linkLine}\n\nМесто закреплено за тобой. Повторное распределение не изменит отряд.`,
    { parse_mode: "HTML", disable_web_page_preview: true });
}
async function showExisting(env, user, chatId, records, squads) {
  const record = records.find(r => r.telegram_id === String(user.id));
  if (!record) return false;
  const squad = squads.find(s => s.name === record.squad);
  if (!squad) {
    await answer(env, chatId, "Я нашёл твою запись в таблице, но не смог определить отряд. Обратись к организатору.");
    return true;
  }
  await sendAssignment(env, chatId, squad, true);
  return true;
}
async function assignSquad(env, user, chatId, squadNumber, records, squads) {
  const existing = records.find(r => r.telegram_id === String(user.id));
  if (existing) {
    const squad = squads.find(s => s.name === existing.squad);
    if (squad) await sendAssignment(env, chatId, squad, true);
    else await answer(env, chatId, "Твоя запись найдена, но отряд не распознан. Обратись к организатору.");
    return;
  }
  const available = availableSquads(records, squads);
  let selected;
  if (squadNumber !== null && squadNumber !== undefined) {
    selected = squads.find(s => s.number === squadNumber);
    if (!selected) { await answer(env, chatId, "Не удалось определить отряд. Нажми /start."); return; }
    if (!available.some(s => s.number === selected.number)) {
      await answer(env, chatId, "Этот отряд уже заполнен или ссылка не настроена. Выбери другой.");
      return;
    }
  } else {
    if (!available.length) {
      await answer(env, chatId, "😔 Свободных мест больше нет. Все 10 отрядов заполнены.");
      return;
    }
    selected = available[Math.floor(Math.random() * available.length)];
  }
  await appendRecord(env, user, selected.name);
  await sendAssignment(env, chatId, selected);
}

async function handleUpdate(env, update) {
  const squads = getSquads(env);
  if (update.callback_query) {
    const cb = update.callback_query;
    const user = cb.from;
    const chatId = cb.message.chat.id;
    const messageId = cb.message.message_id;
    await callbackAnswer(env, cb.id);
    const records = await readRecords(env);
    if (cb.data === "choice:yes") {
      if (await showExisting(env, user, chatId, records, squads)) return;
      const available = availableSquads(records, squads);
      if (!available.length) {
        await edit(env, chatId, messageId, "😔 Свободных мест больше нет. Все 10 отрядов заполнены.");
        return;
      }
      await edit(env, chatId, messageId,
        "Выбери отряд. Здесь отображаются только те, где ещё есть места:",
        { reply_markup: squadKeyboard(available, getCounts(records, squads)) });
      return;
    }
    if (cb.data === "choice:no") {
      await assignSquad(env, user, chatId, null, records, squads);
      return;
    }
    if (cb.data === "choice:back") {
      if (await showExisting(env, user, chatId, records, squads)) return;
      await edit(env, chatId, messageId, "Ты хочешь попасть в определённый отряд?", { reply_markup: startKeyboard() });
      return;
    }
    if (cb.data.startsWith("choose:")) {
      const number = Number(cb.data.split(":")[1]);
      if (!Number.isInteger(number)) {
        await answer(env, chatId, "Не удалось определить отряд. Нажми /start.");
        return;
      }
      await assignSquad(env, user, chatId, number, records, squads);
    }
    return;
  }

  const message = update.message;
  if (!message || !message.from || !message.chat) return;
  const user = message.from;
  const chatId = message.chat.id;
  const text = message.text || "";
  const records = await readRecords(env);

  if (text.startsWith("/start") || text.startsWith("/team")) {
    if (await showExisting(env, user, chatId, records, squads)) return;
    await answer(env, chatId, text.startsWith("/start")
      ? "Привет! 👋\n\nТы хочешь попасть в определённый отряд?"
      : "Ты хочешь попасть в определённый отряд?",
      { reply_markup: startKeyboard() });
    return;
  }
  if (text.startsWith("/admin")) {
    if (!isAdmin(user, env)) { await answer(env, chatId, "⛔ У тебя нет доступа к этой команде."); return; }
    const counts = getCounts(records, squads);
    const lines = ["📊 <b>Статистика отрядов</b>", ""];
    for (const squad of squads) {
      const count = counts[squad.name];
      lines.push(`${count >= CAPACITY_PER_SQUAD ? "🔒" : "🟢"} ${esc(squad.name)}: <b>${count}/${CAPACITY_PER_SQUAD}</b>`);
    }
    lines.push("", `👥 Всего участников: <b>${records.length}/${SQUAD_COUNT * CAPACITY_PER_SQUAD}</b>`);
    await answer(env, chatId, lines.join("\n"), { parse_mode: "HTML" });
    return;
  }
  if (text.startsWith("/reset")) {
    if (!isAdmin(user, env)) { await answer(env, chatId, "⛔ У тебя нет доступа к этой команде."); return; }
    await clearRecords(env);
    await answer(env, chatId, "♻️ Распределение очищено. Все 100 мест снова доступны. Заголовки таблицы и настройки отрядов сохранены.");
    return;
  }
  if (text.startsWith("/help")) {
    let help = "ℹ️ <b>Помощь</b>\n\n/start — начать распределение\n/team — показать свой отряд\n";
    if (isAdmin(user, env)) help += "\n<b>Команды администратора:</b>\n/admin — статистика отрядов\n/reset — очистить распределение\n";
    await answer(env, chatId, help, { parse_mode: "HTML" });
    return;
  }
  if (await showExisting(env, user, chatId, records, squads)) return;
  await answer(env, chatId, "Нажми /start, чтобы выбрать отряд или получить случайный.", { reply_markup: startKeyboard() });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") {
      return new Response("Otryadi Telegram bot is running.");
    }
    if (request.method === "GET" && url.pathname === "/setup") {
      return htmlPage("Введи пароль настройки, чтобы установить Telegram webhook.");
    }
    if (request.method === "POST" && url.pathname === "/setup") {
      let password = "";
      const type = request.headers.get("content-type") || "";
      if (type.includes("application/json")) password = (await request.json()).password || "";
      else password = (await request.formData()).get("password") || "";
      if (!env.SETUP_PASSWORD || password !== env.SETUP_PASSWORD) return htmlPage("Неверный пароль. Попробуй ещё раз.");
      if (!env.BOT_TOKEN || !env.WEBHOOK_SECRET || !env.GOOGLE_SERVICE_ACCOUNT_JSON) {
        return new Response("Не заданы BOT_TOKEN, WEBHOOK_SECRET или GOOGLE_SERVICE_ACCOUNT_JSON.", { status: 500 });
      }
      const hookUrl = url.origin + "/telegram";
      const result = await telegram(env, "setWebhook", {
        url: hookUrl,
        secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message", "callback_query"],
        drop_pending_updates: false,
      });
      return new Response("Webhook установлен. Telegram bot is connected.\n" + JSON.stringify(result), {
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
    if (request.method === "POST" && url.pathname === "/telegram") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
        return new Response("Unauthorized", { status: 401 });
      }
      try {
        const update = await request.json();
        await env.OTRYADI_SERIALIZER.get(env.OTRYADI_SERIALIZER.idFromName("global")).fetch("https://serializer/process", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(update),
        });
        return new Response("OK");
      } catch (error) {
        console.error("Webhook error:", error);
        return new Response("Internal error", { status: 500 });
      }
    }
    return new Response("Not found", { status: 404 });
  },
};

export class OtryadiSerializer {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.queue = Promise.resolve();
  }
  async fetch(request) {
    const task = this.queue.then(async () => {
      const update = await request.json();
      await handleUpdate(this.env, update);
      return new Response("OK");
    });
    this.queue = task.catch(error => {
      console.error("Update processing failed:", error);
    });
    return task;
  }
}
