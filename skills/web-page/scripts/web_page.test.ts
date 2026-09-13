import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT_PATH = path.join(SCRIPT_DIR, "web_page.ts");

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

function assertSearchResult(result: ScriptResult, url: string, query: string): void {
  const output = `${result.stdout}\n${result.stderr}`;
  assert.equal(result.code, 0, output);
  assert.ok(result.stdout.includes(`URL：${url}`), output);
  assert.ok(result.stdout.includes(`SEARCH_OK:${query}`), output);
}

test("web-page 网页读取与自动化交互", async (t) => {
  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url || "/", "http://127.0.0.1");
    response.setHeader("Content-Type", "text/html; charset=utf-8");

    switch (requestUrl.pathname) {
      case "/click-link":
        response.end(
          '<!doctype html><title>link</title><a id="search-link" href="/search?q=clicked-link">查看结果</a>',
        );
        return;
      case "/js-location":
        response.end(
          '<!doctype html><title>js location</title><script>setTimeout(() => { location.href = "/search?q=js-navigation"; }, 50);</script>',
        );
        return;
      case "/form":
        response.end(
          '<!doctype html><title>search form</title><form action="/search" method="get"><input id="query" name="q"><button id="submit" type="submit">搜索</button></form>',
        );
        return;
      case "/redirect-http":
        response.statusCode = 302;
        response.setHeader("Location", "/search?q=normal-http-redirect");
        response.end();
        return;
      case "/search":
        response.end(
          `<!doctype html><title>search ok</title><main id="search-result">SEARCH_OK:${requestUrl.searchParams.get("q") || ""}</main><script>console.log("console output is not page content")</script>`,
        );
        return;
      default:
        response.statusCode = 404;
        response.end("not found");
    }
  });

  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => new Promise<void>((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  }));

  const address = server.address();
  assert.ok(address && typeof address === "object");
  const baseUrl = `http://127.0.0.1:${address.port}`;

  await t.test("读取 HTTP 网页正文", async () => {
    const url = `${baseUrl}/search?q=direct-http`;
    const result = await runWebPage(url);
    assertSearchResult(result, url, "direct-http");
    assert.match(result.stdout, /标题：search ok/);
    assert.doesNotMatch(result.stdout, /console output is not page content/);
  });

  await t.test("跟随 HTTP 302 跳转并返回目标网页", async () => {
    assertSearchResult(
      await runWebPage(`${baseUrl}/redirect-http`),
      `${baseUrl}/search?q=normal-http-redirect`,
      "normal-http-redirect",
    );
  });

  await t.test("点击链接并等待目标网页加载", async () => {
    assertSearchResult(
      await runWebPage(`${baseUrl}/click-link`, [
        "--actions",
        JSON.stringify([{
          type: "click",
          selector: "#search-link",
          wait_for_navigation: true,
        }]),
      ]),
      `${baseUrl}/search?q=clicked-link`,
      "clicked-link",
    );
  });

  await t.test("等待 JavaScript 跳转后的页面元素", async () => {
    assertSearchResult(
      await runWebPage(`${baseUrl}/js-location`, [
        "--actions",
        JSON.stringify([{ type: "wait_for_selector", selector: "#search-result" }]),
      ]),
      `${baseUrl}/search?q=js-navigation`,
      "js-navigation",
    );
  });

  await t.test("填写并提交搜索表单", async () => {
    assertSearchResult(
      await runWebPage(`${baseUrl}/form`, [
        "--actions",
        JSON.stringify([
          { type: "fill", selector: "#query", value: "form-search" },
          { type: "click", selector: "#submit", wait_for_navigation: true },
        ]),
      ]),
      `${baseUrl}/search?q=form-search`,
      "form-search",
    );
  });

  await t.test("动作失败时返回动作序号和原因", async () => {
    const result = await runWebPage(`${baseUrl}/form`, [
      "--actions",
      JSON.stringify([
        { type: "fill", selector: "#query", value: "unused" },
        { type: "click", selector: "#missing-button" },
      ]),
    ]);
    const output = `${result.stdout}\n${result.stderr}`;
    assert.equal(result.code, 1, output);
    assert.match(output, /第 2 个 action\(click\) 执行失败/);
    assert.match(output, /未找到可操作元素: #missing-button/);
  });
});

test("web-page 命令行拒绝非 HTTP/HTTPS 协议", async (t) => {
  for (const url of [
    "file:///tmp/web-page-test.html",
    "filesystem:https://example.com/temporary/a",
    "ftp://example.com/file.txt",
  ]) {
    await t.test(`拒绝 ${new URL(url).protocol} 地址`, async () => {
      const result = await runWebPage(url);
      const output = `${result.stdout}\n${result.stderr}`;
      assert.equal(result.code, 1, output);
      assert.match(output, /网页链接必须是 http 或 https 地址/);
    });
  }
});
