# 免费 LLM 一键安装包

把已经实测验证过的免费 LLM 一键装进WorkBuddy / Claude Code，**不用注册、不用API Key、不用自己验证**。

## 为什么需要这个

市面上的「免费模型清单」基本不可信：

- 标free 的实际要充值（SiliconFlow 标 1000 RPM，实际免费账号只有 50 RPD）
- 标支持工具调用的实际返回 400
- 限流数字全是猜的，没有任何官方出处

本包内置的 3 个模型是**逐个发真实请求（含 function calling）验证过的**，
配置写进去就能用，不需要你自己再验一遍。

## 快速开始

### 方式一：直接跑脚本（推荐）

下载 `install_free_llms.py`，然后：

```bash
python install_free_llms.py
```

Windows 如果 `python` 不在 PATH，用 `py install_free_llms.py`。

脚本会自动：备份配置 → 写入 3 个模型 → **发真实请求逐个验证** → 报告结果。

### 方式二：装成WorkBuddy 技能（自动触发）

把 `free-llm-installer/` 整个目录放到 `~/.workbuddy/skills/` 下重启 WorkBuddy。
之后只要说「装免费模型」「免费 LLM 一键装」「验证免费模型」，WorkBuddy 会自动调用。

```
~/.workbuddy/skills/free-llm-installer/
├── SKILL.md
└── scripts/install_free_llms.py
```

## 内置模型

|模型 id | 端点 | 上下文 | 工具调用 | 读图 |
|---|---|---|---|---|
| `kilo-auto/free` | Kilo Gateway | 256k | ✅ | ❌ |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | Kilo Gateway | 1000k | ✅ | ❌ |
| `space-bunny-free` | OpenCode Zen | — | ✅ | ✅ |

- **Kilo Gateway**（`https://api.kilo.ai/api/gateway`）：免注册免 key，18 个免费模型，首选。
- **OpenCode Zen**（`https://opencode.ai/zen/v1`）：免 key，但免费层已收紧，
  只有 `space-bunny-free` 还能从 API 直连。

**kilo-auto/free 和 nemotron 是纯文本模型，不支持读图。** 读图请用
`space-bunny-free`（Zen，真·免 key）。

⚠️ **space-bunny-free 的免费额度绑在出口 IP 上，按 UTC 天重置（北京时间 08:00）。**
    不需要 key，也不看 UA/Cookie——**同一 IP 下所有用户共享一份日额度**。
    VPN / 代理 / 运营商 NAT 都可能让多人共用一个 IP，别人刷完你跟着锁。
    429 报错带 `Retry-After`（秒），指到 UTC 00:00 就是额度重置时间。
    证据：OpenCode Zen 维护者 GitHub issue #1659（同请求换出口 IP，429↔200）。
    → 排查：`api.ipify.org` 看出口 IP；换网络/换 VPN 出口再试，变 200 就证实。

建议把 `kilo-auto/free` 设为默认——它会自动路由到当前可用的模型，
某个模型挂了会自动切换，比绑定单个模型省心。

## 全部命令

```bash
python install_free_llms.py                # 安装 + 自动验证
python install_free_llms.py --dry-run      # 只看要做什么，不写文件
python install_free_llms.py --verify       # 只重新验证已装的模型
python install_free_llms.py --list         # 列出当前已配置模型
python install_free_llms.py --refresh      # 拉服务端最新免费清单并补装
python install_free_llms.py --uninstall    # 移除本脚本添加的模型
python install_free_llms.py --force        # 覆盖同 id 的现有配置
python install_free_llms.py --deepseek-key sk-xxx   # 顺便加 DeepSeek
```

零依赖，只用Python 标准库。Python 3.7+ 均可，Windows / macOS / Linux 通用。

## 监控：免费模型会不会突然挂

免费渠道最常见的翻车方式是「昨天还好好的，今天突然 429 或 404」。
`--watch` 专门解决这个：

```bash
python install_free_llms.py --watch                # 前台监控，默认每 30 分钟一轮
python install_free_llms.py --watch --watch-interval 3600   # 改成每小时
python install_free_llms.py --watch --watch-rounds 10        # 跑10 轮就退出（配计划任务用）
```

设计要点：

- **每个模型每轮只发 1 个请求**，且这个请求带 `tools`——
  一次请求同时验证「能不能连」「工具调用还在不在」「要不要钱」。
- **只在状态变化时报告**，正常时一行都不输出，不刷屏。
- 状态分 5 档：

  | 状态 | 含义 | 算故障吗 |
  |---|---|---|
  | `OK` | 可用，工具调用正常 | — |
  | `NO_TOOL` | 能对话，但不再返回 `tool_calls` | ⚠️ 不能当 agent 主模型了 |
  | `RATELIMIT` | 429，免费池高峰限流 | 否，正常现象 |
  | `DOWN` | 404/410，模型下线 | 是 |
  | `ERROR` | 其他异常 | 是 |

- 状态持久化在 `~/.workbuddy/free_llm_watch_state.json`，
  变化历史追加在 `~/.workbuddy/free_llm_watch.log`（首轮写基线，之后只记变化）。

实测三轮（间隔 60s）跑出来的真实变化：

```
[14:10:21] 第 1 轮  4/4 可用
[14:11:36] 第 2 轮  stepfun/step-5-preview-free  限流   <<< OK -> RATELIMIT
[14:12:51] 第 3 轮  stepfun/step-5-preview-free  可用   <<< RATELIMIT -> OK
```

日志里只有两行变化记录，4/4 全程可用的模型一个字都没浪费。

想让它常驻，Windows 用计划任务、Linux/macOS 用 cron 挂上即可：

```bash
# 每 30 分钟跑一轮检查（Windows 计划任务 / cron 均可）
python install_free_llms.py --watch --watch-rounds 1
```

## 安全设计

- **每次写入前自动备份**到 `~/.workbuddy/backups/models.json.bak-<时间戳>`
- **原子写入**（先写 `.tmp` 再替换），不会写坏配置
- **配置损坏时拒绝写入**并报错，不覆盖用户数据
- **`--uninstall` 只删本脚本加的**（按 vendor 识别），保留你自己的模型
- **幂等**：重复运行不会重复添加

## 常见问题

**Q:429 报错怎么办？**
先看报错类型：

- **`FreeUsageLimitError`（OpenCode Zen / space-bunny）**：额度绑在**出口 IP** 上，
  按 UTC 天重置（北京时间 08:00）。同一 IP 下所有用户**共享一份日额度**——
  VPN / 代理 / 运营商 NAT 都可能让多人共用一个 IP，别人刷完你跟着锁。
  报错里带 `Retry-After`（秒），指到 UTC 00:00 就是重置时间。
  排查：`api.ipify.org` 看出口 IP；换网络/换 VPN 出口再试，变 200 就证实。
- **普通 429（Kilo 网关）**：免费池高峰限流，等几分钟重试。这不是配置错误。
  `--verify` 对 429 会自动重试 2 次。

**Q:401 / PAID_MODEL_AUTH_REQUIRED？**
两个可能：① `apiKey` 写成了空串，应该用单空格 `" "`；
② 模型 id 被简化了，必须写完整的 `provider/model:free`（不能写成 `xxx-free`）。

**Q: 能读图吗？**
不能。实测 `kilo-auto/free` 和 `dots-3-note-preview` 收图片时返回 200 但
`image_tokens=0`，模型在瞎猜；`nemotron-3-ultra`、`step-5-preview` 直接
404「不支持图片输入」。所以配置里写的是 `supportsImages: false`。

**Q: 免费模型能当主力吗？**
不建议。agent 循环一轮任务要 5-20 次调用，一天几百次，
免费额度几天就烧完。适合调 prompt、试模型性格、零成本横向评测。

**Q: 数据隐私？**
免费渠道的数据可能进上游训练集。不要放密钥、用户数据、生产代码。

## 已知的坑（都已在脚本里处理）

| 坑 | 表现 | 本脚本的处理 |
|---|---|---|
| Cloudflare 按 UA 拦截 | Python 默认 UA → 403 `code:1010` | 强制带浏览器 UA |
| 空 apiKey 回落 | 回落宿主平台 token → 鉴权失败 | 用单空格 `" "` |
| id 简化 | → 401 PAID_MODEL_AUTH | 内置完整 id |
| SSL 瞬时中断 | 偶发 URLError | 自动重试 2 次 |
| 429 限流 | 免费池高峰 | 工具调用阶段重试 2 次 |

## 免责声明

免费模型政策随时会变。上面的实测数据是 2026-10-10 的状态，
过几天可能已经有模型下线。`--refresh` 能拉到当前真实清单，
但**「能列出来」≠「能调用」**，新模型务必先 `--verify` 确认。

各平台的免费额度、数据处理条款请参考官方说明。
本项目不对免费服务的可用性做任何保证。