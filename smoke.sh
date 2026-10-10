#!/bin/bash
pkill -f sriver.py 2>/dev/null
pkill -f "python3 -m sriver" 2>/dev/null
sleep 1

LOG=~/sriver_smoke.log
nohup python3 -u ~/sriver.py > $LOG 2>&1 &
sleep 2

if ! grep -q "已启动" $LOG; then
    echo "❌ 服务器没起来"
    cat $LOG
    exit 1
fi
echo "✅ 服务器已启动"

python3 - << 'PYEOF'
import json, urllib.request

def post(msg):
    data = json.dumps({"messages":[{"role":"user","content":msg}]}).encode()
    req = urllib.request.Request("http://127.0.0.1:7860/v1/chat/completions",
        data=data, headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        text = r.read().decode()
    parts = []
    for line in text.splitlines():
        if line.startswith("data: "):
            payload = line[6:]
            if payload == "[DONE]":
                continue
            try:
                j = json.loads(payload)
                d = j.get("choices",[{}])[0].get("delta",{}).get("content","")
                parts.append(d)
            except Exception:
                pass
    return "".join(parts)

tests = [
    ("/版本", "wizard-patch03", "版本"),
    ("/帮助", "命令列表", "帮助"),
    ("/配置", "密钥掩码", "配置"),
    ("/模型", "模型配置", "模型"),
    ("/only qwen", "本轮仅允许", "only"),
    ("/调试", "Debug", "调试"),
]

all_ok = True
for cmd, expect, name in tests:
    resp = post(cmd)
    if expect in resp:
        print(f"  ✅ {name} OK")
    else:
        print(f"  ❌ {name} FAIL  (收到: {resp[:80]!r})")
        all_ok = False

print()
print("=== 全部通过 ===" if all_ok else "=== 有失败 ===")
PYEOF

pkill -f sriver.py 2>/dev/null
