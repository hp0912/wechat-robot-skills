#!/usr/bin/env node
"use strict";

const fs = require("fs");
const fsp = require("fs/promises");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const DEFAULT_VIEWPORT_WIDTH = 1365;
const DEFAULT_VIEWPORT_HEIGHT = 900;
const DEFAULT_WAIT_MS = 1500;
const DEFAULT_TIMEOUT_MS = 45000;
const DEFAULT_MAX_CHARS = 16000;

function stdout(message) {
  process.stdout.write(`${message}\n`);
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      throw new Error(`存在不支持的参数: ${token}`);
    }

    const equalIndex = token.indexOf("=");
    let key = token.slice(2);
    let value;
    if (equalIndex >= 0) {
      key = token.slice(2, equalIndex);
      value = token.slice(equalIndex + 1);
    } else {
      const next = argv[index + 1];
      if (next === undefined || next.startsWith("--")) {
        value = "true";
      } else {
        value = next;
        index += 1;
      }
    }

    result[key] = value;
  }
  return result;
}

function parseNumber(value, name, fallback = undefined) {
  if (value === undefined || value === "") {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`${name} 必须是数字`);
  }
  return parsed;
}

function parseInteger(value, name, fallback) {
  const parsed = parseNumber(value, name, fallback);
  if (!Number.isInteger(parsed)) {
    throw new Error(`${name} 必须是整数`);
  }
  return parsed;
}

function validateUrl(value) {
  if (!value || !value.trim()) {
    throw new Error("缺少网页链接");
  }
  let parsed;
  try {
    parsed = new URL(value.trim());
  } catch (error) {
    throw new Error(`网页链接格式不正确: ${value}`);
  }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    throw new Error("网页链接必须是 http 或 https 地址");
  }
  return parsed.toString();
}

function normalizeParams(raw) {
  const url = validateUrl(raw.url || "");
  const mode = raw.mode || "content";
  if (!["content", "screenshot"].includes(mode)) {
    throw new Error("mode 只能是 content 或 screenshot");
  }

  const screenshotMode = raw.screenshot_mode || "full";
  if (!["full", "viewport", "selector", "region"].includes(screenshotMode)) {
    throw new Error(
      "screenshot_mode 只能是 full、viewport、selector 或 region",
    );
  }

  const viewportWidth = parseInteger(
    raw.viewport_width || raw.width,
    "width",
    DEFAULT_VIEWPORT_WIDTH,
  );
  const viewportHeight = parseInteger(
    raw.viewport_height || raw.height,
    "height",
    DEFAULT_VIEWPORT_HEIGHT,
  );
  const waitMs = parseInteger(raw.wait_ms, "wait_ms", DEFAULT_WAIT_MS);
  const timeoutMs = parseInteger(
    raw.timeout_ms,
    "timeout_ms",
    DEFAULT_TIMEOUT_MS,
  );
  const maxChars = parseInteger(raw.max_chars, "max_chars", DEFAULT_MAX_CHARS);
  const maxFullHeight = parseInteger(
    raw.max_full_height,
    "max_full_height",
    60000,
  );

  if (viewportWidth <= 0 || viewportHeight <= 0) {
    throw new Error("视口 width 和 height 必须大于 0");
  }
  if (waitMs < 0) {
    throw new Error("wait_ms 不能小于 0");
  }
  if (timeoutMs <= 0) {
    throw new Error("timeout_ms 必须大于 0");
  }
  if (maxChars <= 0) {
    throw new Error("max_chars 必须大于 0");
  }
  if (maxFullHeight <= 0) {
    throw new Error("max_full_height 必须大于 0");
  }

  const params = {
    url,
    mode,
    screenshotMode,
    selector: raw.selector || "",
    x: parseNumber(raw.x, "x"),
    y: parseNumber(raw.y, "y"),
    regionWidth: parseNumber(
      raw.region_width || (screenshotMode === "region" ? raw.width : undefined),
      "width",
    ),
    regionHeight: parseNumber(
      raw.region_height ||
        (screenshotMode === "region" ? raw.height : undefined),
      "height",
    ),
    viewportWidth,
    viewportHeight,
    waitMs,
    timeoutMs,
    maxChars,
    maxFullHeight,
    output: raw.output || "",
    send: raw.send || "auto",
  };

  if (!["auto", "true", "false"].includes(params.send)) {
    throw new Error("send 只能是 auto、true 或 false");
  }
  if (
    mode === "screenshot" &&
    screenshotMode === "selector" &&
    !params.selector.trim()
  ) {
    throw new Error("selector 截图模式必须传 selector");
  }
  if (mode === "screenshot" && screenshotMode === "region") {
    for (const key of ["x", "y", "regionWidth", "regionHeight"]) {
      if (params[key] === undefined) {
        throw new Error("region 截图模式必须传 x、y、width、height");
      }
    }
    if (params.regionWidth <= 0 || params.regionHeight <= 0) {
      throw new Error("region 截图区域 width 和 height 必须大于 0");
    }
  }

  return params;
}

function fileExists(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch (error) {
    return false;
  }
}

function findChromium() {
  const candidates = [
    process.env.CHROME_BIN,
    process.env.CHROME_PATH,
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fileExists(candidate)) {
      return candidate;
    }
  }
  throw new Error("未找到 Chromium，请设置 CHROME_BIN 或 CHROME_PATH");
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function withTimeout(promise, ms, message) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function launchChromium(params) {
  const chromeBin = findChromium();
  const userDataDir = await fsp.mkdtemp(
    path.join(os.tmpdir(), "web-page-chromium-"),
  );
  const args = [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=0",
    `--user-data-dir=${userDataDir}`,
    `--window-size=${params.viewportWidth},${params.viewportHeight}`,
    "about:blank",
  ];

  const chrome = spawn(chromeBin, args, { stdio: ["ignore", "pipe", "pipe"] });
  let logBuffer = "";
  const websocketUrlPromise = new Promise((resolve, reject) => {
    const onData = (chunk) => {
      logBuffer += chunk.toString("utf8");
      const match = logBuffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        resolve(match[1]);
      }
    };
    chrome.stdout.on("data", onData);
    chrome.stderr.on("data", onData);
    chrome.once("error", reject);
    chrome.once("exit", (code) => {
      reject(
        new Error(`Chromium 启动失败，退出码: ${code}; ${logBuffer.trim()}`),
      );
    });
  });

  const websocketUrl = await withTimeout(
    websocketUrlPromise,
    params.timeoutMs,
    "等待 Chromium DevTools 地址超时",
  );

  return { chrome, userDataDir, websocketUrl };
}

class CdpClient {
  constructor(websocketUrl) {
    this.websocketUrl = websocketUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = [];
    this.ws = null;
  }

  async connect(timeoutMs) {
    if (typeof WebSocket === "undefined") {
      throw new Error(
        "当前 Node.js 缺少 WebSocket 支持，请使用 Node.js 22+ 或基础镜像中的 Node.js 24+",
      );
    }

    this.ws = new WebSocket(this.websocketUrl);
    this.ws.addEventListener("message", (event) =>
      this.handleMessage(event.data),
    );

    await withTimeout(
      new Promise((resolve, reject) => {
        this.ws.addEventListener("open", resolve, { once: true });
        this.ws.addEventListener(
          "error",
          () => reject(new Error("连接 Chromium DevTools 失败")),
          { once: true },
        );
      }),
      timeoutMs,
      "连接 Chromium DevTools 超时",
    );
  }

  handleMessage(raw) {
    let rawText;
    if (typeof raw === "string") {
      rawText = raw;
    } else if (Buffer.isBuffer(raw)) {
      rawText = raw.toString("utf8");
    } else if (raw instanceof ArrayBuffer) {
      rawText = Buffer.from(raw).toString("utf8");
    } else {
      rawText = String(raw);
    }

    let message;
    try {
      message = JSON.parse(rawText);
    } catch (error) {
      return;
    }

    if (message.id && this.pending.has(message.id)) {
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) {
        reject(
          new Error(message.error.message || JSON.stringify(message.error)),
        );
      } else {
        resolve(message.result || {});
      }
      return;
    }

    if (message.method) {
      for (const listener of [...this.listeners]) {
        if (listener.method !== message.method) {
          continue;
        }
        if (listener.sessionId && listener.sessionId !== message.sessionId) {
          continue;
        }
        listener.resolve(message.params || {});
        this.listeners = this.listeners.filter((item) => item !== listener);
      }
    }
  }

  send(method, params = {}, sessionId = undefined) {
    const id = this.nextId;
    this.nextId += 1;
    const payload = { id, method, params };
    if (sessionId) {
      payload.sessionId = sessionId;
    }

    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
    });
  }

  waitForEvent(method, sessionId, timeoutMs) {
    return withTimeout(
      new Promise((resolve) => {
        this.listeners.push({ method, sessionId, resolve });
      }),
      timeoutMs,
      `等待事件 ${method} 超时`,
    );
  }

  close() {
    if (this.ws) {
      this.ws.close();
    }
  }
}

async function createPage(client, params) {
  const target = await client.send("Target.createTarget", {
    url: "about:blank",
  });
  const attached = await client.send("Target.attachToTarget", {
    targetId: target.targetId,
    flatten: true,
  });
  const sessionId = attached.sessionId;
  await client.send("Page.enable", {}, sessionId);
  await client.send("Runtime.enable", {}, sessionId);
  await client.send("Network.enable", {}, sessionId);
  await client.send(
    "Emulation.setDeviceMetricsOverride",
    {
      width: params.viewportWidth,
      height: params.viewportHeight,
      deviceScaleFactor: 1,
      mobile: false,
    },
    sessionId,
  );
  return sessionId;
}

async function navigate(client, sessionId, params) {
  const loadEvent = client
    .waitForEvent("Page.loadEventFired", sessionId, params.timeoutMs)
    .catch(() => null);
  await client.send("Page.navigate", { url: params.url }, sessionId);
  await loadEvent;
  if (params.waitMs > 0) {
    await wait(params.waitMs);
  }
}

async function evaluate(client, sessionId, expression) {
  const result = await client.send(
    "Runtime.evaluate",
    {
      expression,
      awaitPromise: true,
      returnByValue: true,
    },
    sessionId,
  );
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "页面脚本执行失败");
  }
  return result.result ? result.result.value : undefined;
}

function cleanText(text) {
  return String(text || "")
    .replace(/\u00a0/g, " ")
    .split("\n")
    .map((line) => line.trim().replace(/[ \t]+/g, " "))
    .filter((line, index, lines) => line || lines[index - 1])
    .join("\n")
    .trim();
}

async function extractContent(client, sessionId, params) {
  const data = await evaluate(
    client,
    sessionId,
    `(() => {
    const textOf = (node) => (node && node.innerText ? node.innerText.trim() : '');
    const meta = (selector) => {
      const el = document.querySelector(selector);
      return el ? (el.getAttribute('content') || '').trim() : '';
    };
    const candidates = Array.from(document.querySelectorAll('article, main, [role="main"], #content, .content, .article, .post, .entry-content'));
    let root = document.body;
    const bodyLength = textOf(document.body).length;
    let bestLength = 0;
    for (const candidate of candidates) {
      const length = textOf(candidate).length;
      if (length > bestLength) {
        root = candidate;
        bestLength = length;
      }
    }
    if (bestLength < bodyLength * 0.25) {
      root = document.body;
    }
    const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
      .map((el) => el.innerText.trim())
      .filter(Boolean)
      .slice(0, 30);
    const links = Array.from(document.querySelectorAll('a[href]'))
      .map((el) => ({ text: el.innerText.trim().replace(/\s+/g, ' '), href: el.href }))
      .filter((item) => item.text && item.href && /^https?:/.test(item.href))
      .slice(0, 40);
    return {
      title: document.title || '',
      url: location.href,
      lang: document.documentElement.lang || '',
      description: meta('meta[name="description"]') || meta('meta[property="og:description"]'),
      text: textOf(root) || textOf(document.body),
      headings,
      links,
    };
  })()`,
  );

  const text = cleanText(data.text || "");
  const truncated = text.length > params.maxChars;
  const body = truncated
    ? `${text.slice(0, params.maxChars)}\n\n[内容过长，已截断到 ${params.maxChars} 字]`
    : text;
  const headings = [...new Set(data.headings || [])].slice(0, 20);
  const links = [];
  const seenLinks = new Set();
  for (const link of data.links || []) {
    const key = `${link.text}\t${link.href}`;
    if (seenLinks.has(key)) {
      continue;
    }
    seenLinks.add(key);
    links.push(link);
    if (links.length >= 20) {
      break;
    }
  }

  const parts = [
    `标题：${data.title || "(无标题)"}`,
    `URL：${data.url || params.url}`,
  ];
  if (data.lang) {
    parts.push(`语言：${data.lang}`);
  }
  if (data.description) {
    parts.push(`页面描述：${cleanText(data.description)}`);
  }
  if (headings.length) {
    parts.push(
      `主要标题：\n${headings.map((heading) => `- ${heading}`).join("\n")}`,
    );
  }
  parts.push(`正文：\n${body || "(未抽取到可见正文)"}`);
  if (links.length) {
    parts.push(
      `主要链接：\n${links.map((link) => `- ${link.text}: ${link.href}`).join("\n")}`,
    );
  }
  return parts.join("\n\n");
}

async function getLayoutMetrics(client, sessionId) {
  const metrics = await client.send("Page.getLayoutMetrics", {}, sessionId);
  return (
    metrics.cssContentSize ||
    metrics.contentSize || { x: 0, y: 0, width: 0, height: 0 }
  );
}

async function getSelectorClip(client, sessionId, selector) {
  const encodedSelector = JSON.stringify(selector);
  const rect = await evaluate(
    client,
    sessionId,
    `(async () => {
    const el = document.querySelector(${encodedSelector});
    if (!el) return null;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const r = el.getBoundingClientRect();
    return { x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height };
  })()`,
  );
  if (!rect) {
    throw new Error(`未找到 selector 对应元素: ${selector}`);
  }
  if (rect.width <= 0 || rect.height <= 0) {
    throw new Error(`selector 对应元素尺寸为空: ${selector}`);
  }
  return rect;
}

async function getScreenshotClip(client, sessionId, params) {
  if (params.screenshotMode === "region") {
    return {
      x: params.x,
      y: params.y,
      width: params.regionWidth,
      height: params.regionHeight,
      scale: 1,
    };
  }
  if (params.screenshotMode === "selector") {
    const rect = await getSelectorClip(client, sessionId, params.selector);
    return { ...rect, scale: 1 };
  }
  if (params.screenshotMode === "viewport") {
    return {
      x: 0,
      y: 0,
      width: params.viewportWidth,
      height: params.viewportHeight,
      scale: 1,
    };
  }

  const contentSize = await getLayoutMetrics(client, sessionId);
  const width = Math.max(
    1,
    Math.ceil(contentSize.width || params.viewportWidth),
  );
  const rawHeight = Math.max(
    1,
    Math.ceil(contentSize.height || params.viewportHeight),
  );
  const height = Math.min(rawHeight, params.maxFullHeight);
  return {
    x: 0,
    y: 0,
    width,
    height,
    scale: 1,
    truncated: rawHeight > height,
    rawHeight,
  };
}

async function captureScreenshot(client, sessionId, params) {
  const clip = await getScreenshotClip(client, sessionId, params);
  const result = await client.send(
    "Page.captureScreenshot",
    {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: true,
      clip: {
        x: Math.max(0, clip.x),
        y: Math.max(0, clip.y),
        width: Math.max(1, clip.width),
        height: Math.max(1, clip.height),
        scale: 1,
      },
    },
    sessionId,
  );
  if (!result.data) {
    throw new Error("Chromium 未返回截图数据");
  }
  const output =
    params.output ||
    path.join(os.tmpdir(), `web-page-screenshot-${Date.now()}.png`);
  await fsp.mkdir(path.dirname(output), { recursive: true });
  await fsp.writeFile(output, Buffer.from(result.data, "base64"));
  return { output, clip };
}

function postJson(url, body, timeoutMs) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const payload = Buffer.from(JSON.stringify(body), "utf8");
    const request = http.request(
      {
        hostname: parsed.hostname,
        port: parsed.port,
        path: parsed.pathname + parsed.search,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": payload.length,
        },
        timeout: timeoutMs,
      },
      (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          if (response.statusCode < 200 || response.statusCode >= 300) {
            reject(new Error(`HTTP ${response.statusCode}: ${text}`));
            return;
          }
          resolve(text);
        });
      },
    );
    request.on("error", reject);
    request.on("timeout", () =>
      request.destroy(new Error("请求机器人客户端超时")),
    );
    request.write(payload);
    request.end();
  });
}

async function maybeSendScreenshot(params, filePath) {
  if (params.send === "false") {
    return false;
  }
  const clientPort = (process.env.ROBOT_WECHAT_CLIENT_PORT || "").trim();
  const toWxid = (process.env.ROBOT_FROM_WX_ID || "").trim();
  if (!clientPort || !toWxid) {
    if (params.send === "true") {
      throw new Error(
        "环境变量 ROBOT_WECHAT_CLIENT_PORT 或 ROBOT_FROM_WX_ID 未配置",
      );
    }
    return false;
  }
  const sendUrl = `http://127.0.0.1:${clientPort}/api/v1/robot/message/send/image/local`;
  await postJson(
    sendUrl,
    { to_wxid: toWxid, file_path: filePath },
    params.timeoutMs,
  );
  return true;
}

async function run() {
  const params = normalizeParams(parseArgs(process.argv.slice(2)));
  const browser = await launchChromium(params);
  const client = new CdpClient(browser.websocketUrl);
  try {
    await client.connect(params.timeoutMs);
    const sessionId = await createPage(client, params);
    await navigate(client, sessionId, params);

    if (params.mode === "content") {
      stdout(await extractContent(client, sessionId, params));
      return;
    }

    const screenshot = await captureScreenshot(client, sessionId, params);
    const sent = await maybeSendScreenshot(params, screenshot.output);
    if (sent) {
      const suffix = screenshot.clip.truncated
        ? `，页面过长，已截取前 ${Math.round(screenshot.clip.height)}px / ${Math.round(screenshot.clip.rawHeight)}px`
        : "";
      stdout(`页面截图已发送${suffix}`);
    } else {
      const suffix = screenshot.clip.truncated
        ? `（页面过长，已截取前 ${Math.round(screenshot.clip.height)}px / ${Math.round(screenshot.clip.rawHeight)}px）`
        : "";
      stdout(`页面截图已保存: ${screenshot.output}${suffix}`);
    }
  } finally {
    client.close();
    browser.chrome.kill("SIGKILL");
    await fsp.rm(browser.userDataDir, { recursive: true, force: true });
  }
}

run().catch((error) => {
  stdout(`执行失败: ${error && error.stack ? error.stack : error}`);
  process.exit(1);
});
