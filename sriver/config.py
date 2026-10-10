#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, threading, socket, urllib.request, urllib.error

from .runtime import RT


CONFIG_PATH = os.path.expanduser("~/.sriver/config.json")
SETUP_FLAG = os.path.expanduser("~/.sriver/.setup_done")
os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
SETUP_LOCK = threading.Lock()

# ===================== DEFAULT RT.config =====================
DEFAULT_CONFIG = {
    "gen_api_base": "",
    "gen_api_key": "",
    "judge_api_base": "",
    "judge_api_key": "",
    "judge_fallback_api_base": "",
    "judge_fallback_api_key": "",
    "smart_keywords": ["qwen", "deepseek"],
    "block_keywords": ["vl", "vision", "tts", "whisper", "flux", "sd", "audio", "image", "video"],
    "judge_keywords": ["grok-4.6", "grok-4.5", "grok-4"],
    "judge_block": [],
    "judge_fallback_keywords": ["agnes-2.5-flash", "agnes-3.0-flash"],
    "judge_fallback_block": [],
    "gen_models": [],
    "judge_models": [],
    "judge_fallback_models": [],
    "max_concurrency": 2,
    "max_models_per_round":5,
    "gen_timeout": 60,
    "judge_timeout": 15,
    "feedback_trigger": "ture",
    "server_host": "127.0.0.1",
    "server_port": 7860,
    "fail_streak_threshold": 2,
    "skip_cooldown_sec": 90,
    "max_output_chars": 8000,
    "tail_keep_chars": 300,
    "model_cache_ttl": 300,
    "user_input_tag": "user_input",
    "stream_buffer_threshold": 150,
    "refusal_check_head_tail_len": 200,
    "score_code_base": 50,
    "score_code_per_sensory": 3,
    "score_code_sensory_max": 20,
    "score_code_context_bonus": 10,
    "score_code_end_bonus": 10,
    "score_code_penalty_steal": -20,
    "draft_min_chars": 120
}
RT.config = dict(DEFAULT_CONFIG)
RT.config_warnings = []
def is_setup_finished():
    return os.path.exists(SETUP_FLAG)
def mark_setup_done():
    with open(SETUP_FLAG,"w",encoding="utf-8") as f:
        f.write(str(time.time()))
# Note: wizard state key hardcoded "default", single-user assumption.
# If multiple LAN clients connect, wizard sessions will overwrite each other.
def clear_setup_state():
    """clear config, flag, runtime cache, wizard sessions"""
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
    if os.path.exists(SETUP_FLAG):
        os.remove(SETUP_FLAG)
    RT.model_cache.clear()
    RT.model_cache_ts.clear()
    RT.monitor.clear()
    save_monitor()
    RT.model_feedback.clear()
    RT.wizard_state.clear()
def persist_config():
    with open(CONFIG_PATH,"w",encoding="utf-8") as f:
        json.dump(RT.config, f, ensure_ascii=False, indent=2)
def load_config_safe():
    if not os.path.exists(CONFIG_PATH):
        return False
    try:
        with open(CONFIG_PATH,"r",encoding="utf-8") as f:
            u = json.load(f)
    except json.JSONDecodeError as e:
        log(f"⚠️ config.json JSON格式损坏: {str(e)}","\033[91m")
        return False
    except Exception as e:
        log(f"⚠️ 读取config.json异常: {str(e)}","\033[91m")
        return False
    final, warns = validate_config(u)
    RT.config = final
    RT.config_warnings = warns
    return True
def validate_config(cfg: dict):
    warnings = []
    final = dict(DEFAULT_CONFIG)
    final.update(cfg)
    required = ("gen_api_base","gen_api_key")
    for k in required:
        if not final.get(k):
            raise ValueError(f"必填配置项缺失: {k}")
    for urlkey in ("gen_api_base","judge_api_base","judge_fallback_api_base"):
        v = final.get(urlkey)
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"{urlkey} 不是合法URL: {v}")
    if final["max_concurrency"] >5:
        warnings.append(f"max_concurrency={final['max_concurrency']} 建议2-3")
    if final["fail_streak_threshold"]>5:
        warnings.append(f"fail_streak_threshold={final['fail_streak_threshold']} 建议2")
    if final["gen_timeout"]>120:
        warnings.append(f"gen_timeout={final['gen_timeout']}s 建议30-60")
    return final, warnings
def test_endpoint(base, key):
    try:
        req = urllib.request.Request(f"{base}/models",
            headers={"Authorization":f"Bearer {key}"}, method="GET")
        with urllib.request.urlopen(req, timeout=12) as r:
            if 200 <= r.status <300:
                resp_json = json.load(r)
                avail = [m["id"] for m in resp_json.get("data", [])]
                return True, "✅ 端点连通正常", avail
            else:
                return False, f"⚠️ HTTP {r.status}", []
    except socket.timeout:
        return False, "❌ 请求超时，请检查地址或网络", []
    except urllib.error.HTTPError as e:
        return False, f"❌ HTTP {e.code}，大概率密钥/地址错误", []
    except Exception as e:
        return False, f"❌ 连接失败：{str(e)[:60]}", []

def _filter_chat_models(models, block_keywords):
    """向导专用：过滤掉明显不是 chat 的模型"""
    return [m for m in models
            if not any(b.lower() in m.lower() for b in block_keywords)]

