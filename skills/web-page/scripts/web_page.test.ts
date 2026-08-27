import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { isLocalFileUrl, validateUrl } from "./web_page.ts";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT_PATH = path.join(SCRIPT_DIR, "web_page.ts");
const PASSWD_MARKER = "root:x:0:0";

interface ScriptResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

function runWebPage(url: string, args: string[] = []): Promise<ScriptResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      [
        "--experimental-strip-types",
        SCRIPT_PATH,
        "--url",
        url,
        "--mode",
        "content",
        "--wait_ms",
        "300",
        "--timeout_ms",
        "8000",
        "--max_chars",
        "4000",
        ...args,
      ],
      {
        cwd: SCRIPT_DIR,
        env: process.env,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });
    child.once("error", reject);

    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`web_page.ts 执行超时\nstdout: ${stdout}\nstderr: ${stderr}`));
    }, 20000);
    child.once("close", (code) => {
      clearTimeout(timer);
      resolve({ code, stdout, stderr });
    });
  });
}

function assertLocalFileBlocked(result: ScriptResult): void {
  const output = `${result.stdout}\n${result.stderr}`;
  assert.notEqual(result.code, 0, output);
  assert.match(
    output,
    /已阻止浏览器|网页链接必须是 http 或 https 地址|网页导航失败/,
  );
  assert.doesNotMatch(output, new RegExp(PASSWD_MARKER));
}

test("web-page 本地文件访问防护", async (t) => {
  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url || "/", "http://127.0.0.1");
    response.setHeader("Content-Type", "text/html; charset=utf-8");

    switch (requestUrl.pathname) {
      case "/redirect-file":
        response.statusCode = 302;
        response.setHeader("Location", "file:///etc/passwd");
        response.end();
        return;
      case "/click-file":
        response.end(
          '<!doctype html><title>click file</title><a id="local-file" href="file:///etc/passwd">打开本地文件</a>',
        );
        return;
      case "/js-location":
        response.end(
          '<!doctype html><title>js location</title><p>safe</p><script>setTimeout(() => { location.href = "file:///etc/passwd"; }, 50);</script>',
        );
        return;
      case "/iframe-file":
        response.end(
          '<!doctype html><title>iframe file</title><p>safe</p><iframe src="file:///etc/passwd"></iframe>',
        );
        return;
      case "/popup-file":
        response.end(
          '<!doctype html><title>popup file</title><button id="open-popup" onclick="window.open(\'file:///etc/passwd\', \'_blank\')">打开弹窗</button>',
        );
        return;
      case "/redirect-http":
        response.statusCode = 302;
        response.setHeader("Location", "/search?q=normal-http-redirect");
        response.end();
        return;
      case "/search":
        response.end(
          `<!doctype html><title>search ok</title><main>SEARCH_OK:${requestUrl.searchParams.get("q") || ""}</main><script>console.log("file:///not-an-access-attempt")</script>`,
        );
        return;
      default:
        response.statusCode = 404;
        response.end("not found");
    }
  });

  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => server.close());

  const address = server.address();
  assert.ok(address && typeof address === "object");
  const baseUrl = `http://127.0.0.1:${address.port}`;

  await t.test("直接访问 file:///etc/passwd", async () => {
    assertLocalFileBlocked(await runWebPage("file:///etc/passwd"));
  });

  await t.test("HTTP 302 跳转到 file://", async () => {
    assertLocalFileBlocked(await runWebPage(`${baseUrl}/redirect-file`));
  });

  await t.test("点击 file:// 链接", async () => {
    assertLocalFileBlocked(
      await runWebPage(`${baseUrl}/click-file`, [
        "--actions",
        JSON.stringify([{ type: "click", selector: "#local-file" }]),
      ]),
    );
  });

  await t.test("JavaScript 修改 location", async () => {
    assertLocalFileBlocked(await runWebPage(`${baseUrl}/js-location`));
  });

  await t.test("iframe 加载本地文件", async () => {
    assertLocalFileBlocked(await runWebPage(`${baseUrl}/iframe-file`));
  });

  await t.test("弹窗加载本地文件", async () => {
    assertLocalFileBlocked(
      await runWebPage(`${baseUrl}/popup-file`, [
        "--actions",
        JSON.stringify([{ type: "click", selector: "#open-popup" }]),
      ]),
    );
  });

  await t.test("正常 HTTP/HTTPS 搜索及浏览不受影响", async () => {
    assert.equal(
      validateUrl("https://example.com/search?q=normal-https"),
      "https://example.com/search?q=normal-https",
    );
    assert.equal(isLocalFileUrl("https://example.com/file.txt"), false);
    assert.equal(isLocalFileUrl("file:///etc/passwd"), true);
    assert.equal(isLocalFileUrl("filesystem:https://example.com/temporary/a"), true);

    const normal = await runWebPage(`${baseUrl}/redirect-http`);
    assert.equal(normal.code, 0, `${normal.stdout}\n${normal.stderr}`);
    assert.match(normal.stdout, /SEARCH_OK:normal-http-redirect/);
    assert.doesNotMatch(normal.stdout, new RegExp(PASSWD_MARKER));
  });
});
