import { defineConfig, devices } from '@playwright/test';

// The API also serves the built UI, so one server covers both. Storage lives in
// a throwaway directory so a test run never touches a real node's data.
// Not 8000: that is the single most contended port on a dev box, and when
// something else holds it Playwright cannot bind and the whole run dies with a
// bare "Process from config.webServer was not able to start". Worse, with
// reuseExistingServer the suite may silently test *someone else's* server.
const PORT = process.env.CFS_TEST_PORT ?? '8021';
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
