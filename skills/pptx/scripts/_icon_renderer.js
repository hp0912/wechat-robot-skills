#!/usr/bin/env node

"use strict";

const fs = require("fs");
const path = require("path");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");

const LIBRARIES = {
  fa6: "react-icons/fa6",
  fi: "react-icons/fi",
  hi2: "react-icons/hi2",
  io5: "react-icons/io5",
  lu: "react-icons/lu",
  md: "react-icons/md",
  ri: "react-icons/ri",
  tb: "react-icons/tb",
};

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    if (!argv[index]?.startsWith("--") || argv[index + 1] === undefined) {
      fail("参数必须按 --name value 成对提供");
    }
    values[argv[index].slice(2)] = argv[index + 1];
  }
  if (!values.spec || !values.output) {
    fail("缺少 --spec 或 --output");
  }
  return values;
}

function color(value, label) {
  if (typeof value !== "string" || !/^[0-9A-Fa-f]{6}$/.test(value)) {
    throw new Error(`${label} 必须是不带 # 的 6 位十六进制颜色`);
  }
  return `#${value.toUpperCase()}`;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const specPath = path.resolve(args.spec);
  const outputPath = path.resolve(args.output);
  if (path.extname(outputPath).toLowerCase() !== ".png") {
    fail("output 必须使用 .png 扩展名");
  }
  let spec;
  try {
    spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  } catch (error) {
    fail(`读取 spec 失败：${error.message}`);
  }
  try {
    const libraryPath = LIBRARIES[spec.library];
    if (!libraryPath) {
      throw new Error(`不支持的图标库：${spec.library}`);
    }
    if (typeof spec.name !== "string" || !/^[A-Za-z][A-Za-z0-9]*$/.test(spec.name)) {
      throw new Error("name 必须是合法的 React Icons 导出名称");
    }
    const size = spec.size ?? 256;
    if (!Number.isInteger(size) || size < 32 || size > 2048) {
      throw new Error("size 必须是 32 到 2048 的整数");
    }
    const moduleExports = require(libraryPath);
    const Icon = moduleExports[spec.name];
    if (typeof Icon !== "function") {
      throw new Error(`${spec.library} 中不存在图标 ${spec.name}`);
    }
    const foreground = color(spec.color || "111827", "color");
    const markup = ReactDOMServer.renderToStaticMarkup(
      React.createElement(Icon, {
        color: foreground,
        size,
        title: typeof spec.title === "string" ? spec.title : undefined,
      }),
    );
    let pipeline = sharp(Buffer.from(markup))
      .resize(size, size, { fit: "contain" });
    if (spec.background) {
      pipeline = pipeline.flatten({ background: color(spec.background, "background") });
    }
    await pipeline.png().toFile(outputPath);
    const metadata = await sharp(outputPath).metadata();
    process.stdout.write(
      `${JSON.stringify({
        library: spec.library,
        name: spec.name,
        width: metadata.width,
        height: metadata.height,
      })}\n`,
    );
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error));
  }
}

main();
