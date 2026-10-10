#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
探测 OpenRouter 免费层里「支持工具调用」的模型是否真能跑 agent 循环。

背景：模型页筛选 supported_parameters=tools & pricing=0 只是「官方标称」，
不代表实际能发请求、更不代表能返回合法的 tool_call 结构。
本脚本对每个候选发一次带 tools 的请求，逐项判定。

零依赖，仅标准库。

用法:
    python probe_openrouter_free.py                    # 探测默认清单
    python probe_openrouter_free.py --repeat 3         # 每个模型连发3次看稳定性
    python probe_openrouter_free.py --key sk-or-v1-xxx # 直接传key，不设环境变量
    OPENROUTER_API_KEY=sk-or-xxx python probe_openrouter_free.py   # 带 key 提额

判读要点:
    OK        -> 返回了合法 tool_calls 结构，可用于 agent 循环
    NO_TOOL   -> 请求成功但模型没选择调工具（可能是纯聊天模型/不会用 tool）
    HTTPxxx   -> 看错误体判断是限流(429)、免费层坏(402/403)还是模型已下线(404)
    注意      -> OpenRouter 匿名请求走 IP 限流，带 key 才能稳定压测
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

URL = "https://openrouter.ai/api/v1/chat/completions"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TIMEOUT = 45

# openrouter/models 页按 supported_parameters=tools & pricing-low-to-high 筛出的
# 「输入输出价格均为 0」的模型，按发布时间倒序。
CANDIDATES = [
    "inclusionai/ling-3.1-flash",
    "apodex/apodex-1.1-mini:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "dots-studio/dots-3-note-preview:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "thinkingmachines/inkling-small:free",
    "poolside/laguna-s-2.1:free",
    "thinkingmachines/inkling:free",
    "poolside/laguna-xs-2.1:free",
    "cohere/north-mini-code:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/free",
]

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

PROMPT = "北京天气怎么样？必须调用 get_weather 工具查询，不要直接回答。"


def probe(model, key=None):
    """发一次带工具的请求，返回 (状态, 详情)。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": TOOLS,
        "max_tokens": 200,
    }
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if key:
        headers["Authorization"] = "Bearer " + key

    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(), headers=headers
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    try:
        resp = opener.open(req, timeout=TIMEOUT)
        raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        # OpenRouter 把错误放在 error.message 里
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body
        return "HTTP%d" % e.code, str(msg)[:110]
    except socket.timeout:
        return "TIMEOUT", "%ds 内无响应" % TIMEOUT
    except Exception as e:
        return "EXC", "%s: %s" % (type(e).__name__, str(e)[:90])

    if "error" in data:
        return "APIErr", str(data["error"].get("message", data["error"]))[:110]

    choices = data.get("choices") or []
    if not choices:
        return "EMPTY", json.dumps(data)[:110]

    msg = choices[0].get("message") or {}
    calls = msg.get("tool_calls") or []
    finish = choices[0].get("finish_reason")
    usage = data.get("usage") or {}

    if any(c.get("function", {}).get("name") == "get_weather" for c in calls):
        args = calls[0]["function"].get("arguments", "")
        return "OK", "finish=%s tokens=%s cost=%s args=%s" % (
            finish,
            usage.get("total_tokens"),
            data.get("cost", "0"),
            args[:40],
        )
    txt = (msg.get("content") or "")[:60]
    return "NO_TOOL", "finish=%s tokens=%s | 模型改用文本回答: %r" % (
        finish,
        usage.get("total_tokens"),
        txt,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1, help="每个模型连发几次")
    ap.add_argument("--only", help="只测指定模型（逗号分隔）")
    ap.add_argument("--key", help="OpenRouter API key（优先于 OPENROUTER_API_KEY）")
    args = ap.parse_args()

    socket.setdefaulttimeout(TIMEOUT)
    key = args.key or os.environ.get("OPENROUTER_API_KEY") or None
    models = args.only.split(",") if args.only else CANDIDATES

    print("OpenRouter 免费层 + 工具调用 实测")
    print("key: %s" % ("已带 OPENROUTER_API_KEY" if key else "无（匿名 IP 限流，代表性略差）"))
    print("每个模型发 %d 次, 间隔 1.5s" % args.repeat)
    print("=" * 108)
    print("%-50s %-10s %-8s %s" % ("MODEL", "状态", "第N次", "详情"))
    print("-" * 108)

    alive, dead = [], []
    for m in models:
        for i in range(1, args.repeat + 1):
            st, det = probe(m, key)
            print("%-50s %-10s %-8s %s" % (m[:50], st, "%d/%d" % (i, args.repeat), det))
            sys.stdout.flush()
            if st == "OK":
                if i == 1:
                    alive.append(m)
                break  # 成功一次就够，不压测
            if st.startswith("HTTP4") and "429" in st:
                pass  # 限流继续试
            time.sleep(1.5)

    print("=" * 108)
    print("\n可用（返回合法 tool_calls）: %d 个" % len(alive))
    for m in alive:
        print("  [OK] %s" % m)
    if dead:
        print("\n不可用: %d 个" % len(dead))
        for m in dead:
            print("  [--] %s" % m)


if __name__ == "__main__":
    main()