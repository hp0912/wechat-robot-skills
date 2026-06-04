#!/usr/bin/env tsx
"use strict";

import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn, type ChildProcess } from "node:child_process";
import type { IncomingMessage } from "node:http";

const DEFAULT_VIEWPORT_WIDTH = 1365;
const DEFAULT_VIEWPORT_HEIGHT = 900;
const DEFAULT_WAIT_MS = 1500;
const DEFAULT_TIMEOUT_MS = 45000;
const DEFAULT_MAX_CHARS = 16000;
const DEFAULT_ACTION_TIMEOUT_MS = 15000;
const DEFAULT_ACTION_WAIT_MS = 300;
const DEFAULT_TIMEZONE = "Asia/Shanghai";

const ACTION_TYPES = new Set<string>([
  "click",
  "fill",
  "type",
  "press",
  "select",
  "check",
  "uncheck",
  "remove",
  "wait",
  "wait_for_selector",
  "scroll",
  "scroll_to",
]);

type ActionType =
  | "click"
  | "fill"
  | "type"
  | "press"
  | "select"
  | "check"
  | "uncheck"
  | "remove"
  | "wait"
  | "wait_for_selector"
  | "scroll"
  | "scroll_to";

type RawArgs = Record<string, string>;

interface NormalizedAction {
  type: ActionType;
  selector: string;
  text: string;
  value: string;
  key: string;
  exact: boolean;
  index: number;
  timeoutMs: number;
  waitMsAfter: number;
  waitForNavigation: boolean;
  state?: string;
  click_count?: unknown;
  ms?: number;
  x?: number;
  y?: number;
}

interface Params {
  url: string;
  mode: "content" | "screenshot";
  screenshotMode: "full" | "viewport" | "selector" | "region";
  selector: string;
  x: number | undefined;
  y: number | undefined;
  regionWidth: number | undefined;
  regionHeight: number | undefined;
  viewportWidth: number;
  viewportHeight: number;
  waitMs: number;
  timeoutMs: number;
  maxChars: number;
  maxFullHeight: number;
  actionTimeoutMs: number;
  actions: NormalizedAction[];
  output: string;
  send: "auto" | "true" | "false";
}

interface ChromiumBrowser {
  chrome: ChildProcess;
  userDataDir: string;
  websocketUrl: string;
}

interface ElementPoint {
  x: number;
  y: number;
  description: string;
}

interface ExtractedLink {
  text: string;
  href: string;
}

interface ExtractedContent {
  title: string;
  url: string;
  lang: string;
  description: string;
  text: string;
  headings: string[];
  links: ExtractedLink[];
}

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface LayoutContentSize extends Rect {}

interface ScreenshotClip extends Rect {
  scale: number;
  truncated?: boolean;
  rawHeight?: number;
}

interface SelectorState {
  attached: boolean;
  visible: boolean;
}

function stdout(message: string): void {
  process.stdout.write(`${message}\n`);
}

function parseArgs(argv: string[]): RawArgs {
  const result: RawArgs = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      throw new Error(`存在不支持的参数: ${token}`);
    }

    const equalIndex = token.indexOf("=");
    let key = token.slice(2);
    let value: string;
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

function parseNumber(
  value: unknown,
  name: string,
  fallback?: number,
): number | undefined {
  if (value === undefined || value === "") {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`${name} 必须是数字`);
  }
  return parsed;
}

function parseInteger(value: unknown, name: string, fallback?: number): number {
  const parsed = parseNumber(value, name, fallback);
  if (parsed === undefined || !Number.isInteger(parsed)) {
    throw new Error(`${name} 必须是整数`);
  }
  return parsed;
}

function readActionsFile(filePath: string | undefined): string {
  if (!filePath || !filePath.trim()) {
    return "";
  }
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch (error) {
    throw new Error(`读取 actions_file 失败: ${errorMessage(error)}`);
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseJsonActions(
  rawActions: string | undefined,
  rawActionsFile: string | undefined,
): unknown[] {
  const source =
    rawActions !== undefined ? rawActions : readActionsFile(rawActionsFile);
  if (source === undefined || source === "") {
    return [];
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch (error) {
    throw new Error(`actions 必须是合法 JSON: ${errorMessage(error)}`);
  }
  if (isPlainObject(parsed) && Array.isArray(parsed.actions)) {
    parsed = parsed.actions;
  }
  if (!Array.isArray(parsed)) {
    throw new Error("actions 必须是动作数组，或包含 actions 数组的对象");
  }
  return parsed;
}

function normalizeAction(
  rawAction: unknown,
  index: number,
  defaultActionTimeoutMs: number,
): NormalizedAction {
  if (!isPlainObject(rawAction)) {
    throw new Error(`第 ${index + 1} 个 action 必须是对象`);
  }

  const type = String(rawAction.type ?? "").trim();
  if (!ACTION_TYPES.has(type)) {
    throw new Error(`第 ${index + 1} 个 action.type 不支持: ${type || "(空)"}`);
  }

  const action: NormalizedAction = {
    type: type as ActionType,
    selector:
      rawAction.selector === undefined ? "" : String(rawAction.selector),
    text: rawAction.text === undefined ? "" : String(rawAction.text),
    value: rawAction.value === undefined ? "" : String(rawAction.value),
    key: rawAction.key === undefined ? "" : String(rawAction.key),
    exact: Boolean(rawAction.exact),
    index: parseInteger(rawAction.index, `actions[${index}].index`, 0),
    timeoutMs: parseInteger(
      rawAction.timeout_ms,
      `actions[${index}].timeout_ms`,
      defaultActionTimeoutMs,
    ),
    waitMsAfter: parseInteger(
      rawAction.wait_ms_after,
      `actions[${index}].wait_ms_after`,
      DEFAULT_ACTION_WAIT_MS,
    ),
    waitForNavigation: Boolean(rawAction.wait_for_navigation),
    state: rawAction.state === undefined ? undefined : String(rawAction.state),
    click_count: rawAction.click_count,
  };

  if (action.index < 0) {
    throw new Error(`actions[${index}].index 不能小于 0`);
  }
  if (action.timeoutMs <= 0) {
    throw new Error(`actions[${index}].timeout_ms 必须大于 0`);
  }
  if (action.waitMsAfter < 0) {
    throw new Error(`actions[${index}].wait_ms_after 不能小于 0`);
  }

  if (type === "click" && !action.selector && !action.text) {
    throw new Error(
      `第 ${index + 1} 个 click action 必须提供 selector 或 text`,
    );
  }
  if (
    ["fill", "select", "check", "uncheck"].includes(type) &&
    !action.selector
  ) {
    throw new Error(`第 ${index + 1} 个 ${type} action 必须提供 selector`);
  }
  if (type === "type" && !action.text) {
    throw new Error(`第 ${index + 1} 个 type action 必须提供 text`);
  }
  if (type === "press" && !action.key) {
    throw new Error(`第 ${index + 1} 个 press action 必须提供 key`);
  }
  if (type === "remove" && !action.selector && !action.text) {
    throw new Error(
      `第 ${index + 1} 个 remove action 必须提供 selector 或 text`,
    );
  }
  if (type === "wait_for_selector" && !action.selector) {
    throw new Error("wait_for_selector action 必须提供 selector");
  }
  if (type === "wait") {
    action.ms = parseInteger(rawAction.ms, `actions[${index}].ms`, undefined);
    if (action.ms === undefined || action.ms < 0) {
      throw new Error("wait action 必须提供大于等于 0 的 ms");
    }
  }
  if (type === "scroll") {
    action.x = parseNumber(rawAction.x, `actions[${index}].x`, 0);
    action.y = parseNumber(rawAction.y, `actions[${index}].y`, 600);
  }
  if (type === "scroll_to") {
    action.x = parseNumber(rawAction.x, `actions[${index}].x`, undefined);
    action.y = parseNumber(rawAction.y, `actions[${index}].y`, undefined);
  }

  return action;
}

function parseActions(
  raw: RawArgs,
  actionTimeoutMs: number,
): NormalizedAction[] {
  return parseJsonActions(raw.actions, raw.actions_file).map((action, index) =>
    normalizeAction(action, index, actionTimeoutMs),
  );
}

function validateUrl(value: string): string {
  if (!value || !value.trim()) {
    throw new Error("缺少网页链接");
  }
  let parsed: URL;
  try {
    parsed = new URL(value.trim());
  } catch {
    throw new Error(`网页链接格式不正确: ${value}`);
  }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    throw new Error("网页链接必须是 http 或 https 地址");
  }
  return parsed.toString();
}

function normalizeParams(raw: RawArgs): Params {
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
  const actionTimeoutMs = parseInteger(
    raw.action_timeout_ms,
    "action_timeout_ms",
    DEFAULT_ACTION_TIMEOUT_MS,
  );
  const actions = parseActions(raw, actionTimeoutMs);

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
  if (actionTimeoutMs <= 0) {
    throw new Error("action_timeout_ms 必须大于 0");
  }

  const send = raw.send || "auto";
  if (!["auto", "true", "false"].includes(send)) {
    throw new Error("send 只能是 auto、true 或 false");
  }

  const params: Params = {
    url,
    mode: mode as Params["mode"],
    screenshotMode: screenshotMode as Params["screenshotMode"],
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
    actionTimeoutMs,
    actions,
    output: raw.output || "",
    send: send as Params["send"],
  };

  if (
    mode === "screenshot" &&
    screenshotMode === "selector" &&
    !params.selector.trim()
  ) {
    throw new Error("selector 截图模式必须传 selector");
  }
  if (mode === "screenshot" && screenshotMode === "region") {
    if (
      params.x === undefined ||
      params.y === undefined ||
      params.regionWidth === undefined ||
      params.regionHeight === undefined
    ) {
      throw new Error("region 截图模式必须传 x、y、width、height");
    }
    if (params.regionWidth <= 0 || params.regionHeight <= 0) {
      throw new Error("region 截图区域 width 和 height 必须大于 0");
    }
  }

  return params;
}

function fileExists(filePath: string): boolean {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function findChromium(): string {
  const candidates = [
    process.env.CHROME_BIN,
    process.env.CHROME_PATH,
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ].filter((candidate): candidate is string => Boolean(candidate));

  for (const candidate of candidates) {
    if (fileExists(candidate)) {
      return candidate;
    }
  }
  throw new Error("未找到 Chromium，请设置 CHROME_BIN 或 CHROME_PATH");
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function withTimeout<T>(
  promise: Promise<T>,
  ms: number,
  message: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function waitForProcessExit(
  child: ChildProcess,
  timeoutMs: number,
): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve();
  }
  return withTimeout(
    new Promise<void>((resolve) => child.once("exit", () => resolve())),
    timeoutMs,
    "等待 Chromium 退出超时",
  );
}

async function closeChromium(
  client: CdpClient,
  browser: ChromiumBrowser,
): Promise<void> {
  try {
    await withTimeout(client.send("Browser.close"), 1500, "关闭 Chromium 超时");
  } catch {
    if (
      browser.chrome.exitCode === null &&
      browser.chrome.signalCode === null
    ) {
      browser.chrome.kill("SIGTERM");
    }
  }

  try {
    await waitForProcessExit(browser.chrome, 3000);
  } catch {
    if (
      browser.chrome.exitCode === null &&
      browser.chrome.signalCode === null
    ) {
      browser.chrome.kill("SIGKILL");
    }
    await waitForProcessExit(browser.chrome, 3000).catch(() => null);
  } finally {
    client.close();
  }
}

async function cleanupUserDataDir(userDataDir: string): Promise<void> {
  try {
    await fsp.rm(userDataDir, {
      recursive: true,
      force: true,
      maxRetries: 8,
      retryDelay: 200,
    });
  } catch (error) {
    stdout(`清理 Chromium 临时目录失败，已忽略: ${errorMessage(error)}`);
  }
}

async function launchChromium(params: Params): Promise<ChromiumBrowser> {
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
  const childStdout = chrome.stdout;
  const childStderr = chrome.stderr;
  if (!childStdout || !childStderr) {
    throw new Error("无法获取 Chromium 输出流");
  }

  let logBuffer = "";
  const websocketUrlPromise = new Promise<string>((resolve, reject) => {
    const onData = (chunk: Buffer): void => {
      logBuffer += chunk.toString("utf8");
      const match = logBuffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        resolve(match[1]);
      }
    };
    childStdout.on("data", onData);
    childStderr.on("data", onData);
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

interface PendingRequest {
  resolve: (result: Record<string, unknown>) => void;
  reject: (error: Error) => void;
}

interface CdpEventListener {
  method: string;
  sessionId?: string;
  resolve: (params: Record<string, unknown>) => void;
}

interface CdpMessage {
  id?: number;
  method?: string;
  sessionId?: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: { message?: string };
}

class CdpClient {
  private readonly websocketUrl: string;
  private nextId: number;
  private readonly pending: Map<number, PendingRequest>;
  private listeners: CdpEventListener[];
  private ws: WebSocket | null;

  constructor(websocketUrl: string) {
    this.websocketUrl = websocketUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = [];
    this.ws = null;
  }

  async connect(timeoutMs: number): Promise<void> {
    if (typeof WebSocket === "undefined") {
      throw new Error(
        "当前 Node.js 缺少 WebSocket 支持，请使用 Node.js 22+ 或基础镜像中的 Node.js 24+",
      );
    }

    const ws = new WebSocket(this.websocketUrl);
    this.ws = ws;
    ws.addEventListener("message", (event: MessageEvent) =>
      this.handleMessage(event.data),
    );

    await withTimeout(
      new Promise<void>((resolve, reject) => {
        ws.addEventListener("open", () => resolve(), { once: true });
        ws.addEventListener(
          "error",
          () => reject(new Error("连接 Chromium DevTools 失败")),
          { once: true },
        );
      }),
      timeoutMs,
      "连接 Chromium DevTools 超时",
    );
  }

  private handleMessage(raw: unknown): void {
    let rawText: string;
    if (typeof raw === "string") {
      rawText = raw;
    } else if (Buffer.isBuffer(raw)) {
      rawText = raw.toString("utf8");
    } else if (raw instanceof ArrayBuffer) {
      rawText = Buffer.from(raw).toString("utf8");
    } else {
      rawText = String(raw);
    }

    let message: CdpMessage;
    try {
      message = JSON.parse(rawText) as CdpMessage;
    } catch {
      return;
    }

    if (message.id !== undefined && this.pending.has(message.id)) {
      const entry = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (!entry) {
        return;
      }
      if (message.error) {
        entry.reject(
          new Error(message.error.message || JSON.stringify(message.error)),
        );
      } else {
        entry.resolve(message.result || {});
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

  send<T extends Record<string, unknown> = Record<string, unknown>>(
    method: string,
    params: Record<string, unknown> = {},
    sessionId?: string,
  ): Promise<T> {
    const ws = this.ws;
    if (!ws) {
      return Promise.reject(new Error("Chromium DevTools 尚未连接"));
    }
    const id = this.nextId;
    this.nextId += 1;
    const payload: Record<string, unknown> = { id, method, params };
    if (sessionId) {
      payload.sessionId = sessionId;
    }

    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        resolve: (result) => resolve(result as T),
        reject,
      });
      ws.send(JSON.stringify(payload));
    });
  }

  waitForEvent(
    method: string,
    sessionId: string | undefined,
    timeoutMs: number,
  ): Promise<Record<string, unknown>> {
    return withTimeout(
      new Promise<Record<string, unknown>>((resolve) => {
        this.listeners.push({ method, sessionId, resolve });
      }),
      timeoutMs,
      `等待事件 ${method} 超时`,
    );
  }

  close(): void {
    if (this.ws) {
      this.ws.close();
    }
  }
}

async function createPage(client: CdpClient, params: Params): Promise<string> {
  const target = await client.send<{ targetId: string }>(
    "Target.createTarget",
    {
      url: "about:blank",
    },
  );
  const attached = await client.send<{ sessionId: string }>(
    "Target.attachToTarget",
    {
      targetId: target.targetId,
      flatten: true,
    },
  );
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
  await client.send(
    "Emulation.setTimezoneOverride",
    {
      timezoneId:
        process.env.WEB_PAGE_TIMEZONE || process.env.TZ || DEFAULT_TIMEZONE,
    },
    sessionId,
  );
  return sessionId;
}

async function navigate(
  client: CdpClient,
  sessionId: string,
  params: Params,
): Promise<void> {
  const loadEvent = client
    .waitForEvent("Page.loadEventFired", sessionId, params.timeoutMs)
    .catch(() => null);
  await client.send("Page.navigate", { url: params.url }, sessionId);
  await loadEvent;
  if (params.waitMs > 0) {
    await wait(params.waitMs);
  }
}

interface RuntimeEvaluateResult {
  result?: { value?: unknown };
  exceptionDetails?: { text?: string };
}

async function evaluate<T>(
  client: CdpClient,
  sessionId: string,
  expression: string,
): Promise<T> {
  const result = await client.send<Record<string, unknown>>(
    "Runtime.evaluate",
    {
      expression,
      awaitPromise: true,
      returnByValue: true,
    },
    sessionId,
  );
  const evaluation = result as RuntimeEvaluateResult;
  if (evaluation.exceptionDetails) {
    throw new Error(evaluation.exceptionDetails.text || "页面脚本执行失败");
  }
  return (evaluation.result ? evaluation.result.value : undefined) as T;
}

async function getElementPoint(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<ElementPoint> {
  const encodedAction = JSON.stringify(action);
  const point = await evaluate<ElementPoint | null>(
    client,
    sessionId,
    `(async () => {
    const action = ${encodedAction};
    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
    };
    const textOf = (el) => normalize([
      el.innerText,
      el.value,
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.getAttribute('alt'),
      el.getAttribute('placeholder'),
    ].filter(Boolean).join(' '));
    const matchesText = (el) => {
      if (!action.text) return true;
      const haystack = textOf(el);
      return action.exact ? haystack === action.text : haystack.includes(action.text);
    };
    const candidates = action.selector
      ? Array.from(document.querySelectorAll(action.selector))
      : Array.from(document.querySelectorAll('button, a, input, textarea, select, label, [role="button"], [onclick], [tabindex]'));
    const matches = candidates.filter((el) => isVisible(el) && matchesText(el));
    const el = matches[action.index || 0];
    if (!el) {
      return null;
    }
    el.scrollIntoView({ block: 'center', inline: 'center' });
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const rect = el.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2)),
      y: Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2)),
      description: action.selector || action.text || textOf(el),
    };
  })()`,
  );
  if (!point) {
    const target = action.selector || `text=${action.text}`;
    throw new Error(`未找到可操作元素: ${target}`);
  }
  return point;
}

async function clickElement(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  const point = await getElementPoint(client, sessionId, action);
  const clickCount = Math.max(
    1,
    parseInteger(action.click_count, "click_count", 1),
  );
  await client.send(
    "Input.dispatchMouseEvent",
    {
      type: "mouseMoved",
      x: point.x,
      y: point.y,
      button: "none",
      clickCount: 0,
    },
    sessionId,
  );
  for (let index = 0; index < clickCount; index += 1) {
    await client.send(
      "Input.dispatchMouseEvent",
      {
        type: "mousePressed",
        x: point.x,
        y: point.y,
        button: "left",
        buttons: 1,
        clickCount: index + 1,
      },
      sessionId,
    );
    await client.send(
      "Input.dispatchMouseEvent",
      {
        type: "mouseReleased",
        x: point.x,
        y: point.y,
        button: "left",
        buttons: 0,
        clickCount: index + 1,
      },
      sessionId,
    );
  }
}

async function fillElement(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  const encodedAction = JSON.stringify(action);
  const ok = await evaluate<boolean>(
    client,
    sessionId,
    `(() => {
    const action = ${encodedAction};
    const el = document.querySelectorAll(action.selector)[action.index || 0];
    if (!el) return false;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    el.focus();
    const value = action.value;
    if (el.isContentEditable) {
      el.textContent = value;
    } else if ('value' in el) {
      el.value = value;
    } else {
      el.textContent = value;
    }
    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  })()`,
  );
  if (!ok) {
    throw new Error(`未找到可填写元素: ${action.selector}`);
  }
}

async function focusElement(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  if (!action.selector) {
    return;
  }
  const encodedAction = JSON.stringify(action);
  const ok = await evaluate<boolean>(
    client,
    sessionId,
    `(() => {
    const action = ${encodedAction};
    const el = document.querySelectorAll(action.selector)[action.index || 0];
    if (!el) return false;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    el.focus();
    return document.activeElement === el || el.contains(document.activeElement);
  })()`,
  );
  if (!ok) {
    throw new Error(`未找到可聚焦元素: ${action.selector}`);
  }
}

async function typeText(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  await focusElement(client, sessionId, action);
  await client.send("Input.insertText", { text: action.text }, sessionId);
}

interface KeyDefinition {
  key: string;
  code: string;
  text?: string;
  windowsVirtualKeyCode: number;
}

function keyDefinition(key: string): KeyDefinition {
  const special: Record<
    string,
    { code: string; windowsVirtualKeyCode: number }
  > = {
    Enter: { code: "Enter", windowsVirtualKeyCode: 13 },
    Tab: { code: "Tab", windowsVirtualKeyCode: 9 },
    Escape: { code: "Escape", windowsVirtualKeyCode: 27 },
    Backspace: { code: "Backspace", windowsVirtualKeyCode: 8 },
    Delete: { code: "Delete", windowsVirtualKeyCode: 46 },
    ArrowUp: { code: "ArrowUp", windowsVirtualKeyCode: 38 },
    ArrowDown: { code: "ArrowDown", windowsVirtualKeyCode: 40 },
    ArrowLeft: { code: "ArrowLeft", windowsVirtualKeyCode: 37 },
    ArrowRight: { code: "ArrowRight", windowsVirtualKeyCode: 39 },
  };
  const found = special[key];
  if (found) {
    return { key, ...found };
  }
  if (key.length === 1) {
    const upper = key.toUpperCase();
    return {
      key,
      code: `Key${upper}`,
      text: key,
      windowsVirtualKeyCode: upper.charCodeAt(0),
    };
  }
  return { key, code: key, windowsVirtualKeyCode: 0 };
}

async function pressKey(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  await focusElement(client, sessionId, action);
  const key = keyDefinition(action.key);
  await client.send(
    "Input.dispatchKeyEvent",
    { type: "keyDown", ...key },
    sessionId,
  );
  await client.send(
    "Input.dispatchKeyEvent",
    { type: "keyUp", ...key },
    sessionId,
  );
}

async function selectElement(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  const encodedAction = JSON.stringify(action);
  const ok = await evaluate<boolean>(
    client,
    sessionId,
    `(() => {
    const action = ${encodedAction};
    const el = document.querySelectorAll(action.selector)[action.index || 0];
    if (!el || !(el instanceof HTMLSelectElement)) return false;
    el.focus();
    el.value = action.value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  })()`,
  );
  if (!ok) {
    throw new Error(`未找到可选择的 select 元素: ${action.selector}`);
  }
}

async function setChecked(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
  checked: boolean,
): Promise<void> {
  const encodedAction = JSON.stringify(action);
  const ok = await evaluate<boolean>(
    client,
    sessionId,
    `(() => {
    const action = ${encodedAction};
    const el = document.querySelectorAll(action.selector)[action.index || 0];
    if (!el || !('checked' in el)) return false;
    el.focus();
    el.checked = ${checked ? "true" : "false"};
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  })()`,
  );
  if (!ok) {
    throw new Error(`未找到可勾选元素: ${action.selector}`);
  }
}

async function removeElement(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  const encodedAction = JSON.stringify(action);
  const ok = await evaluate<boolean>(
    client,
    sessionId,
    `(() => {
    const action = ${encodedAction};
    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const textOf = (el) => normalize([
      el.innerText,
      el.value,
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.getAttribute('alt'),
      el.getAttribute('placeholder'),
    ].filter(Boolean).join(' '));
    const matchesText = (el) => {
      if (!action.text) return true;
      const haystack = textOf(el);
      return action.exact ? haystack === action.text : haystack.includes(action.text);
    };
    const candidates = action.selector
      ? Array.from(document.querySelectorAll(action.selector))
      : Array.from(document.querySelectorAll('body *'));
    const matches = candidates.filter((el) => matchesText(el));
    const el = matches[action.index || 0];
    if (!el) return false;
    el.remove();
    return true;
  })()`,
  );
  if (!ok) {
    const target = action.selector || `text=${action.text}`;
    throw new Error(`未找到可删除元素: ${target}`);
  }
}

async function waitForSelector(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  const state = action.state || "visible";
  if (!["attached", "visible", "hidden", "detached"].includes(state)) {
    throw new Error(
      "wait_for_selector.state 只能是 attached、visible、hidden 或 detached",
    );
  }
  const startedAt = Date.now();
  while (Date.now() - startedAt <= action.timeoutMs) {
    const encodedAction = JSON.stringify(action);
    const result = await evaluate<SelectorState>(
      client,
      sessionId,
      `(() => {
      const action = ${encodedAction};
      const el = document.querySelectorAll(action.selector)[action.index || 0];
      if (!el) return { attached: false, visible: false };
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return {
        attached: true,
        visible: style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && rect.width > 0 && rect.height > 0,
      };
    })()`,
    );
    if (
      (state === "attached" && result.attached) ||
      (state === "visible" && result.visible) ||
      (state === "hidden" && result.attached && !result.visible) ||
      (state === "detached" && !result.attached)
    ) {
      return;
    }
    await wait(100);
  }
  throw new Error(`等待 selector 超时: ${action.selector}`);
}

async function scrollPage(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  await evaluate<unknown>(
    client,
    sessionId,
    `window.scrollBy(${JSON.stringify(action.x)}, ${JSON.stringify(action.y)})`,
  );
}

async function scrollToTarget(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  const encodedAction = JSON.stringify(action);
  const ok = await evaluate<boolean>(
    client,
    sessionId,
    `(() => {
    const action = ${encodedAction};
    if (action.selector || action.text) {
      const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
      const textOf = (el) => normalize([el.innerText, el.value, el.getAttribute('aria-label'), el.getAttribute('title')].filter(Boolean).join(' '));
      const candidates = action.selector
        ? Array.from(document.querySelectorAll(action.selector))
        : Array.from(document.querySelectorAll('button, a, input, textarea, select, label, [role="button"], [onclick], [tabindex], main, article, section, div'));
      const matches = candidates.filter((el) => {
        if (!action.text) return true;
        const haystack = textOf(el);
        return action.exact ? haystack === action.text : haystack.includes(action.text);
      });
      const el = matches[action.index || 0];
      if (!el) return false;
      el.scrollIntoView({ block: 'center', inline: 'center' });
      return true;
    }
    window.scrollTo(action.x || 0, action.y || 0);
    return true;
  })()`,
  );
  if (!ok) {
    const target = action.selector || `text=${action.text}`;
    throw new Error(`未找到可滚动到的元素: ${target}`);
  }
}

async function runSingleAction(
  client: CdpClient,
  sessionId: string,
  action: NormalizedAction,
): Promise<void> {
  switch (action.type) {
    case "click":
      await clickElement(client, sessionId, action);
      return;
    case "fill":
      await fillElement(client, sessionId, action);
      return;
    case "type":
      await typeText(client, sessionId, action);
      return;
    case "press":
      await pressKey(client, sessionId, action);
      return;
    case "select":
      await selectElement(client, sessionId, action);
      return;
    case "check":
      await setChecked(client, sessionId, action, true);
      return;
    case "uncheck":
      await setChecked(client, sessionId, action, false);
      return;
    case "remove":
      await removeElement(client, sessionId, action);
      return;
    case "wait":
      await wait(action.ms ?? 0);
      return;
    case "wait_for_selector":
      await waitForSelector(client, sessionId, action);
      return;
    case "scroll":
      await scrollPage(client, sessionId, action);
      return;
    case "scroll_to":
      await scrollToTarget(client, sessionId, action);
      return;
    default:
      throw new Error(`不支持的 action.type: ${String(action.type)}`);
  }
}

async function runActions(
  client: CdpClient,
  sessionId: string,
  params: Params,
): Promise<void> {
  if (!params.actions.length) {
    return;
  }

  for (let index = 0; index < params.actions.length; index += 1) {
    const action = params.actions[index];
    try {
      const loadEvent = action.waitForNavigation
        ? client
            .waitForEvent("Page.loadEventFired", sessionId, action.timeoutMs)
            .catch(() => null)
        : null;
      await withTimeout(
        runSingleAction(client, sessionId, action),
        action.timeoutMs,
        `执行 action 超时: ${action.type}`,
      );
      if (loadEvent) {
        await loadEvent;
      }
      if (action.waitMsAfter > 0) {
        await wait(action.waitMsAfter);
      }
    } catch (error) {
      throw new Error(
        `第 ${index + 1} 个 action(${action.type}) 执行失败: ${errorMessage(error)}`,
      );
    }
  }
}

function cleanText(text: string): string {
  return String(text || "")
    .replace(/\u00a0/g, " ")
    .split("\n")
    .map((line) => line.trim().replace(/[ \t]+/g, " "))
    .filter((line, index, lines) => line || lines[index - 1])
    .join("\n")
    .trim();
}

async function extractContent(
  client: CdpClient,
  sessionId: string,
  params: Params,
): Promise<string> {
  const data = await evaluate<ExtractedContent>(
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
  const links: ExtractedLink[] = [];
  const seenLinks = new Set<string>();
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

async function getLayoutMetrics(
  client: CdpClient,
  sessionId: string,
): Promise<LayoutContentSize> {
  const metrics = await client.send<{
    cssContentSize?: LayoutContentSize;
    contentSize?: LayoutContentSize;
  }>("Page.getLayoutMetrics", {}, sessionId);
  return (
    metrics.cssContentSize ||
    metrics.contentSize || { x: 0, y: 0, width: 0, height: 0 }
  );
}

async function getSelectorClip(
  client: CdpClient,
  sessionId: string,
  selector: string,
): Promise<Rect> {
  const encodedSelector = JSON.stringify(selector);
  const rect = await evaluate<Rect | null>(
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

async function getScreenshotClip(
  client: CdpClient,
  sessionId: string,
  params: Params,
): Promise<ScreenshotClip> {
  if (params.screenshotMode === "region") {
    if (
      params.x === undefined ||
      params.y === undefined ||
      params.regionWidth === undefined ||
      params.regionHeight === undefined
    ) {
      throw new Error("region 截图模式必须传 x、y、width、height");
    }
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

interface ScreenshotResult {
  output: string;
  clip: ScreenshotClip;
}

async function captureScreenshot(
  client: CdpClient,
  sessionId: string,
  params: Params,
): Promise<ScreenshotResult> {
  const clip = await getScreenshotClip(client, sessionId, params);
  const result = await client.send<{ data?: string }>(
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

function postJson(
  url: string,
  body: Record<string, unknown>,
  timeoutMs: number,
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
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
      (response: IncomingMessage) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk: Buffer) => chunks.push(chunk));
        response.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          const statusCode = response.statusCode ?? 0;
          if (statusCode < 200 || statusCode >= 300) {
            reject(new Error(`HTTP ${statusCode}: ${text}`));
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

async function maybeSendScreenshot(
  params: Params,
  filePath: string,
): Promise<boolean> {
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

async function run(): Promise<void> {
  const params = normalizeParams(parseArgs(process.argv.slice(2)));
  const browser = await launchChromium(params);
  const client = new CdpClient(browser.websocketUrl);
  try {
    await client.connect(params.timeoutMs);
    const sessionId = await createPage(client, params);
    await navigate(client, sessionId, params);
    await runActions(client, sessionId, params);

    if (params.mode === "content") {
      stdout(await extractContent(client, sessionId, params));
      return;
    }

    const screenshot = await captureScreenshot(client, sessionId, params);
    const sent = await maybeSendScreenshot(params, screenshot.output);
    if (sent) {
      const suffix = screenshot.clip.truncated
        ? `，页面过长，已截取前 ${Math.round(screenshot.clip.height)}px / ${Math.round(screenshot.clip.rawHeight ?? screenshot.clip.height)}px`
        : "";
      stdout(`页面截图已发送${suffix}`);
    } else {
      const suffix = screenshot.clip.truncated
        ? `（页面过长，已截取前 ${Math.round(screenshot.clip.height)}px / ${Math.round(screenshot.clip.rawHeight ?? screenshot.clip.height)}px）`
        : "";
      stdout(`页面截图已保存: ${screenshot.output}${suffix}`);
    }
  } finally {
    await closeChromium(client, browser);
    await cleanupUserDataDir(browser.userDataDir);
  }
}

run().catch((error: unknown) => {
  stdout(
    `执行失败: ${error instanceof Error && error.stack ? error.stack : String(error)}`,
  );
  process.exit(1);
});
