#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实测 Kilo 免费网关（https://api.kilo.ai/api/gateway）—— 不用注册、不用 API Key。

背景：微信文章称Kilo（GitHub 27.5K Star 编程 Agent，已被 Anaconda 收购）
开放了免费模型网关，17 个模型直接调、免注册免 key、OpenAI 兼容。
本文档验证：① 免 key 是否真能跑  ② 哪些模型真支持工具调用（CC/WorkBuddy 必需）
          ③ 单模型稳定性与限流。

用法:
    python probe_kilo_gateway.py                # 工具调用全量探测
    python probe_kilo_gateway.py --quality      # 附带问答质量
    python probe_kilo_gateway.py --only kilo-auto/free,nvidia/nemotron-3-ultra-550b-a55b:free
    python probe_kilo_gateway.py --burst 5      # 对指定模型连发 N 次压测
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.kilo.ai/api/gateway"
CHAT = BASE + "/v1/chat/completions"
MODELS_URL = BASE + "/v1/models"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 60

# 文章列出的 17 个免费模型 + 自动路由
ARTICLE_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "inclusionai/ling-3.1-flash",
    "stepfun/step-3.7-flash:free",
    "dots-studio/dots-3-note-preview:free",
    "kilo-auto/free",
    "liquid/lfm-2.5-2.6b:free",
    "thinkingmachines/inkling-small:free",
    "poolside/laguna-s-2.1:free",
    "thinkingmachines/inkling:free",
    "poolside/laguna-xs-2.1:free",
    "cohere/north-mini-code:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3.5-lightning:free",
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


def call(model, prompt, tools=None, max_tokens=200, key=None):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools

    headers = {"Content-Type": "application/json", "User-Agent": UA}
    # 文章说 api_key 留空即可；带 key 时也可试（Kilo 账号 key 走同一网关）
    key = key or os.environ.get("KILO_API_KEY")
    if key:
        headers["Authorization"] = "Bearer " + key

    req = urllib.request.Request(CHAT, data=json.dumps(payload).encode(), headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        resp = opener.open(req, timeout=TIMEOUT)
        return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return {"__http__": e.code, "__err__": json.loads(body)}
        except Exception:
            return {"__http__": e.code, "__err__": body}
    except socket.timeout:
        return {"__http__": "TIMEOUT", "__err__": "no response"}
    except Exception as e:
        return {"__http__": "EXC", "__err__": "%s: %s" % (type(e).__name__, e)}


def err_str(d):
    if "__http__" not in d:
        return None
    code, err = d["__http__"], d.get("__err__")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("error") or json.dumps(err)
        et = err.get("error_type", "")
        return ("HTTP%s %s %s" % (code, et, msg))[:135]
    return "HTTP%s %s" % (code, str(err)[:120])


def probe_tools(model):
    """核心：验证工具调用"""
    d = call(model, PROMPT, TOOLS)
    e = err_str(d)
    if e:
        return ("RATELIMIT" if "429" in e else "FAIL"), e

    ch = d.get("choices") or []
    if not ch:
        return "EMPTY", json.dumps(d, ensure_ascii=False)[:110]

    msg = ch[0].get("message") or {}
    calls = msg.get("tool_calls") or []
    usage = d.get("usage") or {}
    info = "finish=%s ptok=%s ctok=%s cost=%s" % (
        ch[0].get("finish_reason"),
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        d.get("cost", "0"),
    )
    if any(c.get("function", {}).get("name") == "get_weather" for c in calls):
        args = calls[0]["function"].get("arguments", "")
        return "OK", info + " args=" + args[:35]
    txt = (msg.get("content") or "")[:45]
    return "NO_TOOL", info + " | text=%r" % txt


def probe_quality(model):
    d = call(model, "用一句话说明什么是反向代理。", None, 400)
    e = err_str(d)
    if e:
        return e[:130]
    ch = d.get("choices") or []
    if not ch:
        return "EMPTY"
    msg = ch[0].get("message") or {}
    txt = (msg.get("content") or "").strip()
    rea = (msg.get("reasoning") or msg.get("reasoning_content") or "")
    if not txt:
        return "仅reasoning(%d字)无文本" % len(rea)
    return "OK: %s" % txt[:110]


def list_models():
    """从网关拉真实模型清单，筛出免费的"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(MODELS_URL, headers={"User-Agent": UA})
    try:
        d = json.loads(opener.open(req, timeout=TIMEOUT).read().decode())
    except Exception as ex:
        print("拉取模型列表失败:", ex)
        return []
    ms = d.get("data") or d.get("models") or []
    free = []
    for m in ms:
        mid = m.get("id", "")
        pr = m.get("pricing") or {}
        p = float(pr.get("prompt", 1) or 0)
        c = float(pr.get("completion", 1) or 0)
        if p == 0 and c == 0:
            free.append((mid, m.get("context_length") or 0))
    return free, len(ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只测这些模型，逗号分隔")
    ap.add_argument("--quality", action="store_true", help="附测问答质量")
    ap.add_argument("--burst", type=int, help="对第一个模型连发 N 次")
    ap.add_argument("--list", action="store_true", help="只打印网关的免费模型清单")
    args = ap.parse_args()

    socket.setdefaulttimeout(TIMEOUT)

    if args.list:
        r = list_models()
        if r:
            free, total = r
            print("网关总模型数: %d | 免费(输入输出均0): %d" % (total, len(free)))
            print("-" * 78)
            print("%-56s %s" % ("FREE MODEL ID", "CTX"))
            for mid, ctx in sorted(free, key=lambda x: -(x[1] or 0)):
                print("%-56s %s" % (mid[:56], ("%dk" % (ctx // 1000)) if ctx else "?"))
        return

    models = args.only.split(",") if args.only else ARTICLE_MODELS

    print("=" * 104)
    print("Kilo 免费网关实测 · base=%s" % BASE)
    print("key: %s" % ("已带KILO_API_KEY" if os.environ.get("KILO_API_KEY") else "无（文章说免key）"))
    print("=" * 104)
    print("%-48s %-10s %s" % ("MODEL", "工具调用", "详情"))
    print("-" * 104)

    ok = []
    for m in models:
        st, det = probe_tools(m)
        # 429 常是瞬时 TPM 限流（不是模型不可用），重试两次再下结论
        tries = 0
        while st == "RATELIMIT" and tries < 2:
            tries += 1
            time.sleep(8)
            st, det = probe_tools(m)
            if tries:
                det = "[重试%d] %s" % (tries, det)
        print("%-48s %-10s %s" % (m[:48], st, det))
        sys.stdout.flush()
        if st == "OK":
            ok.append(m)
        time.sleep(1.0)

    if args.burst and models:
        print()
        print("=" * 104)
        print("压测 %s × %d 次" % (models[0], args.burst))
        print("-" * 104)
        succ = 0
        for i in range(1, args.burst + 1):
            t0 = time.time()
            st, det = probe_tools(models[0])
            el = (time.time() - t0) * 1000
            if st == "OK":
                succ += 1
            print("  第%d次 %-10s %6.0fms  %s" % (i, st, el, det[:60]))
            time.sleep(1.5)
        print("  → 成功 %d/%d" % (succ, args.burst))

    if args.quality and ok:
        print()
        print("=" * 104)
        print("问答质量")
        print("-" * 104)
        for m in ok[:6]:
            print("%-48s %s" % (m[:48], probe_quality(m)[:120]))
            time.sleep(1.0)

    print()
    print("=" * 104)
    print("支持工具调用的模型: %d/%d" % (len(ok), len(models)))
    for m in ok:
        print("  [OK] %s" % m)


if __name__ == "__main__":
    main()