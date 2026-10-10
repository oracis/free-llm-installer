#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实测 OpenCode Zen 上 step-5-preview-free（阶跃星辰 Step 5 Preview 免费版）
以及同期其他 free 模型的能力，重点验证「工具调用」。

背景：微信文章称Step 5 Preview 在OpenCode Zen / Cline / Kilo Code / Nous Portal
四个平台免费一周（2026-10-08 公告）。本文档对Zen 端点做能力验证。

注意两个已确认的坑：
1. Cloudflare 按 UA 拦截 —— 必须带浏览器 UA，否则 403 code:1010。
2. Zen 免费层限制 —— 报错 FreeTierError "free tier can only be used from
   within OpenCode"，即API 直连拿不到 free 层，只能在 OpenCode 客户端内用。

用法:
    python probe_zen_step5.py
    python probe_zen_step5.py --only step-5-preview-free,space-bunny-free
"""

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request

URL = "https://opencode.ai/zen/v1/chat/completions"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 45

MODELS = [
    "step-5-preview-free",   # 本次主角：阶跃 Step 5 Preview 免费版
    "space-bunny-free",      # 昨天实测唯一可用的
    "longcat-2.5-preview-free",
    "exo-free",
    "mimo-v2.6-flash-free",
    "ling-3.1-flash-free",
    "nemotron-3-ultra-free",
    "big-pickle",
    "jev-1.13-free",
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


def call(model, prompt, tools=None, max_tokens=200):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer  ",
            "User-Agent": UA,
        },
    )
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


def fmt_err(d):
    if "__http__" not in d:
        return None
    code = d["__http__"]
    err = d.get("__err__")
    if isinstance(err, dict):
        e = err.get("error", err)
        msg = e.get("message", "") if isinstance(e, dict) else str(e)
        etype = e.get("type", "") if isinstance(e, dict) else ""
        return "HTTP%s %s %s" % (code, etype, msg)
    return "HTTP%s %s" % (code, str(err)[:120])


def probe(model):
    """测工具调用能力。返回 (状态, 详情)"""
    d = call(model, PROMPT, TOOLS)
    e = fmt_err(d)
    if e:
        return e.split()[0] if e.startswith("HTTP") else e, e[:120]

    ch = d.get("choices") or []
    if not ch:
        return "EMPTY", json.dumps(d, ensure_ascii=False)[:110]

    msg = ch[0].get("message") or {}
    calls = msg.get("tool_calls") or []
    usage = d.get("usage") or {}
    info = "finish=%s tok=%s cost=%s" % (
        ch[0].get("finish_reason"),
        usage.get("completion_tokens"),
        d.get("cost", "0"),
    )

    if any(c.get("function", {}).get("name") == "get_weather" for c in calls):
        args = calls[0]["function"].get("arguments", "")
        return "OK", info + " args=" + args[:40]

    # 没调工具——看看是纯文本还是 reasoning 模型
    txt = (msg.get("content") or "")[:50]
    rea = (msg.get("reasoning_content") or "")[:40]
    return "NO_TOOL", info + " | text=%r reasoning=%r" % (txt, rea)


def probe_quality(model):
    """测基本问答质量 + 是否支持 system + 多模态声明"""
    d = call(model, "用一句话说明什么是反向代理。", None, 300)
    if "__http__" in d:
        return fmt_err(d)[:130]
    ch = d.get("choices") or []
    if not ch:
        return "EMPTY"
    msg = ch[0].get("message") or {}
    txt = (msg.get("content") or "").strip()
    rea = (msg.get("reasoning_content") or "").strip()
    if not txt and rea:
        return "仅reasoning(%d字) 无最终文本 -- max_tokens不够" % len(rea)
    return "OK: %s" % (txt[:120] or "(空)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只测指定模型，逗号分隔")
    ap.add_argument("--quality", action="store_true", help="额外测问答质量")
    args = ap.parse_args()

    socket.setdefaulttimeout(TIMEOUT)
    models = args.only.split(",") if args.only else MODELS

    print("=" * 100)
    print("OpenCode Zen · step-5-preview-free 能力实测")
    print("端点: %s" % URL)
    print("=" * 100)
    print("%-28s %-12s %s" % ("模型", "工具调用", "详情"))
    print("-" * 100)

    ok_list = []
    for m in models:
        st, det = probe(m)
        print("%-28s %-12s %s" % (m[:28], st, det))
        sys.stdout.flush()
        if st == "OK":
            ok_list.append(m)
        time.sleep(1.2)

    if args.quality:
        print()
        print("=" * 100)
        print("问答质量")
        print("-" * 100)
        for m in (ok_list or models[:2]):
            print("%-28s %s" % (m[:28], probe_quality(m)[:130]))
            time.sleep(1.2)

    print()
    print("=" * 100)
    print("支持工具调用的模型: %d 个" % len(ok_list))
    for m in ok_list:
        print("  [OK] %s" % m)


if __name__ == "__main__":
    main()