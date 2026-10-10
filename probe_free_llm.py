#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_free_llm.py — 免费 LLM API 真实性探测器（零依赖，仅标准库）

用途：合集类仓库（README 由 LLM 生成、限流数字常年过期）不可信。
      本脚本对每个base_url + model 发一次最小真实请求，从响应里读三样东西：
        1) usage 里的 prompt/completion/total token
        2) 顶层cost / usage.cost 字段（有值 = 在计费，是"免费额度"不是"免费"）
        3) HTTP 状态码 + 报错文本（区分401 没key、429 限流、402 欠费、404 模型不存在）

用法：
    export GROQ_KEY=xxx
    export MODEL=gpt-oss-120b
    python probe_free_llm.py --base https://api.groq.com/openai/v1 \\
        --key-env GROQ_KEY --model "$MODEL"
    python probe_free_llm.py --config providers.json

providers.json 格式：
    [{"name": "...", "base": "...", "key_env": "XXX_KEY", "model": "..."}]

判读要点：
  - cost 字段为 0 或缺失 + 无限流  -> 真免费
  - cost > 0                        -> 消耗额度，不算免费
  - 429 且带 x-ratelimit-* 响应头   -> 免费但有 RPD/RPM 上限，
                                       真实数字以响应头为准（比 README 准）

坑（README 不写、但会让你误判）：
  - ModelScope 的 GET /v1/models 不鉴权，未绑阿里云实名也返回 200，
    必须发一次 1-token 的 chat/completions 才会暴露
    401 "please bind your alibaba cloud account before use"。
  - OpenRouter /v1/chat/completions 里的 usage.cost 是权威成本字段，
    0 才是免费；>0 说明在扣免费额度。
  - 硅基流动各来源说法冲突（1000 RPM / 30 RPM / 50 RPD 三种都有人写），
    只能靠本脚本实测，别信任何二手表格。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 30
PROMPT = "reply with the single word: ok"


def call(base, key, model, timeout=TIMEOUT):
    """返回 (status, body_dict_or_text, headers)"""
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 8,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw), dict(resp.headers)
            except json.JSONDecodeError:
                return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except json.JSONDecodeError:
            return e.code, raw, dict(e.headers)
    except Exception as e:  # 超时 / DNS / 连接被拒
        return 0, f"{type(e).__name__}: {e}", {}


def extract_cost(body):
    """从响应里找 cost。OpenRouter 放usage.cost，部分平台放顶层 cost。"""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("cost"), (int, float)):
        return body["cost"]
    usage = body.get("usage")
    if isinstance(usage, dict):
        for k in ("cost", "total_cost", "cost_usd"):
            if isinstance(usage.get(k), (int, float)):
                return usage[k]
        return {"prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens")}
    return None


def rate_limit_headers(headers):
    """把 ratelimit 相关响应头摘出来——这才是真实的免费额度。"""
    keep = {}
    for k, v in headers.items():
        lk = k.lower()
        if "rate" in lk or "quota" in lk or lk == "retry-after":
            keep[lk] = v
    return keep


def probe(p, verbose=False):
    base, model, key_env = p["base"], p["model"], p.get("key_env", "")
    key = os.environ.get(key_env, "") if key_env else ""
    row = {"name": p.get("name", base), "model": model}

    if not key:
        row["verdict"] = "SKIP"
        row["note"] = f"环境变量 {key_env} 未设置"
        return row

    status, body, headers = call(base, key, model)
    row["http"] = status
    row["cost"] = extract_cost(body)
    row["ratelimit"] = rate_limit_headers(headers)

    if status == 200:
        cost = row["cost"]
        if isinstance(cost, (int, float)) and cost > 0:
            row["verdict"] = "METERED"
            row["note"] = f"本次请求扣费 ${cost}，属免费额度/试用金，不是永久免费"
        else:
            row["verdict"] = "FREE?"
            row["note"] = "响应无 cost 字段或为 0；再连发 60 次确认不撞限流"
    elif status == 429:
        row["verdict"] = "FREE-RATED"
        row["note"] = "免费但已限流，真实额度见 ratelimit 响应头"
    elif status == 402:
        row["verdict"] = "CREDITED"
        row["note"] = "欠费/额度耗尽，不是真免费"
    elif status == 401:
        row["verdict"] = "BADKEY"
        row["note"] = "key 无效或未生效"
    elif status == 404:
        row["verdict"] = "BADMODEL"
        row["note"] = f"模型不存在 {model}，README 的模型名可能已下线"
    else:
        row["verdict"] = "ERROR"
        row["note"] = str(body)[:160]

    if verbose:
        row["raw"] = str(body)[:400]
    return row


def burst(base, key, model, n=60, gap=1.0):
    """连续打 n 次，看第几次 429 —— 反推真实的 RPM/RPD。"""
    codes = []
    for i in range(n):
        status, body, _ = call(base, key, model, timeout=20)
        codes.append(status)
        if status == 429:
            print(f"  第 {i+1} 次就429，估算 RPM≈{i+1} / 分钟", file=sys.stderr)
            break
        time.sleep(gap)
    return codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="providers.json 路径")
    ap.add_argument("--base", help="直接指定 base url，覆盖配置文件")
    ap.add_argument("--key-env", default="", help="key 环境变量名")
    ap.add_argument("--model", help="模型名")
    ap.add_argument("--burst", action="store_true", help="连发 60 次反推真实 RPM")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.base:
        providers = [{"name": args.base, "base": args.base,
                      "key_env": args.key_env, "model": args.model or ""}]
    elif args.config:
        with open(args.config, encoding="utf-8") as f:
            providers = json.load(f)
    else:
        ap.error("需要 --config 或 --base")

    rows = []
    for p in providers:
        r = probe(p, args.verbose)
        rows.append(r)
        cost = r.get("cost")
        cost_s = f"cost={cost}" if cost is not None else "cost=?"
        print(f"[{r['verdict']:<10}] {r['name']:<28} {r['model']:<32} "
              f"http={r.get('http','-'):<4} {cost_s}")
        print(f"{'':>13}{r['note']}")
        if r.get("ratelimit"):
            print(f"{'':>13}ratelimit: {r['ratelimit']}")
        if args.burst and r.get("http") == 200:
            burst(p["base"], os.environ.get(p.get("key_env", ""), ""), p["model"])
        print()

    bad = [r for r in rows if r["verdict"] in ("METERED", "BADMODEL", "ERROR", "CREDITED")]
    print("=" * 60)
    print(f"探测 {len(rows)} 个；其中 {len(bad)} 个与 README 描述不符或已失效")
    for r in bad:
        print(f"  - {r['name']} / {r['model']}: {r['verdict']}")


if __name__ == "__main__":
    main()
