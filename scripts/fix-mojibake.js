#!/usr/bin/env node
const fs = require("fs");
const path = require("path");

const root = process.cwd();
const targets = process.argv.slice(2);

const skipDirs = new Set([
  ".git",
  "node_modules",
  ".venv",
  ".uv-cache",
  ".docker-tmp",
  "dist",
  "build",
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

const mojibakePattern = /(?:\u00C3.|\u00C4.|\u00C6.|\u00E1\u00BB.|\u00E2\u0080.)/;

function isTextFile(filePath) {
  const base = path.basename(filePath);
  if (base.startsWith(".env")) return true;
  return textExtensions.has(path.extname(filePath).toLowerCase());
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

function collectFiles() {
  if (targets.length === 0) {
    return Array.from(walk(root));
  }

  const files = [];
  for (const target of targets) {
    const full = path.resolve(root, target);
    if (!fs.existsSync(full)) continue;
    const stat = fs.statSync(full);
    if (stat.isDirectory()) {
      files.push(...Array.from(walk(full)));
    } else if (stat.isFile() && isTextFile(full)) {
      files.push(full);
    }
  }
  return files;
}

function markerCount(text) {
  const matches = text.match(/(?:\u00C3.|\u00C4.|\u00C6.|\u00E1\u00BB.|\u00E2\u0080.)/g);
  return matches ? matches.length : 0;
}

function tryFixLine(line) {
  if (!mojibakePattern.test(line)) return line;
  const converted = Buffer.from(line, "latin1").toString("utf8");
  if (converted.includes("\uFFFD")) return line;
  if (markerCount(converted) >= markerCount(line)) return line;
  return converted;
}

let changedFiles = 0;
let changedLines = 0;

for (const file of collectFiles()) {
  const original = fs.readFileSync(file, "utf8");
  const lines = original.split(/\r?\n/);
  let fileChanged = false;

  for (let i = 0; i < lines.length; i += 1) {
    const fixed = tryFixLine(lines[i]);
    if (fixed !== lines[i]) {
      lines[i] = fixed;
      fileChanged = true;
      changedLines += 1;
    }
  }

  if (fileChanged) {
    const next = lines.join("\n");
    fs.writeFileSync(file, next, "utf8");
    changedFiles += 1;
    console.log(`fixed ${path.relative(root, file)}`);
  }
}

if (changedFiles === 0) {
  console.log("No mojibake fixes applied.");
} else {
  console.log(`Done. Updated ${changedFiles} file(s), ${changedLines} line(s).`);
}
