# A股斐波那契波浪看板（a-share-fib-wave）

上证指数 5 年艾略特波浪 + 斐波那契分析引擎，产出单源真值 `data/data.js` 驱动前端 `index.html` 看板，并附带回测闭环与多重质量门禁。

> ⚠️ 免责声明：本工程为个人量化研究工具，所有预测/概率均为模型输出，不构成任何投资建议。据此操作风险自负。

---

## 1. 目录结构

```
a-share-fib-wave/
├── analyze.py          # 取数解析 + zigzag 摆动 → 波浪结构识别（依赖 wb-finance-skill 的 elliott_wave.py）
├── build_data.py       # 波浪/概率 binder/子浪/买卖框架 → 产出 data/data.js（单源真值）
├── backtest.py         # 回测闭环（archive → evaluate → aggregate）
├── calibrate.py        # 概率模型 walk-forward 实证校准
├── validate.py         # 门禁①：NaN/Inf、OHLC、targets、distances、新鲜度、CSV↔data.js 对齐
├── audit49.py          # 门禁②：子浪ⅴ≡卖①、子浪ⅰ<子浪ⅴ、浪⑤起 expDays==10
├── audit50.py          # 门禁③：概率三级降级数学（与 build_data._enrich 逐字同步）
├── audit51.py          # 门禁④：六增强结构不变量（timeCalib.blended≈1）
├── audit52.py          # 门禁⑤：前端↔数据脱节静态核对 + node 沙箱运行时（echarts 已代码内打桩，无需安装）
├── fetch_indices.py    # 取数（westock kline → data/sh000001_raw.md）
├── preflight.py        # 取数后数据体检（主指数变形/失败中止，副指数仅告警）
├── index.html          # 前端看板（echarts 走 CDN）
├── data/
│   ├── data.js         # ★ 单源真值，前端只读它
│   ├── predictions_log.jsonl  # 回测日志（每日 9 条）
│   ├── backtest.json          # 回测聚合结果
│   └── sh000001_raw.md / *.csv
├── R159~R232_*.py      # 历次核查/体检脚本（只读，不改生产）
├── push_to_github.bat / push_silent.bat  # Windows 一键/自动推送到 GitHub（SSH）
└── .workbuddy/         # 项目级 WorkBuddy 数据（已 gitignore，不入库，勿手动提交）
```

---

## 2. 环境依赖

| 依赖 | 说明 | 是否必需 |
|---|---|---|
| Python 3.13+ | 建 venv 隔离运行 | 必需 |
| numpy / pandas | 仅这两个第三方包 | 必需 |
| **wb-finance-skill** | `analyze.py` 需其中的 `elliott_wave.py`（波浪引擎）。`analyze.py` 启动时自动探测 WorkBuddy 安装位置（含 Mac `/Applications/WorkBuddy.app` 与 `~/.workbuddy/plugins/cache`），无需手动放文件 | 必需（装好 WorkBuddy + 该技能即可） |
| Node.js | 仅 `audit52.py` 用 `vm` 沙箱运行时；echarts 已在脚本内打桩，**无需 `npm install echarts`** | 仅跑 audit52 时需 node，前端 echarts 走 CDN |
| 浏览器 + 网络 | 打开 `index.html` 渲染看板 | 看板展示必需 |

> 跨机迁移要点：`analyze.py` 已做跨平台路径探测（R214 修复），只需保证 WorkBuddy + wb-finance-skill 在目标机存在，或设置环境变量 `WORKBUDDY_HOME` 指向 WorkBuddy 根目录。

---

## 3. 快速开始（以换 Mac 为例）

```bash
# 克隆（仓库为 Private，需先配好 GitHub SSH key 并登录）
git clone git@github.com:willinxusheng/A-share-Fibonacci.git
cd A-share-Fibonacci

# 建隔离 venv 并装依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 验证 elliott_wave 可被定位：直接运行 analyze.py（其启动时会自动探测并注入 wb-finance-skill 路径）
python analyze.py   # 首行无 ModuleNotFoundError 即定位成功
# 注意：不要单独 `python -c "from elliott_wave import ..."` —— 该 import 依赖 analyze.py 运行时注入的 sys.path，脱离上下文会失败
```

---

## 4. 运行引擎（手动全流程）

```bash
source venv/bin/activate
cd A-share-Fibonacci

# ① 取数
python fetch_indices.py        # 输出 data/sh000001_raw.md
python preflight.py            # 数据体检（主指数异常会中止）

# ② 波浪结构分析
python analyze.py              # 输出 data/structures.json + data/sh000001.csv

# ③ 构建单源真值 data.js（内部会调 backtest + calibrate）
python build_data.py           # 产出 data/data.js

# ④ 五守门员（各自须 EXIT=0，全绿才安全）
for s in validate.py audit49.py audit50.py audit51.py audit52.py; do
  python "$s"; echo "$s EXIT=$?"
done
```

> 注意：门禁脚本含非 ASCII 输出（如 audit52 首句）。Mac 终端默认 UTF-8 直接通过；请勿在 GBK 代码页下运行（会触发 UnicodeEncodeError 假阴性）。

---

## 5. 看板部署

### 方式 A：GitHub Pages（永久链接，推荐）
1. 仓库 **Settings → Pages → Build and deployment**
2. Source = `Deploy from a branch`，Branch = `main`，目录 = `/(root)`
3. Save，约 1–2 分钟后访问 `https://willinxusheng.github.io/A-share-Fibonacci/`
4. 仓库为 Private 时，该页面仅你登录 GitHub 后可见（自用足够，含交易逻辑不建议改 Public）

### 方式 B：CloudStudio / WorkBuddy 部署
看板代码未改动时，重新发布即得新链接；旧沙箱链接可能回收，以「设置 → 数据管理 → 我发布的应用」中当前有效地址为准。

> 数据是**静态快照**：GitHub Pages / CloudStudio 都不会自动更新。每日更新在 WorkBuddy 沙箱内跑，需把新 `data/data.js` 传到仓库 `data/` 下（见第 6 节）。

---

## 6. 自动推送到 GitHub（SSH，免密码/令牌）

本机已生成 SSH key 并配好 `origin` 为 SSH 地址。每次程序更新后：

- **手动**：双击 `push_to_github.bat`（Windows）或在 Mac 上 `git add -A && git commit -m "..." && git push`
- **自动**（Windows 本机定时任务，绕过沙箱 443 限制）：
  ```bat
  schtasks /create /sc daily /st 19:05 /tn "AshareFibAutoPush" /tr "C:\Users\Administrator\WorkBuddy\2026-08-04-23-16-18\a-share-fib-wave\push_silent.bat" /f
  ```
  SSH key 无 passphrase，可无人值守。`push_silent.bat` 日志落 `%TEMP%/ashare_push.log`。

> Mac 上等价做法：在 `crontab` 或 `launchd` 里每日跑 `git push`，前提是本机网络能连 `github.com:22`（SSH）。

---

## 7. 回测与核查

- `backtest.py` 闭环已接线：`archive`（按 date,key,cat 去重）→ `evaluate`（用记录自身日期的 vol regime 重算命中，避免前视泄漏）→ `aggregate`（写 `backtest.json`）。
- 观察窗 = `max(30, expDays)` 交易日；最短周期目标约 **2026-09-15** 起开始有真实评估数据（此前 `totalEvaluated=0` 为冷启动，非 bug）。
- 每日构建自动化（WorkBuddy 沙箱）会在工作日 18:30 累积数据；首次真实命中率复验由一次性自动化于 **2026-09-21 20:00** 触发。

---

## 8. 关键工程约定

- **单源真值**：价格/比例/日期一律从 `data/data.js`（`wave_points`）派生，前端 `index.html` 直接读取、无任何前端派生计算。
- **R85 优化纪律**：引擎层"准确度提升"改动须先 walk-forward OOS 复验、确降均值 Brier 才部署，严禁盲改。已验最优组合：等权 + 20 日 MA 趋势态 + vol 带 (0.75,1.25) + band 边缘对齐。
- **五门禁**：`validate / audit49 / audit50 / audit51 / audit52` 必须各自 `EXIT=0` 才算安全闸通过。
- **输出末尾须带免责声明**。
