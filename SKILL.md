---
name: free-llm-installer
slug: free-llm-installer
displayName: 免费 LLM 一键安装
version: 1.1.1
description: 一键安装并实测验证已验证可用的免费 LLM 到 WorkBuddy/Claude Code，无需注册、无需 API Key。当用户说「装免费模型」「加免费模型」「免费 LLM」「免费模型一键装」「验证免费模型可用吗」「免费模型怎么接入」「帮我配几个免费模型」「监控免费模型」「免费模型挂了吗」「kilo 网关」「kilo-auto」「nemotron 免费」「space-bunny」「智谱 glm 免费」「zhipu 免费」「glm-4-flash」「glm-4.7-flash」「glm-z1-flash」「glm-4v-flash」「探测国外免费模型」「国外平台免费 llm」「优先用免费模型」时使用。也用于验证已有免费模型配置是否真的可用（tool calling 是否支持、是否限流、是否支持读图），以及定期 watch 监控免费模型是否下线/限流/失去工具调用能力。
agent_created: true
---

# 免费 LLM 一键安装与实测验证

## 核心原则

**「标 free」不等于「真能用」。** 本技能内置的模型都是逐个发真实请求
（含 function calling）验证过的。使用者不需要自己再验证一遍。

已踩过的坑，不要重复：

|坑 | 表现 | 正确做法 |
|---|---|---|
| 模型 id 被简化 | `kilo-auto-free` → 401 PAID_MODEL_AUTH_REQUIRED | 必须写完整 `provider/model:free` |
| 空 apiKey | 回落宿主平台 token → 鉴权失败 | Kilo 用字面量 `"anonymous"`；智谱填真实 key |
| 假多模态 | 返回 200 但 `image_tokens=0`，模型瞎猜 | Kilo 纯文本模型写 `supportsImages: false`；omni 经 base64 图实测可识别，写 `true` |
| 假工具调用 | 能对话但从不返回 `tool_calls` | 必须发带 `tools` 的请求实测；glm-z1-flash / glm-4v-flash 不输出结构化工具调用，标记 `supportsToolCall: false` |
| Cloudflare拦 UA | 403 `code:1010` | 必须带浏览器 User-Agent（脚本已内置） |
| **Zen 免费层封 API 直连（2026-10-11）** | `space-bunny-free` → 403 "free tier can only be used from within OpenCode" | 上游政策收紧，本地配置无解，**已从默认清单移除**。如需使用需走 OpenCode 客户端 |
| **WorkBuddy ≥5.8.0 拒纯空白 key** | 单空格 `" "` 被 UI 判为「未配置接口地址或 API key」标红禁用 | Kilo 用 `anonymous`（实测 200，trim 后非空可过校验） |
| **Kilo 假 key 会 401** | `Bearer free`/`x`/`0` → 401 INVALID_TOKEN | Kilo 匿名 token 只有两个：空串和字面量 `anonymous`。占位一律用 `anonymous` |
| **智谱免费档高峰过载** | glm-4.7-flash 高峰 429；glm-4.6v-flash 持续 1305「访问量过大」 | 日常用 `glm-4-flash`（永久免费·最稳）；`glm-4.7-flash` 非高峰用；`glm-4.6v-flash` 暂不可用 |
| **推理模型吃 token** | max_tokens 太小 → 响应被截断成无 `choices` | 推理模型 verify/probe 时 `max_tokens` 给到 ≥400~600 |
| **智谱视觉模型用「文本框」伪工具调用** | `glm-4.1v-thinking-flash` / `glm-4.6v-flash` 发 tools 请求 → `finish_reason=stop`、`tool_calls=None`，把调用写成 `<|begin_of_box|>get_time(city='北京')<|end_of_box|>` 文本 | 智谱自定义格式，**非标准 OpenAI tool_calls**，WorkBuddy/Claude Code 都读不了 → **不能用于 agent / 不能进切换器**。读图能力是真的（实测可识别颜色），仅限 WorkBuddy 纯聊天/读图用 |
| **1×1 像素图被智谱拒** | 智谱视觉模型对极小图报 400「图片输入格式/解析错误」 | 用 ≥64×64 的正常图测读图，非模型问题 |

## 内置模型（2026-10-11 实测）

### 免 key 梯队（Kilo Gateway，无需注册）
| 模型 id | 端点 | 上下文 | 工具调用 | 读图 |
|---|---|---|---|---|
| `kilo-auto/free` | Kilo Gateway | 256k | ✅ | ❌ |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | Kilo Gateway | 256k | ✅ | ✅（base64 图实测可识别） |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | Kilo Gateway | 1000k | ✅ | ❌ |

> omni 模型是免费读图兜底：免 key（apiKey 填 `anonymous`）、能读图、能调工具。
> 注意 reasoning 模型会先吐 reasoning 吃 token，`max_tokens` 给到 ≥1024 否则响应会被截断。

### 智谱 BigModel 梯队（需 key，但有**永久免费**档，效果最好）
> 用 `--zhipu-key <KEY>` 安装；key 形如 `xxxxxxx.YYYYYYYY`（用户已有，见 glm profile）。
| 模型 id | 免费性质 | 工具调用 | 读图 | 推理 |
|---|---|---|---|---|
| `glm-4-flash` | 永久免费 | ✅ | ❌ | ❌ | 日常主力·最稳 |
| `glm-4.7-flash` | 免费·高峰易 429 | ✅ | ❌ | ✅ | 带推理·质量更好 |
| `glm-z1-flash` | 完全免费 | ❌ | ❌ | ✅ | 数学/代码推理，不输出结构化工具（仅聊天/推理用） |
| `glm-4v-flash` | 永久免费 | ❌ | ✅ | ❌ | 免费读图，无工具（仅聊天/读图用） |
| `glm-4.1v-thinking-flash` | 免费 | ❌ | ✅ | ✅ | 视觉+推理最强，但工具调用是智谱自定义「文本框」格式（非标准 tool_calls），**不能用于 agent / 不能进切换器**；读图实测可用（仅 WorkBuddy 聊天/读图用） |
| `glm-4.6v-flash` | 免费·高峰持续 1305/500 | ❌ | ✅ | ❌ | 与 4.1v 同为视觉模型，但服务端极不稳定（实测连续 429→500→429），工具调用格式同为文本框。**暂不可用，不加** |

> **Claude Code 切换器只放支持标准工具调用的模型**（`glm-4-flash` / `glm-4.7-flash`），
> 因为 Claude Code 靠工具调用干活；z1/v/4.1v/4.6v 都不输出结构化 `tool_calls`，加切换器会断 agent 循环，仅 WorkBuddy 聊天/读图用。

### 已移除 / 不可用
- `space-bunny-free`（OpenCode Zen）：2026-10-11 起上游 403 封 API 直连，暂不可用。
- `glm-4.6v-flash`：持续 1305 服务器过载，用户侧无解，不加。

## 国内外平台免费格局（时效内 + 效果好）

| 平台 | 免费模型 | 状态 | 能力 | 本机可达 |
|---|---|---|---|---|
| **智谱 BigModel**（需 key） | GLM-4-Flash / GLM-4.7-Flash / GLM-Z1-Flash / GLM-4V-Flash | ✅ 永久/限时免费 | 文本/工具/推理/读图 | ✅ 直连 200 |
| **Kilo 网关**（免 key） | kilo-auto/free / nemotron-omni / nemotron-550b | ✅ 稳定 | 文本/工具/读图(omni) | ✅ 307 |
| **硅基流动 SiliconFlow**（需国内手机注册） | GLM 系列蒸馏 / DeepSeek-R1-Distill 等 | ✅ 免费·1K RPM | 文本为主 | 本机暂无 key |
| **OpenRouter**（需 key） | 19 个 `:free` 模型 | 列表可达·调用需 key | 多数文本 | ⚠️ 仅列表可达（用户账号被封则无法调用） |
| Groq / Together / HF / Mistral / Google | 各有免费档 | ❌ 国内被 CF/代理封 | — | ❌ 不可达/需 key |

**结论**：本机最稳的免费组合 = **GLM-4-Flash（文本+工具）+ GLM-4V-Flash（读图）+ GLM-Z1-Flash（推理）**（智谱一个 key，30 并发，永久免费）+
**Kilo 两个免 key 模型**作零注册备用。DeepSeek-V4 是**付费**的，自动模式易烧钱，非必要别用。

## 执行流程

### 场景 A：用户要装免费模型

```bash
python scripts/install_free_llms.py                 # 装 Kilo 免 key 三件套 + 验证
python scripts/install_free_llms.py --zhipu-key KEY # 额外装智谱 GLM 永久免费梯队
```

建议把 `kilo-auto/free` 或 `glm-4-flash` 设为默认（自动路由 / 最稳）。

### 场景 B：用户要验证已有配置

```bash
python scripts/install_free_llms.py --verify
```

发真实请求逐个验证。标记 `supportsToolCall:false` 的模型（glm-z1/v）只验证对话可用，不要求工具调用。
常见失败原因：
- `429` → 免费池高峰限流，等几分钟重试，**不是配置错**
- `401` → apiKey 写错或 id 被简化
- `未调工具` → 该模型不支持 function calling，不能用于 agent
- `HTTP404` → 模型已下线，用 `--refresh` 拉当前清单

### 场景 C：用户问「现在还有哪些免费模型 / 国外平台有免费吗」

```bash
python scripts/install_free_llms.py --probe-foreign   # 探测国外平台免费 LLM
python scripts/install_free_llms.py --refresh --dry-run  # 从 Kilo 拉实时清单
```

`--probe-foreign` 会实测各国外平台可达性，并从 OpenRouter 公开列表拉出当前所有定价为 0 的免费模型。
**注意**：新拉到的模型不在实测清单内，必须先 `--verify` 确认工具调用可用再推荐。

### 场景 D：用户要定期巡检（定时任务 / 每周报告）

```bash
python scripts/install_free_llms.py --report          # 一轮体检 + 国外探测，输出汇总
python scripts/install_free_llms.py --watch           # 常驻后台，状态变化才报告
```

`--report` 适合做成自动化（每周跑一次，出变化报告）。
`--watch` 长期挂机（Windows 可交给计划任务），每 30 分钟一轮，只在状态变化时打印。

```bash
python scripts/install_free_llms.py --watch --watch-interval 1800
python scripts/install_free_llms.py --watch --watch-rounds 3 --watch-interval 3600
```

状态持久化在 `~/.workbuddy/free_llm_watch_state.json`；变化写 `~/.workbuddy/free_llm_watch.log`。

### 场景 E：用户要清理

```bash
python scripts/install_free_llms.py --uninstall
```

只移除本脚本添加的（按 `vendor` 识别），保留用户自己的模型。

## 其他命令

```bash
python scripts/install_free_llms.py --list                # 列出已配置的模型
python scripts/install_free_llms.py --dry-run             # 只看要做什么，不写文件
python scripts/install_free_llms.py --force               # 覆盖同 id 的现有配置
python scripts/install_free_llms.py --zhipu-key KEY      # 安装智谱 GLM 梯队
python scripts/install_free_llms.py --deepseek-key sk-xxx # 顺便加 DeepSeek（付费！）
python scripts/install_free_llms.py --models /path/to/models.json
python scripts/install_free_llms.py --watch --watch-log /path/to/watch.log
```

## 监控相关参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--watch` | 关 | 开启定期探测模式 |
| `--watch-interval` | `1800`（秒） | 两轮之间的间隔 |
| `--watch-rounds` | 无限 | 跑够 N 轮后自动退出 |
| `--watch-gap` | `2.0`（秒） | 同轮内模型间的请求间隔 |
| `--watch-log` | `~/.workbuddy/free_llm_watch.log` | 变化日志路径 |

## 关键实现细节

- **配置文件位置**：`~/.workbuddy/models.json`（脚本自动探测，也支持 `XDG_CONFIG_HOME`）。
- **原子写入**：先写 `.tmp` 再 `os.replace`，避免写一半损坏。
- **每次写入前自动备份**到 `backups/models.json.bak-install-free-<时间戳>`。
- **网络抖动重试**：SSL 中断等 `NET` 类错误自动重试 2 次（间隔 3s）；429 在工具验证阶段重试 2 次（间隔 8s）。
- **非法 JSON 拒绝写入**：配置损坏时报错退出，不覆盖用户数据。
- **必须带浏览器 UA**：Kilo / Zen 前有 Cloudflare，Python 默认 UA 会被拦。
- **推理模型 token 预算**：verify/probe 对 `supportsReasoning` 模型自动放大 `max_tokens`，避免 reasoning 截断。

## 注意事项（转述给用户时用）

- 免费模型**不适合当生产主力**：额度小，agent 循环一轮要 5-20 次调用。
- 免费池高峰会 429（尤其智谱 glm-4.7-flash / Kilo omni 偶发 16/16）。`kilo-auto/free` 的自动路由能缓解这个问题。
- 国外平台（Groq/Together/HF/Mistral/Google）从国内网络基本不可达或被封；唯一稳定可达且免 key 的是 OpenRouter 公开列表。
- 免费渠道的数据可能进上游训练集，**不要放密钥、用户数据、生产代码**。
- 政策随时变。`--refresh` / `--probe-foreign` 能查到当前真实情况，但「能列出来」≠「能调用」，仍需 `--verify` 确认。
- 免费模型会突然限流或下线。`--watch` / `--report` 挂后台跑就能第一时间知道。
