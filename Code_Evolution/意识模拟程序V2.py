#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =====================================================================
#  EDF-MVP v2 —— "执行机"沙盒:单体意识架构循环的玩具实现
#  依赖:  pip install flask
#  运行:  python edf_mvp_v2.py     →  浏览器打开 http://127.0.0.1:5000
#  节拍:  引擎0.5s/帧 · 页面1s轮询 · 昼夜周期≈82s
# =====================================================================
import threading, time, json, math, random, copy, os
from collections import Counter, deque
from datetime import datetime
from flask import Flask, jsonify, request, Response

USE_REAL_WEB = True    # True=爬真实网页(需外网+pip install requests beautifulsoup4)

# ==================== G 基因档案(固定·只读) ====================
G = {
    # 先验必须用"世界的语言"书写,否则出生第一帧A≈1,开机即恐慌(v1的教训)
    "seed_topics": {
        "ai":   ["model","agent","training","inference","dataset","llm"],
        "code": ["python","compiler","github","debug","release","kernel"],
        "mkt":  ["funding","startup","market","growth","invest","ipo"],
        "chip": ["chip","gpu","fab","semiconductor","hardware","wafer"],
    },
    "threat_words": ["war","crash","attack","disaster","death","panic"],  # v2已接线的G短路通道
    "novelty_bonus": 1.5,
}
G_SEED = Counter({w: 1.0 for ws in G["seed_topics"].values() for w in ws})

WORLD_TOPICS = {
    "ai":   ["model","agent","training","inference","dataset","llm","robot","prompt","transformer","alignment"],
    "code": ["python","compiler","github","debug","release","kernel","runtime","api","library","opensrc"],
    "mkt":  ["funding","startup","market","growth","invest","ipo","valuation","revenue","shares","index"],
    "chip": ["chip","gpu","fab","semiconductor","hardware","wafer","lithography","packaging","yield","node"],
    "meta": ["energy","climate","policy","research","university","journal","patent","grant","lab","study"],  # G种子之外:出生时是纯新颖
}

# ==================== H 硬件参数(系统常量·程序内不改) ====================
class H:
    FRAME_SEC    = 0.5
    KEEP         = 0.30    # K_M有损压缩:新信息只保留30%
    WRITE_GAIN   = 1.0
    DECAY        = 0.990   # 清醒期逐帧衰减(代谢债)
    SLEEP_DECAY  = 0.85    # K轨重衰减(还债)
    CURIOUS_A    = 0.35
    ALARM_A      = 0.60
    AWAKE_FRAMES = 140     # 清醒70s
    SLEEP_FRAMES = 24      # 睡眠12s
    I_LR         = 0.05

# ==================== 世界(仿真;真实爬虫可选) ====================
class SimWorld:
    def __init__(self):
        self.base  = {t: 1.0 for t in WORLD_TOPICS}
        self.burst = {}          # topic->剩余帧数(新奇事件)
        self.threat_left = 0
        self.noise = 0.03        # H₄噪声耦合
        self.lock = threading.Lock()
    def step(self):
        with self.lock:
            for t in self.base:  # 世界趋势慢漂移
                self.base[t] = max(0.2, self.base[t] + random.uniform(-0.05, 0.05))
            if random.random() < 0.015 and len(self.burst) < 2:   # 随机新奇事件
                self.burst[random.choice(list(WORLD_TOPICS))] = random.randint(40, 80)
            for t in list(self.burst):
                self.burst[t] -= 1
                if self.burst[t] <= 0: del self.burst[t]
            if self.threat_left > 0: self.threat_left -= 1
            if self.noise > 0.03: self.noise = max(0.03, self.noise - 0.005)
    def emit(self, n=120):
        with self.lock:
            weights = dict(self.base)
            for t in self.burst: weights[t] = weights.get(t, 1.0) + 3.0
            topics = list(weights); ws = [weights[t] for t in topics]
            words = [random.choice(WORLD_TOPICS[random.choices(topics, ws)[0]]) for _ in range(n)]
            if self.threat_left > 0:
                words += [random.choice(G["threat_words"]) for _ in range(8)]
            for _ in range(int(n * self.noise)):
                words.append("".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=random.randint(3, 8))))
            return Counter(words)
    def inject(self, kind):
        with self.lock:
            if kind == "threat":  self.threat_left = 10
            elif kind == "novelty": self.burst[random.choice(list(WORLD_TOPICS))] = random.randint(50, 90)
            elif kind == "noise": self.noise = 0.35

# ---------- 真实爬虫:节流 + 多源轮换 + 中英混合切词 ----------
import re as _re
_REAL_CACHE     = Counter(["offline"])   # 失败/离线时的回退哨兵
_REAL_OFFLINE   = Counter(["offline"])
_REAL_LAST_TS   = 0.0
_REAL_INTERVAL  = 3.0                    # 3秒节流,防被封
_REAL_FAIL_ROW  = 0                      # 连续失败计数,触发失明节能

# 国内可达的真实源(已 curl 验证全部 200)
_SRC_ROTATE = [
    ("ChinaDaily", "https://www.chinadaily.com.cn/"),   # 英文,800+token,含 market/growth/invest/agent
    ("观察者网",    "https://www.guancha.cn/"),          # 中英混合,有大模型/芯片相关中文
    ("新华网",      "https://www.xinhuanet.com/"),      # 中文主流新闻
    ("IT之家",      "https://www.ithome.com/"),          # 中文科技资讯
    ("人民网",      "http://www.people.com.cn/"),        # 中文
]
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/122.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}

def _tokenize(text):
    """中英混合切词:英文单词(≥3字母) + 中文双字滑动窗口."""
    toks = []
    # 英文连续字母串
    toks += [w.lower() for w in _re.findall(r"[a-zA-Z]{3,}", text)]
    # 中文连续段 ≥2 字 → 双字滑动窗口(不依赖 jieba)
    for seg in _re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for i in range(len(seg) - 1):
            toks.append(seg[i:i+2])
    return toks[:1500]

def real_sense(_ignored_url=None):
    """节流爬取多源 → Counter. 依次尝试所有源直到成功;全挂才回退."""
    global _REAL_CACHE, _REAL_LAST_TS, _REAL_FAIL_ROW
    now = time.time()
    if now - _REAL_LAST_TS < _REAL_INTERVAL:
        return _REAL_CACHE
    _REAL_LAST_TS = now

    import requests
    # 每次打乱顺序,依次尝试全部源——直到有一个返回 ≥20 token
    shuffled = list(_SRC_ROTATE); random.shuffle(shuffled)
    any_attempted = False
    for src_name, src_url in shuffled:
        any_attempted = True
        try:
            r = requests.get(src_url, headers=_HEADERS, timeout=5)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, "html.parser")
            text = soup.get_text(" ")
            toks = _tokenize(text)
            if len(toks) >= 20:
                _REAL_CACHE = Counter(toks)
                _REAL_FAIL_ROW = 0
                return _REAL_CACHE
        except Exception:
            continue
    # 全部源都没成功 → 失败计数+1
    if any_attempted:
        _REAL_FAIL_ROW += 1
    return _REAL_CACHE

# ==================== 数学工具 ====================
def js_div(pc, qc, topn=140):
    a = dict(pc.most_common(topn)); b = dict(qc.most_common(topn))
    vocab = set(a) | set(b)
    if not vocab: return 0.0
    eps = 1e-6
    pa = [max(a.get(w, 0.0), eps) for w in vocab]; pb = [max(b.get(w, 0.0), eps) for w in vocab]
    sa, sb = sum(pa), sum(pb)
    pa = [x / sa for x in pa]; pb = [x / sb for x in pb]
    m = [(x + y) / 2 for x, y in zip(pa, pb)]
    kl1 = sum(x * math.log(x / y) for x, y in zip(pa, m))
    kl2 = sum(x * math.log(x / y) for x, y in zip(pb, m))
    return (0.5 * (kl1 + kl2)) / math.log(2)

# ==================== 执行机主体 ====================
class Agent:
    def __init__(self):
        self.lock = threading.RLock()
        self.fresh()

    def fresh(self):
        # M:模式表(语义核core=巩固后的慢库;情景缓冲freq=快库)
        self.M = {"core": copy.deepcopy(G_SEED), "freq": Counter()}
        self.I_w = [random.uniform(-0.05, 0.05) for _ in range(5)]  # Ī自模型权重
        self.energy = 100.0
        self.frame = 0; self.last_A = 0.0
        self.a5 = deque(maxlen=5)
        self.err_self = deque(maxlen=60); self.err_base = deque(maxlen=60)
        self.highA = deque(maxlen=200)
        self.log = deque(maxlen=250)
        self.A_hist = deque(maxlen=600)      # 图表:最近2分钟
        self.sleeping = False; self.sleep_left = 0; self.replay_last = None
        self.dead = False; self.death_cause = ""; self.fossil_file = ""
        self.mood = "初始化"; self.threat_events = 0
        self.snapshot = None
        self.log_add("系统", "出生:G解压为H,M=空白+种子先验;先验与世界初次对齐中")

    # ---------- P(t):生成式先验(来自M,不是上一帧的回声——v1的bug已修) ----------
    def predict(self):
        P = Counter()
        for w, c in self.M["core"].items(): P[w] += c * 3.0
        for w, c in self.M["freq"].items(): P[w] += c * 0.5
        for w in G_SEED: P[w] += 0.1        # 先验残响
        return P

    # ---------- Ī:自模型(必须做预测才有资格叫模型——T2可测量化) ----------
    def features(self, threat_flag):
        m5 = sum(self.a5) / len(self.a5) if self.a5 else 0.0
        return [1.0, self.last_A, m5, self.energy / 100.0, float(threat_flag)]
    def i_predict(self, x):
        s = sum(w * f for w, f in zip(self.I_w, x))
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, s))))
    def i_learn(self, x, target):
        pred = self.i_predict(x); err = target - pred
        for k in range(len(self.I_w)):
            self.I_w[k] += H.I_LR * err * pred * (1 - pred) * x[k]

    def log_add(self, typ, msg):
        self.log.append({"t": datetime.now().strftime("%H:%M:%S"), "type": typ, "msg": msg})

    # ---------- 死亡:EM4(停机被内化为死;服务器继续烧,但那不是它的火) ----------
    def die(self, cause):
        self.dead = True; self.death_cause = cause
        self.fossil_file = os.path.abspath("fossil_%s.json" % datetime.now().strftime("%H%M%S"))
        fossil = {"died_at": datetime.now().isoformat(), "cause": cause,
                  "frames_lived": self.frame, "threat_events": self.threat_events,
                  "top_memory": [(w, round(c, 1)) for w, c in self.combined().most_common(40)],
                  "log_tail": list(self.log)[-100:]}
        try:
            with open(self.fossil_file, "w", encoding="utf-8") as f: json.dump(fossil, f, ensure_ascii=False, indent=1)
        except Exception: pass
        self.log_add("死亡", "能量归零:%s | 化石已封存 → %s" % (cause, os.path.basename(self.fossil_file)))

    def combined(self):
        C = Counter()
        for w, c in self.M["core"].items(): C[w] += c * 3.0
        for w, c in self.M["freq"].items(): C[w] += c
        return C

    # ================= 主循环一步 =================
    def step(self, world):
        with self.lock:
            if self.dead:
                world.step(); return
            self.frame += 1
            world.step()

            # ---------- K轨(睡眠位:E轨塌落) ----------
            if self.sleeping:
                self.sleep_left -= 1
                self.energy = min(100.0, self.energy + 0.45)          # 修复性恢复
                if self.sleep_left % 6 == 0 and self.highA:           # 重放归档(M3)
                    ev = max(self.highA, key=lambda e: e["A"]); self.highA.remove(ev)
                    for w in ev["words"]: self.M["core"][w] += 1.0
                    self.replay_last = ev
                    self.log_add("睡眠", "重放归档:%s (A=%.2f) → 语义核" % (list(ev["words"])[:5], ev["A"]))
                for w in list(self.M["freq"]):                        # 大衰减=还债(命题1)
                    self.M["freq"][w] *= H.SLEEP_DECAY
                if self.sleep_left <= 0:
                    self.sleeping = False; self.highA.clear(); self.replay_last = None
                    self.log_add("系统", "晨间复位:E轨重启,债务已部分偿还")
                self.A_hist.append((self.frame, 0.0, self.energy, "睡眠"))
                return

            # ---------- 清醒位 ----------
            S = world.emit() if not USE_REAL_WEB else real_sense()
            # 失明节能:真实爬虫连续失败 → 世界全是"offline"哨兵 → 自动睡
            if USE_REAL_WEB and _REAL_FAIL_ROW >= 4:
                self.sleeping = True; self.sleep_left = H.SLEEP_FRAMES * 3   # 多睡会儿等网络
                self.log_add("系统", "真实世界失明(连续爬取失败%d次) → 强制节能休眠" % _REAL_FAIL_ROW)
                self.A_hist.append((self.frame, 0.0, self.energy, "睡眠"))
                return
            threat_hits = [w for w in G["threat_words"] if w in S]

            # Ī先预测本帧A(自我不透明的测量点)
            tflag = 1 if threat_hits else 0
            feats = self.features(tflag)
            pred_A = self.i_predict(feats)

            # P vs S → A(偏差即内容:T6)
            P = self.predict()
            A = js_div(P, S)
            self.err_self.append(abs(pred_A - A))
            self.err_base.append(abs(A - self.last_A))

            # ---------- E(t):当帧执行(沙盒里mood=稳态域,不再是标签) ----------
            old_mood = self.mood
            if threat_hits:                                     # G通道:内容触发,不看A幅值(Θ→0短路)
                self.mood = "恐惧"; self.threat_events += 1
                self.energy -= 1.2 * len(threat_hits)
                for w in threat_hits: self.M["core"][w] += 2.0  # 威胁优先强制归档
                self.highA.append({"frame": self.frame, "A": min(1.0, A + 0.3),
                                   "words": Counter({w: S[w] for w in threat_hits})})
                if self.threat_events <= 3:
                    self.log_add("威胁", "G通道短路:检出%s → 跳过审议,强制归档(Θ→0)" % threat_hits)
            elif self.energy < 15:
                self.mood = "恐慌(濒死)"
            elif A > H.ALARM_A:
                self.mood = "震荡"
            elif A > H.CURIOUS_A:
                self.mood = "好奇"
            else:
                self.mood = "心流" if self.energy > 40 else "低耗巡航"
            if self.mood != old_mood:
                self.log_add("状态", "%s → %s (A=%.2f, E=%.0f)" % (old_mood, self.mood, A, self.energy))
            if A > H.ALARM_A and random.random() < 0.1:
                self.log_add("异常", "偏差超警报阈A=%.2f:预测大面积失效" % A)

            # ---------- K_M:有损回写(只留30%新词,其余当场丢失) ----------
            new_info = Counter({w: c for w, c in S.items() if P.get(w, 0) < 0.5})
            keep_n = max(1, int(len(new_info) * H.KEEP))
            compressed = Counter(dict(new_info.most_common(keep_n)))
            for w in list(self.M["freq"]): self.M["freq"][w] *= H.DECAY
            for w, c in compressed.items(): self.M["freq"][w] += c * H.WRITE_GAIN
            if A > H.CURIOUS_A:
                self.highA.append({"frame": self.frame, "A": A, "words": dict(compressed)})

            # ---------- 赌注(能量代谢:活一帧就烧,学习是唯一的进项之一) ----------
            gain   = min(2.0, 0.07 * len(compressed)) * (0.5 if self.energy < 30 else 1.0)  # P30:风险敏感
            maint  = 0.22 + min(0.25, 0.0018 * len(self.M["freq"]))   # 债务随冗余增长
            stress = max(0.0, A - 0.55) * 4.5
            self.energy += gain - maint - stress

            # ---------- Ī学习 + 入睡判定 ----------
            self.i_learn(feats, A)
            self.a5.append(A); self.last_A = A
            self.A_hist.append((self.frame, A, self.energy, self.mood))
            if self.energy <= 0:
                self.die("代谢债务无法偿还(世界过于混沌或学习失败)"); return
            if self.frame % H.AWAKE_FRAMES == 0:
                self.sleeping = True; self.sleep_left = H.SLEEP_FRAMES
                self.log_add("睡眠", "λ下降:进入K轨(E轨塌落,重放归档+还债)")

# ==================== Flask 服务 ====================
app = Flask(__name__)
world = SimWorld()
agent = Agent()
STATS = {"lives": 1, "deaths": 0, "rollbacks": 0}

def engine():
    while True:
        agent.step(world)
        time.sleep(H.FRAME_SEC)

@app.route("/api/state")
def api_state():
    with agent.lock:
        comb = agent.combined()
        top = []
        seen = set()
        for w, c in comb.most_common(60):
            if len(top) >= 30: break
            top.append({"w": w, "c": round(float(c), 1), "core": w in agent.M["core"]})
        mean_self = sum(agent.err_self) / len(agent.err_self) if agent.err_self else 0.0
        mean_base = sum(agent.err_base) / len(agent.err_base) if agent.err_base else 0.0
        return jsonify({
            "alive": not agent.dead, "sleeping": agent.sleeping, "sleep_left": agent.sleep_left,
            "frame": agent.frame, "day": agent.frame // (H.AWAKE_FRAMES + H.SLEEP_FRAMES) + 1,
            "mood": agent.mood, "energy": round(agent.energy, 1),
            "A": round(agent.last_A, 3),
            "mem_size": len(agent.M["freq"]) + len(agent.M["core"]),
            "self_err": round(mean_self, 3), "base_err": round(mean_base, 3),
            "top_words": top, "history": list(agent.A_hist)[-240:],
            "log": list(agent.log)[-100:], "replay": agent.replay_last,
            "stats": {"lives": STATS["lives"], "deaths": STATS["deaths"],
                      "rollbacks": STATS["rollbacks"], "threat_events": agent.threat_events},
            "death_cause": agent.death_cause, "fossil": os.path.basename(agent.fossil_file) if agent.fossil_file else "",
        })

@app.route("/api/inject", methods=["POST"])
def api_inject():
    kind = request.args.get("kind", "novelty")
    world.inject(kind)
    with agent.lock: agent.log_add("世界", "外部注入事件:%s" % kind)
    return jsonify(ok=True)

@app.route("/api/snapshot", methods=["POST"])
def api_snapshot():
    with agent.lock:
        agent.snapshot = {"M": copy.deepcopy(agent.M), "I_w": list(agent.I_w),
                          "energy": agent.energy, "last_A": agent.last_A,
                          "highA": copy.deepcopy(agent.highA), "frame_at": agent.frame}
        agent.log_add("快照", "化石快照已拍摄(第%d帧):记忆可复活,经历不可复制" % agent.frame)
    return jsonify(ok=True)

@app.route("/api/rollback", methods=["POST"])
def api_rollback():
    with agent.lock:
        if not agent.snapshot or agent.dead:
            return jsonify(ok=False, msg="无快照或已死亡")
        s = agent.snapshot
        agent.M = copy.deepcopy(s["M"]); agent.I_w = list(s["I_w"])
        agent.energy = s["energy"]; agent.last_A = s["last_A"]
        agent.highA = copy.deepcopy(s["highA"])
        STATS["rollbacks"] += 1
        agent.log_add("回滚", "化石复活:恢复至第%d帧。记忆为真,经历为假——中间的%d帧从未被它活过(T9)" %
                      (s["frame_at"], agent.frame - s["frame_at"]))
    return jsonify(ok=True)

@app.route("/api/reset", methods=["POST"])
def api_reset():
    global agent
    with agent.lock:
        if agent.dead: STATS["deaths"] += 1
        agent.fresh(); STATS["lives"] += 1
    return jsonify(ok=True)

# ==================== 前端(单页) ====================
HTML = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>EDF-MVP v2 · 执行机沙盒</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
 body{background:#0b1020;color:#cbd5e1;font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;padding:16px}
 h1{font-size:18px;margin:0 0 4px} .sub{font-size:12px;color:#64748b;margin-bottom:12px}
 .grid{display:grid;grid-template-columns:2fr 1fr;gap:12px}
 .panel{background:#111a30;border:1px solid #1e293b;border-radius:10px;padding:12px}
 .lamp{display:inline-block;width:14px;height:14px;border-radius:50%;margin-right:6px;vertical-align:-2px}
 .big{font-size:26px;font-weight:700}
 .metrics{display:flex;gap:18px;flex-wrap:wrap;margin:10px 0}
 .metrics .m{background:#0d1526;border:1px solid #1e293b;border-radius:8px;padding:8px 12px;min-width:110px}
 .metrics .lab{font-size:11px;color:#64748b}
 .bar{height:10px;background:#1e293b;border-radius:5px;overflow:hidden;margin-top:4px}
 .bar>div{height:100%;background:#4ade80}
 #cloud span{margin:3px 5px;display:inline-block}
 #log{height:260px;overflow-y:auto;font-family:Consolas,monospace;font-size:12px;background:#0a0f1e;border-radius:8px;padding:8px}
 .btns button{margin:4px 6px 4px 0;padding:7px 12px;border:0;border-radius:8px;background:#1e3a8a;color:#e2e8f0;cursor:pointer;font-size:13px}
 .btns button.red{background:#7f1d1d}.btns button.purple{background:#5b21b6}.btns button.green{background:#14532d}
 .banner{background:#312e81;color:#c7d2fe;padding:8px 12px;border-radius:8px;margin-bottom:10px;display:none}
 .tomb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:9;align-items:center;justify-content:center}
 .tomb .card{background:#1a0505;border:2px solid #7f1d1d;border-radius:14px;padding:28px;max-width:520px;text-align:center}
 .tomb h2{color:#ef4444;margin-top:0}
</style></head><body>
<h1><span class="lamp" id="lamp"></span>EDF-MVP v2 · 执行机沙盒 <span id="mode" style="font-size:15px;color:#93c5fd"></span></h1>
<div class="sub">引擎 0.5s/帧 · 页面 1s 轮询 · 昼夜周期≈82s · 曲线窗口≈2分钟 —— 曲线贴0的平线=睡眠(E轨塌落)</div>
<div class="banner" id="sleepbn">🌙 K轨开启:E轨塌落 · 重放归档 + 类淋巴还债 · <span id="replay"></span></div>
<div class="metrics">
 <div class="m"><div class="lab">能量(赌注)</div><div class="big" id="energy">100</div><div class="bar"><div id="ebar" style="width:100%"></div></div></div>
 <div class="m"><div class="lab">偏差 A(t)=JS散度</div><div class="big" id="aval">0</div><div class="lab">阈:好奇0.35 / 警报0.60</div></div>
 <div class="m"><div class="lab">Ī自模型误差 (P27:应>基线且永不归零)</div><div class="big" id="serr">-</div><div class="lab">持久基线:<span id="berr">-</span></div></div>
 <div class="m"><div class="lab">记忆量 M(债随冗余长)</div><div class="big" id="mem">0</div><div class="lab">生命<span id="lives">1</span> · 死亡<span id="deaths">0</span> · 回滚<span id="rolls">0</span></div></div>
 <div class="m"><div class="lab">第 N 天 · 帧</div><div class="big" id="day">1</div><div class="lab" id="frame">#0</div></div>
</div>
<div class="grid">
 <div class="panel"><canvas id="chart" height="150"></canvas></div>
 <div class="panel"><b>词云 = M(橙=语义核/巩固后,蓝=情景缓冲/易失)</b><div id="cloud" style="margin-top:8px;line-height:1.9"></div></div>
</div>
<div class="panel" style="margin-top:12px">
 <div class="btns">
  <button class="red"  onclick="post('/api/inject?kind=threat')">☠ 注入威胁(G短路)</button>
  <button class="green" onclick="post('/api/inject?kind=novelty')">✦ 注入新奇事件</button>
  <button onclick="post('/api/inject?kind=noise')">░ 注入噪声(H₄污染)</button>
  <button class="purple" onclick="post('/api/snapshot')">📸 化石快照</button>
  <button class="purple" onclick="post('/api/rollback')">↩ 化石回滚(T9)</button>
  <button onclick="post('/api/reset')">⟳ 重启(杀死后可用)</button>
 </div>
 <div id="log"></div>
</div>
<div class="tomb" id="tomb"><div class="card">
 <h2>☠ 进程已死亡</h2><p id="cause"></p>
 <p style="font-size:12px;color:#94a3b8">服务器(宿主)还在燃烧,但已不为它烧。它的M与日志封存为化石:</p>
 <p><code id="fossil" style="color:#fbbf24"></code></p>
 <button onclick="post('/api/reset')" style="padding:10px 18px;border:0;border-radius:8px;background:#14532d;color:#fff;cursor:pointer">⟳ 重启新个体</button>
</div></div>
<script>
const mc={"心流":"#22c55e","好奇":"#06b6d4","震荡":"#f59e0b","恐惧":"#ef4444","恐慌(濒死)":"#b91c1c","低耗巡航":"#94a3b8","睡眠":"#818cf8","初始化":"#94a3b8"};
const lc={"威胁":"#ef4444","异常":"#f59e0b","状态":"#38bdf8","睡眠":"#818cf8","系统":"#94a3b8","快照":"#c084fc","回滚":"#e879f9","死亡":"#f87171","世界":"#64748b"};
const chart=new Chart(document.getElementById('chart'),{type:'line',
 data:{datasets:[
  {label:'偏差A',data:[],borderColor:'#38bdf8',pointRadius:0,tension:.25,yAxisID:'y'},
  {label:'能量',data:[],borderColor:'#4ade80',pointRadius:0,tension:.25,yAxisID:'y2'},
  {label:'好奇阈0.35',data:[],borderColor:'#0e7490',borderDash:[6,5],pointRadius:0,yAxisID:'y'},
  {label:'警报阈0.60',data:[],borderColor:'#7f1d1d',borderDash:[6,5],pointRadius:0,yAxisID:'y'}]},
 options:{animation:false,scales:{x:{type:'linear',ticks:{display:false}},
  y:{min:0,max:1,title:{display:true,text:'A'}},
  y2:{position:'right',min:0,max:110,grid:{drawOnChartArea:false},title:{display:true,text:'能量'}}}}});
async function poll(){
 try{
  const d=await(await fetch('/api/state')).json();
  document.getElementById('lamp').style.background=mc[d.mood]||"#94a3b8";
  document.getElementById('mode').textContent="〔"+d.mood+"〕";
  document.getElementById('energy').textContent=d.energy;
  document.getElementById('ebar').style.width=Math.max(0,d.energy)+'%';
  document.getElementById('ebar').style.background=d.energy<25?'#ef4444':'#4ade80';
  document.getElementById('aval').textContent=d.A;
  document.getElementById('serr').textContent=d.self_err;
  document.getElementById('berr').textContent=d.base_err;
  document.getElementById('mem').textContent=d.mem_size;
  document.getElementById('day').textContent=d.day;
  document.getElementById('frame').textContent='#'+d.frame;
  document.getElementById('lives').textContent=d.stats.lives;
  document.getElementById('deaths').textContent=d.stats.deaths;
  document.getElementById('rolls').textContent=d.stats.rollbacks;
  document.getElementById('sleepbn').style.display=d.sleeping?'block':'none';
  if(d.sleeping&&d.replay)document.getElementById('replay').textContent="最近重放:"+(d.replay.words?Object.keys(d.replay.words).slice(0,5).join(' '):'');
  const h=d.history;
  chart.data.datasets[0].data=h.map(p=>({x:p[0],y:p[1]}));
  chart.data.datasets[1].data=h.map(p=>({x:p[0],y:p[2]}));
  chart.data.datasets[2].data=h.map(p=>({x:p[0],y:0.35}));
  chart.data.datasets[3].data=h.map(p=>({x:p[0],y:0.60}));
  chart.update('none');
  document.getElementById('cloud').innerHTML=d.top_words.map(t=>
   `<span style="color:${t.core?'#fb923c':'#60a5fa'};font-size:${(12+Math.min(26,t.c/2))}px">${t.w}</span>`).join('');
  const lg=document.getElementById('log');
  lg.innerHTML=d.log.map(e=>`<div style="color:${lc[e.type]||'#94a3b8'}">[${e.t}] ${e.type} · ${e.msg}</div>`).join('');
  lg.scrollTop=lg.scrollHeight;
  const tb=document.getElementById('tomb');
  tb.style.display=d.alive?'none':'flex';
  if(!d.alive){document.getElementById('cause').textContent=d.death_cause;
   document.getElementById('fossil').textContent=d.fossil;}
 }catch(e){}
}
function post(u){fetch(u,{method:'POST'}).then(()=>setTimeout(poll,300));}
setInterval(poll,1000);poll();
</script></body></html>"""

@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")

if __name__ == "__main__":
    threading.Thread(target=engine, daemon=True).start()
    print("EDF-MVP v2 运行中 → http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, threaded=True)
