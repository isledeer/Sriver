#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, socket, queue, threading, time
import urllib.request, urllib.error

from .runtime import RT
from .logging_utils import log, log_error


# ===================== network api =====================
def _call_api_worker(api_base, api_key, model, messages, timeout, abort_event, result_queue):
    """真正阻塞的调用，跑在线程里，结果放入 result_queue。"""
    try:
        data = json.dumps({"model":model,"messages":messages,"stream":False}).encode("utf-8")
        req = urllib.request.Request(f"{api_base}/chat/completions",
            data=data, headers={"Content-Type":"application/json","Authorization":f"Bearer {api_key}"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunks = []
            while True:
                if abort_event is not None and abort_event.is_set():
                    result_queue.put((None, "ABORTED"))
                    return
                buf = resp.read(512)
                if not buf:
                    break
                chunks.append(buf)
            result = json.loads(b"".join(chunks).decode("utf-8", errors="ignore"))
            choices = result.get("choices")
            if not choices:
                result_queue.put((None, "上游返回空choices"))
                return
            result_queue.put((choices[0]["message"]["content"], None))
    except socket.timeout:
        result_queue.put((None, f"超时(>{timeout}s)"))
    except urllib.error.HTTPError as e:
        result_queue.put((None, f"HTTP {e.code} {e.reason}"))
    except urllib.error.URLError as e:
        result_queue.put((None, f"连接失败({e.reason})"))
    except Exception as e:
        result_queue.put((None, str(e)[:100]))


def call_api(api_base, api_key, model, messages, timeout=30, abort_event=None):
    """线程包装版：主线程轮询 abort_event，命中立刻返回，不等上游。"""
    result_queue = queue.Queue()
    t = threading.Thread(
        target=_call_api_worker,
        args=(api_base, api_key, model, messages, timeout, abort_event, result_queue),
        daemon=True,
    )
    t.start()
    elapsed = 0.0
    while elapsed < timeout + 1:
        if abort_event is not None and abort_event.is_set():
            return None, "ABORTED"
        try:
            content, err = result_queue.get(timeout=0.3)
            return content, err
        except queue.Empty:
            elapsed += 0.3
            continue
    return None, f"超时(>{timeout}s)"


def stream_model(model, messages, output_queue, api_base, api_key, abort_event=None):
    try:
        if abort_event and abort_event.is_set():
            output_queue.put(("worker_done", model, None))
            return
        local = list(messages)
        if RT.model_feedback.get(model):
            local.insert(-1, {"role":"system","content":RT.model_feedback[model]})
        data = json.dumps({"model":model,"messages":local,"stream":True}).encode("utf-8")
        req = urllib.request.Request(
            f"{api_base}/chat/completions", data=data,
            headers={"Content-Type":"application/json","Authorization":f"Bearer {api_key}","Accept":"text/event-stream"},
            method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=RT.config["gen_timeout"])
        except urllib.error.HTTPError as e:
            output_queue.put(("error",model,f"HTTP {e.code} {e.reason}"))
            output_queue.put(("worker_done",model,None))
            return
        except socket.timeout:
            output_queue.put(("error",model,f"连接超时(>{RT.config['gen_timeout']}s)"))
            output_queue.put(("worker_done",model,None))
            return
        except urllib.error.URLError as e:
            output_queue.put(("error",model,f"连接失败({e.reason})"))
            output_queue.put(("worker_done",model,None))
            return
        ctype = resp.headers.get("Content-Type","").lower()
        full = ""
        buffer = ""
        if "text/event-stream" in ctype:
            for line_bytes in resp:
                if abort_event and abort_event.is_set():
                    output_queue.put(("worker_done", model, None))
                    return
                if not line_bytes: continue
                line = line_bytes.decode("utf-8", errors="ignore").rstrip("\r\n")
                if not line or line.startswith(":") or not line.startswith("data:"): continue
                payload = line[5:].strip()
                if payload == "[DONE]": break
                try:
                    chunk = json.loads(payload)
                    if "error" in chunk:
                        emsg = str(chunk["error"].get("message","上游报错"))[:80]
                        output_queue.put(("error",model,f"上游报错:{emsg}"))
                        output_queue.put(("worker_done",model,None))
                        return
                    delta = chunk.get("choices",[{}])[0].get("delta",{})
                    piece = delta.get("content","")
                    if piece:
                        full += piece
                        buffer += piece
                        while len(buffer) >= RT.config["stream_buffer_threshold"]:
                            output_queue.put(("chunk", model, buffer[:RT.config["stream_buffer_threshold"]]))
                            buffer = buffer[RT.config["stream_buffer_threshold"]:]
                except Exception:
                    continue
            if buffer:
                output_queue.put(("chunk", model, buffer))
        else:
            raw = resp.read().decode("utf-8", errors="ignore")
            try:
                j = json.loads(raw)
                if "error" in j:
                    emsg = str(j["error"].get("message","上游报错"))[:80]
                    output_queue.put(("error",model,f"上游报错:{emsg}"))
                    output_queue.put(("worker_done",model,None))
                    return
                full = j.get("choices",[{}])[0].get("message",{}).get("content","") or ""
                if full:
                    output_queue.put(("chunk",model,full))
            except Exception as e:
                output_queue.put(("error",model,f"非流式解析失败:{str(e)[:60]}"))
                output_queue.put(("worker_done",model,None))
                return
        if len(full.strip())==0:
            output_queue.put(("error",model,"上游返回200但是内容为空"))
        else:
            output_queue.put(("done",model,full))
    except Exception as e:
        output_queue.put(("error",model,f"未捕获异常:{str(e)[:80]}"))
    finally:
        output_queue.put(("worker_done",model,None))
def _fetch_models(api_base, api_key):
    cache_key = api_base
    now = time.time()
    if cache_key in RT.model_cache and (now - RT.model_cache_ts.get(cache_key,0)) < RT.config["model_cache_ttl"]:
        return RT.model_cache[cache_key]
    try:
        req = urllib.request.Request(f"{api_base}/models", headers={"Authorization":f"Bearer {api_key}"}, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            avail = [m["id"] for m in data.get("data", [])]
    except Exception as e:
        log(f"⚠️ 获取模型列表失败 {api_base}: {e}", "\033[91m")
        log_error("system","resolve_models",str(e)[:80],api_base)
        return []
    RT.model_cache[cache_key] = avail
    RT.model_cache_ts[cache_key] = now
    return avail
def _filter_models(available, keywords, block):
    out = []
    for kw in keywords:
        kl = kw.lower()
        for mid in available:
            ml = mid.lower()
            if kl in ml:
                if block and any(b.lower() in ml for b in block):
                    continue
                if mid not in out:
                    out.append(mid)
    return out
def resolve_models(api_base, api_key, keywords, block=None):
    explicit = RT.config.get("gen_models", [])
    if explicit:
        return explicit[:RT.config["max_models_per_round"]]
    avail = _fetch_models(api_base, api_key)
    return _filter_models(avail, keywords, block)[:RT.config["max_models_per_round"]]
def resolve_judge_from(api_base, api_key, keywords, block):
    avail = _fetch_models(api_base, api_key)
    explicit_judge = RT.config.get("judge_models", [])
    if explicit_judge:
        candidates = explicit_judge
    else:
        candidates = _filter_models(avail, keywords, block)
    return candidates[0] if candidates else None
