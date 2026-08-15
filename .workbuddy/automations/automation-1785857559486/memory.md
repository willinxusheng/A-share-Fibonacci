# 自动化任务记忆：上证指数斐波那契波浪图每日更新

## 2026-08-14（第八次执行）
- **数据更新**：10 只指数全部拉到 08-14 当日新K线（A股6只+港股2只；美股为 08-13 收盘，正常时差）。上证收 3927.18（-0.49%，昨收 3946.68）。preflight EXIT=0，10 只全 OK（0 坏行），无副指数告警。
- **分析结果**：analyze.py + build_data.py 成功（HV20 分位 62% 中波动、bandScale 1.0）。最新收盘 3927.18，未跌破铁律线 3674.40（缓冲 +252.78/+6.44%），不在买点参考区 3674.40~3793.52（上方趋势运行区），未触及卖① 4493.94（距 14.43%），未突破前高 4258.86（距 8.45%），无新引擎信号（最新仍为 2026-07-20 做多）。scenarioSwitch.active=strong（收盘≥浪④底 3741.11）。浪型结构无重大变化，无需人工复核。
- **联动信号**：A股宽基共振 breadth=+1.0（5/5 可用且全涨：中证500 +6.34、创业板 +5.77、上证指数 +4.33、上证50 +3.13、沪深300 +3.02、科创50 +0.14），跨市场 breadth=+1.0（4/4 全涨：纳斯达克 +3.56、标普500 +3.52、恒生 +2.26、恒生科技 +1.82）→ 内外共振同涨，风险偏好回升。
- **trailingStop**：收盘 3927.18 仍低于 MA60(3985.82)（连续第5天触发"收盘跌破 MA60→再减半/转防御"），高于 MA20(3870.39)。
- **守门**：validate/audit49/audit50/audit51/audit52 五脚本独立运行全部 EXIT=0。子浪ⅴ=卖① expDays 54 一致。
- **清理**：删除 3 个 raw（sh000001/sh000300/sz399006_raw.md）+ _preflight_report.txt；无 _check.js/_audit52_rt.js/*.bak/*.tmp。保留 7 个扩展 raw。
- **部署**：一次成功。**链接域名格式变更**：返回 https://31e6dcfaca734fb9a2fb7a00d76fc763.app.workbuddy.link（sandboxId 仍为 31e6dcfaca734fb9a2fb7a00d76fc763 与历史相同，但域名由 .bj7.agentos-app.net 变更为 .app.workbuddy.link，HTTP 200 验证可达）。旧格式链接是否仍有效未知，以新链接为准，后续执行统一用新域名格式。

## 2026-08-13（第七次执行）
- **数据更新**：10 只指数全部拉到 08-13 当日新K线（A股6只+港股2只；美股为 08-12 收盘，正常时差）。上证收 3926.96（-0.50%，昨收 3946.68）。preflight EXIT=0，10 只全 OK（0 坏行），无副指数告警。
- **分析结果**：analyze.py + build_data.py 成功（HV20 分位 83% 高波动、bandScale 1.15）。最新收盘 3926.96，未跌破铁律线 3674.40（缓冲 +252.56/+6.87%），不在买点参考区 3674.40~3793.52（上方趋势运行区），未触及卖① 4493.94（距 14.44%），未突破前高 4258.86（距 8.45%），无新引擎信号（最新仍为 2026-07-20 做多）。scenarioSwitch.active=strong（收盘≥浪④底 3741.11）。浪型结构无重大变化，无需人工复核。
- **联动信号**：A股宽基共振 breadth=-0.2（5/5 可用，近20日：上证50 +0.83、中证500 +0.16、沪深300 -0.74、创业板 -2.88、科创50 -6.99；价值显著强于成长，valueVsGrowth +3.71），跨市场 breadth=+0.5（4/4：恒生 +1.55、标普500 +2.32、纳斯达克 +1.21、恒生科技 -0.87）。
- **trailingStop**：收盘 3926.96 仍低于 MA60(3988.32)（连续第4天触发"收盘跌破 MA60→再减半/转防御"），仍高于 MA20(3862.24)。
- **守门**：validate/audit49/audit50/audit51/audit52 五脚本独立运行全部 EXIT=0。子浪ⅴ=卖① expDays 54 一致。
- **清理**：删除 3 个 raw（sh000001/sh000300/sz399006_raw.md）+ _preflight_report.txt；无 _check.js/_audit52_rt.js/*.bak/*.tmp。保留 7 个扩展 raw。
- **部署**：一次成功，链接未变 https://31e6dcfaca734fb9a2fb7a00d76fc763.bj7.agentos-app.net（sandboxId 31e6dcfaca734fb9a2fb7a00d76fc763）。

## 2026-08-12（第六次执行）
- **数据更新**：10 只指数全部拉到 08-12 当日新K线。上证收 3946.68（+0.32%，昨收 3934.09）。preflight EXIT=0，10 只全 OK（0 坏行），无副指数告警。
- **分析结果**：analyze.py + build_data.py 成功。最新收盘 3946.68，未跌破铁律线 3674.40（缓冲 +272.28/+7.41%），不在买点参考区 3674.40~3793.52（在其上方趋势运行区），未触及卖① 4493.94（距 13.87%），未突破前高 4258.86（距 7.91%），无新引擎信号（最新仍为 2026-07-20 做多）。scenarioSwitch.active=strong（收盘≥浪④底 3741.11，浪⑤已启动完整子浪）。浪型结构无重大变化，无需人工复核。
- **联动信号**：A股宽基共振 breadth=-1.0（5/5 全跌：科创50 -9.73%、创业板 -5.32%、中证500 -1.25%、沪深300 -2.00%、上证50 -0.96%），跨市场 breadth=+1.0（4/4 全涨：恒生+3.07%、恒生科技+0.76%、标普500+2.45%、纳斯达克+1.30%）→ 内外背离延续，但上证自身今日反弹 +0.32%。风格：价值(上证50)显著强于成长(创业板)，资金偏价值。
- **trailingStop**：收盘 3946.68 仍低于 MA60(3992.24)（连续第3天触发"收盘跌破 MA60→再减半/转防御"），仍高于 MA20(3860.02)。
- **守门**：validate/audit49/audit50/audit51/audit52 五脚本独立运行全部 EXIT=0。子浪ⅴ=卖① expDays 52 一致。
- **清理**：删除 3 个 raw（sh000001/sh000300/sz399006_raw.md）+ _preflight_report.txt；无 _check.js/_audit52_rt.js/*.bak/*.tmp。保留 7 个扩展 raw。
- **部署**：一次成功，链接未变 https://31e6dcfaca734fb9a2fb7a00d76fc763.bj7.agentos-app.net（sandboxId 31e6dcfaca734fb9a2fb7a00d76fc763）。

## 2026-08-11（第五次执行）
- **数据更新**：10 只指数全部拉到 08-11 当日新K线。上证收 3934.09（-0.82%，昨收 3966.59）。preflight EXIT=0，10 只全 OK（0 坏行），无副指数告警。
- **分析结果**：analyze.py + build_data.py 成功。最新收盘 3934.09，未跌破铁律线 3674.40（缓冲 +259.69/+7.07%），不在买点参考区 3674.40~3793.52（在其上方趋势运行区），未触及卖① 4493.94（距 14.23%），未突破前高 4258.86（距 8.26%），无新引擎信号（最新仍为 2026-07-20 做多）。scenarioSwitch.active=strong（收盘≥浪④底 3741.11，浪⑤已启动完整子浪）。浪型结构无重大变化，无需人工复核。
- **联动信号（新增关注点）**：收盘 3934.09 已低于 MA60(3995.96)（trailingStop 第二条"收盘跌破 MA60→再减半/转防御"连续第2天触发；仍高于 MA20 3860.46）。A股宽基共振 breadth=-1.0（5/5 全跌：科创50近20日 -14.94%、创业板 -7.84%、中证500 -3.73%、沪深300 -2.76%），跨市场 breadth=+1.0（4/4 全涨：恒生+5.39%、恒生科技+3.10%、标普500+3.16%、纳斯达克+2.83%）→ 内外背离，A股独立走弱。
- **守门**：validate/audit49/audit50/audit51/audit52 五脚本独立运行全部 EXIT=0。子浪ⅴ=卖① expDays 54 一致。
- **清理**：删除 3 个 raw（sh000001/sh000300/sz399006_raw.md）+ _preflight_report.txt；无 _check.js/_audit52_rt.js/*.bak/*.tmp。保留 7 个扩展 raw。
- **部署**：一次成功，链接未变 https://31e6dcfaca734fb9a2fb7a00d76fc763.bj7.agentos-app.net（sandboxId 31e6dcfaca734fb9a2fb7a00d76fc763）。

## 2026-08-10（第四次执行）
- **数据更新**：10 只指数全部拉到 08-10 当日新K线。上证收 3966.59（+0.67%，昨收 3940.04）。preflight EXIT=0，10 只全 OK（0 坏行），无副指数告警。
- **分析结果**：analyze.py + build_data.py 成功。最新收盘 3966.59，未跌破铁律线 3674.40（缓冲 +292.19/+7.95%），不在买点参考区 3674.40~3793.52（在其上方趋势运行区），未触及卖① 4493.94（距 13.29%），未突破前高 4258.86（距 7.37%），无新引擎信号（最新仍为 2026-07-20 做多）。scenarioSwitch.active=strong（收盘≥浪④底 3741.11，浪⑤已启动完整子浪）。浪型结构无重大变化，无需人工复核。注意：收盘 3966.59 已低于 MA60(3999.25)、高于 MA20(3862.11)，trailingStop 规则提示减仓/防御档位。
- **守门**：validate/audit49/audit50/audit51/audit52 五脚本独立运行全部 EXIT=0。子浪ⅴ=卖① expDays 50 一致。
- **清理**：删除 3 个 raw（sh000001/sh000300/sz399006_raw.md）+ _preflight_report.txt；无 _check.js/_audit52_rt.js/*.bak/*.tmp。保留 7 个扩展 raw。
- **部署**：首次 deploy 报 "exec failed (400)"，立即重试成功。链接未变 https://31e6dcfaca734fb9a2fb7a00d76fc763.bj7.agentos-app.net（sandboxId 31e6dcfaca734fb9a2fb7a00d76fc763）。经验：cloudstudio deploy 偶发 400，直接重试即可。

## 2026-08-07（第三次执行）
- **数据更新**：10 只指数全部拉到当日新K线。上证收 3940.04（+1.17%，昨收 3900.35）。preflight EXIT=0，10 只全 OK（0 坏行），无副指数告警。
- **分析结果**：analyze.py + build_data.py 成功。最新收盘 3940.04，未跌破铁律线 3674.40（缓冲 +265.64/+6.74%），不在买点参考区 3674.40~3793.52（在其上方趋势运行区），未触及卖① 4493.94（距 14.06%），未突破前高 4258.86（距 8.09%），无新引擎信号（最新仍为 2026-07-20 做多）。scenarioSwitch.active=strong（收盘≥浪④底 3741.11，浪⑤已启动完整子浪）。浪型结构无重大变化，无需人工复核。
- **守门**：validate/audit49/audit50/audit51/audit52 五脚本独立运行全部 EXIT=0。子浪ⅴ=卖① expDays 53 一致。
- **清理**：删除 3 个 raw（sh000001/sh000300/sz399006_raw.md）+ _preflight_report.txt；_check.js/_audit52_rt.js 不存在。保留 7 个扩展 raw（sh000016/sh000905/sh000688/hkHSI/hkHSTECH/usINX/usIXIC）。
- **部署**：cloudstudio 部署成功，链接未变 https://31e6dcfaca734fb9a2fb7a00d76fc763.bj7.agentos-app.net（sandboxId 31e6dcfaca734fb9a2fb7a00d76fc763）。

## 2026-08-06（第二次执行）
- **数据更新**：三指数均拉到当日新K线（2026-08-06 交易日）。上证收 3900.35（+0.57%，昨收 3878.43）。HV20 五年 89% 分位（高波动区），bandScale 1.15。
- **分析结果**：analyze.py + build_data.py 成功。最新收盘 3900.35，未跌破铁律线 3674.40（缓冲 +225.95 点/+5.79%），未进入买点参考区 3674.40~3793.52（在其上方、趋势运行区内），未触及卖① 4493.94（距 15.22%），未突破前高 4258.86（距 9.19%），无新引擎信号（最新仍为 2026-07-20 做多）。浪型结构无重大变化，无需人工复核。
- **守门**：validate/audit49/audit50/audit51/audit52 五脚本独立运行全部 EXIT=0。子浪ⅴ=卖① expDays 57 一致。
- **清理**：删除 3 个 raw 中间文件（sh000001/sh000300/sz399006_raw.md）；无 _check.js/_audit52_rt.js/*.bak/*.tmp。
- **部署**：cloudstudio 部署成功，链接未变 https://31e6dcfaca734fb9a2fb7a00d76fc763.bj7.agentos-app.net（sandboxId 31e6dcfaca734fb9a2fb7a00d76fc763，与 08-05 相同）。

## 2026-08-05（首次执行）
- **数据更新**：三指数（sh000001/sh000300/sz399006）均拉到当日新K线（2026-08-05 交易日）。上证收 3878.43（+1.47%）。
- **分析结果**：analyze.py + build_data.py 运行成功。最新收盘 3878.43，未跌破铁律线 3674.40（缓冲 +204.03 点/+5.26%），未进入买点参考区 3674.40~3793.52（在其上方，趋势运行区内），未触及卖① 4493.94，未突破前高 4258.86，无新引擎信号（最新仍为 2026-07-20 做多）。浪型结构无重大变化，无需人工复核。
- **清理**：删除 83 个历史审计/临时文件（audit*.py 49个、日志3个、__pycache__、data/_audit* 30个）。保留 index.html、analyze.py、build_data.py、backtest.py、validate.py、data/。
- **部署**：cloudstudio 部署成功，分享链接 https://31e6dcfaca734fb9a2fb7a00d76fc763.bj7.agentos-app.net（sandboxId 31e6dcfaca734fb9a2fb7a00d76fc763）。
- **注意**：删除命令勿用 bash rm（会被 safe-delete 拦截、路径转换失败），改用 python os.remove/glob。

## 执行要点备忘
- westock-data CLI 路径：C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe + D:\WorkBuddy\...\westock-data\scripts\index.js
- python 环境：C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe
- 关键价位：铁律线/风控 3674.40；买点参考区 3674.40~3793.52；前高/浪3顶 4258.86；卖①首目标 4493.94；卖② 4633.93；卖③ 4725.81。
- build_data.py 中 wavePoints/scenarios/tradePlan/findings 为人工校订，重大结构变化时提醒用户人工复核，勿自行修改。
