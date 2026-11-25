/**
 * Playwright: o e2e do sinal de presença (TSK-1520) e o do Casais de Alta
 * Performance (TSK-1622), que precisam do backend, do front e do Conecta falso de
 * pé. Quem sobe tudo é o `rodar-e2e.sh` (o `rodar-presenca.sh` roda só o da
 * presença); rodar `playwright test` sozinho não acha nada no ar. Não entra no CI.
 *
 * 1.64.1 fixa o Chromium 1248 (`npx playwright install chromium`).
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.mjs$/,
  workers: 1,
  timeout: 120 * 1000,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.E2E_URL,
    viewport: { width: 1440, height: 900 },
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
  },
});
