# 项目 Skill 功能总结

## 结论

- Skill 总数：`46`
- 类别数：`9`
- 注册方式：通过 `server/src/skills/registry.py` 汇总各模块 `ALL_SKILLS`

## 分类统计

| 类别 | 模块文件 | 数量 |
|---|---|---:|
| 运营（ops） | `server/src/skills/ops.py` | 7 |
| 数据分析（data） | `server/src/skills/data_analysis.py` | 6 |
| 客服（service） | `server/src/skills/customer_service.py` | 5 |
| 设计（design） | `server/src/skills/design.py` | 5 |
| 财务（accounting） | `server/src/skills/accounting.py` | 5 |
| 工程（engineering） | `server/src/skills/engineering.py` | 4 |
| 网站增长（web） | `server/src/skills/web.py` | 5 |
| 创意内容（creative） | `server/src/skills/creative.py` | 6 |
| 协同编排（coordination） | `server/src/skills/coordination.py` | 3 |

## 各 Skill 功能清单

### 1) 运营（ops）- 7 个

- `ops_promo_planning`（促销策划）：根据商品、预算和平台，生成促销活动方案，包含阶段划分、KPI 和预算分配。
- `ops_pricing_strategy`（定价策略）：根据成本、竞品价格和平台，输出建议定价、利润率及竞品对比。
- `ops_channel_strategy`（渠道策略）：根据商品类型、预算和目标人群，输出平台推荐和内容组合。
- `ops_inventory_planning`（库存规划）：根据日均销量和供货周期，计算安全库存、补货点和建议订货量。
- `ops_listing_copy`（上架文案）：根据商品名称、卖点和平台，生成标题、五点描述和关键词。
- `ops_assortment_planning`（选品规划）：根据类目和预算，输出商品组合推荐。
- `ops_execution_plan`（运营执行计划）：根据运营目标和阶段，输出 AARRR 指标、行动项和风险规则。

### 2) 数据分析（data）- 6 个

- `data_funnel_analysis`（漏斗分析）：分析电商转化漏斗各阶段转化率，定位流失环节。
- `data_anomaly_diagnosis`（异常诊断）：对指标序列做异常检测，输出 Z-Score、异常点和可能原因。
- `data_trend_forecast`（趋势预测）：基于历史序列计算增长率并预测未来趋势。
- `data_customer_segmentation`（客户分层）：基于 RFM 模型进行客户分层分析。
- `data_competitor_analysis`（竞品分析）：输入竞品信息，输出对比矩阵和策略建议。
- `data_dashboard`（数据看板）：生成店铺核心经营指标看板。

### 3) 客服（service）- 5 个

- `service_ticket_handler`（工单处理）：按问题类型和紧急程度生成工单处理方案与话术。
- `service_faq_playbook`（FAQ 话术库）：按常见问题关键词返回标准话术和处理指引。
- `service_escalation_flow`（升级流程）：按问题严重度输出分级升级处理流程。
- `service_dsr_improvement`（DSR 提升方案）：按当前 DSR 评分输出维度提升建议。
- `service_return_handler`（退换货处理）：按退换货原因和订单信息输出处理方案及运费承担方。

### 4) 设计（design）- 5 个

- `design_main_image`（主图设计方案）：按商品类型和平台要求输出主图规范及创意方案。
- `design_detail_page`（详情页策划）：按商品信息策划详情页结构和内容框架。
- `design_color_scheme`（配色方案）：按品牌调性和品类推荐配色方案。
- `design_material_spec`（素材规范）：按用途输出各平台素材尺寸与格式规范。
- `design_campaign_poster`（活动海报设计）：按活动信息输出海报设计方案和文案框架。

### 5) 财务（accounting）- 5 个

- `accounting_cost_calc`（成本核算）：按成本明细计算单品综合成本和成本结构。
- `accounting_profit_analysis`（利润分析）：按营收与成本分析利润构成和趋势。
- `accounting_budget_plan`（预算编制）：按目标 GMV 与利润率要求编制月度预算。
- `accounting_roi_calc`（ROI 计算）：计算投放或项目 ROI 及相关指标。
- `accounting_compliance_check`（合规检查）：检查财务数据合规性与风险点。

### 6) 工程（engineering）- 4 个

- `engineering_arch_review`（架构评估）：评估系统架构健康度并给出优化建议。
- `engineering_bug_analysis`（Bug 排查方案）：按错误描述生成排查思路和检查清单。
- `engineering_perf_optimize`（性能优化建议）：按瓶颈类型输出分层优化方案。
- `engineering_tech_selection`（技术选型）：按业务需求推荐技术方案并做对比。

### 7) 网站增长（web）- 5 个

- `web_seo_optimize`（SEO 优化方案）：按页面信息和关键词输出 SEO 优化方案。
- `web_title_generator`（标题生成）：按商品信息生成多风格 SEO 友好标题。
- `web_page_conversion`（页面转化优化）：分析页面转化瓶颈并输出优化建议。
- `web_store_design`（店铺装修方案）：按店铺定位和平台输出装修框架及模块设计。
- `web_keyword_research`（关键词研究）：按种子词拓展关键词并分析搜索意图和竞争度。

### 8) 创意内容（creative）- 6 个

- `creative_video_script`（短视频脚本）：按商品和平台特点生成短视频脚本框架。
- `creative_seeding_copy`（种草文案）：生成小红书/社交媒体种草文案框架。
- `creative_ip_branding`（IP 定位）：为品牌或个人账号做 IP 人设定位。
- `creative_content_calendar`（内容排期）：按运营节奏生成周/月内容发布计划。
- `creative_trend_catch`（趋势捕捉）：分析内容趋势并给出可借鉴创意方向。
- `creative_live_script`（直播脚本）：按直播时长和商品数生成直播流程脚本。

### 9) 协同编排（coordination）- 3 个

- `coordination_agent_handoff`（Agent 交接）：根据任务需求确定交接目标 Agent 及交接信息。
- `coordination_task_orchestration`（任务编排）：将复杂需求拆分为多 Agent 子任务并确定执行顺序。
- `coordination_status_query`（状态查询）：查询系统各 Agent 和任务运行状态。

## 说明

- 本总结基于 `server/src/skills/*.py` 中各 Skill 的 `name`、`display_name`、`description` 字段整理。
- 实际对外可用工具集合由 `server/src/skills/registry.py` 在运行时统一注册。
