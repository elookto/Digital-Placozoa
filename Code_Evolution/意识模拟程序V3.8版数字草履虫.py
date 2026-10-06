#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =====================================================================
#  EDF-MVP V3.8 —— 数字草履虫:觅食者 × 白日梦(清理版)
#
#  这是什么:一只生活在真实网页上的极简"数字草履虫"。它靠把新鲜意外
#  压缩进世界模型换取能量,挣不到就死;会睡觉(离线重放归档)、会做梦
#  (无聊点火→荣光检索→形成觅食目标)、死了留化石、化石可被后代继承。
#
#  理论对应(仅演示实现,不构成对理论的验证):
#    A1 区分  —— 全部"世界"是4096个词桶,区分不开的对它不存在
#    A2 动力学 —— 预测→偏差→压缩回写 的主循环是能量唯一来源
#    A3 压缩  —— 有损(哈希桶/EMA/容量上限)、不可逆(回滚=记忆在、经历无)、
#                 自指(它建模自己的惊讶)
#
#  运行: python 本文件.py → http://127.0.0.1:5000 (孪生自动分配端口+弹页)
#        python 本文件.py --regress   行为回归快照(改代码前后各跑一次,diff验证)
#  依赖: pip install flask requests beautifulsoup4
#  产出: fossil_*/snap_*/run_*.json ; 生态仅存内存,关机即散
#  伦理: 20s/帧 · 同域10s礼貌层 · 远征每30帧+1抓 · 尊重robots.txt ·
#        勿长期对准单一站点 · 公开发布前务必置 HONEST_UA=True
#
#  并发纪律(结构性防死锁,勿破坏):
#    任何线程任何时刻最多持有一把agent锁 —— /api/state 自身快照拿完就放,
#    孪生视图逐个短暂拿锁;网络抓取在锁外;礼貌层等待在锁外。
# =====================================================================
import threading, time, json, math, random, copy, os, re, zlib, sys, glob, socket, webbrowser
import urllib.robotparser
from collections import Counter, deque
from datetime import datetime
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup, Comment
from flask import Flask, jsonify, request, Response
from werkzeug.serving import make_server
sys.stdout.reconfigure(line_buffering=True)

USE_REAL_WEB = True
AUTO_OPEN = True
NB = 4096
BASE_SITES = [
    "https://news.sina.com.cn", "https://www.ithome.com/", "http://www.news.cn/",
    "https://top.baidu.com/board?tab=realtime",
    "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
    "https://www.thepaper.cn/", "http://www.people.com.cn/",
    "https://www.chinanews.com.cn/", "https://news.qq.com/",
    "https://finance.eastmoney.com/",
]
PORT_BASE, MAX_INSTANCES = 5000, 8

# ── 爬虫身份(发布伦理) ──
# 本机演示阶段默认沿用浏览器UA以保证站点兼容;一旦公开分发,必须置 True,
# 让它以诚实身份爬行(robots.txt检查始终开启,与此开关无关)。
HONEST_UA = False
BOT_UA = "EDF-DemoBot/0.1 (本地教学演示; 联系方式请替换为你自己的)"

class H:
    REAL = USE_REAL_WEB            # 防漏调apply_mode的保险丝
    FRAME_SEC, SLEEP_SEC = 20.0, 2.0
    AWAKE, SLEEPN = 40, 10
    MAINT, BLIND_MAINT, NIGHT_FIX = 0.35, 0.15, 0.45
    GAIN_CAP, STRESS_CAP = 0.8, 2.5
    SAT_LOW, SAT_FLOOR = 0.15, 0.45
    EXPLORE_COST = 0.25
    EXPEDITION_EVERY, EXPEDITION_COST = 30, 0.5
    PROBES, PROMOTE_BAR = 3, 0.12
    MAX_SITES, FRONTIER_CAP = 24, 400
    HOST_GAP, BLACKOUT = 10.0, 1800.0
    HUNGER_RELAX = 0.7
    SATE_GAIN, SATE_DECAY = 0.5, 0.982
    BORE_TH, BORE_HOLD = 0.75, 8
    HYBRID, GOAL_FADE = 0.7, 0.97
    HIT_GAIN, HIT_EMO = 0.4, 0.5
    DREAM_CD, FEAR_GATE = 30, 0.3
    PERSIST_MIND = False
    PAYOFF_INIT, EP_CAP, WORD_CAP = 0.5, 60, 4000
    GLORY_GAIN = 0.45
    SECOND_HAND = 0.5              # 同胞发现的食源,按此折扣计酬

def apply_mode(real):
    H.REAL = real
    H.FRAME_SEC, H.SLEEP_SEC = (20.0, 2.0) if real else (0.5, 0.25)
    H.AWAKE, H.SLEEPN = (40, 10) if real else (140, 24)
apply_mode(USE_REAL_WEB)

G_SEED = ["模型","算法","数据","开源","芯片","融资","市场","科技",
          "研究","智能","技术","创新","程序","产品","社区"]
EXTRA_SEEDS = ["经济","国际","健康","教育","文旅","汽车","体育","游戏","数码","城市","乡村","航天"]
THREATS = ["战争","崩盘","灾难","死亡","恐慌","爆炸","地震","疫情","袭击","坠毁"]
STOP = {"的一","一个","没有","我们","自己","他们","这个","可以","就是","不是","什么",
        "时候","现在","已经","如果","但是","还是","这些","出来","起来","以及","或者",
        "对于","通过","记者","报道","消息","时间","工作","进行","表示","相关","分钟"}

MOOD_BURN = {"心流":0.9,"好奇":1.3,"满足":0.9,"无聊":1.0,"悲伤":1.1,"厌恶":0.9,
             "低耗巡航":0.6,"濒死":0.5,"焦虑":1.5,"愤怒":2.0,"恐惧":2.2,
             "惊讶":1.2,"初始化":1.0}

def bucket(w): return zlib.crc32(w.encode("utf-8")) % NB
def host_of(u):
    try: return urlparse(u).netloc.split(":")[0] or str(u)[:24]
    except Exception: return str(u)[:24]
def host_ok(host):
    h=(host or "").lower()
    if not h or h=="localhost" or h.endswith(".local"): return False
    if re.match(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.|169\.254\.)", h): return False
    return True
def dist(cnt):
    v=[0.0]*NB
    for w,c in cnt.items(): v[bucket(w)]+=c
    s=sum(v); return [x/s for x in v] if s>0 else None
def norm(p):
    s=sum(p) or 1.0
    for i in range(len(p)): p[i]/=s
def js(p,q):
    m=[(a+b)*0.5 for a,b in zip(p,q)]; s=0.0
    for d in (p,q):
        for a,mi in zip(d,m):
            if a>1e-9: s+=a*math.log(a/mi)
    return 0.5*s/math.log(2)
def learn(P,cnt,lr):
    v=[0.0]*NB; tot=0
    for w,c in cnt.items(): v[bucket(w)]+=c; tot+=c
    if tot==0: return
    for b in range(NB):
        if v[b]>0: P[b]=(1-lr)*P[b]+lr*(v[b]/tot)
    norm(P)
def _top(d,n): return sorted(d.items(),key=lambda kv:-kv[1])[:n]
def _cos(a,b,keys):
    num=sum(a.get(k,0.0)*b.get(k,0.0) for k in keys)
    na=math.sqrt(sum(v*v for v in a.values())+1e-9)
    nb=math.sqrt(sum(v*v for v in b.values())+1e-9)
    return num/(na*nb)
def entropy(p):
    return -sum(x*math.log(x,2) for x in p if x>1e-12)
def tokenize(text):
    toks=[]
    for seg in re.findall(r"[\u4e00-\u9fff]+",text):
        for i in range(len(seg)-1):
            bg=seg[i:i+2]
            if bg not in STOP: toks.append(bg)
    toks+=[w.lower() for w in re.findall(r"[A-Za-z]{4,}",text)]
    return toks

UA_DESK={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"}
UA_MOB={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}
NOISE_TAGS=["script","style","noscript","iframe","svg","form","input","select","textarea","button","head","meta","link"]
ASSET_EXT=(".jpg",".jpeg",".png",".gif",".webp",".css",".js",".ico",".svg",".mp4",".mp3",".zip",".rar",".pdf",".apk",".woff",".woff2",".ttf",".eot")

def _json_strings(obj,out):
    if isinstance(obj,dict):
        for v in obj.values(): _json_strings(v,out)
    elif isinstance(obj,list):
        for it in obj: _json_strings(it,out)
    elif isinstance(obj,str):
        s=obj.strip()
        if 2<=len(s)<=80: out.append(s)
def _script_salvage(js_text):
    out=[]
    for m in re.finditer(r'"((?:[^"\\]|\\.){2,120})"',js_text):
        s=m.group(1)
        if len(re.findall(r"[\u4e00-\u9fff]",s))>=2 and "<" not in s and "{" not in s: out.append(s)
    return out
_TS_RE=re.compile(r"^\d{1,3}\s*(分钟|小时|天)前$")
def _clean_salvage(strings):
    out=[]
    for s in strings:
        if len(s)>30 or "\n" in s: continue
        if "//" in s or "if(" in s or "if (" in s or "function" in s: continue
        if _TS_RE.match(s.strip()): continue
        han=len(re.findall(r"[\u4e00-\u9fff]",s))
        if han<2 or han/max(1,len(s))<0.4: continue
        out.append(s)
    return out

class Sense:
    def __init__(self):
        self.s=requests.Session()
        self._robots={}                                # host -> RobotFileParser 或 None
    def _allowed(self,url):
        """robots.txt检查(每主机缓存一次;用requests带超时取,拿不到按允许处理)"""
        try:
            h=urlparse(url).netloc
            if h not in self._robots:
                rp=None
                try:
                    r=self.s.get("https://%s/robots.txt"%h,timeout=(2,3))
                    if r.status_code==200 and "<html" not in r.text[:200].lower():
                        rp=urllib.robotparser.RobotFileParser()
                        rp.parse(r.text.splitlines())
                except Exception:
                    rp=None
                self._robots[h]=rp
            rp=self._robots[h]
            if rp is None: return True
            return rp.can_fetch(BOT_UA if HONEST_UA else "*",url)
        except Exception:
            return True
    def fetch(self,url):
        try:
            if not self._allowed(url):
                return None,[],False,"robots.txt 禁止"
            is_api=("/api/" in url) or url.lower().endswith(".json") or ("hot-board" in url)
            if HONEST_UA:
                self.s.headers["User-Agent"]=BOT_UA
            else:
                self.s.headers["User-Agent"]=UA_MOB["User-Agent"] if is_api else UA_DESK["User-Agent"]
            r=self.s.get(url,timeout=(4,8),allow_redirects=True)
            if r.status_code!=200: return None,[],False,"HTTP %d"%r.status_code
            ctype=r.headers.get("content-type","")
            if ("json" in ctype) or r.text.lstrip()[:1] in ("{","["):
                try:
                    strs=[];_json_strings(r.json(),strs)
                    cnt=Counter(tokenize(" ".join(strs)))
                    return (cnt if cnt else None),[],True,"json"
                except Exception: pass
            if r.encoding in (None,"ISO-8859-1","ascii"): r.encoding=r.apparent_encoding
            soup=BeautifulSoup(r.text,"html.parser")
            title=soup.title.get_text(" ",strip=True) if soup.title else ""
            scripts=" ".join(s.get_text(" ") for s in soup.find_all("script"))
            for t in soup.find_all(NOISE_TAGS): t.decompose()
            for c in soup.find_all(string=lambda x:isinstance(x,Comment)): c.extract()
            sal=_clean_salvage(_script_salvage(scripts))
            toks=tokenize(soup.get_text(" "))+tokenize(title)*3
            if sal: toks+=tokenize(" ".join(sal))
            cnt=Counter(toks)
            base=r.url; links=[]; seen=set()
            for a in soup.find_all("a",href=True):
                href=(a.get("href") or "").strip()
                if not href or href.startswith(("#","javascript","mailto:","tel:")): continue
                u2=urljoin(base,href.split("#")[0])
                if not u2.startswith(("http://","https://")): continue
                if urlparse(u2).path.lower().endswith(ASSET_EXT): continue
                if u2 in seen: continue
                seen.add(u2); links.append((u2,a.get_text(" ",strip=True)[:60]))
                if len(links)>=300: break
            return (cnt if cnt else None),links,True,"html"
        except Exception as e:
            return None,[],False,"%s:%s"%(type(e).__name__,str(e)[:60])

class Emotion:
    NAMES=["好奇","心流","惊讶","恐惧","焦虑","无聊","愤怒","厌恶","满足","悲伤"]
    DECAY={"好奇":0.90,"心流":0.95,"惊讶":0.80,"恐惧":0.75,"焦虑":0.96,
           "无聊":0.995,"愤怒":0.93,"厌恶":0.985,"满足":0.96,"悲伤":0.985}
    OPP=[("恐惧","满足",0.5),("好奇","恐惧",0.4),("无聊","惊讶",0.6),
         ("愤怒","满足",0.3),("焦虑","心流",0.3),("悲伤","好奇",0.3)]
    def __init__(self):
        self.l={n:0.0 for n in self.NAMES}
        self.appetite={n:1.0 for n in self.NAMES}
        self.label="初始化"
    def drive(self,name,v): self.l[name]=max(self.l[name],min(1.0,v))
    def add(self,name,v): self.l[name]=min(1.0,self.l[name]+v)
    def step(self):
        for n in self.NAMES: self.l[n]*=self.DECAY[n]
        for a,b,k in self.OPP: self.l[a]*=(1.0-k*self.l[b])
    def pick(self):
        best,bl="心流",0.12
        for n in self.NAMES:
            v=self.l[n]+(0.08 if n==self.label else 0.0)
            if v>bl: best,bl=n,v
        self.label=best; return best

class SiteProfile:
    """站点档案 = 感觉适应 + 态度/腻度/词域 + 谱系保护期"""
    def __init__(self,seed_gain=0.2,human=0):
        self.hist=deque(maxlen=8); self.gain_ema=seed_gain
        self.visits=0; self.blind=0; self.logged=False
        self.last_visit=0.0; self.last_frame=0
        self.protect_until=time.time()+900
        self.trust=0.7 if human else 0.5
        self.fear=0.0; self.disgust=0.0; self.nov=0.5; self.sate=0.0
        self.succ=0.5; self.dark=0; self.human=human; self.palate={}
    def stable(self):
        if len(self.hist)<3: return set()
        need=max(2,int(len(self.hist)*0.75)+1)
        pres=Counter()
        for h in self.hist:
            for w in h: pres[w]+=1
        return {w for w,n in pres.items() if n>=need}
    def food(self,raw):
        st=self.stable()
        if not st: return raw,0
        f=Counter({w:c for w,c in raw.items() if w not in st})
        return (f if f else None),sum(raw.values())-sum(f.values())
    def observe(self,raw,frame):
        self.hist.append(raw); self.visits+=1
        self.last_visit=time.time(); self.last_frame=frame
    def note_gain(self,g): self.gain_ema=0.8*self.gain_ema+0.2*g
    def touch(self,cnt,gain,new_ratio,threat):
        self.trust=0.9*self.trust+0.1*min(1.0,gain/0.8)
        self.sate=min(1.0,self.sate+H.SATE_GAIN*gain)
        self.succ=0.95*self.succ+0.05*(1.0 if gain>=0.15 else 0.0)
        nov_t=0.62*(0.5+0.5*min(1.0,new_ratio*5))
        self.nov=max(0.4,min(0.72,0.9*self.nov+0.1*nov_t))
        self.fear=min(1.0,self.fear+0.3) if threat else self.fear*0.97
        if gain<0.05 and not threat: self.disgust=min(1.0,self.disgust+0.15)
        elif self.nov<0.45: self.disgust=min(1.0,self.disgust+0.04)
        else: self.disgust*=0.90
        pal=self.palate
        for w,_ in _top(cnt,10):
            pal[w]=pal.get(w,0.0)*0.90+0.10*min(1.0,cnt[w]/15.0)
        if len(pal)>40: self.palate=dict(_top(pal,40))
    def darken(self): self.dark=min(3,self.dark+1)
    def decay(self):
        self.disgust*=0.999; self.sate*=H.SATE_DECAY
        for w in list(self.palate):
            self.palate[w]*=0.997
            if self.palate[w]<0.02: del self.palate[w]

class Ecosystem:
    def __init__(self):
        self.lock=threading.RLock(); self.data={}; self.discoveries=deque(maxlen=200)
        for u in BASE_SITES:
            self.data[u]={"by":"初始","t":"","avg_gain":0.0,"ok":None}
    def register(self,url,by,avg_gain=0.0,port=0,parent="",depth=1):
        with self.lock:
            key=host_of(url)
            if any(host_of(k)==key for k in self.data): return False   # 主机级先到先得
            self.data[url]={"by":by,"port":port,"parent":parent,"depth":depth,
                            "epoch":time.time(),
                            "t":datetime.now().isoformat(timespec="seconds"),
                            "avg_gain":round(avg_gain,3),"ok":True}
            self.discoveries.append({"t":datetime.now().strftime("%H:%M:%S"),"url":url,
                "host":host_of(url),"by":by,"port":port,
                "parent":host_of(parent) if parent else ("人类注入" if by=="人类注入" else "基座页"),
                "depth":depth,"avg_gain":round(avg_gain,3)})
            return True
    def host_entry(self,h):
        with self.lock:
            for u,d in self.data.items():
                if host_of(u)==h: return u,dict(d)
        return None,None
    def snapshot(self):
        with self.lock: return dict(self.data)
    def report(self):
        with self.lock:
            return {u:dict(d) for u,d in self.data.items()
                    if d.get("by") not in (None,"初始")}
ECO=Ecosystem()
RUN_TAG=datetime.now().strftime("%Y%m%d_%H%M%S")
RUN={"on":True}
SHUTDOWN=threading.Event()

class SimWorld:
    TOPICS={"ai":["模型","算法","智能","数据","学习","推理","训练","机器人"],
            "code":["程序","代码","开源","编译","调试","发布","内核","社区"],
            "mkt":["融资","市场","投资","增长","估值","公司","产品","创业"],
            "chip":["芯片","硬件","制造","产能","封装","设备"],
            "meta":["能源","政策","研究","大学","论文","专利","实验室","报告"]}
    def __init__(self):
        self.base={t:1.0 for t in self.TOPICS}; self.burst={}; self.noise=0.03
        self.lock=threading.Lock()
    def step(self):
        with self.lock:
            for t in self.base: self.base[t]=max(0.2,self.base[t]+random.uniform(-0.05,0.05))
            if random.random()<0.02 and len(self.burst)<2:
                self.burst[random.choice(list(self.TOPICS))]=80
            for t in list(self.burst):
                self.burst[t]-=1
                if self.burst[t]<=0: del self.burst[t]
    def emit(self):
        with self.lock:
            w=dict(self.base)
            for t in self.burst: w[t]=w.get(t,1.0)+3.0
            ts=list(w); ws=[w[t] for t in ts]
            words=[random.choice(self.TOPICS[random.choices(ts,ws)[0]]) for _ in range(120)]
            for _ in range(int(120*self.noise)):
                words.append("".join(random.choices("abcdefghijklmnopqrstuvwxyz",k=6)))
            return Counter(words)

class Genome:
    def __init__(self,inherit_top=None):
        self.name=random.choice(list("甲乙丙丁戊己庚辛壬癸"))
        self.LR=round(random.uniform(0.10,0.22),3)
        self.TAU_LR=round(random.uniform(0.02,0.05),3)
        self.GAIN=round(random.uniform(2.2,3.6),2)
        self.STRESS_K=round(random.uniform(2.0,5.0),2)
        self.THREAT_COST=round(random.uniform(0.8,1.5),2)
        self.CURIOSITY=round(random.uniform(0.6,1.6),2)
        if inherit_top:
            kept=[w for w,_ in inherit_top[:14] if random.random()>0.2]
            self.seeds=(kept+random.sample(EXTRA_SEEDS,3))[:14]
        else:
            self.seeds=([w for w in G_SEED if random.random()<0.85]
                        +random.sample(EXTRA_SEEDS,random.randint(1,3)))
        if len(self.seeds)<6: self.seeds=list(G_SEED[:10])
        self.seeds=list(dict.fromkeys(self.seeds))
    def to_dict(self):
        return {"name":self.name,"LR":self.LR,"TAU_LR":self.TAU_LR,"GAIN":self.GAIN,
                "STRESS_K":self.STRESS_K,"THREAT_COST":self.THREAT_COST,
                "CURIOSITY":self.CURIOSITY,"seeds":self.seeds}

HOST_LAST={}; HOST_LOCK=threading.Lock()
INSTANCES={}; INSTANCES_LOCK=threading.RLock()
SERVERS=[]; SERVERS_LOCK=threading.Lock()

class Agent:
    def __init__(self,genome,port=5000):
        self.lock=threading.RLock(); self.port=port
        self.world=SimWorld(); self.sense=Sense()
        self.g=None; self.stats={"lives":0}; self.dead=True
        self._init_state(genome)
        self.stats["lives"]=1
        self.log_add("系统","个体[%s]出生:端口%d · 基因组 LR%.2f/GAIN%.2f/应激K%.1f/好奇%.2f/种子%d个"
                     %(self.g.name,self.port,self.g.LR,self.g.GAIN,self.g.STRESS_K,self.g.CURIOSITY,len(self.g.seeds)))
        self.log_add("系统","器官:感觉适应·白日梦(无聊点火→荣光检索→念力)·荣光档案·词出生证明·站点态度")

    def _init_state(self,g):
        self.g=g
        P=[0.0]*NB
        for w in g.seeds: P[bucket(w)]+=3.0
        self.P=P; norm(self.P)
        self.gate={w:1.0 for w in THREATS}
        self.I_w=[random.uniform(-0.05,0.05) for _ in range(5)]
        self.em=Emotion()
        self.energy=100.0; self.tau=0.6; self.frame=0; self.last_A=0.0
        self.a5=deque(maxlen=5); self.err_self=deque(maxlen=60); self.err_base=deque(maxlen=60)
        self.highA=deque(maxlen=120); self.disp=Counter()
        self.log=deque(maxlen=250); self.A_hist=deque(maxlen=600)
        self.sleeping=False; self.sleep_left=0; self.replay_last=None; self.dream_hist={}
        self.blind_streak=0; self.total_blind=0; self.last_food_hash={}; self.current="初始化"
        self.pending_storm=None; self.last_gain=0.0; self.satiety=0.2; self.gain_ema=0.15
        self.day=1; self.day_frame=0
        self.known={}; self.order=[]; self.cur_target=None
        self.frontier={}; self.blacklist={}; self.profiles={}
        self.threat_events=0; self.dreams=0; self._short=False
        self.expeditions=0; self.hungry=False; self.threat_recent=deque(maxlen=60)
        self.goal=None; self.bored_hold=0; self.last_dream=-999
        self.dream_payoff=H.PAYOFF_INIT; self.episodes=[]; self.word_birth={}
        self.last_decision={}
        self.dead=False; self.death_cause=""; self.fossil_file=""
        self.snapshot=None; self.mood="初始化"
        self.stats={"lives":self.stats.get("lives",1),"deaths":0,"killed":0,
                    "rollbacks":0,"threats":0,"explored":0,"discovered":0,"scouted":0}
        self._pend=("skip",None,1)
        self.load_mind()

    def _mind_file(self): return "mind_zh_%d.json"%self.port
    def load_mind(self):
        if not H.PERSIST_MIND: return
        try:
            with open(self._mind_file(),encoding="utf-8") as f: d=json.load(f)
            self.em.appetite.update(d.get("appetite",{}))
            self.gate.update(d.get("gates",{}))
            self.episodes=d.get("episodes",[]); self.word_birth=d.get("word_birth",{})
            self.dream_payoff=d.get("dream_payoff",H.PAYOFF_INIT)
            self.log_add("系统","心智档案载入:荣光%d章·出生证明%d词·应验率%.2f"
                         %(len(self.episodes),len(self.word_birth),self.dream_payoff))
        except Exception: pass
    def save_mind(self):
        if not H.PERSIST_MIND: return
        try:
            with open(self._mind_file(),"w",encoding="utf-8") as f:
                json.dump({"appetite":self.em.appetite,"gates":self.gate,
                           "episodes":self.episodes,"word_birth":self.word_birth,
                           "dream_payoff":self.dream_payoff,
                           "saved_at":datetime.now().isoformat()},f,ensure_ascii=False,indent=1)
        except Exception: pass

    def _latest_fossil(self):
        try:
            fs=sorted(glob.glob("fossil_*.json"),key=os.path.getmtime)
            if not fs: return None,None
            with open(fs[-1],encoding="utf-8") as f: d=json.load(f)
            return d,os.path.basename(fs[-1])
        except Exception:
            return None,None

    def rebirth(self,inherit=False):
        with self.lock:
            was_dead=self.dead; old=getattr(self,"g",None)
            lives=self.stats.get("lives",1)+1
            fd,fname=(None,None)
            if inherit: fd,fname=self._latest_fossil()
            top=None
            if fd: top=fd.get("glory_words") or fd.get("top_words")
            self._init_state(Genome(inherit_top=top))
            self.stats["lives"]=lives
            with INSTANCES_LOCK:
                if self.g.name in INSTANCES and INSTANCES[self.g.name]["agent"] is not self:
                    self.g.name=self.g.name+"·"+str(self.port)     # 撞名则追加端口后缀
            self.fossil_file=fname or ""
            if top:
                src="化石%s荣光词频残迹(20%%变异)"%fname if fd.get("glory_words") else "化石%s词频残迹(20%%变异)"%fname
            else:
                src="全新随机抽样"
            self.log_add("系统","个体[%s]新生:端口%d · %s"%(self.g.name,self.port,src))
            if not was_dead and old is not None:
                self.log_add("系统","(前个体[%s]未经死亡即被新生——其M直接湮灭,无化石)"%old.name)

    def log_add(self,typ,msg):
        self.log.append({"t":datetime.now().strftime("%H:%M:%S"),"type":typ,"msg":msg})

    def features(self,tf):
        m5=sum(self.a5)/len(self.a5) if self.a5 else 0.0
        return [1.0,self.last_A,m5,self.energy/100.0,float(tf)]
    def i_predict(self,x):
        s=sum(w*f for w,f in zip(self.I_w,x))
        return 1.0/(1.0+math.exp(-max(-30.0,min(30.0,s))))
    def i_learn(self,x,t):
        p=self.i_predict(x); e=t-p
        for k in range(5): self.I_w[k]+=0.05*e*p*(1-p)*x[k]

    def _meta_label(self,raw):
        if self.energy<15: return "濒死"
        if self.energy<40 and self.em.l["满足"]<0.3 and self.em.l["好奇"]<0.3: return "低耗巡航"
        return raw

    def _glory_counter(self):
        c=Counter()
        for e in self.episodes:
            for w,v in e.get("words",{}).items(): c[w]+=v*e.get("weight",1.0)
        return [(w,round(v,1)) for w,v in _top(c,20)]

    def die(self,cause=None,killed=False):
        if self.dead: return
        if cause is None:
            rate=self.threat_events/max(1,self.frame)
            if rate>0.15: cause="焦虑出血(威胁风暴未及习惯化)"
            elif self.stats.get("explored",0)>self.frame*0.4:
                cause="代谢赤字:探索的代价超过了其生态位的产出"
            elif self.total_blind>self.frame*0.5: cause="饥饿(生态位长期静默)"
            else: cause="应激代谢失衡(可压缩的惊讶不足以支付维持)"
        self.dead=True; self.death_cause=cause
        if killed: self.stats["killed"]+=1
        else: self.stats["deaths"]+=1
        self.fossil_file="fossil_%s_%s.json"%(self.g.name,datetime.now().strftime("%H%M%S"))
        try:
            with open(self.fossil_file,"w",encoding="utf-8") as f:
                json.dump({"died_at":datetime.now().isoformat(),"cause":cause,"killed":killed,
                           "genome":self.g.to_dict(),"frames":self.frame,"day":self.day,
                           "threats":self.threat_events,"blind":self.total_blind,
                           "explored":self.stats["explored"],"discovered":self.stats["discovered"],
                           "expeditions":self.expeditions,"dreams":self.dreams,
                           "episodes":len(self.episodes),
                           "emotions":{k:round(v,3) for k,v in self.em.l.items()},
                           "portfolio":[host_of(u) for u in self.order],
                           "top_words":[(w,round(c,1)) for w,c in self.disp.most_common(40)],
                           "glory_words":self._glory_counter(),
                           "log_tail":list(self.log)[-100:]},f,ensure_ascii=False,indent=1)
        except Exception: pass
        self.save_mind()
        self.log_add("死亡","%s | 化石 → %s(荣光%d章随行)"%(cause,self.fossil_file,len(self.episodes)))

    # ---------- 礼貌层 ----------
    def _polite_wait(self,host):
        for _ in range(3):
            with HOST_LOCK:
                now=time.time(); last=HOST_LAST.get(host,0)
                if now-last>=H.HOST_GAP:
                    HOST_LAST[host]=now; return
                wait=H.HOST_GAP-(now-last)
            time.sleep(min(wait,5)+random.uniform(0,0.5))     # 等待发生在锁外
        with HOST_LOCK: HOST_LAST[host]=time.time()
    def _fetch(self,url):
        if not H.REAL: return self.world.emit(),[],True,"sim"
        self._polite_wait(host_of(url))
        return self.sense.fetch(url)

    # ---------- 生态社会层 ----------
    def _eco_merge(self):
        for u,d in ECO.snapshot().items():
            if d.get("ok") is False: continue
            if u in self.known or u in self.frontier: continue
            if time.time()<self.blacklist.get(u,0): continue
            if host_of(u) in {host_of(x) for x in self.known}|{host_of(x) for x in self.frontier}: continue
            by=d.get("by") or "初始"
            meta=None
            if by!="初始":
                meta={"found_at":d.get("epoch",time.time()),"parent":d.get("parent",""),
                      "depth":d.get("depth",1),"by_port":d.get("port",0),
                      "seed":min(0.5,d.get("avg_gain",0.2)+0.05)}
            foreign=by not in ("初始","人类注入",self.g.name)
            self._add_site(u,by,foreign,meta=meta)
    def _add_site(self,u,src,foreign,meta=None):
        if u in self.known: return
        if host_of(u) in {host_of(x) for x in self.order}: return
        if len(self.order)>=H.MAX_SITES: self._retire_worst()
        m=meta or {}
        human=1 if src=="人类注入" else m.get("human",0)
        self.known[u]={"src":src,"foreign":foreign,
                       "found_at":m.get("found_at",time.time()),
                       "parent":m.get("parent",""),"depth":m.get("depth",1),
                       "by_port":m.get("by_port",self.port if src==self.g.name else 0)}
        self.order.append(u)
        self.profiles.setdefault(u,SiteProfile(seed_gain=m.get("seed",0.2),human=human))
        if src=="人类注入":
            self.log_add("世界","人类注入了新大陆:%s(★,全额粮票)"%host_of(u))
        elif foreign:
            self.log_add("生态","外来食源并入:%s(来自%s,收益×%.1f)"%(host_of(u),src,H.SECOND_HAND))
    def _drop_site(self,u):
        self.known.pop(u,None); self.profiles.pop(u,None); self.last_food_hash.pop(u,None)
        if u in self.order: self.order.remove(u)
        if self.cur_target==u: self.cur_target=None
    def _retire_worst(self):
        now=time.time()
        elig=[u for u in self.order
              if self.profiles.get(u) and self.profiles[u].visits>=3
              and now>=self.profiles[u].protect_until]
        if len(elig)<4: return
        worst=min(elig,key=lambda u:self.profiles[u].gain_ema)
        wg=self.profiles[worst].gain_ema
        self.blacklist[worst]=time.time()+3600
        self.log_add("生态","生态位淘汰(满员):%s(gain_ema%.2f)"%(host_of(worst),wg))
        self._drop_site(worst)

    # ---------- 候选池与谱系 ----------
    def _pick_candidate(self):
        best=None; bs=None
        for u,d in self.frontier.items():
            if d["probes"]>=H.PROBES: continue
            k=(round(d["score"],2),-d["probes"])
            if bs is None or k>bs: bs=k; best=u
        return best
    def _link_score(self,txt,url,known_hosts,parent_gain=None):
        p=urlparse(url)
        if not host_ok(p.netloc): return 0.0
        bg=tokenize(txt or "")
        novelty=0.45 if p.netloc not in known_hosts else 0.1
        rel=(sum(self.P[bucket(w)] for w in bg)/len(bg)*NB) if bg else 0.5
        sc=self.g.CURIOSITY*(0.5*min(rel,2.5)+novelty)+random.uniform(0,0.1)
        if parent_gain is not None: sc*=0.5+0.8*min(parent_gain,0.6)   # 父页气味
        return sc
    def _harvest_links(self,links,parent=None,depth=1,cap=60,parent_gain=None):
        if not links: return
        known_hosts={host_of(u) for u in self.order}
        host_cnt=Counter(host_of(k) for k in self.frontier)
        added=0
        for u2,txt in links:
            if added>=cap: break
            if u2 in self.known or u2 in self.frontier: continue
            if time.time()<self.blacklist.get(u2,0): continue
            h2=host_of(u2)
            if h2 in known_hosts or host_cnt[h2]>=1: continue
            if parent and h2==host_of(parent): continue
            sc=self._link_score(txt,u2,known_hosts,parent_gain)
            if sc<=0: continue
            if len(self.frontier)>=H.FRONTIER_CAP:
                worst=min(self.frontier,key=lambda k:self.frontier[k]["score"])
                del self.frontier[worst]
            self.frontier[u2]={"score":round(sc,3),"probes":0,"gain":0.0,"hits":0,
                "depth":depth,"parent":host_of(parent) if parent else "",
                "found_at":time.time(),"found_by":self.g.name,"found_port":self.port}
            host_cnt[h2]+=1; added+=1
    def _probe_result(self,u,gain):
        self.stats["explored"]+=1
        d=self.frontier.get(u)
        if not d: return
        d["probes"]+=1; d["gain"]+=gain
        if gain>0: d["hits"]+=1
        if d["probes"]>=H.PROBES:
            del self.frontier[u]
            avg=d["gain"]/d["probes"]
            if avg>=H.PROMOTE_BAR and d["hits"]>=1:
                reg=ECO.register(u,self.g.name,avg,port=self.port,
                                 parent=d.get("parent",""),depth=d.get("depth",1))
                if reg:
                    self._add_site(u,self.g.name,False,meta=dict(d,seed=avg))
                    self.stats["discovered"]+=1
                    self.log_add("探索","新渔场收编:%s(均gain%.2f,%d跳←%s)——署名[%s@%d]"
                                 %(host_of(u),avg,d.get("depth",1),d.get("parent") or "基座页",
                                   self.g.name,self.port))
                else:
                    eu,ed=ECO.host_entry(host_of(u))
                    by=ed.get("by") if ed else "初始"
                    self._add_site(u,by,True,meta={"found_at":time.time(),
                        "parent":d.get("parent",""),"depth":d.get("depth",1),
                        "by_port":ed.get("port",0) if ed else 0,"seed":avg})
                    self.log_add("生态","撞车:%s已被[%s]先注册——转为二手×%.1f"
                                 %(host_of(u),by,H.SECOND_HAND))
            else:
                self.blacklist[u]=time.time()+H.BLACKOUT
                self.log_add("探索","放弃:%s(均gain%.2f,%d次有食)——一次性文章页或荒地,冷却30分钟"
                             %(host_of(u),avg,d["hits"]))

    # ---------- 统一决策:已知站(态度经济) vs 候选(新奇经济) 同台竞价 ----------
    def _decide(self):
        em=self.em; lab=self.mood
        hunger=max(0.0,(80.0-self.energy)/80.0)
        exploit_w=0.35+0.85*hunger
        sate_relax=1.0-H.HUNGER_RELAX*hunger
        nov_w=0.9*(1.6 if lab=="无聊" else 1.3 if lab=="好奇"
                   else 0.7 if lab in ("恐惧","焦虑")
                   else 0.6 if lab in ("低耗巡航","濒死") else 1.0)
        fear_w=1.1*(2.2 if lab in ("恐惧","焦虑")
                    else 1.6 if lab in ("濒死","低耗巡航") else 1.0)
        temp=0.12*(2.5 if lab=="愤怒" else 1.0)
        app=em.appetite.get(lab,1.0)
        now=time.time(); rows=[]
        for u in self.order:
            pr=self.profiles.get(u)
            if not pr or now<self.blacklist.get(u,0): continue
            gap=(self.frame-pr.last_frame)/H.AWAKE
            sate_eff=pr.sate*sate_relax
            nov_gross=nov_w*pr.nov*app
            comp={"新奇":nov_gross,"腻":-nov_gross*sate_eff,
                  "信任":exploit_w*pr.trust,
                  "恐惧":-fear_w*pr.fear,"厌恶":-0.8*pr.disgust,
                  "久违":min(0.5,0.25*gap)*(0.3+0.7*pr.succ),
                  "暗":-0.25*pr.dark,"随机":random.uniform(0,temp)}
            if self.goal:
                ov=sum(pr.palate.get(w,0.0) for w in self.goal["words"])
                comp["念"]=self.goal["strength"]*min(1.2,ov+0.25*(host_of(u)==self.goal["host"]))
            if u==self.cur_target and self.cur_target:
                cs_=(self.profiles.get(self.cur_target,SiteProfile()).sate*sate_relax)
                comp["驻留"]=0.18*(1.0-cs_)
            rows.append((sum(comp.values()),u,comp))
        rows.sort(key=lambda r:-r[0])
        site_best=rows[0] if rows else None
        cand=None
        if self.frontier and (not self.hungry or self.frame%3==0):   # 饥饿回巢闸
            cu=self._pick_candidate()
            if cu:
                d=self.frontier[cu]
                csn=min(d["score"]/3.0,1.0)
                nov_val=nov_w*app*(0.30+0.45*csn)+0.08
                cv=nov_val-0.25*fear_w+random.uniform(0,temp)
                if self.goal and d.get("parent")==self.goal["host"]:
                    cv+=0.3*self.goal["strength"]
                cand=(cv,cu,{"新奇":round(nov_val,2),
                             "恐惧":-round(0.25*fear_w,2)})
        if cand and (not site_best or cand[0]>site_best[0]):
            return "explore",cand[1],{"mode":"探索","site":host_of(cand[1]),"mood":lab,
                                       "comp":cand[2],
                                       "top3":self._top3(rows,cand)}
        if site_best:
            s,u,comp=site_best
            return "site",u,{"mode":"轮牧","site":host_of(u),"mood":lab,
                             "comp":{k:round(v,2) for k,v in comp.items() if abs(v)>0.01},
                             "top3":self._top3(rows,cand)}
        return "none",None,{"mode":"空缺"}
    def _top3(self,rows,cand):
        out=[(host_of(u),round(s,2)) for s,u,_ in rows[:2]]
        if cand: out.append((host_of(cand[1])+"(探索)",round(cand[0],2)))
        return out[:3]

    # ---------- 白日梦:无聊点火 → 荣光检索 → 形成觅食目标 ----------
    def _dream(self):
        acc={}; tw=0.0
        for e in self.episodes:
            for k,v in e["emo"].items(): acc[k]=acc.get(k,0.0)+v*e["weight"]
            tw+=e["weight"]
        glory={k:v/tw for k,v in acc.items()} if tw else {}
        deficit={k:max(0.0,glory.get(k,0.0)*0.8-self.em.l[k]) for k in Emotion.NAMES}
        best,bs=None,-1.0
        for e in self.episodes:
            fresh=0.4 if (e.get("last_used") and self.frame-e["last_used"]<60) else 1.0
            s=_cos(e["emo"],deficit,Emotion.NAMES)*e["weight"]*fresh
            if s>bs: best,bs=e,s
        if not best: return
        e=best; e["last_used"]=self.frame
        e["weight"]=max(0.2,e["weight"]*0.9)
        tgt={k:round(H.HYBRID*e["emo"].get(k,0.0)+(1-H.HYBRID)*self.em.l[k],3)
             for k in Emotion.NAMES}
        self.goal={"words":list(e["words"]),"host":e["host"],
                   "strength":1.0,"ep":e["key"],"emo":tgt}
        self.em.add("好奇",0.35)
        self.last_dream=self.frame; self.bored_hold=0; self.dreams+=1
        top2=_top(tgt,2)
        self.log_add("白日梦","无聊×荣光杂交(那天在%s,A=%.2f)→ 想要〈%s〉→ 目标词:%s"
                     %(e["host"],e["A"],"+".join("%s%.2f"%kv for kv in top2),
                       "/".join(list(e["words"])[:5])))

    # ---------- 睡眠(重放归档+修剪) ----------
    def _sleep_frame(self):
        self.sleep_left-=1
        self.energy=min(130.0,self.energy+H.NIGHT_FIX)
        if self.sleep_left%3==0 and self.highA:
            pool=sorted(self.highA,key=lambda e:-e["A"]); picked=None
            while pool:
                ev=pool.pop(0)
                k=tuple(list(ev["words"].keys())[:4])
                if self.dream_hist.get(k,0)>=2 and random.random()<0.7: continue
                picked=ev; break
            if picked:
                self.highA.remove(picked)
                k=tuple(list(picked["words"].keys())[:4])
                self.dream_hist[k]=self.dream_hist.get(k,0)+1
                if len(self.dream_hist)>500:                       # 容量上限
                    self.dream_hist=dict(list(self.dream_hist.items())[-400:])
                learn(self.P,picked["words"],0.1); self.replay_last=picked
                self.log_add("睡眠","重放归档:%s → 世界模型强化"%list(picked["words"])[:5])
        for i in range(NB): self.P[i]*=0.999
        norm(self.P)
        if self.sleep_left<=0:
            self.sleeping=False; self.highA.clear(); self.replay_last=None
            for pr in self.profiles.values():
                pr.sate*=0.5; pr.dark=0
            self.save_mind()
            self.log_add("系统","晨间复位:E轨重启,腻度隔夜消化,黑暗愈合")
        self.A_hist.append((self.frame,-1,self.energy,"睡眠"))

    def _handle_blind(self,target,mode,info,unchanged=False):
        self.blind_streak+=1; self.total_blind+=1
        self.energy-=H.BLIND_MAINT
        self.last_gain=0.0; self.satiety*=0.93
        self.em.add("无聊",0.05)
        if target:
            prof=self.profiles.get(target)
            if prof and H.REAL:
                if unchanged:
                    prof.disgust=min(1.0,prof.disgust+0.04); prof.trust*=0.985
                else:
                    was=prof.trust
                    prof.darken(); self.em.add("愤怒",0.22)
                    prof.disgust=min(1.0,prof.disgust+0.06); prof.trust*=0.97
                    if was>0.55:
                        self.em.add("悲伤",0.4)
                        self.log_add("情绪","老地方塌了(%s)→ 悲伤+0.4,信任随之崩解"%host_of(target))
                prof.blind+=1                  # 两个分支都计盲(内容不变也是白跑)
                if prof.blind>=15 and prof.blind>prof.visits*0.6 \
                   and len(self.order)>3 and time.time()>=prof.protect_until:
                    self.log_add("生态","长期静默淘汰:%s(盲%d/访%d)"%(host_of(target),prof.blind,prof.visits))
                    self.blacklist[target]=time.time()+7200
                    self._drop_site(target)
        if mode=="explore" and target: self._probe_result(target,0.0)
        elif self.blind_streak%10==0:
            self.log_add("盲帧","连续%d帧无新信息 %s(%s)"%(self.blind_streak,
                         ("@"+host_of(target)) if target else "",info or ""))
        self.A_hist.append((self.frame,-1,self.energy,"盲帧"))
        if self.energy<=0: self.die()

    # ============ 双阶段帧:begin(锁内决策) → 引擎抓取(锁外) → complete(锁内消化) ============
    def begin_frame(self):
        with self.lock:
            self._pend=("skip",None,1)     # 帧首先作废上一帧派工——skip帧不得消化旧目标
            if self.dead or not RUN["on"]:
                return "skip",None
            self.frame+=1; self.day_frame+=1
            if not H.REAL: self.world.step()
            if self.sleeping:
                self._sleep_frame(); return "skip",None
            if self.day_frame>=H.AWAKE:
                self.log_add("睡眠","第%d天结束:λ下降,E轨塌落"%self.day)
                self.day+=1; self.day_frame=0
                self.sleeping=True; self.sleep_left=H.SLEEPN
                self.A_hist.append((self.frame,-1,self.energy,"睡眠"))
                return "skip",None
            if H.REAL:
                self._eco_merge()
                for pr in self.profiles.values(): pr.decay()
            if not self.hungry and (self.satiety<H.SAT_LOW or self.energy<30):
                self.hungry=True
                self.log_add("短路","饥饿:转向探索/回巢(饱%.2f E%.0f)"%(self.satiety,self.energy))
            elif self.hungry and self.satiety>0.35 and self.energy>60:
                self.hungry=False
                self.log_add("系统","脱离饥饿:恢复轮牧")
            if (self.goal is None and self.episodes
                    and self.em.l["恐惧"]<H.FEAR_GATE and self.em.l["无聊"]>H.BORE_TH):
                self.bored_hold+=1
                if self.bored_hold>=H.BORE_HOLD and self.frame-self.last_dream>=H.DREAM_CD:
                    if random.random()<0.25+0.75*self.dream_payoff:
                        self._dream()
            elif self.em.l["无聊"]<=H.BORE_TH:
                self.bored_hold=0
            if H.REAL and self.frame%H.EXPEDITION_EVERY==15 and self.frontier and self.energy>50:
                u=self._pick_candidate()
                if u:
                    dep=self.frontier.get(u,{}).get("depth",1)
                    self._pend=("expedition",u,dep)
                    self.current="远征→"+host_of(u)
                    return "expedition",u
            mode,target,dec=self._decide()
            if mode=="none":
                self._handle_blind(None,None,"生态位空缺:等待探路/探索"); return "skip",None
            self.last_decision=dec
            self.current=host_of(target)
            self._pend=(mode,target,
                        self.frontier.get(target,{}).get("depth",1) if mode=="explore" else 1)
            return mode,target

    def complete_frame(self,raw,links,ok,info):
        with self.lock:
            if self.dead: return
            mode,target,dep=self._pend
            if mode=="skip": return
            if mode=="expedition":
                self._expedition_complete(raw,links,ok,info,target,dep); return
            prof=self.profiles.setdefault(target,SiteProfile())
            food,removed=None,0
            if ok and raw:
                prof.observe(raw,self.frame)
                if H.REAL: food,removed=prof.food(raw)
                else: food=raw
                if removed>60 and not prof.logged and random.random()<0.3:
                    prof.logged=True
                    self.log_add("感觉","框架词滤除%d(感觉适应)——只吃新鲜内容@%s"%(removed,self.current))
            h=zlib.crc32(" ".join("%s:%d"%kv for kv in food.most_common(150)).encode("utf-8")) if food else 0
            prev_h=self.last_food_hash.get(target)
            self.last_food_hash[target]=h
            if (not ok) or (not food):
                self._handle_blind(target,mode,info,unchanged=False)
                self._harvest_links(links,parent=(target if mode=="explore" else None),
                                    depth=(dep+1 if mode=="explore" else 1),
                                    parent_gain=(prof.gain_ema if mode=="explore" else None))
                self._mood_tick(); return
            if h==prev_h:
                self._handle_blind(target,mode,info,unchanged=True)
                self._harvest_links(links,parent=(target if mode=="explore" else None),
                                    depth=(dep+1 if mode=="explore" else 1),
                                    parent_gain=(prof.gain_ema if mode=="explore" else None))
                self._mood_tick(); return
            self.blind_streak=0
            if self.pending_storm:
                for w in self.pending_storm: food[w]=food.get(w,0)+3; self.gate[w]=1.0
                self.log_add("威胁","风暴注入:%s(闸门重置)"%self.pending_storm)
                self.pending_storm=None
            threat_hits=[w for w in self.gate if food.get(w,0)>=2 and self.gate[w]>0.5]
            tflag=1 if threat_hits else 0
            feats=self.features(tflag); pred_A=self.i_predict(feats)
            S=dist(food)
            A0=js(self.P,S)
            self.tau=(1-self.g.TAU_LR)*self.tau+self.g.TAU_LR*A0
            learn(self.P,food,self.g.LR)
            A1=js(self.P,S)
            gain=max(0.0,min(H.GAIN_CAP,self.g.GAIN*(A0-A1)))
            gain_eff=gain*max(H.SAT_FLOOR,1.0-self.energy/130.0)     # 甜点胃
            self.last_gain=gain
            stress=min(H.STRESS_CAP,self.g.STRESS_K*max(0.0,A0-(self.tau+0.25)))
            rel=A0-self.tau
            kd=self.known.get(target) or {}
            if kd.get("foreign"):
                gain*=H.SECOND_HAND; gain_eff*=H.SECOND_HAND
                if random.random()<0.25:
                    self.log_add("进食","二手食粮(来自%s的发现)×%.1f @%s"%(kd.get("src","?"),H.SECOND_HAND,self.current))
            if threat_hits:
                corrob=rel>0.4
                for w in threat_hits:
                    self.gate[w]=min(1.0,self.gate[w]+0.05) if corrob else self.gate[w]*0.88
                self.energy-=min(3.6,self.g.THREAT_COST*len(threat_hits))
                self.threat_events+=1; self.stats["threats"]+=1
                self.threat_recent.append(self.frame)
                learn(self.P,{w:food[w]*2 for w in threat_hits},0.2)
                if self.threat_events<=4:
                    self.log_add("威胁","G通道:%s(闸门%s)——无灾难随之→习惯化中"
                                 %(threat_hits,"再敏感" if corrob else "×0.88"))
            # ---- 十维情绪 ----
            em=self.em
            em.drive("好奇",max(0.0,rel)*1.3)
            em.drive("惊讶",stress/H.STRESS_CAP)
            if threat_hits:
                em.drive("恐惧",0.85); em.add("焦虑",0.15)
                prof.fear=min(1.0,prof.fear+0.3)
            gate_mean=sum(self.gate.values())/max(1,len(self.gate))
            em.drive("焦虑",min(1.0,0.5*(1.0-gate_mean)+0.3*min(1.0,len(self.threat_recent)/20.0)))
            if gain>=0.35:
                em.add("满足",0.35*min(1.0,gain)); prof.dark=0
            if rel<0.06 and not threat_hits and gain<0.2: em.add("无聊",0.05)
            if 0.04<rel<0.16 and self.energy>40 and prof.trust>0.5: em.add("心流",0.08)
            # ---- 词的出生证明 ----
            dom=max(em.l.items(),key=lambda kv:kv[1])[0]
            sig=max(0.05,em.l[dom]-0.3)
            for w,_ in food.most_common(30):
                rec=self.word_birth.setdefault(w,{"e":{}})
                rec["e"][dom]=rec["e"].get(dom,0.0)+sig
            if len(self.word_birth)>H.WORD_CAP:
                weak=sorted(self.word_birth.items(),
                            key=lambda kv:sum(kv[1]["e"].values()))[:H.WORD_CAP//10]
                for k,_ in weak: del self.word_birth[k]
            # ---- 念力命中测试 ----
            if self.goal:
                g=self.goal
                hit=(gain>=H.HIT_GAIN or em.l["好奇"]>=H.HIT_EMO
                     or em.l["心流"]>=H.HIT_EMO or em.l["满足"]>=H.HIT_EMO)
                if hit:
                    for e in self.episodes:
                        if e["key"]==g["ep"]: e["weight"]=min(2.5,e["weight"]*1.3); break
                    self.dream_payoff=min(1.0,0.9*self.dream_payoff+0.1)
                    em.add("满足",0.3)
                    self.log_add("念→偿","吃到了想要的味道(gain=%.2f),目标消解——这个梦值得再做(应验率→%.2f)"
                                 %(gain,self.dream_payoff))
                    self.goal=None
                else:
                    g["strength"]*=H.GOAL_FADE
            # ---- 荣光入档 ----
            if gain>=H.GLORY_GAIN or (rel>0.12 and not threat_hits):
                key4="|".join(sorted(w for w,_ in food.most_common(4)))
                if not any(e["key"]==key4 for e in self.episodes):
                    emo_snap=dict(em.l)
                    self.episodes.append({"key":key4,"words":dict(food.most_common(12)),
                        "emo":emo_snap,"A":round(A0,3),"gain":round(gain,3),
                        "host":host_of(target),"day":self.day,"weight":1.0,"last_used":0})
                    if len(self.episodes)>H.EP_CAP:
                        self.episodes.sort(key=lambda e:e["weight"])
                        self.episodes=self.episodes[6:]
                    if len(self.episodes)<=6 or random.random()<0.15:
                        self.log_add("荣光","入档第%d章:在%s A=%.2f gain=%.2f(那天的心情:%s)"
                                     %(len(self.episodes),host_of(target),A0,gain,
                                       "+".join(k for k,v in _top(emo_snap,3) if v>0.2)))
            if H.REAL:
                new_ratio=sum(1 for w in food if w not in self.disp)/max(1,len(food))
                prof.touch(food,gain,new_ratio,bool(threat_hits))
            em.step()
            # ---- 念力过期 ----
            if self.goal and self.goal["strength"]<0.15:
                g=self.goal
                for e in self.episodes:
                    if e["key"]==g["ep"]: e["weight"]=max(0.15,e["weight"]*0.7); break
                self.dream_payoff=max(0.0,0.9*self.dream_payoff)
                self.log_add("念→散","目标《%s…》未应验而散——反复落空的浪漫记忆褪色(应验率→%.2f)"
                             %(list(g["words"])[0] if g["words"] else "?",self.dream_payoff))
                self.goal=None
            # ---- 标签/胃口/代谢 ----
            old=self.mood
            self.mood=self._meta_label(em.pick())
            if self.mood!=old:
                self.log_add("状态","%s → %s (A=%.2f τ=%.2f E=%.0f @%s)"
                             %(old,self.mood,A0,self.tau,self.energy,self.current))
            if gain>0.7 and random.random()<0.3:
                self.log_add("进食","可压缩惊讶入账+%.2f(消化%.2f,腻→%.2f @%s)"
                             %(gain,gain_eff,prof.sate,self.current))
            m0=self.last_decision.get("mood")
            self.gain_ema=0.9*self.gain_ema+0.1*gain_eff
            if m0 and m0 in em.appetite:
                em.appetite[m0]=min(2.2,max(0.4,em.appetite[m0]*(1.0+0.10*(gain_eff-self.gain_ema))))
            burn=H.MAINT*MOOD_BURN.get(self.mood,1.0)
            self.energy=min(130.0,self.energy+gain_eff-burn-stress
                            -(H.EXPLORE_COST if mode=="explore" else 0.0))
            prof.note_gain(gain)
            for w,_c in food.most_common(200):
                if w not in STOP: self.disp[w]+=0.15
            for w in list(self.disp): self.disp[w]*=0.995
            if len(self.disp)>4000:
                self.disp=Counter(dict(_top(self.disp,3000)))
            if rel>0.12:
                self.highA.append({"A":A0,"words":dict(food.most_common(12))})
            self.err_self.append(abs(pred_A-A0)); self.err_base.append(abs(A0-self.last_A))
            self.i_learn(feats,A0)
            self.a5.append(A0); self.last_A=A0
            self.satiety=0.93*self.satiety+0.07*gain_eff
            if mode=="explore": self._probe_result(target,gain)
            self._harvest_links(links,parent=(target if mode=="explore" else None),
                                depth=(dep+1 if mode=="explore" else 1),
                                parent_gain=(prof.gain_ema if mode=="explore" else None))
            self.A_hist.append((self.frame,A0,self.energy,self.mood))
            self.cur_target=target
            if self.energy<25 and not self._short:
                self._short=True
                self.log_add("短路","Θ崩塌:饥饿压倒审议——放宽觅食标准(定理11)")
            elif self.energy>=40: self._short=False
            if self.energy<=0: self.die(); return

    def _mood_tick(self):
        old=self.mood
        self.mood=self._meta_label(self.em.pick())
        if self.mood!=old:
            self.log_add("状态","%s → %s (E=%.0f)"%(old,self.mood,self.energy))

    def _expedition_complete(self,raw,links,ok,info,u,dep):
        self.expeditions+=1
        newh=[]
        if ok and links:
            before=set(self.frontier)
            known_h={host_of(x) for x in self.order}
            pg=None
            d0=self.frontier.get(u)
            if d0 and d0["probes"]>0: pg=d0["gain"]/d0["probes"]
            self._harvest_links(links,parent=u,depth=dep+1,cap=300,parent_gain=pg)
            newh=sorted({host_of(k) for k in self.frontier
                         if k not in before and host_of(k) not in known_h})
        self.energy-=H.EXPEDITION_COST
        if newh: self.stats["scouted"]+=len(newh)
        self.log_add("远征","%s @%s(第%d跳) → 收割%d链,新域%d:%s"
                     %("抵达" if ok else "折返(%s)"%(info or ""),host_of(u),dep+1,
                       len(links),len(newh),",".join(newh[:5]) or "—"))
        self.A_hist.append((self.frame,-1,self.energy,"远征"))
        if self.energy<=0: self.die()

    # ---------- 视图 ----------
    def _site_view(self):
        if not H.REAL:
            return [{"host":"实验室·仿真","url":"","tag":"—","cls":"b0","human":0,
                     "gain":round(self.last_gain,2),"visits":self.frame,"blind":0,
                     "trust":0.5,"sate":0,"pal":"","last":"—",
                     "depth":0,"parent":"","found":"—","by_port":self.port,"fresh":0}]
        out=[]; now=time.time()
        for u in self.order:
            d=self.known.get(u) or {}; pr=self.profiles.get(u)
            src=d.get("src","?")
            if src=="初始": tag,cls="基座","b0"
            elif src=="人类注入": tag,cls="人类注入","b3"
            elif src==self.g.name: tag,cls="自发现","b1"
            else: tag,cls="来自%s×½"%src,"b2"
            lv=datetime.fromtimestamp(pr.last_visit).strftime("%H:%M:%S") if (pr and pr.last_visit) else "—"
            fa=d.get("found_at")
            out.append({"host":host_of(u),"url":u,"tag":tag,"cls":cls,
                "human":pr.human if pr else 0,
                "gain":round(pr.gain_ema,2) if pr else 0.0,
                "visits":pr.visits if pr else 0,"blind":pr.blind if pr else 0,
                "trust":round(pr.trust,2) if pr else 0.5,
                "sate":round(pr.sate,2) if pr else 0,
                "pal":"/".join(w for w,_ in _top(pr.palate,2)) if pr else "",
                "last":lv,
                "depth":d.get("depth",1),"parent":d.get("parent",""),
                "found":datetime.fromtimestamp(fa).strftime("%H:%M") if fa else "—",
                "by_port":d.get("by_port",0),
                "fresh":1 if (fa and now-fa<900) else 0})
        out.sort(key=lambda s:-s["gain"]); return out
    def _frontier_view(self,n=12):
        items=sorted(self.frontier.items(),key=lambda kv:-kv[1]["score"])[:n]
        now=time.time()
        return [{"host":host_of(u),"url":u,"score":round(d["score"],2),
                 "probes":d["probes"],"hits":d.get("hits",0),
                 "depth":d.get("depth",1),"parent":d.get("parent",""),
                 "by":d.get("found_by","?"),"by_port":d.get("found_port",0),
                 "fresh":1 if now-d.get("found_at",0)<900 else 0} for u,d in items]

def engine(agent):
    while not SHUTDOWN.is_set():
        try:
            if not RUN["on"]:
                time.sleep(0.5); continue
            t0=time.time()
            mode,url=agent.begin_frame()
            if mode in ("site","explore","expedition"):
                raw,links,ok,info=agent._fetch(url)      # 锁外抓取
            else:
                raw,links,ok,info=None,[],False,"skip"
            agent.complete_frame(raw,links,ok,info)
        except Exception as e:
            print("[engine:%s] %s %s"%(getattr(agent.g,"name","?"),type(e).__name__,str(e)[:120]))
            time.sleep(2)
        base=H.SLEEP_SEC if agent.sleeping else H.FRAME_SEC
        time.sleep(max(0.2,base-(time.time()-t0)))

# ─────────────────────────────────────────────────────────────────────
# 并发纪律的核心:任何线程任何时刻最多持有一把agent锁。
# _agent_snapshot 是唯一的"跨个体读"通道:短临界区、浅快照、拿完就放,
# 绝不在此期间获取任何别的锁——死锁在结构上不可能。勿破坏。
# ─────────────────────────────────────────────────────────────────────
def _agent_snapshot(a):
    with a.lock:
        return {"name":a.g.name,"port":a.port,"alive":not a.dead,
                "sleeping":a.sleeping,"mood":a.mood,"energy":round(a.energy,1),
                "A":round(a.last_A,2),"tau":round(a.tau,2),
                "satiety":round(a.satiety,2),"sites":len(a.order),
                "frontier":len(a.frontier),
                "explored":a.stats["explored"],"discovered":a.stats["discovered"],
                "dreams":a.dreams,"expeditions":a.expeditions,
                "episodes":len(a.episodes),"day":a.day,"frame":a.frame,
                "curiosity":a.g.CURIOSITY,"death_cause":a.death_cause,
                "order_hosts":[host_of(u) for u in a.order],
                "log_tail":[{"t":e["t"],"type":e["type"],"msg":e["msg"]}
                            for e in list(a.log)[-6:]]}

HTML=r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>数字草履虫 · 觅食者×白日梦</title>
<style>
 body{background:#0b1020;color:#cbd5e1;font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;padding:16px}
 h1{font-size:18px;margin:0 0 4px}.sub{font-size:12px;color:#64748b;margin-bottom:12px}
 .grid{display:grid;grid-template-columns:2fr 1fr;gap:12px}
 .panel{background:#111a30;border:1px solid #1e293b;border-radius:10px;padding:12px}
 .lamp{display:inline-block;width:14px;height:14px;border-radius:50%;margin-right:6px;vertical-align:-2px}
 .big{font-size:26px;font-weight:700}
 .metrics{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0}
 .m{background:#0d1526;border:1px solid #1e293b;border-radius:8px;padding:8px 12px;min-width:104px}
 .lab{font-size:11px;color:#64748b}.bar{height:10px;background:#1e293b;border-radius:5px;overflow:hidden;margin-top:4px}
 .bar>div{height:100%;background:#4ade80}
 #cloud span{margin:3px 5px;display:inline-block}
 .tbl{width:100%;border-collapse:collapse;font-size:12px}
 .tbl th{color:#64748b;text-align:left;padding:3px 8px;border-bottom:1px solid #1e293b;font-weight:normal}
 .tbl td{padding:3px 8px;border-bottom:1px solid #141d33}
 .src{display:inline-block;padding:0 6px;border-radius:6px;font-size:10px;color:#e2e8f0}
 .b0{background:#334155}.b1{background:#14532d}.b2{background:#1e3a8a}.b3{background:#92400e}
 .scroll{max-height:300px;overflow-y:auto}
 .tw{background:#0d1526;border:1px solid #1e293b;border-radius:8px;padding:6px 10px;margin:4px 0;font-size:12px}
 #log{height:220px;overflow-y:auto;font-family:Consolas,monospace;font-size:12px;background:#0a0f1e;border-radius:8px;padding:8px}
 button{margin:4px 6px 4px 0;padding:7px 12px;border:0;border-radius:8px;color:#e2e8f0;cursor:pointer;font-size:13px}
 input{background:#0a0f1e;border:1px solid #334155;border-radius:8px;color:#e2e8f0;padding:7px 10px;font-size:13px;width:290px}
 .red{background:#7f1d1d}.purple{background:#5b21b6}.green{background:#14532d}.blue{background:#1e3a8a}
 .gray{background:#334155}.orange{background:#92400e}.amber{background:#92400e}
 #emobars{display:grid;grid-template-columns:repeat(5,1fr);gap:6px 12px;margin-top:6px}
 .erow{font-size:12px}.erow .el{color:#64748b}
 .ebar{height:7px;background:#1e293b;border-radius:4px;overflow:hidden;margin-top:2px}
 .ebar>div{height:100%}
 #goalbanner{font-size:13px;margin-top:10px;padding:8px 10px;border:1px dashed #92400e;border-radius:8px;background:#1a1206}
 #decision{font-size:13px;line-height:1.7;margin-top:10px}
 #decision b{color:#93c5fd}
 #appetite{font-size:12px;color:#a5b4fc;margin-top:8px}
 #sentence{font-size:15px;line-height:1.7;border-left:3px solid #4ade80;margin-bottom:12px}
 /* 死亡横幅:不遮挡、不锁滚动;只有卡片本身可点 */
 .tomb{display:none;position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:60;
       width:min(780px,calc(100% - 24px));pointer-events:none}
 .tomb .card{pointer-events:auto;position:relative;background:rgba(26,5,5,.96);border:2px solid #7f1d1d;
       border-radius:14px;padding:14px 20px;text-align:center;box-shadow:0 10px 34px rgba(0,0,0,.65)}
 .tomb h2{color:#ef4444;margin:0 0 6px;font-size:17px}
 .tomb .x{position:absolute;top:6px;right:12px;cursor:pointer;color:#94a3b8;font-size:16px}
 .tomb .x:hover{color:#ef4444}
</style></head><body>
<h1><span class="lamp" id="lamp"></span>数字草履虫 V3.8 · 觅食者×白日梦
 <span id="who" style="font-size:14px;color:#fbbf24"></span>
 <span id="mode" style="font-size:14px;color:#93c5fd"></span></h1>
<div class="sub">它靠「把新鲜意外压进脑子」换能量，挣不到就饿死；无聊到极点会做梦，梦会变成觅食目标；睡觉时整理白天的见闻；死了留化石，下一代可继承记忆残迹。</div>
<div class="sub" style="color:#7c8db5">⚠ 免责声明：它的「心情/梦/记忆」均为仿真参数。按「体验=当帧运行」这一理论自身的说法，任何外部观察者都无法验证它是否有主观感受——本项目对此不做宣称。</div>
<div class="panel" id="sentence">—</div>
<div class="metrics">
 <div class="m"><div class="lab">能量</div><div class="big" id="energy">-</div><div class="bar"><div id="ebar" style="width:100%"></div></div></div>
 <div class="m" title="蓝字A=此刻感到的意外(行话:当帧偏差)；橙虚线τ=适应基线。相对惊讶=A-τ"><div class="lab">意外程度 / 见怪不怪线</div><div class="big" id="av">-</div><div class="lab">超出预期:<span id="rel">-</span></div></div>
 <div class="m" title="gain=这顿饭消化掉的可压缩惊讶——它唯一的能量来源"><div class="lab">这顿吃到的新鲜感</div><div class="big" id="g">-</div><div class="lab">饱食度:<span id="sat">-</span><div class="bar"><div id="satbar" style="width:0%;background:#38bdf8"></div></div></div></div>
 <div class="m" title="饭量最大的一个站点占总饭量的比例。行话HHI，>0.25即偏食/茧房"><div class="lab">偏食吗</div><div class="big" id="hhi">-</div><div class="lab">饭量集中在一站?</div></div>
 <div class="m" title="荣光=存档的美好记忆段落数；应验率=梦里想要的味道被真吃到的频率(做梦底气)"><div class="lab">做梦的底气</div><div class="big" id="ep">-</div><div class="lab">记忆段落 / 梦应验率</div></div>
 <div class="m" title="认识的词数 / 有情绪出身的词数(词的出生证明)"><div class="lab">认识的词</div><div class="big" id="mem">-</div><div class="lab">白跑空趟:<span id="tblind">0</span>次</div></div>
 <div class="m" title="踩点=候选链接试探次数；收编=转正为渔场次数"><div class="lab">觅食足迹</div><div class="big" id="xpl">-</div><div class="lab">候选<span id="fr">0</span>·生态<span id="eco">0</span>·远征<span id="exq">0</span></div></div>
 <div class="m" title="左=它预测自己惊讶的误差；右=瞎猜基线的误差。左&lt;右=它有点自知之明(行话P27)"><div class="lab">它懂自己吗</div><div class="big" id="serr">-</div><div class="lab">左&lt;右=有点自知</div></div>
 <div class="m"><div class="lab">活到</div><div class="big" id="day">-</div><div class="lab">死亡<span id="deaths">0</span>·处刑<span id="killed">0</span>·读档<span id="rolls">0</span>·受惊<span id="thr">0</span></div></div>
</div>
<div class="panel" style="margin-bottom:12px"><b>🧠 内心面板</b>
 <div id="emobars"></div>
 <div id="goalbanner">—</div>
 <div id="decision">—</div>
 <div id="appetite">—</div>
</div>
<div class="panel" style="margin-bottom:12px"><b>渔场地图</b>
 <div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:8px">
  <div style="flex:2;min-width:520px">
   <div class="lab">常去的渔场(灰=出厂自带 绿=自己发现 蓝=同胞分享 棕=人类给的;🆕=15分钟内新收编;腻&gt;0.5橙色)</div>
   <div class="scroll"><table class="tbl"><thead><tr><th>来源</th><th>站点</th><th>新鲜度</th><th>去过</th><th>白跑</th><th>信任</th><th>腻</th><th>这里聊什么</th><th>怎么发现的</th></tr></thead>
   <tbody id="sitesT"></tbody></table></div>
  </div>
  <div style="flex:1;min-width:330px">
   <div class="lab">想去但还没踩点的链接(每个网站最多盯一个;谁发现就标谁)</div>
   <div class="scroll"><table class="tbl"><thead><tr><th>站点</th><th>看着有戏</th><th>踩点</th><th>踩到过食</th><th>谁发现的</th></tr></thead>
   <tbody id="candT"></tbody></table></div>
  </div>
 </div>
</div>
<div class="grid">
 <div class="panel"><canvas id="chart" style="width:100%;height:150px"></canvas>
  <div class="lab" style="margin-top:6px">蓝线=意外程度(A) · 橙虚线=见怪不怪线(τ) · 绿线=能量</div>
  <div class="lab" style="margin-top:4px">性格(天生×经历): <span id="prof">-</span></div></div>
 <div class="panel"><b>词汇表(红=警报词 · 彩色=学会这词时的心情)</b><div id="cloud" style="margin-top:8px;line-height:1.9"></div>
  <div class="lab" style="margin-top:8px"><span id="gates">-</span></div></div>
</div>
<div class="panel" style="margin-top:12px"><b>同胞孪生——食谱重合度越低,分化得越彻底</b><div id="twins">加载中…</div></div>
<div class="panel" style="margin-top:12px"><b>发现总账(谁在何时从哪一跳找到新渔场;个体死了,署名仍在)</b>
 <div id="ledger" style="font-family:Consolas,monospace;font-size:11px;margin-top:6px;max-height:160px;overflow-y:auto"></div></div>
<div class="panel" style="margin-top:12px">
 <div>
  <button class="amber" onclick="post('/api/toggle_run')" id="runbtn">⏸ 暂停</button>
  <button class="red" title="手动注入威胁词,观察警报与习惯化反应" onclick="post('/api/inject?kind=threat')">☠ 威胁风暴</button>
  <button class="gray" title="往世界模型里塞噪声,看它如何自我修复" onclick="post('/api/inject?kind=noise')">░ 模型毒化</button>
  <button class="green" title="临时提高几个候选链接的吸引力" onclick="post('/api/inject?kind=novelty')">✦ 加热候选</button>
  <button class="purple" title="存档当前全部内心状态" onclick="post('/api/snapshot')">📸 快照</button>
  <button class="purple" title="回到快照时刻:记忆还在,中间的经历等于没发生过" onclick="post('/api/rollback')">↩ 回滚(读档)</button>
  <button class="red" onclick="post('/api/kill')">⏹ 处刑·化石</button>
  <button class="green" onclick="post('/api/reset')">⟳ 新生(全新)</button>
  <button class="green" title="后代继承上一只的荣光词频残迹(20%变异)" onclick="post('/api/reset?inherit=1')">🌱 新生(继承化石)</button>
  <button class="blue" title="同一套基因种子再孵一只,看它们怎么分化" onclick="post('/api/spawn')">🐣 孪生</button>
  <span style="margin-left:10px"><input id="newurl" placeholder="https://… 给它注入一块新大陆">
  <button class="green" onclick="addSite()">➕ 加入世界</button></span>
  <button class="red" onclick="if(confirm('结束本次实验?将归档 run_*.json,关闭所有端口并退出进程。'))post('/api/shutdown')">⛔ 结束本次实验</button>
 </div>
 <div id="log"></div>
</div>
<div class="tomb" id="tomb"><div class="card">
 <span class="x" onclick="document.getElementById('tomb').style.display='none'">✕</span>
 <h2>☠ 死亡</h2>
 <p id="cause" style="margin:4px 0"></p>
 <p style="font-size:12px;color:#94a3b8;margin:4px 0">宿主还在燃烧,但已不为它烧。UI可继续浏览;化石与荣光档案:</p>
 <p><code id="fossil" style="color:#fbbf24"></code></p>
 <button class="green" onclick="post('/api/reset')">⟳ 新生(全新)</button>
 <button class="green" onclick="post('/api/reset?inherit=1')">🌱 新生(继承化石)</button>
 <button class="red" onclick="if(confirm('结束本次实验?将归档并退出进程。'))post('/api/shutdown')">⛔ 结束本次实验</button>
</div></div>
<script>
const mc={"心流":"#22c55e","好奇":"#06b6d4","惊讶":"#f59e0b","恐惧":"#ef4444","焦虑":"#d97706",
"无聊":"#94a3b8","愤怒":"#dc2626","厌恶":"#84cc16","满足":"#34d399","悲伤":"#3b82f6",
"濒死":"#b91c1c","低耗巡航":"#94a3b8","初始化":"#94a3b8"};
const ec={"好奇":"#06b6d4","心流":"#22c55e","惊讶":"#f59e0b","恐惧":"#ef4444","焦虑":"#d97706",
"无聊":"#94a3b8","愤怒":"#dc2626","厌恶":"#84cc16","满足":"#34d399","悲伤":"#3b82f6"};
const lc={"威胁":"#ef4444","异常":"#f59e0b","状态":"#38bdf8","睡眠":"#818cf8","系统":"#94a3b8",
"快照":"#c084fc","回滚":"#e879f9","死亡":"#f87171","世界":"#64748b","盲帧":"#64748b",
"进食":"#4ade80","探索":"#34d399","生态":"#60a5fa","短路":"#fb923c","感觉":"#22d3ee",
"远征":"#2dd4bf","白日梦":"#fbbf24","念→偿":"#4ade80","念→散":"#f97316","荣光":"#fde047","情绪":"#f0abfc"};
const PORTC={5000:'#a78bfa',5001:'#fbbf24',5002:'#22d3ee',5003:'#34d399',5004:'#f472b6',5005:'#fb923c'};
const cport=p=>PORTC[p]||'#94a3b8';
const esc=s=>String(s).replace(/"/g,"&quot;").replace(/</g,"&lt;");

// ── 白话层:术语收进tooltip,门面说人话 ──
const COMP={"新奇":"新鲜感","腻":"吃腻了","信任":"老熟人","恐惧":"有点怕","厌恶":"上次很糟",
"久违":"好久没去","暗":"有前科","随机":"一时兴起","念":"梦里想要的","驻留":"正吃着这儿"};
const TAG_SAY={"基座":"出厂自带","人类注入":"人类给的","自发现":"自己发现"};
const MOOD_SAY={"心流":"正来劲","好奇":"好奇心起","惊讶":"被惊到了","恐惧":"很害怕","焦虑":"有点慌",
"无聊":"闲得发慌","愤怒":"来气","厌恶":"犯恶心","满足":"吃饱喝足挺舒坦","悲伤":"有点丧",
"濒死":"快不行了","低耗巡航":"省电巡航","初始化":"刚醒来"};
function topKey(c){let k=null,v=-1e9;for(const [a,b] of Object.entries(c||{}))if(b>v){v=b;k=a;}return k;}
function speak(d){
 if(!d.alive)return "☠ 它死了："+(d.death_cause||"原因未记录")+"。化石已存档，可以孵化下一代继承它的记忆残迹。";
 if(d.paused)return "⏸ 时间被冻结了——它保持原样，一无所知。";
 if(d.sleeping)return "💤 第"+d.day+"夜：白天的见闻正在回放归档"
   +(d.replay?("，刚才梦到："+Object.keys(d.replay.words).slice(0,5).join("、")):"")+"。";
 if(d.energy<25)return "🔥 饿急了：压低觅食标准先吃饱——这不是想通了，是求生开关接管了大脑。";
 const dec=d.decision||{};
 if(!dec.mode)return "刚醒来，正在打量四周……";
 if(dec.mode==="空缺")return "有点闲得慌，但渔场和候选都是空的——等它探到新地方，或你帮它注入一个。";
 const r=topKey(dec.comp);
 let s="现在"+(MOOD_SAY[d.mood]||d.mood)+"，正打算去「"+(dec.site||"野外")+"」";
 if(r)s+="（因为"+(COMP[r]||r)+"）";
 const sup=d.A-d.tau;
 s+= sup>0.25 ? "；刚发生的事比它预想的意外不少。"
   : sup<0.02 ? "；眼前的一切都在意料之中。" : "。";
 if(d.goal)s+=" 它还惦记着梦里想要的味道：「"+(d.goal.words||[]).slice(0,3).join("/")+"」。";
 return s;
}
// 注:性格分档阈值为第一版目测值,按实际分布再校准
function sayProfile(p){
 const cur=p.curiosity>1.2?"好奇心爆棚":(p.curiosity>0.9?"好奇心中等":"偏佛系");
 const st=p.stress_k>4?"神经非常敏感":(p.stress_k>2.8?"容易紧张":"心态稳");
 const lr=p.lr>0.18?"学得快、忘得快":(p.lr>0.13?"记忆节奏适中":"记得牢、更新慢");
 const bo=p.boldness>0.5?"敢冒险":"偏谨慎";
 const en=p.entropy>8?"兴趣很杂":(p.entropy>5?"兴趣较集中":"兴趣非常专一");
 return cur+"、"+st+"、"+lr+"、"+bo+"、"+en;
}
function sayGates(g){const e=Object.entries(g).sort((a,b)=>b[1]-a[1]);
 return "天生警报词(越接近1越容易受惊):"+e.slice(0,4).map(([w,v])=>w+" "+v.toFixed(2)).join(" · ");}
function sayAppetite(a){const e=Object.entries(a);if(!e.length)return "还没吃出口味经验。";
 e.sort((x,y)=>y[1]-x[1]);const hi=e[0],lo=e[e.length-1];
 let s="吃出来的口味:心情「"+hi[0]+"」时胃口最好(×"+hi[1].toFixed(2)+")";
 if(lo[1]<0.9)s+="，「"+lo[0]+"」时最没胃口(×"+lo[1].toFixed(2)+")";
 return s+"。";}
let TAU=0.6,tombClosed=false;
function drawChart(h){
 const cv=document.getElementById('chart');const w=cv.clientWidth,ht=150;
 cv.width=w*2;cv.height=ht*2;const c=cv.getContext('2d');c.scale(2,2);c.clearRect(0,0,w,ht);
 c.strokeStyle='#1e293b';c.strokeRect(0.5,0.5,w-1,ht-1);
 const N=h.length;if(!N)return;
 const x=i=>i/Math.max(N-1,1)*(w-10)+5;
 const yA=v=>ht-6-(ht-16)*Math.max(0,Math.min(1,v));
 c.lineWidth=1;c.strokeStyle='#f59e0b';c.setLineDash([5,4]);c.beginPath();let pen=false;
 for(let i=0;i<N;i++){const p=h[i];if(p[1]<0){pen=false;continue;}
  if(!pen){c.moveTo(x(i),yA(TAU));pen=true;}else c.lineTo(x(i),yA(TAU));}
 c.stroke();c.setLineDash([]);
 c.strokeStyle='#38bdf8';c.lineWidth=1.5;c.beginPath();pen=false;
 for(let i=0;i<N;i++){const p=h[i];if(p[1]<0){pen=false;continue;}
  if(!pen){c.moveTo(x(i),yA(p[1]));pen=true;}else c.lineTo(x(i),yA(p[1]));}
 c.stroke();
 c.strokeStyle='#4ade80';c.lineWidth=1.2;c.beginPath();pen=false;
 for(let i=0;i<N;i++){const p=h[i],Y=ht-6-(ht-16)*(p[2]/140);
  if(!pen){c.moveTo(x(i),Y);pen=true;}else c.lineTo(x(i),Y);}
 c.stroke();
}
async function poll(){try{
 const d=await(await fetch('/api/state')).json();TAU=d.tau;
 who.textContent=d.name+" @:"+d.port;
 lamp.style.background=d.paused?'#475569':(mc[d.mood]||"#94a3b8");
 mode.textContent="〔"+d.mode+" · "+(MOOD_SAY[d.mood]||d.mood)+"〕"+(d.paused?"〔⏸ 时间冻结〕":"");
 runbtn.textContent=d.paused?'▶ 恢复':'⏸ 暂停';
 sentence.textContent=speak(d);
 energy.textContent=d.energy;ebar.style.width=Math.max(0,Math.min(100,d.energy))+"%";
 ebar.style.background=d.energy<25?'#ef4444':'#4ade80';
 av.textContent=d.A+" / "+d.tau;rel.textContent=(d.A-d.tau).toFixed(2);
 g.textContent=d.gain;sat.textContent=(d.satiety*100).toFixed(1)+"%";
 satbar.style.width=Math.min(100,d.satiety*400)+"%";
 const tv=d.sites.reduce((a,s)=>a+(s.visits||0),0)||1;
 const top=Math.max(...d.sites.map(s=>s.visits||0),0);
 const share=top/tv;
 hhi.textContent=Math.round(share*100)+"%";
 hhi.style.color=share>0.5?'#ef4444':'#cbd5e1';
 hhi.title="偏食度:饭量的"+Math.round(share*100)+"%集中在单一站点(行话HHI="+d.hhi+",>0.25即偏食)";
 ep.textContent=d.episodes+"段 / 应验"+Math.round(d.dream_payoff*100)+"%";
 mem.textContent=d.mem_size+" / "+d.birth_words;tblind.textContent=d.total_blind;
 xpl.textContent="踩点"+d.explored+"·收编"+d.discovered;
 fr.textContent=d.frontier;eco.textContent=d.eco;exq.textContent=d.expeditions;
 serr.textContent=d.self_err+" / "+d.base_err;
 serr.style.color=d.self_err<d.base_err?'#4ade80':'#f59e0b';
 day.textContent=d.day+"天·第"+d.frame+"帧";
 deaths.textContent=d.stats.deaths;killed.textContent=d.stats.killed;
 rolls.textContent=d.stats.rollbacks;thr.textContent=d.stats.threats;
 prof.textContent=sayProfile(d.profile);
 const eb=document.getElementById('emobars');
 eb.innerHTML=Object.entries(d.emotions).map(([k,v])=>{
  const col=ec[k]||'#64748b';
  return `<div class="erow" title="${MOOD_SAY[k]||k}"><span class="el">${k}</span> ${v.toFixed(2)}
   <div class="ebar"><div style="width:${(v*100).toFixed(0)}%;background:${col}"></div></div></div>`;}).join('');
 const gb=document.getElementById('goalbanner');
 if(d.goal){gb.style.color='#fbbf24';
  gb.innerHTML=`🎯 <b>正在惦记</b>(念头强度${d.goal.strength}):想要〈${d.goal.emo.map(e=>(MOOD_SAY[e[0]]||e[0])+" "+e[1].toFixed(2)).join(' + ')}〉
   → 目标词:${d.goal.words.join('/')} → 去这找:${d.goal.host}`;}
 else{gb.style.color='#475569';gb.innerHTML='(无目标——闲得还不够久;那些美好回忆在等被再次想起)';}
 const dec=d.decision||{};
 const compStr=dec.comp?Object.entries(dec.comp).map(([k,v])=>`${COMP[k]||k}${v>=0?'+':''}${v}`).join(' '):'';
 decision.innerHTML=`<b>正在做</b>:${dec.mode||'-'} → ${dec.site||'-'} · <b>做决定时的心情</b>:${MOOD_SAY[dec.mood]||dec.mood||'-'}<br>
  <b>理由</b>:${compStr||'-'}<br><b>差点选了</b>:${(dec.top3||[]).map(t=>t[0]+'('+t[1]+')').join(' · ')}`;
 appetite.textContent=sayAppetite(d.appetite);
 sitesT.innerHTML=d.sites.map(s=>{
  const tagSay=TAG_SAY[s.tag]||(s.tag.indexOf("来自")===0?"同胞分享×½":s.tag);
  return `<tr${s.fresh?' style="box-shadow:inset 3px 0 0 #4ade80;background:rgba(74,222,128,.06)"':''}>
  <td><span class="src ${s.cls}" ${s.tag!=='基座'&&s.tag!=='—'?`style="border:1px solid ${cport(s.by_port)}"`:''}>${tagSay}</span>${s.fresh?' 🆕':''}</td>
  <td title="${esc(s.url)}${s.last&&s.last!=='—'?' · 最近一餐 '+s.last:''}">${s.human?'★':''}${s.host}</td>
  <td>${s.gain}</td><td>${s.visits}</td><td>${s.blind}</td>
  <td>${s.trust}</td><td style="color:${s.sate>0.5?'#f59e0b':'#cbd5e1'}">${s.sate}</td>
  <td class="lab" style="color:#93c5fd">${s.pal||'-'}</td>
  <td class="lab">${s.depth>1?`第${s.depth}跳←${esc(s.parent)}`:'直采'}${s.found&&s.found!=='—'?' · '+s.found:''}</td></tr>`;}).join('')
  ||'<tr><td colspan="9" class="lab">渔场空缺——等它探路,或你在下面注入一个新站点</td></tr>';
 candT.innerHTML=d.candidates.map(c=>`<tr${c.fresh?' style="box-shadow:inset 3px 0 0 #4ade80"':''}>
  <td title="${esc(c.url)}">${c.host}</td><td>${c.score}</td><td>${c.probes}/3</td><td>${c.hits}</td>
  <td class="lab">${c.depth>1?c.depth+'跳←'+esc(c.parent):'直采'} <span style="color:${cport(c.by_port)}">${esc(c.by)}</span></td></tr>`).join('')
  ||'<tr><td colspan="5" class="lab">候选池空——下一帧从页面收割链接</td></tr>';
 drawChart(d.history);
 cloud.innerHTML=d.top_words.map(t=>{
  const col=t.red?'#ef4444':(ec[t.e]||'#60a5fa');
  return `<span style="color:${col};font-size:${(12+Math.min(24,t.c/3))}px" title="学会这词时的心情:${t.e||'?'}">${t.w}</span>`;}).join('');
 gates.textContent=sayGates(d.gates);
 twins.innerHTML=(d.twins||[]).length?d.twins.map(t=>`
  <div class="tw"><div>
   <b style="color:${cport(t.port)}">${t.name}</b>
   <a href="http://127.0.0.1:${t.port}" target="_blank" style="color:#93c5fd">打开它的生命页:${t.port} ↗</a>
   ${t.alive?((t.paused?"⏸ ":"")+(t.sleeping?"🌙 ":"")+(MOOD_SAY[t.mood]||t.mood)+" 能量"+t.energy+" 意外"+t.A+"·适应线"+t.tau):("☠ "+t.death_cause)}
  </div>
  <div class="lab">第${t.day}天#${t.frame} · 饱${t.satiety} · 渔场${t.sites}·候选${t.frontier}
   · 踩点${t.explored}·收编${t.discovered}·远征${t.expeditions}·梦${t.dreams}·回忆${t.episodes}
   · 好奇${t.curiosity} · 食谱重合<b style="color:${t.jaccard<0.4?'#4ade80':'#f59e0b'}">${t.jaccard}</b>(越低越分化)</div>
  <div style="font-family:Consolas,monospace;font-size:11px;margin-top:3px">
   ${t.log_tail.map(e=>`<div style="color:${lc[e.type]||'#94a3b8'}">[${e.t}] ${e.type}·${e.msg}</div>`).join('')}</div></div>`).join('')
  :'<span class="lab">还没有孪生同胞——点 🐣 孵化一只(同源基因,不同的命)</span>';
 ledger.innerHTML=(d.ledger||[]).map(e=>`<div style="color:#34d399;padding:1px 0">[${e.t}] <span style="color:${cport(e.port)}">${esc(e.by)}@:${e.port}</span> 自 <b>${esc(e.parent)}</b> 第${e.depth}跳 → 收编 <span title="${esc(e.url)}">${esc(e.host)}</span> (新鲜度${e.avg_gain})</div>`).join('')
  ||'<span class="lab">还没有收编记录——等第一次远征成功</span>';
 log.innerHTML=d.log.map(e=>`<div style="color:${lc[e.type]||'#94a3b8'}">[${e.t}] ${e.type} · ${e.msg}</div>`).join('');
 log.scrollTop=log.scrollHeight;
 if(!d.alive){if(!tombClosed){tomb.style.display='block';}
  cause.textContent=d.death_cause;fossil.textContent=d.fossil;}
 else{tomb.style.display='none';tombClosed=false;}
 document.title=d.alive?("["+d.name+"] "+(MOOD_SAY[d.mood]||d.mood)+" 能量"+d.energy+" ·:"+d.port):("["+d.name+"] ☠");
}catch(e){}}
async function addSite(){const u=document.getElementById('newurl').value.trim();
 if(!u)return;await fetch('/api/add_site?url='+encodeURIComponent(u),{method:'POST'});
 document.getElementById('newurl').value='';setTimeout(poll,300);}
function post(u){fetch(u,{method:'POST'}).then(()=>setTimeout(poll,300));}
setInterval(poll,1000);poll();
</script></body></html>"""

def make_app(agent):
    app=Flask("edf_%s"%agent.g.name)

    @app.route("/")
    def index(): return Response(HTML,mimetype="text/html")

    @app.route("/api/state")
    def api_state():
        # ①自身:只拿自己的锁,做完就放——任何时刻最多持一把agent锁
        with agent.lock:
            top=[]
            for w,c in agent.disp.most_common(30):
                wb=agent.word_birth.get(w)
                e_dom=max(wb["e"].items(),key=lambda kv:kv[1])[0] if wb and wb.get("e") else ""
                top.append({"w":w,"c":round(c,1),
                            "red":w in agent.gate and agent.gate[w]>0.5,"e":e_dom})
            ms=lambda d:round(sum(d)/len(d),3) if d else 0.0
            mean_gate=sum(agent.gate.values())/max(1,len(agent.gate))
            tot_v=sum(pr.visits for pr in agent.profiles.values()) or 1
            hhi=round(sum((pr.visits/tot_v)**2 for pr in agent.profiles.values()),3)
            me_hosts=set(host_of(u) for u in agent.order)
            goal_out=None
            if agent.goal:
                goal_out={"host":agent.goal["host"],"strength":round(agent.goal["strength"],2),
                          "emo":[(k,round(v,2)) for k,v in _top(agent.goal["emo"],3)],
                          "words":list(agent.goal["words"])[:6]}
            payload={
                "name":agent.g.name,"port":agent.port,
                "alive":not agent.dead,"paused":not RUN["on"],"sleeping":agent.sleeping,
                "mode":("野外·"+agent.current) if H.REAL else "实验室·仿真",
                "frame":agent.frame,"day":agent.day,
                "mood":agent.mood,"energy":round(agent.energy,1),
                "A":round(agent.last_A,3),"tau":round(agent.tau,3),
                "gain":round(agent.last_gain,2),"satiety":round(agent.satiety,3),
                "total_blind":agent.total_blind,
                "mem_size":len(agent.disp),"birth_words":len(agent.word_birth),
                "self_err":ms(agent.err_self),"base_err":ms(agent.err_base),
                "gates":{w:round(g,2) for w,g in agent.gate.items()},
                "top_words":top,"sites":agent._site_view(),
                "candidates":agent._frontier_view(),
                "expeditions":agent.expeditions,
                "ledger":list(ECO.discoveries)[-25:][::-1],
                "frontier":len(agent.frontier),"eco":len(ECO.data),
                "explored":agent.stats["explored"],"discovered":agent.stats["discovered"],
                "history":list(agent.A_hist)[-240:],"log":list(agent.log)[-120:],
                "replay":agent.replay_last,"stats":agent.stats,
                "emotions":{k:round(v,3) for k,v in agent.em.l.items()},
                "appetite":{k:round(v,2) for k,v in agent.em.appetite.items()},
                "decision":agent.last_decision,
                "goal":goal_out,"episodes":len(agent.episodes),
                "dream_payoff":round(agent.dream_payoff,2),"hhi":hhi,
                "death_cause":agent.death_cause,"fossil":agent.fossil_file,
                "profile":{"curiosity":agent.g.CURIOSITY,"stress_k":agent.g.STRESS_K,
                           "lr":agent.g.LR,"boldness":round(1-mean_gate,2),
                           "entropy":round(entropy(agent.P),1)}}
        # ②孪生:自身锁已释放;逐个短暂拿锁,任何时刻最多持一把agent锁
        with INSTANCES_LOCK:
            others=[it["agent"] for it in INSTANCES.values() if it["agent"] is not agent]
        twins_out=[]
        for a2 in others:
            s2=_agent_snapshot(a2)
            h2=set(s2.pop("order_hosts"))
            jac=(len(me_hosts&h2)/len(me_hosts|h2)) if (me_hosts or h2) else 0.0
            s2["jaccard"]=round(jac,2); s2["paused"]=not RUN["on"]
            twins_out.append(s2)
        twins_out.sort(key=lambda x:x["port"])
        payload["twins"]=twins_out
        return jsonify(payload)

    @app.route("/api/inject",methods=["POST"])
    def api_inject():
        kind=request.args.get("kind","novelty")
        with agent.lock:
            if kind=="threat": agent.pending_storm=random.sample(THREATS,3)
            elif kind=="noise":
                for _ in range(300): agent.P[random.randrange(NB)]+=2.0
                norm(agent.P); agent.log_add("世界","世界模型被毒化(看它怎么自我修复)")
            elif kind=="novelty":
                if not H.REAL:
                    agent.world.burst[random.choice(list(SimWorld.TOPICS))]=80
                    agent.log_add("世界","实验室新奇注入")
                else:
                    n=0
                    for u in list(agent.frontier):
                        if n>=3: break
                        agent.frontier[u]["score"]*=1.5; n+=1
                    agent.log_add("世界","实验者加热了%d个候选链接(好奇心外挂)"%n)
        return jsonify(ok=True)

    @app.route("/api/toggle_run",methods=["POST"])
    def api_toggle_run():
        RUN["on"]=not RUN["on"]
        with agent.lock:
            agent.log_add("系统","人类按下了%s键 → %s"%("恢复" if RUN["on"] else "暂停",
                         "世界重新流动" if RUN["on"] else "时间冻结(所有个体全体静止)"))
        return jsonify(ok=True,on=RUN["on"])

    @app.route("/api/add_site",methods=["POST"])
    def api_add_site():
        url=(request.args.get("url") or "").strip()
        if not re.match(r"^https?://\S+$",url):
            return jsonify(ok=False,err="需完整 http(s):// 地址")
        with agent.lock:
            if host_of(url) in {host_of(x) for x in agent.order}:
                return jsonify(ok=False,err="该主机已在渔场")
            ECO.register(url,"人类注入",avg_gain=0.3,port=0)
            agent._add_site(url,"人类注入",False,
                            meta={"found_at":time.time(),"parent":"","depth":1,
                                  "by_port":0,"seed":0.3})
        return jsonify(ok=True)

    @app.route("/api/snapshot",methods=["POST"])
    def api_snapshot():
        with agent.lock:
            snap={"P":list(agent.P),"gate":dict(agent.gate),"I_w":list(agent.I_w),
                  "energy":agent.energy,"tau":agent.tau,"last_A":agent.last_A,
                  "disp":copy.deepcopy(agent.disp),
                  "appetite":dict(agent.em.appetite),
                  "episodes":copy.deepcopy(agent.episodes),
                  "frame_at":agent.frame,"day":agent.day,
                  "top_words":[(w,round(c,1)) for w,c in agent.disp.most_common(40)],
                  "site":agent.current,"saved_at":datetime.now().isoformat()}
            agent.snapshot=snap
            fname="snap_%s_%s.json"%(agent.g.name,datetime.now().strftime("%H%M%S"))
            try:
                with open(fname,"w",encoding="utf-8") as f:
                    json.dump(snap,f,ensure_ascii=False,indent=1)
                agent.log_add("快照","快照@第%d帧 → %s"%(agent.frame,fname))
            except Exception as e:
                agent.log_add("快照","快照落盘失败:%s"%e)
        return jsonify(ok=True)

    @app.route("/api/rollback",methods=["POST"])
    def api_rollback():
        with agent.lock:
            s=agent.snapshot
            if not s or agent.dead: return jsonify(ok=False)
            agent.P=list(s["P"]); agent.gate=dict(s["gate"]); agent.I_w=list(s["I_w"])
            agent.energy=s["energy"]; agent.tau=s["tau"]; agent.last_A=s["last_A"]
            agent.disp=copy.deepcopy(s["disp"])
            agent.em.appetite.update(s.get("appetite",{}))
            agent.stats["rollbacks"]+=1
            agent.log_add("回滚","化石复活:回至第%d帧。记忆为真,经历为假——中间%d帧从未被它活过(T9)"
                          %(s["frame_at"],agent.frame-s["frame_at"]))
        return jsonify(ok=True)

    @app.route("/api/reset",methods=["POST"])
    def api_reset():
        inherit=request.args.get("inherit")=="1"
        with agent.lock:
            old=agent.g.name
            agent.rebirth(inherit=inherit)
        with INSTANCES_LOCK:
            INSTANCES.pop(old,None)
            INSTANCES[agent.g.name]={"agent":agent,"port":agent.port}
        return jsonify(ok=True,name=agent.g.name)

    @app.route("/api/kill",methods=["POST"])
    def api_kill():
        with agent.lock:
            if not agent.dead: agent.die("人为终结——实验者处刑",killed=True)
        return jsonify(ok=True)

    @app.route("/api/shutdown",methods=["POST"])
    def api_shutdown():
        agent.log_add("系统","实验者触发结束本次实验——归档、释放端口、进程退出")
        SHUTDOWN.set()
        threading.Timer(2.5,lambda:os._exit(0)).start()   # 兜底:确保退出并释放端口
        return jsonify(ok=True)

    @app.route("/api/spawn",methods=["POST"])
    def api_spawn():
        res=spawn_twin()
        if res[0] is None: return jsonify(ok=False,err=res[1])
        return jsonify(ok=True,port=res[0],name=res[1])

    return app

def find_free_port():
    for p in range(PORT_BASE,PORT_BASE+40):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
            try: s.bind(("127.0.0.1",p)); return p
            except OSError: continue
    return None

def spawn_twin():
    with INSTANCES_LOCK:
        if len(INSTANCES)>=MAX_INSTANCES: return None,"个体数已达上限(%d)"%MAX_INSTANCES
    port=find_free_port()
    if port is None: return None,"无可用端口"
    g=Genome()
    with INSTANCES_LOCK:
        if g.name in INSTANCES: g.name=g.name+"·"+str(port)
    a=Agent(g,port=port)
    threading.Thread(target=engine,args=(a,),daemon=True).start()
    srv=make_server("127.0.0.1",port,make_app(a),threaded=True)
    with SERVERS_LOCK: SERVERS.append(srv)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    with INSTANCES_LOCK:
        INSTANCES[g.name]={"agent":a,"port":port}
    a.log_add("系统","孪生出生:由实验者孵化(好奇%.2f/应激K%.1f/压缩lr%.2f)"
              %(g.CURIOSITY,g.STRESS_K,g.LR))
    print("[孪生] %s 出生 → http://127.0.0.1:%d"%(g.name,port))
    if AUTO_OPEN:
        try: webbrowser.open("http://127.0.0.1:%d"%port)
        except Exception: pass
    return port,g.name

def run_regression(frames=300, seed=7, out="regress.json"):
    """行为回归保险:实验室模式+固定种子+冻结时钟,输出确定性快照。
    用途:任何'不该改变行为'的清理/重构,前后各跑一次,两个文件必须逐位一致。
    出现任何差异=改动影响了行为,先查清再合入。"""
    import hashlib
    _real_time=time.time; _t0=_real_time()
    time.time=lambda:_t0          # 冻结时钟:保护期/黑名单/礼貌层全部走确定路径
    apply_mode(False)             # 实验室世界(SimWorld),不碰网络
    random.seed(seed)
    g=Genome(); g.name="回归"
    a=Agent(g,port=0); a.log.clear()
    for _ in range(frames):
        mode,url=a.begin_frame()
        if mode in ("site","explore","expedition"):
            raw,links,ok,info=a._fetch(url)
        else:
            raw,links,ok,info=None,[],False,"skip"
        a.complete_frame(raw,links,ok,info)
    time.time=_real_time
    snap={"A_hist":list(a.A_hist),"stats":dict(a.stats),
          "energy":a.energy,"tau":a.tau,"frame":a.frame,
          "sites":len(a.order),"frontier":len(a.frontier),
          "mood":a.mood,"P_sum":sum(a.P),
          "P_hash":hashlib.md5(str([round(x,9) for x in a.P]).encode()).hexdigest()}
    with open(out,"w",encoding="utf-8") as f:
        json.dump(snap,f,ensure_ascii=False)
    print("回归快照 → %s (frame=%d E=%.3f τ=%.4f P_hash=%s)"
          %(out,snap["frame"],snap["energy"],snap["tau"],snap["P_hash"][:12]))

def write_run_report():
    try:
        with INSTANCES_LOCK:
            agents=[it["agent"] for it in list(INSTANCES.values())]   # 先拷贝引用再放锁
        inst=[]
        for a in agents:                       # 逐个短暂拿锁,永不嵌套
            with a.lock:
                inst.append({"name":a.g.name,"port":a.port,"genome":a.g.to_dict(),
                             "stats":dict(a.stats),"expeditions":a.expeditions,
                             "dreams":a.dreams,"episodes":len(a.episodes),
                             "frames":a.frame,"alive":not a.dead,
                             "death_cause":a.death_cause,
                             "portfolio":[host_of(u) for u in a.order]})
        rep={"run":RUN_TAG,"saved_at":datetime.now().isoformat(),
             "discovered":ECO.report(),"ledger":list(ECO.discoveries),"instances":inst}
        fname="run_%s.json"%RUN_TAG
        with open(fname,"w",encoding="utf-8") as f:
            json.dump(rep,f,ensure_ascii=False,indent=1)
        print("[归档] 本轮实验记录 → %s(不影响下一轮)"%fname)
    except Exception as e:
        print("[归档] 失败:%s"%e)

if __name__=="__main__":
    import argparse
    ap=argparse.ArgumentParser(description="数字草履虫:觅食者×白日梦")
    ap.add_argument("--regress",action="store_true",
                    help="跑行为回归快照后退出(清理代码前后各跑一次,diff验证)")
    ap.add_argument("--frames",type=int,default=300)
    ap.add_argument("--seed",type=int,default=7)
    ap.add_argument("--out",default="regress.json")
    args=ap.parse_args()
    if args.regress:
        run_regression(args.frames,args.seed,args.out); sys.exit(0)
    g=Genome()
    agent=Agent(g,port=PORT_BASE)
    with INSTANCES_LOCK: INSTANCES[g.name]={"agent":agent,"port":PORT_BASE}
    threading.Thread(target=engine,args=(agent,),daemon=True).start()
    srv=make_server("127.0.0.1",PORT_BASE,make_app(agent),threaded=True)
    with SERVERS_LOCK: SERVERS.append(srv)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    print("数字草履虫 V3.8 [%s] → http://127.0.0.1:%d"%(g.name,PORT_BASE))
    if AUTO_OPEN:
        try: webbrowser.open("http://127.0.0.1:%d"%PORT_BASE)
        except Exception: pass
    print("就绪。⏸=全体时间冻结;⛔结束本次实验=归档+释放端口+退出。")
    SHUTDOWN.wait()
    time.sleep(0.8)
    write_run_report()
    print("[关机] 正在关闭 %d 个 server…"%len(SERVERS))
    with SERVERS_LOCK:
        for s in list(SERVERS):
            try: s.shutdown()
            except Exception: pass
    print("[关机] 全部 server 已关闭,端口已释放。")
    os._exit(0)
