#!/usr/bin/env node

"use strict";

const fs = require("fs");
const path = require("path");
const PptxGenJS = require("pptxgenjs");

const LAYOUTS = {
  LAYOUT_WIDE: { width: 13.333, height: 7.5 },
  LAYOUT_16X9: { width: 10, height: 5.625 },
  LAYOUT_4X3: { width: 10, height: 7.5 },
};

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key || !key.startsWith("--") || value === undefined) {
      fail("参数必须按 --name value 成对提供");
    }
    values[key.slice(2)] = value;
  }
  if (!values.spec || !values.output) {
    fail("缺少 --spec 或 --output");
  }
  return values;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function requireObject(value, label) {
  if (!isPlainObject(value)) {
    throw new Error(`${label} 必须是对象`);
  }
  return value;
}

function requireArray(value, label, maxLength = 1000) {
  if (!Array.isArray(value)) {
    throw new Error(`${label} 必须是数组`);
  }
  if (value.length > maxLength) {
    throw new Error(`${label} 超过 ${maxLength} 项限制`);
  }
  return value;
}

function requireString(value, label, maxLength = 100000) {
  if (typeof value !== "string") {
    throw new Error(`${label} 必须是字符串`);
  }
  if (value.length > maxLength) {
    throw new Error(`${label} 超过 ${maxLength} 字符限制`);
  }
  return value;
}

function cleanColor(value, label) {
  if (typeof value !== "string" || !/^[0-9A-Fa-f]{6}$/.test(value)) {
    throw new Error(`${label} 必须是不带 # 的 6 位十六进制颜色`);
  }
  return value.toUpperCase();
}

function validateTree(value, label = "options", depth = 0) {
  if (depth > 12) {
    throw new Error(`${label} 嵌套层级过深`);
  }
  if (value === null || typeof value === "boolean" || typeof value === "number") {
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new Error(`${label} 包含非有限数值`);
    }
    return;
  }
  if (typeof value === "string") {
    if (value.length > 200000) {
      throw new Error(`${label} 字符串过长`);
    }
    return;
  }
  if (Array.isArray(value)) {
    if (value.length > 5000) {
      throw new Error(`${label} 数组过长`);
    }
    value.forEach((item, index) => validateTree(item, `${label}[${index}]`, depth + 1));
    return;
  }
  if (!isPlainObject(value)) {
    throw new Error(`${label} 包含不支持的数据类型`);
  }
  const entries = Object.entries(value);
  if (entries.length > 500) {
    throw new Error(`${label} 字段过多`);
  }
  for (const [key, item] of entries) {
    if (["__proto__", "prototype", "constructor"].includes(key)) {
      throw new Error(`${label} 包含禁止字段 ${key}`);
    }
    if (key === "color" && typeof item === "string") {
      cleanColor(item, `${label}.${key}`);
    }
    if (key === "chartColors" && Array.isArray(item)) {
      item.forEach((color, index) => cleanColor(color, `${label}.chartColors[${index}]`));
    }
    if (key === "offset" && label.toLowerCase().includes("shadow")) {
      if (typeof item !== "number" || item < 0) {
        throw new Error(`${label}.offset 必须是非负数`);
      }
    }
    validateTree(item, `${label}.${key}`, depth + 1);
  }
  if (isPlainObject(value.fill) && value.fill.type === "gradient") {
    throw new Error(`${label}.fill 不支持渐变；请使用渐变背景图片`);
  }
}

function requireBox(options, label) {
  for (const key of ["x", "y", "w", "h"]) {
    if (typeof options[key] !== "number" || !Number.isFinite(options[key])) {
      throw new Error(`${label}.${key} 必须是有限数值`);
    }
  }
}

function normalizeRuns(runs, label) {
  return requireArray(runs, label, 2000).map((run, index) => {
    requireObject(run, `${label}[${index}]`);
    const text = requireString(run.text ?? "", `${label}[${index}].text`);
    const options = clone(run.options || {});
    requireObject(options, `${label}[${index}].options`);
    validateTree(options, `${label}[${index}].options`);
    return { text, options };
  });
}

function normalizeTableRows(rows, label) {
  return requireArray(rows, label, 500).map((row, rowIndex) =>
    requireArray(row, `${label}[${rowIndex}]`, 100).map((cell, columnIndex) => {
      if (typeof cell === "string" || typeof cell === "number") {
        return String(cell);
      }
      requireObject(cell, `${label}[${rowIndex}][${columnIndex}]`);
      const normalized = {
        text: requireString(
          String(cell.text ?? ""),
          `${label}[${rowIndex}][${columnIndex}].text`,
        ),
        options: clone(cell.options || {}),
      };
      requireObject(
        normalized.options,
        `${label}[${rowIndex}][${columnIndex}].options`,
      );
      validateTree(
        normalized.options,
        `${label}[${rowIndex}][${columnIndex}].options`,
      );
      return normalized;
    }),
  );
}

function normalizeChartData(data, label) {
  return requireArray(data, label, 100).map((series, index) => {
    requireObject(series, `${label}[${index}]`);
    const labels = requireArray(series.labels, `${label}[${index}].labels`, 10000);
    const values = requireArray(series.values, `${label}[${index}].values`, 10000);
    if (labels.length !== values.length) {
      throw new Error(`${label}[${index}] 的 labels 与 values 长度必须一致`);
    }
    return {
      name: requireString(String(series.name ?? ""), `${label}[${index}].name`, 500),
      labels: labels.map((item) => String(item)),
      values: values.map((item, valueIndex) => {
        if (typeof item !== "number" || !Number.isFinite(item)) {
          throw new Error(`${label}[${index}].values[${valueIndex}] 必须是有限数值`);
        }
        return item;
      }),
    };
  });
}

function resolveImagePath(value, label) {
  const raw = requireString(value, label, 4096);
  const resolved = path.resolve(raw);
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
    throw new Error(`${label} 文件不存在：${resolved}`);
  }
  return resolved;
}

function elementBounds(element, options, slideNumber, index, dimensions, warnings) {
  if (!["text", "shape", "image", "chart", "table"].includes(element.type)) {
    return;
  }
  requireBox(options, `slides[${slideNumber - 1}].elements[${index}].options`);
  const tolerance = 0.002;
  if (
    options.x < -tolerance ||
    options.y < -tolerance ||
    options.x + options.w > dimensions.width + tolerance ||
    options.y + options.h > dimensions.height + tolerance
  ) {
    warnings.push({
      slide: slideNumber,
      element: index + 1,
      code: "out_of_bounds",
      message: "元素超出演示文稿画布",
    });
  }
}

async function build(spec, outputPath) {
  requireObject(spec, "spec");
  const slides = requireArray(spec.slides, "slides", 300);
  if (slides.length === 0) {
    throw new Error("slides 至少需要一页");
  }

  const layout = spec.layout || "LAYOUT_WIDE";
  if (!Object.prototype.hasOwnProperty.call(LAYOUTS, layout)) {
    throw new Error(`layout 不支持：${layout}`);
  }
  const dimensions = LAYOUTS[layout];
  const pptx = new PptxGenJS();
  pptx.layout = layout;

  const properties = spec.properties || {};
  requireObject(properties, "properties");
  const propertyMap = {
    author: "author",
    company: "company",
    subject: "subject",
    title: "title",
    comments: "comments",
    revision: "revision",
  };
  for (const [sourceKey, targetKey] of Object.entries(propertyMap)) {
    if (properties[sourceKey] !== undefined) {
      pptx[targetKey] = requireString(
        String(properties[sourceKey]),
        `properties.${sourceKey}`,
        4000,
      );
    }
  }

  const theme = spec.theme || {};
  requireObject(theme, "theme");
  const headFont = requireString(
    String(theme.head_font || "Noto Sans CJK SC"),
    "theme.head_font",
    200,
  );
  const bodyFont = requireString(
    String(theme.body_font || "Noto Sans CJK SC"),
    "theme.body_font",
    200,
  );
  pptx.theme = {
    headFontFace: headFont,
    bodyFontFace: bodyFont,
    lang: requireString(String(theme.language || "zh-CN"), "theme.language", 40),
  };
  pptx.lang = theme.language || "zh-CN";

  const warnings = [];
  const elementCounts = {};
  for (let slideIndex = 0; slideIndex < slides.length; slideIndex += 1) {
    const slideSpec = requireObject(slides[slideIndex], `slides[${slideIndex}]`);
    const slide = pptx.addSlide();
    if (slideSpec.background !== undefined) {
      slide.background = {
        color: cleanColor(slideSpec.background, `slides[${slideIndex}].background`),
      };
    }
    const elements = requireArray(
      slideSpec.elements || [],
      `slides[${slideIndex}].elements`,
      1000,
    );
    if (elements.length === 0) {
      warnings.push({
        slide: slideIndex + 1,
        code: "empty_slide",
        message: "页面没有可见元素",
      });
    }
    for (let elementIndex = 0; elementIndex < elements.length; elementIndex += 1) {
      const label = `slides[${slideIndex}].elements[${elementIndex}]`;
      const element = requireObject(elements[elementIndex], label);
      const type = requireString(element.type, `${label}.type`, 50);
      const options = clone(element.options || {});
      requireObject(options, `${label}.options`);
      validateTree(options, `${label}.options`);
      elementBounds(
        element,
        options,
        slideIndex + 1,
        elementIndex,
        dimensions,
        warnings,
      );
      elementCounts[type] = (elementCounts[type] || 0) + 1;

      if (type === "text") {
        if (options.fontFace === undefined) {
          options.fontFace = bodyFont;
        }
        const content =
          element.runs !== undefined
            ? normalizeRuns(element.runs, `${label}.runs`)
            : requireString(String(element.text ?? ""), `${label}.text`);
        slide.addText(content, options);
      } else if (type === "shape") {
        const shapeName = requireString(element.shape || "rect", `${label}.shape`, 100);
        const shapeType = pptx.ShapeType[shapeName];
        if (!shapeType) {
          throw new Error(`${label}.shape 不支持：${shapeName}`);
        }
        slide.addShape(shapeType, options);
      } else if (type === "image") {
        if (element.path !== undefined) {
          options.path = resolveImagePath(element.path, `${label}.path`);
        } else if (element.data !== undefined) {
          const data = requireString(element.data, `${label}.data`, 20_000_000);
          if (!/^data:image\/(?:png|jpeg|jpg|webp);base64,/.test(data)) {
            throw new Error(`${label}.data 必须是受支持图片的 base64 data URL`);
          }
          options.data = data;
        } else {
          throw new Error(`${label} 必须提供 path 或 data`);
        }
        slide.addImage(options);
      } else if (type === "chart") {
        const chartName = requireString(element.chart_type, `${label}.chart_type`, 50);
        const chartType = pptx.ChartType[chartName];
        if (!chartType) {
          throw new Error(`${label}.chart_type 不支持：${chartName}`);
        }
        const chartData = normalizeChartData(element.data, `${label}.data`);
        const chartOptions = {
          showLegend: chartData.length > 1,
          showTitle: Boolean(options.title),
          showValue: true,
          chartColors: ["2563EB", "14B8A6", "F97316", "8B5CF6", "E11D48"],
          catAxisLabelColor: "475569",
          valAxisLabelColor: "475569",
          valGridLine: { color: "E2E8F0", size: 1 },
          catAxisLabelFontFace: bodyFont,
          valAxisLabelFontFace: bodyFont,
          dataLabelFontFace: bodyFont,
          legendFontFace: bodyFont,
          titleFontFace: headFont,
          ...options,
        };
        validateTree(chartOptions, `${label}.options`);
        slide.addChart(chartType, chartData, chartOptions);
      } else if (type === "table") {
        if (options.fontFace === undefined) {
          options.fontFace = bodyFont;
        }
        const rows = normalizeTableRows(element.rows, `${label}.rows`);
        slide.addTable(rows, options);
      } else {
        throw new Error(`${label}.type 不支持：${type}`);
      }
    }
    if (slideSpec.speaker_notes !== undefined) {
      const notes = requireString(
        String(slideSpec.speaker_notes),
        `slides[${slideIndex}].speaker_notes`,
        100000,
      );
      slide.addNotes(notes);
    }
  }

  await pptx.writeFile({ fileName: outputPath });
  if (!fs.existsSync(outputPath) || fs.statSync(outputPath).size === 0) {
    throw new Error("PptxGenJS 未生成有效输出文件");
  }
  return {
    slide_count: slides.length,
    element_counts: elementCounts,
    layout,
    width_inches: dimensions.width,
    height_inches: dimensions.height,
    warnings,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const specPath = path.resolve(args.spec);
  const outputPath = path.resolve(args.output);
  if (!fs.existsSync(specPath) || !fs.statSync(specPath).isFile()) {
    fail(`spec 文件不存在：${specPath}`);
  }
  if (path.extname(outputPath).toLowerCase() !== ".pptx") {
    fail("output 必须使用 .pptx 扩展名");
  }
  let spec;
  try {
    spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  } catch (error) {
    fail(`读取 spec 失败：${error.message}`);
  }
  try {
    const result = await build(spec, outputPath);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error));
  }
}

main();
