#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实测 Kilo 网关上 step-5-preview-free 的读图能力。

背景：阶跃星辰官方文档明确 step-5-preview 原生支持多模态（图片/视频输入）。
但你的 stepfun/step-5-preview-free 走的是 Kilo 网关，网关可能不透传图片。
需要区分「模型原生能力」和「网关透传能力」。
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
import zlib

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
MODELS_PATH = os.path.expanduser("~/.workbuddy/models.json")
KILO = "https://api.kilo.ai/api/gateway/v1/chat/completions"


def make_png_2x2():
    """2x2：左上红 / 右上绿 / 左下黄 / 右下蓝。"""
    rows = [
        bytes([0, 255, 0, 0, 0, 0, 255, 0]),
        bytes([0, 255, 255, 0, 0, 0, 0, 255]),
    ]
    raw = b"".join(rows)

    def chunk(tag, data):
        c = tag + data
        return (struct.pack(">I", len(data)) + c +
                struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def post(payload, timeout=120):
    req = urllib.request.Request(
        KILO, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer ",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:400]}
    except Exception as e:
        return "NET", {"err": str(e)}


def text_of(data):
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    c = msg.get("content")
    if isinstance(c, list):
        c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return str(c or "")


def probe(model_id, png_b64, label, retries=4):
    q = ("这张 2x2 图的四个格子颜色分别是：左上、右上、左下、右下。"
         "请只输出四个颜色词，用空格分隔，不要解释。")
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": q},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + png_b64}},
        ]}],
        "max_tokens": 800,
    }
    for attempt in range(retries):
        st, data = post(payload)
        if st == 429:
            wait = 15 * (attempt + 1)
            print("  [{}] 429，{}s 后重试".format(label, wait))
            time.sleep(wait)
            continue
        break
    print("=" * 66)
    print("【{}】 model={}  Kilo 网关".format(label, model_id))
    print("  HTTP {}".format(st))
    if st != 200:
        print("  错误: {}".format(json.dumps(data, ensure_ascii=False)[:300]))
        return
    usage = data.get("usage") or {}
    ct = usage.get("completion_tokens_details") or {}
    pt = usage.get("prompt_tokens_details") or {}
    print("  回复: {}".format(text_of(data).replace("\n", " ")[:120]))
    print("  prompt image_tokens={} / completion image_tokens={}".format(
        pt.get("image_tokens"), ct.get("image_tokens")))
    print("  正确答案: 红 绿 黄 蓝")


def main():
    png_b64 = base64.b64encode(make_png_2x2()).decode()
    print("测试图: 2x2 PNG (左上红/右上绿/左下黄/右下蓝)\n")

    # A. 已知假多模态做对照
    probe("kilo-auto/free", png_b64, "A 对照：kilo-auto/free（已知假多模态）")
    time.sleep(3)
    # B. 目标模型
    probe("stepfun/step-5-preview-free", png_b64, "B 目标：step-5-preview-free")
    time.sleep(3)
    # C. nemotron 也测一下
    probe("nvidia/nemotron-3-ultra-550b-a55b:free", png_b64, "C nemotron-ultra:free")
    time.sleep(3)
    # D. dots
    probe("dots-3-note-preview:free", png_b64, "D dots-3-note-preview:free")

    print()
    print("=" * 66)
    print("判读：")
    print("  image_tokens > 0 且答对 -> 网关支持读图")
    print("  image_tokens = 0 / 空回复 -> 假多模态，网关不透传")
    print("  404 'No endpoints found that support image input' -> 明确不支持")


if __name__ == "__main__":
    main()