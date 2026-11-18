import { defineConfig } from '@playwright/test'
import path from 'node:path'
export default defineConfig({
  testDir:'./tests/e2e', workers:1, timeout:45000, retries:process.env.CI?1:0,
  use:{baseURL:'http://127.0.0.1:8000',headless:true,trace:'retain-on-failure',screenshot:'only-on-failure'},
  webServer:{
    command:'python -m alembic upgrade head && python -m app.seed && (python -m app.worker & exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000)',
    cwd:path.resolve(import.meta.dirname,'../backend'),url:'http://127.0.0.1:8000/api/health',
    env:{OWNER_PASSWORD:'ci-browser-owner'},reuseExistingServer:!process.env.CI,timeout:60000,
  },
})
