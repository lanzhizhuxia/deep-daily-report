# LLM Setup Guide

deep-daily 的 pipeline 需要调用 LLM 完成 4 个步骤：话题筛选 (filter)、事件聚类 (cluster)、
长文撰写 (write)、附录生成 (appendix)。本文档指导你配置 LLM 接入。

## 前置概念

deep-daily 支持两种 backend，在 `config.yaml` 的 `llm.backend` 指定：

| Backend | 适用场景 | 读取的环境变量 |
|---|---|---|
| `openai` | 单 endpoint，单 key | `LLM_API_BASE` + `LLM_API_KEY` |
| `multikey` | LiteLLM 多 key 轮询 | `LITELLM_API_BASE` + `LITELLM_API_KEYS` |

绝大多数新用户选 `openai` 即可。下面提供三种接入路径，按复杂度递增。

---

## 方式 A：OpenRouter 直连（最简单）

[OpenRouter](https://openrouter.ai/) 是 LLM 聚合器，一个 API key 调用几乎所有主流模型，
按量付费，无需逐一注册各家厂商。

### 步骤

1. 注册 [openrouter.ai](https://openrouter.ai/)，创建 API key
2. 充值（最低 $5）
3. 编辑 `<HOME>/.env`：

```bash
LLM_API_BASE=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-your-key-here
```

4. 编辑 `<HOME>/config.yaml`，模型名带 provider 前缀：

```yaml
llm:
  backend: "openai"

models:
  filter: "deepseek/deepseek-v4-flash"
  cluster: "deepseek/deepseek-v4-flash"
  write: "google/gemini-3.1-pro-preview"
  appendix: "openai/gpt-5.4-mini"
```

5. 验证：

```bash
deep-daily --home <HOME> doctor --deep
```

`--deep` 会实际发一条 LLM 请求确认连通性。通过后即可 `run`。

### 成本参考（OpenRouter 定价，per 1M tokens）

| 步骤 | 推荐模型 | Input | Output |
|---|---|---|---|
| filter / cluster | `deepseek/deepseek-v4-flash` | $0.14 | $0.28 |
| write | `google/gemini-3.1-pro-preview` | $2.00 | $12.00 |
| appendix | `openai/gpt-5.4-mini` | $0.75 | $4.50 |

一篇典型日报约消耗 20k–50k input + 5k–15k output tokens，单次成本约 $0.10–$0.30。

---

## 方式 B：本地 LiteLLM Proxy（推荐生产环境）

如果你有多把 API key（不同厂商、不同额度），或者需要统一管理速率限制和 fallback，
可以在本机运行一个 [LiteLLM](https://docs.litellm.ai/) proxy。

### 安装

```bash
pip install litellm[proxy]
```

### 最小配置

创建 `litellm_config.yaml`：

```yaml
model_list:
  - model_name: deepseek-v4-flash
    litellm_params:
      model: openrouter/deepseek/deepseek-v4-flash
      api_key: ${OPENROUTER_API_KEY}
  - model_name: gemini-3.1-pro-preview
    litellm_params:
      model: openrouter/google/gemini-3.1-pro-preview
      api_key: ${OPENROUTER_API_KEY}
  - model_name: gpt-5.4-mini
    litellm_params:
      model: openrouter/openai/gpt-5.4-mini
      api_key: ${OPENROUTER_API_KEY}

general_settings:
  master_key: sk-local-proxy-key
```

### 启动

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
litellm --config litellm_config.yaml --port 14265
```

### deep-daily 配置

```bash
# <HOME>/.env
LLM_API_BASE=http://localhost:14265
LLM_API_KEY=sk-local-proxy-key
```

```yaml
# <HOME>/config.yaml
llm:
  backend: "openai"

models:
  filter: "deepseek-v4-flash"       # 无前缀，对齐 proxy model_name
  cluster: "deepseek-v4-flash"
  write: "gemini-3.1-pro-preview"
  appendix: "gpt-5.4-mini"
```

本地 proxy 的优势：
- 模型名统一无前缀，切换 provider 不影响 config.yaml
- 多 key 轮询 + 预算熔断（`multikey` backend）
- 统一监控和速率限制

---

## 方式 C：原生 API Key 直连

如果你已有某家厂商的 API key，可以直接用。

### OpenAI

```bash
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-...
```

```yaml
models:
  filter: "gpt-5.4-mini"
  cluster: "gpt-5.4-mini"
  write: "gpt-5.4"
  appendix: "gpt-5.4-mini"
```

### Google Gemini

```bash
LLM_API_BASE=https://generativelanguage.googleapis.com/v1beta/openai
LLM_API_KEY=your-gemini-api-key
```

```yaml
models:
  filter: "gemini-2.5-flash-lite"
  cluster: "gemini-2.5-flash-lite"
  write: "gemini-2.5-pro"
  appendix: "gemini-2.5-flash-lite"
```

> ⚠️ 模型名格式取决于各厂商的 OpenAI 兼容端点实现。启动前先 `doctor --deep` 验证。

---

## 模型选型建议

### 各步骤的需求特征

| 步骤 | 调用频率 | 每次 token 量 | 核心需求 |
|---|---|---|---|
| filter | 极高（每篇候选文章一次） | 低（几百 token） | 速度快、成本低 |
| cluster | 中（每天几十次） | 中（几千 token） | 语义理解、中文好 |
| write | 低（每天 1 次） | 高（2 万–5 万 token） | 长文质量、结构化输出 |
| appendix | 低（每天 1 次） | 低（几百 token） | 最便宜够用即可 |

### 推荐组合

| 步骤 | 推荐模型 | 原因 |
|---|---|---|
| filter | `deepseek-v4-flash` | 1M 上下文 + 中文友好 + $0.14/$0.28 极低成本 |
| cluster | `deepseek-v4-flash` | 同上，聚类也在大量候选上运行 |
| write | `gemini-3.1-pro-preview` | 强推理 + 1M 上下文 + 结构化 HTML 输出稳定 |
| appendix | `gpt-5.4-mini` | 最便宜的可用模型，附录只需简单格式化 |

### 为什么不用 DeepSeek V4 Pro 写长文？

DeepSeek V4 Pro 默认启用 reasoning（思考链），即使设 `reasoning_effort=low`
仍有 ~80% token 消耗在内部推理而非内容输出。对于需要生成长篇 HTML 的 write 步骤，
这会导致延迟数倍于 Gemini 且成本无优势。如果你能完全关闭 reasoning 模式，
V4 Pro 也是可行选择。

---

## 验证连通性

```bash
deep-daily --home <HOME> doctor --deep
```

`--deep` 会向配置的 LLM endpoint 发送 `/models` 请求确认连通性。

---

## 下一步

- 配置好 LLM 后，编辑 `configs/topics.yaml` 定义你的话题分类
- 编辑 `configs/sources.yaml` 添加 RSS 源
- 回到 [getting-started.md](getting-started.md) 继续 Step 4