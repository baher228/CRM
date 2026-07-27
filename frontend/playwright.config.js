import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // The product is a single-operator SQLite workspace. Run the desktop and
  // mobile projects serially so they do not mutate one acceptance database at
  // the same time and create a test-only multi-operator race.
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  reporter: [["list"]],
  outputDir: "../output/playwright/test-results",
  use: {
    baseURL: "http://127.0.0.1:8765",
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "powershell -NoProfile -ExecutionPolicy Bypass -File ../scripts/Start-E2E.ps1",
    url: "http://127.0.0.1:8765/api/v1/health",
    reuseExistingServer: false,
    timeout: 60_000,
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true } },
  ],
});
