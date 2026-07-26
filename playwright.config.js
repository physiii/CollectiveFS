import { defineConfig, devices } from '@playwright/test';

// The API also serves the built UI, so one server covers both. Storage lives in
// a throwaway directory so a test run never touches a real node's data.
const PORT = process.env.CFS_TEST_PORT ?? '8000';
const STORAGE = process.env.CFS_TEST_STORAGE ?? '.pw-collective';
const UVICORN = process.env.CFS_UVICORN ?? '.venv/bin/uvicorn';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [['html', { outputFolder: 'tests/e2e/report', open: 'never' }], ['line']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
  ],
  webServer: {
    command: `${UVICORN} api.main:app --host 0.0.0.0 --port ${PORT}`,
    url: `http://localhost:${PORT}/api/health`,
    env: { COLLECTIVE_PATH: STORAGE },
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
});
