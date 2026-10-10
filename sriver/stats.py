#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, datetime


STATS_PATH  = os.path.expanduser("~/.sriver/stats.json")
QUOTES_PATH = os.path.expanduser("~/.sriver/quotes.json")


# ===================== stats / quotes =====================
def load_stats():
    if os.path.exists(STATS_PATH):
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
def save_stats(stats):
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
def update_stats(model, outcome, code_score=None, judge_score=None, final_score=None):
    stats = load_stats()
    if model not in stats:
        stats[model] = {"participate":0,"win":0,"refused":0,"failed":0,
                        "code_sum":0.0,"judge_sum":0.0,"final_sum":0.0,"scored":0}
    s = stats[model]
    s["participate"] +=1
    if outcome in ("refused","failed"):
        s[outcome] +=1
    elif outcome == "win":
        s["win"] +=1
    if code_score is not None:
        s["code_sum"] += code_score
        s["judge_sum"] += (judge_score or 0)
        s["final_sum"] += (final_score or 0)
        s["scored"] +=1
    save_stats(stats)
def load_quotes():
    if os.path.exists(QUOTES_PATH):
        with open(QUOTES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []
def add_quote(model, quote):
    if not quote or len(quote)<5: return
    qs = load_quotes()
    qs.append({"time":datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"model":model,"quote":quote})
    qs = qs[-200:]
    with open(QUOTES_PATH, "w", encoding="utf-8") as f:
        json.dump(qs, f, ensure_ascii=False, indent=2)
