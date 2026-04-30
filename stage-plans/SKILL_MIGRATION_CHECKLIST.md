# Skill Migration Checklist

Scope: port old Python `SkillBase` skills into Rust skill modules without wiring them into role registry, tool routing, or runtime execution yet.

## Current Boundary

- [x] Inventory old built-in skills from `old_code/server/src/skills`.
- [x] Preserve old skill names, display names, categories, and JSON input fields.
- [x] Add Rust skill specs and deterministic first-pass executors for the first P0 batch.
- [ ] Keep registry integration out of this stage.
- [x] Keep LLM/search/DB/platform side effects represented as deferred dependencies unless explicitly implemented.

## Migration Strategy

- `P0`: deterministic or mostly local computation; good first Rust ports.
- `P1`: deterministic shell with optional DB/LLM/search context; can return structured fallback now.
- `P2`: requires real external integration, approval, or orchestration; spec first, executor later.

## Checklist By Category

### Ops (11)

- [ ] `ops_promo_planning` - 促销策划 - P1 - required: `product`, `budget`, `platform`
- [x] `ops_pricing_strategy` - 定价策略 - P0 - required: `cost`, `competitor_prices`
- [ ] `ops_channel_strategy` - 渠道策略 - P1 - required: `product_type`, `budget`
- [x] `ops_inventory_planning` - 库存规划 - P0 - required: `avg_daily_demand`, `lead_time_days`
- [ ] `ops_listing_copy` - 上架文案 - P1 - required: none
- [ ] `ops_assortment_planning` - 选品规划 - P1 - required: `category`, `budget`
- [ ] `ops_execution_plan` - 运营执行计划 - P1 - required: `goal`, `stage`
- [x] `ops_smart_pricing` - 智能定价引擎 - P0 - required: `cost`, `current_price`
- [x] `ops_inventory_optimizer` - 库存优化计算 - P0 - required: none
- [x] `ops_ad_fatigue_detector` - 广告疲劳检测 - P0 - required: none
- [x] `ops_abc_xyz_classifier` - ABC-XYZ库存分类 - P0 - required: none

### Data (18)

- [ ] `query_store_metrics` - 查询店铺指标 - P1 - required: none
- [ ] `data_funnel_analysis` - 漏斗分析 - P0 - required: none
- [ ] `data_anomaly_diagnosis` - 异常诊断 - P0 - required: none
- [ ] `data_trend_forecast` - 趋势预测 - P0 - required: none
- [ ] `data_dashboard` - 数据看板 - P1 - required: none
- [ ] `data_customer_segmentation` - RFM客户分层 - P0 - required: none
- [ ] `data_competitor_analysis` - 竞品分析 - P0 - required: `our_product`
- [ ] `data_multi_period_trend` - 多周期趋势分析 - P0 - required: none
- [ ] `data_channel_roi` - 渠道ROI归因 - P1 - required: none
- [ ] `data_ltv_calculator` - LTV客户价值计算 - P0 - required: none
- [ ] `data_cohort_analysis` - 队列留存分析 - P0 - required: none
- [ ] `data_ab_test_analyzer` - A/B测试分析 - P0 - required: `control_visitors`, `control_conversions`, `test_visitors`, `test_conversions`
- [ ] `data_attribution_analysis` - 渠道归因分析 - P1 - required: none
- [ ] `data_refund_decomposition` - 退款帕累托分析 - P1 - required: none
- [ ] `data_seasonal_decompose` - 季节性趋势分解 - P0 - required: none
- [ ] `data_price_elasticity` - 价格弹性分析 - P0 - required: none
- [ ] `data_demand_forecast` - 需求预测 - P0 - required: none
- [ ] `data_comprehensive_diagnosis` - 综合业务诊断 - P2 - required: none

### Accounting (12)

- [x] `accounting_cost_calc` - 成本核算 - P0 - required: `purchase_cost`, `selling_price`
- [x] `accounting_profit_analysis` - 利润分析 - P0 - required: none
- [ ] `accounting_budget_plan` - 预算编制 - P0 - required: none
- [x] `accounting_roi_calc` - ROI计算 - P0 - required: none
- [ ] `accounting_compliance_check` - 合规检查 - P0 - required: none
- [ ] `accounting_pl_statement` - 完整利润表 - P0 - required: none
- [x] `accounting_break_even_calc` - 盈亏平衡分析 - P0 - required: none
- [ ] `accounting_cash_flow_forecast` - 现金流预测 - P1 - required: none
- [ ] `accounting_financial_narrative` - 财务健康诊断 - P1 - required: none
- [x] `accounting_budget_vs_actual` - 预算vs实际分析 - P0 - required: none
- [x] `accounting_gmv_waterfall` - GMV利润瀑布分析 - P0 - required: none
- [x] `accounting_scenario_analysis` - 情景财务分析 - P0 - required: `base_gmv`

### Search (6)

- [x] `search_trends` - 市场趋势搜索 - P2 - required: `query`
- [x] `search_competitor` - 竞品信息搜索 - P2 - required: `query`
- [x] `search_market_info` - 市场行情搜索 - P2 - required: `query`
- [x] `search_product_reviews` - 用户评价搜索 - P2 - required: `query`
- [x] `search_platform_policy` - 平台政策搜索 - P2 - required: `platform`
- [x] `search_industry_benchmarks` - 行业基准搜索 - P2 - required: `industry`

### Creative (8)

- [ ] `creative_video_script` - 短视频脚本 - P1 - required: none
- [ ] `creative_seeding_copy` - 种草文案 - P1 - required: none
- [ ] `creative_ip_branding` - IP定位 - P1 - required: `brand_name`, `category`
- [x] `creative_content_calendar` - 内容排期 - P0 - required: none
- [ ] `creative_trend_catch` - 趋势捕捉 - P1 - required: `category`
- [ ] `creative_live_script` - 直播脚本 - P1 - required: `duration_hours`
- [ ] `creative_product_article` - 产品软文 - P1 - required: none
- [x] `creative_title_ctr_scorer` - 标题CTR预测评分 - P0 - required: none

### Service (9)

- [ ] `service_ticket_handler` - 工单处理 - P1 - required: `issue_type`
- [ ] `service_faq_playbook` - FAQ话术库 - P1 - required: none
- [ ] `service_escalation_flow` - 升级流程 - P1 - required: `issue_description`
- [ ] `service_dsr_improvement` - DSR提升方案 - P0 - required: none
- [x] `service_return_handler` - 退换货处理 - P0 - required: `reason`
- [ ] `service_query_product` - 查询商品信息 - P1 - required: none
- [x] `service_nps_analyzer` - NPS客户满意度分析 - P0 - required: none
- [x] `service_sentiment_analyzer` - 客服情感分析 - P0 - required: none
- [x] `service_nps_driver_analysis` - NPS驱动因素分析 - P0 - required: none

### Design (5)

- [ ] `design_main_image` - 主图设计方案 - P1 - required: none
- [ ] `design_detail_page` - 详情页设计方案 - P1 - required: none
- [ ] `design_color_scheme` - 配色方案 - P1 - required: `brand_tone`
- [x] `design_material_spec` - 素材规范 - P0 - required: `usage`
- [ ] `design_campaign_poster` - 活动海报设计 - P1 - required: `campaign_name`

### Engineering (6)

- [ ] `engineering_arch_review` - 架构评估 - P1 - required: `system_type`
- [ ] `engineering_bug_analysis` - Bug排查方案 - P1 - required: `error_type`
- [ ] `engineering_perf_optimize` - 性能优化方案 - P1 - required: `bottleneck`
- [ ] `engineering_tech_selection` - 技术选型 - P1 - required: `domain`
- [ ] `engineering_system_check` - 平台接口健康检查 - P2 - required: none
- [x] `engineering_sla_monitor` - SLA监控 - P0 - required: none

### Web (7)

- [ ] `web_seo_optimize` - SEO优化方案 - P1 - required: none
- [ ] `web_title_generator` - 标题生成 - P1 - required: none
- [x] `web_page_conversion` - 页面转化优化 - P0 - required: `page_type`
- [ ] `web_store_design` - 店铺装修方案 - P1 - required: `store_type`
- [ ] `web_keyword_research` - 关键词研究 - P1 - required: none
- [ ] `web_product_description` - 详情页文案 - P1 - required: none
- [x] `web_title_seo_scorer` - 标题SEO评分 - P0 - required: none

### Coordination And Platform (11)

- [x] `coordination_agent_handoff` - Agent交接 - P2 - required: `task_description`
- [ ] `coordination_task_orchestration` - 任务编排 - P2 - required: `goal`
- [ ] `coordination_status_query` - 状态查询 - P2 - required: none
- [ ] `coordination_create_campaign` - 创建营销活动 - P2 - required: `name`
- [ ] `coordination_create_task` - 创建工作任务 - P2 - required: `title`
- [ ] `coordination_save_memory` - 保存工作记忆 - P2 - required: `key`, `content`
- [ ] `platform_update_price` - 修改商品价格 - P2 - required: `platform`, `product_id`, `new_price`
- [ ] `platform_update_inventory` - 修改商品库存 - P2 - required: `platform`, `product_id`, `quantity`
- [ ] `platform_sync_products` - 同步平台商品 - P2 - required: `platform`
- [ ] `coordination_skill_chain_planner` - 技能链规划 - P2 - required: `goal`
- [ ] `coordination_external_agent_call` - 外部Agent调用 - P2 - required: `endpoint`, `description`
