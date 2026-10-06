# E2E Tests for Tornado Spreadsheet Application

This directory contains the Playwright-based end-to-end (E2E) testing suite for the Tornado web spreadsheet application. The tests are written in TypeScript and conform to modern Playwright best practices.

---

## Suite Structure

- **`tests/e2e/helpers/app-helper.ts`**: Reusable helper utilities that perform common user actions (e.g. registration, login, logout, cell edits, and saving spreadsheets).
- **`tests/e2e/fixtures/auth.fixture.ts`**: Custom Playwright fixtures that handle automatic per-test unique user registration and authentication setup.
- **`tests/e2e/landing.spec.ts`**: Validates the landing page load and redirection behavior.
- **`tests/e2e/auth.spec.ts`**: Tests the complete registration, login, and logout lifecycle.
- **`tests/e2e/spreadsheet.spec.ts`**: Tests opening a sheet, editing cell data, saving it as a new file (creating a new sheet), and reloading the sheet to ensure persistence.

---

## Prerequisites

Ensure you have Node.js and npm installed.

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Install Playwright Browsers**:
   ```bash
   npx playwright install chromium
   ```

---

## Configuration

The E2E tests read the application base URL from the `BASE_URL` environment variable.

- **Default URL**: `http://localhost:8888`
- If your Tornado server runs on a different port or IP (e.g., inside Docker at `http://172.18.0.3:8888`), set the environment variable when running the tests.

---

## Running the Tests

You can run the tests using the npm scripts configured in `package.json`:

### 1. Headless Execution (Default)
Runs all tests headlessly in parallel-safe sequential mode.
```bash
# Using the default URL (localhost:8888)
npm run test

# Specifying a custom application URL (e.g. Docker container IP)
BASE_URL=http://172.18.0.3:8888 npm run test
```

### 2. Headed Execution
Runs tests with the browser window visible, useful for debugging.
```bash
BASE_URL=http://172.18.0.3:8888 npm run test:headed
```

### 3. Playwright UI Mode
Launches Playwright's interactive UI runner, providing full time-travel debugging and inspector capabilities.
```bash
BASE_URL=http://172.18.0.3:8888 npm run test:ui
```

### 4. Step-by-step Debugger
Launches tests in debug mode, opening the Playwright Inspector.
```bash
BASE_URL=http://172.18.0.3:8888 npm run test:debug
```

---

## Best Practices Followed

- **No Hardcoded Credentials**: Every test flow uses dynamically generated unique credentials or reads them from test setups to prevent collisions.
- **Sequential Run Mode**: Handlers are configured to run with `workers: 1` in `playwright.config.ts`. Because the application storage (S3/MySQL) has shared state, sequential execution prevents tests from stomping on each other's sheets/directories.
- **UI Dialog Handling**: Handled browser `alert` prompts during saving (`dialog.accept()`) to prevent Playwright from hanging.
- **Page Object Helpers**: Unified helper functions instead of duplicated locator/interaction code, reducing maintenance overhead.
