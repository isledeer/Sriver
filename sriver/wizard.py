#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from .runtime import RT
from .config import SETUP_LOCK, test_endpoint, _filter_chat_models
from .config import mark_setup_done, persist_config, clear_setup_state, is_setup_finished



def _finish_with_skip(state: dict) -> tuple[str, dict]:
    """SKIP 分支公共逻辑：保存配置 + 拼输出。"""
    gen_base = state["gen_base"]
    gen_key = state["gen_key"]
    judge_base = state["judge_base"]
    judge_key = state["judge_key"]
    RT.config["gen_api_base"] = gen_base
    RT.config["gen_api_key"] = gen_key
    RT.config["judge_api_base"] = judge_base
    RT.config["judge_api_key"] = judge_key
    RT.config["gen_models"] = state["selected_gen"]
    RT.config["judge_models"] = []
    persist_config()
    mark_setup_done()
    RT.judge_model_resolved = None
    RT.judge_fallback_resolved = None
    RT.model_cache.clear()
    RT.model_cache_ts.clear()

    def _mask(k):
        return k[:8] + "********" if len(k) > 8 else "***"

    out_lines = [
        "🎉 配置完成，已保存。（已SKIP LLM裁判，启用纯代码打分）",
        f"生成端：{gen_base}",
        f"生成Key：{_mask(gen_key)}",
        f"生成模型：{','.join(state['selected_gen'])}",
        "⚠️ 已跳过LLM裁判：仅使用内置代码规则打分，不会调用裁判大模型。",
        "",
        "可用命令：/models  /stats /health /quotes /debug",
        "反馈模式：第一行发 ture 触发",
        "",
        "══════════════════════════════════════",
        "⚠️ 重要：请【新建一个对话】再开始游玩",
        "══════════════════════════════════════",
        "原因：当前对话里含有向导交互的配置信息",
        "（URL / Key / 编号 / SKIP），如果直接继续聊天，",
        "这些内容会被当作剧情上下文发给 AI 模型，",
        "导致模型困惑、复述你的配置、或者拒绝回答。",
        "",
        "操作：在 RK 里长按当前对话 → 删除或新建，",
        "然后在【新对话】里正常发消息即可。",
        "══════════════════════════════════════",
    ]
    return "\n".join(out_lines), {"step": "done"}


def parse_model_selection(user_input: str, available_models: list[str]) -> tuple[list[str]|None, str|None]:
    text = user_input.strip()
    # 归一化中文标点，全角空格转为普通空格
    text = text.replace("，", ",").replace("、", ",").replace(" ", " ")
    text_upper = text.upper()
    if text_upper == "ALL":
        return available_models.copy(), None
    if text_upper == "NONE":
        return [], None
    parts = [p.strip() for p in text.split(",")]
    selected = []
    for p in parts:
        if not p.isdigit():
            return None, f"格式错误：「{p}」不是数字，请输入编号，逗号分隔。"
        idx = int(p)
        if not 1 <= idx <= len(available_models):
            return None, f"编号 {idx} 超出范围，可用编号1~{len(available_models)}"
        selected.append(available_models[idx-1])
    return selected, None



# ===================== wizard step pure functions =====================
def wizard_step1_init(user_input: str, state: dict) -> tuple[str, dict]:
    raw_lines = [l.rstrip() for l in user_input.splitlines()]
    lines = [x.strip() for x in raw_lines if x.strip()]
    guide_text = """👋 Sriver 初始化 Step1：填写端点信息

【格式】
第1行：生成端 URL（必须 http:// 或 https:// 开头）
第2行：生成端 API-Key（原样粘贴，不加引号）
第3行：裁判端 URL（可选，不填则复用第1行）
第4行：裁判端 Key（可选，不填则复用第2行）

【最简示例】（两行即可，请替换为你自己的，不要复制！）
https://your-endpoint.com/v1
your-api-key-here

【分离示例】（生成端和裁判端不同时，4行）
https://gen-endpoint.com/v1
gen-key-here
https://judge-endpoint.com/v1
judge-key-here

填写完毕直接发送；输入 /resetconfig 放弃本次配置。"""
    if not (len(lines)>=2 and lines[0].startswith(("http://","https://")) and len(lines[1])>3):
        return guide_text, {"step":"init"}
    gen_base = lines[0]
    gen_key = lines[1]
    judge_base = lines[2] if len(lines)>=3 else ""
    judge_key = lines[3] if len(lines)>=4 else ""
    if judge_base and not judge_key:
        judge_key = gen_key
    if not judge_base:
        judge_base = gen_base
        judge_key = gen_key
    ok1, msg1, gen_model_list_raw = test_endpoint(gen_base, gen_key)
    if not ok1:
        return f"❌ 生成端连通失败：{msg1}\n请重新填写端点信息。", {"step":"init"}
    ok2, msg2, judge_model_list_raw = test_endpoint(judge_base, judge_key)
    if not ok2:
        return f"❌ 裁判端连通失败：{msg2}\n请重新填写端点信息。", {"step":"init"}
    gen_model_list = _filter_chat_models(gen_model_list_raw, RT.config["block_keywords"])
    judge_model_list = _filter_chat_models(judge_model_list_raw, RT.config["block_keywords"])
    state["gen_base"] = gen_base
    state["gen_key"] = gen_key
    state["judge_base"] = judge_base
    state["judge_key"] = judge_key
    state["gen_model_list"] = gen_model_list
    state["judge_model_list"] = judge_model_list
    buf = ["✅ Step1 连通成功，请选择【生成模型】","发送编号逗号分隔，支持 ALL / NONE"]
    for idx,m in enumerate(gen_model_list, start=1):
        buf.append(f"{idx}. {m}")
    buf.append("\n示例：1,3  或者 ALL")
    new_state = dict(state)
    new_state["step"] = "choose_gen"
    return "\n".join(buf), new_state

def wizard_step2_choose_gen(user_input: str, state: dict) -> tuple[str, dict]:
    avail = state["gen_model_list"]
    input_upper = user_input.strip().upper()
    if input_upper == "SKIP":
        sel, err = parse_model_selection("ALL", avail)
        if err is not None or len(sel) == 0:
            reply = "❌ 没有可用生成模型，无法SKIP。请正常选择生成模型编号。"
            new_state = dict(state)
            new_state["step"] = "choose_gen"
            return reply, new_state
        state["selected_gen"] = sel
        return _finish_with_skip(state)

    sel, err = parse_model_selection(user_input, avail)
    if err is not None:
        reply = f"❌ {err}\n请重新输入生成模型编号。"
        new_state = dict(state)
        new_state["step"] = "choose_gen"
        return reply, new_state
    if len(sel) ==0:
        reply = "❌ 生成模型至少选择1个，不能填NONE。"
        new_state = dict(state)
        new_state["step"] = "choose_gen"
        return reply, new_state
    state["selected_gen"] = sel
    j_avail = state["judge_model_list"]
    buf = ["✅ Step2 生成模型已选择，请选择【主裁判模型】",
           "💡 如果你只想多模型并发、不需要裁判择优，回复 SKIP"]
    for idx,m in enumerate(j_avail, start=1):
        buf.append(f"{idx}. {m}")
    buf.append("\n示例：2  或者回复 SKIP")
    new_state = dict(state)
    new_state["step"] = "choose_judge"
    return "\n".join(buf), new_state

def wizard_step3_choose_judge(user_input: str, state: dict) -> tuple[str, dict]:
    avail = state["judge_model_list"]
    # SKIP：跳过裁判选择，启用纯代码打分
    if user_input.strip().upper() == "SKIP":
        return _finish_with_skip(state)

    sel, err = parse_model_selection(user_input, avail)
    if err is not None:
        reply = f"❌ {err}\n请重新输入裁判模型编号。"
        new_state = dict(state)
        new_state["step"] = "choose_judge"
        return reply, new_state
    if len(sel) !=1:
        reply = "❌ 主裁判必须且只能选择1个模型。"
        new_state = dict(state)
        new_state["step"] = "choose_judge"
        return reply, new_state
    gen_base = state["gen_base"]
    gen_key = state["gen_key"]
    judge_base = state["judge_base"]
    judge_key = state["judge_key"]
    RT.config["gen_api_base"] = gen_base
    RT.config["gen_api_key"] = gen_key
    RT.config["judge_api_base"] = judge_base
    RT.config["judge_api_key"] = judge_key
    RT.config["gen_models"] = state["selected_gen"]
    RT.config["judge_models"] = sel
    persist_config()
    mark_setup_done()
    RT.model_cache.clear()
    RT.model_cache_ts.clear()
    def mask_key(k):
        return k[:8]+"********" if len(k)>8 else "***"
    out_lines = [
        "🎉 Step3 全部配置完成，已保存。",
        f"生成端：{gen_base}",
        f"生成Key：{mask_key(gen_key)}",
        f"生成模型：{','.join(state['selected_gen'])}",
        f"裁判端：{judge_base}",
        f"裁判Key：{mask_key(judge_key)}",
        f"裁判模型：{sel[0]}",
        "",
        "可用命令：/models  /stats /health /quotes /debug",
        "反馈模式：第一行发 ture 触发",
        "",
        "══════════════════════════════════════",
        "⚠️ 重要：请【新建一个对话】再开始游玩",
        "══════════════════════════════════════",
        "原因：当前对话里含有向导交互的配置信息",
        "（URL / Key / 编号 / SKIP），如果直接继续聊天，",
        "这些内容会被当作剧情上下文发给 AI 模型，",
        "导致模型困惑、复述你的配置、或者拒绝回答。",
        "",
        "操作：在 RK 里长按当前对话 → 删除或新建，",
        "然后在【新对话】里正常发消息即可。",
        "══════════════════════════════════════"
    ]
    new_state = dict()
    new_state["step"] = "done"
    return "\n".join(out_lines), new_state

def handle_setup_wizard(user_input: str, push_cot, final_reply) -> bool:
    """
    single-user fixed session key "default"; return True=wizard handled, stop main flow
    NOTE: only single-local-user design; multi-LAN clients will overwrite wizard state.
    """
    with SETUP_LOCK:
        state = RT.wizard_state.get("default", {"step":"init"})
    if is_setup_finished():
        return False
    stripped = user_input.strip()
    if stripped == "/resetconfig":
        with SETUP_LOCK:
            clear_setup_state()
            RT.judge_model_resolved = None
            RT.judge_fallback_resolved = None
            RT.wizard_state.pop("default", None)
        final_reply("🔁 全部会话与配置已清空，重新开始初始化。")
        return True
    step = state.get("step","init")
    if step == "init":
        reply, new_state = wizard_step1_init(user_input, state)
    elif step == "choose_gen":
        reply, new_state = wizard_step2_choose_gen(user_input, state)
    elif step == "choose_judge":
        reply, new_state = wizard_step3_choose_judge(user_input, state)
    else:
        reply, new_state = "内部状态异常，请发送 /resetconfig 重试。", {"step":"init"}
    with SETUP_LOCK:
        if new_state.get("step") == "done":
            RT.wizard_state.pop("default", None)
        else:
            RT.wizard_state["default"] = new_state
    final_reply(reply)
    return True
