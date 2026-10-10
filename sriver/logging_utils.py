#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, datetime

from .runtime import RT

LOG_DIR   = os.path.expanduser("~/.sriver/logs")
ERROR_LOG = os.path.expanduser("~/.sriver/errors.log")
os.makedirs(LOG_DIR, exist_ok=True)


# ===================== logging =====================
def log(msg, color=""):
    now_dt = datetime.datetime.now()
    today_str = now_dt.strftime("%Y-%m-%d")
    if RT.log_file_date != today_str:
        if RT.log_file is not None:
            RT.log_file.close()
        log_path = os.path.join(LOG_DIR, f"{today_str}.log")
        RT.log_file = open(log_path, "a", encoding="utf-8")
        RT.log_file_date = today_str
    plain = re.sub(r'\033\[[0-9;]*m', '', msg)
    RT.log_file.write(f"[{now_dt.strftime('%H:%M:%S')}] {plain}\n")
    RT.log_file.flush()
    print(f"{color}{msg}\033[0m" if color else msg)
def log_error(model, stage, reason, detail=""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{stage}] [{model}] {reason}"
    if detail:
        line += f" | {detail[:300]}"
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(line+"\n")
def log_round_summary(user_input, per_model, winner, degraded, truncated):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [f"[{ts}]", f"user={user_input[:40]!r}"]
    for m, info in per_model.items():
        parts.append(f"{m}({info})")
    parts.append(f"winner={winner}")
    if degraded: parts.append("DEGRADED")
    if truncated: parts.append("TRUNCATED")
    with open(os.path.join(LOG_DIR, "rounds.log"), "a", encoding="utf-8") as f:
        f.write(" | ".join(parts)+"\n")


