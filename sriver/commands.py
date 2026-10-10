#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re

from .runtime import RT


def parse_command(text):
    """命令解析：白名单制。不在白名单的 /xxx 当普通对话；// 转义剥一个斜杠。"""
    if not text:
        return None, [], text
    lines = text.split("\n", 1)
    first = lines[0].strip()
    rest = lines[1] if len(lines) > 1 else ""
    if not first.startswith("/"):
        return None, [], text
    if first.startswith("//"):
        new_text = first[1:] + ("\n" + rest if rest else "")
        return None, [], new_text
    parts = first[1:].split()
    if not parts:
        return None, [], text
    raw_cmd = parts[0].lower()
    args = parts[1:]
    ALIASES = {
        "help": "help", "帮助": "help",
        "stats": "stats", "统计": "stats",
        "health": "health", "状态": "health",
        "quotes": "quotes", "金句": "quotes",
        "reset": "reset", "重置": "reset",
        "models": "models", "模型": "models",
        "config": "config", "配置": "config",
        "version": "version", "版本": "version",
        "debug": "debug", "调试": "debug", "测试": "debug",
        "resetconfig": "resetconfig", "重建": "resetconfig",
        "only": "only",
        "judge": "judge",
        "request": "request",
    }
    canonical = ALIASES.get(raw_cmd)
    if canonical is None:
        return None, [], text
    return canonical, args, rest.strip()


def print_sep(title, color="\033[96m"):
    print(f"\n{color}{'='*60}\033[0m")
    print(f"{color}{title}\033[0m")
    print(f"\033[96m{'='*60}\033[0m")
def strip_user_tag(text):
    if not text: return text
    _tag = RT.config.get("user_input_tag","user_input")
    escaped = text.replace(f"<{_tag}>","").replace(f"</{_tag}>","")
    return re.sub(rf'</?{re.escape(_tag)}>', '', escaped)
