import { defineConfig } from '@playwright/test'
import path from 'node:path'

const database = process.env.DATABASE_URL
if (!database || !/\/\w+_test$/.test(database.split('?')[0])) {
  throw new Error('Playwright requires DATABASE_URL pointing to an isolated _test database.')
}
const baseURL = 'http://127.0.0.1:8010'

export default defineConfig({
  testDir:'./tests/e2e', workers:1, timeout:45000, retries:process.env.CI?1:0, reporter:'list',
  use:{baseURL,headless:true,trace:'retain-on-failure',screenshot:'only-on-failure',
    launchOptions:{executablePath:process.env.E2E_CHROMIUM}},
  webServer:{
    command:'python -m alembic upgrade head && python -m app.seed && (python -m app.worker & exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8010)',
    cwd:path.resolve(import.meta.dirname,'../backend'),url:baseURL+'/api/health',
    env:{OWNER_PASSWORD:'ci-browser-owner'},reuseExistingServer:false,timeout:60000,
  },
})
