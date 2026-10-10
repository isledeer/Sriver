#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ===================== Runtime 容器（B-1 空壳） =====================
# 目的：把散落各处的全局状态收进一个对象，逐步消灭 global 声明。
# B-1 阶段只放容器，不做任何搬迁；老全局变量保持不变，老代码照常运行。
# B-2 阶段一个变量一个变量地把"模块级全局 X"替换成"RT.X"。
class Runtime:
    """Sriver 运行时状态容器。"""
    def __init__(self):
        # 配置（将来从 RT.config 搬过来）
        self.config = {}
        self.config_warnings = []
        # 模型监控（将来从 RT.monitor 搬过来）
        self.monitor = {}
        # 裁判解析缓存
        self.judge_model_resolved = None
        self.judge_fallback_resolved = None
        # 运行时覆盖（/only /debug 等）
        self.runtime_overrides = {}
        # 模型列表缓存
        self.model_cache = {}
        self.model_cache_ts = {}
        # 向导状态
        self.wizard_state = {}
        # 反馈（ture 触发）
        self.model_feedback = {}
        # 日志文件句柄
        self.log_file = None
        self.log_file_date = None
        # 一次性提示标志
        self.ture_hint_shown = False

RT = Runtime()
