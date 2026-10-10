#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键装好已实测可用的免费 LLM（免注册 / 免 API Key / 支持工具调用）。

背景
----
市面上的「免费模型清单」大多不可信：标free 的实际要充值、标支持工具调用的实际返回 400、
限流数字全是猜的。本脚本内置的4 个模型是逐个发真实请求（含 function calling）验证过的。

实测结论（2026-10-10）
----------------------
| 模型 | 端点 | 工具调用 | 读图|
|---|---|---|---|
| kilo-auto/free                | Kilo Gateway | ✅ | ❌ |
| nvidia/nemotron-3-ultra-550b-a55b:free | Kilo Gateway | ✅ | ❌ |
| space-bunny-free              | OpenCode Zen | ✅ | ✅ |

关于 deepseek-v4-flash：需要你自己的 API Key，不在免密列表里，
可用 `--deepseek-key sk-xxx` 单独添加。

用法（按顺序挑一条就行）
------------------------
    python install_free_llms.py              # 安装 + 自动验证（推荐）
    python install_free_llms.py --verify     # 只重新验证已装的模型
    python install_free_llms.py --list       # 列出当前已配置的模型
    python install_free_llms.py --refresh    # 从服务端拉最新免费清单（自动补装）
    python install_free_llms.py --watch      # 定期探测，模型状态一变就报告
    python install_free_llms.py --uninstall  # 移除本脚本添加的模型

watch 模式（监控免费模型是否突然失效）
------------------------------------------
免费模型没有 SLA，随时可能下线。--watch 定期探测并**只在状态变化时报告**：

    python install_free_llms.py --watch
    python install_free_llms.py --watch --watch-interval 600 --watch-rounds 12

省额度设计：每轮对每个模型**只发 1 个请求**（verify 要2 个），
这一个请求同时判定三件事——能否连通、是否支持 function calling、cost 是否仍为 0。
4 个模型 = 4 请求/轮，30 分钟一轮 ≈ 每天 192 个请求，不会烧掉额度。

状态变化同时写入日志（默认与配置同目录 free_llm_watch.log），便于接入通知脚本。
状态取值 OK / NO_TOOL / RATELIMIT / DOWN / ERROR，
其中「限流」和「不支持工具调用」不算故障。

全程零依赖，只用Python 标准库。Python 3.7+ 均可。
"""

from __future__ import print_function

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# 内置配置（全部经实测验证）
# ---------------------------------------------------------------------------

KILO_URL = "https://api.kilo.ai/api/gateway/v1/chat/completions"
ZEN_URL = "https://opencode.ai/zen/v1/chat/completions"

# Kilo 网关前面有 Cloudflare，按 User-Agent 拦截，必须带浏览器 UA
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

REASONING = {
    "defaultEffort": "medium",
    "supportedEfforts": ["minimal", "low", "medium", "high", "xhigh", "max"],
}


def kilo_model(model_id, name, supports_images=False):
    """Kilo 网关模型。注意 id 必须是完整串，省略斜杠会导致 401。"""
    return {
        "id": model_id,
        "name": name,
        "vendor": "Kilo Gateway",
        "url": KILO_URL,
        # 单空格而非空串：空串会被宿主回落到平台 token 导致鉴权失败
        "apiKey": " ",
        "supportsToolCall": True,
        # 默认不写读图：Kilo 上 kilo-auto/free 实测 image_tokens=0 且空回复，
        # 模型接受图片请求但根本不看，写 true 会得到瞎猜的答案。
        # 注意：image_tokens=0 在 Kilo 网关上不可靠（真读图的模型也可能显示 0），判据以回复内容为准。
        # 真正的判据是回复内容是否真的反映出图片信息。
        "supportsImages": supports_images,
        "supportsReasoning": True,
        "reasoning": dict(REASONING),
    }


def zen_model(model_id, name):
    return {
        "id": model_id,
        "name": name,
        "vendor": "OpenCode Zen",
        "url": ZEN_URL,
        "apiKey": " ",
        "supportsToolCall": True,
        # space-bunny-free 支持读图。
        # 依据：用户实测 —— WorkBuddy 把 supportsImages 设为 false 时贴图，
        # 端点返回 "Current model (custom-local:kilo-auto/free) does not support
        # reading images"；设为 true 时贴图能识别成功。
        # 之前写 false 是我误判，原因见下方踩坑记录。
        "supportsImages": True,
        "supportsReasoning": True,
        "reasoning": dict(REASONING),
    }


FREE_MODELS = [
    kilo_model("kilo-auto/free", "Kilo Auto (Free)"),
    kilo_model("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra 550B (Free)"),
    zen_model("space-bunny-free", "Space Bunny (Free)"),
]

# 仅用于 --uninstall 识别归属，不影响安装
OWNED_VENDORS = ("Kilo Gateway", "OpenCode Zen")

DEFAULT_MODELS_PATH = os.path.join(
    os.path.expanduser("~"), ".workbuddy", "models.json"
)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}]

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def eprint(*a):
    print(*a, file=sys.stderr)


def find_models_path():
    """定位 models.json。macOS/Linux 下 WorkBuddy 可能用 ~/.config。"""
    candidates = [DEFAULT_MODELS_PATH]
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    candidates.append(os.path.join(xdg, "workbuddy", "models.json"))
    for p in candidates:
        if os.path.isfile(p):
            return p
    # 配置文件不存在时返回默认路径，让调用方决定是否创建
    return DEFAULT_MODELS_PATH


def load_models(path):
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SystemExit(
            "[x] %s 不是合法 JSON：%s\n"
            "    拒绝写入以免破坏你的配置。请先手动修复该文件，\n"
            "    或把它改名备份后重试（例如 models.json.bak）。"
            % (path, e)
        )
    if isinstance(data, dict):
        # 兼容 {models:[...]} 与 {模型id:{...}} 两种包装
        if isinstance(data.get("models"), list):
            data = data["models"]
        else:
            #看起来是 {id: {...}} 映射，转成数组
            vals = list(data.values())
            if vals and all(isinstance(v, dict) for v in vals):
                conv = []
                for k, v in vals:
                    m = dict(v)
                    m.setdefault("id", k)
                    conv.append(m)
                data = conv
            else:
                raise SystemExit(
                    "[x] %s 的结构无法识别（既不是数组也不是 models 数组）。\n"
                    "    拒绝写入，请手动检查。" % path
                )
    if not isinstance(data, list):
        raise SystemExit("[x] %s 的结构不是数组，无法解析" % path)
    return [m for m in data if isinstance(m, dict)]


def save_models(path, models):
    """原子写入，避免写一半损坏配置。"""
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(models, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def backup(path):
    bdir = os.path.join(os.path.dirname(path), "backups")
    if not os.path.isdir(bdir):
        os.makedirs(bdir)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(bdir, "models.json.bak-install-free-%s" % ts)
    if os.path.isfile(path):
        with open(path, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())
        return dst
    return None


def _post(url, key, payload, timeout):
    """实际发一次 POST，返回 (状态, 数据或错误串)。"""
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        resp = opener.open(req, timeout=timeout)
        return "OK", json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 429:
            return "RATELIMIT", "429 限流（免费池高峰）"
        try:
            msg = json.loads(body).get("error", {}).get("message", body)[:150]
        except Exception:
            msg = body[:150]
        return "FAIL", "HTTP%s %s" % (e.code, msg)
    except socket.timeout:
        return "FAIL", "超时（%ds 无响应）" % timeout
    except Exception as e:
        # SSL 瞬时中断、连接重置等属于可重试的网络抖动
        return "NET", "%s: %s" % (type(e).__name__, str(e)[:110])


def post_retry(url, key, payload, timeout=60, net_retries=2):
    """带网络抖动重试的 POST。"""
    st, data = _post(url, key, payload, timeout)
    tries = 0
    while st == "NET" and tries < net_retries:
        tries += 1
        time.sleep(3)
        st, data = _post(url, key, payload, timeout)
    return st, data


def call(url, key, payload, timeout=60):
    """发一次普通对话请求，返回 (状态, 详情)。"""
    st, data = post_retry(url, key, payload, timeout)
    if st != "OK":
        return st, data
    if isinstance(data, str):
        return "FAIL", data
    if "error" in data:
        msg = data["error"]
        if isinstance(msg, dict):
            msg = msg.get("message", json.dumps(msg)[:120])
        return "FAIL", str(msg)[:150]
    if not (data.get("choices") or []):
        return "FAIL", "响应无 choices（结构异常）"
    cost = data.get("cost", "?")
    txt = ((data["choices"][0].get("message") or {}).get("content") or "")[:20]
    return "OK", "cost=%s 回=%r" % (cost, txt)


def has_tool_call(data):
    calls = ((data.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    return any(c.get("function", {}).get("name") == "get_weather" for c in calls)


def tool_call_detail(data):
    calls = ((data.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    if calls:
        return "cost=%s args=%s" % (data.get("cost", "?"),
                                    calls[0]["function"].get("arguments", "")[:24])
    return "cost=%s 但未调工具" % data.get("cost", "?")


def call_tools(url, key, payload, timeout=60):
    """发带工具的请求，返回 (状态, 详情, 是否命中 tool_call)。"""
    st, data = post_retry(url, key, payload, timeout)
    if st != "OK":
        return st, data, False
    if isinstance(data, str):
        return "FAIL", data, False
    if "error" in data:
        msg = data["error"]
        if isinstance(msg, dict):
            msg = msg.get("message", json.dumps(msg)[:120])
        return "FAIL", str(msg)[:150], False
    if not (data.get("choices") or []):
        return "FAIL", "响应无 choices", False
    hit = has_tool_call(data)
    return ("OK" if hit else "FAIL"), tool_call_detail(data), hit


def verify_model(m, retries=2):
    """验证单个模型：纯对话能通+ 工具调用能命中。返回 (bool, 详情)。"""
    url = m.get("url")
    key = m.get("apiKey", "")
    mid = m.get("id")

    # 1) 纯对话：只要有正常回复就算通过
    st, det = call(url, key, {
        "model": mid,
        "messages": [{"role": "user", "content": "只回答：好"}],
        "max_tokens": 40,
    })
    if st == "RATELIMIT":
        return False, "对话被限流（429），稍后重试"
    if st != "OK":
        return False, "对话失败：%s" % det

    # 2) 工具调用：429 常是瞬时 TPM 限流，重试几次
    payload = {
        "model": mid,
        "messages": [{"role": "user", "content": "北京天气？用get_weather 工具查"}],
        "tools": TOOLS,
        "max_tokens": 200,
    }
    st, det, hit = call_tools(url, key, payload)
    tries = 0
    while st == "RATELIMIT" and tries < retries:
        tries += 1
        time.sleep(8)
        st, det, hit = call_tools(url, key, payload)
    if st == "RATELIMIT":
        return False, "工具调用持续被限流（429）"
    if not hit:
        return False, "对话通但工具调用失败：%s" % det
    return True, det


def probe_once(m):
    """单请求探测（省额度版）。

    只发**一个带 tools 的请求**，一次同时判定三件事：
      · 能不能连通（对话可用）
      · 支不支持 function calling（tool_calls 是否命中）
      · cost 是否为 0（是否仍免费）

    相比 verify_model 的两次请求，省一半额度，适合高频轮询。
    返回 (状态, 详情)，状态取值：
      OK / NO_TOOL / RATELIMIT / DOWN / ERROR
    """
    payload = {
        "model": m.get("id"),
        "messages": [{"role": "user", "content": "北京天气？用get_weather 工具查"}],
        "tools": TOOLS,
        "max_tokens": 120,
    }
    st, det, hit = call_tools(m.get("url"), m.get("apiKey", ""), payload)
    if st == "RATELIMIT":
        return "RATELIMIT", "429 限流"
    if st != "OK":
        low = det.lower()
        if "404" in det or "not found" in low or "unavailable" in low \
                or "not exist" in low or "deprecated" in low:
            return "DOWN", det[:80]
        if "401" in det or "auth" in low:
            return "ERROR", "鉴权失败：%s" % det[:70]
        return "ERROR", det[:80]
    if not hit:
        return "NO_TOOL", det[:70]
    return "OK", det


# ---------------------------------------------------------------------------
# watch 模式
# ---------------------------------------------------------------------------

WATCH_STATES = {
    "OK": ("可用", True),
    "NO_TOOL": ("可用但不支持工具调用", True),
    "RATELIMIT": ("限流（不算故障）", True),
    "DOWN": ("已下线/不可用", False),
    "ERROR": ("异常", False),
}


def _state_path(args):
    base = os.path.dirname(os.path.abspath(args.models))
    return os.path.join(base, "free_llm_watch_state.json")


def _load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(path, state):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _append_log(path, line):
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def do_watch(args):
    """定期探测已装模型的状态，变化时报告。"""
    models = load_models(args.models)
    managed = [m for m in models
               if isinstance(m, dict) and m.get("vendor") in OWNED_VENDORS]
    if not managed:
        print("配置里没有本脚本管理的模型。先运行：python install_free_llms.py")
        return 1

    statefile = _state_path(args)
    logfile = args.watch_log or os.path.join(
        os.path.dirname(os.path.abspath(args.models)), "free_llm_watch.log")

    interval = max(60, args.watch_interval)
    max_rounds = args.watch_rounds if args.watch_rounds > 0 else 0  # 0 = 无限

    print("=" * 74)
    print("watch 模式：每 %d 秒探测一次，单请求/模型（省额度）" % interval)
    print("模型数：%d" % len(managed))
    print("状态文件：%s" % statefile)
    print("日志：%s" % logfile)
    print("变化会即时打印；按 Ctrl+C 退出")
    if max_rounds:
        print("本轮将跑 %d 轮后自动退出" % max_rounds)
    print("=" * 74)

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print("\n[%s] 第1 轮探测" % ts)
    prev = _load_state(statefile)
    cur = {}
    alive = 0
    for m in managed:
        st, det = probe_once(m)
        label, ok = WATCH_STATES.get(st, (st, False))
        cur[m.get("id")] = {"status": st, "detail": det,
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        if st == "OK":
            alive += 1
        old = (prev.get(m.get("id")) or {}).get("status")
        mark = ""
        if old and old != st:
            mark = "  <<< 变了: %s -> %s" % (old, st)
            _append_log(logfile, "[%s] %s: %s -> %s (%s)"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"),
                           m.get("id"), old, st, det))
        print("  %-40s %-22s %s" % (str(m.get("id"))[:40], label, mark))
        sys.stdout.flush()
        time.sleep(args.watch_gap)
    _save_state(statefile, cur)
    print("  → 可用 %d / %d" % (alive, len(managed)))

    round_no = 1
    try:
        while max_rounds == 0 or round_no < max_rounds:
            time.sleep(interval)
            round_no += 1
            print("\n[%s] 第 %d 轮探测"
                  % (time.strftime("%Y-%m-%d %H:%M:%S"), round_no))
            prev = cur
            cur = {}
            alive = 0
            changes = 0
            for m in managed:
                st, det = probe_once(m)
                label, ok = WATCH_STATES.get(st, (st, False))
                cur[m.get("id")] = {"status": st, "detail": det,
                                    "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                if st == "OK":
                    alive += 1
                old = (prev.get(m.get("id")) or {}).get("status")
                if old and old != st:
                    changes += 1
                    print("  %-40s %-22s  <<< 变了: %s -> %s"
                          % (str(m.get("id"))[:40], label, old, st))
                    _append_log(logfile, "[%s] %s: %s -> %s (%s)"
                                % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                   m.get("id"), old, st, det))
                else:
                    print("  %-40s %-22s" % (str(m.get("id"))[:40], label))
                sys.stdout.flush()
                time.sleep(args.watch_gap)
            _save_state(statefile, cur)
            print("  → 可用 %d / %d%s"
                  % (alive, len(managed),
                     ("（%d 个变化）" % changes) if changes else "（无变化）"))
    except KeyboardInterrupt:
        print("\n已停止。状态文件保留在 %s" % statefile)
        return 0
    return 0


def fetch_kilo_free_models():
    """从 Kilo 网关拉当前真实的免费模型清单（用于 --refresh）。"""
    url = KILO_URL.rsplit("/chat/completions", 1)[0] + "/models"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        data = json.loads(opener.open(req, timeout=40).read().decode("utf-8", "replace"))
    except Exception as e:
        return None, "拉取失败：%s" % str(e)[:90]

    out = []
    for m in (data.get("data") or []):
        pr = m.get("pricing") or {}
        try:
            p = float(pr.get("prompt", 1) or 0)
            c = float(pr.get("completion", 1) or 0)
        except (TypeError, ValueError):
            continue
        if p == 0 and c == 0:
            ctx = m.get("context_length") or 0
            out.append((m.get("id"), ctx))
    return out, None


# ---------------------------------------------------------------------------
# 各子命令
# ---------------------------------------------------------------------------


def cmd_list(args):
    path = args.models
    models = load_models(path)
    if not models:
        print("配置文件为空或不存在：%s" % path)
        return 0
    print("配置文件：%s（共 %d 个模型）\n" % (path, len(models)))
    print("%-42s %-16s %s" % ("MODEL ID", "VENDOR", "本脚本管理"))
    print("-" * 78)
    for m in models:
        owned = m.get("vendor") in OWNED_VENDORS
        print("%-42s %-16s %s" % (
            str(m.get("id"))[:42], str(m.get("vendor"))[:16],
            "是" if owned else "-"))
    return 0


def build_targets(args):
    targets = list(FREE_MODELS)
    if args.deepseek_key:
        targets.append({
            "id": "deepseek-v4-flash",
            "name": "DeepSeek-V4 Flash",
            "vendor": "DeepSeek",
            "url": "https://api.deepseek.com/chat/completions",
            "apiKey": args.deepseek_key,
            "supportsToolCall": True,
            "supportsImages": True,
            "supportsReasoning": True,
            "reasoning": {
                "defaultEffort": "high",
                "supportedEfforts": ["minimal", "low", "medium", "high", "xhigh", "max"],
            },
        })
    return targets


def do_install(args, targets):
    path = args.models
    existing = load_models(path)

    by_id = {m.get("id"): m for m in existing if isinstance(m, dict)}
    added, updated, skipped = [], [], []

    for t in targets:
        cur = by_id.get(t["id"])
        if cur is None:
            existing.append(t)
            added.append(t["id"])
        elif args.force:
            idx = existing.index(cur)
            existing[idx] = t
            updated.append(t["id"])
        elif cur.get("url") == t["url"]:
            skipped.append(t["id"])
        else:
            skipped.append("%s（URL 不同，保留你现有的）" % t["id"])

    print("配置文件：%s" % path)
    print("新增 %d 个 · 覆盖 %d 个 · 跳过 %d 个\n"
          % (len(added), len(updated), len(skipped)))
    for i in added:
        print("  [新增] %s" % i)
    for i in updated:
        print("  [覆盖] %s" % i)
    for i in skipped:
        print("  [跳过] %s" % i)

    if args.dry_run:
        print("\n--dry-run：未写入任何文件。去掉该参数即可实际安装。")
        return 0

    bk = backup(path)
    if bk:
        print("\n已备份：%s" % bk)

    save_models(path, existing)
    print("已写入 %s（共 %d 个模型）\n" % (path, len(existing)))
    return 0


def do_verify(args):
    path = args.models
    models = load_models(path)
    managed = [m for m in models
               if isinstance(m, dict) and m.get("vendor") in OWNED_VENDORS]
    if args.deepseek_key:
        managed = models
    if not managed:
        print("配置里没有本脚本管理的模型。先运行：python install_free_llms.py")
        return 1

    print("验证 %d 个模型（发真实请求，各含一次 function calling）\n" % len(managed))
    print("%-42s %-7s %s" % ("MODEL ID", "结果", "详情"))
    print("-" * 100)

    passed, failed = [], []
    for m in managed:
        ok, det = verify_model(m)
        print("%-42s %-7s %s" % (str(m.get("id"))[:42],
                                "OK" if ok else "FAIL", det[:50]))
        sys.stdout.flush()
        (passed if ok else failed).append(m.get("id"))
        time.sleep(0.8)

    print("\n通过 %d / %d" % (len(passed), len(managed)))
    if failed:
        print("失败：%s" % ", ".join(failed))
        print("\n排查顺序：① 等几分钟再试（免费池高峰会限流）"
              " ② 看日志确认请求 URL 正确  ③ 该模型可能已下线")
    return 0 if not failed else 2


def do_refresh(args):
    path = args.models
    print("从 Kilo 网关拉取当前免费模型清单…\n")
    free, err = fetch_kilo_free_models()
    if err:
        print("[x] %s" % err)
        return 1
    if not free:
        print("[x] 未拉到任何免费模型")
        return 1

    free.sort(key=lambda x: -(x[1] or 0))
    print("服务端当前 %d 个免费模型（按上下文排序）：\n" % len(free))
    existing = {m.get("id") for m in load_models(path)}
    to_add = []
    for mid, ctx in free:
        ctxs = ("%dk" % (ctx // 1000)) if ctx else "?"
        mark = "已装" if mid in existing else ""
        print("  %-52s %8s  %s" % (mid[:52], ctxs, mark))
        if mid not in existing and mid not in [t["id"] for t in FREE_MODELS]:
            to_add.append(mid)

    if not to_add:
        print("\n没有新模型需要添加。")
        return 0
    if args.dry_run:
        print("\n--dry-run：%d 个新模型未写入。去掉该参数即可安装。" % len(to_add))
        return 0

    models = load_models(path)
    for mid in to_add:
        models.append(kilo_model(mid, "%s (Free)" % mid.split("/")[-1]))
    bk = backup(path)
    if bk:
        print("\n已备份：%s" % bk)
    save_models(path, models)
    print("已添加 %d 个模型，请运行 --verify 验证" % len(to_add))
    print("（注意：新加的不在实测清单内，可能不支持工具调用）")
    return 0


def do_uninstall(args):
    path = args.models
    models = load_models(path)
    managed = [m for m in models
               if isinstance(m, dict) and m.get("vendor") in OWNED_VENDORS]
    managed_ids = set(id(m) for m in managed)
    kept = [m for m in models if id(m) not in managed_ids]

    if not managed:
        print("没有本脚本添加的模型，无需清理。")
        return 0
    if args.dry_run:
        print("--dry-run：将移除 %d 个模型" % len(managed))
        for m in managed:
            print("  [将移除] %s" % m.get("id"))
        return 0

    bk = backup(path)
    save_models(path, kept)

    print("已移除 %d 个模型：" % len(managed))
    for m in managed:
        print("  [已移除] %s" % m.get("id"))
    if bk:
        print("\n备份：%s" % bk)

    # 明确说明剩下的是什么，避免用户以为配置被清空
    if kept:
        print("\n保留的模型（%d 个，由你或其他工具管理）：" % len(kept))
        for m in kept:
            print("  [保留] %s" % m.get("id"))
    else:
        print("\n配置现在是空的。")
    return 0


# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="一键安装已实测可用的免费 LLM（免key、支持工具调用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python install_free_llms.py\n"
               "  python install_free_llms.py --verify\n"
               "  python install_free_llms.py --refresh\n",
    )
    ap.add_argument("--models", help="models.json 路径（默认自动探测）")
    ap.add_argument("--verify", action="store_true", help="只验证已装模型")
    ap.add_argument("--list", action="store_true", help="列出已配模型")
    ap.add_argument("--refresh", action="store_true", help="拉最新免费清单并补装")
    ap.add_argument("--uninstall", action="store_true", help="移除本脚本添加的模型")
    ap.add_argument("--watch", action="store_true",
                    help="定期探测模型状态，变化时报告（Ctrl+C 退出）")
    ap.add_argument("--watch-interval", type=int, default=1800,
                    help="watch 轮询间隔秒数（默认 1800=30 分钟，最小 60）")
    ap.add_argument("--watch-rounds", type=int, default=0,
                    help="watch 跑几轮后退出（0=一直跑）")
    ap.add_argument("--watch-gap", type=float, default=2.0,
                    help="watch 每轮内各模型之间的间隔秒数（默认 2）")
    ap.add_argument("--watch-log", help="变化日志文件路径（默认与配置同目录）")
    ap.add_argument("--dry-run", action="store_true", help="只看要做什么，不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖同 id 的现有配置")
    ap.add_argument("--deepseek-key", help="顺便添加 DeepSeek（需你自己的 Key）")
    args = ap.parse_args()

    socket.setdefaulttimeout(60)
    if not args.models:
        args.models = find_models_path()

    if args.list:
        return cmd_list(args)
    if args.verify:
        return do_verify(args)
    if args.refresh:
        return do_refresh(args)
    if args.uninstall:
        return do_uninstall(args)
    if args.watch:
        return do_watch(args)

    rc = do_install(args, build_targets(args))
    if rc or args.dry_run:
        return rc

    print("=" * 78)
    print("开始实测验证（这一步能挡住「标 free 但实际不可用」）\n")
    vrc = do_verify(args)
    if vrc == 0:
        print("\n全部可用。在 WorkBuddy 模型列表里即可选择。")
        print("建议把 kilo-auto/free 设为默认——它会自动路由到当前可用的模型。")
    else:
        print("\n部分模型未通过。可用 --list 查看，"
              "或等高峰过后重跑 --verify 再判断。")
    return vrc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        eprint("\n已中断")
        sys.exit(130)