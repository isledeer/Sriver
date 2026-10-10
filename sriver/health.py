#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, time

from .runtime import RT

HEALTH_PATH = os.path.expanduser("~/.sriver/health.json")


# ===================== health monitor =====================
def save_monitor():
    with open(HEALTH_PATH, "w", encoding="utf-8") as f:
        json.dump(RT.monitor, f, ensure_ascii=False, indent=2)
def load_monitor():
    if os.path.exists(HEALTH_PATH):
        try:
            with open(HEALTH_PATH, "r", encoding="utf-8") as f:
                RT.monitor = json.load(f)
        except Exception:
            RT.monitor = {}
def record_ok(model):
    h = RT.monitor.get(model, {})
    h["fail_streak"] = 0
    h["skip_until"] = 0
    h["total_ok"] = h.get("total_ok",0)+1
    h["last_ts"] = time.time()
    RT.monitor[model] = h
    save_monitor()
def record_fail(model, reason):
    h = RT.monitor.get(model, {})
    h["fail_streak"] = h.get("fail_streak",0)+1
    h["total_fail"] = h.get("total_fail",0)+1
    h["last_reason"] = reason[:120]
    h["last_ts"] = time.time()
    if h["fail_streak"] >= RT.config["fail_streak_threshold"]:
        h["skip_until"] = time.time() + RT.config["skip_cooldown_sec"]
        from .core import log as _log
        _log(f"⏸️ [{model}] 连续失败 {RT.config['fail_streak_threshold']} 次，跳过 {RT.config['skip_cooldown_sec']}s", "\033[93m")
    RT.monitor[model] = h
    save_monitor()
def is_skipped(model):
    h = RT.monitor.get(model)
    if not h: return False
    return h.get("skip_until",0) > time.time()
def skip_left(model):
    h = RT.monitor.get(model)
    if not h: return 0
    return max(0, int(h.get("skip_until",0) - time.time()))
def health_report():
    if not RT.monitor:
        return "（暂无监控记录）"
    lines = ["📊 模型监控状态", "-"*56]
    now = time.time()
    for m,h in RT.monitor.items():
        streak = h.get("fail_streak",0)
        skip_until = h.get("skip_until",0)
        tf = h.get("total_fail",0)
        to = h.get("total_ok",0)
        if skip_until>now:
            left = int(skip_until - now)
            status = f"⏸️ 跳过中（剩 {left}s）"
        elif streak>0:
            status = f"🟡 观察中（连续失败 {streak}）"
        else:
            status = "🟢 正常"
        lines.append(f"{m:<26}{status}  成功{to} 失败{tf}")
    return "\n".join(lines)
