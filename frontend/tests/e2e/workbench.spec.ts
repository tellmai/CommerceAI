import { test, expect } from '@playwright/test'

test.beforeEach(async({page})=>{
  await page.goto('/')
  await page.getByLabel('访问口令',{exact:true}).fill(process.env.E2E_PASSWORD || 'ci-browser-owner')
  await page.getByRole('button',{name:'进入工作台',exact:true}).click()
  await expect(page.getByRole('heading',{name:'今天的经营概况'})).toBeVisible()
})

test('data spaces stay explicit and profit handles zero',async({page})=>{
  await page.getByRole('combobox',{name:'数据空间'}).click()
  await page.getByRole('option',{name:'真实经营',exact:true}).click()
  await expect(page.getByText('仅使用你有权访问并核验的数据')).toBeVisible()
  await page.getByRole('combobox',{name:'数据空间'}).click()
  await page.getByRole('option',{name:'模拟演示',exact:true}).click()
  await page.getByRole('link',{name:'利润测算',exact:true}).click()
  await expect(page.locator('.hero-number')).toContainText('10.75')
  await page.getByLabel('实付收入 P',{exact:true}).fill('0')
  await expect(page.getByText('贡献率',{exact:true}).locator('..')).toContainText('—')
  await expect(page.locator('.hero-number')).not.toContainText('NaN')
})

test('preview mapping, one valid row and one rejected row',async({page})=>{
  await page.getByRole('link',{name:'数据中心',exact:true}).click()
  await page.getByRole('button',{name:'登记数据来源',exact:true}).click()
  const dialog=page.getByRole('dialog')
  const name='CI浏览器来源-'+Date.now()
  await dialog.getByLabel('名称',{exact:true}).fill(name)
  await dialog.getByLabel('平台',{exact:true}).fill('TEST')
  await dialog.getByLabel('允许的数据类型',{exact:true}).click()
  await page.getByRole('option',{name:'全部数据类型',exact:true}).click()
  await dialog.getByLabel('使用权限说明',{exact:true}).fill('合成数据自动化演练')
  await dialog.getByRole('button',{name:'保存记录',exact:true}).click()
  await expect(dialog).toBeHidden()
  await page.getByRole('combobox',{name:'已登记的数据来源'}).click()
  await page.getByRole('option',{name,exact:true}).click()
  const stamp=new Date(Date.now()-60000),end=new Date(stamp);end.setUTCHours(0,0,0,0)
  const start=new Date(end.getTime()-7*86400000)
  const term='CI浏览器关键词-'+Date.now()
  const csv='keyword,category,search_index,listing_count,conversion,window_start,window_end,observed_at\n'+
    `${term},测试,1200,200,0.06,${start.toISOString()},${end.toISOString()},${stamp.toISOString()}\n`+
    `错误比例,测试,1200,200,8,${start.toISOString()},${end.toISOString()},${stamp.toISOString()}\n`
  await page.locator('input[type=file]').setInputFiles({name:'browser-e2e.csv',mimeType:'text/csv',buffer:Buffer.from(csv)})
  await page.getByRole('button',{name:'读取并预览',exact:true}).click()
  await expect(page.getByRole('heading',{name:'检查预览与字段映射'})).toBeVisible()
  await page.getByRole('button',{name:'确认映射并导入',exact:true}).click()
  const row=page.getByRole('row').filter({hasText:'browser-e2e.csv'})
  await expect(row).toContainText('已完成',{timeout:20000})
  await expect(row).toContainText('下载')
  await page.getByRole('link',{name:'关键词研究',exact:true}).click()
  await page.getByPlaceholder('搜索名称、规格或备注').fill(term)
  await page.getByPlaceholder('搜索名称、规格或备注').press('Enter')
  await expect(page.getByRole('cell',{name:term,exact:true})).toBeVisible()
})

test('candidate research exposes score, strategy and excluded prices',async({page})=>{
  await page.getByRole('link',{name:'选品中心',exact:true}).click()
  await page.getByRole('button',{name:'研究详情',exact:true}).first().click()
  const drawer=page.getByRole('dialog')
  await expect(drawer.getByRole('tab',{name:'历史版本'})).toBeVisible()
  await expect(drawer.getByRole('heading',{name:'同规格零售价格带'})).toBeVisible()
  await expect(drawer.getByText('采购/批发价不计入零售价格带')).toBeVisible()
})
