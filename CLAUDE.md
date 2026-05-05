# CLAUDE.md — deep-daily-report

给未来的 Claude/Codex：**别再翻仓库找日报在哪了**。

> 本文件只记录通用结构与路径约定（相对路径 / 环境变量占位符）。
> 机器/用户相关的绝对路径、launchd Label、运行日志位置等私密信息写在 **`CLAUDE.local.md`**（已 gitignore），需要时一并阅读。

---

## 1. 代码 vs 数据 分离

| 类型 | 路径 | 说明 |
|---|---|---|
| **代码** | 本仓库根目录 | 改代码只看这里 |
| **数据根（runtime）** | `~/.local/deep-daily/legacy-data/`（默认） | 所有抓取/生成产物 |
| 仓库内 `./data/` | `data/{articles,dailies,tweets}/` | **空占位目录**，永远是空的，别在这里找数据；已 gitignore |

> 数据根默认值来自 `src/deep_daily/config.py::_default_data_root()`（第 70-71 行）：
> `Path.home() / ".david" / "data" / "rss"`
> 可通过 `configure_paths(data_root=...)` 或 `readers.yaml` 里 `defaults.data_root` 字段覆盖。

---

## 2. 每日产出（最常用）

**路径**：`<DATA_ROOT>/dailies/`（默认 `~/.local/deep-daily/legacy-data/dailies/`）

每天清晨由定时任务生成两个文件：

| 文件 | 大小量级 | 内容 |
|---|---|---|
| `YYYY-MM-DD.html` | ~80–90 KB | 完整渲染版日报（浏览器直接看） |
| `YYYY-MM-DD.json` | ~1 KB | 元数据/结构化数据（事件列表、归类等） |

**中间产物**：`<DATA_ROOT>/dailies/.pipeline/YYYY-MM-DD/`
  存去重、聚类、撰写每步缓存，调试 pipeline 时看这里。

---

## 3. 其它关键数据文件（均位于 `<DATA_ROOT>` 下）

| 相对路径 | 用途 |
|---|---|
| `articles/` | RSS 抓回原始文章 JSON（数千个文件） |
| `tweets/` | Twitter/X 原始推文 JSON（数千个文件） |
| `tweets-nas/` | Twitter 推文 NAS 归档 |
| `news-6551/` | 6551 News API 抓到的内容 |
| `reported_events.json` | 已报事件去重库（7 天 TTL） |
| `reader-profile.yaml` | 读者画像 |
| `active-systems.yaml` | 活跃系统配置 |
| `dynamic-topics.json` / `dynamic-kols.json` | 动态主题/KOL |
| `twitter-kols.json` | Twitter KOL 主列表 |
| `state.json` | 抓取 state |
| `fetch.log` | RSS 抓取日志 |
| `digests/` | 旧版 digest 归档 |

---

## 4. 定时任务（launchd，macOS 本机）

**不在本仓库内**，在 dotfiles 里。换机要重新部署。

- 触发：每日清晨一次
- 机制：launchd `StartCalendarInterval`
- 脚本职责：`source` 环境 → `python -m deep_daily generate --publisher feishu` → 成功发 WEA 通知；失败 30 分钟后 `--resume` 重试一次；仍失败 exit 1

> 具体的 plist Label、脚本路径、日志文件位置见 **`CLAUDE.local.md`**。

**项目自身无内置调度器**（无 APScheduler/cron 依赖）。`configs/topics.yaml` 里的 `"cron"`/`"scheduler"` 是**内容主题关键词**，不是定时配置。

手动跑一次：
```bash
python -m deep_daily generate --publisher feishu
# 或断点续跑：
python -m deep_daily generate --resume --publisher feishu
```

---

## 5. 常见陷阱

- ❌ 在仓库 `./data/dailies/` 找日报 → **永远是空的**，去 `<DATA_ROOT>/dailies/`
- ❌ 在仓库内搜 "cron/schedule" 找定时配置 → 只会命中 `topics.yaml` 的主题词；真调度在 `~/Library/LaunchAgents/`（见 `CLAUDE.local.md`）
- ❌ 直接改仓库里的 `configs/topics.yaml` 以为能影响生成路径 → 路径走 `config.py`，不走 topics.yaml
- ✅ 改生成路径：要么传 `configure_paths(data_root=...)`，要么在对应 `readers.yaml` 里加 `defaults.data_root`

---

## 6. 隐私/敏感数据约定

| 类型 | 存放位置 | 是否入库 |
|---|---|---|
| LLM API Key / OPENNEWS_TOKEN | `.env.local` | ❌ gitignore |
| 机器绝对路径、launchd Label、日志路径 | `CLAUDE.local.md` | ❌ gitignore |
| 运行时产出、读者画像、抓取缓存 | `<DATA_ROOT>/`（家目录外） | ❌ 物理分离 |
| 运行日志 | `logs/`（项目内临时） | ❌ gitignore |
| 代码、配置模板、KOL 种子 | 仓库 tracked 文件 | ✅ |
