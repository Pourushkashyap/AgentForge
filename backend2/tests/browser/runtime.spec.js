const { test, expect } = require("@playwright/test");

const routes = (process.env.APP_ROUTES || "/")
  .split(",")
  .map((route) => route.trim())
  .filter(Boolean);

for (const route of routes) {
  test(`route ${route} loads without runtime errors`, async ({ page }) => {
    const runtimeErrors = [];
    const consoleErrors = [];
    const failedRequests = [];

    page.on("pageerror", (error) => {
      runtimeErrors.push(error.stack || error.message);
    });

    // React logs caught render errors with console.error; failed network
    // resources are reported separately below, so skip that noise here.
    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const text = msg.text();
      if (/Failed to load resource|net::ERR_/i.test(text)) return;
      consoleErrors.push(text);
    });

    page.on("requestfailed", (request) => {
      failedRequests.push({
        url: request.url(),
        error: request.failure()?.errorText,
      });
    });

    const baseURL = process.env.APP_URL || "http://127.0.0.1:4173";

    const response = await page.goto(`${baseURL}${route}`, {
      waitUntil: "networkidle",
      timeout: 30000,
    });

    expect(response, `No response for ${route}`).not.toBeNull();
    expect(response.status(), `HTTP error on ${route}`).toBeLessThan(400);
    await expect(page.locator("body")).toBeVisible();

    // Let effects and data fetching finish rendering.
    await page.waitForTimeout(500);

    // 1. The error boundary in main.jsx marks crashed pages.
    const crash = page.locator("[data-agentforge-crash]");
    if ((await crash.count()) > 0) {
      const crashText = (await crash.first().innerText()).trim();
      runtimeErrors.unshift(`Page crashed while rendering: ${crashText}`);
    }

    // 2. A blank page (nothing mounted in #root) is a failure.
    const rootChildren = await page.evaluate(() => {
      const root = document.getElementById("root");
      return root ? root.childElementCount : -1;
    });
    if (rootChildren <= 0) {
      runtimeErrors.unshift(
        `Blank page: #root has no rendered content on ${route}`
      );
    }

    // Scroll images into view so lazy-loaded images get a chance to load.
    const images = page.locator("img");
    const imageCount = await images.count();

    for (let i = 0; i < imageCount; i++) {
      const image = images.nth(i);

      await image.scrollIntoViewIfNeeded();

      // Wait briefly for loading, but do not hang on broken URLs.
      await image.evaluate((img) => {
        if (img.complete) return Promise.resolve();

        return new Promise((resolve) => {
          const timer = setTimeout(resolve, 8000);

          img.addEventListener(
            "load",
            () => {
              clearTimeout(timer);
              resolve();
            },
            { once: true }
          );

          img.addEventListener(
            "error",
            () => {
              clearTimeout(timer);
              resolve();
            },
            { once: true }
          );
        });
      });
    }

    const imageResults = await images.evaluateAll((imgs) =>
      imgs.map((img) => ({
        src: img.currentSrc || img.src || "",
        alt: img.alt || "",
        loaded: img.complete && img.naturalWidth > 0,
        naturalWidth: img.naturalWidth,
      }))
    );

    const brokenImages = imageResults.filter((image) => !image.loaded);

    console.log(
      `${route} image results:`,
      JSON.stringify(imageResults, null, 2)
    );

    console.log(
      `${route} failed requests:`,
      JSON.stringify(failedRequests, null, 2)
    );

    expect(
      brokenImages,
      `Broken images on ${route}:\n${JSON.stringify(brokenImages, null, 2)}`
    ).toEqual([]);

    expect(
      [...runtimeErrors, ...consoleErrors],
      `Runtime errors on ${route}:\n${[...runtimeErrors, ...consoleErrors].join("\n")}`
    ).toEqual([]);
  });
}
