import { defineConfig, devices } from '@playwright/test';

/**
 * Golden flows for the Overview.
 *
 * The suite serves recorded API payloads rather than standing the stack up, so
 * it needs only a built frontend: no database, no engine, no Python. That is
 * what lets it run in CI on every pull request instead of on a laptop with a
 * prepared workspace. The payloads are generated from `OverviewResponse` and
 * re-validated by `backend/tests/test_overview_e2e_fixtures.py`, which is what
 * stops them drifting into fiction.
 *
 * Desktop only, deliberately. This is an operations console and the flows being
 * protected are the ones a Data Engineer runs at a desk.
 */
export default defineConfig({
  testDir: './e2e',
  // A golden flow that needs a retry is a golden flow that is lying.
  retries: 0,
  fullyParallel: true,
  timeout: 30_000,
  expect: { timeout: 7_000 },
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:3251',
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'] } }],
  // This suite always starts and owns its server, and on a port nothing else
  // in the workspace uses. Reusing whatever answered the port cost an hour
  // once: a sibling project was serving 3210, Playwright adopted it, and four
  // tests failed against an application this repository does not contain.
  webServer: process.env.E2E_BASE_URL ? undefined : {
    // The standalone server, which is what the image runs. `next start`
    // warns that it does not support `output: standalone`, and testing a
    // server the product never uses is how an E2E suite passes on an
    // artifact nobody ships.
    command: 'npm run e2e:server',
    env: { PORT: '3251', HOSTNAME: '127.0.0.1' },
    url: 'http://127.0.0.1:3251/login',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
