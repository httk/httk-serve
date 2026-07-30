import { defineConfig } from "@playwright/test";

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;

export default defineConfig({
  use: {
    browserName: "chromium",
    // The development container's system Chrome runs without a user namespace.
    // CI uses Playwright's downloaded Chromium and keeps its normal defaults.
    launchOptions: executablePath ? {
      executablePath,
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-v8-sandbox"],
      env: { ...process.env, XDG_CACHE_HOME: "/tmp/httk-serve-playwright/cache", XDG_CONFIG_HOME: "/tmp/httk-serve-playwright/config" },
    } : {},
  },
});
