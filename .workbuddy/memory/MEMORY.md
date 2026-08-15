# 项目长期记忆 · A股斐波那契波浪看板 (a-share-fib-wave)

## 引擎当前态(R110 终局, 2026-08-06)
- 等权 + 固定20日MA趋势态匹配(close vs MA20) + 波动率匹配带 (0.75,1.25) + 实证命中容差/先验首达障碍对齐预测 band 边缘(R89, Brier −39.4%)。8 目标全实证级驱动。
- 子浪比率：斐波那契标准(0.5/1.618/0.2917) + regime 回撤缩放(0.7~1.3) + 端点锁定使子浪ⅴ≡卖①。
- 子浪触达带(R105/N3)：与卖点统一用 vol term-structure `_frac`(horizon 依赖)，显示带≡概率带(R89 延伸至子浪)，不再用历史离散度单一值。
- **R106 GIGO 守卫**：`build_data` 引入 `_have_valid = (_sw_emp is not None)`(有效同浪级 5 浪结构≥6 且过 sanity)。无有效结构时：浪⑤截断收缩归零(trunc_prob=0)、独立V交叉校验置"不可用"(deviation=0)、`_date_mae` 返回 None→时间权重 `_W_EMP` 回退先验 0.5。即"无经验证据不扭曲斐波那契基线"。
- **R107 时间校准修正**：`_date_mae` 旧式比对退化 `_sw_runs`(8% zigzag 同浪级检测，0 有效)→静默强锁 `_W_EMP=0.5`；现改比对正确来源 `_runs`(指数自身上行5腿摆动，N=5) + `_MIN_RUNS=12` 门槛防过拟合。N=5<12 → 仍 0.5（数值不变，理由正确、可审计）。
- **R108 子浪预测收口(最终核查)**：前四轮(R104–R107)已穷尽子浪所有可独立验证杠杆(价格比率/带一致性/去污染/时间校准)；本轮再查概率引擎**漂移源 drift**(`_drift_for(_exp)*_drift_conf` = 按horizon插值的滚动收益率均值×波动率regime阻尼 + 跨指数共振加权，line 1196/1228/1103-1118)，确认属全局概率核心、R90 已 OOS 证校准良好(0.23≈0.25)、已是"当前趋势捕捉"，改之风险高(须同步audit50+全链OOS)且预期无增益。**结论：子浪预测在本指数数据约束下已确证到达可验证准确度上限，停止盲改。**

## 守门员链(改 build_data 后必跑, venv python)
`C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe validate.py audit49.py audit50.py audit51.py`
- 系统 python 缺 pandas/numpy 会崩(exit=1)；必须用 venv。
- validate: NaN/OHLC/targets/distances/wavePoints/子浪ⅴ≡卖①。
- audit49: 子浪 expDays 锚定 last_date、子浪ⅴ==卖①。
- audit50: 概率三级降级数学 + 触达带；`_expected` 与 build `_enrich` 逐字同步(改首达公式须同步)。
- audit51: 六增强结构、timeCalib.blended 累计和≈1、bandPct∈(0,25)。

## 关键约束/已知局限
- raw.md 每日自动化删除 → 跨指数共振缺失时 breadth→0 兜底。
- 部署：workbuddy_cloudstudio_deploy；每日自动化 automation-1785857559486(工作日18:30)重部署。
- **经验比率校准被斩钉截铁判定为结构性不可行(R106)**：代码实际消费的 8% `st["zigzag"]` 仅 4 个候选 5 浪组且**全部退化**(子浪ⅱ回撤>100%，违反推动浪规则)；2%~8% 各尺度 R104 已确认 0 有效样本。不是 clamp/参数问题，放宽子浪ⅲ上界到 2.618 仍 0 有效(全死在 ii_ret)。放弃该路径、诚实保留斐波那契标准+端点锁定最可信基线。
- **经验时间占比来源也稀疏(R107)**：子浪时间校准的"经验占比"取自 `_runs`(指数上行5腿摆动)，本指数仅 N=5；忠实 MAE 扫参显示各权重 medianMAE 0.146~0.152 基本持平，经验占比**不显著优于经典5浪时间先验**→ 维持 50/50(经典先验为主) 已是最优，N<12 不启用经验权重。
- 唯一真实同浪级结构=浪③，子浪ⅱ 回撤 22%(SUB_RET) << 模型假设 61.8%；已作透明度信号(不覆盖模型)。

## 子浪预测"已到可验证上限"· 收口与终局(R110)
- 现状：比率=斐波那契(无有效同浪级样本)、端点=卖①锁定(最准)、触达带=vol/horizon一致(R105)、真实浪③对照透明(R105)、时间校准bug已修+诚实标注(R107)、概率drift已最优(R108)。
- 解锁路径(均须先忠实 OOS 复验、严禁盲改)：
  1. ~~多指数同浪级结构~~ **已彻底证伪、永久关闭(R109)**：实测 上证/沪深300/创业板 三指数 × 2%~8% 各尺度 zigzag，过 Elliott sanity(ii_ret∈[0.2,0.7]、iii∈[1.0,2.2]、iv_ret∈[0.2,0.6]) 的同浪级5浪推动浪样本 **恒为 0**（8%: 上证4/沪深3003/创业板1 runs 全退化，细尺度并集仍 0）。结构性根因：A股主要指数大级别浪型不干净(子浪ⅱ回撤常>70%、子浪ⅲ常>2.2)，全被 sanity 过滤。**经验比率校准无数据基础、与时间/指数数量无关、永久不可激活**。子浪价格比率=斐波那契+端点锁已是数学最可信基线。
  2. **更长样本**：A股浪型不干净属结构性问题，加时间维度不指望解决；当前已含三指数各5年仍 0 有效，路径实质已并入门#1证伪，低优先级。
  3. ~~新数据源~~ **经 R110 评估不为"子浪准确度"立项**：westock-data 的 `fund flow`/`market` 已确认可得上证指数每日主力净流入(MainNetFlow)历史序列，数据层可行；但 (a)子浪价格=斐波那契+端点锁数学锁死、新源改不了价格(准确度核心)；(b)路径#3 仅改善概率 drift，而 drift 已 R90 OOS 验证最优(0.23≈0.25)；(c)工程成本极高(外部管线+5年缓存+drift重写+OOS台改造+每日同步)；(d)R85 须 OOS 证明降 Brier，当前 drift 已最优预期≈0增益。即便成功仅边际改善全局概率(非子浪专属)。
- **正式终局(R110)**：R104→R110 七轮穷尽审计已确证——子浪价格(斐波那契+端点锁)、时间(经典先验50/50)、触达带(vol/horizon一致 R105)、概率 drift(已最优 R108)、多指数经验校准(证伪 R109)、新数据源(边界明确不立项 R110)——**全部无进一步可验证增益，无盲改空间**。未来唯一触发重估=市场走出更长样本/新数据源已接入且 OOS 证明降 Brier；未触发前，任何对子浪引擎(尤其价格比率)的"优化"均属盲改(违反 R85)，自动化/新会话见此声明应直接跳过空转、不再重复审计。

## 优化铁律
- 任何预测引擎"准确度提升"改动须先忠实 walk-forward OOS 复验(无前视、复刻 i+exp>=nn 跳过)，仅当降低平均 Brier 才部署；严禁盲改。
- 概率数学未变时守门员锁零风险；改首达公式/融合/范围须同步 audit50 并复跑全链。
- 子浪中间点(子浪ⅰ~ⅳ)准确度受比率约束=斐波那契(无有效样本不可改)；端点(子浪ⅴ)=卖①锁定为最准。本指数数据约束下子浪可验证准确度已逼近上限，再提升须补样本/多指数或新数据源。

## 情景自适应切换(随机应变, 2026-08-06)
- 用户要求：浪5子浪预测须随行情"随机应变"——走强势→子浪切强势、走基准→切基准走势、走风险→切风险失效，不再写死"强势"前提。
- 实现：build_data 新增 `scenarioSwitch`，按**当日收盘**相对两关键位自动判定 `active`：
  - `last_close < KEY_LINE(3674.40)` → risk（跌破铁律，子浪失效）
  - `KEY_LINE ≤ last_close < w4_low(3741.11)` → base（浪④磨底中，子浪待激活）
  - `last_close ≥ w4_low` → strong（浪⑤已启动，完整子浪）
  - 阈值即框架既有真值(铁律线/浪④底)，不新增硬编码。
- 数据哲学(用户选"严谨占位")：strong=现有完整子浪(审计铁律 子浪ⅴ≡卖① 仅校验 strong)；base=用 `scenarios[0].points` 作"基准走势线"占位(子浪未启动不杜撰)；risk=用 `scenarios[2].points` 作"风险下行线"+失效说明。base/risk 不生成子浪数据。
- 前端(index.html 面板4)：子浪图/表格/横幅抽成 `renderSubF(mode)`(strong/base/risk 三分支)，顶部加 `#scenarioSel` 下拉手动预览三套(自动判定仍为主、手动仅预览)；横幅动态显示当前情景+切换规则+自动判定结论。
- 改 `subForecast`/`scenarios`/`scenarioSwitch` 结构时须同步 **audit52 NESTED_PATHS**(已含 scenarioSwitch.active/lastClose/keyLine/w4Low/rules/base/base.path/risk/risk.path)。
- 触发口径：每日 18:30 自动化重跑即随行情切换(收盘有效跌破即切，非盘中实时)。

## R113 轮（情景联动全看板；用户新需求"各模块之前能联动的必须联动"）
### 任务
用户："各模块之前能联动的必须联动"。经 AskUserQuestion 对齐（仅勾选"情景联动全看板"），把"当前情景"提成看板级状态，驱动多模块联动。

### 改动（纯前端 index.html，data.js 结构未变）
- 脚本顶部（charts 后）提取看板级 `CURRENT_SCENARIO`（=SS.active 自动判定）+ `SCN_IDX={base:0,strong:1,risk:2}`（active→D.scenarios 索引）；删 p4 内局部重复 SS。
- p3 通道推演图（`#chart2` + `#c2ScnStatus` 状态条）：情景线按当前情景加粗实色(width 3.4/opacity1)、其他两线淡化(opacity 0.28)；暴露 `window.applyScenarioC2(mode)` 切换钩子 + `_renderC2ScnStatus` 显示"当前情景高亮/自动判定/手动预览"。
- p5 买卖框架（`#planNote`）：`renderPlanNote(mode)` 抽成函数，按 strong/base/risk 在顶部显示不同情景状态条（强势=完整框架/基准=子浪待激活/风险=数浪证伪转防御）；暴露 `window.applyScenarioPlan(mode)`。
- p4 子浪（`#scenarioSel`）：change 时 `_onScn` 同步调用 `renderSubF`+`applyScenarioC2`+`applyScenarioPlan`（初始化即联动）；手动预览不影响自动判定（横幅/状态条标注）。

### 前端联动架构（长期）
- 各模块暴露切换钩子：p3 `window.applyScenarioC2(mode)`、p5 `window.applyScenarioPlan(mode)`、p4 `renderSubF(mode)`（已有）。子浪面板 `#scenarioSel` change → `_onScn` 同步三者；初始化亦联动。改情景相关逻辑须保持三钩子签名一致、audit52 前端运行时沙箱 0 错误。切换口在子浪面板，但联动驱动全看板；手动预览不影响自动判定。

### 验证 + 部署
- 5 门禁各自独立 EXIT=0；audit52 前端运行时沙箱 0 错误（三联动钩子签名一致、无悬挂引用）。
- 纯前端联动，data.js 未改；部署覆盖同一 bj7 沙箱，链接不变 https://31e6dcfaca734fb9a2fb7a00d76fc763.bj7.agentos-app.net 。
