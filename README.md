# A股斐波那契波浪看板（A-share-Fibonacci）

上证指数 5 年艾略特波浪 + 斐波那契分析引擎，产出单源真值 `data/data.js` 驱动前端 `index.html` 看板，并附带回测闭环与多重质量门禁。

> ⚠️ 免责声明：本工程为个人量化研究工具，所有预测/概率均为模型输出，不构成任何投资建议。据此操作风险自负。

---

## 1. 目录结构

```
A-share-Fibonacci/   # git clone 出的仓库目录名（本仓库 GitHub 名）
├── analyze.py          # 取数解析 + zigzag 摆动 → 波浪结构识别（依赖 wb-finance-skill 的 elliott_wave.py）
├── build_data.py       # 波浪/概率 binder/子浪/买卖框架 → 产出 data/data.js（单源真值）
├── backtest.py         # 回测闭环（archive → evaluate → aggregate）
├── calibrate.py        # 概率模型 walk-forward 实证校准
├── validate.py         # 门禁①：NaN/Inf、OHLC、targets、distances、新鲜度、CSV↔data.js 对齐
├── audit49.py          # 门禁②：子浪ⅴ≡卖①、子浪ⅰ<子浪ⅴ、浪⑤起 expDays==10
├── audit50.py          # 门禁③：概率三级降级数学（与 build_data._enrich 逐字同步）
├── audit51.py          # 门禁④：六增强结构不变量（timeCalib.blended≈1）
├── audit52.py          # 门禁⑤：前端↔数据脱节静态核对 + node 沙箱运行时（echarts 已代码内打桩，无需安装）
├── _audit_overlap2.js   # 门禁⑥：标注重叠 SSR 审计（chart1/2/subF×3 真实渲染查重叠，须带 NODE_PATH）
├── fetch_indices.py    # 取数（westock kline → data/sh000001_raw.md）
├── preflight.py        # 取数后数据体检（主指数变形/失败中止，副指数仅告警）
├── index.html          # 前端看板（echarts 走 CDN）
├── data/
│   ├── data.js         # ★ 单源真值，前端只读它
│   ├── predictions_log.jsonl  # 回测日志（每日 9 条）
│   ├── backtest.json          # 回测聚合结果
│   └── sh000001_raw.md / *.csv
├── R159~R232_*.py      # 历次核查/体检脚本（只读，不改生产）
├── push_to_github.bat / push_silent.bat  # Windows 一键/应急推送到 GitHub（SSH，仅手动用，勿设定时）
├── push_to_github.sh / com.user.ashare.autopush.plist  # 仅手动/应急推送脚本 + 已废弃 launchd 模板（常态数据更新由 CI 负责，勿启用 launchd 自动推送）
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
# 注意：不要脱离 analyze.py 单独做该模块的导入验证 —— 路径注入只在 analyze.py 运行时发生，脱离上下文必然失败
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

# ④ 六守门员（各自须 EXIT=0，全绿才安全）
for s in validate.py audit49.py audit50.py audit51.py audit52.py; do
  python "$s"; echo "$s EXIT=$?"
done
# 门禁⑥：标注重叠 SSR 审计（须带 NODE_PATH 指向含 echarts 的 node_modules，否则报 Cannot find module echarts）
NODE_PATH=/path/to/node_modules node _audit_overlap2.js; echo "_audit_overlap2 EXIT=$?"
```

> 注意：门禁脚本含非 ASCII 输出（如 audit52 首句）。Mac 终端默认 UTF-8 直接通过；请勿在 GBK 代码页下运行（会触发 UnicodeEncodeError 假阴性）。

---

## 5. 看板部署

### 方式 A：GitHub Pages（永久链接，推荐）
1. 仓库 **Settings → Pages → Build and deployment**
2. Source = `Deploy from a branch`，Branch = `main`，目录 = `/(root)`
3. Save，约 1–2 分钟后访问 `https://willinxusheng.github.io/A-share-Fibonacci/`
4. 仓库为 Private 时，该页面仅你登录 GitHub 后可见（自用足够，含交易逻辑不建议改 Public）

### 方式 B：CloudStudio / WorkBuddy 部署（已废弃）
> ⚠️ **R234 起已废弃**：AgentOS / CloudStudio 两套平台不再使用。看板**唯一入口**为 GitHub Pages（`https://willinxusheng.github.io/A-share-Fibonacci/`），由 CI 自动同步，本机不开机也每日更新。请勿再发布或引用 CloudStudio 链接。

> 数据由 CI 每日重建并自动发布：GitHub Pages 线上的 `data.js` 即仓库 `main` 分支根 `data/data.js`（CI 提交后 Pages 自动发布）。每日数据回写 `main` 统一由 CI 负责（见第 6 节），本机无需手动传。

---

## 6. 推送到 GitHub（SSH，免密码/令牌）

### 常态数据更新：由 CI 独家负责（本机无需任何自动推送）
> **架构铁律（R234/R237）**：`data/` 目录（看板数据 `data.js` 等）**唯一写入方是 GitHub Actions CI**（`daily.yml`，北京时间工作日 16:30 自动跑 取数→体检→分析→构建→六门禁→仅 `git add data/ && commit && push`）。**本机切勿**启用任何自动推送（launchd / schtasks / WorkBuddy 定时任务）去写 `data/`——否则会与 CI 并发双写 `main`，导致 push 被拒、数据竞争、或丢失当日更新。**换多少台电脑都一样：数据只信 CI，本机只读。**

### 何时需要本机推送
- **源码改动**（改了 `.py`/`.js`/`.html`/`.yml`）：你手动 `git pull --rebase` → 编辑 → `git add <具体文件>` → `git commit` → `git push`。
- **应急手动补数据**（仅当 CI 当日失败、你确需手工补一次）：用仓库带的 `push_to_github.sh`（Mac）/ `push_to_github.bat`（Windows），它们已限定为 `git add data/`（只同步数据、不碰源码/诊断残留）。**仅一次性手动跑，不要设成定时任务。**

### 本机推送脚本定位（仅手动/应急，勿启用定时）
- `push_to_github.sh` / `push_silent.bat` / `push_to_github.bat`：本地一键或补推脚本，已统一 `git add data/` 口径。
- `com.user.ashare.autopush.plist`：Mac launchd **模板，已废弃**——**不要** `launchctl load` 它。它原本用于本地自动写 `data/`，与"CI 独家写 `data/`"铁律冲突；CI 上线后此模板仅作历史参考。
- 历史 Windows `schtasks` 自动推送同理已废弃，**不要**重建。

### 手动推送命令参考（应急 / 源码）
```bash
# 源码改动（日常）
git pull --rebase origin main
git add build_data.py _audit_overlap2.js   # 只 add 你改的具体文件
git commit -m "描述"
git push origin main

# 应急补数据（仅 CI 失败且确需手工补一次，非定时）
./push_to_github.sh        # Mac；内部 git add data/ 后推送
# push_to_github.bat       # Windows 等价
```
> ⚠️ 首次使用确保本机已配 SSH key（见第 9 节 B）且能 `ssh -T git@github.com`（看到 `Hi willinxusheng!`）。SSH key 无 passphrase 方可无人值守**手动**跑；但"手动跑 ≠ 定时跑"——定时写 `data/` 一律交给 CI。

---

## 7. 回测与核查

- `backtest.py` 闭环已接线：`archive`（按 date,key,cat 去重）→ `evaluate`（用记录自身日期的 vol regime 重算命中，避免前视泄漏）→ `aggregate`（写 `backtest.json`）。
- 观察窗 = `max(30, expDays)` 交易日；最短周期目标约 **2026-09-15** 起开始有真实评估数据（此前 `totalEvaluated=0` 为冷启动，非 bug）。
- 每日构建由 GitHub Actions CI（`daily.yml`）在工作日 16:30 跑（取数→体检→分析→构建→六门禁→仅回写 `data/`）；首次真实命中率复验由一次性自动化于 **2026-09-21 20:00** 触发。

---

## 8. 关键工程约定

- **单源真值**：价格/比例/日期一律从 `data/data.js`（`wave_points`）派生，前端 `index.html` 直接读取、无任何前端派生计算。
- **R85 优化纪律**：引擎层"准确度提升"改动须先 walk-forward OOS 复验、确降均值 Brier 才部署，严禁盲改。已验最优组合：等权 + 20 日 MA 趋势态 + vol 带 (0.75,1.25) + band 边缘对齐。
- **六门禁**：`validate / audit49 / audit50 / audit51 / audit52 / _audit_overlap2.js` 必须各自 `EXIT=0` 才算安全闸通过（`_audit_overlap2.js` 须带 `NODE_PATH`）。
- **输出末尾须带免责声明**。

---

## 9. 换 Mac 总流程（WorkBuddy 大脑 + GitHub 仓库）

分两层，独立处理，换机都要做：

### A. WorkBuddy AI 大脑（记忆 / 技能 / 自动化 / 设置）— 云同步自动恢复
1. Mac 安装 WorkBuddy，登录**同一账号**（willinxusheng@163.com）。
2. 云同步（edge-sync）自动拉回 `.workbuddy/` 大脑目录：SOUL / IDENTITY / USER / MEMORY.md、memory/、skills/、workbuddy.db（含自动化定义）、settings.json。
3. ⚠️ 注意：自动化定义虽回来，但其 `cwd` 仍指向 Windows 路径、运行依赖的本地引擎 / PortableGit / SSH key **不随同步**；需在 Mac 重新指向仓库并配环境（见 B）。

### B. GitHub 项目仓库（引擎 + 看板 + 数据）— 需手动 clone
1. 配 SSH key：`ssh-keygen -t ed25519 -C "willinxusheng@163.com"`，公钥贴 GitHub → Settings → SSH and GPG keys；`ssh -T git@github.com` 验证 `Hi willinxusheng!`。
2. `git clone git@github.com:willinxusheng/A-share-Fibonacci.git`（默认目录 `A-share-Fibonacci`）。
3. 按第 3 节建 venv + `pip install -r requirements.txt`；确保 WorkBuddy + wb-finance-skill 在 Mac 存在（analyze.py 自动探测）。
4. 数据更新由 CI 自动负责（见第 6 节），**本机无需挂 launchd / 计划任务**；仅源码改动时按需手动 `git pull --rebase` → 改 → `git push`。

> 关系：WorkBuddy 云同步管"AI 大脑"，GitHub 管"项目代码 + 看板数据"，两者独立，换机都要做。**看板数据由 CI 独家每日更新，本机不挂任何自动推送**（Windows schtasks / Mac launchd 均已废弃，勿重建）。
