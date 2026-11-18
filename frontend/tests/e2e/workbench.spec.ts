import { test, expect } from '@playwright/test'

test.beforeEach(async({page})=>{
  await page.goto('/')
  await page.getByLabel('访问口令',{exact:true}).fill(process.env.E2E_PASSWORD || 'ci-browser-owner')
  await page.getByRole('button',{name:'进入工作台',exact:true}).click()
  await expect(page.getByRole('heading',{name:'今天的经营概况'})).toBeVisible()
})

test('data spaces stay explicit and profit handles zero',async({page})=>{
  await page.getByRole('combobox',{name:'数据空间'}).press('ArrowDown')
  await page.getByRole('option',{name:'真实经营',exact:true}).click()
  await expect(page.getByText('仅使用你有权访问并核验的数据')).toBeVisible()
  await page.getByRole('combobox',{name:'数据空间'}).press('ArrowDown')
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
  await dialog.getByLabel('允许的数据类型',{exact:true}).press('ArrowDown')
  await page.getByRole('option',{name:'全部数据类型',exact:true}).click()
  await dialog.getByLabel('使用权限说明',{exact:true}).fill('合成数据自动化演练')
  await dialog.getByRole('button',{name:'保存记录',exact:true}).click()
  await expect(dialog).toBeHidden()
  await page.getByRole('combobox',{name:'已登记的数据来源'}).press('ArrowDown')
  await page.getByRole('option',{name,exact:true}).click()
  const stamp=new Date(Date.now()-60000),end=new Date(stamp);end.setUTCHours(0,0,0,0)
  const start=new Date(end.getTime()-7*86400000)
  const term='CI浏览器关键词-'+Date.now()
  const csv='keyword,category,search_index,listing_count,conversion,window_start,window_end,observed_at\n'+
    `${term},测试,1200,200,0.06,${start.toISOString()},${end.toISOString()},${stamp.toISOString()}\n`+
    `错误比例,测试,1200,200,8,${start.toISOString()},${end.toISOString()},${stamp.toISOString()}\n`
  const filename='browser-e2e-'+Date.now()+'.csv'
  await page.locator('input[type=file]').setInputFiles({name:filename,mimeType:'text/csv',buffer:Buffer.from(csv)})
  await page.getByRole('button',{name:'读取并预览',exact:true}).click()
  await expect(page.getByRole('heading',{name:'检查预览与字段映射'})).toBeVisible()
  await page.getByRole('button',{name:'确认映射并导入',exact:true}).click()
  const row=page.getByRole('row').filter({hasText:filename})
  await expect(row).toContainText('已完成',{timeout:20000})
  await expect(row).toContainText('下载')
  await page.getByRole('link',{name:'关键词研究',exact:true}).click()
  await page.getByPlaceholder('搜索名称、规格或备注').fill(term)
  await page.getByPlaceholder('搜索名称、规格或备注').press('Enter')
  await expect(page.getByRole('cell',{name:term,exact:true})).toBeVisible()
})

test('candidate research exposes score, strategy and excluded prices',async({page})=>{
  await page.getByRole('link',{name:'选品中心',exact:true}).click()
  await page.getByRole('row').filter({hasText:'模型工具整理架 10只装'}).getByRole('button',{name:'研究详情',exact:true}).click()
  const drawer=page.getByRole('dialog')
  await expect(drawer.getByRole('tab',{name:'历史版本'})).toBeVisible()
  await expect(drawer.getByRole('heading',{name:'同规格零售价格带'})).toBeVisible()
  await expect(drawer.getByText('采购/批发价不计入零售价格带')).toBeVisible()
})

test('free discovery clearly disables unconfigured live queries',async({page})=>{
  await page.getByRole('link',{name:'找货与比价',exact:true}).click()
  await expect(page.getByRole('heading',{name:'找货与比价',exact:true})).toBeVisible()
  await expect(page.getByText('四平台自动查询待开通，当前按免费方式使用',{exact:true})).toBeVisible()
  await expect(page.getByRole('link',{name:'前往1688货源 ↗',exact:true})).toHaveAttribute('href',/1688\.com/)
  await page.getByRole('combobox',{name:'数据空间'}).press('ArrowDown')
  await page.getByRole('option',{name:'真实经营',exact:true}).click()
  await expect(page.getByRole('button',{name:'联网查询待开通',exact:true})).toBeDisabled()
  await page.getByRole('button',{name:'平台连接',exact:true}).click()
  await expect(page.getByRole('dialog',{name:'平台数据连接',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'测试连接（最多4次搜索）',exact:true})).toBeDisabled()
})

test('toothbrush discovery preserves unknown costs and saves a demo report',async({page})=>{
  await page.getByRole('link',{name:'找货与比价',exact:true}).click()
  await page.getByLabel('每平台详情数量',{exact:true}).fill('1')
  await page.getByRole('button',{name:'开始模拟分析',exact:true}).click()
  await expect(page.getByRole('heading',{name:'核对规格与成本',exact:true})).toBeVisible({timeout:20000})
  const analyze=page.getByRole('button',{name:'分析所选产品并保存报告',exact:true})
  await expect(analyze).toBeEnabled()
  await analyze.click()
  const report=page.getByTestId('market-report')
  await expect(report).toContainText('费用不完整')
  await expect(report).toContainText('模拟分析结果')
  for(const [name,value] of [
    ['整批进货运费（代发为供应商直发运费）','0'],['每销售包装的额外发货运费','3'],
    ['每单包材与服务费','0.2'],['每单平台费及变动税费','0.5'],['每支付订单广告费','1'],
    ['每次售后净损失（不含广告）','5'],['本批广告预算（用于现金估算）','100'],
  ])await page.getByLabel(name,{exact:true}).fill(value)
  await page.getByLabel('售后损失概率（0–1）',{exact:true}).fill('0.05')
  await page.getByRole('checkbox',{name:'已核对数量、材质、尺寸、品牌型号、币种及所选SKU价格，确认可比',exact:true}).press('Space')
  await page.getByLabel('核验理由与价格依据',{exact:true}).fill('浏览器模拟验收：已核验两支装规格和全部费用')
  await analyze.click()
  await expect(report).toContainText('¥5.59')
  await expect(report).toContainText('人工核验已确认')
  await expect(report.getByRole('button',{name:'保存为研究候选',exact:true})).toBeVisible()
})

test('record public platform quotes and calculate a free comparison',async({page})=>{
  await page.getByRole('link',{name:'找货与比价',exact:true}).click()
  await page.getByRole('combobox',{name:'数据空间'}).press('ArrowDown')
  await page.getByRole('option',{name:'真实经营',exact:true}).click()
  await page.getByRole('button',{name:'录入平台报价',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'录入平台报价',exact:true})
  const entries=[
    {platform:'1688货源',url:'https://detail.1688.com/offer/123.html',price:'2'},
    {platform:'淘宝',url:'https://item.taobao.com/item.htm?id=123',price:'12.9'},
  ]
  for(const entry of entries){
    const quote=dialog.getByRole('region',{name:entry.platform+'报价',exact:true})
    for(const [label,value] of [
      ['商品标题',entry.platform+'浏览器合成牙刷'],['原商品链接',entry.url],['每包装报价（元）',entry.price],
      ['每包装件数','2'],['材质','PBT'],['尺寸或型号规格','180mm'],['报价依据','浏览器验收合成报价，两支装'],
    ])await quote.getByLabel(label,{exact:true}).fill(value)
  }
  await dialog.getByRole('button',{name:'保存报价并开始比较',exact:true}).click()
  await expect(dialog).toBeHidden()
  await page.getByRole('button',{name:'分析所选产品并保存报告',exact:true}).click()
  const report=page.getByTestId('market-report')
  await expect(report).toContainText('人工录入')
  await expect(report).toContainText('¥10.90')
  await expect(report).toContainText('费用不完整')
})
