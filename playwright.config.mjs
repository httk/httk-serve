import { defineConfig } from "@playwright/test";

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
// CI runs Chromium; HTTK_SERVE_BROWSER=firefox selects Firefox locally.
const browserName = process.env.HTTK_SERVE_BROWSER ?? "chromium";

function launchOptions() {
  // The development container has no user namespace: Firefox's content sandbox crashes there.
  if (browserName === "firefox") return { env: { ...process.env, MOZ_DISABLE_CONTENT_SANDBOX: "1" } };
  // The development container's system Chrome runs without a user namespace.
  // CI uses Playwright's downloaded Chromium and keeps its normal defaults.
  return executablePath ? {
    executablePath,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-v8-sandbox"],
    env: { ...process.env, XDG_CACHE_HOME: "/tmp/httk-serve-playwright/cache", XDG_CONFIG_HOME: "/tmp/httk-serve-playwright/config" },
  } : {};
}

export default defineConfig({
  use: { browserName, launchOptions: launchOptions() },
});
