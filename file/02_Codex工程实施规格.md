# AI电商智能选品系统 · Codex 工程实施规格 V1.0

> 与 01 总体设计方案配套。此文件是建议的开发基线，不是已经实现的产品。首版单店、人工审核、授权数据导入。所有样例均为模拟。日期 2026-11-17。

## 1. 项目约束与目录

- Python 3.12 / FastAPI / Pydantic v2 / SQLAlchemy 2 / Alembic / PostgreSQL 16；Vue 3、TypeScript、Element Plus、ECharts；使用 Docker Compose。本地先验证；前端依赖版本实施时写入锁文件。
- 金额使用 `Decimal` / `numeric(18,4)`，API 金额采用十进制字符串，UI显示2位。比例为 0–1 小数，时间储存带时区，业务统计按 Asia/Shanghai；窗口统一 `[start,end)`。
- 首版禁止自动采购、支付、发布、刷量或绕过平台访问限制。外部接口未获授权时保留空适配器。所有演示使用 fixture，真实数据不得与模拟数据混合。

```text
commerce-intelligence/
  AGENTS.md
  README.md
  compose.yaml
  .env.example
  backend/
    pyproject.toml
    app/
      main.py                # FastAPI应用工厂
      core/{config,auth,errors,logging}.py
      db/{base,session,models}.py
      domains/
        ingestion/           # adapters, schema, validate, import_service
        market/              # keywords, snapshot, comparison
        catalog/             # spu, sku, matching
        sourcing/            # supplier, offer, sample
        economics/          # profit, scenarios, cashflow
        selection/          # strategy, normalize, score
        risk/               # gates, review
        experiments/        # funnel, order_ledger, review
        ai/                 # providers, prompts, schema, evidence
      api/v1/                # routes by domain
      worker/                # job claim/retry/heartbeat
    migrations/
    tests/{unit,integration,fixtures}/
  frontend/
    src/{api,router,stores,views,components,types}/
    tests/e2e/
  docs/                     # 本交付文档、ADR、字典
  scripts/                  # seed_demo / backup / restore
```

**依赖方向**：路由→应用服务→领域纯函数/仓储接口→基础设施；领域层不得调用HTTP或LLM。AI只能通过白名单读取快照、调用经济性计算服务；所有采购发布相关工具不注册。

## 2. 功能需求与验收

| ID | 用户故事 | 验收条件 |
|---|---|---|
| FR-01 | 上传授权数据文件并映射列 | 预览20行、单位与时间窗口确认、错误行下载；重复相同哈希及映射不重复写入 |
| FR-02 | 筛选关键词机会 | 可设置策略和阈值，显示命中/未命中/未知；零分母不报错、不产生无穷大 |
| FR-03 | 研究商品及跨平台价格 | 同规格匹配确认后才计入统计；不同套装数、材质冲突被拒；显示价格时效、区域与优惠条件 |
| FR-04 | 管理1688货源 | 可登记至少两家备选，确认SKU、代发价、运费、时效、退换货及证据；未核验不可进入采购建议 |
| FR-05 | 计算贡献利润 | 参数化正常/压力情景；支持 r=0、r=1；明确全退损失和广告，返回盈亏平衡CAC |
| FR-06 | 评分并排序候选 | 显示七因子、缺失字段、权重版本和输入快照；硬闸阻断优先于高分 |
| FR-07 | 样品及合规审核 | 审核证据、结果、人员、时间；样品失败禁止测款；状态变更记录 |
| FR-08 | 测款与复盘 | 计划预算、点击/曝光/订单/退款、成熟订单贡献；用户人工选择继续/暂停/放量 |
| FR-09 | AI证据报告（P1） | JSON Schema、证据ID与数字校验；无数据返回不足；超时可降级，不影响规则系统 |
| FR-10 | 数据出口 | CSV/XLSX导出候选、筛选条件、来源、时间、置信度；对公式注入字符进行转义 |

## 3. 数据合同与接入流程

**接入先验任务**：实际登录用户有权访问的商家后台、采购助手；采集20行真实导出样本，确认数据字段、日期口径、能否导出、权限范围与费用；形成证据清单后再实现平台 adapter。接口文档不等于账号授权。

| 对象 | 必需数据 | 不可假定 / 例外 |
|---|---|---|
| keyword_snapshot | keyword/category/source/period/observed_at/search_index/listing_count | 搜索人气是指数或人数需单列；转化率/商城占比可空 |
| listing_snapshot | platform/listing_id/sku_id/observed_at/price_kind/price/qty/region | SKU未知标记待核验；券价有条件；价格仅快照 |
| metric_observation | entity_type/entity_id/metric_kind/unit/window/raw/precision/source/evidence | 已售、已拼、评价、估算销量不混同；“1000+”保留为 lower_bound |
| supplier_offer | supplier/sku/代发价/运费/region/quantity/quote_time | 阶梯价不是一件代发价；不支持代发标明 |
| review | listing/文本/采样时间/去重哈希/来源 | 不保留头像、昵称、手机号等；缺失的评价不得合成 |
| order_ledger | 外部脱敏订单号/sku/支付日/收入/采购/运费/平台费/广告分配/退款/回收额/结算标记 | 订单去重；退款按支付订单归属；只导入必要数据 |

### 导入过程
1. 创建来源（权限范围、允许的数据类型、有效期）；上传文件，限制 20 MB、10万行（初始可配置），禁止宏或执行公式。
2. 按 SHA256 与映射版本查幂等记录；解析、规范化编码、列别名、比例、金额、日期、SKU键。
3. 预览及逐行错误：`REQUIRED_MISSING / INVALID_UNIT / INVALID_WINDOW / DUPLICATE_KEY / SOURCE_NOT_AUTHORIZED`。关键字段缺失不伪造；可允许部分成功，报告 accepted/rejected 数。
4. 写入 staging 并事务提交，记录 row_number、source、raw_hash；相同快照出现不同值时保留修订版，不覆写旧结果。
5. 刷新候选计算为显式任务；失败保留上次快照，提示已过期。

### 数据优先级与口径
- 第一方授权数据 > 官方导出 > 人工核验公开信息 > 合规第三方估算。此排序不替代字段权限审核。
- `precision`：exact/lower_bound/range/estimate/unknown；`quality_flag`：valid/stale/conflict/missing/rejected。价格默认24小时、市场7日快照默认48小时、人工报价以有效期为准；均可配置。过期不能作为 READY_TEST 审批依据。
- 任何趋势计算必须相同平台、类目、来源、指标定义、同长度且不重叠的完整窗口；比较样本不够时返回 unknown。历史补录按 observed_at 和 event window 区分。

## 4. 策略与评分实现

参考 `strategy_config.yaml`，策略使用三值逻辑（PASS/FAIL/UNKNOWN），筛选器允许 AND/OR。入池条件为任一策略 PASS；UNKNOWN 保留待核验但不可进入推荐队列。禁止把蓝海比解释为真实供需比例。

- `opportunity = search_index / listing_count`：分母>0且同窗口，否则 None。
- `growth = (current - previous)/previous`：前值>0、窗口相容且样本足够。
- `head_share = sum(top5 comparable sales_signal)/sum(all comparable sales_signal)`；只对同口径精确值或明确可比数据，样本建议≥10且总量>0；否则 None。下界值不可直接求精确集中度。
- `percentile(x) = 100*(rank_avg(x)-1)/(n-1)`，平值用平均名次；n>=30，匹配 category/source/window；反向 100−p。n<30 用配置的低/高阈值线性截断，标 low confidence；没有配置则 unknown。
- 评分七维：需求20、竞争15、利润25、供应链15、增长10、售后10、差异化5；细分权重见主方案。每个因子必须有全部必要子项才为有效。分数用 Decimal 汇总。
- `partial = Σ(known_weight * score/100)`；`coverage = Σknown_weight/100`；未知部分的可能上界 `partial + (100 - Σknown_weight)`。coverage<0.8 或关键缺失：NEEDS_EVIDENCE；coverage≥0.8但不完整：仅展示区间、不出正式等级。完整且证据通过才给等级；S>=85/A>=75/B>=65/C>=50/D<50。
- 硬性闸门：已确认侵权、资质不符、样品失败、不可履约、压力情景亏损 → BLOCKED。缺证据 → NEEDS_EVIDENCE。已确认的严重风险不可被数值或AI覆盖。
- 数据置信度独立于机会分。记录各证据来源等级、时效、匹配确认、样本覆盖；低置信或过期不允许 READY_TEST。

## 5. 同款匹配与比价

1. 标准化标题（去促销词但保留规格），单位转换 mm/cm/g/kg，提取品牌、型号、材质、尺寸、数量、包装。
2. 文本倒排 + 图像相似候选；首版人工录入或工具导入，不需要向量数据库。
3. 必需属性按类目配置；硬冲突直接 REJECTED，缺属性 PENDING。建议匹配权重：规格40、型号/条码25、材质15、图像10、标题10；不适用型号时由类目配置，不自动重分配缺失证据。机器分≥85仅提示优先人工确认。
4. 人工确认 MATCHED，保留审阅者和证据。相似但不同款可标 ALTERNATIVE，不能参与严格比价。
5. 只聚合相同 canonical_sku_id、币种/地区/数量、相同可得价格口径和有效报价；统计 count, min, p25, median, p75, max；<5条标 sparse。每个链接只保留最近有效报价，避免重复。

## 6. 利润与现金流

所有参数以每支付订单为单位：`P,C,F,B,T,A,r,L` 定义见主方案。保留订单贡献 `M=P-C-F-B-T`；`E=(1-r)*M-r*L-A`；`max_cac=(1-r)*M-r*L`。不重复扣减退款损失；L 应包含退款订单无法收回的净支出，不含广告。r∈[0,1]，成本≥0。贡献率=E/P（P>0）。`Amax<0` 直接不可投放。固定成本另做周期汇总。

| 测试输入 | 期望 E |
|---|---:|
| P39.9 C15 F4 B1 T1 A6 r0.08 L8 | 10.748 → 10.75 |
| P34.9 其余同上 | 6.148 → 6.15 |
| P39.9 r0.15 L10 A6 其余同上 | 8.565 → 8.57（ROUND_HALF_UP） |
| P34.9 r0.15 L10 A10 其余同上 | 0.315 → 0.32 |
| r=0, P39.9, A6 | 12.90 |
| r=1, L8, A6 | -14.00 |

真实账本贡献 = 买家实付及归属补贴 − 客户退款 − 采购支出 + 采购退款 − 出库及退货运费 − 包材/服务费 − 净平台及变动税费 − 广告 − 其他净变动费用。仅对已成熟订单计算最终标签；未成熟单另列，广告分配策略写入版本。现金需求以结算延迟和冻结风险计算，不以收入替代余额。

## 7. 数据库、API与任务

- `schema.sql` 是可审查 PostgreSQL DDL 草案，开发时以 Alembic 迁移维护。关键表分层：来源/导入/证据；关键词与商品/SKU/报价快照；供应商、样品；候选、评分、审核；实验与订单账本；任务与审计。
- `openapi.yaml` 为内部 REST 核心契约（不是淘宝/1688接口）。未覆盖的页面功能按 FR 扩展契约并审查。所有可写路由需身份权限、审计；候选状态迁移事务内校验 `expected_version`，冲突 HTTP 409。
- Job：QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED；worker 使用 `FOR UPDATE SKIP LOCKED` 领取，lease+heartbeat，超时重领；限3次指数退避，可重复执行但不重复产生业务结果。请求使用幂等键。
- 不记录 token、客户联系方式；上传路径随机生成；拒绝路径穿越、超大文件、外部任意 URL 访问；外部接口 host allowlist、超时和配额；SaaS阶段再加租户隔离，首版只绑定本地或经鉴权内网。

## 8. AI结构化协议（P1）

```json
{
  "candidate_id": "demo-001",
  "decision": "NEEDS_EVIDENCE",
  "confidence": "LOW",
  "summary": "样品和运费尚未核验",
  "evidence_ids": ["evidence-demo-1"],
  "missing_fields": ["shipping_quote", "sample_result"],
  "risks": [{"code": "SUPPLY_UNVERIFIED", "severity": "HIGH", "evidence_ids": ["evidence-demo-1"]}],
  "next_actions": ["联系供应商确认运费", "购买样品"],
  "model_version": "provider/model-version",
  "prompt_version": "research-v1"
}
```

约束：decision 枚举 RESEARCH / NEEDS_EVIDENCE / BLOCKED / READY_SAMPLE / HOLD；confidence LOW/MEDIUM/HIGH；证据ID必须属于输入快照；未发现证据时禁止宣称事实。AI不得输出或修改分数、利润、审批结果；可以引用程序返回的数据。对外部评论的指令忽略，禁止任意工具调用；最大文本长度、token、单日预算及超时可配置。模型失败时显示规则报告，不自动重试无限次。

## 9. 性能、部署与质量

初始目标：10万快照行的批次导入在10分钟内（需本地基准验证）；候选列表常规分页95%响应<1秒（固定数据集/硬件说明）；任务进度可轮询；数据库索引覆盖查询路径。每晚备份，至少每月验证恢复；日志结构化包含 request_id/job_id，不含个人信息。密钥使用环境变量或秘密管理，仓库只放 `.env.example`。CI运行 lint、类型检查、pytest、迁移 smoke、前端 E2E。先模拟数据全链路，再使用合法真实数据验收。

## 10. 优先级与实施顺序

先执行 `03_运营SOP与开发任务.md` 的 D00–D02。每一任务独立分支或小PR；测试通过并由人审阅再进入下一任务。禁止Codex在未验证权限时发明平台接口或接入不可用服务。技术版本、评分参数变更用ADR记录并保存旧版快照。
