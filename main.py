import json
import os
import time
from datetime import datetime
import asyncio
import html
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# HTML 模板：用于生成最后的结果图
# 包含了 CSS 样式（左侧进度条，右侧意识形态描述和代表人物）
HTML_TMPL = """
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<style>
    body { width: 1000px; background: #f4f7f9; padding: 40px; font-family: sans-serif; }
    .main-container { display: flex; gap: 30px; background: white; padding: 40px; border-radius: 30px; box-shadow: 0 20px 50px rgba(0,0,0,0.1); }
    .left-panel { flex: 1.2; border-right: 1px solid #eee; padding-right: 30px; }
    .right-panel { flex: 1; padding-left: 10px; }
    .result-name { font-size: 54px; color: #1a73e8; font-weight: 900; margin-bottom: 20px; }
    .section-title { font-size: 18px; color: #666; font-weight: bold; margin: 25px 0 10px; border-left: 5px solid #1a73e8; padding-left: 10px; }
    .description { font-size: 16px; color: #444; line-height: 1.6; background: #f9f9f9; padding: 15px; border-radius: 12px; }
    .figure-tag { display: inline-block; background: #e1f5fe; color: #0288d1; padding: 6px 15px; border-radius: 20px; margin: 5px; font-size: 14px; font-weight: bold; }
    .axis-container { margin-bottom: 25px; }
    .axis-labels { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 16px; font-weight: bold; color: #444; }
    .bar-bg { height: 30px; background: #eee; border-radius: 15px; display: flex; overflow: hidden; }
    .bar-left, .bar-right { height: 100%; transition: width 0.5s; }
    .color-econ-l { background: #f44336; } .color-econ-r { background: #00897b; }
    .color-dipl-l { background: #ff9800; } .color-dipl-r { background: #03a9f4; }
    .color-govt-l { background: #ffeb3b; } .color-govt-r { background: #3f51b5; }
    .color-scty-l { background: #8bc34a; } .color-scty-r { background: #7b1fa2; }
    .footer { text-align: center; color: #999; font-size: 14px; margin-top: 30px; }
</style>
</head>
<body>
    <div class="main-container">
        <div class="left-panel">
            <div class="header">
                <p>8values 政治倾向测试报告</p>
                <div class="result-name">{{ ideology_name }}</div>
            </div>
            {% for axis in axes %}
            <div class="axis-container">
                <div class="axis-labels">
                    <span>{{ axis.left_label }} {{ axis.left_score|round(1) }}%</span>
                    <span>{{ axis.right_score|round(1) }}% {{ axis.right_label }}</span>
                </div>
                <div class="bar-bg">
                    <div class="bar-left {{ axis.color_l }}" style="width: {{ axis.left_score }}%"></div>
                    <div class="bar-right {{ axis.color_r }}" style="width: {{ axis.right_score }}%"></div>
                </div>
            </div>
            {% endfor %}
            <div class="footer">Powered By Astrbot ＆ 8values</div>
            <div class="footer">Author By MasaIodine</div>
        </div>
        <div class="right-panel">
            <div class="section-title">🕵️ 意识形态概述</div>
            <div class="description">{{ description }}</div>
            <div class="section-title">👤 代表人物</div>
            <div class="figures-box">
                {% for figure in figures %}
                <span class="figure-tag">{{ figure }}</span>
                {% endfor %}
            </div>
            <div class="section-title">🤔 受试者</div>
            <div class="description" style="font-size: 13px; color: #666;">{{ userinfo }}</div>
            <div class="section-title">🕛 测试时间</div>
            <div class="description" style="font-size: 13px; color: #666;">{{ time }}</div>
        </div>
    </div>
    <div class="footer">注：测试结果仅供参考</div>
</body>
</html>
"""

@register("astrbot_plugin_political_test",
           "MasaIodine", 
           "8values 政治倾向测试",
            "1.0.0")

class PoliticalValuePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

        # 保存引用，防止任务被意外回收
        self.cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())

        # 加载题目数据和意识形态定义数据
        self.questions = self._load_json("data", "test.json")
        self.ideologies = self._load_json("data", "idea.json")
        
        # 计算每个维度的最大可能得分，用于后续计算归一化
        self.max_scores = {"econ": 0, "dipl": 0, "govt": 0, "scty": 0}
        for q in self.questions:
            for axis, val in q["effect"].items():
                self.max_scores[axis] += abs(val)
        
        # 存储用户的测试进度 {user_id: session_data}
        self.user_sessions = {}

        # 会话超时时间：20分钟
        self.TIMEOUT_SECONDS = 20 * 60
        
        # 启动后台异步任务：清理超时不响应的用户
        asyncio.create_task(self._cleanup_expired_sessions())

    def _load_json(self, *path_parts):
        """跨平台安全加载 JSON"""
        path = os.path.join(os.path.dirname(__file__), *path_parts)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
            
        except FileNotFoundError:
            logger.error(f"错误：找不到文件 {path}")
            return []
        
        except json.JSONDecodeError:
            logger.error(f"错误：文件 {path} 格式有误")
            return []

    async def _cleanup_expired_sessions(self):
        """循环检查并清理超过 20 分钟未操作的用户会话"""
        while True:
            # 每分钟检查一次
            await asyncio.sleep(60)
            now = time.time()
            expired_users = []
            
            for user_id, session in self.user_sessions.items():
                if now - session.get("last_active", 0) > self.TIMEOUT_SECONDS:
                    expired_users.append(user_id)
            
            for user_id in expired_users:
                del self.user_sessions[user_id]

    @filter.command("valueteststart")
    async def start_test(self, event: AstrMessageEvent):
        """用户输入 /valueteststart 开始回答"""
        user_id = str(event.get_sender_id()) # 显式转字符串，防止拼接出错
        user_name = event.get_sender_name()
        safe_name = html.escape(user_name) # 过滤潜在的恶意payload
        userinfo = f"@{safe_name} ({user_id})"# 使用 f-string 避免拼接错误
        self.user_sessions[user_id] = {
            "index": 0,
            "scores": {"econ": 0, "dipl": 0, "govt": 0, "scty": 0},
            "last_active": time.time(),
            "userinfo": userinfo
        }

        yield event.plain_result(f"8values 测试开始！共 {len(self.questions)} 题。\n第 1 题：\n{self.questions[0]['question']}\n\n请回复 [valueans 数字]：\n1: 非常赞同\n2: 赞同\n3: 中立\n4: 反对\n5: 非常反对")

    @filter.command("valueans")
    async def answer_test(self, event: AstrMessageEvent, score_idx: int):
        """用户输入 /valueans [1-5] 开始回答"""
        try:
            user_id = event.get_sender_id()

            # 防止用户未开始先回答引发错误
            if user_id not in self.user_sessions:
                yield event.plain_result("请先发送 /valueteststart 以开始测试")
                return

            # 防止用户回答无效答案引发错误
            if not (1 <= score_idx <= 5):
                yield event.plain_result("请输入 1-5 之间的数字。")
                return

            session = self.user_sessions[user_id]
            curr_q = self.questions[session["index"]]
        
            session["last_active"] = time.time()

            # 根据选项转换分值倍率
            # 1:非常赞同(100%), 3:中立(0%), 5:非常反对(-100%)
            multiplier = {1: 1.0, 2: 0.5, 3: 0.0, 4: -0.5, 5: -1.0}[score_idx]

            # 累加各维度的分数
            for axis, val in curr_q["effect"].items():
                session["scores"][axis] += multiplier * val

            session["index"] += 1

            # 如果还有下一题，则继续
            if session["index"] < len(self.questions):
                next_q = self.questions[session["index"]]
                yield event.plain_result(f"第 {session['index']+1}/{len(self.questions)} 题：\n{next_q['question']}")
            else:
                # --- 所有题目完成，计算最终结果 ---
                final_raw = session["scores"]
                normalized = {}

                #强制 left_score 对应左边标签，right_score 对应右边标签，防止渲染错误
                for axis in ["econ", "dipl", "govt", "scty"]:
                    max_val = self.max_scores[axis]
                    # 计算百分比：(当前得分 + 最大偏移值) / (2 * 最大偏移值)
                    normalized[axis] = ((final_raw[axis] + max_val) / (2 * max_val)) * 100 if max_val != 0 else 50
            
                # 匹配最接近的意识形态
                best_match = self.find_closest_ideology(normalized)
                yield event.plain_result("测试结束，正在渲染结果图。。。")
            
                # 构建渲染 HTML 模版所需的数据

                # 获取测试时间戳
                readable_time = datetime.fromtimestamp(session["last_active"]).strftime('%Y年%m月%d日 %H:%M')
            
                # 构建意识形态轴
                axes_data = [
                {
                    "left_label": "⚖️平等", "right_label": "市场💲", 
                    "left_score": normalized["econ"], "right_score": 100 - normalized["econ"],
                    "color_l": "color-econ-l", "color_r": "color-econ-r"
                },
                {
                    "left_label": "🚩民族", "right_label": "世界🌐", 
                    "left_score": 100 - normalized["dipl"], "right_score": normalized["dipl"],
                    "color_l": "color-dipl-l", "color_r": "color-dipl-r"
                },
                {
                    "left_label": "🗽自由", "right_label": "威权⚒️", 
                    "left_score": normalized["govt"], "right_score": 100 - normalized["govt"],
                    "color_l": "color-govt-l", "color_r": "color-govt-r"
                },
                {
                    "left_label": "⌛进步", "right_label": "传统✡️", 
                    "left_score": normalized["scty"], "right_score": 100 - normalized["scty"],
                    "color_l": "color-scty-l", "color_r": "color-scty-r"
                }
                ]

                # 合并到render_data，使用get方法防止返回空值
                render_data = {
                "ideology_name": best_match["name"],
                "description": best_match.get("desc", "暂无意识形态简述"),
                "figures": best_match.get("figures", ["暂无代表人物"]),
                "axes": axes_data,
                "userinfo": session.get("userinfo", "获取用户信息失败"),
                "time": readable_time
                }
            
                # 截图输出配置
                options = {
                "type": "png",           
                "full_page": False,
                "omit_background": True,
                "clip": {"x": 0, "y": 0, "width": 1100, "height": 740}
                }
            
                # 调用 html_render 将 HTML 转为图片发送
                image_url = await self.html_render(HTML_TMPL, render_data, options=options)
                yield event.image_result(image_url)
            
                try:
                    # 获取对话 ID
                    umo = event.unified_msg_origin

                    # 获取当前模型 ID
                    provider_id = await self.context.get_current_chat_provider_id(umo=umo)

                    # 获取人设
                    persona_mgr = self.context.persona_manager
                    persona_obj = await persona_mgr.get_default_persona_v3(umo=umo)

                    # 提取系统提示词
                    sys_prompt = ""
                    if isinstance(persona_obj, dict):
                        sys_prompt = persona_obj.get("prompt", "")
                    else:
                        sys_prompt = getattr(persona_obj, "prompt", getattr(persona_obj, "system_prompt", ""))

                    # 构造提示词
                    llm_prompt = f"""
                    用户刚刚完成了一次 8values 政治倾向测试。
                    匹配意识形态：{best_match['name']}
                    各维度得分：
                    - 经济：{normalized['econ']:.1f}% 平等
                    - 外交：{100-normalized['dipl']:.1f}% 民族
                    - 政治：{normalized['govt']:.1f}% 自由
                    - 社会：{normalized['scty']:.1f}% 进步
                
                    请结合你的人设，对该结果进行一段简短的点评。直接输出评价，不要有废话。
                    """

                    # 调用大模型
                    llm_resp = await self.context.llm_generate(
                    chat_provider_id=provider_id, 
                    system_prompt=sys_prompt,
                    prompt=llm_prompt
                    )

                    # 输出 LLM 响应
                    if llm_resp and llm_resp.completion_text:
                        yield event.plain_result(f"🤖 AI 评价：")
                        yield event.plain_result(f"{llm_resp.completion_text}")
                    
                except Exception as e:
                    # 使用 logger 记录错误，以防止 LLM 服务短期不可用引发的排查困难
                    yield event.plain_result(f"响应失败，请在控制台查看详情")
                    logger.error(f"AI Evaluation Error: {e}")
        finally:
            # 无论成功还是中间崩溃，最后都确保删掉 session
            self.user_sessions.pop(user_id, None)
            
    @filter.command("valuestop")
    async def valuestop(self, event: AstrMessageEvent):
        """主动清空当前测试进度"""
        user_id = event.get_sender_id()

        if user_id in self.user_sessions:
            del self.user_sessions[user_id]
            yield event.plain_result("测试已中止，你的进度已清空。")
        else:
            # 防止用户未开始先结束引发错误
            yield event.plain_result("你当前并没有正在进行的测试。")
            
    def find_closest_ideology(self, scores):
        """使用欧几里得距离算法。在四维空间（econ, dipl, govt, scty）中，找到与用户得分距离最短的已知意识形态。"""
        min_distance = float('inf')
        closest_obj = None

        for ideology in self.ideologies:
            dist_sq = 0
            for key in ['econ', 'dipl', 'govt', 'scty']:
                # 计算每个维度差值的平方
                dist_sq += (scores[key] - ideology['stats'][key]) ** 2
            # 开方得到直线距离
            distance = dist_sq ** 0.5 
            if distance < min_distance:
                min_distance = distance
                closest_obj = ideology
                
        return closest_obj if closest_obj else self.ideologies[0]