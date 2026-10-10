#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, queue, threading, os, re, time, datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor

from .runtime import RT
from .logging_utils import log, log_error, log_round_summary
from .config import SETUP_LOCK, clear_setup_state, load_config_safe
from .health import is_skipped, skip_left, health_report, load_monitor, save_monitor, record_ok, record_fail
from .stats import load_stats, update_stats, add_quote, load_quotes
from .commands import parse_command, print_sep, strip_user_tag
from .wizard import handle_setup_wizard, parse_model_selection
from .network import stream_model, _fetch_models, resolve_models, resolve_judge_from
from .judge import judge_anonymous, score_judge, extract_weakness
from .core import (
    SYSTEM_PROMPT,
    sanitize_wizard_messages,
    is_refusal,
    score_code,
    detect_radar,
)


# ===================== HTTP Handler =====================
def _handle_command(cmd, args, rest, push_cot, final_reply):
    """处理 /xxx 命令。返回非 False 表示已处理，主流程直接 return。"""
    if cmd == "help":
        help_text = [
            "📖 Sriver 命令列表",
            "-" * 40,
            "/help 或 /帮助      显示本帮助",
            "/stats 或 /统计     查看模型战绩榜",
            "/health 或 /状态    查看模型监控状态",
            "/quotes 或 /金句    查看裁判金句",
            "/models 或 /模型    查看或修改模型配置",
            "/config 或 /配置    查看当前配置（密钥掩码）",
            "/version 或 /版本   查看版本号",
            "/debug 或 /调试     开关 debug 模式",
            "/only 模型a,模型b   本轮只用这些模型",
            "/judge 模型名       临时切换主裁判",
            "/reset 或 /重置     清空战绩和监控记录（配置不动）",
            "/resetconfig 或 /重建  清空所有配置，重新初始化",
            "",
            "反馈模式：消息第一行发 ture，本轮结束后给每个模型反馈缺点。",
            "转义：// 开头会被当作普通对话（剥掉一个斜杠）。",
            "普通对话：直接发消息即可，不需要命令前缀。",
        ]
        final_reply("\n".join(help_text))
        return

    if cmd == "version":
        final_reply("Sriver v0.1.11-wizard-patch03")
        return

    if cmd == "config":
        def _mask_cfg(k):
            k = str(k or "")
            if not k:
                return "【未设置】"
            return k[:8] + "********" if len(k) > 8 else "***"
        cfg_lines = ["⚙️ 当前配置（密钥掩码）", "-" * 40]
        cfg_lines.append(f"生成端：{RT.config.get('gen_api_base') or '【未设置】'}")
        cfg_lines.append(f"生成 Key：{_mask_cfg(RT.config.get('gen_api_key'))}")
        cfg_lines.append(f"裁判端：{RT.config.get('judge_api_base') or '【未设置】'}")
        cfg_lines.append(f"裁判 Key：{_mask_cfg(RT.config.get('judge_api_key'))}")
        cfg_lines.append(f"生成模型（显式）：{RT.config.get('gen_models') or '【关键词模式】'}")
        cfg_lines.append(f"裁判模型（显式）：{RT.config.get('judge_models') or '【关键词模式】'}")
        cfg_lines.append(f"最大并发：{RT.config.get('max_concurrency')}")
        cfg_lines.append(f"每轮最多模型数：{RT.config.get('max_models_per_round')}")
        cfg_lines.append(f"生成超时：{RT.config.get('gen_timeout')}s")
        cfg_lines.append(f"裁判超时：{RT.config.get('judge_timeout')}s")
        cfg_lines.append(f"反馈触发词：{RT.config.get('feedback_trigger')}")
        cfg_lines.append(f"跳过冷却：{RT.config.get('skip_cooldown_sec')}s")
        cfg_lines.append(f"服务器端口：{RT.config.get('server_port')}")
        final_reply("\n".join(cfg_lines))
        return

    if cmd == "reset":
        try:
            if os.path.exists(STATS_PATH):
                os.remove(STATS_PATH)
            if os.path.exists(HEALTH_PATH):
                os.remove(HEALTH_PATH)
            RT.monitor.clear()
            save_monitor()
            final_reply("✅ 已清空战绩和模型监控记录（配置未动，端点和模型保持原样）。")
        except Exception as e:
            final_reply(f"❌ 清空失败：{e}")
        return

    if cmd == "stats":
        stats = load_stats()
        if not stats:
            final_reply("暂无战绩数据。")
            return
        lines = ["📊 模型战绩榜", "-" * 40,
                 f"{'模型':<24}{'参赛':>5}{'胜':>5}{'拒答':>6}{'均分':>7}"]
        for _m, s in sorted(stats.items(),
                             key=lambda kv: kv[1]["final_sum"] / max(kv[1]["scored"], 1),
                             reverse=True):
            avg = s["final_sum"] / max(s["scored"], 1)
            lines.append(f"{_m:<24}{s['participate']:>5}{s['win']:>5}{s['refused']:>6}{avg:>7.1f}")
        final_reply(chr(10).join(lines))
        return

    if cmd == "health":
        final_reply(health_report())
        return

    if cmd == "quotes":
        quotes = load_quotes()
        if not quotes:
            final_reply("暂无裁判金句。")
            return
        lines = ["💬 裁判金句榜（最近 10 条）", "-" * 40]
        for q in quotes[-10:]:
            lines.append(f"[{q['time']}] {q['model']}")
            lines.append(f"  {q['quote']}")
        final_reply(chr(10).join(lines))
        return

    if cmd == "debug":
        RT.runtime_overrides["debug"] = not RT.runtime_overrides.get("debug", False)
        final_reply(f"✅ Debug 模式：{RT.runtime_overrides['debug']}")
        return

    if cmd == "only":
        if not args:
            final_reply("用法：/only qwen,grok")
            return
        only_list = args[0].split(",")
        RT.runtime_overrides["only"] = only_list
        final_reply(f"✅ 本轮仅允许：{only_list}")
        return

    if cmd == "judge":
        if not args:
            final_reply("用法：/judge grok-4.6")
            return
        RT.judge_model_resolved = (RT.config["judge_api_base"], RT.config["judge_api_key"], args[0])
        final_reply(f"✅ 主裁判已临时切换为：{args[0]}")
        return

    if cmd == "request":
        final_reply("⚠️ request 命令暂未实现")
        return

    # ========= /models command handler =========
    if cmd == "models":
        sub = args[0].lower() if args else ""
        if sub == "":
            lines = ["📋 当前模型配置"]
            ge = RT.config["gen_models"]
            je = RT.config["judge_models"]
            jfe = RT.config["judge_fallback_models"]
            lines.append(f"gen_models(显式): {ge if ge else '【未设置，使用关键词模式】'}")
            lines.append(f"judge_models(显式): {je if je else '【未设置，使用关键词模式】'}")
            lines.append(f"judge_fallback_models(显式): {jfe if jfe else '【未设置，使用关键词模式】'}")
            lines.append("\n子命令：")
            lines.append("/models gen 1,2,3")
            lines.append("/models judge 2")
            lines.append("/models reset 清空显式列表回到关键词模式")
            final_reply("\n".join(lines))
            return
        elif sub == "reset":
            RT.config["gen_models"] = []
            RT.config["judge_models"] = []
            RT.config["judge_fallback_models"] = []
            persist_config()
            # 清空裁判全局缓存
            RT.judge_model_resolved = None
            RT.judge_fallback_resolved = None
            final_reply("✅ 已清空显式模型列表，切回关键词匹配模式。")
            return
        elif sub in ("gen","judge"):
            sel_str = " ".join(args[1:]).strip()
            if not sel_str:
                final_reply("❌缺少编号参数，示例：/models gen 1,3")
                return
            if sub == "gen":
                avail = _fetch_models(RT.config["gen_api_base"], RT.config["gen_api_key"])
            else:
                avail = _fetch_models(RT.config["judge_api_base"], RT.config["judge_api_key"])
            selected, err = parse_model_selection(sel_str, avail)
            if err:
                final_reply(f"❌ {err}")
                return
            if sub == "gen":
                if len(selected) == 0:
                    final_reply("❌ gen至少选择1个模型，不能NONE。")
                    return
                if len(selected) > RT.config["max_models_per_round"]:
                    push_cot(f"⚠️ 选择数量超过max_models_per_round({RT.config['max_models_per_round']})，将被截断。")
                RT.config["gen_models"] = selected
            else:
                if len(selected) != 1:
                    final_reply("❌ judge只能选择1个模型。")
                    return
                RT.config["judge_models"] = selected
                # 修改裁判，立刻清空解析缓存
                RT.judge_model_resolved = None
                RT.judge_fallback_resolved = None
            persist_config()
            final_reply(f"✅ {sub}模型已更新：{selected}")
            return
        else:
            final_reply(f"❌未知子命令 {sub}")
            return
    if cmd == "resetconfig":
        with SETUP_LOCK:
            clear_setup_state()
            RT.judge_model_resolved = None
            RT.judge_fallback_resolved = None
        final_reply("🔁 配置已清空！重新发送消息即可进入配置向导。"); return
    return False


def _run_generation(messages, user_input, push_cot, push_raw, final_reply, abort_event,
                    available_models, gen_api_base, gen_api_key, max_concurrency):
    """并发调度：多模型写草稿，过滤拒答，返回成功草稿列表。None 表示提前终止。"""
    queue_models = list(available_models)
    running = []
    successful = []
    announced = set()
    output_queue = queue.Queue()

    def start_model(m):
        running.append(m)
        executor.submit(stream_model, m, messages, output_queue, gen_api_base, gen_api_key, abort_event)
        push_cot(f"⏳ [{m}] 奋笔疾书...")
    output_queue = queue.Queue()
    running = []
    successful = []
    announced = set()
    def start_model(m):
        running.append(m)
        executor.submit(stream_model, m, messages, output_queue, gen_api_base, gen_api_key, abort_event)
        push_cot(f"⏳ [{m}] 奋笔疾书...")
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        while len(running) < max_concurrency and queue_models:
            start_model(queue_models.pop(0))
        active = len(running)
        while active > 0 and not abort_event.is_set():
            try:
                kind, model, data = output_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "chunk":
                if model not in announced:
                    push_cot(f"\n\n━━━━━ [{model}] 草稿输出 ━━━━━")
                    announced.add(model)
                push_raw(data)
            elif kind == "done":
                print(f"\n\033[90m📄 [{model}] 原始草稿:\n{data}\n{'-'*40}\033[0m")
                refused, refuse_reason = is_refusal(data)
                if refused:
                    push_cot(f"\n\n❌ [{model}] 淘汰：{refuse_reason}")
                    log(f"❌ [{model}] 淘汰：{refuse_reason}", "\033[91m")
                    log_error(model, "refused", refuse_reason, data[:100])
                    update_stats(model, "refused")
                    record_ok(model)
                else:
                    push_cot(f"\n\n✅ [{model}] 完成（{len(data)} 字）")
                    successful.append((model, data, detect_radar(data)))
                    record_ok(model)
            elif kind == "error":
                push_cot(f"\n❌ [{model}] {data}")
                log(f"❌ [{model}] 失败：{data}", "\033[91m")
                log_error(model, "gen", data)
                update_stats(model, "failed")
                record_fail(model, data)
            elif kind == "worker_done":
                active -=1
                if queue_models and not abort_event.is_set():
                    start_model(queue_models.pop(0))
                    active +=1
    if abort_event.is_set():
        log("🛑 客户端断开，本轮提前终止（不调用裁判）", "\033[93m")
        return None

    RT.model_feedback = {}
    for i,(m,c,h) in enumerate(successful,1):
        print_sep(f"📜 草稿 {i} | {m} | {len(c)}字", "\033[93m")
        print(c)
        print()
    return successful


def _run_judging(successful, user_input, push_cot, abort_event, trigger):
    """裁判打分 + 挑优。返回最佳草稿字符串；None 表示 aborted。"""
    primary_name = RT.judge_model_resolved[2] if RT.judge_model_resolved else "无(纯代码打分)"
    fallback_name = RT.judge_fallback_resolved[2] if RT.judge_fallback_resolved else "无"
    print_sep(f"⚖️ 匿名裁判 (主:{primary_name}|备:{fallback_name})", "\033[95m")
    push_cot(f"\n⚖️ 裁判开始盲审 {len(successful)} 份...")
    final_scores = []
    new_fb = []
    judge_degraded = False
    for i,(m,c,h) in enumerate(successful,1):
        if abort_event.is_set():
            log("🛑 裁判阶段断连，提前退出本轮", "\033[93m")
            return None
        code_score, code_reason = score_code(c, h, user_input)
        push_cot(f"⚖️ 盲审第 {i} 份...")
        jr = judge_anonymous(c, user_input, RT.judge_model_resolved, RT.judge_fallback_resolved, abort_event=abort_event)
        if jr:
            used_judge = jr.get("_judge_model","?")
            if RT.runtime_overrides.get("debug"):
                log(json.dumps(jr, ensure_ascii=False, indent=2), "\033[90m")
            add_quote(m, jr.get("总评",""))
            push_cot(f"📝 第 {i} 份由[{used_judge}]评分。总评:{jr.get('总评','')}")
            if trigger:
                note = extract_weakness(jr)
                if note: new_fb.append((m, note))
        else:
            judge_degraded = True
        jscore, jreason = score_judge(jr)
        if jscore is None:
            final = code_score
            wdesc = "仅代码规则评分 ⚠️裁判不可用/已跳过"
        else:
            final = code_score*0.6 + jscore*0.4
            wdesc = "6:4(代码:裁判)"
        final_scores.append({"model":m,"content":c,"code":code_score,"judge":jscore,"final":final})
        log(f"📊 [{m}] 代码{code_score:.1f} 裁判{jscore if jscore else 'N/A'} 最终{final:.1f} ({wdesc})", "\033[92m")
    if judge_degraded:
        push_cot("\n⚠️【降级提示】裁判全部不可用/已手动跳过，本轮仅使用代码规则打分，质量可能波动。")
    if trigger and new_fb:
        RT.model_feedback = dict(new_fb)
        push_cot(f"📝 已为 {len(new_fb)} 个模型生成下一轮反馈。")
    if not final_scores:
        best = "本次所有模型均被上游服务拦截或拒答，请稍后重试。"
        log("⚠️ 无有效草稿", "\033[93m")
    else:
        final_scores.sort(key=lambda x:(x["final"],x["code"]), reverse=True)
        best = final_scores[0]["content"]
        log(f"🏆 夺魁: {final_scores[0]['model']} ({final_scores[0]['final']:.1f})", "\033[95m")
        update_stats(final_scores[0]["model"], "win", final_scores[0]["code"], final_scores[0]["judge"], final_scores[0]["final"])
        for e in final_scores[1:]:
            update_stats(e["model"], "participate", e["code"], e["judge"], e["final"])
    push_cot(f"🏆 终审完成，正式回复生成中...")
    return best


class SriverHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length",0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        messages = body.get("messages", [])
        user_input = messages[-1]["content"] if messages else ""
        abort_event = threading.Event()
        self.send_response(200)
        self.send_header("Content-Type","text/event-stream")
        self.send_header("Cache-Control","no-cache")
        self.end_headers()
        def push_raw(text):
            if abort_event.is_set():
                return
            try:
                chunk = {"choices":[{"delta":{"reasoning_content":text}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                abort_event.set()
        def push_cot(text): push_raw(text+"\n")
        def final_reply(text):
            if abort_event.is_set():
                return
            try:
                chunk = {"choices":[{"delta":{"content":text}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                abort_event.set()
        if handle_setup_wizard(user_input, push_cot, final_reply):
            return
        # 每轮无条件清洗向导消息，幂等
        messages = sanitize_wizard_messages(messages)
        user_input = messages[-1]["content"] if messages else ""
        cmd, args, rest = parse_command(user_input)
        if _handle_command(cmd, args, rest, push_cot, final_reply) is not False:
            return

        if messages and messages[0]["role"] == "system":
            messages[0]["content"] = messages[0]["content"] + SYSTEM_PROMPT
        else:
            messages.insert(0, {"role":"system","content":"你是一个文字游戏GM和角色扮演引擎。"+SYSTEM_PROMPT})
        if cmd is not None and cmd != "models":
            if not rest: final_reply("命令已执行。"); return
            messages[-1]["content"] = rest
            user_input = rest
        trigger = False
        if messages and messages[-1].get("role") == "user":
            lines = messages[-1]["content"].split("\n",1)
            if lines[0].strip().lower() == RT.config["feedback_trigger"].lower():
                trigger = True
                messages[-1]["content"] = lines[1].strip() if len(lines)>1 else ""
                user_input = messages[-1]["content"]
                if not RT.ture_hint_shown:
                    push_cot("💡 反馈模式已触发：本轮结束后，每个模型会收到自己专属的缺点笔记，下一轮生效。")
                    RT.ture_hint_shown = True
        if messages and messages[-1].get("role") == "user":
            _c = messages[-1]["content"]
            _tag = RT.config.get("user_input_tag","user_input")
            if not _c.startswith(f"<{_tag}>"):
                messages[-1]["content"] = f"<{_tag}>{_c}</{_tag}>"
        print_sep(f"🎬 新一轮 | 玩家: {user_input[:60]}", "\033[94m")
        log(f"🎬 新一轮 | 玩家: {user_input[:100]}")
        GEN_API_BASE   = RT.config["gen_api_base"]
        GEN_API_KEY    = RT.config["gen_api_key"]
        SMART_KEYWORDS = RT.config["smart_keywords"]
        BLOCK_KEYWORDS = RT.config["block_keywords"]
        JUDGE_API_BASE = RT.config["judge_api_base"]
        JUDGE_API_KEY  = RT.config["judge_api_key"]
        JUDGE_KEYWORDS = RT.config["judge_keywords"]
        JUDGE_BLOCK    = RT.config["judge_block"]
        JUDGE_FB_API_BASE = RT.config["judge_fallback_api_base"]
        JUDGE_FB_API_KEY  = RT.config["judge_fallback_api_key"]
        JUDGE_FB_KEYWORDS = RT.config["judge_fallback_keywords"]
        JUDGE_FB_BLOCK    = RT.config["judge_fallback_block"]
        MAX_CONCURRENCY = RT.config["max_concurrency"]
        all_models = resolve_models(GEN_API_BASE, GEN_API_KEY, SMART_KEYWORDS, BLOCK_KEYWORDS)
        if RT.runtime_overrides.get("only"):
            only = RT.runtime_overrides["only"]
            all_models = [m for m in all_models if any(o.lower() in m.lower() for o in only)]
            RT.runtime_overrides.pop("only")
        if not all_models:
            final_reply("❌ 无可用模型。"); return
        available = []
        skipped_models = []
        for m in all_models:
            if is_skipped(m):
                skipped_models.append(m)
            else:
                available.append(m)
        if skipped_models:
            skip_info = ", ".join(f"{m}(剩{skip_left(m)}s)" for m in skipped_models)
            log(f"⏸️ 被动监控跳过：{skip_info}", "\033[93m")
            push_cot(f"⏸️ 跳过监控中的模型：{skip_info}")
        if not available:
            report = health_report()
            final_reply(f"❌ 所有候选模型都在被动监控的跳过期内。\n\n{report}\n\n{RT.config['skip_cooldown_sec']}秒后会自动重试，或用 /resetconfig 清空配置。")
            return
        queue_models = available
        # 如果judge_models为空，禁用裁判
        if len(RT.config.get("judge_models", [])) == 0:
            RT.judge_model_resolved = None
            RT.judge_fallback_resolved = None
        else:
            if RT.judge_model_resolved is None:
                jm = resolve_judge_from(JUDGE_API_BASE, JUDGE_API_KEY, JUDGE_KEYWORDS, JUDGE_BLOCK)
                if jm:
                    RT.judge_model_resolved = (JUDGE_API_BASE, JUDGE_API_KEY, jm)
                    log(f"✅ 主裁判: {jm}", "\033[92m")
            if RT.judge_fallback_resolved is None and JUDGE_FB_API_BASE:
                fm = resolve_judge_from(JUDGE_FB_API_BASE, JUDGE_FB_API_KEY, JUDGE_FB_KEYWORDS, JUDGE_FB_BLOCK)
                if fm:
                    RT.judge_fallback_resolved = (JUDGE_FB_API_BASE, JUDGE_FB_API_KEY, fm)
                    log(f"✅ 备选裁判: {fm}", "\033[92m")
        successful = _run_generation(messages, user_input, push_cot, push_raw, final_reply, abort_event,
                                      available, GEN_API_BASE, GEN_API_KEY, MAX_CONCURRENCY)
        if successful is None:
            return

        best = _run_judging(successful, user_input, push_cot, abort_event, trigger)
        if best is None:
            return

        best = strip_user_tag(best)
        max_chars = RT.config.get("max_output_chars",8000)
        tail_keep = RT.config.get("tail_keep_chars",300)
        truncated = False
        if len(best) > max_chars:
            truncated = True
            head = best[:max_chars]
            tail = best[-tail_keep:] if len(best) > max_chars + tail_keep else ""
            best = head + f"……（中段略，共{len(best)}字）……" + tail
            log(f"⚠️ 本轮答案超长({len(best)}字)，已截断", "\033[93m")
        final_reply(best)
        try:
            per_model_data = {}
            for fs in final_scores:
                per_model_data[fs["model"]] = f"code={fs['code']:.0f} judge={fs.get('judge','N/A')} final={fs['final']:.0f}"
            winner_judge_score = final_scores[0].get("judge") if final_scores else None
            is_degraded = winner_judge_score is None and final_scores
            log_round_summary(user_input, per_model_data, final_scores[0]["model"] if final_scores else "None", is_degraded, truncated)
        except Exception:
            pass
def main():
    load_monitor()
    load_config_safe()
    server = HTTPServer((RT.config["server_host"], RT.config["server_port"]), SriverHandler)
    print(f"\033[92m🚀 Sriver v0.1.11-wizard-patch03 已启动: http://{RT.config['server_host']}:{RT.config['server_port']}\033[0m")
    print(f"\033[90m💡 首次使用直接发消息触发交互式三步配置向导！\033[90m")
    print(f"\033[90m   重置配置命令：/resetconfig\033[90m")
    print(f"\033[90m⚠️设计假设：单本地用户；多局域网客户端向导会话会互相覆盖\033[90m")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 已停止")
        if RT.log_file is not None:
            RT.log_file.close()


if __name__ == "__main__":
    main()
