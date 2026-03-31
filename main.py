import json
import os
import time
import html
from datetime import datetime
import asyncio
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

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
    </div>
    <div class="footer">注：测试结果仅供参考 · Powered By Astrbot</div>
</body>
</html>
"""

@register("astrbot_plugin_political_test", "MasaIodine", "8values 政治倾向测试", "1.0.1")
class PoliticalValuePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.questions = self._load_json("data", "test.json")
        self.ideologies = self._load_json("data", "idea.json")
        
        self.max_scores = {"econ": 0, "dipl": 0, "govt": 0, "scty": 0}
        for q in self.questions:
            for axis, val in q.get("effect", {}).items():
                self.max_scores[axis] += abs(val)
        
        self.user_sessions = {}
        self.TIMEOUT_SECONDS = 20 * 60
        self.cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())

    def _load_json(self, *path_parts):
        path = os.path.join(os.path.dirname(__file__), *path_parts)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载数据失败 {path}: {e}")
            return []

    async def _cleanup_expired_sessions(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired_users = [uid for uid, s in self.user_sessions.items() 
                             if now - s.get("last_active", 0) > self.TIMEOUT_SECONDS]
            for uid in expired_users:
                self.user_sessions.pop(uid, None)

    @filter.command("valueteststart")
    async def start_test(self, event: AstrMessageEvent):
        if not self.questions:
            yield event.plain_result("错误：题库文件加载失败，请检查插件安装。")
            return
            
        user_id = str(event.get_sender_id())
        user_name = html.escape(event.get_sender_name())
        
        self.user_sessions[user_id] = {
            "index": 0,
            "scores": {"econ": 0, "dipl": 0, "govt": 0, "scty": 0},
            "last_active": time.time(),
            "userinfo": f"@{user_name} ({user_id})"
        }
        yield event.plain_result(f"8values 测试开始！共 {len(self.questions)} 题。\n第 1 题：\n{self.questions[0]['question']}\n\n请回复 [/valueans 数字]：\n1: 非常赞同 | 2: 赞同 | 3: 中立 | 4: 反对 | 5: 非常反对")

    @filter.command("valueans")
    async def answer_test(self, event: AstrMessageEvent, score_idx: int):
        user_id = str(event.get_sender_id())
        if user_id not in self.user_sessions:
            yield event.plain_result("请先发送 /valueteststart 开始测试")
            return

        if not (1 <= score_idx <= 5):
            yield event.plain_result("请输入 1-5 之间的数字。")
            return

        session = self.user_sessions[user_id]
        curr_q = self.questions[session["index"]]
        session["last_active"] = time.time()

        multiplier = {1: 1.0, 2: 0.5, 3: 0.0, 4: -0.5, 5: -1.0}[score_idx]
        for axis, val in curr_q["effect"].items():
            session["scores"][axis] += multiplier * val

        session["index"] += 1

        if session["index"] < len(self.questions):
            next_q = self.questions[session["index"]]
            yield event.plain_result(f"第 {session['index']+1}/{len(self.questions)} 题：\n{next_q['question']}")
        else:
            try:
                yield event.plain_result("测试结束，正在生成结果...")
                
                final_raw = session["scores"]
                normalized = {}
                for axis in ["econ", "dipl", "govt", "scty"]:
                    max_v = self.max_scores[axis]
                    normalized[axis] = ((final_raw[axis] + max_v) / (2 * max_v)) * 100 if max_v != 0 else 50
                
                best_match = self.find_closest_ideology(normalized)
                readable_time = datetime.fromtimestamp(session["last_active"]).strftime('%Y-%m-%d %H:%M')

                axes_data = [
                    {"left_label": "⚖️平等", "right_label": "市场💲", "left_score": normalized["econ"], "right_score": 100-normalized["econ"], "color_l": "color-econ-l", "color_r": "color-econ-r"},
                    {"left_label": "🚩民族", "right_label": "世界🌐", "left_score": 100-normalized["dipl"], "right_score": normalized["dipl"], "color_l": "color-dipl-l", "color_r": "color-dipl-r"},
                    {"left_label": "🗽自由", "right_label": "威权⚒️", "left_score": normalized["govt"], "right_score": 100-normalized["govt"], "color_l": "color-govt-l", "color_r": "color-govt-r"},
                    {"left_label": "⌛进步", "right_label": "传统✡️", "left_score": normalized["scty"], "right_score": 100-normalized["scty"], "color_l": "color-scty-l", "color_r": "color-scty-r"}
                ]

                render_data = {
                    "ideology_name": best_match["name"],
                    "description": best_match.get("desc", "无描述"),
                    "figures": best_match.get("figures", []),
                    "axes": axes_data,
                    "userinfo": session["userinfo"],
                    "time": readable_time
                }
                
                image_url = await self.html_render(HTML_TMPL, render_data, options={"clip": {"x": 0, "y": 0, "width": 1100, "height": 740}})
                yield event.image_result(image_url)

                try:
                    umo = event.unified_msg_origin
                    provider_id = await self.context.get_current_chat_provider_id(umo=umo)
                    persona = await self.context.persona_manager.get_default_persona_v3(umo=umo)
                    sys_prompt = persona.get("prompt", "") if isinstance(persona, dict) else getattr(persona, "prompt", "")
                    
                    llm_prompt = f"用户完成测试，匹配意识形态为：{best_match['name']}。请结合人设对此进行一段简短评价。"
                    llm_resp = await self.context.llm_generate(chat_provider_id=provider_id, system_prompt=sys_prompt, prompt=llm_prompt)
                    if llm_resp and llm_resp.completion_text:
                        yield event.plain_result(f"🤖 AI 评价：\n{llm_resp.completion_text}")
                except Exception as ai_err:
                    logger.error(f"AI Eval Error: {ai_err}")
                    yield event.plain_result("抱歉，生成ai评价时出错了。")

            except Exception as e:
                logger.error(f"渲染结果失败: {e}", exc_info=True)
                yield event.plain_result("抱歉，生成结果图时出错了。")
            finally:
                self.user_sessions.pop(user_id, None)

    def find_closest_ideology(self, scores):
        if not self.ideologies: return {"name": "未知", "stats": scores}
        min_dist = float('inf')
        closest = self.ideologies[0]
        for ideology in self.ideologies:
            dist = sum((scores[k] - ideology['stats'][k])**2 for k in ['econ', 'dipl', 'govt', 'scty'])**0.5
            if dist < min_dist:
                min_dist = dist
                closest = ideology
        return closest

    @filter.command("valuestop")
    async def valuestop(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        if self.user_sessions.pop(user_id, None):
            yield event.plain_result("测试已中止。")
        else:
            yield event.plain_result("你当前没有正在进行的测试。")