import { test } from '@playwright/test';

test.describe('鐧诲綍+浠〃鐩樿瑙夊洖褰?, () => {
  let eyes: any;

  test.beforeEach(async ({ page }) => {
    const { Eyes, Configuration } = await import('@applitools/eyes-playwright');
    const config = new Configuration();
    config.setApiKey(process.env.APPLITOOLS_API_KEY!);
    config.setServerUrl('https://eyes.applitools.com');
    config.setAppName('蓝鉴 lanjian');
    config.setViewportSize({ width: 1280, height: 720 });

    eyes = new Eyes();
    eyes.setConfiguration(config);
    await eyes.open(page, '蓝鉴 lanjian', '鐧诲綍娴佺▼瑙嗚鍥炲綊');
  });

  test.afterEach(async () => {
    try { await eyes.close(); } catch { await eyes.abort(); }
  });

  test('瀹屾暣鐧诲綍娴佺▼', async ({ page }) => {
    await page.goto('/login');
    await page.waitForSelector('form');
    await page.fill('#username', 'admin');
    await page.fill('#password', '123456789');
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.waitForLoadState('networkidle');
    const screenshot = await page.screenshot({ type: 'png' });
    await eyes.checkImage(screenshot, '浠〃鐩榑鐧诲綍鍚?);
  });
});
