#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const { TextDecoder } = require("util");

const root = process.cwd();
const utf8Decoder = new TextDecoder("utf-8", { fatal: true });
// Mojibake của UTF-8 bị đọc như Latin-1: byte dẫn (C3/C4/C6, E1 BB, E2 80) LUÔN đi kèm
// byte tiếp theo trong khoảng 80–BF. Bắt buộc khoảng đó để không đánh dấu nhầm chữ
// hoa tiếng Việt hợp lệ như "ĐÃ XOÁ" (Ã + khoảng trắng) — trước đây `.` bắt mọi ký tự
// nên hook đỏ trên ~60 file hoàn toàn sạch.
const mojibakePattern =
  /(?:[\u00C3\u00C4\u00C6][\u0080-\u00BF]|\u00E1\u00BB[\u0080-\u00BF]|\u00E2\u0080[\u0080-\u00BF])/;

const skipDirs = new Set([
  ".git",
  "node_modules",
  ".venv",
  ".uv-cache",
  ".docker-tmp",
  "dist",
  "build",
  "staticfiles",
  "__pycache__",
]);

const textExtensions = new Set([
  ".py",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".json",
  ".md",
  ".toml",
  ".yml",
  ".yaml",
  ".css",
  ".html",
  ".txt",
  ".env",
]);

const mojibakeCheckExtensions = new Set([
  ".py",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".md",
  ".html",
  ".css",
]);

const intentionalMojibakeFiles = new Set([
  path.join("backend", "apps", "orders", "address_catalog.py"),
  path.join("backend", "apps", "orders", "scripts", "convert_sorted_catalog.py"),
]);

function isTextFile(filePath) {
  const base = path.basename(filePath);
  if (base.startsWith(".env")) return true;
  return textExtensions.has(path.extname(filePath).toLowerCase());
}

function shouldCheckMojibake(filePath) {
  const rel = path.relative(root, filePath);
  const ext = path.extname(filePath).toLowerCase();
  const base = path.basename(filePath).toLowerCase();
  if (intentionalMojibakeFiles.has(rel)) return false;
  if (!mojibakeCheckExtensions.has(ext)) return false;
  if (base.endsWith(".min.js")) return false;
  return true;
}

function* walk(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (skipDirs.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(full);
    } else if (isTextFile(full)) {
      yield full;
    }
  }
}

function hasUtf16Bom(buf) {
  return (
    (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) ||
    (buf.length >= 2 && buf[0] === 0xfe && buf[1] === 0xff)
  );
}

const errors = [];
const brokenNewlineSequence = String.fromCharCode(96) + "r" + String.fromCharCode(96) + "n";

for (const file of walk(root)) {
  const rel = path.relative(root, file);
  const buf = fs.readFileSync(file);

  if (hasUtf16Bom(buf)) {
    errors.push(`${rel}: UTF-16 BOM detected (must be UTF-8)`);
    continue;
  }

  try {
    const text = utf8Decoder.decode(buf);
    if (text.includes(brokenNewlineSequence)) {
      errors.push(`${rel}: found literal \`r\`n sequence (broken replacement artifact)`);
    }
    if (shouldCheckMojibake(file) && mojibakePattern.test(text)) {
      const lines = text.split(/\r?\n/);
      for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        const lower = line.toLowerCase();
        if (lower.includes("mojibake") || lower.includes("repair_mojibake")) continue;
        if (mojibakePattern.test(line)) {
          errors.push(`${rel}:${i + 1}: possible mojibake detected`);
          break;
        }
      }
    }
  } catch {
    errors.push(`${rel}: invalid UTF-8 byte sequence`);
  }
}

if (errors.length > 0) {
  console.error("Encoding check failed:");
  for (const err of errors) console.error(`- ${err}`);
  process.exit(1);
}

console.log("Encoding check passed.");
