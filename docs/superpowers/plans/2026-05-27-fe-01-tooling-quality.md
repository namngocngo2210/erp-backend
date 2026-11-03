# FE-01: Tooling & Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dựng hàng rào chất lượng tự động (ESLint + Prettier + pre-commit + CI) cho frontend và dọn các nợ kỹ thuật cấp file: BOM trong tsconfig, `console.*` sót, lạm dụng `any`.

**Architecture:** Thêm ESLint flat config (typescript-eslint + react-hooks + react-refresh) và Prettier; gắn husky + lint-staged để chặn commit bẩn; thêm GitHub Actions chạy lint/format/test/build. Lint chạy ở chế độ "no new errors" — bật rule cảnh báo cho `console`/`any` thay vì fail toàn bộ codebase cũ ngay lập tức.

**Tech Stack:** ESLint 9 (flat config), typescript-eslint, eslint-plugin-react-hooks, eslint-plugin-react-refresh, Prettier 3, husky, lint-staged, GitHub Actions.

Giải quyết: P1 (không có ESLint/Prettier + CI), P3 (BOM tsconfig, console.*, any).

---

### Task 1: Fix BOM trong tsconfig.json

**Files:**
- Modify: `frontend/tsconfig.json`

- [ ] **Step 1: Xác nhận BOM tồn tại**

Run: `head -c 3 frontend/tsconfig.json | xxd`
Expected: dòng đầu chứa `efbb bf` (BOM).

- [ ] **Step 2: Strip BOM (ghi lại file UTF-8 không BOM)**

```bash
cd frontend
perl -i -pe 'BEGIN{undef $/;} s/^\xEF\xBB\xBF//' tsconfig.json
```

- [ ] **Step 3: Verify hết BOM và JSON còn hợp lệ**

Run: `head -c 3 frontend/tsconfig.json | xxd && node -e "JSON.parse(require('fs').readFileSync('frontend/tsconfig.json','utf8')); console.log('OK')"`
Expected: 3 byte đầu là `7b0a ...` (`{`), in ra `OK`.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add tsconfig.json && git commit -m "fix: remove BOM from tsconfig.json"
```

---

### Task 2: Cài Prettier + config

**Files:**
- Create: `frontend/.prettierrc.json`
- Create: `frontend/.prettierignore`
- Modify: `frontend/package.json` (scripts + devDependencies)

- [ ] **Step 1: Cài Prettier**

```bash
cd frontend && npm i -D prettier@^3
```

- [ ] **Step 2: Tạo `.prettierrc.json`**

```json
{
  "semi": true,
  "singleQuote": false,
  "trailingComma": "all",
  "printWidth": 100,
  "tabWidth": 2
}
```

- [ ] **Step 3: Tạo `.prettierignore`**

```
dist
node_modules
coverage
src/i18n/messages.ts
```

- [ ] **Step 4: Thêm scripts vào `package.json`**

Trong khối `"scripts"` thêm:

```json
"format": "prettier --write \"src/**/*.{ts,tsx,css}\"",
"format:check": "prettier --check \"src/**/*.{ts,tsx,css}\""
```

- [ ] **Step 5: Format toàn bộ codebase một lần (commit riêng để diff sau sạch)**

Run: `cd frontend && npm run format`
Expected: Prettier ghi lại nhiều file, exit 0.

- [ ] **Step 6: Commit**

```bash
cd frontend && git add -A && git commit -m "chore: add prettier and format codebase"
```

---

### Task 3: Cài ESLint flat config

**Files:**
- Create: `frontend/eslint.config.js`
- Modify: `frontend/package.json` (scripts + devDependencies)

- [ ] **Step 1: Cài ESLint + plugin**

```bash
cd frontend && npm i -D eslint@^9 typescript-eslint@^8 eslint-plugin-react-hooks@^5 eslint-plugin-react-refresh@^0.4 eslint-config-prettier@^9 globals@^15
```

- [ ] **Step 2: Tạo `eslint.config.js`**

```js
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import prettier from "eslint-config-prettier";

export default tseslint.config(
  { ignores: ["dist", "coverage", "src/i18n/messages.ts"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Nợ cũ: cảnh báo trước, siết dần (xem Task 6)
      "no-console": ["warn", { allow: ["error", "warn"] }],
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  prettier,
);
```

- [ ] **Step 3: Thêm script lint vào `package.json`**

```json
"lint": "eslint src --max-warnings=9999",
"lint:strict": "eslint src --max-warnings=0"
```

> `lint` dùng cho CI giai đoạn đầu (không fail vì warning nợ cũ); `lint:strict` là mục tiêu sau khi dọn xong.

- [ ] **Step 4: Chạy lint, xác nhận không có ERROR (chỉ warning)**

Run: `cd frontend && npm run lint`
Expected: exit 0; chỉ có warning (no-console / no-explicit-any), không có error. Nếu có error thật (vd react-hooks vi phạm) → sửa ngay trong commit này.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "chore: add eslint flat config"
```

---

### Task 4: Pre-commit hook (husky + lint-staged)

**Files:**
- Create: `frontend/.husky/pre-commit`
- Modify: `frontend/package.json`

> Lưu ý: husky cài ở thư mục `frontend/`. Nếu git root ở repo cha, dùng `prepare` script trong `frontend/package.json` và chạy `npm run prepare` từ `frontend/`.

- [ ] **Step 1: Cài husky + lint-staged**

```bash
cd frontend && npm i -D husky@^9 lint-staged@^15
```

- [ ] **Step 2: Thêm `prepare` script + cấu hình lint-staged vào `package.json`**

```json
"scripts": {
  "prepare": "husky"
},
"lint-staged": {
  "src/**/*.{ts,tsx}": ["eslint --fix", "prettier --write"],
  "src/**/*.css": ["prettier --write"]
}
```

- [ ] **Step 3: Khởi tạo husky + tạo hook**

```bash
cd frontend && npm run prepare && printf 'cd frontend && npx lint-staged\n' > .husky/pre-commit && chmod +x .husky/pre-commit
```

> Dòng `cd frontend` cần thiết nếu git root nằm ở repo cha; nếu `frontend/` chính là git root thì bỏ `cd frontend &&`.

- [ ] **Step 4: Test hook thủ công**

```bash
cd frontend && npx lint-staged --diff="HEAD~1 HEAD"
```
Expected: chạy eslint --fix + prettier trên file đã đổi, exit 0.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "chore: add husky pre-commit with lint-staged"
```

---

### Task 5: CI GitHub Actions

**Files:**
- Create: `.github/workflows/frontend-ci.yml` (ở git root)

- [ ] **Step 1: Tạo workflow**

```yaml
name: frontend-ci
on:
  pull_request:
    paths: ["frontend/**"]
  push:
    branches: [master]
    paths: ["frontend/**"]
jobs:
  build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm run format:check
      - run: npm run lint
      - run: npm run test
      - run: npm run build
```

- [ ] **Step 2: Validate cú pháp YAML**

Run: `node -e "require('js-yaml')" 2>/dev/null && echo has-yaml || python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/frontend-ci.yml')); print('YAML OK')"`
Expected: `YAML OK` (hoặc dùng `yamllint` nếu có).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/frontend-ci.yml && git commit -m "ci: add frontend lint/test/build workflow"
```

---

### Task 6: Siết dần nợ console/any (theo module, không big-bang)

> Đây là task lặp, làm dần. Mục tiêu cuối: `lint:strict` (max-warnings=0) pass. Mỗi vòng chọn 1 thư mục, dọn warning, rồi nâng rule cho riêng phạm vi đó lên `error` qua override.

**Files:**
- Modify: `frontend/eslint.config.js` (thêm override theo thư mục)
- Modify: các file trong thư mục đang dọn

- [ ] **Step 1: Đếm warning hiện tại theo thư mục**

Run: `cd frontend && npx eslint src --format=compact | grep -c "no-console\|no-explicit-any"`
Expected: số warning (baseline). Ghi vào phần "Tiến độ" cuối plan.

- [ ] **Step 2: Dọn `console.*` ở 1 thư mục (vd `src/services`)**

Thay `console.log(...)` bằng việc xóa, hoặc thay bằng logger có kiểm soát. `console.error`/`console.warn` được allow nên giữ. Sửa thủ công từng chỗ trong thư mục đó.

- [ ] **Step 3: Thay `any` cụ thể trong thư mục đó**

Ví dụ `FetchJson = (url: string, options?: RequestInit) => Promise<any>` → tạo generic `Promise<unknown>` rồi ép kiểu tại call-site, hoặc khai báo type response cụ thể. Sửa từng chỗ.

- [ ] **Step 4: Thêm override nâng rule lên error cho thư mục đã dọn**

Trong `eslint.config.js` thêm block:

```js
{
  files: ["src/services/**/*.{ts,tsx}"],
  rules: {
    "no-console": ["error", { allow: ["error", "warn"] }],
    "@typescript-eslint/no-explicit-any": "error",
  },
},
```

- [ ] **Step 5: Verify thư mục đó không còn warning**

Run: `cd frontend && npx eslint src/services --max-warnings=0`
Expected: exit 0.

- [ ] **Step 6: Commit, rồi lặp Step 2-5 cho thư mục tiếp theo**

```bash
cd frontend && git add -A && git commit -m "chore: enforce no-console/no-any in services"
```

- [ ] **Step 7: Khi tất cả thư mục đã dọn, đổi CI sang `lint:strict`**

Trong `.github/workflows/frontend-ci.yml` đổi `npm run lint` → `npm run lint:strict`, commit.

---

## Self-review checklist
- [ ] `npm run lint` exit 0 (chỉ warning nợ cũ), `npm run format:check` pass.
- [ ] Pre-commit hook chặn được file bẩn (test bằng cách cố commit file có `console.log`).
- [ ] CI workflow chạy đủ 5 bước (format/lint/test/build).
- [ ] tsconfig.json không còn BOM.

## Tiến độ (cập nhật khi thực hiện)
- Baseline warning no-console: ___
- Baseline warning no-explicit-any: ___
- Thư mục đã siết error: ___
