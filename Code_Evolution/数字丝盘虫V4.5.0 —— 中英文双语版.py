#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =====================================================================
# Digital Placozoa V4.5.0 (build 47) — bilingual edition
# UI/log: zh default, EN via globe button (cookie-persisted).
# Base pools: zh+en mixed, 3+4 or 4+3 drawn per launch.
# Semantic buckets: multilingual MiniLM (EDF_STMODEL to override; changing
#   the model changes the coordinate epoch — old snapshots are refused).
# Map: zoom-to-fit hex canvas. Docs: README.md / FRAMEWORK.md.
# Run: python placozoa.py [--control] [--longevity] [--seed N]
# Env: EDF_PORT=5000  EDF_STMODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
# =====================================================================
import threading, time, json, math, random, copy, os, re, zlib, sys, glob, socket, webbrowser
import urllib.robotparser
from collections import Counter, deque, defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTO
from datetime import datetime
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup, Comment
from flask import Flask, jsonify, request, Response
from werkzeug.serving import make_server
try:
    import resource
except ImportError:
    resource=None
try:
    import numpy as np
except Exception:
    print("[fatal] numpy required: pip install numpy"); sys.exit(1)
NEURAL_OK=True
try:
    import jieba; JIEBA_OK=True
except Exception:
    jieba=None; JIEBA_OK=False
try:
    from sentence_transformers import SentenceTransformer
    ST_OK=True
except Exception:
    SentenceTransformer=None; ST_OK=False
try: sys.stdout.reconfigure(line_buffering=True)
except Exception: pass

USE_REAL_WEB=True
AUTO_OPEN=True
CHW=16384
CD={"title":0,"body":1,"nav":2,"link":3}
DIM=4*CHW
ST_MODEL=os.environ.get("EDF_STMODEL","sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
BASE_POOL_ZH=[
    "https://news.sina.com.cn","https://www.ithome.com/","http://www.news.cn/",
    "https://top.baidu.com/board?tab=realtime","https://www.thepaper.cn/",
    "http://www.people.com.cn/","https://finance.eastmoney.com/"]
BASE_POOL_EN=[
    "https://news.ycombinator.com/","https://www.bbc.com/news","https://arstechnica.com/",
    "https://www.reuters.com/","https://www.theverge.com/","https://techcrunch.com/",
    "https://www.theguardian.com/international"]
_NZ=random.choice((3,4))
BASE_SITES=random.sample(BASE_POOL_ZH,_NZ)+random.sample(BASE_POOL_EN,7-_NZ)
PORT_BASE=int(os.environ.get("EDF_PORT","5000"))
MAX_INSTANCES=8

def _seed32(port,salt): return (port*7919+salt)%(2**31-1)
def _rss_mb():
    if resource is None: return -1.0
    try:
        r=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r/1048576.0 if sys.platform=="darwin" else r/1024.0
    except Exception: return -1.0
RSS_BACKPRESSURE=3600.0
PARSE_SEM=threading.Semaphore(2)
_FETCH_POOL=ThreadPoolExecutor(max_workers=6,thread_name_prefix="fetch")
FETCH_TIMEOUT=45.0
MAX_BODY=6_000_000

# ===================== bilingual lexicon (log/UI templates) =====================
def _lx(lang,k,p):
    e=LEX.get(k)
    if e is None: return str(k)
    tpl=e[0] if lang=="zh" else e[1]
    try: return tpl.format(**(p or {}))
    except Exception: return tpl

LEX={
"born":("个体[{n}]出生:端口{pt} · LR{lr}/GAIN{gn}/应激K{sk}/好奇{cu} · 器官[{og}] · α={al} · 共振×{cg} · 语义={se} · 工资={wg}",
        "Individual [{n}] born :{pt} · LR {lr} / GAIN {gn} / stressK {sk} / curiosity {cu} · organs [{og}] · alpha {al} · coupling x{cg} · sem={se} · wage={wg}"),
"organs":("附件在线:{og}(总线15线·出生全聋)","Organs online: {og} (bus 15ch, born deaf)"),
"selftest":("器官自检 W={W} I={I} J={J} G={G}","Organ selftest W={W} I={I} J={J} G={G}"),
"no_snap":("本端口无快照——空白出生","No snapshot for this port — blank birth"),
"snap_dim":("快照坐标系不符({d0}≠{d1})——空白出生;化石词频/神经基因仍可继承","Snapshot dims mismatch ({d0}≠{d1}) — blank birth; fossil words/genes still inheritable"),
"snap_sem":("快照语义模型不符({s0}≠{s1})——拒绝载入(空白出生);以 EDF_STMODEL={s0} 重启可延续","Snapshot sem model mismatch ({s0}≠{s1}) — refused (blank birth); restart with EDF_STMODEL={s0} to resume"),
"snap_ok":("快照延续:{f} @第{fr}帧(荣光{ep}章·词{wd}·能量{en}·{og})","Snapshot resumed: {f} @frame {fr} (glory {ep}, words {wd}, energy {en}, {og})"),
"wg_restore":("词图随快照恢复:{w}词/{e}边","Word graph restored: {w} words / {e} edges"),
"organ_partial":("⚠器官键未全继承:{lst}——缺失键重新生长","⚠ organ keys partially restored: {lst} — missing keys regrow"),
"organ_file_fail":("器官档案读取失败:{e}","Organ file read failed: {e}"),
"terr_restore":("领地恢复:{s}站·候选{f}","Territory restored: {s} sites, {f} candidates"),
"terr_fail":("领地恢复失败:{e}","Territory restore failed: {e}"),
"eco_merge":("ECO总账并入{n}条","{n} ECO records merged"),
"eco_fail":("ECO并入失败:{e}","ECO merge failed: {e}"),
"dyn_fail":("动态状态恢复失败(核心已就绪):{e}","Dynamic state restore failed (core ready): {e}"),
"sem_restore":("语义字典恢复:{w}词·{c}簇","Semantic dict restored: {w} words, {c} clusters"),
"sem_restore_fail":("语义字典恢复失败:{e}","Semantic dict restore failed: {e}"),
"sem_manual":("手动载入:不恢复全局语义字典(防多胞胎竞争)","Manual load: global sem dict not restored (twin-race guard)"),
"mind_load":("心智档案载入:{f}(荣光{ep}章)","Mind file loaded: {f} (glory {ep})"),
"rebirth":("个体[{n}]新生:端口{pt} · {src}","Individual [{n}] reborn :{pt} · {src}"),
"rebirth_fg":("化石神经基因继承(±20%变异,α {a0}→{a1},共振×{cg})","fossil neural genes inherited (±20% mutation, alpha {a0}→{a1}, coupling x{cg})"),
"rebirth_fw":("化石词频残迹(±20%变异)","fossil word-frequency residue (±20%)"),
"rebirth_fresh":("全新随机抽样","fresh random draw"),
"rebirth_cross":("跨语义纪元继承:化石{f}——种子词按{c}桶重编码","Cross-epoch inheritance from fossil {f} — seeds re-bucketed into {c} buckets"),
"rebirth_live":("(前个体[{o}]未死亡即新生——词图湮灭,无化石)","(previous [{o}] reborn without death — word graph annihilated, no fossil)"),
"blackout":("⚡能量耗尽→强制休眠(夜+{nf}/帧回血)","⚡ energy depleted → forced sleep (night +{nf}/frame)"),
"pause":("时间被冻结(人类暂停)","Time frozen (paused by human)"),
"resume":("时间恢复流动","Time resumed"),
"shutdown":("实验者触发结束——归档退出","Shutdown requested — archiving, exiting"),
"twin":("孪生出生:好奇{cu}/K{sk}/α={al}/共振×{cg}","Twin born: curiosity {cu}, K {sk}, alpha {al}, coupling x{cg}"),
"mood_change":("{o} → {n} (A={a} τ={t} E={e} @{h})","{o} → {n} (A={a} tau={t} E={e} @{h})"),
"mood_tick":("{o} → {n} (E={e})","{o} → {n} (E={e})"),
"hunger_on":("饥饿短路:转向探索/回巢(饱{s} E{e})","Hunger short-circuit: explore/nest (sat {s}, E {e})"),
"hunger_off":("脱离饥饿:恢复轮牧","Hunger cleared: grazing resumed"),
"death":("{c} | 化石 → {f}","{c} | fossil → {f}"),
"dc_anxiety":("焦虑出血(威胁风暴未及习惯化)","anxiety bleed (threat storms unhabituated)"),
"dc_deficit":("代谢赤字:探索代价超过生态位产出","metabolic deficit: exploration cost exceeds niche yield"),
"dc_starve":("饥饿(生态位长期静默)","starvation (long-silent niche)"),
"dc_stress":("应激代谢失衡","stress metabolic imbalance"),
"dc_executed":("人为终结——实验者处刑","terminated by experimenter"),
"foreign_merge":("外来食源并入:{h}(来自{s},×{x})","Foreign source merged: {h} (via {s}, x{x})"),
"exp_promote":("远征链收编:{h}(前{m}餐×{x}补贴)","Expedition-chain promoted: {h} (first {m} meals x{x} bonus)"),
"world_full":("世界已满({m})且无可淘汰——{h}暂无法接纳","World full ({m}), nothing to retire — {h} rejected"),
"retire":("生态位淘汰(满员):{h}(有效分{e})","Niche retired (full): {h} (eff {e})"),
"silent_retire":("长期静默淘汰:{h}(盲{b}/访{v})","Long-silence retired: {h} (blind {b}/visits {v})"),
"clash":("撞车:{h}已被[{b}]先注册——转二手×{x}","Race lost: {h} already claimed by [{b}] — secondhand x{x}"),
"human_inject":("人类注入新大陆:{h}(★全额粮票)","Human injected a new land: {h} (full ration)"),
"heat":("加热{n}候选","Heated {n} candidates"),
"poison":("H₄污染:世界模型与附件被毒化","H4 poisoning: world model & organs corrupted"),
"new_fishery":("新渔场收编:{h}(均gain{a},复采利息{l},{d}跳←{p}{x})——署名[{n}@{pt}]","Fishery promoted: {h} (avg {a}, revisit interest {l}, {d} hops ← {p}{x}) — credit [{n}@{pt}]"),
"give_up_credit":("放弃:{h}(首采红利{f},复采利息{l}<{b})——一次性内容,冷却{c}分钟","Dropped: {h} (first-visit {f}, revisit interest {l}<{b}) — one-shot content, cooldown {c}min"),
"give_up":("放弃:{h}(均gain{a},{n}次有食)——冷却{c}分钟","Dropped: {h} (avg {a}, {n} hits) — cooldown {c}min"),
"exp_arrived":("抵达 @{h}(第{d}跳)→链{l}条,新域{n}:{ls}","Arrived @{h} (hop {d}) → {l} links, {n} new: {ls}"),
"exp_turnback":("折返 @{h}({i})","Turned back @{h} ({i})"),
"exp_bust":("{h}三次考察无果,退出候选","{h}: 3 barren probes, dropped from candidates"),
"big_bite":("高工资入账+{g}(消化{ge},腻→{s} @{h})","Big wage +{g} (digested {ge}, satiety→{s} @{h})"),
"secondhand":("二手食粮(来自{s})×{x} @{h}","Secondhand meal (via {s}) x{x} @{h}"),
"exp_bonus":("🧭开荒补贴 @{h}:前{m}餐×{x}(单列入账)","🧭 pioneer bonus @{h}: first {m} meals x{x} (ledger-only)"),
"filter":("滤除{r}(适应{a}+结构指纹{nv}词)@{h}","Filtered {r} (adapted {a} + structural {nv} words) @{h}"),
"damp":("阻尼{n}词·高频模板簇@{h}","Damped {n} words · hot template cluster @{h}"),
"entropy":("兴趣熵{e}bit——偏食预警","Interest entropy {e} bits — picky-eater warning"),
"streak":("连续{n}帧无新信息 {h}({i})","{n} blind frames {h} ({i})"),
"storm":("威胁注入:{w}(闸门重置)","Threat storm injected: {w} (gates reset)"),
"hits":("警报词命中:{w}({m})","Alarm words hit: {w} ({m})"),
"collapse":("老地方塌了({h})→悲伤+0.4,信任崩解","Old place collapsed ({h}) → sadness+0.4, trust broken"),
"dream":("白日梦(那天@{h},A={a})→想要〈{e}〉→目标词:{w}","Daydream (that day @{h}, A={a}) → wants 〈{e}〉 → target words: {w}"),
"day_end":("第{d}天结束(今日素材{h}帧→可重构{q}%·夜+{n}/帧)","Day {d} ended (today's素材 {h} frames → replayable {q}% · night +{n}/frame)"),
"replay":("夜的重放:{w}→世界模型强化","Night replay: {w} → world-model reinforced"),
"morning":("晨间复位:腻度隔夜消化","Morning reset: satiety digested overnight"),
"paid":("梦应验:词命中{k}/{n},gain={g}——目标消解(应验率→{p})","Dream fulfilled: {k}/{n} words hit, gain={g} — goal resolved (payoff→{p})"),
"fade":("目标未应验而散——《{w}…》(应验率→{p})","Goal dissolved unfulfilled — 〈{w}…〉 (payoff→{p})"),
"glory_entry":("荣光入档第{n}章 @{h} A={a} gain={g}(心情:{e})","Glory entry #{n} @{h} A={a} gain={g} (mood: {e})"),
"gene_up":("账本+{l}·相关{c}→α↑{a}","Ledger +{l}, corr {c} → alpha up {a}"),
"gene_down":("账本{l}·相关{c}→α↓{a}","Ledger {l}, corr {c} → alpha down {a}"),
"bus_flip":("总线翻转预警:{c}(仅记录)","Bus flip warning: {c} (logged only)"),
"w_removed":("W器官衰竭({e})——摘除,退回先天P","W organ failed ({e}) — removed, fallback to innate P"),
"w_eat":("W.eat失败:{e}——A1退回先天P","W.eat failed: {e} — A1 falls back to innate P"),
"w_head":("W.head_update失败:{e}","W.head_update failed: {e}"),
"w_replay":("W.replay失败:{e}","W.replay failed: {e}"),
"i_pred":("I.predict失败:{e}","I.predict failed: {e}"),
"i_learn":("I.learn失败:{e}","I.learn failed: {e}"),
"j_fail":("J失败:{e}","J failed: {e}"),
"g_score":("G.score失败(600帧内静默):{e}","G.score failed (silent 600 frames): {e}"),
"g_learn":("G.learn失败:{e}","G.learn failed: {e}"),
"g_verdict":("G.verdict失败:{e}","G.verdict failed: {e}"),
"g_bus":("G总线回写失败:{e}","G bus writeback failed: {e}"),
"thought":("思维快照失败:{e}","Thought snapshot failed: {e}"),
"snap_npz":("器官快照失败:{e}","Organ snapshot failed: {e}"),
"sem_npz":("语义字典存档失败:{e}","Sem dict snapshot failed: {e}"),
"snapshot_ok":("纪元存档@第{fr}帧 → {f}(全量;重启自动延续)","Epoch snapshot @frame {fr} → {f} (full; auto-resumed on restart)"),
"snapshot_fail":("快照失败:{e}","Snapshot failed: {e}"),
"rollback_ok":("回滚至第{fr}帧。记忆为真,经历为假;情绪/目标/词图回退","Rolled back to frame {fr}. Memory true, experience false; moods/goal/wordgraph reverted"),
"rollback_organ":("附件权重回滚:{lst}(逐键守卫)","Organ weights rolled back: {lst} (per-key guard)"),
"rollback_organ_fail":("附件回滚失败:{e}","Organ rollback failed: {e}"),
}

# ===================== semantic buckets =====================
class SemanticBucket:
    THRESH=0.50; DRIFT=0.88; CAP=40000
    MU_FREEZE=8000; CAL_STEP=2000
    def __init__(self,nb,model):
        self.nb=nb; self.lock=threading.RLock()
        self.model_name=model; self.engine=model.split("/")[-1]
        self.embedder=None; self.D=None
        if ST_OK:
            try:
                self.embedder=SentenceTransformer(model,device="cpu")
                print("[sem] embedder=%s (theta=%.2f, buckets=%d)"%(self.engine,self.THRESH,nb))
            except Exception as e:
                print("[sem] model load failed (%s) — crc32 hash fallback"%str(e)[:80])
        if self.embedder is None: self.engine="hash"
        print("[lex] jieba=%s"%("on" if JIEBA_OK else "off (bigram fallback)"))
        self.proto=None; self.count=0
        self.w2b={}; self.vec_cache={}
        self.mu=None; self.mu_n=0
        self._cal_sims=deque(maxlen=2000)
        self._next_cal=self.CAL_STEP
        self.clu_hist=deque(maxlen=24)
    def _update_mu(self,v):
        if self.mu is None:
            self.mu=np.asarray(v,dtype=np.float64).copy(); self.mu_n=1
        elif self.mu_n<self.MU_FREEZE:
            self.mu_n+=1; self.mu+=(v-self.mu)/self.mu_n
    def _center(self,v):
        if self.mu is None or self.mu_n<4: return None
        c=np.asarray(v,dtype=np.float64)-self.mu
        n=float(np.linalg.norm(c))
        if n<1e-6: return None
        return (c/n).astype(np.float32)
    def _encode(self,word):
        if not self.embedder: return None
        v=self.vec_cache.get(word)
        if v is not None: return v
        try: v=self.embedder.encode([word],normalize_embeddings=True)[0].astype(np.float32)
        except Exception: return None
        if self.D is None:
            self.D=len(v); self.proto=np.zeros((self.nb,self.D),dtype=np.float32)
        if len(self.vec_cache)>self.CAP:
            for k in list(self.vec_cache.keys())[:self.CAP//2]: self.vec_cache.pop(k,None)
        self.vec_cache[word]=v
        return v
    def _assign(self,word,v):
        cv=self._center(v) if v is not None else None
        if v is None or cv is None: b=zlib.crc32(word.encode("utf-8"))%self.nb
        elif self.count==0: self.proto[0]=cv; self.count=1; b=0
        else:
            sims=self.proto[:self.count]@cv
            best=int(np.argmax(sims)); bs=float(sims[best])
            self._cal_sims.append(bs)
            if bs>=self.THRESH:
                b=best
                if bs<0.90:
                    p=self.DRIFT*self.proto[b]+(1-self.DRIFT)*cv
                    n=float(np.linalg.norm(p))
                    if n>0: self.proto[b]=p/n
            elif self.count<self.nb: b=self.count; self.proto[b]=cv; self.count+=1
            else: b=best
        self.w2b[word]=b
        return b
    def warm(self,words):
        if not self.embedder: return
        unk=[w for w in dict.fromkeys(words) if w and w not in self.w2b and w not in self.vec_cache]
        if not unk: return
        if _rss_mb()>RSS_BACKPRESSURE: return
        try: vecs=self.embedder.encode(unk,normalize_embeddings=True,batch_size=16)
        except Exception: return
        with self.lock:
            for w,v in zip(unk,vecs):
                v=np.asarray(v,dtype=np.float32)
                self.vec_cache[w]=v
                if self.D is None:
                    self.D=len(v); self.proto=np.zeros((self.nb,self.D),dtype=np.float32)
                self._update_mu(v)
            for w in unk:
                if w not in self.w2b: self._assign(w,self.vec_cache[w])
            if len(self.vec_cache)>self.CAP:
                for k in list(self.vec_cache.keys())[:self.CAP//2]: self.vec_cache.pop(k,None)
        self._calibrate()
    def bucket(self,word):
        b=self.w2b.get(word)
        if b is not None: return b
        v=self._encode(word)
        with self.lock:
            b=self.w2b.get(word)
            if b is not None: return b
            if v is not None: self._update_mu(v)
            b=self._assign(word,v)
        self._calibrate()
        return b
    def _calibrate(self):
        n=len(self.w2b)
        if n<self._next_cal: return
        self._next_cal=n+self.CAL_STEP
        ratio=self.count/max(1,n)
        if self.count>=int(self.nb*0.95):
            if self.THRESH<0.62: self.THRESH=min(0.62,self.THRESH+0.02)
        elif ratio>0.80:
            self.THRESH=max(0.45,self.THRESH-0.05)
        elif ratio<0.03 and n>500:
            self.THRESH=min(0.60,self.THRESH+0.05)
        print("[sem-cal@%dw] clusters=%d ratio=%.2f theta=%.2f"%(n,self.count,ratio,self.THRESH))
    def note_meal(self,chc):
        c=Counter()
        for ch in ("title","body","nav"):
            for w,k in chc.get(ch,{}).items():
                b=self.w2b.get(w)
                if b is not None: c[b]+=k
        self.clu_hist.append(c)
    def damp(self,chc,factor=0.1):
        n=len(self.clu_hist)
        if n<6: return chc,0
        cur={self.w2b.get(w,-1) for ch in ("title","body","nav") for w in chc.get(ch,{})}
        cur.discard(-1)
        tots=[sum(c.values()) or 1 for c in self.clu_hist]
        hot=set()
        for b in cur:
            meals=0; share=0.0
            for c,t in zip(self.clu_hist,tots):
                if b in c: meals+=1; share+=c[b]/t
            if meals/n>=0.8 and share/max(1,meals)>=0.12: hot.add(b)
        if not hot: return chc,0
        out={}; damped=0
        for ch,cnt in chc.items():
            oc=Counter()
            for w,k in cnt.items():
                if self.w2b.get(w,-1) in hot: k=k*factor; damped+=1
                if k>0: oc[w]=k
            out[ch]=oc
        return out,damped
    def stats(self):
        return {"engine":self.engine,"model":self.model_name,"clusters":self.count,
                "words":len(self.w2b),"thresh":round(self.THRESH,2),"mu":self.mu_n}

SEM=SemanticBucket(CHW,ST_MODEL)
_SEM_RESTORED={"done":False,"by":None}

class H:
    REAL=USE_REAL_WEB
    CREDIT_SHIFT=True
    RESONANCE=True
    MOOD_METAB=True
    WAGE_LEDGER=True
    LONGEVITY=False
    FRAME_SEC,SLEEP_SEC=6.0,0.6
    AWAKE,SLEEPN=40,10
    MAINT,BLIND_MAINT,NIGHT_FIX=0.20,0.10,0.45
    SLEEP_MAINT=NIGHT_FIX
    HARVEST_FULL=10.0
    GAIN_CAP,STRESS_CAP=0.8,2.5
    SAT_LOW,SAT_FLOOR=0.15,0.45
    EXPLORE_COST=0.10
    EXPEDITION_EVERY,EXPEDITION_COST=24,0.20
    EXPEDITION_BONUS=0.5
    EXPEDITION_BONUS_MEALS=5
    EXPEDITION_BONUS_CAP=0.4
    PROBES,PROMOTE_BAR=3,0.12
    PROMOTE_SUSTAIN=0.05
    MAX_SITES,FRONTIER_CAP=72,180
    HOST_GAP,BLACKOUT=10.0,1800.0
    PROTECT_BASE,PROTECT_WILD=14400.0,3600.0
    PROTECT_MEAL_EXT=1800.0
    BLIND_RECOVER=3
    HUNGER_RELAX=0.7
    SATE_GAIN,SATE_DECAY=0.5,0.982
    BORE_TH,BORE_HOLD=0.75,8
    HYBRID,GOAL_FADE,GOAL_BLIND=0.7,0.97,0.90
    HIT_GAIN,HIT_EMO=0.4,0.5
    GOAL_HIT_MIN=2
    DREAM_CD,FEAR_GATE=30,0.3
    PERSIST_MIND=False
    PAYOFF_INIT,EP_CAP,WORD_CAP=0.5,60,4000
    PAYOFF_CAP=0.85
    GLORY_GAIN=0.45
    NEURAL=True
    GLORY_NOVEL=True
    LED_WIN,LED_LOSS=3.0,-1.0
    LED_NEUTRAL=0.08
    LED_MISS=0.20
    LED_DECAY=0.98
    LED_RISE,LED_FALL=6.0,-3.0
    LOC_HIT,LOC_MISS=0.2,-0.5
    SELF_DECAY,SELF_RAMP,SELF_W=0.95,200.0,0.14
    ENT_WARN=6.0
    SEM_GUARD=50
    WG_K=24
    WG_PAIR_CAP=60
    WG_DECAY=0.995
    WG_MINW=0.02
    WG_TAU=0.25

def apply_mode(real):
    H.REAL=real
    H.FRAME_SEC,H.SLEEP_SEC=(6.0,0.6) if real else (0.5,0.25)
    H.AWAKE,H.SLEEPN=(40,10) if real else (140,24)
apply_mode(USE_REAL_WEB)

# canonical mood IDs (persisted in snapshots/word_birth; zh labels are display-only)
G_SEED=["model","data","chip","ai","research","code","market","community",
        "模型","算法","数据","芯片","市场","研究","科技","开源","程序","社区"]
EXTRA_SEEDS=["economy","health","education","sports","space","city",
             "经济","健康","教育","体育","航天","城市"]
THREATS=["战争","崩盘","灾难","死亡","恐慌","爆炸","地震","疫情","袭击","坠毁",
         "war","crash","disaster","death","panic","attack","earthquake","pandemic"]
STOP_ZH={"的一","一个","没有","我们","自己","他们","这个","可以","就是","不是","什么",
         "时候","现在","已经","如果","但是","还是","这些","出来","起来","以及","或者",
         "对于","通过","记者","报道","消息","时间","工作","进行","表示","相关","分钟"}
STOP_EN={"the","a","an","and","or","but","of","to","in","on","for","with","at","by","from",
         "as","is","are","was","were","be","been","being","that","this","these","those",
         "it","its","have","has","had","do","does","did","will","would","can","could",
         "not","no","nor","you","your","we","our","they","their","he","she","his","her",
         "about","into","over","after","than","then","so","if","when","what","which",
         "who","whom","how","all","also","more","most","some","such","only","own","same",
         "just","now","one","two","new","out","up","down","off","again","very","says",
         "said","say","per","via","am","pm","www","com"}
STOP=STOP_ZH|STOP_EN
MOOD_BURN={"flow":0.9,"curiosity":1.3,"satisfaction":0.9,"boredom":1.0,"sadness":1.1,
           "disgust":0.9,"low_power":0.6,"dying":0.5,"anxiety":1.5,"oscillation":1.8,
           "anger":2.0,"fear":2.2,"surprise":1.2,"init":1.0}
MOOD_MIG={"好奇":"curiosity","心流":"flow","惊讶":"surprise","恐惧":"fear","焦虑":"anxiety",
          "无聊":"boredom","愤怒":"anger","厌恶":"disgust","满足":"satisfaction","悲伤":"sadness",
          "濒死":"dying","低耗巡航":"low_power","震荡":"oscillation","初始化":"init","睡眠":"sleeping"}
THREAT_MIG={w:w for w in THREATS}
THREAT_MIG.update({"崩盘":"崩盘","疫情":"疫情"})  # zh set unchanged; en additions are new keys

def bch(w,ch): return CD[ch]*CHW+(SEM.bucket(w))
def host_of(u):
    try: return urlparse(u).netloc.split(":")[0] or str(u)[:24]
    except Exception: return str(u)[:24]
def host_ok(host):
    h=(host or "").lower()
    if not h or h=="localhost" or h.endswith(".local"): return False
    if re.match(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.|169\.254\.)",h): return False
    return True
def norm_np(p):
    s=float(p.sum())
    if s>0: p/=s
def js_np(p,q):
    m=0.5*(p+q)
    kl=lambda a,b: float(np.sum(a[a>1e-12]*np.log2(a[a>1e-12]/b[a>1e-12])))
    return 0.5*(kl(p,m)+kl(q,m))
def learn_mc(P,chc,lr):
    v=np.zeros(DIM); tot=0.0
    for ch in ("title","body","nav"):
        for w,c in chc.get(ch,{}).items():
            v[bch(w,ch)]+=c; tot+=c
    if tot<=0: return
    nz=v>0
    P[nz]=(1-lr)*P[nz]+lr*(v[nz]/tot)
    norm_np(P)
def _top(d,n): return sorted(d.items(),key=lambda kv:-kv[1])[:n]
def _cos(a,b,keys):
    num=sum(a.get(k,0.0)*b.get(k,0.0) for k in keys)
    na=math.sqrt(sum(v*v for v in a.values())+1e-9)
    nb=math.sqrt(sum(v*v for v in b.values())+1e-9)
    return num/(na*nb)
def entropy_np(p):
    x=p[p>1e-12]
    return float(-np.sum(x*np.log2(x)))

# ===================== hex spiral (display layer) =====================
_HEX_DIRS=[(1,0),(1,-1),(0,-1),(-1,0),(-1,1),(0,1)]
def _hex_spiral(n_max=420):
    out=[(0,0)]; seen={(0,0)}; layer=[(0,0)]
    while len(out)<n_max:
        nxt=[]
        for q,r in layer:
            for dq,dr in _HEX_DIRS:
                c=(q+dq,r+dr)
                if c not in seen:
                    seen.add(c); nxt.append(c)
        out.extend(nxt); layer=nxt
    return out
HEX_SPIRAL=_hex_spiral()
ROAM0=271; ROAM_N=60   # roam band = ring 10 [271,331); territory+candidates max index 251, no overlap

_DIG5=re.compile(r"^\d{5,}$")
def _token_ok(w):
    if _DIG5.match(w): return False
    if len(w)>=12 and sum(c.isdigit() for c in w)>=len(w)*0.5: return False
    return True
def tokenize(text):
    toks=[]; t=text or ""
    for seg in re.findall(r"[\u4e00-\u9fff]+",t):
        if JIEBA_OK:
            for w in jieba.cut(seg):
                w=w.strip()
                if len(w)>=2 and w not in STOP_ZH and not re.match(r"^[A-Za-z]+$",w) and _token_ok(w):
                    toks.append(w)
        else:
            for i in range(len(seg)-1):
                bg=seg[i:i+2]
                if bg not in STOP_ZH: toks.append(bg)
    for x in re.findall(r"[A-Za-z][A-Za-z'-]{2,}",t):
        x=x.lower().strip("'-")
        if len(x)>=3 and x not in STOP_EN and _token_ok(x): toks.append(x)
    return toks

# ===================== word co-occurrence graph =====================
class WordGraph:
    def __init__(self):
        self.g={}; self.seen={}
    def observe(self,cnt,frame,lr=1.0):
        ws=[w for w,_ in _top(cnt,H.WG_PAIR_CAP)]
        if len(ws)<2: return
        self._decay()
        k2=H.WG_K*2
        for a in ws:
            ga=self.g.setdefault(a,{})
            ca=min(float(cnt[a]),30.0)
            for b in ws:
                if a==b: continue
                ga[b]=ga.get(b,0.0)+lr*min(ca,min(float(cnt[b]),30.0))
            if len(ga)>k2:
                for n,_ in sorted(ga.items(),key=lambda kv:kv[1])[:len(ga)-H.WG_K]:
                    del ga[n]
            self.seen[a]=frame
        if len(self.g)>H.WORD_CAP:
            over=len(self.g)-H.WORD_CAP
            cand=sorted(self.g.keys(),key=lambda w:(self.seen.get(w,0),len(self.g.get(w,{}))))[:over]
            for w in cand:
                self.g.pop(w,None); self.seen.pop(w,None)
    def _decay(self):
        dead=[]
        for w,ga in self.g.items():
            for n in list(ga.keys()):
                v=ga[n]*H.WG_DECAY
                if v<H.WG_MINW: del ga[n]
                else: ga[n]=v
            if not ga: dead.append(w)
        for w in dead: self.g.pop(w,None); self.seen.pop(w,None)
    def _bridge(self,w,visited):
        b=SEM.w2b.get(w)
        if b is None: return {}
        nb={}
        with SEM.lock: items=list(SEM.w2b.items())
        for w2,b2 in items:
            if b2!=b or w2==w or w2 in visited: continue
            ga=self.g.get(w2)
            if not ga: nb.setdefault(w2,0.3); continue
            nb.setdefault(w2,max(nb.get(w2,0.0),0.3))
            for n,v in ga.items():
                if n not in visited: nb[n]=max(nb.get(n,0.0),v*0.7)
        return nb
    def chain(self,seed,depth=5,det=False,rng=None):
        if rng is None: rng=random
        out=[]; visited={seed}; cur=seed
        mode='bridge' if seed not in self.g else 'graph'
        for _ in range(max(0,depth)):
            nb=self.g.get(cur)
            if not nb:
                nb=self._bridge(cur,visited)
                if not nb: break
                if mode=='graph': mode='bridge'
            items=[(w,v) for w,v in sorted(nb.items(),key=lambda kv:-kv[1]) if w not in visited][:3]
            if not items: break
            if det: nxt,wgt=items[0]
            else:
                vs=np.array([v for _,v in items],dtype=float)
                z=vs/max(H.WG_TAU,1e-6); z=z-z.max()
                p=np.exp(z); s=float(p.sum())
                p=(p/s) if s>0 else np.ones(len(items))/len(items)
                r=rng.random(); cum=0.0; idx=0
                for i,pi in enumerate(p):
                    cum+=float(pi)
                    if r<=cum: idx=i; break
                else: idx=len(items)-1
                nxt,wgt=items[idx]
            out.append({"w":nxt,"wgt":round(float(wgt),3)})
            visited.add(nxt); cur=nxt
        return out,mode
    def dump(self):
        return {"g":{w:[[n,round(v,3)] for n,v in sorted(ga.items(),key=lambda kv:-kv[1])[:H.WG_K]]
                     for w,ga in self.g.items()},
                "seen":{k:int(v) for k,v in self.seen.items()}}
    def load(self,d):
        try:
            for w,arr in (d.get("g") or {}).items():
                self.g[str(w)]={str(n):float(v) for n,v in (arr or [])}
            self.seen={str(k):int(v) for k,v in (d.get("seen") or {}).items()}
        except Exception: pass
    def stats(self): return {"words":len(self.g),"edges":sum(len(ga) for ga in self.g.values())}

# ===================== organ bus =====================
NBUS=15
class OrganBus:
    CH=["w_a0","w_drop","w_graw","w_herr","i_pA","i_pDE","i_pG","i_val","i_aro",
        "j_conf","j_errA","g_net","g_corr","g_ledger","noise"]
    def __init__(self):
        self.prev=dict.fromkeys(self.CH,0.0)
        self.ema=dict.fromkeys(self.CH,0.0)
        self.hist={k:deque(maxlen=12) for k in self.CH}
    def read(self):
        v=[self.prev[k] for k in self.CH]
        v[-1]=random.uniform(-0.05,0.05)
        return np.asarray(v,dtype=np.float64)
    def writeback(self,**kw):
        for k,val in kw.items():
            if k not in self.prev or k=="noise": continue
            val=max(-1.,min(1.,float(val)))
            self.prev[k]=val; self.ema[k]=0.7*self.ema[k]+0.3*val
            self.hist[k].append(val)
    def cool(self,f=0.98):
        for k in self.prev: self.prev[k]*=f
    def hot(self):
        out=[]
        for k,h in self.hist.items():
            if len(h)>=10:
                seq=list(h)
                flips=sum(1 for a,b in zip(seq,seq[1:]) if a*b<0)
                if flips>=7: out.append(k)
        return out

class WOrgan:
    CLIP=3.0
    def __init__(self,port,g):
        self.rng=np.random.RandomState(_seed32(port,13))
        self.d_site=16; self.d_lat=16; self.d_bow=32; self.n_ctx=6
        self.din=self.d_site+self.d_lat+self.d_bow+self.n_ctx+NBUS
        self.h=64
        self.W1=self.rng.normal(0,.08,(self.din,self.h)); self.b1=np.zeros(self.h)
        self.W1[self.din-NBUS:,:]=0.0
        self.W2=np.zeros((self.h,DIM))
        self.Wemb=self.rng.normal(0,.05,(DIM,self.d_bow))
        self.head=np.zeros(self.h)
        self.site_emb={}; self.last_lat=np.zeros(self.d_lat); self.last_bow=np.zeros(self.d_bow)
        self.lr=max(.03,g.neural_lr_w*.5); self.steps=0; self.err_ema=0.5; self.head_err_ema=0.5
    def _site_vec(self,host):
        v=self.site_emb.get(host)
        if v is None:
            v=self.rng.normal(0,.3,self.d_site); self.site_emb[host]=v
        return v
    def _emb(self,S):
        idx=np.nonzero(S)[0]
        v=np.zeros(self.d_bow)
        if len(idx)>0: v=self.Wemb[idx].sum(0)/max(1.0,len(idx))
        return v,idx
    def _ctx(self,host,gap_f,threat,nov,bus_x=None):
        bx=np.zeros(NBUS) if bus_x is None else np.asarray(bus_x,dtype=np.float64)[:NBUS]
        return np.concatenate([self._site_vec(host),self.last_lat,self.last_bow,
             np.array([gap_f,threat,nov,1.0,0.0,0.0]),bx])
    def _pred(self,x,base):
        hh=np.tanh(x@self.W1+self.b1)
        corr=np.clip(hh@self.W2,-self.CLIP,self.CLIP)
        lg=base+corr; lg-=lg.max()
        e=np.exp(lg); p=e/e.sum()
        graw=float(np.clip(hh@self.head,0.0,1.0))
        return p,(x,hh),graw
    def surprise(self,S,host,base,gap_f=.5,threat=0.,nov=.5,bus_x=None):
        x=self._ctx(host,gap_f,threat,nov,bus_x)
        p,cache,graw=self._pred(x,base)
        return js_np(p,np.asarray(S)),cache,p,graw
    def eat(self,S,cache,pred,S_title):
        x,hh=cache; S=np.asarray(S)
        d=np.clip(pred-S,-.5,.5)
        cols=np.union1d(np.nonzero(S>1e-9)[0],np.argpartition(-pred,64)[:64])
        self.W2[:,cols]-=self.lr*np.outer(hh,d[cols])
        np.clip(self.W2,-self.CLIP,self.CLIP,out=self.W2)
        dh=self.W2[:,cols]@d[cols]
        dhx=dh*(1-hh*hh)
        self.W1-=self.lr*0.5*np.outer(x,dhx)
        self.b1-=self.lr*0.5*dhx
        dv=dhx[self.d_site+self.d_lat:self.d_site+self.d_lat+self.d_bow]
        _,tidx=self._emb(np.asarray(S_title))
        if len(tidx)>0: self.Wemb[tidx]-=self.lr*0.3*dv
        self.last_lat=.7*self.last_lat+.3*hh[:self.d_lat]
        self.last_bow,_=self._emb(np.asarray(S_title))
        self.err_ema=.95*self.err_ema+.05*float(np.mean(np.abs(d)))
        self.steps+=1
    def head_update(self,hh,gain_actual,lr_frac=.5):
        g_t=min(1.0,max(0.0,gain_actual)/0.8)
        graw=float(np.clip(hh@self.head,0.0,1.0))
        self.head-=self.lr*lr_frac*(graw-g_t)*hh
        np.clip(self.head,-2.0,2.0,out=self.head)
        self.head_err_ema=.95*self.head_err_ema+.05*abs(graw-g_t)
        return graw,g_t
    def replay(self,S,host,base,gain,gap_f=.5,threat=0.,nov=.5,bus_x=None):
        a0,cache,pred,graw=self.surprise(S,host,base,gap_f,threat,nov,bus_x)
        st=np.zeros(DIM); st[:CHW]=np.asarray(S)[:CHW]
        self.eat(np.asarray(S),cache,pred,st)
        self.head_update(cache[1],gain)
        return a0,graw
    def decay(self):
        self.W2*=.9995; self.head*=.999; self.b1*=.999; self.Wemb*=.9995

class IOrgan:
    DIM_IN=12+NBUS+2
    N_H=24
    I_A=0; I_EMO=slice(1,11); I_DE=11; I_G=12
    DE_LO,DE_HI=-7.0,1.0
    def __init__(self,port,lr):
        rng=np.random.RandomState(_seed32(port,7))
        self.W1=rng.normal(0,.1,(self.DIM_IN,self.N_H)); self.b1=np.zeros(self.N_H)
        self.W1[12:12+NBUS,:]=0.0
        self.W2=rng.normal(0,.1,(self.N_H,13)); self.b2=np.zeros(13)
        self.lr=lr; self.last_in=None
    def _x(self,s):
        m={"site":(1,0,0),"explore":(0,1,0),"expedition":(0,0,1)}.get(s.get("mode","site"),(1,0,0))
        base=np.array([1.,s["last_A"],s["m5"],s["energy"],s["satiety"],s["tau"],
                       max(0.,s["rel"]),s["threat"],s["nov"],m[0],m[1],m[2]])
        bv=s.get("bus")
        bx=np.asarray(bv,dtype=np.float64)[:NBUS] if bv is not None else np.zeros(NBUS)
        return np.concatenate([base,bx,np.array([float(s.get("emo_val",0.)),
                                                 float(s.get("emo_aro",0.))])])
    def _forward(self,s):
        x=self._x(s); self.last_in=x
        hh=np.tanh(x@self.W1+self.b1); o=hh@self.W2+self.b2
        pA=1/(1+math.exp(-max(-30,min(30,o[self.I_A]))))
        pE=np.clip(1/(1+np.exp(-np.clip(o[self.I_EMO],-30,30))),0,1)
        q=1/(1+math.exp(-max(-30,min(30,o[self.I_DE]))))
        pDE=self.DE_LO+(self.DE_HI-self.DE_LO)*q
        r=1/(1+math.exp(-max(-30,min(30,o[self.I_G]))))
        return x,hh,pA,pE,pDE,r,q
    def predict(self,s):
        x,hh,pA,pE,pDE,pG,q=self._forward(s)
        return pA,pE,pDE,pG
    def learn(self,s,A0,emo,dE,gain,lr=None):
        lr=lr or self.lr
        x,hh,pA,pE,pDE,pG,q=self._forward(s)
        g_t=min(1.0,max(0.0,gain)/0.8)
        tgt=np.concatenate([[A0],np.asarray(emo),
            [(max(self.DE_LO,min(self.DE_HI,dE))-self.DE_LO)/(self.DE_HI-self.DE_LO)],[g_t]])
        out=np.concatenate([[pA],pE,[q],[pG]])
        e=out-tgt; d=e.copy()
        d[self.I_EMO]*=pE*(1-pE); d[self.I_DE]*=q*(1-q); d[self.I_G]*=pG*(1-pG)
        dh=(d@self.W2.T)*(1-hh*hh)
        self.W2-=lr*np.outer(hh,d); self.b2-=lr*d
        self.W1-=lr*np.outer(x,dh); self.b1-=lr*dh
        return float(np.mean(np.abs(e))),float(abs(pA-A0)),float(abs(pG-g_t))

class JOrgan:
    DIM_IN=61
    N_H=16
    def __init__(self,port,lr):
        rng=np.random.RandomState(_seed32(port,11))
        self.W1=rng.normal(0,.1,(self.DIM_IN,self.N_H)); self.b1=np.zeros(self.N_H)
        self.W1[self.DIM_IN-NBUS:,:]=0.0
        self.W2=rng.normal(0,.1,(self.N_H,2)); self.b2=np.zeros(2)
        self.lr=lr
    def _forward(self,x):
        hh=np.tanh(x@self.W1+self.b1); o=hh@self.W2+self.b2
        return hh,1/(1+np.exp(-np.clip(o,-30,30)))
    def predict(self,x):
        _,o=self._forward(x); return float(o[0]),float(o[1])
    def learn(self,x,errA,errT,lr=None):
        lr=lr or self.lr
        hh,o=self._forward(x)
        tgt=np.array([min(1.0,errA),min(1.0,errT)])
        e=o-tgt; d=e*o*(1-o)
        dh=(d@self.W2.T)*(1-hh*hh)
        self.W2-=lr*np.outer(hh,d); self.b2-=lr*d
        self.W1-=lr*np.outer(x,dh); self.b1-=lr*dh
        return float(np.mean(np.abs(e)))

class GOrgan:
    DIM_IN=36+NBUS
    N_H=16
    def __init__(self,port,lr):
        rng=np.random.RandomState(_seed32(port,41))
        self.W1=rng.normal(0,.1,(self.DIM_IN,self.N_H)); self.b1=np.zeros(self.N_H)
        self.W1[self.DIM_IN-NBUS:,:]=0.0
        self.W2=rng.normal(0,.1,(self.N_H,1)); self.b2=np.zeros(1)
        self.lr=lr; self.hist=deque(maxlen=150); self.ledger=0.0
        self.recent=deque(maxlen=12)
    def bow(self,text):
        v=np.zeros(32)
        toks=tokenize(text or "")[:24]
        for w in toks: v[SEM.bucket(w)%32]+=1.0
        n=max(1.0,len(toks)); return v/n
    def _forward(self,x):
        hh=np.tanh(x@self.W1+self.b1)
        o=float(hh@self.W2[:,0]+self.b2[0])
        return hh,float(1/(1+math.exp(-max(-30,min(30,o)))))
    def score(self,text,parent_gain=0.2,bus_x=None):
        bx=np.zeros(NBUS) if bus_x is None else np.asarray(bus_x,dtype=np.float64)[:NBUS]
        x=np.concatenate([self.bow(text),[1.0,min(0.6,max(0.0,parent_gain)),1.0,
                          min(1.0,len(tokenize(text or ""))/12.0)],bx])
        _,r=self._forward(x); return 0.8*r
    def verdict(self,pred,actual):
        err=abs(pred-actual)
        if err<=H.LED_NEUTRAL:
            self.ledger=min(30.0,self.ledger*H.LED_DECAY+H.LED_WIN)
            return {"sym":"✓","local":H.LOC_HIT}
        if err>H.LED_MISS:
            self.ledger=max(-30.0,self.ledger*H.LED_DECAY+H.LED_LOSS)
            return {"sym":"✗","local":H.LOC_MISS}
        self.ledger*=H.LED_DECAY
        return {"sym":"·","local":0.0}
    def learn(self,text,gain,parent_gain=0.2,bus_x=None):
        bx=np.zeros(NBUS) if bus_x is None else np.asarray(bus_x,dtype=np.float64)[:NBUS]
        x=np.concatenate([self.bow(text),[1.0,min(0.6,max(0.0,parent_gain)),1.0,
                          min(1.0,len(tokenize(text or ""))/12.0)],bx])
        hh,r=self._forward(x)
        t=min(1.0,max(0.0,gain)/0.8); e=r-t
        d=np.array([e*r*(1-r)])
        dh=(d@self.W2.T)*(1-hh*hh)
        self.W2-=self.lr*np.outer(hh,d); self.b2-=self.lr*d
        self.W1-=self.lr*np.outer(x,dh); self.b1-=self.lr*dh
        self.hist.append((r,min(1.0,gain/0.8)))
    def corr(self):
        if len(self.hist)<20: return 0.0
        a=np.array([p for p,_ in self.hist]); b=np.array([g for _,g in self.hist])
        if a.std()<1e-6 or b.std()<1e-6: return 0.0
        return float(np.clip(np.corrcoef(a,b)[0,1],-1,1))

def _npz_fit(o,z,pref,attrs):
    ok=[]
    for a in attrs:
        k=pref+a
        try:
            if k not in z: continue
            if not hasattr(o,a): continue
            if getattr(z[k],"shape",None)!=getattr(o,a).shape: continue
            setattr(o,a,z[k]); ok.append(a)
        except Exception: continue
    return ok

UA_DESK={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"}
UA_MOB={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}
HONEST_UA=False
BOT_UA="PlacozoaBot/0.4 (local research demo; contact: replace-with-your-email)"
NOISE_TAGS=["script","style","noscript","iframe","svg","form","input","select","textarea","button","head","meta","link"]
STRUCT_TAGS=["nav","header","footer","menu"]
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
        if "<" in s or "{" in s: continue
        if len(re.findall(r"[\u4e00-\u9fff]",s))>=2: out.append(s)
        elif len(re.findall(r"[A-Za-z]{3,}",s))>=3: out.append(s)
    return out
_TS_RE=re.compile(r"^\d{1,3}\s*(分钟|小时|天)前$|^\d{1,3}\s*(minutes?|hours?|days?)\s*ago$",re.I)
def _clean_salvage(strings):
    out=[]
    for s in strings:
        if len(s)>60 or "\n" in s: continue
        if "//" in s or "if(" in s or "if (" in s or "function" in s: continue
        if _TS_RE.match(s.strip()): continue
        han=len(re.findall(r"[\u4e00-\u9fff]",s))
        if han>=2 and han/max(1,len(s))>=0.4: out.append(s); continue
        if han==0 and len(re.findall(r"[A-Za-z]{3,}",s))>=3: out.append(s)
    return out
def _decode_bytes(raw):
    for enc in ("utf-8","gb18030"):
        try:
            t=raw.decode(enc)
            if t.count("\ufffd")<=len(t)*0.01: return t
        except Exception: pass
    return raw.decode("utf-8",errors="replace")

class Sense:
    def __init__(self):
        self.s=requests.Session()
        self._robots={}; self._rlock=threading.Lock()
    def _allowed(self,url):
        try:
            p=urlparse(url); h=p.netloc; scheme=p.scheme or "https"
            key=(scheme,h); now=time.time()
            with self._rlock:
                ent=self._robots.get(key)
                if ent and now-ent[1]<21600: rp=ent[0]
                else:
                    rp=None
                    try:
                        r=self.s.get("%s://%s/robots.txt"%(scheme,h),timeout=(2,3))
                        if r.status_code==200 and "<html" not in r.text[:200].lower():
                            rp=urllib.robotparser.RobotFileParser()
                            rp.parse(r.text.splitlines())
                    except Exception: rp=None
                    self._robots[key]=(rp,now)
            if rp is None: return True
            return rp.can_fetch(BOT_UA if HONEST_UA else "*",url)
        except Exception: return True
    def fetch(self,url):
        try: return _FETCH_POOL.submit(self._raw_fetch,url).result(timeout=FETCH_TIMEOUT)
        except _FutTO: return None,[],False,"fetch watchdog: no response in 45s"
        except Exception as e: return None,[],False,"fetch:%s"%str(e)[:60]
    def _raw_fetch(self,url):
        try:
            if not self._allowed(url): return None,[],False,"robots.txt blocked"
            is_api=("/api/" in url) or url.lower().endswith(".json") or ("hot-board" in url)
            if HONEST_UA: self.s.headers["User-Agent"]=BOT_UA
            else: self.s.headers["User-Agent"]=UA_MOB["User-Agent"] if is_api else UA_DESK["User-Agent"]
            r=self.s.get(url,timeout=(4,8),allow_redirects=True,stream=True)
            if r.status_code!=200:
                r.close(); return None,[],False,"HTTP %d"%r.status_code
            cl=int(r.headers.get("content-length") or 0)
            if cl>MAX_BODY:
                r.close(); return None,[],False,"page too large (%dMB)"%(cl//10**6)
            chunks=[]; got=0
            try:
                for chunk in r.iter_content(65536):
                    if not chunk: continue
                    chunks.append(chunk); got+=len(chunk)
                    if got>MAX_BODY: return None,[],False,"stream over %dMB cut"%(got//10**6)
            finally: r.close()
            raw=b"".join(chunks)
            text=_decode_bytes(raw)
            with PARSE_SEM:
                ctype=r.headers.get("content-type","")
                if ("json" in ctype) or text.lstrip()[:1] in ("{","["):
                    try:
                        strs=[];_json_strings(json.loads(text),strs)
                        cnt=Counter(tokenize(" ".join(strs)))
                        if cnt: return {"title":Counter(),"body":cnt,"nav":Counter(),"link":Counter()},[],True,"json"
                    except Exception: pass
                soup=BeautifulSoup(text,"html.parser")
                t_el=soup.title.get_text(" ",strip=True) if soup.title else ""
                h_txt=" ".join(h.get_text(" ",strip=True) for h in soup.find_all(["h1","h2","h3","h4"]))
                title_cnt=Counter(tokenize(t_el)*2+tokenize(h_txt))
                nav_txt=" ".join(e.get_text(" ",strip=True) for e in soup.find_all(STRUCT_TAGS))
                nav_cnt=Counter(tokenize(nav_txt))
                scripts=" ".join(s.get_text(" ") for s in soup.find_all("script"))
                for t in soup.find_all(NOISE_TAGS+STRUCT_TAGS): t.decompose()
                for c in soup.find_all(string=lambda x:isinstance(x,Comment)): c.extract()
                sal=_clean_salvage(_script_salvage(scripts))
                body_cnt=Counter(tokenize(soup.get_text(" ")))+Counter(tokenize(" ".join(sal)))
                link_cnt=Counter(); links=[]; seen=set()
                for a in soup.find_all("a",href=True):
                    href=(a.get("href") or "").strip()
                    at=a.get_text(" ",strip=True)
                    link_cnt.update(tokenize(at)[:8])
                    if not href or href.startswith(("#","javascript","mailto:","tel:")): continue
                    u2=urljoin(r.url,href.split("#")[0])
                    if not u2.startswith(("http://","https://")): continue
                    if urlparse(u2).path.lower().endswith(ASSET_EXT): continue
                    if u2 in seen: continue
                    seen.add(u2); links.append((u2,at[:60]))
                    if len(links)>=300: break
                chc={"title":title_cnt,"body":body_cnt,"nav":nav_cnt,"link":link_cnt}
            return chc,links,True,"html"
        except Exception as e:
            return None,[],False,"%s:%s"%(type(e).__name__,str(e)[:60])
class Emotion:
    NAMES=["curiosity","flow","surprise","fear","anxiety","boredom","anger","disgust","satisfaction","sadness"]
    DECAY={"curiosity":0.90,"flow":0.95,"surprise":0.80,"fear":0.75,"anxiety":0.96,
           "boredom":0.995,"anger":0.93,"disgust":0.985,"satisfaction":0.96,"sadness":0.985}
    OPP=[("fear","satisfaction",0.5),("curiosity","fear",0.4),("boredom","surprise",0.6),
         ("anger","satisfaction",0.3),("anxiety","flow",0.3),("sadness","curiosity",0.3)]
    def __init__(self):
        self.l={n:0.0 for n in self.NAMES}
        self.appetite={n:1.0 for n in self.NAMES}
        self.label="init"
    def drive(self,name,v): self.l[name]=max(self.l[name],min(1.0,v))
    def add(self,name,v): self.l[name]=min(1.0,self.l[name]+v)
    def step(self):
        for n in self.NAMES: self.l[n]*=self.DECAY[n]
        for a,b,k in self.OPP: self.l[a]*=(1.0-k*self.l[b])
    def pick(self):
        best,bl="flow",0.12
        for n in self.NAMES:
            v=self.l[n]+(0.08 if n==self.label else 0.0)
            if v>bl: best,bl=n,v
        self.label=best; return best

def _mig_moods(d):
    """old-zh -> canonical mood ids (snapshot migration)"""
    out={}
    for k,v in (d or {}).items():
        out[MOOD_MIG.get(k,k)]=v
    return out

class SiteProfile:
    def __init__(self,seed_gain=0.2,human=0,protect=900.0):
        self.hist=deque(maxlen=8); self.gain_ema=seed_gain
        self.visits=0; self.blind=0; self.logged=False
        self.last_visit=0.0; self.last_frame=0
        self.protect_until=time.time()+protect
        self.trust=0.7 if human else 0.5
        self.fear=0.0; self.disgust=0.0; self.nov=0.5; self.sate=0.0
        self.succ=0.5; self.dark=0; self.human=human; self.palate={}
        self.nav_vocab=set(); self.nav_seen=0
        self.a0_prev=None
    def observe(self,chc,frame):
        tb=Counter(chc.get("title",Counter())); tb.update(chc.get("body",Counter()))
        self.hist.append(tb); self.visits+=1
        self.last_visit=time.time(); self.last_frame=frame
        navc=chc.get("nav",Counter())
        self.nav_seen+=1
        if self.nav_seen<=2: self.nav_vocab|=set(navc.keys())
    def stable(self):
        if len(self.hist)<3: return set()
        need=max(2,int(len(self.hist)*0.75)+1)
        pres=Counter()
        for h in self.hist:
            for w in h: pres[w]+=1
        return {w for w,n in pres.items() if n>=need}
    def food(self,chc):
        navc=chc.get("nav",Counter())
        nv_new=Counter({w:c for w,c in navc.items() if w not in self.nav_vocab})
        st=self.stable()
        ft=Counter({w:c for w,c in chc.get("title",Counter()).items() if w not in st})
        fb=Counter({w:c for w,c in chc.get("body",Counter()).items() if w not in st})
        removed=(sum(chc.get("title",Counter()).values())-sum(ft.values())) \
               +(sum(chc.get("body",Counter()).values())-sum(fb.values())) \
               +(sum(navc.values())-sum(nv_new.values()))
        food={"title":ft,"body":fb,"nav":nv_new}
        if sum(sum(c.values()) for c in food.values())==0: food=None
        return food,removed
    def note_gain(self,g): self.gain_ema=0.8*self.gain_ema+0.2*g
    def touch(self,cnt,gain,new_ratio,threat):
        self.protect_until=max(self.protect_until,time.time()+H.PROTECT_MEAL_EXT)
        self.blind=max(0,self.blind-H.BLIND_RECOVER)
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
            self.data[u]={"by":"base","t":"","avg_gain":0.0,"ok":None}
    def register(self,url,by,avg_gain=0.0,port=0,parent="",depth=1):
        with self.lock:
            key=host_of(url)
            if any(host_of(k)==key for k in self.data): return False
            self.data[url]={"by":by,"port":port,"parent":parent,"depth":depth,
                            "epoch":time.time(),
                            "t":datetime.now().isoformat(timespec="seconds"),
                            "avg_gain":round(avg_gain,3),"ok":True}
            self.discoveries.append({"t":datetime.now().strftime("%H:%M:%S"),"url":url,
                "host":host_of(url),"by":by,"port":port,
                "parent":host_of(parent) if parent else ("human" if by=="human" else "base"),
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
            return {u:dict(d) for u,d in self.data.items() if d.get("by") not in (None,"base")}
ECO=Ecosystem()
RUN_TAG=datetime.now().strftime("%Y%m%d_%H%M%S")
GLOBAL_SEED=None
RUN={"on":True}
SHUTDOWN=threading.Event()

class SimWorld:
    TOPICS={"ai":["model","algorithm","intelligence","data","learning","inference","training","robot"],
            "code":["program","code","opensource","compile","debug","release","kernel","community"],
            "mkt":["funding","market","investor","growth","valuation","startup","product"],
            "chip":["chip","hardware","fab","capacity","packaging","equipment"],
            "meta":["energy","policy","research","university","paper","patent","lab","report"]}
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
            return {"title":Counter(),"body":Counter(words),"nav":Counter(),"link":Counter()}

class Genome:
    NAMES=["Ada","Turing","Hopper","Dijkstra","Noether","Curie","Feynman","Knuth","Lovelace","Hamming"]
    def __init__(self,inherit_top=None,neural_genes=None):
        self.name=random.choice(self.NAMES)
        self.LR=round(random.uniform(0.10,0.22),3)
        self.TAU_LR=round(random.uniform(0.02,0.05),3)
        self.GAIN=round(random.uniform(2.2,3.6),2)
        self.STRESS_K=round(random.uniform(2.0,5.0),2)
        self.THREAT_COST=round(random.uniform(0.8,1.5),2)
        self.CURIOSITY=round(random.uniform(0.6,1.6),2)
        self.SECOND_HAND=0.5
        ng=neural_genes or {}
        def mut(k,lo,hi,df):
            v=ng.get(k,df)
            return round(min(hi,max(lo,v*random.uniform(0.8,1.2))),3)
        self.trust_birth=mut("trust_neural",0.05,0.9,random.uniform(0.15,0.5))
        self.trust_neural=self.trust_birth
        self.neural_lr_w=mut("neural_lr_w",0.03,0.12,random.uniform(0.04,0.10))
        self.neural_lr_i=mut("neural_lr_i",0.03,0.12,random.uniform(0.04,0.10))
        self.corr_gain=mut("corr_gain",0.6,1.5,random.uniform(0.8,1.2))
        self.coupling_gain=mut("coupling_gain",0.15,0.7,random.uniform(0.25,0.6))
        self.origin=("fossil word residue (±20%)" if inherit_top else "random from gene pool") \
                    +(" + fossil neural genes (±20%)" if neural_genes else "")
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
    def neural_dict(self):
        return {"trust_neural":self.trust_neural,"trust_birth":self.trust_birth,
                "neural_lr_w":self.neural_lr_w,"neural_lr_i":self.neural_lr_i,
                "corr_gain":self.corr_gain,"coupling_gain":self.coupling_gain}

HOST_LAST={}; HOST_LOCK=threading.Lock()
INSTANCES={}; INSTANCES_LOCK=threading.RLock()
SERVERS=[]; SERVERS_LOCK=threading.Lock()

class Agent:
    _URL_COLD=re.compile(r"(mail\.|/mail|login|signin|passport|register|landing|download\.|/download|/apk|beian|/icp)",re.I)

    FE_MOODS={
        "explore_setout":{"curiosity":0.70,"anxiety":0.15},
        "nomad_setout":{"curiosity":0.40,"satisfaction":0.30},
        "expedition_setout":{"curiosity":0.75,"anxiety":0.30},
        "meal":{"satisfaction":0.60},
        "blind_stale":{"boredom":0.60,"disgust":0.35},
        "blind_fail":{"anger":0.65,"anxiety":0.35},
        "vacancy":{"boredom":0.70},
        "home_collapsed":{"sadness":0.75,"anger":0.50},
        "threat":{"fear":0.90,"anxiety":0.60},
        "surprise":{"surprise":0.70,"anxiety":0.25},
        "expedition_bounty":{"curiosity":0.70,"satisfaction":0.55},
        "expedition_barren":{"boredom":0.45,"sadness":0.30},
        "expedition_turnback":{"anger":0.35,"boredom":0.45},
        "misstep":{"disgust":0.50,"boredom":0.30},
        "dream_fulfilled":{"satisfaction":0.95,"flow":0.60},
        "daydream":{"curiosity":0.60,"flow":0.30},
        "dream_faded":{"sadness":0.60,"boredom":0.30},
        "glory":{"satisfaction":0.60,"curiosity":0.45},
        "new_fishery":{"satisfaction":0.70,"curiosity":0.55},
        "secondhand":{"satisfaction":0.35,"disgust":0.30},
        "hunger":{"anxiety":0.70,"boredom":0.30}}
    FE_PRI=["fear","surprise","flow","satisfaction","curiosity","sadness","anger","disgust","anxiety","boredom"]

    def __init__(self,genome,port=5000,load_snap=False,allow_sem=False):
        self.lock=threading.RLock(); self.port=port
        self.world=SimWorld(); self.sense=Sense()
        self.g=None; self.stats={"lives":0}; self.dead=True
        self._init_state(genome,load_snap=load_snap,allow_sem=allow_sem)
        self.stats["lives"]=1
        alive=[n for n,o in (("W",self.organ),("I",self.iorgan),("J",self.jorgan),("G",self.gorgan)) if o]
        self.log_add("sys","born",n=self.g.name,pt=self.port,lr=self.g.LR,gn=self.g.GAIN,
                     sk=self.g.STRESS_K,cu=self.g.CURIOSITY,
                     og="/".join(alive) if alive else "-",al=self.g.trust_neural,
                     cg=self.g.coupling_gain,se=SEM.engine,
                     wg="credit-shift" if H.CREDIT_SHIFT else "legacy")

    def _init_state(self,g,load_snap=False,allow_sem=False):
        self.g=g
        self.birth_genes={"origin":g.origin,
                          "born_at":datetime.now().isoformat(timespec="seconds"),
                          "genes":{"LR":g.LR,"TAU_LR":g.TAU_LR,"GAIN":g.GAIN,
                                   "STRESS_K":g.STRESS_K,"THREAT_COST":g.THREAT_COST,
                                   "CURIOSITY":g.CURIOSITY,"trust_birth":g.trust_birth,
                                   "neural_lr_w":g.neural_lr_w,"neural_lr_i":g.neural_lr_i,
                                   "corr_gain":g.corr_gain,"coupling_gain":g.coupling_gain},
                          "seeds":list(g.seeds)}
        self.bus=OrganBus()
        self.wg=WordGraph()
        SEM.warm(list(g.seeds))
        P=np.zeros(DIM)
        for w in g.seeds: P[bch(w,"title")]+=3.0; P[bch(w,"body")]+=3.0
        norm_np(P); self.P=P
        self.gate={w:1.0 for w in THREATS}
        self.I_w=[random.uniform(-0.05,0.05) for _ in range(5)]
        self.em=Emotion()
        self.mood_frame="init"; self.mood_frame_event="init"
        self.em_f={n:0.0 for n in Emotion.NAMES}
        self.em_f_last={n:0.0 for n in Emotion.NAMES}
        self.energy=100.0; self.tau=0.6; self.frame=0; self.last_A=0.0
        self.a5=deque(maxlen=5); self.err_self=deque(maxlen=60); self.err_base=deque(maxlen=60)
        self.highA=deque(maxlen=120); self.disp=Counter()
        self.log=deque(maxlen=250); self.A_hist=deque(maxlen=600)
        self.sleeping=False; self.sleep_left=0; self.replay_last=None; self.dream_hist={}
        self.blind_streak=0; self.total_blind=0; self.last_food_hash={}; self.current="init"
        self.pending_storm=None; self.last_gain=0.0; self.satiety=0.2; self.gain_ema=0.15
        self.day=1; self.awake_frame=0
        self.day_harvest=0.0; self.harvest_ema=0.0; self.night_quality=0.0
        self.known={}; self.order=[]; self.site_idx=0; self.cur_target=None
        self.frontier={}; self.blacklist={}; self.profiles={}
        self.threat_events=0; self.dreams=0; self._short=False
        self.expeditions=0; self.hungry=False; self.threat_recent=deque(maxlen=60)
        self.goal=None; self.bored_hold=0; self.last_dream=-999
        self.dream_payoff=H.PAYOFF_INIT; self.episodes=[]; self.word_birth={}
        self.last_decision={}
        self.frame_act={"host":None,"k":"init","p":{}}
        self.dead=False; self.death_cause=""; self.fossil_file=""; self.snap_file=""
        self.snapshot=None; self.mood="init"
        self.stats={"lives":self.stats.get("lives",1),"deaths":0,"killed":0,
                    "rollbacks":0,"threats":0,"explored":0,"discovered":0,"scouted":0,
                    "blackouts":0}
        self.organ=None; self.iorgan=None; self.jorgan=None; self.gorgan=None
        self.organ_thought={}; self.ithought={}; self.jthought={}; self.gthought={}
        self.dim_label={}
        self.i_surprise=0.0; self.sim_text={}; self.meta_conf=0.5
        self.j_err_ema=0.5; self.i_errA_ema=0.3; self.i_errT_ema=0.3
        self.j_lag_errA=0.3; self.j_lag_errT=0.5; self.j_lag_surp=0.0
        self.j_errA_hist=deque(maxlen=200)
        self._g_score_broken=False; self._g_score_broken_at=-10**9
        self.curves={"w":deque(maxlen=120),"i":deque(maxlen=120),
                     "j":deque(maxlen=120),"g":deque(maxlen=120)}
        self.self_profile={"bored_ema":0.0,"explore_ratio":0.0}
        self.bus_x0=np.zeros(NBUS)
        if NEURAL_OK and getattr(H,"NEURAL",False):
            s4={n:_seed32(self.port,s) for n,s in (("W",13),("I",7),("J",11),("G",41))}
            try:
                self.organ=WOrgan(self.port,g)
            except Exception: self.organ=None
            try: self.iorgan=IOrgan(self.port,max(.03,g.neural_lr_i))
            except Exception: self.iorgan=None
            try: self.jorgan=JOrgan(self.port,max(.04,g.neural_lr_i*1.2))
            except Exception: self.jorgan=None
            try: self.gorgan=GOrgan(self.port,max(.04,g.neural_lr_i))
            except Exception: self.gorgan=None
            if self.organ or self.iorgan or self.jorgan or self.gorgan:
                self._organ_selftest()
        self._pend=("skip",None,1)
        if load_snap: self._load_snapshot(load_snap,allow_sem=allow_sem)
        self.load_mind()

    def _load_snapshot(self,mode="port",path=None,allow_sem=False):
        try:
            cands=sorted(glob.glob("snap450_%d_*.json"%self.port),key=os.path.getmtime)
            if path: cands=[path]
            if not cands:
                self.log_add("sys","no_snap"); return False
            with open(cands[-1],encoding="utf-8") as f: s=json.load(f)
            if len(s.get("P",[]))!=DIM:
                self.log_add("sys","snap_dim",d0=len(s.get("P",[])),d1=DIM); return False
            sm=s.get("sem_model")
            if sm and sm!=SEM.model_name:
                self.log_add("sys","snap_sem",s0=sm,s1=SEM.model_name); return False
            seng=s.get("sem_engine")
            if seng and seng!=SEM.engine:
                self.log_add("sys","snap_sem",s0=seng,s1=SEM.engine); return False
            if allow_sem and seng==SEM.engine and SEM.engine!="hash" and not _SEM_RESTORED["done"]:
                try:
                    with SEM.lock:
                        SEM.w2b={str(k):int(v) for k,v in (s.get("sem_w2b") or {}).items()}
                        SEM.THRESH=float(s.get("sem_thresh",SEM.THRESH))
                        _sf=s.get("sem_file")
                        if _sf and os.path.exists(_sf):
                            zz=np.load(_sf)
                            if "proto" in zz and getattr(zz["proto"],"ndim",0)==2:
                                SEM.proto=zz["proto"].astype(np.float32); SEM.D=SEM.proto.shape[1]
                            if "count" in zz: SEM.count=int(zz["count"])
                            if "mu" in zz and getattr(zz["mu"],"ndim",0)==1 and SEM.D and zz["mu"].shape[0]==SEM.D:
                                SEM.mu=zz["mu"].astype(np.float64)
                            if "mu_n" in zz: SEM.mu_n=int(zz["mu_n"])
                    _SEM_RESTORED["done"]=True; _SEM_RESTORED["by"]=self.port
                    self.log_add("sem","sem_restore",w=len(SEM.w2b),c=SEM.count)
                except Exception as e:
                    self.log_add("anomaly","sem_restore_fail",e=str(e)[:60])
            elif seng==SEM.engine and SEM.engine!="hash" and not allow_sem:
                self.log_add("sem","sem_manual")
            self.P=np.array(s["P"],dtype=np.float64); norm_np(self.P)
            self.gate={**{w:1.0 for w in THREATS},**dict(s.get("gate",{}))}
            self.I_w=list(s.get("I_w",self.I_w))
            self.tau=float(s.get("tau",self.tau)); self.last_A=float(s.get("last_A",0.0))
            self.energy=min(120.0,float(s.get("energy",100.0)))
            self.disp=Counter(s.get("disp",{}))
            self.em.appetite.update(_mig_moods(s.get("appetite",{})))
            self.episodes=s.get("episodes",[])
            for e in self.episodes:
                if isinstance(e,dict) and isinstance(e.get("emo"),dict): e["emo"]=_mig_moods(e["emo"])
            self.word_birth=dict(s.get("word_birth",{}))
            for w,wb in self.word_birth.items():
                if isinstance(wb,dict) and isinstance(wb.get("e"),dict): wb["e"]=_mig_moods(wb["e"])
            self.dream_payoff=float(s.get("dream_payoff",H.PAYOFF_INIT))
            wgd=s.get("wordgraph")
            if isinstance(wgd,dict) and wgd.get("g"):
                self.wg.load(wgd)
                self.log_add("mind","wg_restore",w=self.wg.stats()["words"],e=self.wg.stats()["edges"])
            bp=s.get("bus_prev")
            if isinstance(bp,dict):
                for k,v in bp.items():
                    if k in self.bus.prev: self.bus.prev[k]=max(-1.,min(1.,float(v)))
            be=s.get("bus_ema")
            if isinstance(be,dict):
                for k,v in be.items():
                    if k in self.bus.ema: self.bus.ema[k]=max(-1.,min(1.,float(v)))
            _of=s.get("organ_file"); _summary=[]
            if _of and os.path.exists(_of) and NEURAL_OK:
                try:
                    z=np.load(_of); _specs=[]
                    if self.organ is not None:
                        _specs.append(("W",self.organ,z,"",["W1","b1","W2","Wemb","head","last_lat","last_bow"]))
                    if self.iorgan is not None:
                        _specs.append(("I",self.iorgan,z,"i_",["W1","b1","W2","b2"]))
                    if self.jorgan is not None:
                        _specs.append(("J",self.jorgan,z,"j_",["W1","b1","W2","b2"]))
                    if self.gorgan is not None:
                        _specs.append(("G",self.gorgan,z,"g_",["W1","b1","W2","b2"]))
                    for name,o,zz,pref,attrs in _specs:
                        got=_npz_fit(o,zz,pref,attrs)
                        _summary.append((name,len(got),len(attrs)))
                        if name=="W" and len(got)==len(attrs):
                            try:
                                if "steps" in z: self.organ.steps=int(np.array(z["steps"]).reshape(-1)[0])
                                if "err_ema" in z: self.organ.err_ema=float(np.array(z["err_ema"]).reshape(-1)[0])
                                if "head_err_ema" in z: self.organ.head_err_ema=float(np.array(z["head_err_ema"]).reshape(-1)[0])
                            except Exception: pass
                    _bad=[n for n,gg,aa in _summary if gg<aa]
                    if _bad: self.log_add("sys","organ_partial",lst=",".join(_bad))
                    if self.organ is not None and isinstance(s.get("site_emb"),dict):
                        for h,v in s["site_emb"].items():
                            try:
                                if len(v)==self.organ.d_site:
                                    self.organ.site_emb[h]=np.array(v,dtype=np.float64)
                            except Exception: pass
                except Exception as e:
                    self.log_add("anomaly","organ_file_fail",e=str(e)[:60])
            _organ_note=("none" if not _summary else
                         ("full" if all(gg==aa for _,gg,aa in _summary)
                          else "partial:"+",".join("%s%d/%d"%(n,gg,aa) for n,gg,aa in _summary)))
            self.known={}; self.order=[]; self.profiles={}; self.frontier={}
            self.blacklist={}; self.last_food_hash={}; self.site_idx=0; self.cur_target=None
            st=s.get("sites") or {}
            if isinstance(st,dict) and st.get("order"):
                try:
                    kn=st.get("known") or {}; pmap=st.get("profiles") or {}
                    for u in st["order"]:
                        if u not in kn: continue
                        self.known[u]=dict(kn[u]); self.order.append(u)
                        pc=pmap.get(u) or {}
                        pr=SiteProfile(seed_gain=float(pc.get("gain_ema",0.2)),
                                       human=int(pc.get("human",0)))
                        for k in ("gain_ema","visits","blind","logged","last_visit","last_frame",
                                  "protect_until","trust","fear","disgust","nov","sate","succ",
                                  "dark","nav_seen","a0_prev"):
                            if k in pc:
                                try: setattr(pr,k,pc[k])
                                except Exception: pass
                        pr.hist=deque([Counter(c) for c in (pc.get("hist") or [])],maxlen=8)
                        pr.palate=dict(pc.get("palate") or {})
                        pr.nav_vocab=set(pc.get("nav_vocab") or [])
                        self.profiles[u]=pr
                    for u,t in (st.get("blacklist") or {}).items(): self.blacklist[u]=float(t)
                    self.last_food_hash=dict(st.get("last_food_hash") or {})
                    try: self.site_idx=int(st.get("site_idx",0))
                    except Exception: self.site_idx=0
                    if self.site_idx>=max(1,len(self.order)): self.site_idx=0
                    ct=st.get("cur_target")
                    self.cur_target=ct if (ct in self.known) else None
                    for u,d in (st.get("frontier") or {}).items():
                        try: self.frontier[u]=dict(d)
                        except Exception: pass
                    self.log_add("sys","terr_restore",s=len(self.order),f=len(self.frontier))
                except Exception as e:
                    self.log_add("anomaly","terr_fail",e=str(e)[:60])
            if allow_sem and isinstance(s.get("eco"),dict) and s["eco"]:
                try:
                    hosts={host_of(k) for k in ECO.data}; added=0
                    for u,d in s["eco"].items():
                        if u in ECO.data or host_of(u) in hosts: continue
                        ECO.data[u]=dict(d); hosts.add(host_of(u)); added+=1
                    if added: self.log_add("eco","eco_merge",n=added)
                except Exception as e: self.log_add("anomaly","eco_fail",e=str(e)[:60])
            try:
                emo=s.get("emotions")
                if isinstance(emo,dict) and emo:
                    for k,v in _mig_moods(emo).items():
                        if k in self.em.l: self.em.l[k]=max(0.0,min(1.0,float(v)))
                self.mood=str(MOOD_MIG.get(s.get("mood",self.mood),s.get("mood",self.mood)))
                self.mood_frame=str(MOOD_MIG.get(s.get("mood_frame",self.mood),
                                                 s.get("mood_frame",self.mood)))
                gl=s.get("goal")
                if isinstance(gl,dict) and gl.get("words"): self.goal=dict(gl)
                for e in (s.get("highA") or []):
                    if isinstance(e,dict): self.highA.append(dict(e))
                cy=s.get("cycle") or {}
                self.day=int(cy.get("day",self.day)); self.awake_frame=int(cy.get("awake_frame",0))
                self.sleeping=bool(cy.get("sleeping",False)); self.sleep_left=int(cy.get("sleep_left",0))
                self.day_harvest=float(cy.get("day_harvest",0.0))
                self.harvest_ema=float(cy.get("harvest_ema",0.0))
                self.night_quality=float(cy.get("night_quality",0.0))
                em_=s.get("emas") or {}
                for k in ("i_errA_ema","i_errT_ema","j_err_ema","j_lag_errA","j_lag_errT",
                          "j_lag_surp","meta_conf","gain_ema","satiety"):
                    if k in em_:
                        try: setattr(self,k,float(em_[k]))
                        except Exception: pass
                self.hungry=bool(em_.get("hungry",False))
                self.blind_streak=int(em_.get("blind_streak",0))
                self.total_blind=int(em_.get("total_blind",0))
                self.threat_events=int(em_.get("threat_events",0))
                self.dreams=int(em_.get("dreams",0)); self.expeditions=int(em_.get("expeditions",0))
            except Exception as e:
                self.log_add("anomaly","dyn_fail",e=str(e)[:60])
            self.log_add("sys","snap_ok",f=os.path.basename(cands[-1]),fr=s.get("frame_at",0),
                         ep=len(self.episodes),wd=len(self.disp),en=round(self.energy),og=_organ_note)
            return True
        except Exception as e:
            self.log_add("anomaly","dyn_fail",e=str(e)[:70]); return False

    def _organ_selftest(self):
        res={}
        if self.organ:
            try:
                rng=np.random.RandomState(999)
                S=rng.dirichlet(np.ones(DIM)*0.05).astype(np.float64)
                base=np.log(self.P+1e-9)
                bkW1,bkb1=self.organ.W1.copy(),self.organ.b1.copy()
                bkW2=self.organ.W2.copy(); bkWemb=self.organ.Wemb.copy()
                bkhead=self.organ.head.copy(); bklat=self.organ.last_lat.copy()
                bklbow=self.organ.last_bow.copy(); steps0=self.organ.steps
                h0=self.organ.head.copy()
                for i in range(50):
                    a0,cache,pred,graw=self.organ.surprise(S,"__selftest__",base,.5,0.,.5)
                    st=np.zeros(DIM); st[:CHW]=S[:CHW]
                    self.organ.eat(S,cache,pred,st)
                    self.organ.head_update(cache[1],0.6 if i%2 else 0.1)
                moved=float(np.abs(self.organ.head-h0).max())
                self.organ.W1,self.organ.b1=bkW1,bkb1
                self.organ.W2,self.organ.Wemb=bkW2,bkWemb
                self.organ.head,self.organ.last_lat=bkhead,bklat
                self.organ.last_bow=bklbow; self.organ.steps=steps0
                self.organ.head_err_ema=0.5; self.organ.err_ema=0.5
                self.organ.site_emb.pop("__selftest__",None)
                res["W"]="pass" if moved>1e-6 else "warn(%.5f)"%moved
            except Exception as ex: res["W"]="fail"
        if self.iorgan:
            try:
                bi1,bb1=self.iorgan.W1.copy(),self.iorgan.b1.copy()
                bi2,bb2=self.iorgan.W2.copy(),self.iorgan.b2.copy()
                errs=[]
                for i in range(120):
                    _s={"last_A":0.2 if i%2 else 0.8,"m5":0.2 if i%2 else 0.8,
                        "energy":0.9,"satiety":0.5,"tau":0.6,
                        "rel":0.0 if i%2 else 0.6,"threat":0,"nov":0.5,
                        "mode":"site","bus":None,"emo_val":0.,"emo_aro":0.}
                    _A=0.2 if i%2 else 0.8
                    _emo=[0.9,0,0,0,0,0,0,0,0,0] if i%2 else [0,0,0,0,0,0,0,0,0.9,0]
                    ie_,_,_=self.iorgan.learn(_s,_A,_emo,-0.5,0.6 if i%2 else 0.1)
                    errs.append(ie_)
                head_m=sum(errs[:20])/20.0; tail_m=sum(errs[-20:])/20.0
                i_drop=(head_m-tail_m)/(head_m+1e-9)
                self.iorgan.W1,self.iorgan.b1=bi1,bb1
                self.iorgan.W2,self.iorgan.b2=bi2,bb2
                self.i_errA_ema=0.3; self.i_errT_ema=0.3
                res["I"]="pass" if i_drop>0.25 else "warn(%.0f%%)"%(i_drop*100)
            except Exception: res["I"]="fail"
        if self.jorgan:
            try:
                rng=np.random.RandomState(998)
                bjW1,bjb1=self.jorgan.W1.copy(),self.jorgan.b1.copy()
                bjW2,bjb2=self.jorgan.W2.copy(),self.jorgan.b2.copy()
                xa=rng.normal(0,.3,JOrgan.DIM_IN); xb=rng.normal(1.0,.3,JOrgan.DIM_IN)
                errs=[]
                for i in range(120):
                    x=xa if i%2 else xb
                    ta,tb=(0.2,0.3) if i%2 else (0.6,0.7)
                    self.jorgan.learn(x,ta,tb)
                    p,q=self.jorgan.predict(x)
                    errs.append(abs(p-ta)+abs(q-tb))
                head_m=sum(errs[:20])/20.0; tail_m=sum(errs[-20:])/20.0
                j_drop=(head_m-tail_m)/max(1e-9,head_m)
                self.jorgan.W1,self.jorgan.b1=bjW1,bjb1
                self.jorgan.W2,self.jorgan.b2=bjW2,bjb2
                res["J"]="pass" if j_drop>0.25 else "warn(%.0f%%)"%(j_drop*100)
            except Exception: res["J"]="fail"
        if self.gorgan:
            try:
                bg1,bgb1=self.gorgan.W1.copy(),self.gorgan.b1.copy()
                bg2,bgb2=self.gorgan.W2.copy(),self.gorgan.b2.copy()
                r0=self.gorgan.score("test anchor model data algorithm")
                for i in range(60):
                    self.gorgan.learn("test anchor model data algorithm",0.6 if i%2 else 0.1)
                r1=self.gorgan.score("test anchor model data algorithm")
                self.gorgan.W1,self.gorgan.b1=bg1,bgb1
                self.gorgan.W2,self.gorgan.b2=bg2,bgb2
                self.gorgan.hist.clear(); self.gorgan.recent.clear(); self.gorgan.ledger=0.0
                res["G"]="pass" if abs(r1-r0)>1e-6 else "warn"
            except Exception: res["G"]="fail"
        self._selftest_ok=(all(v=="pass" for v in res.values()) if res else None)
        self.log_add("sys","selftest",W=res.get("W","-"),I=res.get("I","-"),
                     J=res.get("J","-"),G=res.get("G","-"))

    def _mind_file(self): return "mind450_%d.json"%self.port
    def load_mind(self):
        if not H.PERSIST_MIND: return
        for fn in (self._mind_file(),"mind445_%d.json"%self.port):
            try:
                with open(fn,encoding="utf-8") as f: d=json.load(f)
                self.em.appetite.update(_mig_moods(d.get("appetite",{})))
                self.gate.update(d.get("gates",{}))
                self.episodes=d.get("episodes",[]); self.word_birth=d.get("word_birth",{})
                self.dream_payoff=d.get("dream_payoff",H.PAYOFF_INIT)
                self.log_add("mind","mind_load",f=fn,ep=len(self.episodes))
                return
            except Exception:
                continue
    def save_mind(self):
        if not H.PERSIST_MIND: return
        try:
            with open(self._mind_file(),"w",encoding="utf-8") as f:
                json.dump({"appetite":self.em.appetite,"gates":self.gate,
                           "episodes":self.episodes,"word_birth":self.word_birth,
                           "dream_payoff":self.dream_payoff,
                           "neural":self.g.neural_dict(),
                           "saved_at":datetime.now().isoformat()},f,ensure_ascii=False,indent=1)
        except Exception: pass

    def _latest_fossil(self):
        try:
            fs=sorted(glob.glob("fossil450_*.json")+glob.glob("fossil445_*.json")
                      +glob.glob("fossil444_*.json")+glob.glob("fossil443_*.json")
                      +glob.glob("fossil442_*.json")+glob.glob("fossil441_*.json")
                      +glob.glob("fossil440_*.json"),key=os.path.getmtime)
            if not fs: return None,None
            with open(fs[-1],encoding="utf-8") as f: d=json.load(f)
            return d,os.path.basename(fs[-1])
        except Exception:
            return None,None

    def rebirth(self,inherit=False):
        with self.lock:
            was_dead=self.dead; old=getattr(self,"g",None)
            lives=self.stats.get("lives",1)+1
            fd,fname=(None,None); ng=None
            if inherit:
                fd,fname=self._latest_fossil()
                if fd: ng=fd.get("neural_genes")
            top=fd.get("glory_words") or fd.get("top_words") if fd else None
            self._init_state(Genome(inherit_top=top,neural_genes=ng))
            self.stats["lives"]=lives
            with INSTANCES_LOCK:
                if self.g.name in INSTANCES and INSTANCES[self.g.name]["agent"] is not self:
                    self.g.name=self.g.name+"·"+str(self.port)
            self.fossil_file=fname or ""
            if fname and not fname.startswith("fossil450"):
                self.log_add("sys","rebirth_cross",f=fname,c=CHW)
            if ng:
                src=_lx("zh","rebirth_fg",a0=ng.get("trust_neural",0),
                        a1=self.g.trust_neural,cg=self.g.coupling_gain)
            elif top: src=_lx("zh","rebirth_fw")
            else: src=_lx("zh","rebirth_fresh")
            self.log_add("sys","rebirth",n=self.g.name,pt=self.port,src=src)
            if not was_dead and old is not None:
                self.log_add("sys","rebirth_live",o=old.name)

    def log_add(self,typ,k,**p):
        self.log.append({"t":datetime.now().strftime("%H:%M:%S"),"type":typ,
                         "k":k,"p":{a:(round(b,3) if isinstance(b,float) else b) for a,b in p.items()}})
    def features(self,tf):
        m5=sum(self.a5)/len(self.a5) if self.a5 else 0.0
        return [1.0,self.last_A,m5,self.energy/100.0,float(tf)]
    def i_predict(self,x):
        s=sum(w*f for w,f in zip(self.I_w,x))
        return 1.0/(1.0+math.exp(-max(-30.0,min(30.0,s))))
    def i_learn(self,x,t):
        p=self.i_predict(x); e=t-p
        for k in range(5): self.I_w[k]+=0.05*e*p*(1-p)*x[k]

    def _fe(self,ev,emo):
        for k,v in emo.items():
            if k in self.em_f: self.em_f[k]=min(1.0,self.em_f[k]+float(v))
        if self.mood_frame_event in ("","init"): self.mood_frame_event=ev
        elif ev not in self.mood_frame_event: self.mood_frame_event=(self.mood_frame_event+"+"+ev)[:40]
    def _fe_flush(self):
        self.em_f_last=dict(self.em_f)
        if self.em_f and max(self.em_f.values())>=0.35:
            self.mood_frame=self._meta_label(max(self.FE_PRI,key=lambda k:self.em_f.get(k,0.0)))
        else:
            self.mood_frame=self.mood
        self.em_f={n:0.0 for n in Emotion.NAMES}

    def _meta_label(self,raw):
        if self.energy<15: return "dying"
        if self.energy<40 and self.em.l["satisfaction"]<0.3 and self.em.l["curiosity"]<0.3:
            return "low_power"
        return raw
    def _awake_burn(self):
        if not H.MOOD_METAB: return H.MAINT
        k=MOOD_BURN.get(self.mood,1.0)
        if getattr(H,"LONGEVITY",False): k=min(k,1.2)
        raw=H.MAINT*k*(1.0+max(0.0,self.energy-90)/45.0)
        return min(raw,0.95)
    def _glory_counter(self):
        c=Counter()
        for e in self.episodes:
            for w,v in e.get("words",{}).items(): c[w]+=v*e.get("weight",1.0)
        return [(w,round(v,1)) for w,v in _top(c,20)]
    def d_proxy(self):
        d=0.1
        if self.organ: d+=0.25
        if self.iorgan and self.i_errA_ema<0.12: d+=0.25
        if self.jorgan and self.j_err_ema<0.15: d+=0.25
        if self.gorgan and self.gorgan.corr()>0.15: d+=0.15
        return round(d,2)
    def _bus_read(self):
        if not getattr(H,"RESONANCE",True): return np.zeros(NBUS)
        v=self.bus.read()
        v[:NBUS-1]*=float(self.g.coupling_gain)
        return v
    def _bus_dict(self,x=None):
        v=self.bus_x0 if x is None else x
        return {k:float(v[i]) for i,k in enumerate(OrganBus.CH)}
    def _emo_summary(self):
        l=self.em.l
        pos=l["satisfaction"]+l["curiosity"]+l["flow"]
        neg=l["fear"]+l["anxiety"]+l["anger"]+l["disgust"]+l["sadness"]
        return max(-1.,min(1.,pos-neg)), min(1.,l["fear"]+l["anxiety"]+l["anger"]+l["surprise"])
    def _listen(self):
        out={}
        def rows(W1,lo,hi):
            return [round(float(v),4) for v in np.abs(W1[lo:hi,:]).mean(axis=1)]
        if self.organ is not None:
            n=self.organ.W1.shape[0]; out["W"]=rows(self.organ.W1,n-NBUS,n)
        if self.iorgan is not None:
            out["I"]=rows(self.iorgan.W1,12,12+NBUS)
        if self.jorgan is not None:
            n=self.jorgan.W1.shape[0]; out["J"]=rows(self.jorgan.W1,n-NBUS,n)
        if self.gorgan is not None:
            n=self.gorgan.W1.shape[0]; out["G"]=rows(self.gorgan.W1,n-NBUS,n)
        return out

    def die(self,cause=None,killed=False):
        if self.dead: return
        if cause is None:
            rate=self.threat_events/max(1,self.frame)
            if rate>0.15: cause="dc_anxiety"
            elif self.stats.get("explored",0)>self.frame*0.4: cause="dc_deficit"
            elif self.total_blind>self.frame*0.5: cause="dc_starve"
            else: cause="dc_stress"
        self.dead=True; self.death_cause=cause
        self.frame_act={"host":None,"k":"death","p":{"c":cause}}
        self.mood_frame="dying"; self.mood_frame_event="death"
        if killed: self.stats["killed"]+=1
        else: self.stats["deaths"]+=1
        self.fossil_file="fossil450_%s_%s.json"%(self.g.name,datetime.now().strftime("%H%M%S"))
        try:
            with open(self.fossil_file,"w",encoding="utf-8") as f:
                json.dump({"died_at":datetime.now().isoformat(),"cause":cause,"killed":killed,
                           "genome":self.g.to_dict(),"neural_genes":self.g.neural_dict(),
                           "frames":self.frame,"day":self.day,
                           "threats":self.threat_events,"blind":self.total_blind,
                           "explored":self.stats["explored"],"discovered":self.stats["discovered"],
                           "expeditions":self.expeditions,"dreams":self.dreams,
                           "episodes":len(self.episodes),
                           "emotions":{k:round(v,3) for k,v in self.em.l.items()},
                           "portfolio":[host_of(u) for u in self.order],
                           "top_words":[(w,round(c,1)) for w,c in self.disp.most_common(40)],
                           "glory_words":self._glory_counter(),
                           "organ_steps":self.organ.steps if self.organ else 0,
                           "head_err":round(self.organ.head_err_ema,4) if self.organ else None,
                           "ledger":round(self.gorgan.ledger,1) if self.gorgan else None,
                           "d_proxy":self.d_proxy(),
                           "g_corr":round(self.gorgan.corr(),3) if self.gorgan else None,
                           "bus_listen":self._listen(),
                           "log_tail":list(self.log)[-100:]},f,ensure_ascii=False,indent=1)
        except Exception: pass
        self.save_mind()
        self.log_add("death","death",c=cause,f=self.fossil_file)

    def _energy_floor(self):
        if self.energy>0: return
        if getattr(H,"LONGEVITY",False):
            self.energy=0.5
            self.sleeping=True; self.sleep_left=H.SLEEPN
            self.awake_frame=0
            self.stats["blackouts"]+=1
            self.frame_act={"host":None,"k":"sleep","p":{"why":"blackout"}}
            self.mood_frame="sleeping"; self.mood_frame_event="blackout"
            self.log_add("sys","blackout",nf=H.NIGHT_FIX)
        else:
            self.die()

    def _polite_wait(self,host):
        for _ in range(3):
            with HOST_LOCK:
                now=time.time(); last=HOST_LAST.get(host,0)
                if now-last>=H.HOST_GAP:
                    HOST_LAST[host]=now; return
                wait=H.HOST_GAP-(now-last)
            time.sleep(min(wait,5)+random.uniform(0,0.5))
        with HOST_LOCK: HOST_LAST[host]=time.time()
    def _fetch(self,url):
        if not H.REAL: return self.world.emit(),[],True,"lab"
        self._polite_wait(host_of(url))
        return self.sense.fetch(url)

    def _eco_merge(self):
        for u,d in ECO.snapshot().items():
            if d.get("ok") is False: continue
            if u in self.known or u in self.frontier: continue
            if time.time()<self.blacklist.get(u,0): continue
            if host_of(u) in {host_of(x) for x in self.known}|{host_of(x) for x in self.frontier}: continue
            by=d.get("by") or "base"
            meta=None
            if by!="base":
                meta={"found_at":d.get("epoch",time.time()),"parent":d.get("parent",""),
                      "depth":d.get("depth",1),"by_port":d.get("port",0),
                      "seed":min(0.5,d.get("avg_gain",0.2)+0.05)}
            foreign=by not in ("base","human",self.g.name)
            self._add_site(u,by,foreign,meta=meta)
    def _add_site(self,u,src,foreign,meta=None):
        if u in self.known: return
        if host_of(u) in {host_of(x) for x in self.order}: return
        if len(self.order)>=H.MAX_SITES:
            self._retire_worst()
            if len(self.order)>=H.MAX_SITES:
                if src=="human":
                    self.log_add("eco","world_full",m=H.MAX_SITES,h=host_of(u))
                return
        m=meta or {}
        human=1 if src=="human" else m.get("human",0)
        self.known[u]={"src":src,"foreign":foreign,
                       "found_at":m.get("found_at",time.time()),
                       "parent":m.get("parent",""),"depth":m.get("depth",1),
                       "by_port":m.get("by_port",self.port if src==self.g.name else 0),
                       "expedited":bool(m.get("expedited",False))}
        self.order.append(u)
        _prot=H.PROTECT_BASE if src in ("base","human") else H.PROTECT_WILD
        self.profiles.setdefault(u,SiteProfile(seed_gain=m.get("seed",0.2),human=human,protect=_prot))
        if src=="human":
            self.log_add("world","human_inject",h=host_of(u))
        elif foreign:
            self.log_add("eco","foreign_merge",h=host_of(u),s=src,x=self.g.SECOND_HAND)
        elif m.get("expedited"):
            self.log_add("eco","exp_promote",h=host_of(u),m=H.EXPEDITION_BONUS_MEALS,
                         x=1.0+H.EXPEDITION_BONUS)
    def _drop_site(self,u):
        self.known.pop(u,None); self.profiles.pop(u,None); self.last_food_hash.pop(u,None)
        if u in self.order: self.order.remove(u)
        if self.cur_target==u: self.cur_target=None
        if self.site_idx>=max(1,len(self.order)): self.site_idx=0
    def _retire_worst(self):
        now=time.time()
        elig=[u for u in self.order
              if self.profiles.get(u) and self.profiles[u].visits>=3]
        if len(elig)<4 or len(self.order)<H.MAX_SITES: return
        def _eff(pr):
            conf=min(1.0,pr.visits/10.0)
            return pr.gain_ema*conf+(1-conf)*0.3
        worst=min(elig,key=lambda u:_eff(self.profiles[u]))
        if _eff(self.profiles[worst])>=0.30: return
        self.blacklist[worst]=time.time()+3600
        self.log_add("eco","retire",h=host_of(worst),e=round(_eff(self.profiles[worst]),2))
        self._drop_site(worst)

    def _link_score(self,txt,url,known_hosts,parent_gain=None):
        p=urlparse(url)
        if not host_ok(p.netloc): return 0.0
        bg=tokenize(txt or "")
        novelty=0.45 if p.netloc not in known_hosts else 0.1
        rel=sum(float(self.P[bch(w,"body")]+self.P[bch(w,"title")]) for w in bg)
        rel=(rel/len(bg)*CHW) if bg else 0.5
        if bg:
            fresh=sum(1.0 for w in bg
                      if float(self.P[bch(w,"body")])
                         +float(self.P[bch(w,"title")])<=0.0)
            new_ratio=fresh/len(bg)
        else:
            new_ratio=0.0
        sc=self.g.CURIOSITY*(0.5*min(rel,2.5)+novelty+0.5*new_ratio)+random.uniform(0,0.1)
        if parent_gain is not None: sc*=0.5+0.8*min(parent_gain,0.6)
        if self._URL_COLD.search(url): sc*=0.3
        return sc
    def _harvest_links(self,links,parent=None,depth=1,cap=60,parent_gain=None,expedited=False):
        if not links: return
        SEM.warm([w for _u2,_t in links[:cap] for w in tokenize(_t or "")])
        known_hosts={host_of(u) for u in self.order}
        host_cnt=Counter(host_of(k) for k in self.frontier)
        _farm=Counter()
        added=0
        for u2,txt in links:
            if added>=cap: break
            if parent and _farm.get(host_of(parent),0)>=8: continue
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
                "late_wage":0.0,
                "depth":depth,"parent":host_of(parent) if parent else "",
                "pgain":round(parent_gain,3) if parent_gain is not None else None,
                "found_at":time.time(),"found_by":self.g.name,"found_port":self.port,
                "txt":txt,"net_pred":None,"local":0.0,
                "expedited":bool(expedited)}
            host_cnt[h2]+=1; added+=1
            if parent: _farm[host_of(parent)]+=1
    def _probe_result(self,u,gain):
        self.stats["explored"]+=1
        d=self.frontier.get(u)
        if not d: return
        d["probes"]+=1; d["gain"]+=gain
        if gain>0: d["hits"]+=1
        if d["probes"]>=2:
            d["late_wage"]=d.get("late_wage",0.0)+gain
        if self.gorgan:
            pg=d.get("pgain") if d.get("pgain") is not None else 0.2
            if d.get("txt"):
                try: self.gorgan.learn(d["txt"],gain,pg,self._bus_read())
                except Exception as ex: self.log_add("anomaly","g_learn",e=str(ex)[:50])
                self.curves["g"].append(round(self.gorgan.corr(),3))
            if d.get("net_pred") is not None:
                try:
                    v=self.gorgan.verdict(d["net_pred"],gain)
                    d["local"]=v["local"]
                    self.gorgan.recent.append((round(d["net_pred"],2),round(gain,2),v["sym"]))
                except Exception as ex: self.log_add("anomaly","g_verdict",e=str(ex)[:50])
            try:
                self.bus.writeback(g_net=float(np.clip((d.get("net_pred") or 0.)/0.8,0.,1.)),
                    g_corr=max(-1.,min(1.,self.gorgan.corr())),
                    g_ledger=max(-1.,min(1.,self.gorgan.ledger/30.0)))
            except Exception: pass
        if d["probes"]>=H.PROBES:
            del self.frontier[u]
            avg=d["gain"]/d["probes"]
            _ok=(avg>=H.PROMOTE_BAR and d["hits"]>=1)
            if H.CREDIT_SHIFT:
                _ok=_ok and d.get("late_wage",0)>=H.PROMOTE_SUSTAIN
            if _ok:
                reg=ECO.register(u,self.g.name,avg,port=self.port,
                                 parent=d.get("parent",""),depth=d.get("depth",1))
                if reg:
                    self._add_site(u,self.g.name,False,meta=dict(d,seed=avg))
                    self.stats["discovered"]+=1
                    self._fe("new_fishery",Agent.FE_MOODS["new_fishery"])
                    self.log_add("explore","new_fishery",h=host_of(u),a=round(avg,2),
                                 l=round(d.get("late_wage",0),2),d=d.get("depth",1),
                                 p=d.get("parent") or "base",
                                 x=" +expedition" if d.get("expedited") else "",
                                 n=self.g.name,pt=self.port)
                else:
                    eu,ed=ECO.host_entry(host_of(u))
                    by=ed.get("by") if ed else "base"
                    self._add_site(u,by,True,meta={"found_at":time.time(),
                        "parent":d.get("parent",""),"depth":d.get("depth",1),
                        "by_port":ed.get("port",0) if ed else 0,"seed":avg})
                    self._fe("secondhand",Agent.FE_MOODS["secondhand"])
                    self.log_add("eco","clash",h=host_of(u),b=by,x=self.g.SECOND_HAND)
            else:
                self.blacklist[u]=time.time()+H.BLACKOUT
                self._fe("misstep",Agent.FE_MOODS["misstep"])
                if H.CREDIT_SHIFT and d["hits"]>=1 and avg>=H.PROMOTE_BAR:
                    self.log_add("explore","give_up_credit",h=host_of(u),
                                 f=round(d["gain"]-d.get("late_wage",0),2),
                                 l=round(d.get("late_wage",0),2),b=H.PROMOTE_SUSTAIN,
                                 c=int(H.BLACKOUT/60))
                else:
                    self.log_add("explore","give_up",h=host_of(u),a=round(avg,2),
                                 n=d["hits"],c=int(H.BLACKOUT/60))

    def _pick_candidate(self):
        best=None; bs=None
        a=self.g.trust_neural
        retry_ok=(not self._g_score_broken) or (self.frame-self._g_score_broken_at>600)
        for u,d in self.frontier.items():
            if d["probes"]>=H.PROBES: continue
            if d.get("net_pred") is None and self.gorgan and retry_ok:
                pg=d.get("pgain") if d.get("pgain") is not None else 0.2
                try:
                    d["net_pred"]=round(self.gorgan.score(d.get("txt",""),pg,self.bus_x0),3)
                    self._g_score_broken=False
                except Exception as ex:
                    self._g_score_broken=True; self._g_score_broken_at=self.frame
                    self.log_add("anomaly","g_score",e=str(ex)[:50])
                    d["net_pred"]=None
            net3=min(3.0,(d.get("net_pred") or 0.2)/0.8*3.0)+d.get("local",0.0)
            final=(1-a)*d["score"]+a*net3
            k=(round(final,3),-d["probes"])
            if bs is None or k>bs: bs=k; best=u
        return best

    def _decide(self):
        em=self.em; lab=self.mood
        hunger=max(0.0,(80.0-self.energy)/80.0)
        exploit_w=0.35+0.85*hunger
        sate_relax=1.0-H.HUNGER_RELAX*hunger
        boost=1.0+0.5*(1.0-self.meta_conf)
        nov_w=0.9*boost*(1.6 if lab=="boredom" else 1.3 if lab=="curiosity"
                else 0.7 if lab in ("fear","anxiety")
                else 0.6 if lab in ("low_power","dying") else 1.0)
        fear_w=1.1*(2.2 if lab in ("fear","anxiety")
                    else 1.6 if lab in ("dying","low_power") else 1.0)
        if getattr(H,"RESONANCE",True):
            bi=self._bus_dict()
            aro_b=max(0.,min(1.,bi.get("i_aro",0.)))
            pg_b=max(0.,min(1.,bi.get("i_pG",0.)))
            pde_b=max(0.,min(1.,bi.get("i_pDE",0.5)))
            wg_b=max(0.,min(1.,bi.get("w_graw",0.5)))
            nov_w*=(0.8+0.4*pg_b)
            fear_w*=(0.9+0.5*aro_b)
        else:
            aro_b=pg_b=pde_b=0.0; wg_b=0.5
        temp=0.12*(2.5 if lab=="anger" else 1.0)
        app=em.appetite.get(lab,1.0)
        now=time.time(); rows=[]
        ramp=min(1.0,self.frame/H.SELF_RAMP)
        self_mod=H.SELF_W*ramp*(0.6*self.self_profile["bored_ema"]
                                -0.4*self.self_profile["explore_ratio"])
        for u in self.order:
            pr=self.profiles.get(u)
            if not pr or now<self.blacklist.get(u,0): continue
            gap=(self.frame-pr.last_frame)/H.AWAKE
            sate_eff=pr.sate*sate_relax
            nov_gross=nov_w*pr.nov*app
            comp={"novelty":nov_gross,"satiety":-nov_gross*sate_eff,
                  "trust":exploit_w*pr.trust,
                  "fear":-fear_w*pr.fear,"disgust":-0.8*pr.disgust,
                  "revisit":min(0.5,0.25*gap)*(0.3+0.7*pr.succ),
                  "record":-0.25*pr.dark,"random":random.uniform(0,temp)}
            if getattr(H,"RESONANCE",True):
                comp["reso_i"]=round(0.35*pg_b-0.25*aro_b+0.15*(pde_b-0.5),2)
                comp["reso_w"]=round(0.3*(wg_b-0.5),2)
            if self.goal:
                ov=sum(pr.palate.get(w,0.0) for w in self.goal["words"])
                comp["goal"]=self.goal["strength"]*min(1.2,ov+0.25*(host_of(u)==self.goal["host"]))
            if u==self.cur_target and self.cur_target:
                cs_=(self.profiles.get(self.cur_target,SiteProfile()).sate*sate_relax)
                comp["stay"]=0.18*(1.0-cs_)
            if self_mod:
                comp["self"]=-0.7*self_mod
            rows.append((sum(comp.values()),u,comp))
        rows.sort(key=lambda r:-r[0])
        site_best=rows[0] if rows else None
        cand=None
        if self.frontier and (not self.hungry or self.frame%3==0):
            cu=self._pick_candidate()
            if cu:
                d=self.frontier[cu]
                csn=min(d["score"]/3.0,1.0)
                nov_val=nov_w*app*(0.30+0.45*csn)+0.08
                netv=d.get("net_pred") or 0.2
                cv=nov_val-0.25*fear_w+random.uniform(0,temp)
                if d.get("expedited"): cv+=0.15
                if self.goal and d.get("parent")==self.goal["host"]:
                    cv+=0.3*self.goal["strength"]
                if self_mod: cv+=1.2*self_mod
                if getattr(H,"RESONANCE",True):
                    cv+=0.25*(pg_b-0.4)-0.2*aro_b+0.3*(wg_b-0.5)
                cand=(cv,cu,{"novelty":round(nov_val,2),
                             "fear":-round(0.25*fear_w,2),
                             "net_gain":round(netv,3),
                             "loc":round(d.get("local",0.0),2),
                             "alpha":round(self.g.trust_neural,2)})
                if getattr(H,"RESONANCE",True):
                    cand[2]["reso_w"]=round(0.3*(wg_b-0.5),2)
        if cand and (not site_best or cand[0]>site_best[0]):
            net_top=sorted(((min(3.0,(d.get("net_pred") or 0)/0.8*3.0)+d.get("local",0.0),u)
                            for u,d in self.frontier.items() if d["probes"]<H.PROBES))[-3:][::-1]
            return "explore",cand[1],{"mode":"explore","site":host_of(cand[1]),"mood":lab,
                                       "comp":cand[2],
                                       "hand_top":self._top3(rows,cand),
                                       "net_top":[(host_of(u),round(v,2)) for v,u in net_top],
                                       "alpha":round(self.g.trust_neural,2),
                                       "self_mod":round(self_mod,3)}
        if site_best:
            s,u,comp=site_best
            return "site",u,{"mode":"nomad","site":host_of(u),"mood":lab,
                             "comp":{k:round(v,2) for k,v in comp.items() if abs(v)>0.01},
                             "top3":self._top3(rows,cand),
                             "self_mod":round(self_mod,3)}
        return "none",None,{"mode":"vacancy","self_mod":round(self_mod,3)}
    def _top3(self,rows,cand):
        out=[(host_of(u),round(s,2)) for s,u,_ in rows[:2]]
        if cand: out.append((host_of(cand[1])+"(explore)",round(cand[0],2)))
        return out[:3]
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
                   "strength":1.0,"ep":e["key"],"emo":tgt,"born":self.frame}
        self.em.add("curiosity",0.35)
        self.last_dream=self.frame; self.bored_hold=0; self.dreams+=1
        top2=_top(tgt,2)
        self.log_add("dream","dream",h=e["host"],a=e["A"],
                     e="+".join("%s%.2f"%kv for kv in top2),
                     w="/".join(list(e["words"])[:5]))

    def _sleep_frame(self):
        self.sleep_left-=1
        self.frame_act={"host":None,"k":"sleep","p":{}}
        self.mood_frame="sleeping"; self.mood_frame_event="night"
        self.bus.cool()
        self.energy=min(120.0,self.energy+H.NIGHT_FIX)
        if self.sleep_left%3==0 and self.highA:
            pool=sorted(self.highA,key=lambda e:-e["A"]); picked=None
            while pool:
                ev=pool.pop(0)
                k=tuple(list(ev["words"].keys())[:4])
                if self.dream_hist.get(k,0)>=2 and random.random()<0.7:
                    continue
                picked=ev
                break
            if picked:
                self.highA.remove(picked)
                k=tuple(list(picked["words"].keys())[:4])
                self.dream_hist[k]=self.dream_hist.get(k,0)+1
                if len(self.dream_hist)>500:
                    self.dream_hist=dict(list(self.dream_hist.items())[-400:])
                if self.organ and picked.get("S") is not None:
                    try:
                        base_r=np.log(self.P+1e-9)
                        a0r,graw_r=self.organ.replay(
                            picked["S"],picked.get("host","?"),base_r,
                            picked.get("gain",0.3),picked.get("gap_f",.5),
                            0.0,picked.get("nov",.5),bus_x=self._bus_read())
                        self.curves["w"].append(round(self.organ.err_ema,4))
                        self.bus.writeback(
                            w_a0=min(1.,float(a0r)),
                            w_graw=float(np.clip(graw_r,0.,1.)),
                            w_herr=min(1.,float(self.organ.head_err_ema)))
                    except Exception as ex:
                        self.log_add("anomaly","w_replay",e=str(ex)[:50])
                SEM.warm(list(picked["words"].keys()))
                learn_mc(self.P,{"body":picked["words"]},0.1)
                self.replay_last=picked
                self.frame_act={"host":picked.get("host","?"),"k":"replay",
                                "p":{"w":"/".join(list(picked["words"])[:3])[:24]}}
                self.log_add("sleep","replay",w="/".join(list(picked["words"])[:5]))
        self.P*=0.999
        norm_np(self.P)
        if self.organ: self.organ.decay()
        if self.sleep_left<=0:
            self.sleeping=False
            self.highA.clear(); self.replay_last=None
            self.day_harvest=0.0
            for pr in self.profiles.values():
                pr.sate*=0.5; pr.dark=0
            ent=entropy_np(self.P)
            if ent<H.ENT_WARN:
                self.log_add("sense","entropy",e=round(ent,1))
            self.save_mind()
            self.log_add("sys","morning")
        self.A_hist.append((self.frame,-1,self.energy,"sleep"))

    def _handle_blind(self,target,mode,info,unchanged=False):
        self.blind_streak+=1
        self.total_blind+=1
        self.energy-=H.BLIND_MAINT
        self.last_gain=0.0
        self.satiety*=0.93
        self.em.add("boredom",0.05)
        self.frame_act={"host":(host_of(target) if target else None),"k":"blind",
                        "p":{"why":("stale" if unchanged else (info or "fail"))[:20]}}
        if unchanged: self._fe("blind_stale",Agent.FE_MOODS["blind_stale"])
        elif target: self._fe("blind_fail",Agent.FE_MOODS["blind_fail"])
        else: self._fe("vacancy",Agent.FE_MOODS["vacancy"])
        if target:
            prof=self.profiles.get(target)
            if prof and H.REAL:
                if unchanged:
                    prof.disgust=min(1.0,prof.disgust+0.04)
                    prof.trust*=0.985
                else:
                    was=prof.trust
                    prof.darken()
                    self.em.add("anger",0.22)
                    prof.disgust=min(1.0,prof.disgust+0.06)
                    prof.trust*=0.97
                    if was>0.55:
                        self.em.add("sadness",0.4)
                        self._fe("home_collapsed",Agent.FE_MOODS["home_collapsed"])
                        self.log_add("mood","collapse",h=host_of(target))
                prof.blind+=1
                if (prof.blind>=15 and prof.blind>prof.visits*0.6
                        and len(self.order)>3 and time.time()>=prof.protect_until):
                    self.log_add("eco","silent_retire",h=host_of(target),
                                 b=prof.blind,v=prof.visits)
                    self.blacklist[target]=time.time()+7200
                    self._drop_site(target)
        if mode=="explore" and target:
            self._probe_result(target,0.0)
        elif self.blind_streak%10==0:
            self.log_add("blind","streak",n=self.blind_streak,
                         h=("@"+host_of(target)) if target else "-",i=(info or "")[:30])
        self.A_hist.append((self.frame,-1,self.energy,"blind"))
        self._fe_flush()
        self._energy_floor()

    def begin_frame(self):
        with self.lock:
            self._pend=("skip",None,1)
            if self.dead or not RUN["on"]:
                return "skip",None
            self.bus_x0=self._bus_read()
            self.frame+=1
            self.em_f={n:0.0 for n in Emotion.NAMES}
            self.mood_frame_event=""
            if self.frame%60==0:
                hot=self.bus.hot()
                if hot: self.log_add("bus","bus_flip",c=",".join(hot))
            if not H.REAL:
                self.world.step()
            sd=H.SELF_DECAY
            self.self_profile["bored_ema"]=(
                self.self_profile["bored_ema"]*sd+self.em.l["boredom"]*(1-sd))
            exp=1.0 if self.last_decision.get("mode")=="explore" else 0.0
            self.self_profile["explore_ratio"]=(
                self.self_profile["explore_ratio"]*sd+exp*(1-sd))
            if self.sleeping:
                self._sleep_frame()
                return "skip",None
            if self.awake_frame>=H.AWAKE:
                self.harvest_ema=0.7*self.harvest_ema+0.3*self.day_harvest
                self.night_quality=min(1.0,self.harvest_ema/H.HARVEST_FULL)
                self.log_add("sleep","day_end",d=self.day,h=round(self.day_harvest,1),
                             q=int(100*self.night_quality),n=H.NIGHT_FIX)
                self.day+=1; self.awake_frame=0
                self.sleeping=True; self.sleep_left=H.SLEEPN
                self.A_hist.append((self.frame,-1,self.energy,"sleep"))
                self._sleep_frame()
                return "skip",None
            self.awake_frame+=1
            if H.REAL:
                self._eco_merge()
                for pr in self.profiles.values(): pr.decay()
            if not self.hungry and (self.satiety<H.SAT_LOW or self.energy<30):
                self.hungry=True
                self._fe("hunger",Agent.FE_MOODS["hunger"])
                self.log_add("sys","hunger_on",s=round(self.satiety,2),e=round(self.energy))
            elif self.hungry and (self.satiety>0.35 and self.energy>60):
                self.hungry=False
                self.log_add("sys","hunger_off")
            if (self.goal is None and self.episodes
                    and self.em.l["fear"]<H.FEAR_GATE
                    and self.em.l["boredom"]>H.BORE_TH):
                self.bored_hold+=1
                if (self.bored_hold>=H.BORE_HOLD
                        and self.frame-self.last_dream>=H.DREAM_CD):
                    if random.random()<0.25+0.75*self.dream_payoff:
                        self._dream()
                        self._fe("daydream",Agent.FE_MOODS["daydream"])
            elif self.em.l["boredom"]<=H.BORE_TH:
                self.bored_hold=0
            if (H.REAL and self.frame%H.EXPEDITION_EVERY==15
                    and self.frontier and self.energy>50):
                u=self._pick_candidate()
                if u:
                    dep=self.frontier.get(u,{}).get("depth",1)
                    self._pend=("expedition",u,dep)
                    self.current=host_of(u)
                    self.frame_act={"host":host_of(u),"k":"expedition",
                                    "p":{"hop":dep+1}}
                    self._fe("expedition_setout",Agent.FE_MOODS["expedition_setout"])
                    return "expedition",u
            mode,target,dec=self._decide()
            if mode=="none":
                self._handle_blind(None,None,"niche vacancy")
                return "skip",None
            self.last_decision=dec
            self.current=host_of(target)
            self.frame_act={"host":host_of(target),
                            "k":("explore" if mode=="explore" else "nomad"),
                            "p":{"site":dec.get("site","")}}
            _ev="explore_setout" if mode=="explore" else "nomad_setout"
            self._fe(_ev,Agent.FE_MOODS[_ev])
            if self.iorgan and dec.get("mode") in ("nomad","explore"):
                try:
                    comp=dec.get("comp",{})
                    nov0=min(1.0,abs(comp.get("novelty",.5))/1.5) or .5
                    ev0,ea0=self._emo_summary()
                    pA,pE,pDE,pG=self.iorgan.predict({
                        "last_A":self.last_A,
                        "m5":(sum(self.a5)/len(self.a5) if self.a5 else 0.0),
                        "energy":self.energy/100.0,"satiety":self.satiety,"tau":self.tau,
                        "rel":max(0.,self.last_A-self.tau),"threat":0.0,"nov":nov0,
                        "mode":("explore" if dec["mode"]=="explore" else "site"),
                        "bus":self.bus_x0,"emo_val":ev0,"emo_aro":ea0})
                    es="+".join("%s%.2f"%(k,v) for k,v in zip(Emotion.NAMES,pE) if v>.22) or "calm"
                    self.sim_text={"mode":dec["mode"],"site":dec.get("site","?"),
                        "pA":round(float(pA),2),"pG":round(float(pG),2),"emo":es,
                        "dE":round(float(pDE),1),"jerr":round(self.j_lag_errT,2),
                        "conf":int(self.meta_conf*100)}
                except Exception as ex:
                    self.sim_text={}
                    self.log_add("anomaly","i_pred",e=str(ex)[:50])
            else:
                self.sim_text={}
            self._pend=(mode,target,
                self.frontier.get(target,{}).get("depth",1) if mode=="explore" else 1)
            return mode,target

    def complete_frame(self,chc,links,ok,info):
        with self.lock:
            if self.dead: return
            mode,target,dep=self._pend
            if mode=="skip": return
            e_before=self.energy
            if mode=="expedition":
                self._expedition_complete(chc,links,ok,info,target,dep); return
            prof=self.profiles.setdefault(target,SiteProfile())
            gap_f=min(1.0,(self.frame-prof.last_frame)/max(1,H.AWAKE)) if prof.visits else 0.5
            food,removed=(None,0)
            if ok and chc:
                prof.observe(chc,self.frame)
                if H.REAL: food,removed=prof.food(chc)
                else: food={"title":chc.get("title",Counter()),
                            "body":chc.get("body",Counter()),
                            "nav":Counter({w:c for w,c in chc.get("nav",Counter()).items() if w not in prof.nav_vocab})}
                if removed>60 and not prof.logged and random.random()<0.3:
                    prof.logged=True
                    nv=len(prof.nav_vocab)
                    self.log_add("sense","filter",r=removed,a=removed-nv,nv=nv,h=self.current)
            dkill=0
            if food:
                food,dkill=SEM.damp(food)
                if sum(sum(c.values()) for c in food.values())<=0: food=None
            fh=json.dumps({"t":sorted(food["title"].items()) if food else [],
                           "b":sorted(food["body"].items())[:150] if food else [],
                           "n":sorted(food["nav"].items()) if food else []},ensure_ascii=False) if food else "0"
            h=zlib.crc32(fh.encode("utf-8"))
            prev_h=self.last_food_hash.get(target)
            self.last_food_hash[target]=h
            if (not ok) or (not food):
                self._handle_blind(target,mode,info,unchanged=False)
                self._harvest_links(links,parent=(target if mode=="explore" else None),
                                    depth=(dep+1 if mode=="explore" else 1),
                                    parent_gain=prof.gain_ema)
                self._mood_tick(); return
            if h==prev_h:
                self._handle_blind(target,mode,info,unchanged=True)
                self._harvest_links(links,parent=(target if mode=="explore" else None),
                                    depth=(dep+1 if mode=="explore" else 1),
                                    parent_gain=prof.gain_ema)
                self._mood_tick(); return
            self.blind_streak=0
            if self.pending_storm:
                for w in self.pending_storm: food["body"][w]=food["body"].get(w,0)+3; self.gate[w]=1.0
                self.log_add("threat","storm",w=",".join(self.pending_storm))
                self.pending_storm=None
            SEM.warm([w for ch in ("title","body","nav") for w in food.get(ch,{})])
            SEM.note_meal(food)
            if dkill>=3 and random.random()<0.2:
                self.log_add("sense","damp",n=dkill,h=self.current)
            if SEM.count>=H.SEM_GUARD:
                bcnt=Counter()
                for ch in ("title","body","nav"):
                    for w,c in food.get(ch,{}).items(): bcnt[SEM.bucket(w)]+=c
                threat_hits=[w for w,g in self.gate.items()
                             if g>0.5 and bcnt.get(SEM.bucket(w),0)>=2]
            else:
                tf_cnt=Counter()
                for ch in ("title","body","nav"): tf_cnt.update(food.get(ch,{}))
                threat_hits=[w for w,g in self.gate.items()
                             if g>0.5 and tf_cnt.get(w,0)>=2]
            tflag=1 if threat_hits else 0
            nov_est=.5
            try: nov_est=min(1.0,abs(self.last_decision.get("comp",{}).get("novelty",.5))/1.5) or .5
            except Exception: pass
            feats=self.features(tflag); pred_A=self.i_predict(feats)
            bvI=self._bus_read()
            ev0,ea0=self._emo_summary()
            istate={"last_A":self.last_A,
                    "m5":(sum(self.a5)/len(self.a5) if self.a5 else 0.0),
                    "energy":self.energy/100.0,"satiety":self.satiety,"tau":self.tau,
                    "rel":max(0.0,self.last_A-self.tau),"threat":tflag,
                    "nov":nov_est,"mode":mode,
                    "bus":bvI,"emo_val":ev0,"emo_aro":ea0}
            ipA,ipE,ipDE,ipG=(pred_A,None,0.0,0.3)
            if self.iorgan:
                try: ipA,ipE,ipDE,ipG=self.iorgan.predict(istate)
                except Exception as ex: ipE=None; self.log_add("anomaly","i_pred",e=str(ex)[:50])
            if self.iorgan and ipE is not None:
                try:
                    _pe=[float(v) for v in ipE]
                    _pos=_pe[8]+_pe[0]+_pe[1]
                    _neg=_pe[3]+_pe[4]+_pe[6]+_pe[7]+_pe[9]
                    self.bus.writeback(i_pA=float(np.clip(ipA,0.,1.)),
                        i_pDE=(max(-7.,min(1.,float(ipDE)))+7.)/8.,
                        i_pG=float(np.clip(ipG,0.,1.)),
                        i_val=max(-1.,min(1.,_pos-_neg)),
                        i_aro=min(1.,_pe[3]+_pe[4]+_pe[6]+_pe[2]))
                except Exception: pass
            S=dist_mc(food)
            base=np.log(self.P+1e-9)
            ocache=None; opred=None; ograw=0.0
            if self.organ:
                try:
                    A0,ocache,opred,ograw=self.organ.surprise(S,host_of(target),base,
                        gap_f=gap_f,threat=tflag,nov=nov_est,bus_x=self._bus_read())
                except Exception as ex:
                    self.log_add("anomaly","w_removed",e=str(ex)[:50])
                    self.organ=None; ocache=None; opred=None
                    self.bus.writeback(w_a0=0.,w_drop=0.,w_graw=0.5,w_herr=1.)
            if ocache is None: A0=js_np(self.P,S)
            else:
                self.bus.writeback(w_a0=min(1.,float(A0)),
                    w_graw=float(np.clip(ograw,0.,1.)),
                    w_herr=min(1.,float(self.organ.head_err_ema)))
            self.tau=(1-self.g.TAU_LR)*self.tau+self.g.TAU_LR*A0
            for ch in ("title","body","nav"):
                for w in food.get(ch,{}):
                    b=bch(w,ch)
                    if b not in self.dim_label: self.dim_label[b]=(ch,w)
            learn_mc(self.P,food,self.g.LR)
            A1=js_np(self.P,S)
            if self.organ and ocache is not None:
                try:
                    S_t=np.zeros(DIM); st_dummy=np.zeros(DIM); st_dummy[:CHW]=S[:CHW]
                    self.organ.eat(S,ocache,opred,S_t)
                    pred2,_,_=self.organ._pred(ocache[0],np.log(self.P+1e-9))
                    A1=js_np(pred2,S)
                except Exception as ex:
                    self.log_add("anomaly","w_eat",e=str(ex)[:50])
                    A1=js_np(self.P,S)
            self.bus.writeback(w_drop=max(-1.,min(1.,(float(A0)-float(A1))/0.8)))
            if H.CREDIT_SHIFT and prof.a0_prev is not None and prof.visits>1:
                gain=max(0.0,min(H.GAIN_CAP,self.g.GAIN*(prof.a0_prev-A0)))
            else:
                gain=max(0.0,min(H.GAIN_CAP,self.g.GAIN*(A0-A1)))
            prof.a0_prev=A0
            kd=self.known.get(target) or {}
            self.day_harvest+=(self.g.SECOND_HAND if kd.get("foreign") else 1.0)
            if kd.get("foreign"): gain*=self.g.SECOND_HAND
            exp_bonus=0.0
            if kd.get("expedited") and prof.visits<=H.EXPEDITION_BONUS_MEALS:
                exp_bonus=min(H.EXPEDITION_BONUS_CAP,gain*H.EXPEDITION_BONUS)
                if prof.visits==1:
                    self.log_add("meal","exp_bonus",h=host_of(target),
                                 m=H.EXPEDITION_BONUS_MEALS,x=1.0+H.EXPEDITION_BONUS)
            if self.organ and ocache is not None:
                try: self.organ.head_update(ocache[1],gain)
                except Exception as ex: self.log_add("anomaly","w_head",e=str(ex)[:50])
            if self.organ and opred is not None:
                try:
                    tp=np.argsort(opred)[::-1][:10]; ts=np.argsort(S)[::-1][:10]
                    self.organ_thought={
                        "host":host_of(target),"a0":round(float(A0),3),"a1":round(float(A1),3),
                        "graw":round(float(ograw),3),
                        "herr":round(float(self.organ.head_err_ema),3),
                        "hidden":[round(float(v),3) for v in ocache[1]],
                        "pred":[{"w":self._dim_label(int(b)),"p":round(float(opred[b]),4)} for b in tp],
                        "real":[{"w":self._dim_label(int(b)),"p":round(float(S[b]),4)} for b in ts]}
                    if self.organ.steps%40==1:
                        self.log_add("sense","thought_w",
                            p="/".join(t["w"] for t in self.organ_thought["pred"][:3]),
                            r="/".join(t["w"] for t in self.organ_thought["real"][:3]),
                            a0=round(A0,2),a1=round(A1,2))
                except Exception as ex: self.log_add("anomaly","thought",e=str(ex)[:50])
            if self.organ: self.curves["w"].append(round(self.organ.err_ema,4))
            gain_eff=gain*max(H.SAT_FLOOR,1.0-self.energy/120.0)
            self.last_gain=gain
            self.frame_act={"host":host_of(target),"k":"meal",
                            "p":{"wage":round(gain,2),
                                 "bonus":round(exp_bonus,2) if exp_bonus>0 else None}}
            if gain>=0.15:
                self._fe("meal",{"satisfaction":min(1.0,0.40+0.75*gain)})
            stress=min(H.STRESS_CAP,self.g.STRESS_K*max(0.0,A0-(self.tau+0.25)))
            rel=A0-self.tau
            if stress>0.5:
                self._fe("surprise",dict(Agent.FE_MOODS["surprise"],
                                         surprise=min(1.0,stress/1.2)))
            if kd.get("foreign") and random.random()<0.25:
                self._fe("secondhand",Agent.FE_MOODS["secondhand"])
                self.log_add("meal","secondhand",s=kd.get("src","?"),
                             x=self.g.SECOND_HAND,h=self.current)
            if threat_hits:
                self._fe("threat",Agent.FE_MOODS["threat"])
                corrob=rel>0.4
                for w in threat_hits:
                    self.gate[w]=min(1.0,self.gate[w]+0.05) if corrob else self.gate[w]*0.88
                _tk=1.0 if corrob else 0.35
                self.energy-=min(2.4,self.g.THREAT_COST*len(threat_hits)*_tk)*(0.5 if getattr(H,"LONGEVITY",False) else 1.0)
                self.threat_events+=1; self.stats["threats"]+=1
                self.threat_recent.append(self.frame)
                learn_mc(self.P,{ch:{w:food[ch][w]*2 for w in threat_hits if w in food[ch]}
                                 for ch in ("title","body","nav")},0.2)
                if self.threat_events<=4:
                    self.log_add("threat","hits",w=",".join(threat_hits),
                                 m=("resensitize" if corrob else "x0.88"))
            em=self.em
            em.drive("curiosity",max(0.0,rel)*1.3)
            em.drive("surprise",stress/H.STRESS_CAP)
            if threat_hits:
                em.drive("fear",0.85); em.add("anxiety",0.15)
                prof.fear=min(1.0,prof.fear+0.3)
            gate_mean=sum(self.gate.values())/max(1,len(self.gate))
            em.drive("anxiety",min(1.0,0.5*(1.0-gate_mean)+0.3*min(1.0,len(self.threat_recent)/20.0)))
            if gain>=0.35:
                em.add("satisfaction",0.35*min(1.0,gain)); prof.dark=0
            if rel<0.06 and not threat_hits and gain<0.2: em.add("boredom",0.05)
            if 0.04<rel<0.16 and self.energy>40 and prof.trust>0.5:
                em.add("flow",0.08)
                self._fe("meal",{"flow":0.55})
            tb=Counter(food.get("title",Counter())); tb.update(food.get("body",Counter()))
            self.wg.observe(tb,self.frame)
            dom=max(em.l.items(),key=lambda kv:kv[1])[0]
            sig=max(0.05,em.l[dom]-0.3)
            for w,_ in tb.most_common(30):
                rec=self.word_birth.setdefault(w,{"e":{},"$g":0.0})
                rec["e"][dom]=rec["e"].get(dom,0.0)+sig
                rec["$g"]=rec["$g"]*0.98+gain*0.02
            if len(self.word_birth)>H.WORD_CAP:
                weak=sorted(self.word_birth.items(),
                            key=lambda kv:sum(kv[1]["e"].values()))[:H.WORD_CAP//10]
                for k,_ in weak: del self.word_birth[k]
            if self.goal:
                g=self.goal
                _hw=sum(1 for w in g["words"] if tb.get(w,0)>0)
                hit=(_hw>=H.GOAL_HIT_MIN and
                     (gain>=H.HIT_GAIN or
                      ((em.l["curiosity"]>=H.HIT_EMO or em.l["flow"]>=H.HIT_EMO
                        or em.l["satisfaction"]>=H.HIT_EMO) and gain>=0.05)))
                if hit and H.GLORY_NOVEL and self.frame-g.get("born",0)<10:
                    hit=False
                if hit:
                    for e in self.episodes:
                        if e["key"]==g["ep"]: e["weight"]=min(2.5,e["weight"]*1.3); break
                    self.dream_payoff=min(H.PAYOFF_CAP,0.9*self.dream_payoff+0.1)
                    em.add("satisfaction",0.3)
                    self._fe("dream_fulfilled",Agent.FE_MOODS["dream_fulfilled"])
                    self.log_add("dream","paid",k=_hw,n=len(g["words"]),
                                 g=round(gain,2),p=round(self.dream_payoff,2))
                    self.goal=None
                else:
                    g["strength"]*=H.GOAL_FADE
            if (gain>=H.GLORY_GAIN) or (rel>0.12 and gain>0.05 and not threat_hits):
                key4="|".join(sorted(w for w,_ in tb.most_common(4)))
                if not any(e["key"]==key4 for e in self.episodes):
                    emo_snap=dict(em.l)
                    self.episodes.append({"key":key4,"words":dict(tb.most_common(12)),
                        "emo":emo_snap,"A":round(A0,3),"gain":round(gain,3),
                        "host":host_of(target),"day":self.day,"weight":1.0,"last_used":0})
                    if len(self.episodes)>H.EP_CAP:
                        self.episodes.sort(key=lambda e:e["weight"])
                        self.episodes=self.episodes[6:]
                    self._fe("glory",Agent.FE_MOODS["glory"])
                    if len(self.episodes)<=6 or random.random()<0.15:
                        self.log_add("glory","glory_entry",n=len(self.episodes),
                                     h=host_of(target),a=round(A0,2),g=round(gain,2),
                                     e="+".join(k for k,v in _top(emo_snap,3) if v>0.2))
            if H.REAL:
                new_ratio=sum(1 for w in tb if w not in self.disp)/max(1,len(tb))
                prof.touch(tb,gain,new_ratio,bool(threat_hits))
            em.step()
            if self.goal and self.goal["strength"]<0.15:
                g=self.goal
                for e in self.episodes:
                    if e["key"]==g["ep"]: e["weight"]=max(0.15,e["weight"]*0.7); break
                self.dream_payoff=max(0.0,0.9*self.dream_payoff)
                self._fe("dream_faded",Agent.FE_MOODS["dream_faded"])
                self.log_add("dream","fade",w=list(g["words"])[0] if g["words"] else "?",
                             p=round(self.dream_payoff,2))
                self.goal=None
            old=self.mood
            self.mood=self._meta_label(em.pick())
            if self.mood!=old:
                self.log_add("mood","mood_change",o=old,n=self.mood,a=round(A0,2),
                             t=round(self.tau,2),e=round(self.energy),h=self.current)
            if gain>0.7 and random.random()<0.3:
                self.log_add("meal","big_bite",g=round(gain,2),ge=round(gain_eff,2),
                             s=round(prof.sate,2),h=self.current)
            m0=self.last_decision.get("mood")
            self.gain_ema=0.9*self.gain_ema+0.1*gain_eff
            if m0 and m0 in em.appetite:
                em.appetite[m0]=min(2.2,max(0.4,em.appetite[m0]*(1.0+0.10*(gain_eff-self.gain_ema))))
            burn=self._awake_burn()
            self.energy=min(120.0,self.energy+gain_eff+exp_bonus-burn-stress
                            -(H.EXPLORE_COST if mode=="explore" else 0.0))
            if H.WAGE_LEDGER:
                try:
                    with open("wages450_%d.jsonl"%self.port,"a",encoding="utf-8") as _f:
                        _f.write(json.dumps({"t":round(time.time(),1),"frame":self.frame,
                            "day":self.day,"mode":mode,"host":host_of(target),
                            "mood":self.mood,"mood_frame":self.mood_frame,
                            "mood_event":self.mood_frame_event,
                            "a0":round(float(A0),4),"a1":round(float(A1),4),
                            "gain":round(float(gain),4),"gain_eff":round(float(gain_eff),4),
                            "exp_bonus":round(float(exp_bonus),4),
                            "expedited":bool(kd.get("expedited")),
                            "burn":round(float(burn),4),"stress":round(float(stress),4),
                            "dE":round(self.energy-e_before,3),
                            "w_steps":(self.organ.steps if self.organ else 0),
                            "gap_f":round(float(gap_f),3),"threat":bool(threat_hits)},
                            ensure_ascii=False)+"\n")
                except Exception: pass
            emo_now=[self.em.l[n] for n in Emotion.NAMES]
            if self.iorgan:
                try:
                    ie,ieA,ieG=self.iorgan.learn(istate,A0,emo_now,self.energy-e_before,gain)
                    self.i_errA_ema=.95*self.i_errA_ema+.05*ieA
                    self.i_errT_ema=.95*self.i_errT_ema+.05*ie
                    self.curves["i"].append(round(ieA,4))
                    if ipE is not None:
                        self.i_surprise=float(np.mean(np.abs(ipE-np.asarray(emo_now))))
                        xin=self.iorgan.last_in
                        self.ithought={"predA":round(float(ipA),3),"A":round(float(A0),3),
                                       "predDE":round(float(ipDE),1),
                                       "DE":round(self.energy-e_before,1),
                                       "predG":round(float(ipG),3),"G":round(float(gain),3),
                                       "predE":[round(float(v),2) for v in ipE],
                                       "realE":[round(float(v),2) for v in emo_now],
                                       "xin":[round(float(v),3) for v in xin] if xin is not None else [],
                                       "err":round(ie,3),"errA":round(ieA,3),"errG":round(ieG,3),
                                       "surprise":round(self.i_surprise,3)}
                except Exception as ex: self.log_add("anomaly","i_learn",e=str(ex)[:50])
            if self.jorgan and self.iorgan:
                try:
                    out13=([float(ipA)]+([float(v) for v in ipE] if ipE is not None else [0.0]*10)
                           +[float(ipDE),float(ipG)])
                    act13=([float(A0)]+[float(v) for v in emo_now]
                           +[float(self.energy-e_before),float(gain)])
                    errA_now=min(1.0,abs(float(ipA)-float(A0)))
                    errT_now=min(1.0,float(np.mean(np.abs(np.array(out13)-np.array(act13)))))
                    bvJ=self._bus_read()
                    jx=np.concatenate([self.iorgan.last_in,np.array(out13),
                        [self.j_lag_errA,self.j_lag_errT,self.j_lag_surp,
                         min(1.0,(self.organ.steps/500.0) if self.organ else 0.0)],bvJ])
                    jerrA_pred,jerrT_pred=self.jorgan.predict(jx)
                    self.meta_conf=1.0-min(1.0,jerrT_pred)
                    self.bus.writeback(j_conf=float(self.meta_conf),
                        j_errA=float(np.clip(jerrA_pred,0.,1.)))
                    je=self.jorgan.learn(jx,errA_now,errT_now)
                    self.j_err_ema=.95*self.j_err_ema+.05*je
                    self.j_lag_errA=errA_now
                    self.j_lag_errT=errT_now
                    self.j_lag_surp=self.i_surprise
                    self.j_errA_hist.append(errA_now)
                    jbase=None
                    if len(self.j_errA_hist)>=30:
                        _h=np.array(self.j_errA_hist); _mu=float(_h.mean())
                        jbase=float(np.mean(np.abs(_h-_mu)))
                    self.curves["j"].append(round(je,4))
                    self.jthought={"errA_pred":round(jerrA_pred,3),"errA":round(errA_now,3),
                                   "errT_pred":round(jerrT_pred,3),"errT":round(errT_now,3),
                                   "conf":round(self.meta_conf,3),
                                   "expl_boost":round(1.0+0.5*(1.0-self.meta_conf),2),
                                   "base_mae":round(jbase,3) if jbase is not None else None}
                except Exception as ex: self.log_add("anomaly","j_fail",e=str(ex)[:50])
            if self.gorgan and self.awake_frame%20==0 and len(self.gorgan.hist)>=20:
                L=self.gorgan.ledger; c=self.gorgan.corr()
                if L>H.LED_RISE and c>0.05 and self.g.trust_neural<0.9:
                    self.g.trust_neural=round(min(0.9,self.g.trust_neural+0.02),3)
                    self.log_add("gene","gene_up",l=round(L,1),c=round(c,2),
                                 a=self.g.trust_neural)
                elif (L<H.LED_FALL or (c<0.0 and L<0.0)) and self.g.trust_neural>0.05:
                    self.g.trust_neural=round(max(0.05,self.g.trust_neural-0.03),3)
                    self.log_add("gene","gene_down",l=round(L,1),c=round(c,2),
                                 a=self.g.trust_neural)
                elif self.g.trust_neural<=0.06 and L<-8:
                    self.gorgan.ledger*=0.96
            prof.note_gain(gain)
            for w,_c in tb.most_common(200):
                if w not in STOP: self.disp[w]+=0.15
            for w in list(self.disp): self.disp[w]*=0.995
            if len(self.disp)>4000:
                self.disp=Counter(dict(_top(self.disp,3000)))
            if rel>0.12:
                ent={"A":A0,"words":dict(tb.most_common(12)),"gain":gain}
                if self.organ:
                    ent.update({"S":S,"host":host_of(target),
                                "gap_f":gap_f,"nov":nov_est})
                self.highA.append(ent)
            self.err_self.append(abs((ipA if self.iorgan else pred_A)-A0))
            self.err_base.append(abs(A0-self.last_A))
            self.i_learn(feats,A0)
            self.a5.append(A0); self.last_A=A0
            self.satiety=0.93*self.satiety+0.07*gain_eff
            if mode=="explore": self._probe_result(target,gain)
            self._harvest_links(links,parent=(target if mode=="explore" else None),
                                depth=(dep+1 if mode=="explore" else 1),
                                parent_gain=prof.gain_ema)
            self.A_hist.append((self.frame,A0,self.energy,self.mood))
            self.cur_target=target
            if self.energy<25 and not self._short:
                self._short=True
                self.log_add("sys","hunger_on",s=round(self.satiety,2),e=round(self.energy))
            elif self.energy>=40: self._short=False
            self._fe_flush()
            if self.energy<=0: self._energy_floor(); return

    def _dim_label(self,d):
        ch,w=self.dim_label.get(d,("?","cluster%d"%d))
        return "%s:%s"%({"title":"T","body":"B","nav":"N"}.get(ch,ch),w)

    def _mood_tick(self):
        old=self.mood
        self.mood=self._meta_label(self.em.pick())
        if self.mood!=old:
            self.log_add("mood","mood_tick",o=old,n=self.mood,e=round(self.energy))

    def _expedition_complete(self,chc,links,ok,info,u,dep):
        self.expeditions+=1
        self.frame_act={"host":host_of(u),"k":"expedition",
                        "p":{"out":("arrived" if ok else "turnback:"+str(info or "")[:14])}}
        newh=[]
        if ok and links:
            before=set(self.frontier)
            known_h={host_of(x) for x in self.order}
            pg=None
            d0=self.frontier.get(u)
            if d0 and d0["probes"]>0: pg=d0["gain"]/d0["probes"]
            self._harvest_links(links,parent=u,depth=dep+1,cap=120,
                                parent_gain=pg,expedited=True)
            newh=sorted({host_of(k) for k in self.frontier
                         if k not in before and host_of(k) not in known_h})
        if u in self.frontier:
            d=self.frontier[u]
            d["probes"]+=1
            if d["probes"]>=H.PROBES:
                del self.frontier[u]
                self.log_add("expedition","exp_bust",h=host_of(u))
        self.energy-=H.EXPEDITION_COST
        if newh: self.stats["scouted"]+=len(newh)
        _ev=("expedition_bounty" if (ok and newh)
             else ("expedition_barren" if ok else "expedition_turnback"))
        self._fe(_ev,Agent.FE_MOODS[_ev])
        if ok:
            self.log_add("expedition","exp_arrived",h=host_of(u),d=dep+1,
                         l=len(links),n=len(newh),ls=",".join(newh[:5]) or "-")
        else:
            self.log_add("expedition","exp_turnback",h=host_of(u),
                         i=str(info or "")[:20])
        self.A_hist.append((self.frame,-1,self.energy,"expedition"))
        self._fe_flush()
        self._energy_floor()

    def _site_view(self):
        if not H.REAL:
            return [{"host":"sim-lab","url":"","tag":"base","cls":"b0","human":0,
                     "gain":round(self.last_gain,2),"visits":self.frame,"blind":0,
                     "trust":0.5,"sate":0,"pal":"","last":"—",
                     "depth":0,"parent":"","found":"—","by_port":self.port,"fresh":0,"expd":0}]
        out=[]; now=time.time()
        for u in self.order:
            d=self.known.get(u) or {}; pr=self.profiles.get(u)
            src=d.get("src","?")
            if src=="base": tag,cls="base","b0"
            elif src=="human": tag,cls="human","b3"
            elif src==self.g.name: tag,cls="self","b1"
            else: tag,cls=src,"b2"
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
                "fresh":1 if (fa and now-fa<900) else 0,
                "expd":1 if d.get("expedited") else 0})
        out.sort(key=lambda s:-s["gain"]); return out
    def _frontier_view(self,n=12):
        items=sorted(self.frontier.items(),key=lambda kv:-kv[1]["score"])[:n]
        now=time.time()
        return [{"host":host_of(u),"url":u,"score":round(d["score"],2),
                 "net":(round(d["net_pred"],2) if d.get("net_pred") is not None else None),
                 "loc":round(d.get("local",0.0),2),
                 "probes":d["probes"],"hits":d.get("hits",0),
                 "late":round(d.get("late_wage",0.0),2),
                 "depth":d.get("depth",1),"parent":d.get("parent",""),
                 "by":d.get("found_by","?"),"by_port":d.get("found_port",0),
                 "expd":1 if d.get("expedited") else 0,
                 "fresh":1 if now-d.get("found_at",0)<900 else 0} for u,d in items]

def dist_mc(chc):
    v=np.zeros(DIM); tot=0.0
    for ch in ("title","body","nav"):
        for w,c in chc.get(ch,{}).items():
            v[bch(w,ch)]+=c; tot+=c
    return (v/tot) if tot>0 else np.full(DIM,1.0/DIM)

def engine(agent):
    while not SHUTDOWN.is_set():
        try:
            if not RUN["on"]:
                time.sleep(0.5); continue
            t0=time.time()
            mode,url=agent.begin_frame()
            if mode in ("site","explore","expedition"):
                chc,links,ok,info=agent._fetch(url)
            elif mode=="lab":
                chc,links,ok,info=agent.world.emit(),[],True,"lab"
            else:
                chc,links,ok,info=None,[],False,"skip"
            agent.complete_frame(chc,links,ok,info)
            if agent.frame%15==0:
                _mb=_rss_mb()
                if _mb>3500: print("[mem-warn] peak RSS %.0fMB"%_mb)
        except Exception as e:
            print("[engine:%s] %s %s"%(getattr(agent.g,"name","?"),type(e).__name__,str(e)[:120]))
            time.sleep(2)
        base=H.SLEEP_SEC if agent.sleeping else H.FRAME_SEC
        time.sleep(max(0.2,base-(time.time()-t0)))

def _prof_save(pr):
    return {"gain_ema":pr.gain_ema,"visits":pr.visits,"blind":pr.blind,"logged":pr.logged,
            "last_visit":pr.last_visit,"last_frame":pr.last_frame,"protect_until":pr.protect_until,
            "trust":pr.trust,"fear":pr.fear,"disgust":pr.disgust,"nov":pr.nov,"sate":pr.sate,
            "succ":pr.succ,"dark":pr.dark,"human":pr.human,"nav_seen":pr.nav_seen,
            "a0_prev":pr.a0_prev,"palate":dict(pr.palate),
            "nav_vocab":list(pr.nav_vocab)[:200],
            "hist":[dict(list(c.items())[:80]) for c in pr.hist]}

def _agent_snapshot(a):
    with a.lock:
        return {"name":a.g.name,"port":a.port,"alive":not a.dead,
                "sleeping":a.sleeping,"mood":(a.mood_frame or a.mood),
                "mood_acc":a.mood,"mood_event":a.mood_frame_event or "",
                "energy":round(a.energy,1),
                "A":round(a.last_A,2),"tau":round(a.tau,2),
                "satiety":round(a.satiety,2),"sites":len(a.order),
                "frontier":len(a.frontier),
                "explored":a.stats["explored"],"discovered":a.stats["discovered"],
                "dreams":a.dreams,"expeditions":a.expeditions,
                "blackouts":a.stats.get("blackouts",0),
                "episodes":len(a.episodes),"day":a.day,"frame":a.frame,
                "curiosity":a.g.CURIOSITY,"death_cause":a.death_cause,
                "order_hosts":[host_of(u) for u in a.order],
                "log_tail":[{"t":e["t"],"type":e["type"],"k":e["k"],"p":e.get("p",{})}
                            for e in list(a.log)[-6:]]}

def _twin_fog_view(a2,shex,fhex):
    with a2.lock:
        _name=a2.g.name
        fh=a2.frame_act.get("host")
        if not fh and a2.cur_target: fh=host_of(a2.cur_target)
        _k=a2.frame_act.get("k","init"); _p=a2.frame_act.get("p",{}) or {}
        _mood=(a2.mood_frame or a2.mood); _slp=a2.sleeping; _alive=not a2.dead
    def _roam_hex(key):
        return HEX_SPIRAL[ROAM0+(zlib.crc32(key.encode())%ROAM_N)]
    if not _alive:
        q,r=_roam_hex(fh or _name)
        return {"fog":"roam","q":q,"r":r,"k":"death","p":{},
                "mood":_mood,"sleeping":False,"alive":False}
    if _slp:
        if fh and fh in shex:
            q,r=shex[fh]
            return {"fog":"known","q":q,"r":r,"k":"sleep","p":{},
                    "mood":_mood,"sleeping":True,"alive":True}
        if fh and fh in fhex:
            q,r=fhex[fh]
            return {"fog":"frontier","q":q,"r":r,"k":"sleep","p":{},
                    "mood":_mood,"sleeping":True,"alive":True}
        q,r=_roam_hex(fh or _name)
        return {"fog":"roam","q":q,"r":r,"k":"sleep","p":{},
                "mood":_mood,"sleeping":True,"alive":True}
    if not fh:
        q,r=_roam_hex(_name)
        return {"fog":"roam","q":q,"r":r,"k":_k,"p":{},
                "mood":_mood,"sleeping":False,"alive":True}
    if fh in shex: q,r=shex[fh]; mode="known"
    elif fh in fhex: q,r=fhex[fh]; mode="frontier"
    else: q,r=_roam_hex(fh); mode="roam"; _p={}
    return {"fog":mode,"q":q,"r":r,"k":_k,"p":_p,
            "mood":_mood,"sleeping":False,"alive":True}
HTML=r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Digital Placozoa V4.5.0</title>
<style>
 body{background:#0b1020;color:#cbd5e1;font-family:system-ui,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;padding:16px}
 h1{font-size:18px;margin:0 0 4px}.sub{font-size:12px;color:#64748b;margin-bottom:10px}
 .grid{display:grid;grid-template-columns:2fr 1fr;gap:12px}
 .panel{background:#111a30;border:1px solid #1e293b;border-radius:10px;padding:12px}
 .lamp{display:inline-block;width:14px;height:14px;border-radius:50%;margin-right:6px;vertical-align:-2px}
 .big{font-size:26px;font-weight:700}
 .metrics{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0}
 .m{background:#0d1526;border:1px solid #1e293b;border-radius:8px;padding:8px 12px;min-width:108px}
 .lab{font-size:11px;color:#64748b}.bar{height:10px;background:#1e293b;border-radius:5px;overflow:hidden;margin-top:4px}
 .bar>div{height:100%;background:#4ade80}
 #cloud span{margin:3px 5px;display:inline-block}
 .tbl{width:100%;border-collapse:collapse;font-size:12px}
 .tbl th{color:#64748b;text-align:left;padding:3px 8px;border-bottom:1px solid #1e293b;font-weight:normal}
 .tbl td{padding:3px 8px;border-bottom:1px solid #141d33}
 .src{display:inline-block;padding:0 6px;border-radius:6px;font-size:10px;color:#e2e8f0}
 .b0{background:#334155}.b1{background:#14532d}.b2{background:#1e3a8a}.b3{background:#92400e}
 .scroll{max-height:300px;overflow-y:auto}
 #log{height:220px;overflow-y:auto;font-family:Consolas,monospace;font-size:12px;background:#0a0f1e;border-radius:8px;padding:8px}
 button{margin:4px 6px 4px 0;padding:7px 12px;border:0;border-radius:8px;color:#e2e8f0;cursor:pointer;font-size:13px}
 input{background:#0a0f1e;border:1px solid #334155;border-radius:8px;color:#e2e8f0;padding:7px 10px;font-size:13px;width:290px}
 select{background:#0a0f1e;border:1px solid #334155;border-radius:8px;color:#e2e8f0;padding:7px 8px;font-size:13px}
 .red{background:#7f1d1d}.purple{background:#5b21b6}.green{background:#14532d}.blue{background:#1e3a8a}
 .gray{background:#334155}.amber{background:#92400e}.cyan{background:#155e75}
 #emobars{display:grid;grid-template-columns:repeat(5,1fr);gap:6px 12px;margin-top:6px}
 .erow{font-size:12px}.erow .el{color:#64748b}
 .ebar{height:7px;background:#1e293b;border-radius:4px;overflow:hidden;margin-top:2px}
 .ebar>div{height:100%}
 #goalbanner{font-size:13px;margin-top:10px;padding:8px 10px;border:1px dashed #92400e;border-radius:8px;background:#1a1206}
 #decision{font-size:13px;line-height:1.7;margin-top:10px}#decision b{color:#93c5fd}
 #appetite{font-size:12px;color:#a5b4fc;margin-top:8px}
 #selfline{font-size:12px;color:#7dd3fc;margin-top:4px}
 #sentence{font-size:15px;line-height:1.7;border-left:3px solid #4ade80;margin-bottom:12px}
 .nbox{flex:1;min-width:300px;border:1px solid #1e293b;border-radius:8px;padding:8px}
 .nbox h4{margin:0 0 6px;font-size:12px;color:#93c5fd}
 .row{display:flex;gap:12px;flex-wrap:wrap}
 .tw{background:#0d1526;border:1px solid #1e293b;border-radius:8px;padding:6px 10px;margin:4px 0;font-size:12px}
 .tomb{display:none;position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:60;width:min(780px,calc(100% - 24px));pointer-events:none}
 .tomb .card{pointer-events:auto;position:relative;background:rgba(26,5,5,.96);border:2px solid #7f1d1d;border-radius:14px;padding:14px 20px;text-align:center}
 .tomb h2{color:#ef4444;margin:0 0 6px;font-size:17px}
 .tomb .x{position:absolute;top:6px;right:12px;cursor:pointer;color:#94a3b8}
 .badge{display:inline-block;background:#1e3a8a;color:#e2e8f0;border-radius:6px;padding:2px 8px;font-size:12px}
 .okb{background:#14532d}
 #birthgenes .chip{display:inline-block;background:#0d1526;border:1px solid #1e293b;border-radius:6px;padding:2px 8px;margin:2px;font-size:12px}
 .modal{display:none;position:fixed;inset:0;background:rgba(2,6,23,.72);z-index:90;padding:26px;overflow:auto}
 .mcard{background:#111a30;border:1px solid #334155;border-radius:12px;max-width:1080px;margin:0 auto;padding:14px 16px}
 .mhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
 .mhead .x{cursor:pointer;color:#94a3b8;font-size:16px}
 .mtools{margin-bottom:8px}
 .mbody{max-height:66vh;overflow-y:auto}
 .vgroup{margin:8px 0}
 .vgroup h4{margin:4px 0;font-size:13px;color:#93c5fd}
 .vword{display:inline-block;background:#0d1526;border:1px solid #1e293b;border-radius:6px;padding:2px 8px;margin:2px;font-size:13px}
 #hexwrap{background:#0a0f1e;border:1px solid #1e293b;border-radius:10px;overflow:hidden}
 #hexmap{width:100%;height:660px;display:block}
 .hexname{font-size:16px;fill:#e2e8f0;text-anchor:middle;pointer-events:none}
 .hexsub{font-size:13px;fill:#94a3b8;text-anchor:middle;pointer-events:none}
 .mapside{font-size:14px;line-height:1.8;color:#94a3b8}
 .mapside b{color:#93c5fd}
</style></head><body>
<h1><span class="lamp" id="lamp"></span>Digital Placozoa <span style="color:#64748b">V4.5.0</span>
 <span id="who" style="font-size:14px;color:#fbbf24"></span>
 <span id="mode" style="font-size:14px;color:#93c5fd"></span>
 <span class="badge" id="dbadge" data-ltt="t_d">d≈?</span>
 <span class="badge okb" id="stbadge" data-ltt="t_st">—</span>
 <span class="badge" id="resbadge" data-ltt="t_res">×?</span>
 <span class="badge okb" id="lbadge" style="display:none" data-ltt="t_long">⚡B</span>
 <button class="cyan" id="langbtn" style="float:right" onclick="toggleLang()">🌐 EN</button></h1>
<div class="sub" data-lt="sub1"></div>
<div class="sub" data-lt="sub2"></div>
<div class="sub" data-lt="sub3"></div>
<div class="sub" style="color:#7c8db5" data-lt="sub4"></div>
<div class="sub" id="longbanner" style="display:none;color:#4ade80" data-lt="longbanner"></div>
<div class="panel" id="sentence">—</div>
<div class="panel" style="margin-bottom:12px"><b data-lt="map_title"></b>
 <div class="lab" style="margin:2px 0 6px" data-lt="map_legend"></div>
 <div class="lab" id="maphead" style="margin:0 0 6px;font-size:15px;color:#93c5fd">—</div>
 <div style="display:flex;gap:10px">
  <div class="mapside" style="width:230px;flex-shrink:0" id="mapside">—</div>
  <div id="hexwrap" style="flex:1;min-width:420px">
   <svg id="hexmap" viewBox="-560 -620 1120 1240" preserveAspectRatio="xMidYMid meet">
    <g id="hexes"></g><g id="bugs"></g>
   </svg>
  </div>
 </div>
 <div class="row" style="margin-top:8px">
  <div style="flex:2;min-width:520px" class="scroll">
   <div class="lab" id="siteslab">—</div>
   <table class="tbl"><thead><tr>
    <th data-lt="th_s1"></th><th data-lt="th_s2"></th><th data-lt="th_s3"></th><th data-lt="th_s4"></th>
    <th data-lt="th_s5"></th><th data-lt="th_s6"></th><th data-lt="th_s7"></th><th data-lt="th_s8"></th><th data-lt="th_s9"></th>
   </tr></thead><tbody id="sitesT"></tbody></table></div>
  <div style="flex:1;min-width:330px" class="scroll">
   <div class="lab" data-lt="cand_legend"></div>
   <table class="tbl"><thead><tr>
    <th data-lt="th_c1"></th><th data-lt="th_c2"></th><th data-lt="th_c3"></th><th data-lt="th_c4"></th><th data-lt="th_c5"></th>
   </tr></thead><tbody id="candT"></tbody></table></div>
 </div>
</div>
<div class="metrics">
 <div class="m" data-ltt="tt_energy"><div class="lab" data-lt="m_energy"></div><div class="big" id="energy">-</div><div class="bar"><div id="ebar" style="width:100%"></div></div></div>
 <div class="m" data-ltt="tt_av"><div class="lab" data-lt="m_av"></div><div class="big" id="av">-</div><div class="lab" id="msub_av">-</div></div>
 <div class="m" data-ltt="tt_gain"><div class="lab" data-lt="m_gain"></div><div class="big" id="g">-</div><div class="lab" id="msub_gain">-</div></div>
 <div class="m" data-ltt="tt_harvest"><div class="lab" data-lt="m_harvest"></div><div class="big" id="hv">-</div><div class="lab" id="msub_hv">-</div></div>
 <div class="m" data-ltt="tt_sem" onclick="openSem()" style="cursor:pointer"><div class="lab" data-lt="m_sem"></div><div class="big" id="sem">-</div><div class="lab" id="msub_sem">-</div></div>
 <div class="m" data-ltt="tt_hhi"><div class="lab" data-lt="m_hhi"></div><div class="big" id="hhi">-</div><div class="lab" data-lt="m_hhi_s"></div></div>
 <div class="m" data-ltt="tt_ep" onclick="openEpi()" style="cursor:pointer"><div class="lab" data-lt="m_ep"></div><div class="big" id="ep">-</div><div class="lab" id="msub_ep">-</div></div>
 <div class="m" data-ltt="tt_mem" onclick="openVocab()" style="cursor:pointer"><div class="lab" data-lt="m_mem"></div><div class="big" id="mem">-</div><div class="lab" id="msub_mem">-</div></div>
 <div class="m" data-ltt="tt_xpl"><div class="lab" data-lt="m_xpl"></div><div class="big" id="xpl">-</div><div class="lab" id="msub_xpl">-</div></div>
 <div class="m" data-ltt="tt_serr"><div class="lab" data-lt="m_serr"></div><div class="big" id="serr">-</div><div class="lab" data-lt="m_serr_s"></div></div>
 <div class="m" data-ltt="tt_day"><div class="lab" data-lt="m_day"></div><div class="big" id="day">-</div><div class="lab" id="msub_day">-</div></div>
</div>
<div class="panel" style="margin-bottom:12px"><b data-lt="mind_title"></b>
 <div id="emobars"></div><div class="lab" id="moodaccline" style="margin-top:6px">—</div>
 <div id="goalbanner">—</div><div id="decision">—</div><div id="appetite">—</div><div id="selfline">—</div>
 <div id="simline" style="margin-top:8px;color:#fbbf24">—</div>
</div>
<div class="panel" style="margin-bottom:12px"><b data-lt="nw_title"></b>
 <div class="lab" id="nstat" style="margin-top:4px">—</div>
 <div class="row" style="margin-top:8px">
  <div class="nbox" data-ltt="nb1_tt"><h4 data-lt="nb1_h"></h4>
   <div class="lab" style="color:#38bdf8" data-lt="lbl_pred"></div><div id="wpred"></div>
   <div class="lab" style="color:#4ade80" data-lt="lbl_real"></div><div id="wreal"></div>
   <div class="lab" data-ltt="lbl_hid_tt" data-lt="lbl_hid"></div><canvas id="whidden" style="width:100%;height:36px"></canvas>
   <div class="lab" id="wmeta"></div></div>
  <div class="nbox" data-ltt="nb2_tt"><h4 data-lt="nb2_h"></h4>
   <div id="iemo"></div><div class="lab" id="imeta"></div>
   <div class="lab" data-ltt="lbl_iin_tt" data-lt="lbl_iin"></div><canvas id="iinput" style="width:100%;height:30px"></canvas></div>
  <div class="nbox" data-ltt="nb3_tt"><h4 data-lt="nb3_h"></h4>
   <div id="jmeta" style="font-size:12px;line-height:1.8">—</div><div class="lab" id="jline" style="margin-top:6px"></div></div>
  <div class="nbox" data-ltt="nb4_tt"><h4 data-lt="nb4_h"></h4>
   <div id="gmeta" style="font-size:12px;line-height:1.8">—</div></div>
 </div>
 <div class="row" style="margin-top:8px">
  <div class="nbox" style="flex:2"><h4 data-lt="curves_t"></h4>
   <canvas id="cv_w" style="width:100%;height:26px"></canvas>
   <canvas id="cv_i" style="width:100%;height:26px"></canvas>
   <canvas id="cv_j" style="width:100%;height:26px"></canvas>
   <canvas id="cv_g" style="width:100%;height:26px"></canvas></div>
  <div class="nbox"><h4 data-lt="genes_t"></h4><div id="genes" style="font-size:12px;line-height:1.8">—</div></div>
 </div>
</div>
<div class="panel" style="margin-bottom:12px"><b data-lt="ls_title"></b>
 <div class="lab" style="margin:4px 0" data-lt="ls_desc"></div>
 <table class="tbl"><thead><tr><th data-lt="ls_ch"></th><th>W</th><th>I</th><th>J</th><th>G</th></tr></thead><tbody id="listenT"></tbody></table>
<div class="lab" id="busline" style="margin-top:4px;font-size:12px;color:#777">—</div>
</div>
<div class="grid">
 <div class="panel"><canvas id="chart" style="width:100%;height:150px"></canvas>
  <div class="lab" style="margin-top:8px" data-lt="chart_lbl"></div>
  <div class="lab" style="margin-top:4px" id="profline">—</div></div>
 <div class="panel"><b data-lt="cloud_t"></b><div id="cloud" style="margin-top:8px;line-height:1.9"></div>
  <div class="lab" style="margin-top:8px"><span id="gates">-</span></div></div>
</div>
<div class="panel" style="margin-top:12px"><b data-lt="birth_t"></b>
 <div id="birthgenes" style="margin-top:6px;font-size:12px;line-height:1.9">—</div>
 <div class="lab" style="margin-top:6px" data-lt="birth_note"></div>
</div>
<div class="panel" style="margin-top:12px"><b data-lt="twins_t"></b><div id="twins">…</div></div>
<div class="panel" style="margin-top:12px"><b data-lt="ledger_t"></b><div id="ledger" style="font-family:Consolas,monospace;font-size:11px;margin-top:6px;max-height:140px;overflow-y:auto"></div></div>
<div class="panel" style="margin-top:12px">
 <div>
  <button class="amber" onclick="post('/api/toggle_run')" id="runbtn">⏸</button>
  <button class="red" data-ltt="b_threat_tt" onclick="post('/api/inject?kind=threat')" data-lt="b_threat"></button>
  <button class="gray" data-ltt="b_noise_tt" onclick="post('/api/inject?kind=noise')" data-lt="b_noise"></button>
  <button class="green" data-ltt="b_heat_tt" onclick="post('/api/inject?kind=novelty')" data-lt="b_heat"></button>
  <button class="purple" data-ltt="b_snap_tt" onclick="post('/api/snapshot')" data-lt="b_snap"></button>
  <button class="purple" data-ltt="b_load_tt" onclick="post('/api/load_snap')" data-lt="b_load"></button>
  <button class="purple" data-ltt="b_rb_tt" onclick="post('/api/rollback')" data-lt="b_rb"></button>
  <button class="red" data-ltt="b_kill_tt" onclick="post('/api/kill')" data-lt="b_kill"></button>
  <button class="green" data-ltt="b_reset_tt" onclick="post('/api/reset')" data-lt="b_reset"></button>
  <button class="green" data-ltt="b_inh_tt" onclick="post('/api/reset?inherit=1')" data-lt="b_inh"></button>
  <button class="blue" data-ltt="b_spawn_tt" onclick="post('/api/spawn')" data-lt="b_spawn"></button>
  <button class="gray" onclick="openVocab()" data-lt="b_vocab"></button>
  <button class="gray" onclick="openSem()" data-lt="b_sem"></button>
  <button class="gray" onclick="openChain()" data-lt="b_chain"></button>
  <button class="gray" onclick="openEpi()" data-lt="b_epi"></button>
  <span><input id="newurl" data-ltp="add_ph"><button class="green" onclick="addSite()" data-lt="b_add"></button></span>
  <button class="red" onclick="if(confirm(T('confirm_end')))post('/api/shutdown')" data-lt="b_end"></button>
 </div><div id="log"></div>
</div>
<div class="tomb" id="tomb"><div class="card"><span class="x" onclick="document.getElementById('tomb').style.display='none'">✕</span>
 <h2 data-lt="tomb_t"></h2><p id="cause"></p>
 <p style="font-size:12px;color:#94a3b8;margin:4px 0"><span data-lt="tomb_fossil"></span>:<code id="fossil" style="color:#fbbf24"></code></p>
 <button class="green" onclick="post('/api/reset')" data-lt="b_reset"></button>
 <button class="green" onclick="post('/api/reset?inherit=1')" data-lt="b_inh"></button></div></div>
<div class="modal" id="modal"><div class="mcard">
 <div class="mhead"><b id="mtitle">—</b><span class="x" onclick="closeModal()" data-lt="m_close"></span></div>
 <div class="mtools" id="mtools"></div><div class="mbody" id="mbody"></div>
</div></div>
<script>
let LANG=localStorage.getItem('pz_lang')||'zh';
const LT={
t_d:["神经发育度(内部刻度):结构分+精度分,非生物意识宣称","Neural-development proxy (internal scale): structure + precision. Not a claim of biological consciousness"],
t_st:["出生时对功能附件做的一次学习自检","One-shot learning selftest of organs at birth"],
t_res:["耦合基因:天生『听同胞多少』(0.15~0.7),随化石遗传±20%","Coupling gene: innate peer-listening weight (0.15–0.7), inherits ±20% via fossils"],
t_long:["长寿拨盘B:⚡触0强制休眠不死·情绪烧蚀封顶1.2·威胁×0.5·冷URL×0.3","Longevity dial B: ⚡ forced sleep at 0 energy (no death) · burn cap 1.2 · threat x0.5 · cold-URL x0.3"],
longbanner:["🛡 长寿拨盘B生效:情绪从死因变性格;能量低位震荡+⚡回血锯齿;行为学观测照旧","🛡 Longevity dial B active: moods become traits, not causes of death; low-energy sawtooth with ⚡ recovery; behavioral readings unchanged"],
sub1:["它靠「把新鲜意外压进脑子」换能量——工资=信用后移:首访=发现红利,复访=上次学到、这次真省下的惊讶。无聊到极点会做梦,梦要真吃到想要的内容才算应验;死了留化石。","It earns energy by compressing fresh surprise into its model — wage = credit-shifted: first visit = discovery bonus, revisits = surprise actually saved by last visit's learning. Deep boredom triggers dreams; a dream counts only if the wanted content is really eaten. Death leaves a fossil."],
sub2:["🪣 语义桶=多语嵌入聚类(模型可换=换坐标系,旧快照不互认);🧭 基座渔场中英混合池,每次开机随机抽 3+4 或 4+3;🗺 地图动态取景(自动贴近实际内容)。","🪣 Semantic buckets = multilingual embedding clustering (changing model = new coordinate epoch; old snapshots refused). 🧭 Base pool mixes zh+en sites, 3+4 or 4+3 drawn per launch. 🗺 Map auto-zooms to actual content."],
sub3:["🎭 彩条=当帧情绪(事件驱动)·灰条=累计底色(决策/代谢用它)——表情会演戏,性格不抽风。👥 族群:同胞按URL落在『我』的格上;领地外=环10远域(按host稳定散列·落点=估计·URL未知)。","🎭 Colored bars = current-frame emotion (event-driven); gray = accumulated baseline (drives decisions/metabolism). Expressions act; temperament doesn't twitch. 👥 Kin are placed by URL on MY map; outside my territory = ring-10 far field (stable host hash; position is an estimate; URL unknown)."],
sub4:["⚠ 免责:心情/梦/记忆/联想均为仿真参数;『丝盘虫』=无神经元的最大单细胞动物,器官为功能类比。理论映射见 FRAMEWORK.md。","⚠ Disclaimer: moods/dreams/memories/associations are simulation parameters. 'Placozoa' = the simplest known animal (no neurons); 'organs' are functional analogies. Theory mapping: FRAMEWORK.md."],
map_title:["🗺 蜂窝渔场 · 身位与族群剪影","🗺 Hex fishery · position & kin silhouette"],
map_legend:["实线=渔场(绿=自发现 蓝=同胞 棕=人类 灰=出厂;🧭=开荒站) · 虚线=候选 · 远处空白=未探索荒野(领地外同胞落于此·落点=估计) · 虫色=端口色·同格自动散开·悬停看当帧动作 · 点虫→联想","solid=fishery (green=self, blue=peer, amber=human, gray=base; 🧭=pioneer) · dashed=candidate · outer blank=unexplored wild (out-of-territory kin land here; estimate) · bug color=port · auto-scatter on same cell · hover for current action · click bug→associations"],
hex_sub:["工资{g}·{v}次","w{g}·{v}v"],
cand_sub:["踩{p}/3","p{p}/3"],
siteslab:["渔场 {n}/{c}","Fishery {n}/{c}"],
th_s1:["来源","Source"],th_s2:["站点","Site"],th_s3:["新鲜度","Fresh"],th_s4:["去过","Visits"],
th_s5:["白跑","Blind"],th_s6:["信任","Trust"],th_s7:["腻","Satiety"],th_s8:["这里聊什么","Topics"],th_s9:["怎么发现的","Discovered via"],
cand_legend:["想去还没踩点的链接(🧭=远征收割;利息=第2/3次踩点合计工资;冷URL×0.3)","Links it wants to scout (🧭=expedition-harvested; interest = wage summed over probes 2-3; cold URLs x0.3)"],
th_c1:["站点","Site"],th_c2:["本能分","Instinct"],th_c3:["直觉分","Intuition"],th_c4:["踩点/利息","Probes/interest"],th_c5:["谁发现的","Found by"],
m_energy:["能量","Energy"],tt_energy:["0=死亡(拨盘A)或⚡瘫眠(拨盘B)","0 = death (dial A) or ⚡ blackout (dial B)"],
m_av:["意外 / 见怪线","Surprise / habituation"],tt_av:["A=此刻的意外;τ=见怪不怪线","A = current surprise; tau = habituation line"],
m_gain:["这顿工资","Meal wage"],tt_gain:["信用后移制;远征链前5餐另列开荒补贴","Credit-shifted; expedition-chain first-5-meals bonus listed separately"],
m_harvest:["今日素材","Today's material"],tt_harvest:["真实进食帧数;只决定夜间可重放量,不发钱","Frames with real food; gates night replay volume only, pays nothing"],
m_sem:["语义桶","Semantic buckets"],tt_sem:["多语嵌入自动聚类;点击看全景","Multilingual embedding clustering; click for full view"],
m_hhi:["偏食吗","Picky?"],tt_hhi:["饭量最大站占比(HHI>0.25=偏食)","Largest-site share of meals (HHI>0.25 = picky)"],m_hhi_s:["HHI>0.25=偏食","HHI>0.25 = picky"],
m_ep:["做梦的底气","Dream capital"],tt_ep:["荣光回忆段数/梦应验率;点击看全文","Glory episodes / dream-payoff; click for details"],
m_mem:["认识的词","Words known"],tt_mem:["词汇总数/有情绪出身的词;点击打开词汇总表","Total words / words with emotional birth; click for vocab"],
m_xpl:["觅食足迹","Foraging"],tt_xpl:["踩点=候选试探;收编=转正渔场","Scouts = candidate probes; promotions = new fisheries"],
m_serr:["它懂自己吗","Self-knowledge"],tt_serr:["左=预测自己惊讶的误差;右=瞎猜基线;左<右=有点自知","Left = error predicting own surprise; right = naive baseline; left<right = some self-knowledge"],m_serr_s:["左<右=有点自知","left<right = some insight"],
m_day:["活到","Age"],tt_day:["⚡=能量触0强制休眠次数(拨盘B)","⚡ = forced-sleep count at zero energy (dial B)"],
mind_title:["🧠 内心(彩=当帧·灰=累计底色)","🧠 Inner states (color = current frame · gray = accumulated)"],
no_goal:["(无目标——闲得还不够久)","(no goal — not bored enough yet)"],
goal_b:["🎯 正在惦记(强度{s}):想要〈{e}〉→ 目标词:{w} → 去这找:{h}","🎯 Craving (strength {s}): wants 〈{e}〉 → target words: {w} → look at: {h}"],
goal_note:["(目标词≥2个真出现在饭里才算应验)","(counts as fulfilled only if ≥2 target words really appear in a meal)"],
moodaccline:["本帧事件:{ev} · 底色(驱动决策/代谢):{acc}","This frame: {ev} · baseline (drives decisions/metabolism): {acc}"],
dec_doing:["<b>正在做</b>:{m} → {s} · <b>决定时心情</b>:{md}<br>","<b>Doing</b>: {m} → {s} · <b>mood at decision</b>: {md}<br>"],
dec_base:["<span class=\"lab\">(累计盘·决策用它)</span>","<span class=\"lab\">(accumulated — used by decisions)</span>"],
dec_why:["<b>理由</b>:{c}<br>","<b>Because</b>: {c}<br>"],
dec_hand:["<b>本能前三</b>:{t}","<b>Instinct top-3</b>: {t}"],
dec_net:["<br><b>直觉前三</b>:{t} · <b>α={a}</b>","<br><b>Intuition top-3</b>: {t} · <b>alpha {a}</b>"],
selfline:["出门冲动(自省):无聊积累 {b} · 探索比例 {e} · 对本次选择的推力 {m}","Urge to roam (self-monitoring): boredom EMA {b} · explore ratio {e} · push on this choice {m}"],
sim_pre:["🪞 它心里预演:","🪞 Inner rehearsal: "],
nw_title:["🧠✨ 神经思维窗 —— 四件功能附件的内心独白","🧠✨ Neural window — inner monologues of the four functional organs"],
nstat_on:["四附件在线(总线互听):世界脑W(已学{s}餐) · 内感脑I · 自知脑J · 眼力G","Four organs online (bus-connected): world-model W ({s} meals) · interoception I · metacognition J · forager's eye G"],
nstat_off:["功能附件未启用——纯反射模式","Organs disabled — pure reflex mode"],
nb1_tt:["W世界脑:饭前猜味道,饭后把差学进去","W world-model: predicts the meal, then learns the gap"],
nb1_h:["它预测的世界 vs 真实尝到的","Predicted world vs actually tasted"],
lbl_pred:["饭前猜的","Predicted"],lbl_real:["真尝到的","Actual"],
lbl_hid:["此刻内心指纹","Current hidden fingerprint"],lbl_hid_tt:["W隐藏层激活","W hidden-layer activations"],
nb2_tt:["I内感脑:饭前预测自己的心情/能量/收获","I interoception: predicts own mood/energy/yield before eating"],
nb2_h:["它预测的自己 vs 真实的自己","Predicted self vs actual self"],
lbl_iin:["输入:它看到的自己","Input: how it sees itself"],lbl_iin_tt:["I附件的身体状态输入","I organ's body-state input"],
nb3_tt:["J自知脑:给I的预测打分;不自信时更敢闯","J metacognition: scores I's predictions; less confidence → more boldness"],
nb3_h:["它知道自己的预测准不准吗","Does it know how wrong its predictions are?"],
nb4_tt:["G眼力:看链接文字猜有没有饭;战绩好α才升","G forager's eye: guesses food from link text; alpha rises only with a good track record"],
nb4_h:["觅食直觉 + 战绩本","Foraging intuition + track record"],
curves_t:["四条学习曲线(近120餐):蓝=世界脑 绿=内感脑 紫=自知脑 黄=眼力相关","Four learning curves (last 120 meals): blue=W green=I purple=J yellow=G corr"],
genes_t:["神经基因(α随战绩变;出生值见出生档案)","Neural genes (alpha moves with track record; birth values in birth record)"],
ls_title:["🔊 共振总线·听力表","🔊 Resonance bus · hearing table"],
ls_desc:["行=总线通道(信念,非事实) · 列=四附件 · 值=平均|权重|。出生时全零——全聋出生,听力只能靠『真的降低预测误差』长出来。","rows = bus channels (beliefs, not facts) · columns = organs · value = mean |weight|. All zeros at birth — born deaf; hearing can only grow from genuinely reduced prediction error."],
ls_ch:["通道","Channel"],
bus_pre:["总线(上帧): ","Bus (last frame): "],
chart_lbl:["蓝=A(意外) · 橙虚线=τ(见怪线) · 绿=能量","blue = A (surprise) · orange dashed = tau (habituation) · green = energy"],
prof_pre:["性格(天生×经历): ","Temperament (nature x history): "],
cloud_t:["词汇表(红=警报词 · 彩色=学会时的心情)","Vocabulary (red = alarm words · colored = mood at birth)"],
birth_t:["🧬 出生档案(先天基因)","🧬 Birth record (innate genes)"],
birth_note:["参数启动时从基因池随机抽取,不代表真实遗传规律;只有『继承化石』带上上一代词频残迹与神经基因(±20%变异)。词图不随化石继承——学来的心智,死亡即湮灭。","Parameters are drawn from a gene pool at start — not a claim about real genetics. Only 'inherit fossil' carries the parent's word residue + neural genes (±20% mutation). The word graph is not inherited — learned minds annihilate at death."],
twins_t:["同胞孪生——食谱重合越低,分化越彻底","Twin peers — the lower the diet overlap, the deeper the divergence"],
twins_none:["还没有孪生同胞","No twins yet"],
tw_open:["打开它的生命页:{p} ↗","Open its page: {p} ↗"],
tw_row:["第{d}天#{f} · 渔场{s}·收编{dc}·梦{dr}·回忆{e}","day {d} #{f} · fishery {s}·promoted {dc}·dreams {dr}·episodes {e}"],
tw_black:["·⚡瘫眠{b}","·⚡blackouts {b}"],
tw_diet:["食谱重合","diet overlap"],
ledger_t:["发现总账","Discovery ledger"],
ledger_none:["还没有收编记录","No promotions yet"],
led_row:["[{t}] {by}@{pt} 自 {p} 第{d}跳 → 收编 {h} (新鲜度{a})","[{t}] {by}@{pt} from {p} hop {d} → promoted {h} (avg {a})"],
b_threat:["☠ 威胁","☠ Threat"],b_threat_tt:["手动撒威胁词(拨盘A=可能致死;拨盘B=烧到0瘫一觉)","Inject threat words (dial A: may kill; dial B: burn to 0 → one blackout)"],
b_noise:["░ 毒化","░ Poison"],b_noise_tt:["毒化世界模型与功能附件","Corrupt world model & organs"],
b_heat:["✦ 加热","✦ Heat"],b_heat_tt:["加热候选链接","Heat up candidates"],
b_snap:["📸 快照","📸 Snapshot"],b_snap_tt:["纪元存档(全附件+语义字典+总线+领地/情绪+词图;450坐标系)","Epoch snapshot (organs + sem dict + bus + territory/moods + word graph; epoch 450)"],
b_load:["⤵ 载入","⤵ Load"],b_load_tt:["手动载入快照(旧4系列档仅供考古——坐标/模型不互认)","Manually load a snapshot (old v4 files are archaeology only — epochs/models mismatch)"],
b_rb:["↩ 回滚","↩ Rollback"],b_rb_tt:["回到快照时刻(含情绪与目标)","Return to the snapshot moment (moods & goal included)"],
b_kill:["⏹ 处刑","⏹ Kill"],b_kill_tt:["死亡并写化石(词频残迹+神经基因可继承)","Kill and write a fossil (word residue + neural genes inheritable)"],
b_reset:["⟳ 新生","⟳ Rebirth"],b_reset_tt:["全新随机基因","Fresh random genes"],
b_inh:["🌱 继承","🌱 Inherit"],b_inh_tt:["继承化石词频残迹与神经基因","Inherit fossil word residue & neural genes"],
b_spawn:["🐣 孪生","🐣 Twin"],b_spawn_tt:["孵同源孪生","Spawn a same-origin twin"],
b_vocab:["📖 词汇","📖 Vocab"],b_sem:["🪣 语义桶","🪣 Buckets"],b_chain:["🔗 联想","🔗 Associate"],b_epi:["🏛 回忆","🏛 Memories"],
b_add:["➕ 加入","➕ Add"],add_ph:["https://… 注入新大陆","https://… inject a new land"],
b_end:["⛔ 结束","⛔ End"],confirm_end:["结束并归档?","Shut down and archive?"],
tomb_t:["☠ 死亡","☠ Death"],tomb_fossil:["化石(神经基因可跨纪元继承)","Fossil (neural genes inherit across epochs)"],
m_close:["✕ 关闭(Esc)","✕ Close (Esc)"],
dm_explore:["探索","Exploring"],dm_nomad:["轮牧","Grazing"],dm_vacancy:["空缺","Vacancy"],
c_novelty:["新鲜感","novelty"],c_satiety:["吃腻了","satiety"],c_trust:["老熟人","trust"],c_fear:["有点怕","fear"],
c_disgust:["上次很糟","bad last time"],c_revisit:["好久没去","overdue"],c_record:["有前科","bad record"],
c_random:["一时兴起","whim"],c_goal:["梦里想要的","dreamed-of"],c_stay:["正吃着这儿","already here"],
c_self:["逛腻了想出门","restless"],c_net_gain:["直觉预估收获","predicted yield"],c_loc:["直觉好恶","gut like/dislike"],
c_alpha:["直觉话事权","intuition weight"],c_reso_i:["内感预演(总线)","interoceptive rehearsal (bus)"],c_reso_w:["胃的预感(总线)","gut hunch (bus)"],
tag_base:["出厂","base"],tag_human:["人类注入","human"],tag_self:["自发现","self-found"],
m_say_curiosity:["好奇心起","curious"],m_say_flow:["正来劲","in flow"],m_say_surprise:["被惊到了","startled"],
m_say_fear:["很害怕","afraid"],m_say_anxiety:["有点慌","anxious"],m_say_boredom:["闲得发慌","bored stiff"],
m_say_anger:["来气","angry"],m_say_disgust:["犯恶心","disgusted"],m_say_satisfaction:["吃饱喝足挺舒坦","content"],
m_say_sadness:["有点丧","down"],m_say_dying:["快不行了","failing"],m_say_low_power:["省电巡航","low-power"],
m_say_sleeping:["睡着了","asleep"],m_say_init:["刚醒来","waking up"],m_say_oscillation:["震荡","oscillating"],
ev_explore_setout:["探索出发","set out to explore"],ev_nomad_setout:["游牧出发","set out to graze"],
ev_expedition_setout:["远征出发","set out on expedition"],ev_meal:["进食","meal"],
ev_blind_stale:["白跑(未变)","blind (stale)"],ev_blind_fail:["白跑(失败)","blind (failed)"],
ev_vacancy:["空缺","vacancy"],ev_home_collapsed:["老地方塌了","old place collapsed"],
ev_threat:["威胁","threat"],ev_surprise:["超预期","beyond expectation"],
ev_expedition_bounty:["远征丰收","expedition bounty"],ev_expedition_barren:["远征无果","expedition barren"],
ev_expedition_turnback:["远征折返","expedition turnback"],ev_misstep:["踩空","misstep"],
ev_dream_fulfilled:["梦应验","dream fulfilled"],ev_daydream:["白日梦","daydream"],
ev_dream_faded:["梦落空","dream faded"],ev_glory:["荣光","glory"],
ev_new_fishery:["新渔场","new fishery"],ev_secondhand:["二手","secondhand"],
ev_hunger:["饥饿","hunger"],ev_blackout:["⚡瘫眠","⚡blackout"],ev_death:["死亡","death"],ev_night:["夜里","night"],
dc_anxiety:["焦虑出血(威胁风暴未及习惯化)","anxiety bleed (threat storms never habituated)"],
dc_deficit:["代谢赤字:探索代价超过生态位产出","metabolic deficit: exploration cost exceeded niche yield"],
dc_starve:["饥饿(生态位长期静默)","starvation (long-silent niche)"],
dc_stress:["应激代谢失衡","stress metabolic imbalance"],
dc_executed:["人为终结——实验者处刑","terminated by experimenter"],
g_LR:["学习率(压缩步幅)","learning rate (compression step)"],g_TAU_LR:["适应速度","habituation rate"],
g_GAIN:["消化力","digestive power"],g_STRESS_K:["应激敏感度","stress sensitivity"],
g_THREAT_COST:["受惊代价","cost of alarm"],g_CURIOSITY:["好奇心","curiosity"],
g_trust_birth:["对直觉的初始信任α","innate trust in intuition (alpha)"],
g_neural_lr_w:["世界脑学习率","world-model LR"],g_neural_lr_i:["内感脑学习率","interoception LR"],
g_corr_gain:["估值器增益","valuator gain"],g_coupling_gain:["共振增益(天生听同胞多少)","coupling gain (innate peer-listening)"],
b_w_a0:["W·饭前惊讶","W·pre-meal surprise"],b_w_drop:["W·本餐压缩","W·meal compression"],
b_w_graw:["W·效价预感","W·valence hunch"],b_w_herr:["W·预感误差","W·hunch error"],
b_i_pA:["I·预测意外","I·predicted surprise"],b_i_pDE:["I·预测能量变","I·predicted dEnergy"],
b_i_pG:["I·预测工资","I·predicted wage"],b_i_val:["I·效价信念","I·valence belief"],
b_i_aro:["I·唤醒信念","I·arousal belief"],b_j_conf:["J·自信度","J·confidence"],
b_j_errA:["J·自估误差","J·self-error est."],b_g_net:["G·直觉分","G·intuition"],
b_g_corr:["G·眼力相关","G·eye corr"],b_g_ledger:["G·战绩账本","G·ledger"],
b_noise:["噪声(每帧新)","noise (new each frame)"],
sp_dead:["☠ 它死了:{c}。化石已存档,词图随心智湮灭。","☠ It died: {c}. Fossil archived; word graph annihilated with the mind."],
sp_paused:["⏸ 时间被冻结——它保持原样,一无所知。","⏸ Time frozen — it stays exactly as it is, knowing nothing."],
sp_night:["💤 第{d}夜:可重构素材{q}%(今日真实进食{h}帧),见闻回放归档","💤 Night {d}: replayable material {q}% ({h} real-meal frames today); experiences replaying to archive"],
sp_night_dream:[",刚才梦到:{w}",", just dreamed of: {w}"],
sp_night_end:["。夜里每帧+0.45回补能量。",". +0.45 energy per night frame."],
sp_hungry:["🔥 饿急了:压低觅食标准先吃饱——求生开关接管。","🔥 Starving: foraging standards lowered — the survival switch took over."],
sp_hungry_b:["(拨盘B:烧到0只会⚡瘫一觉,不会死)","(dial B: hitting 0 means one ⚡ blackout, not death)"],
sp_wake:["刚醒来,正在打量四周……","Just woke up, looking around…"],
sp_vacant:["有点闲得慌,但渔场和候选都是空的——等它探到新地方,或注入一个站点。","Restless, but fishery and candidates are empty — wait for exploration, or inject a site."],
sp_going:["现在{m}(当帧表情),正打算去「{s}」","Now {m} (current-frame mood), heading to 「{s}」"],
sp_because:["(因为{r})","(because {r})"],
sp_sup_hi:[";刚发生的事比它预想的意外不少。","; what just happened surprised it more than expected."],
sp_sup_lo:[";眼前的一切都在意料之中。","; everything is exactly as expected."],
sp_goal:[" 它还惦记着梦里想要的味道:「{w}」——要真吃到才算应验。"," It still craves the dreamed taste: 「{w}」 — fulfilled only by really eating it."],
sp_mind:["(这一口它饭前在心里预演过——见神经思维窗)","(this bite was rehearsed in advance — see neural window)"],
ms_energy:["⚡能量 <b>{e}</b>/120","⚡energy <b>{e}</b>/120"],
ms_d:["🧠认知 d≈<b>{d}</b><br>自知 <b>{s}</b> vs 瞎猜 {b}","🧠d≈<b>{d}</b><br>self-err <b>{s}</b> vs naive {b}"],
ms_mood:["心情 <b style=\"color:{c}\">{m}</b> {f}<span class=\"lab\">(当帧)</span>","mood <b style=\"color:{c}\">{m}</b> {f}<span class=\"lab\">(frame)</span>"],
ms_wage:["🍴工资EMA <b>{g}</b><br>踩点<b>{x}</b>·收编<b>{d}</b>·远征<b>{e}</b>","🍴wage EMA <b>{g}</b><br>scouts<b>{x}</b>·promoted<b>{d}</b>·expeditions<b>{e}</b>"],
ms_black:["<br>⚡瘫眠回血 <b>{b}</b>次","<br>⚡blackout-recovery <b>{b}</b>x"],
maphead:["📅第{d}天·第{f}帧 · 🪣{sc}/{nb}桶·{sw}词 · 📖词汇{mw}(出生{bw}) · 🏛回忆{e}章·应验{p}% · 👥族群{k}只 · {dn}","📅day {d}·frame {f} · 🪣{sc}/{nb} buckets·{sw} words · 📖vocab {mw} (born {bw}) · 🏛{e} episodes·payoff {p}% · 👥kin {k} · {dn}"],
daynight_day:["☀ 日间","☀ day"],daynight_night:["🌙 夜里(原地回梦)","🌙 night (replaying in place)"],
stself_pass:["自检✓","selftest ✓"],stself_warn:["自检⚠","selftest ⚠"],stself_na:["自检—","selftest —"],
wage_cs:["信用后移","credit-shift"],wage_legacy:["旧制","legacy"],
sem_hash:["随机分桶","hash buckets"],sem_emb:["词向量","embeddings"],
sat_lbl:["饱食度 ","satiety "],nq_lbl:["可重构 {v}%·不发钱","replayable {v}% · pays nothing"],
ep_lbl:["{n}段 / 应验{p}%","{n} eps / payoff {p}%"],
xpl_lbl:["踩点{s}·收编{d}","scouts {s}·promoted {d}"],
day_lbl:["{d}天·第{f}帧","day {d}·frame {f}"],
site_direct:["直采","direct"],site_hop:["{d}跳←{p}","{d} hops ← {p}"],
cand_probes:["{p}/3·利息{l}","{p}/3·int {l}"],
filter_lab:["滤除{r}(适应{a}+结构指纹{nv}词)","filtered {r} (adapted {a} + structural {nv} words)"],
wait_first:["(等待第一餐)","(waiting for first meal)"],
gt_line:["直觉话事权α=<b style=\"color:#fbbf24\">{a}</b> · 眼力相关 <b>{c}</b>(样本{n})<br>战绩本: <b style=\"color:{lc}\">{l}</b> <span class=\"lab\">猜对+3/猜错−1·平局遗忘×0.98</span><br>近期判定: <b>{r}</b><br><span class=\"lab\">信用后移制下,gain更真实——眼力学的是『哪里真有可复采的结构』</span>","alpha=<b style=\"color:#fbbf24\">{a}</b> · eye-corr <b>{c}</b> (n={n})<br>ledger: <b style=\"color:{lc}\">{l}</b> <span class=\"lab\">hit+3/miss−1·tie decay x0.98</span><br>recent: <b>{r}</b><br><span class=\"lab\">Under credit-shifting, gain is honest — the eye learns where structure is really re-harvestable</span>"],
gt_none:["(候选估值为空)","(no candidate estimates yet)"],
jt_line:["自知力误差EMA:{e}(低于0.15才计入d)","metacognition error EMA: {e} (below 0.15 counts toward d)"],
it_row:["预{p}/实{r}","pred {p}/act {r}"],
it_wage:["工资","wage"],
wm_line:["@{h} · 意外 饭前{a0}→饭后{a1} · 预感={g}(误差{e})","@{h} · surprise {a0}→{a1} · hunch={g} (err {e})"],
genes_line:["直觉话事权α:出生<b>{b}</b> → 现在<b style=\"color:#fbbf24\">{n}</b><br>lr_w={w} · lr_i={i} · 估值×{c} · 共振×{cg}<br><span class=\"lab\">一生中:战绩好→α+0.02/20帧;战绩差→α−0.03<br>代间:死亡→化石→继承(±20%)</span>","alpha: born <b>{b}</b> → now <b style=\"color:#fbbf24\">{n}</b><br>lr_w={w} · lr_i={i} · valuator x{c} · coupling x{cg}<br><span class=\"lab\">In life: good record → alpha +0.02/20 frames; bad → −0.03<br>Across generations: death → fossil → inherit (±20%)</span>"],
vocab_title:["📖 词汇总表 — 按『学会时主导心情』分组(红=警报词;点词→联想)","📖 Vocabulary — grouped by dominant mood at birth (red = alarm; click word → associate)"],
vocab_filter_ph:["过滤词…","filter words…"],
vocab_info:["词库{n}条,显示{s}。点任意词→联想链。","{n} words, showing {s}. Click any word → association chain."],
vocab_more:["…还有{n}个,用过滤缩小","…{n} more — filter to narrow"],
vocab_group_alarm:["警报词","alarm words"],vocab_group_none:["无出生记录","no birth record"],
sem_title:["🪣 语义桶全景 — 词→桶(嵌入=语义聚类;hash=随机;点词→联想)","🪣 Semantic buckets — word→bucket (embedding = semantic clustering; hash = random; click word → associate)"],
sem_engine:["引擎{e} · 已用{u}/{n}桶 · 词{w} · θ{t} · 空桶{e2}","engine {e} · used {u}/{n} · words {w} · theta {t} · empty {e2}"],
sem_filter_ph:["过滤词或桶号…","filter word or bucket id…"],
sem_none:["无匹配桶","no matching buckets"],
sem_more:["…仅显示前500桶","…first 500 buckets only"],
sem_sel:["桶{b} 的全部词({n}) — 点词→联想","All words of bucket {b} ({n}) — click to associate"],
sem_pick:["← 点选左侧桶查看全部词","← pick a bucket to see its words"],
sem_row:["桶{b} · {n}词 · 近餐{s}% · P质量{p}","bucket {b} · {n}w · recent {s}% · P mass {p}"],
epi_title:["🏛 荣光回忆 — 高gain/高意外经历(梦的素材库)","🏛 Glory memories — high-gain/high-surprise episodes (dream material)"],
epi_none:["还没有回忆","no memories yet"],
epi_emo:["情绪:","mood:"],epi_words:["词:","words:"],
chain_title:["🔗 词汇联想链 — 共现统计显微镜(非语言模型)","🔗 Word association chain — co-occurrence microscope (not a language model)"],
chain_ph:["输入一个词,如: model / 模型","type a word, e.g. model / 模型"],
chain_det:["固定最大关联(确定性)","fixed max edge (deterministic)"],
chain_go:["联想","associate"],
chain_info:["词图=同餐词共现·每餐×0.995遗忘·每词top-24。<b>同一输入,不同天联想不同</b>——链随『年龄』漂移。未学过的词经语义桶桥接(multilingual=语义近亲×0.7;hash=随机,会警示)。","Graph = same-meal co-occurrence · x0.995 decay per meal · top-24 per word. <b>Same input, different day, different chain</b> — the chain drifts with age. Unseen words bridge via semantic buckets (multilingual = semantic kin x0.7; hash = random, warned)."],
chain_hint:["<br>输入词后点「联想」,或从词汇表/语义桶/地图小虫身上点任意词。","<br>Type a word and press associate, or click any word in vocab/buckets/on-map bugs."],
chain_run:["游走中…","wandering…"],
chain_fail:["请求失败","request failed"],
chain_warn_g:["(词图直达","(direct graph walk"],
chain_warn_b:["(部分跳经语义桶桥接","(some hops bridged via semantic buckets"],
chain_warn_end:[" · 图谱{w}词/{e}边{d})"," · graph {w} words/{e} edges{d})"],
chain_det2:[" · 确定性"," · deterministic"],chain_soft:[" · 软采样"," · soft-sampled"],
chain_empty:["『{q}』暂时没有足够的关联——多喂它几天。","『{q}』has no strong associations yet — feed it more days."],
chain_wgt:["关联权重","edge weight"],chain_more:["点击从此词继续展开","click to continue from this word"],
chain_foot:["软采样:每跳在top-3关联中按权重随机;勾选「固定最大关联」则每跳取最高者。<b>明天同一输入,链会不同</b>。","Soft sampling: each hop picks among top-3 edges by weight; check deterministic to always take the max. <b>Tomorrow, same input, different chain</b>."],
chain_need:["需要输入一个词","type a word first"],
chain_hash:["『{q}』不在词图中,且当前为hash降级——桥接是随机的,联想无语义。装 sentence-transformers torch 后再试。","『{q}』is not in the graph, and hash fallback is active — bridges are random, not semantic. Install sentence-transformers torch and retry."],
chain_none:["『{q}』不在词图中,语义桶里也没有近亲可桥接——它还没学过相关词汇。","『{q}』is not in the graph and no bucket-kin exists — it hasn't learned related words yet."],
tip_roam:["{n} · 在我领地之外的远域游猎(荒野落点=估计,URL未知)","{n} · foraging in far wild beyond my territory (position = estimate, URL unknown)"],
tip_bug:["{n}{me} · {a} {d} · 点虫→联想","{n}{me} · {a} {d} · click → associations"],
tip_me:["(我)","(me)"],
sleep_lab:["😴","😴"],
log_none:["(空)","(empty)"],
// ----- act_* (bug subtitle + tooltip) -----
act_meal:["进食","meal"],
act_explore:["探索","explore"],
act_nomad:["游牧","graze"],
act_expedition:["远征","expedition"],
act_blind:["白跑","blind"],
act_sleep:["睡眠","sleep"],
act_daydream:["做梦","dream"],
act_replay:["重放","replay"],
act_death:["死亡","death"],
act_init:["初始化","init"],
// ----- mode_* (顶栏) -----
mode_field:["野外","field"],
mode_lab:["实验室","lab"],
};
function T(k){const e=LT[k];if(!e)return k;return LANG==='zh'?e[0]:e[1];}
function F(k,p){const e=LT[k];if(!e)return k;const t=LANG==='zh'?e[0]:e[1];
 return t.replace(/\{(\w+)\}/g,(_,x)=>(p&&p[x]!=null)?p[x]:"?");}
function applyStatic(){
 document.querySelectorAll('[data-lt]').forEach(el=>{el.textContent=T(el.dataset.lt)});
 document.querySelectorAll('[data-ltt]').forEach(el=>{el.title=T(el.dataset.ltt)});
 document.querySelectorAll('[data-ltp]').forEach(el=>{el.placeholder=T(el.dataset.ltp)});
 document.getElementById('langbtn').textContent=(LANG==='zh'?'🌐 EN':'🌐 中文');
}
function toggleLang(){LANG=(LANG==='zh'?'en':'zh');localStorage.setItem('pz_lang',LANG);
 applyStatic();closeModal();poll();}

const LOGK={
"sys.born":["个体[{n}]出生:端口{pt} · LR{lr}/GAIN{gn}/应激K{sk}/好奇{cu} · 器官[{og}] · α={al} · 共振×{cg} · 语义={se} · 工资={wg}",
 "Individual [{n}] born :{pt} · LR {lr}/GAIN {gn}/stressK {sk}/curiosity {cu} · organs [{og}] · alpha {al} · coupling x{cg} · sem={se} · wage={wg}"],
"sys.selftest":["器官自检 W={W} I={I} J={J} G={G}","Organ selftest W={W} I={I} J={J} G={G}"],
"sys.no_snap":["本端口无快照——空白出生","No snapshot for this port — blank birth"],
"sys.snap_dim":["快照坐标系不符({d0}≠{d1})——空白出生;化石词频/神经基因仍可继承","Snapshot dims mismatch ({d0}≠{d1}) — blank birth; fossil words/genes still inheritable"],
"sys.snap_sem":["快照语义模型不符({s0}≠{s1})——拒绝载入(空白出生);以 EDF_STMODEL={s0} 重启可延续","Snapshot sem model mismatch ({s0}≠{s1}) — refused (blank birth); restart with EDF_STMODEL={s0} to resume"],
"sys.snap_ok":["快照延续:{f} @第{fr}帧(荣光{ep}章·词{wd}·能量{en}·器官{og})","Snapshot resumed: {f} @frame {fr} (glory {ep}, words {wd}, energy {en}, organs {og})"],
"sys.organ_partial":["⚠器官键未全继承:{lst}——缺失键重新生长","⚠ organ keys partially restored: {lst} — missing keys regrow"],
"sys.terr_restore":["领地恢复:{s}站·候选{f}","Territory restored: {s} sites, {f} candidates"],
"sys.blackout":["⚡能量耗尽→强制休眠(夜+{nf}/帧回血)","⚡ energy depleted → forced sleep (night +{nf}/frame)"],
"sys.rebirth":["个体[{n}]新生:端口{pt} · {src}","Individual [{n}] reborn :{pt} · {src}"],
"sys.rebirth_cross":["跨语义纪元继承:化石{f}——种子词按{c}桶重编码","Cross-epoch inheritance from fossil {f} — seeds re-bucketed into {c} buckets"],
"sys.rebirth_live":["(前个体[{o}]未死亡即新生——词图湮灭,无化石)","(previous [{o}] reborn without death — word graph annihilated, no fossil)"],
"sys.hunger_on":["饥饿短路:转向探索/回巢(饱{s} E{e})","Hunger short-circuit: explore/nest (sat {s}, E {e})"],
"sys.hunger_off":["脱离饥饿:恢复轮牧","Hunger cleared: grazing resumed"],
"sys.morning":["晨间复位:腻度隔夜消化","Morning reset: satiety digested overnight"],
"sys.twin":["孪生出生:好奇{cu}/K{sk}/α={al}/共振×{cg}","Twin born: curiosity {cu}, K {sk}, alpha {al}, coupling x{cg}"],
"sem.sem_restore":["语义字典恢复:{w}词·{c}簇","Semantic dict restored: {w} words, {c} clusters"],
"sem.sem_restore_fail":["语义字典恢复失败:{e}","Semantic dict restore failed: {e}"],
"sem.sem_manual":["手动载入:不恢复全局语义字典(防多胞胎竞争)","Manual load: global sem dict not restored (twin-race guard)"],
"mind.wg_restore":["词图随快照恢复:{w}词/{e}边","Word graph restored: {w} words / {e} edges"],
"mind.mind_load":["心智档案载入:{f}(荣光{ep}章)","Mind file loaded: {f} (glory {ep})"],
"anomaly.organ_file_fail":["器官档案读取失败:{e}","Organ file read failed: {e}"],
"anomaly.terr_fail":["领地恢复失败:{e}","Territory restore failed: {e}"],
"anomaly.eco_fail":["ECO并入失败:{e}","ECO merge failed: {e}"],
"anomaly.dyn_fail":["动态状态恢复失败(核心已就绪):{e}","Dynamic state restore failed (core ready): {e}"],
"anomaly.g_learn":["G.learn失败:{e}","G.learn failed: {e}"],
"anomaly.g_verdict":["G.verdict失败:{e}","G.verdict failed: {e}"],
"anomaly.g_score":["G.score失败(600帧内静默):{e}","G.score failed (silent 600 frames): {e}"],
"anomaly.w_removed":["W器官衰竭({e})——摘除,退回先天P","W organ failed ({e}) — removed, fallback to innate P"],
"anomaly.w_eat":["W.eat失败:{e}——A1退回先天P","W.eat failed: {e} — A1 falls back to innate P"],
"anomaly.w_head":["W.head_update失败:{e}","W.head_update failed: {e}"],
"anomaly.w_replay":["W.replay失败:{e}","W.replay failed: {e}"],
"anomaly.i_pred":["I.predict失败:{e}","I.predict failed: {e}"],
"anomaly.i_learn":["I.learn失败:{e}","I.learn failed: {e}"],
"anomaly.j_fail":["J失败:{e}","J failed: {e}"],
"anomaly.thought":["思维快照失败:{e}","Thought snapshot failed: {e}"],
"eco.eco_merge":["ECO总账并入{n}条","{n} ECO records merged"],
"eco.world_full":["世界已满({m})且无可淘汰——{h}暂无法接纳","World full ({m}), nothing to retire — {h} rejected"],
"eco.retire":["生态位淘汰(满员):{h}(有效分{e})","Niche retired (full): {h} (eff {e})"],
"eco.silent_retire":["长期静默淘汰:{h}(盲{b}/访{v})","Long-silence retired: {h} (blind {b}/visits {v})"],
"eco.clash":["撞车:{h}已被[{b}]先注册——转二手×{x}","Race lost: {h} already claimed by [{b}] — secondhand x{x}"],
"eco.foreign_merge":["外来食源并入:{h}(来自{s},×{x})","Foreign source merged: {h} (via {s}, x{x})"],
"eco.exp_promote":["远征链收编:{h}(前{m}餐×{x}补贴)","Expedition-chain promoted: {h} (first {m} meals x{x} bonus)"],
"world.human_inject":["人类注入新大陆:{h}(★全额粮票)","Human injected a new land: {h} (full ration)"],
"explore.new_fishery":["新渔场收编:{h}(均gain{a},复采利息{l},{d}跳←{p}{x})——署名[{n}@{pt}]","Fishery promoted: {h} (avg {a}, revisit interest {l}, {d} hops ← {p}{x}) — credit [{n}@{pt}]"],
"explore.give_up_credit":["放弃:{h}(首采红利{f},复采利息{l}<{b})——一次性内容,冷却{c}分钟","Dropped: {h} (first-visit {f}, revisit interest {l}<{b}) — one-shot content, cooldown {c}min"],
"explore.give_up":["放弃:{h}(均gain{a},{n}次有食)——冷却{c}分钟","Dropped: {h} (avg {a}, {n} hits) — cooldown {c}min"],
"meal.exp_bonus":["🧭开荒补贴 @{h}:前{m}餐×{x}(单列入账)","🧭 pioneer bonus @{h}: first {m} meals x{x} (ledger-only)"],
"meal.secondhand":["二手食粮(来自{s})×{x} @{h}","Secondhand meal (via {s}) x{x} @{h}"],
"meal.big_bite":["高工资入账+{g}(消化{ge},腻→{s} @{h})","Big wage +{g} (digested {ge}, satiety→{s} @{h})"],
"sense.filter":["滤除{r}(适应{a}+结构指纹{nv}词)@{h}","Filtered {r} (adapted {a} + structural {nv} words) @{h}"],
"sense.damp":["阻尼{n}词·高频模板簇@{h}","Damped {n} words · hot template cluster @{h}"],
"sense.entropy":["兴趣熵{e}bit——偏食预警","Interest entropy {e} bits — picky-eater warning"],
"sense.thought_w":["W:预测〈{p}〉实际〈{r}〉 A {a0}→{a1}","W: predicted 〈{p}〉 actual 〈{r}〉 A {a0}→{a1}"],
"blind.streak":["连续{n}帧无新信息 {h}({i})","{n} blind frames {h} ({i})"],
"threat.storm":["威胁注入:{w}(闸门重置)","Threat storm injected: {w} (gates reset)"],
"threat.hits":["警报词命中:{w}({m})","Alarm words hit: {w} ({m})"],
"mood.collapse":["老地方塌了({h})→悲伤+0.4,信任崩解","Old place collapsed ({h}) → sadness+0.4, trust broken"],
"mood.mood_change":["{o} → {n} (A={a} τ={t} E={e} @{h})","{o} → {n} (A={a} tau={t} E={e} @{h})"],
"mood.mood_tick":["{o} → {n} (E={e})","{o} → {n} (E={e})"],
"dream.dream":["白日梦(那天@{h},A={a})→想要〈{e}〉→目标词:{w}","Daydream (that day @{h}, A={a}) → wants 〈{e}〉 → target words: {w}"],
"dream.paid":["梦应验:词命中{k}/{n},gain={g}——目标消解(应验率→{p})","Dream fulfilled: {k}/{n} words hit, gain={g} — goal resolved (payoff→{p})"],
"dream.fade":["目标未应验而散——《{w}…》(应验率→{p})","Goal dissolved unfulfilled — 〈{w}…〉 (payoff→{p})"],
"glory.glory_entry":["荣光入档第{n}章 @{h} A={a} gain={g}(心情:{e})","Glory entry #{n} @{h} A={a} gain={g} (mood: {e})"],
"sleep.day_end":["第{d}天结束(今日素材{h}帧→可重构{q}%·夜+{n}/帧)","Day {d} ended (today's material {h} frames → replayable {q}% · night +{n}/frame)"],
"sleep.replay":["夜的重放:{w}→世界模型强化","Night replay: {w} → world-model reinforced"],
"bus.bus_flip":["总线翻转预警:{c}(仅记录)","Bus flip warning: {c} (logged only)"],
"gene.gene_up":["账本+{l}·相关{c}→α↑{a}","Ledger +{l}, corr {c} → alpha up {a}"],
"gene.gene_down":["账本{l}·相关{c}→α↓{a}","Ledger {l}, corr {c} → alpha down {a}"],
"death.death":["{c} | 化石 → {f}","{c} | fossil → {f}"],
"expedition.exp_bust":["{h}三次考察无果,退出候选","{h}: 3 barren probes, dropped from candidates"],
"expedition.exp_arrived":["抵达 @{h}(第{d}跳)→链{l}条,新域{n}:{ls}","Arrived @{h} (hop {d}) → {l} links, {n} new: {ls}"],
"expedition.exp_turnback":["折返 @{h}({i})","Turned back @{h} ({i})"],
"sys.pause":["暂停 · RUN 离线 · 闸门冻结 · 能量停滞","PAUSED · RUN offline · gates frozen · energy stalled"],
"sys.resume":["重启 · RUN 恢复 · 闸门解冻 · 能量重启","RESUMED · RUN online · gates thawed · energy live"],
"world.heat":["⚠️ 热浪:{h} · 核心{n}词被炙伤","⚠️ HEATWAVE {h} · {n} core words scorched"],
"world.poison":["☠️ 毒域:{h} · 核心{n}词被异化","☠️ POISON ZONE {h} · {n} core words corrupted"],
};
function renderLog(e){
 const key=e.type+"."+e.k;const tpl=LOGK[key];
 const p=Object.assign({},e.p||{});
 if(key==='death.death'&&p.c&&p.c.indexOf('dc_')===0)p.c=T(p.c);
 if(LANG==='en'&&typeof p.src==='string'){
  if(p.src.indexOf('化石')>=0)p.src='fossil inheritance';
  else if(p.src.indexOf('全新')>=0||p.src.indexOf('随机')>=0)p.src='fresh random draw';}
 if(!tpl)return "["+e.type+"] "+e.k+" "+JSON.stringify(p);
 return tpl[LANG==='zh'?0:1].replace(/\{(\w+)\}/g,(_,k)=>p[k]!=null?p[k]:"?");}

const mc={"flow":"#22c55e","curiosity":"#06b6d4","surprise":"#f59e0b","fear":"#ef4444","anxiety":"#d97706",
"boredom":"#94a3b8","anger":"#dc2626","disgust":"#84cc16","satisfaction":"#34d399","sadness":"#3b82f6",
"dying":"#b91c1c","low_power":"#94a3b8","sleeping":"#818cf8","init":"#94a3b8","oscillation":"#e879f9"};
const ec=mc;
const lc={"threat":"#ef4444","anomaly":"#f59e0b","mood":"#38bdf8","sleep":"#818cf8","sys":"#94a3b8",
"death":"#f87171","blind":"#64748b","meal":"#4ade80","explore":"#34d399","eco":"#60a5fa","sense":"#22d3ee",
"expedition":"#2dd4bf","dream":"#fbbf24","glory":"#fde047","mind":"#f0abfc","gene":"#e879f9","bus":"#c084fc",
"world":"#a3e635","sem":"#67e8f9"};
const PORTC={5000:'#a78bfa',5001:'#fbbf24',5002:'#22d3ee',5003:'#34d399',5004:'#f472b6',5005:'#fb923c',5006:'#e879f9',5007:'#38bdf8',5040:'#94a3b8'};
const cport=p=>PORTC[p]||('hsl('+((p*47)%360)+',65%,68%)');
const esc=s=>String(s).replace(/"/g,"&quot;").replace(/</g,"&lt;");
const CHC={"T":"#38bdf8","B":"#4ade80","N":"#fb923c"};
const MOOD_FACE={"flow":"😄","curiosity":"🧐","surprise":"😲","fear":"😨","anxiety":"😰","boredom":"🥱",
"anger":"😠","disgust":"🤢","satisfaction":"😋","sadness":"😢","dying":"😵","low_power":"😪",
"sleeping":"😴","init":"🙂","oscillation":"😐"};
const ACT_COL={"meal":"#4ade80","explore":"#34d399","expedition":"#2dd4bf","blind":"#94a3b8",
"daydream":"#fbbf24","sleep":"#818cf8","nomad":"#38bdf8","init":"#94a3b8","replay":"#c084fc","death":"#ef4444"};
const HCLS={b0:["#141d33","#334155"],b1:["#12331f","#22c55e"],b2:["#16255c","#3b82f6"],b3:["#3a2606","#f59e0b"]};
const SS=34,SQ3=Math.sqrt(3);
const hexXY=(q,r)=>[SS*1.5*q,SS*SQ3*(r+q/2)];
function hexPts(cx,cy,s){let p=[];for(let k=0;k<6;k++){const a=Math.PI/3*k;
 p.push((cx+s*Math.cos(a)).toFixed(1)+","+(cy+s*Math.sin(a)).toFixed(1));}return p.join(" ");}
const shortHost=h=>{h=(h||"").replace(/^www\./,"").replace(/\.(com|cn|net|org)(\.cn)?$/,"");
 return h.length>10?h.slice(0,10)+"…":h;};
const MSAY=k=>T("m_say_"+k);

let lastD=null,bugLayout=[];
function layoutBugs(d){
 const all=[];
 if(d.bug&&d.alive)all.push({q:d.bug.q,r:d.bug.r,name:d.name,port:d.port,
   mood:d.bug.mood,k:d.bug.k,p:d.bug.p||{},sleeping:d.bug.sleeping,roam:false,me:true});
 (d.twins||[]).forEach(t=>{
   if(!t.alive||t.q==null)return;
   all.push({q:t.q,r:t.r,name:t.name,port:t.port,mood:t.mood,k:t.k,p:t.p||{},
     sleeping:t.sleeping,roam:(t.fog==="roam")});});
 const counts={},seen={};
 all.forEach(b=>{const k=b.q+","+b.r;counts[k]=(counts[k]||0)+1;});
 bugLayout=[];
 all.forEach(b=>{const k=b.q+","+b.r;const i=seen[k]||0;seen[k]=i+1;
   const p=hexXY(b.q,b.r);const n=counts[k];let ox=0,oy=0;
   if(n>1){const ang=Math.PI*2*i/n-Math.PI/2,rad=18+4*Math.min(n,6);
     ox=Math.cos(ang)*rad;oy=Math.sin(ang)*rad;}
   b.x=p[0]+ox;b.y=p[1]+oy;bugLayout.push(b);});
}
function renderHexMap(d){
 const g=document.getElementById('hexes');if(!g)return;
 let s="";
 const mk=(hx,fill,stroke,dash,name,sub,tip)=>{const p=hexXY(hx.q,hx.r);
  s+='<polygon points="'+hexPts(p[0],p[1],SS-1.5)+'" fill="'+fill+'" stroke="'+stroke+'" stroke-width="1.4"'+(dash?' stroke-dasharray="5,3"':'')+'><title>'+esc(tip)+'</title></polygon>'
   +'<text class="hexname" x="'+p[0]+'" y="'+(p[1]-1)+'">'+esc(name)+'</text>'
   +'<text class="hexsub" x="'+p[0]+'" y="'+(p[1]+16)+'">'+esc(sub)+'</text>';};
 (d.sites||[]).forEach(t=>{const c=HCLS[t.cls]||HCLS.b0;
  mk(t.hex,c[0],c[1],false,(t.expd?"🧭":"")+shortHost(t.host),
     F('hex_sub',{g:t.gain,v:t.visits}),
     t.host+" · "+(LT["tag_"+t.tag]?T("tag_"+t.tag):t.tag)+" · gain "+t.gain+" · "+t.visits+"v · blind "+t.blind);});
 (d.candidates||[]).forEach(c=>{mk(c.hex,"rgba(30,41,59,.2)","#64748b",true,
     shortHost(c.host),F('cand_sub',{p:c.probes}),
     c.host+" · candidate · instinct "+c.score);});
 g.innerHTML=s;
 const bg=document.getElementById('bugs');let bs="";
 bugLayout.forEach(b=>{
   const col=cport(b.port);
   const face=b.sleeping?"😴":(MOOD_FACE[b.mood]||"🙂");
   const glow=b.roam?"":("filter:drop-shadow(0 0 4px "+col+")");
   const sub=b.roam?"":(((T("act_"+(b.k||"init"))||b.k||"")+" "+(b.k==="meal"?("+"+(b.p.wage||0)):String(b.p.hop?("hop "+b.p.hop):(b.p.site||b.p.why||b.p.w||"")))).trim().slice(0,14));
   const tip=b.roam?F('tip_roam',{n:b.name})
     :F('tip_bug',{n:b.name,me:b.me?T('tip_me'):"",a:T("act_"+(b.k||"init")),d:JSON.stringify(b.p||{}).slice(1,40)});
   bs+='<g transform="translate('+b.x.toFixed(1)+','+b.y.toFixed(1)+')" style="cursor:pointer;opacity:'+(b.roam?0.85:1)+'" onclick="openChain()">'
    +'<title>'+esc(tip)+'</title>'
    +'<text x="0" y="-26" style="font-size:18px;font-weight:700;fill:'+col+';text-anchor:middle;paint-order:stroke;stroke:#0a0f1e;stroke-width:3px">'+esc(b.name)+'</text>'
    +'<text x="0" y="11" style="font-size:46px;text-anchor:middle;'+glow+'">'+face+'</text>'
    +'<text x="0" y="30" style="font-size:13px;fill:'+(ACT_COL[b.k]||'#fde047')+';text-anchor:middle;paint-order:stroke;stroke:#0a0f1e;stroke-width:2.5px">'+esc(sub)+'</text>'
    +'</g>';});
 bg.innerHTML=bs;
 const mh=document.getElementById('maphead');
 if(mh){mh.textContent=F('maphead',{d:d.day,f:d.frame,sc:d.sem.clusters,nb:d.sem.nb,sw:d.sem.words,
  mw:d.mem_size,bw:d.birth_words,e:d.episodes,p:Math.round(d.dream_payoff*100),
  k:((d.twins||[]).filter(t=>t.alive).length)+1,
  dn:(d.bug&&d.bug.sleeping)?T('daynight_night'):T('daynight_day')});}
 const ms=document.getElementById('mapside');
 if(ms){const bm=d.bug?(d.bug.mood||'init'):'init';
  ms.innerHTML=F('ms_energy',{e:d.energy})
   +'<div class="bar" style="margin:2px 0 6px"><div style="width:'+Math.max(0,Math.min(100,d.energy))+'%;background:'+(d.energy<25?'#ef4444':'#4ade80')+'"></div></div>'
   +F('ms_d',{d:d.d_proxy,s:d.self_err,b:d.base_err})
   +'<br>'+F('ms_mood',{m:MSAY(bm),c:(mc[bm]||'#94a3b8'),f:(MOOD_FACE[bm]||"")})
   +'<br>'+F('ms_wage',{g:d.gain_ema,x:d.explored,d:d.discovered,e:d.expeditions})
   +(d.blackouts?F('ms_black',{b:d.blackouts}):"");}
}
/* ---- zoom-to-fit viewBox ---- */
let vbCur=null,vbTgt=null,vbFrom=null,vbT0=0,vbAnim=null;
function computeVB(){
 let x0=0,x1=0,y0=0,y1=0;
 const add=(x,y,px,py)=>{x0=Math.min(x0,x-px);x1=Math.max(x1,x+px);y0=Math.min(y0,y-py);y1=Math.max(y1,y+py);};
 (lastD&&lastD.sites||[]).forEach(t=>{const p=hexXY(t.hex.q,t.hex.r);add(p[0],p[1],SS+52,SS+20);});
 ((lastD&&lastD.candidates)||[]).forEach(c=>{const p=hexXY(c.hex.q,c.hex.r);add(p[0],p[1],SS+52,SS+20);});
 bugLayout.forEach(b=>add(b.x,b.y,64,58));
 const M=46;
 let w=Math.min(Math.max((x1-x0)+2*M,560),1700);
 let h=Math.min(Math.max((y1-y0)+2*M,560),1700);
 return {x:(x0+x1)/2-w/2,y:(y0+y1)/2-h/2,w:w,h:h};
}
function setVBTarget(v){
 if(!vbCur){vbCur=Object.assign({},v);vbTgt=v;applyVB();return;}
 if(vbTgt&&Math.abs(v.w-vbTgt.w)<vbTgt.w*0.02&&Math.abs(v.h-vbTgt.h)<vbTgt.h*0.02
   &&Math.abs(v.x-vbTgt.x)<vbTgt.w*0.02&&Math.abs(v.y-vbTgt.y)<vbTgt.h*0.02)return;
 vbFrom=Object.assign({},vbCur);vbTgt=v;vbT0=performance.now();
 if(!vbAnim)vbAnim=requestAnimationFrame(vbStep);}
function vbStep(ts){
 const t=Math.min(1,(ts-vbT0)/600),e=1-Math.pow(1-t,3);
 const L=(a,b)=>a+(b-a)*e;
 vbCur={x:L(vbFrom.x,vbTgt.x),y:L(vbFrom.y,vbTgt.y),w:L(vbFrom.w,vbTgt.w),h:L(vbFrom.h,vbTgt.h)};
 applyVB();
 if(t<1)vbAnim=requestAnimationFrame(vbStep);else vbAnim=null;}
function applyVB(){const el=document.getElementById('hexmap');
 if(el&&vbCur)el.setAttribute('viewBox',vbCur.x.toFixed(1)+' '+vbCur.y.toFixed(1)+' '+vbCur.w.toFixed(1)+' '+vbCur.h.toFixed(1));}

function topKey(c){let k=null,v=-1e9;for(const[a,b]of Object.entries(c||{}))if(b>v){v=b;k=a;}return k;}
function speak(d){
 if(!d.alive)return F('sp_dead',{c:T(d.death_cause||"dc_stress")});
 if(d.paused)return T('sp_paused');
 if(d.sleeping){let s=F('sp_night',{d:d.day,q:Math.round((d.nq||0)*100),h:d.harvest||0});
  if(d.replay)s+=F('sp_night_dream',{w:Object.keys(d.replay.words||{}).slice(0,5).join("、")});
  return s+T('sp_night_end');}
 if(d.energy<25)return T('sp_hungry')+(d.longevity?(" "+T('sp_hungry_b')):"");
 const dec=d.decision||{};
 if(!dec.mode)return T('sp_wake');
 if(dec.mode==="vacancy")return T('sp_vacant');
 const r=topKey(dec.comp);
 let s=F('sp_going',{m:MSAY(d.mood),s:dec.site||"?"});
 if(r)s+=" "+F('sp_because',{r:T("c_"+r)||r});
 const sup=d.A-d.tau;
 s+=sup>0.25?T('sp_sup_hi'):(sup<0.02?T('sp_sup_lo'):"。");
 if(d.goal)s+=" "+F('sp_goal',{w:(d.goal.words||[]).slice(0,3).join("/")});
 if(d.organ_on)s+=" "+T('sp_mind');
 return s;}
function sayProfile(p){
 const cur=p.curiosity>1.2?(LANG==='zh'?"好奇心爆棚":"very curious"):(p.curiosity>0.9?(LANG==='zh'?"好奇心中等":"moderately curious"):(LANG==='zh'?"偏佛系":"chill"));
 const st=p.stress_k>4?(LANG==='zh'?"神经非常敏感":"highly sensitive"):(p.stress_k>2.8?(LANG==='zh'?"容易紧张":"jumpy"):(LANG==='zh'?"心态稳":"steady"));
 const lr=p.lr>0.18?(LANG==='zh'?"学得快忘得快":"fast to learn, fast to fade"):(p.lr>0.13?(LANG==='zh'?"记忆节奏适中":"moderate memory"):(LANG==='zh'?"记得牢更新慢":"sticky memory, slow updates"));
 const bo=p.boldness>0.5?(LANG==='zh'?"敢冒险":"bold"):(LANG==='zh'?"偏谨慎":"cautious");
 const en=p.entropy>8?(LANG==='zh'?"兴趣很杂":"wide interests"):(p.entropy>5?(LANG==='zh'?"兴趣较集中":"focused interests"):(LANG==='zh'?"兴趣非常专一":"narrow & deep"));
 return cur+"、"+st+"、"+lr+"、"+bo+"、"+en;}
function sayGates(g,wg){const e=Object.entries(g).sort((a,b)=>b[1]-a[1]);
 let s=(LANG==='zh'?"天生警报词:":"innate alarm words:")+e.slice(0,4).map(([w,v])=>w+" "+v.toFixed(2)).join(" · ");
 if(wg)s+=(LANG==='zh'?" · 🔗词图:":" · 🔗 word graph:")+wg.words+(LANG==='zh'?"词/":"w/")+wg.edges+(LANG==='zh'?"边":"e");
 return s;}
function sayAppetite(a){const e=Object.entries(a);if(!e.length)return LANG==='zh'?"还没吃出口味经验。":"No taste experience yet.";
 e.sort((x,y)=>y[1]-x[1]);const hi=e[0],lo=e[e.length-1];
 let s=(LANG==='zh'?"吃出来的口味:心情「":"Learned taste: best appetite in mood 「")+hi[0]+"」(×"+hi[1].toFixed(2)+")";
 if(lo[1]<0.9)s+=(LANG==='zh'?",「":", worst in 「")+lo[0]+"」(×"+lo[1].toFixed(2)+")";
 return s+"。";}
function renderBirth(bg){
 const el=document.getElementById('birthgenes');if(!el)return;
 if(!bg){el.innerHTML="(—)";return;}
 const gs=Object.entries(bg.genes||{}).map(([k,v])=>
  '<span class="chip" title="'+esc(k)+'">'+esc(T("g_"+k)||k)+" = "+v+"</span>").join(" ");
 el.innerHTML='<div style="margin:4px 0">'+gs+"</div>"
  +"<div>"+(LANG==='zh'?"天生兴趣词:":"innate seed words: ")+(bg.seeds||[]).map(w=>esc(w)).join("、")+"</div>"
  +'<div class="lab">'+(LANG==='zh'?"出身:":"origin: ")+esc(bg.origin||"?")+" · "+esc(bg.born_at||"")+"</div>";}
function renderListen(L){
 const cell=(arr,ci)=>{const v=arr?arr[ci]:null;
  if(v==null)return '<td class="lab">—</td>';
  const a=Math.min(.85,Math.abs(v)*3);
  return '<td><span style="background:rgba(56,189,248,'+a.toFixed(2)+');border-radius:3px;padding:0 4px">'+v.toFixed(3)+"</span></td>";};
 document.getElementById('listenT').innerHTML=BCH.map((ch,ci)=>
  '<tr><td class="lab">'+T("b_"+ch)+"</td>"+cell(L.W,ci)+cell(L.I,ci)+cell(L.J,ci)+cell(L.G,ci)+"</tr>").join('')
  ||'<tr><td colspan="5" class="lab">—</td></tr>';}
const BCH=["w_a0","w_drop","w_graw","w_herr","i_pA","i_pDE","i_pG","i_val","i_aro","j_conf","j_errA","g_net","g_corr","g_ledger","noise"];
let TAU=0.6,tombClosed=false;
function drawChart(h){const cv=document.getElementById('chart');const w=cv.clientWidth,ht=150;
 cv.width=w*2;cv.height=ht*2;const c=cv.getContext('2d');c.scale(2,2);c.clearRect(0,0,w,ht);
 c.strokeStyle='#1e293b';c.strokeRect(0.5,0.5,w-1,ht-1);const N=h.length;if(!N)return;
 const x=i=>i/Math.max(N-1,1)*(w-10)+5;const yA=v=>ht-6-(ht-16)*Math.max(0,Math.min(1,v));
 c.lineWidth=1;c.strokeStyle='#f59e0b';c.setLineDash([5,4]);c.beginPath();let pen=false;
 for(let i=0;i<N;i++){const p=h[i];if(p[1]<0){pen=false;continue;}
  if(!pen){c.moveTo(x(i),yA(TAU));pen=true;}else c.lineTo(x(i),yA(TAU));}
 c.stroke();c.setLineDash([]);c.strokeStyle='#38bdf8';c.lineWidth=1.5;c.beginPath();pen=false;
 for(let i=0;i<N;i++){const p=h[i];if(p[1]<0){pen=false;continue;}
  if(!pen){c.moveTo(x(i),yA(p[1]));pen=true;}else c.lineTo(x(i),yA(p[1]));}
 c.stroke();c.strokeStyle='#4ade80';c.lineWidth=1.2;c.beginPath();pen=false;
 for(let i=0;i<N;i++){const p=h[i],Y=ht-6-(ht-16)*(p[2]/140);
  if(!pen){c.moveTo(x(i),Y);pen=true;}else c.lineTo(x(i),Y);}c.stroke();}
function hbars(el,items,cfix){if(!el||!items||!items.length)return;
 const mx=Math.max.apply(null,items.map(i=>i.p).concat([1e-6]));
 el.innerHTML=items.map(i=>{const pre=i.w.slice(0,1);const col=cfix||CHC[pre]||'#60a5fa';
  const wPct=Math.max(1.5,Math.min(100,i.p/mx*100));
  return '<div style="display:flex;align-items:center;margin:1px 0"><span class="lab" style="width:96px;color:'+col+';overflow:hidden;white-space:nowrap">'+esc(i.w)+'</span><div style="flex:1;background:#1e293b;border-radius:3px;height:8px"><div style="width:'+wPct+'%;height:100%;background:'+col+';border-radius:3px"></div></div><span class="lab" style="width:52px;text-align:right">'+i.p.toFixed(4)+"</span></div>";}).join('');}
function strip(cv,vals,negC,posC){if(!cv||!vals||!vals.length)return;
 const w=cv.clientWidth||300,hgt=cv.clientHeight||36;cv.width=w*2;cv.height=hgt*2;
 const c=cv.getContext('2d');c.scale(2,2);c.clearRect(0,0,w,hgt);
 const n=vals.length,bw=w/n;
 vals.forEach((v,i)=>{const t=Math.max(-1,Math.min(1,v));const bh=Math.abs(t)*(hgt/2-2);
  c.fillStyle=t>=0?posC:negC;c.fillRect(i*bw+.5,t>=0?hgt/2-bh:hgt/2,Math.max(1,bw-1),bh);});
 c.strokeStyle='#334155';c.beginPath();c.moveTo(0,hgt/2);c.lineTo(w,hgt/2);c.stroke();}
function spark(cv,vals,color){if(!cv)return;
 const w=cv.clientWidth||300,hgt=cv.clientHeight||24;cv.width=w*2;cv.height=hgt*2;
 const c=cv.getContext('2d');c.scale(2,2);c.clearRect(0,0,w,hgt);
 if(!vals||vals.length<2)return;const mx=Math.max(...vals,0.05);
 c.strokeStyle=color;c.lineWidth=1.2;c.beginPath();
 vals.forEach((v,i)=>{const x=i/(vals.length-1)*w,y=hgt-2-(hgt-4)*(v/mx);
  i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
function setModal(t){document.getElementById('modal').style.display='block';
 document.getElementById('mtitle').textContent=t;
 document.getElementById('mtools').innerHTML='';
 document.getElementById('mbody').innerHTML='<span class="lab">…</span>';}
function closeModal(){document.getElementById('modal').style.display='none';}
document.getElementById('modal').addEventListener('click',e=>{if(e.target.id==='modal')closeModal();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});
let vocabData=null;
async function openVocab(){setModal(T('vocab_title'));
 vocabData=await(await fetch('/api/vocab')).json();
 document.getElementById('mtools').innerHTML='<input id="vfilter" placeholder="'+T('vocab_filter_ph')+'" style="width:220px" oninput="renderVocab()">';
 renderVocab();}
function renderVocab(){if(!vocabData)return;
 const q=(document.getElementById('vfilter')||{value:''}).value.trim();
 const NMS=["curiosity","flow","surprise","fear","anxiety","boredom","anger","disgust","satisfaction","sadness"];
 const groups={};NMS.forEach(n=>groups[n]=[]);groups['alarm']=[];groups['none']=[];
 let shown=0;
 for(const it of vocabData.items){
  if(q&&it.w.indexOf(q)<0)continue;shown++;
  if(it.red){groups['alarm'].push(it);continue;}
  let dom='none',bv=0;
  for(const[k,v]of Object.entries(it.e))if(v>bv){bv=v;dom=k;}
  groups[dom].push(it);}
 let html='<div class="lab">'+F('vocab_info',{n:vocabData.n,s:shown})+'</div>';
 const order=['alarm'].concat(NMS).concat(['none']);
 for(const g of order){const arr=groups[g];if(!arr.length)continue;
  const col=(g==='alarm')?'#ef4444':(ec[g]||'#60a5fa');
  const gname=(g==='alarm')?T('vocab_group_alarm'):(g==='none')?T('vocab_group_none'):MSAY(g);
  html+='<div class="vgroup"><h4 style="color:'+col+'">'+gname+' · '+arr.length+'</h4>'+
   arr.slice(0,400).map(it=>'<span class="vword" data-w="'+esc(it.w)+'" onclick="openChain(this.dataset.w)" style="cursor:pointer;color:'+col+';'+(it.red?'border-color:#ef4444;':'')+'" title="'+esc(it.w)+' · '+it.c+' · $g '+it.g+'">'+esc(it.w)+'</span>').join('')+
   (arr.length>400?'<span class="lab">'+F('vocab_more',{n:arr.length-400})+'</span>':'')+'</div>';}
 document.getElementById('mbody').innerHTML=html||'<span class="lab">—</span>';}
let semData=null,semSel=null;
async function openSem(){setModal(T('sem_title'));
 semData=await(await fetch('/api/sem')).json();
 semSel=semData.buckets.length?semData.buckets[0].b:null;
 document.getElementById('mtools').innerHTML='<span class="lab">'+F('sem_engine',{e:semData.engine,u:semData.used,n:semData.nb,w:semData.words,t:semData.thresh,e2:semData.empty})+'</span> <input id="sfilter" placeholder="'+T('sem_filter_ph')+'" style="width:220px" oninput="renderSem()">';
 renderSem();}
function renderSem(){if(!semData)return;
 const q=(document.getElementById('sfilter')||{value:''}).value.trim();
 let bs=semData.buckets;
 if(q)bs=bs.filter(b=>String(b.b)===q||b.words.some(w=>w.indexOf(q)>=0));
 const sel=bs.find(b=>b.b===semSel)||bs[0];
 let html='<div style="display:flex;gap:12px"><div style="width:320px;max-height:62vh;overflow-y:auto;flex-shrink:0">';
 if(!bs.length)html+='<span class="lab">'+T('sem_none')+'</span>';
 html+=bs.slice(0,500).map(b=>'<div class="vword" style="display:block;cursor:pointer;'+(sel&&b.b===sel.b?'border-color:#fbbf24;':'')+'" onclick="semSel='+b.b+';renderSem()">'+F('sem_row',{b:b.b,n:b.n,s:(b.share*100).toFixed(1),p:b.p})+'</div>').join('');
 if(bs.length>500)html+='<span class="lab">'+T('sem_more')+'</span>';
 html+='</div><div style="flex:1;max-height:62vh;overflow-y:auto">';
 if(sel){const whtml=sel.words.map(w=>'<span class="vword" data-w="'+esc(w)+'" onclick="openChain(this.dataset.w)" style="cursor:pointer">'+esc(w)+'</span>').join('')||'<span class="lab">(—)</span>';
  html+='<h4 style="color:#93c5fd;margin:4px 0">'+F('sem_sel',{b:sel.b,n:sel.n})+'</h4>'+whtml;}
 else html+='<span class="lab">'+T('sem_pick')+'</span>';
 html+='</div></div>';
 document.getElementById('mbody').innerHTML=html;}
async function openEpi(){setModal(T('epi_title'));
 const d=await(await fetch('/api/episodes')).json();
 document.getElementById('mbody').innerHTML=d.episodes.map(e=>'<div class="vword" style="display:block;margin:6px 0;line-height:1.8">'+
  '<b>'+(LANG==='zh'?"第":"day ")+e.day+(LANG==='zh'?"天":"")+' @'+esc(e.host)+'</b> · A='+e.A+' · gain='+e.gain+' · '+e.weight+'<br>'+
  T('epi_emo')+e.emo.map(x=>MSAY(x[0])+' '+x[1]).join(' · ')+'<br>'+
  T('epi_words')+e.words.map(x=>'<span class="vword" data-w="'+esc(x[0])+'" onclick="openChain(this.dataset.w)" style="cursor:pointer">'+esc(x[0])+'</span>').join(' ')+'</div>').join('')||'<span class="lab">'+T('epi_none')+'</span>';}
async function openChain(seed){
 setModal(T('chain_title'));
 document.getElementById('mtools').innerHTML='<input id="cseed" placeholder="'+T('chain_ph')+'" style="width:260px" onkeydown="if(event.key===\'Enter\')runChain()"> '+
  '<select id="cdepth"><option value="5">5</option><option value="10" selected>10</option><option value="20">20</option></select> '+
  '<label style="font-size:12px;color:#64748b"><input type="checkbox" id="cdet"> '+T('chain_det')+'</label> '+
  '<button class="green" onclick="runChain()">'+T('chain_go')+'</button> <span class="lab" id="cwarn"></span>';
 document.getElementById('mbody').innerHTML='<div class="lab" style="line-height:1.9">'+T('chain_info')+
  (seed?'':T('chain_hint'))+'</div>';
 if(seed){document.getElementById('cseed').value=seed;runChain();}}
async function runChain(){
 const si=document.getElementById('cseed');if(!si)return;
 const q=si.value.trim();if(!q)return;
 const depth=document.getElementById('cdepth').value;
 const det=document.getElementById('cdet').checked?'1':'0';
 document.getElementById('mbody').innerHTML='<span class="lab">'+T('chain_run')+'</span>';
 let d;
 try{d=await(await fetch('/api/chain?q='+encodeURIComponent(q)+'&depth='+depth+'&det='+det)).json();}
 catch(e){document.getElementById('mbody').innerHTML='<span class="lab">'+T('chain_fail')+'</span>';return;}
 if(d.err){document.getElementById('mbody').innerHTML='<span class="lab" style="color:#f59e0b">'+esc(F(d.err,{q:d.q||q}))+'</span>';
  document.getElementById('cwarn').textContent='';return;}
 document.getElementById('cwarn').textContent=(d.mode==='bridge'?T('chain_warn_b'):T('chain_warn_g'))+
  F('chain_warn_end',{w:d.stats.words,e:d.stats.edges,d:d.det?" · "+T('chain_det2'):""})+')';
 let html='<div style="line-height:2.4;font-size:14px;margin:10px 0">';
 html+='<span class="vword" style="border-color:#fbbf24;color:#fbbf24;font-size:15px">'+esc(q)+'</span>';
 for(const s of d.chain){
  html+=' <span class="lab">→</span> <span class="vword" data-w="'+esc(s.w)+'" onclick="document.getElementById(\'cseed\').value=this.dataset.w;runChain()" style="cursor:pointer" title="'+T('chain_wgt')+' '+s.wgt+' · '+T('chain_more')+'">'+esc(s.w)+' <span class="lab">'+s.wgt+'</span></span>';}
 html+='</div>';
 if(!d.chain.length)html+='<div class="lab">'+F('chain_empty',{q:q})+'</div>';
 html+='<div class="lab" style="margin-top:8px">'+T('chain_foot')+'</div>';
 document.getElementById('mbody').innerHTML=html;}
async function poll(){try{
 const d=await(await fetch('/api/state')).json();TAU=d.tau;lastD=d;
 document.getElementById('who').textContent=d.name+" :"+d.port;
 document.getElementById('resbadge').textContent="×"+(d.coupling!=null?d.coupling:"?");
 document.getElementById('lbadge').style.display=d.longevity?'inline-block':'none';
 document.getElementById('longbanner').style.display=d.longevity?'block':'none';
 document.getElementById('lamp').style.background=d.paused?'#475569':(mc[d.mood]||"#94a3b8");
 document.getElementById('mode').textContent="〔"+(d.mode==='lab'?T('mode_lab'):T('mode_field'))+" · "+d.cur+" · "+MSAY(d.mood)+"〕"+(d.paused?"〔⏸〕":"");
 document.getElementById('runbtn').textContent=d.paused?'▶':'⏸';
 document.getElementById('sentence').textContent=speak(d);
 document.getElementById('energy').textContent=d.energy;
 const eb=document.getElementById('ebar');eb.style.width=Math.max(0,Math.min(100,d.energy))+"%";eb.style.background=d.energy<25?'#ef4444':'#4ade80';
 document.getElementById('av').textContent=d.A+" / "+d.tau;
 document.getElementById('msub_av').textContent=(LANG==='zh'?"Δ=":"Δ=")+(d.A-d.tau).toFixed(2);
 document.getElementById('g').textContent=d.gain;
 document.getElementById('msub_gain').textContent=T('sat_lbl')+(d.satiety*100).toFixed(1)+"%";
 document.getElementById('hv').textContent=(d.harvest||0);
 document.getElementById('msub_hv').textContent=F('nq_lbl',{v:Math.round((d.nq||0)*100)});
 document.getElementById('sem').textContent=d.sem.clusters;
 document.getElementById('msub_sem').textContent=d.sem.words+" · "+(d.sem.engine==="hash"?T('sem_hash'):T('sem_emb'))+"·θ"+d.sem.thresh;
 const hh=document.getElementById('hhi');hh.textContent=d.hhi;hh.style.color=d.hhi>0.25?'#ef4444':'#cbd5e1';
 document.getElementById('ep').textContent=d.episodes;
 document.getElementById('msub_ep').textContent=F('ep_lbl',{n:d.episodes,p:Math.round(d.dream_payoff*100)});
 document.getElementById('mem').textContent=d.mem_size+" / "+d.birth_words;
 document.getElementById('msub_mem').textContent=(LANG==='zh'?"白跑空趟:":"blind frames:")+d.total_blind;
 document.getElementById('xpl').textContent=d.explored+"/"+d.discovered;
 document.getElementById('msub_xpl').textContent=F('xpl_lbl',{s:d.explored,d:d.discovered});
 const sr=document.getElementById('serr');sr.textContent=d.self_err+" / "+d.base_err;sr.style.color=d.self_err<d.base_err?'#4ade80':'#f59e0b';
 document.getElementById('day').textContent=d.day;
 document.getElementById('msub_day').textContent=F('day_lbl',{d:d.day,f:d.frame})
  +" · ☠"+d.stats.deaths+" · ⚠"+d.stats.threats+" · ⚡"+(d.blackouts||0);
 document.getElementById('dbadge').textContent="d≈"+d.d_proxy;
 document.getElementById('stbadge').textContent=d.selftest===true?T('stself_pass'):d.selftest===false?T('stself_warn'):T('stself_na');
 var bl=document.getElementById("busline");
 if(bl){bl.textContent=T('bus_pre')+Object.entries(d.bus||{}).filter(([k])=>k!=="noise").map(([k,v])=>T("b_"+k)+" "+v).join(" · ");}
 document.getElementById('profline').textContent=T('prof_pre')+sayProfile(d.profile);
 document.getElementById('siteslab').textContent=F('siteslab',{n:d.sites_n,c:d.sites_cap});
 document.getElementById('emobars').innerHTML=Object.entries(d.emotions_frame||d.emotions).map(([k,v])=>{
  const col=ec[k]||'#64748b';const acc=(d.emotions||{})[k]||0;
  return '<div class="erow" title="'+esc(MSAY(k))+'"><span class="el">'+MSAY(k)+"</span> "+v.toFixed(2)+' <span class="lab" style="color:#475569">'+acc.toFixed(2)+'</span><div class="ebar"><div style="width:'+(acc*100).toFixed(0)+'%;background:#334155"></div><div style="width:'+(v*100).toFixed(0)+'%;background:'+col+';margin-top:-7px"></div></div></div>';}).join('');
 document.getElementById('moodaccline').textContent=F('moodaccline',{
  ev:((d.mood_event||"").split("+").map(k=>T("ev_"+k)||k).join("+"))||"—",
  acc:MSAY(d.mood_acc)});
 const gb=document.getElementById('goalbanner');
 if(d.goal){gb.style.color='#fbbf24';
  gb.innerHTML=F('goal_b',{s:d.goal.strength,e:d.goal.emo.map(e=>MSAY(e[0])+" "+e[1].toFixed(2)).join(' + '),
    w:d.goal.words.join('/'),h:d.goal.host})+' <span class="lab">'+T('goal_note')+'</span>';}
 else{gb.style.color='#475569';gb.innerHTML=T('no_goal');}
 const dec=d.decision||{};
 const compStr=dec.comp?Object.entries(dec.comp).map(([k,v])=>(T("c_"+k)||k)+(v>=0?'+':'')+v).join(' '):'';
 document.getElementById('decision').innerHTML=F('dec_doing',{m:T("dm_"+(dec.mode||"vacancy")),s:dec.site||'-',md:MSAY(dec.mood||"init")})+T('dec_base')+"<br>"+
  F('dec_why',{c:compStr||'-'})+"<br>"+
  F('dec_hand',{t:(((dec.hand_top||dec.top3)||[]).map(t=>t[0]+'('+t[1]+')').join(' · '))})+
  (dec.net_top?("<br>"+F('dec_net',{t:dec.net_top.map(t=>t[0]+'('+t[1]+')').join(' · '),a:dec.alpha})):'');
 document.getElementById('appetite').textContent=sayAppetite(d.appetite);
 document.getElementById('selfline').textContent=F('selfline',{b:d.self_profile.bored_ema,
  e:d.self_profile.explore_ratio,m:(dec.self_mod!=null?((dec.self_mod>=0?'+':'')+dec.self_mod):'-')});
 document.getElementById('simline').innerHTML=T('sim_pre')+(d.sim_text&&d.sim_text.mode
  ?("["+(LANG==='zh'?d.sim_text.mode:d.sim_text.mode)+"@"+d.sim_text.site+"] "+(LANG==='zh'?"预计A":"predA")+"≈"+d.sim_text.pA+" · "+(LANG==='zh'?"gain":"gain")+"≈"+d.sim_text.pG+" · "+d.sim_text.emo+" · ΔE≈"+d.sim_text.dE+" · "+(LANG==='zh'?"置信":"conf")+d.sim_text.conf+"%")
  :"—");
 document.getElementById('nstat').textContent=d.organ_on?F('nstat_on',{s:d.organ_steps}):T('nstat_off');
 const th=d.thought||{};
 if(th.pred&&th.pred.length)hbars(document.getElementById('wpred'),th.pred);
 if(th.real&&th.real.length)hbars(document.getElementById('wreal'),th.real);
 if(th.hidden)strip(document.getElementById('whidden'),th.hidden,'#f472b6','#38bdf8');
 document.getElementById('wmeta').innerHTML=th.host?F('wm_line',{h:esc(th.host),a0:th.a0,a1:th.a1,g:th.graw,e:th.herr}):T('wait_first');
 const it=d.ithought||{};
 if(it.predE&&it.realE){const NM=["curiosity","flow","surprise","fear","anxiety","boredom","anger","disgust","satisfaction","sadness"];
 document.getElementById('iemo').innerHTML=NM.map((nm,i)=>{const pv=Math.max(0,Math.min(1,it.predE[i])),rv=Math.max(0,Math.min(1,it.realE[i]));
  return '<div style="display:flex;align-items:center;margin:1px 0"><span class="lab" style="width:76px">'+MSAY(nm)+'</span><div style="flex:1;background:#1e293b;height:8px;border-radius:3px;position:relative"><div style="width:'+(rv*100).toFixed(0)+'%;height:100%;background:#4ade80;opacity:.5"></div><div style="width:'+(pv*100).toFixed(0)+'%;height:100%;background:#38bdf8;position:absolute;top:0;opacity:.75"></div></div><span class="lab" style="width:90px;text-align:right">'+F('it_row',{p:pv.toFixed(2),r:rv.toFixed(2)})+"</span></div>";}).join('')
  +'<div style="display:flex;align-items:center;margin:1px 0"><span class="lab" style="width:76px;color:#fbbf24">'+T('it_wage')+'</span><div style="flex:1;background:#1e293b;height:8px;border-radius:3px;position:relative"><div style="width:'+Math.min(100,it.G/0.8*100).toFixed(0)+'%;height:100%;background:#4ade80;opacity:.5"></div><div style="width:'+Math.min(100,it.predG/0.8*100).toFixed(0)+'%;height:100%;background:#fbbf24;position:absolute;top:0;opacity:.75"></div></div><span class="lab" style="width:90px;text-align:right">'+F('it_row',{p:it.predG,r:it.G})+"</span></div>";}
 if(it.xin&&it.xin.length)strip(document.getElementById('iinput'),it.xin,'#64748b','#a5b4fc');
 document.getElementById('imeta').innerHTML=(it.predA!=null&&it.xin&&it.xin.length)
  ?((LANG==='zh'?"猜的意外=":"pred surprise=")+it.predA+"/"+it.A+" · ΔE="+it.predDE+"/"+it.DE+" · "+(LANG==='zh'?"心情误差":"mood err")+"="+it.err+" · "+(LANG==='zh'?"收获误差":"gain err")+"="+it.errG)
  :T('wait_first');
 const jt=d.jthought||{};
 document.getElementById('jmeta').innerHTML=jt.errA_pred!=null
  ?((LANG==='zh'?"它预测『自己的预测会有多不准』:<br>意外头误差:预测":"It predicts how wrong its own predictions are:<br>surprise-head err: pred ")+
    '<b style="color:#c084fc">'+jt.errA_pred+"</b> / "+'<span style="color:#4ade80">'+jt.errA+"</span>"+
    (jt.base_mae!=null?((LANG==='zh'?"(瞎猜基线":"(naive baseline ")+jt.base_mae+","+(jt.errA_pred<jt.base_mae?'<span style="color:#4ade80">✓</span>':'<span style="color:#f59e0b">⚠</span>')+")"):'')+
    "<br>"+(LANG==='zh'?"总误差:":"total err: ")+jt.errT_pred+"/"+jt.errT+"<br>conf="+jt.conf+" → "+(LANG==='zh'?"不自信时更敢闯(探索×":"less confidence → bolder (explore x")+'<b style="color:#fbbf24">'+jt.expl_boost+"</b>)")
  :T('wait_first');
 document.getElementById('jline').textContent=F('jt_line',{e:d.j_err_ema});
 const gt=d.gthought||{};
 document.getElementById('gmeta').innerHTML=gt.alpha==null?T('gt_none'):F('gt_line',{a:gt.alpha,c:gt.corr,n:gt.n,
  lc:gt.ledger>=0?'#4ade80':'#f59e0b',l:(gt.ledger>=0?'+':'')+gt.ledger,
  r:(gt.recent&&gt.recent.length?gt.recent.map(r=>r[2]).join(' '):'—')});
 renderListen(d.listen||{});
 spark(document.getElementById('cv_w'),d.curves.w,'#38bdf8');
 spark(document.getElementById('cv_i'),d.curves.i,'#4ade80');
 spark(document.getElementById('cv_j'),d.curves.j,'#c084fc');
 spark(document.getElementById('cv_g'),d.curves.g.map(v=>(v+1)/2),'#fbbf24');
 document.getElementById('genes').innerHTML=F('genes_line',{b:d.genes.trust_birth,n:d.genes.trust_now,
  w:d.genes.lr_w,i:d.genes.lr_i,c:d.genes.corr_gain,cg:d.coupling});
 renderBirth(d.birth_genes);
 document.getElementById('sitesT').innerHTML=d.sites.map(s=>{
  const tagSay=T("tag_"+s.tag)||(s.tag);
  return '<tr><td><span class="src '+s.cls+'">'+tagSay+"</span>"+(s.fresh?' 🆕':'')+(s.expd?' 🧭':'')+"</td>"+
  '<td title="'+esc(s.url)+'">'+(s.human?'★':'')+esc(s.host)+"</td><td>"+s.gain+"</td><td>"+s.visits+"</td><td>"+s.blind+"</td>"+
  "<td>"+s.trust+'</td><td style="color:'+(s.sate>0.5?'#f59e0b':'#cbd5e1')+'">'+s.sate+"</td>"+
  '<td class="lab" style="color:#93c5fd">'+esc(s.pal||'-')+"</td>"+
  '<td class="lab">'+(s.depth>1?F('site_hop',{d:s.depth,p:esc(s.parent)}):T('site_direct'))+"</td></tr>";}).join('')
  ||'<tr><td colspan="9" class="lab">—</td></tr>';
 document.getElementById('candT').innerHTML=d.candidates.map(c=>'<tr><td title="'+esc(c.url)+'">'+esc(c.host)+(c.expd?' 🧭':'')+"</td><td>"+c.score+"</td>"+
  '<td style="color:#fbbf24">'+(c.net!=null?c.net:'—')+(c.loc?(c.loc>0?'<span style="color:#4ade80">+'+c.loc+'</span>':'<span style="color:#ef4444">'+c.loc+'</span>'):'')+"</td>"+
  "<td>"+F('cand_probes',{p:c.probes,l:(c.late!=null?c.late:'—')})+'</td><td class="lab">'+(c.depth>1?F('site_hop',{d:c.depth,p:esc(c.parent)}):T('site_direct'))+' <span style="color:'+cport(c.by_port)+'">'+esc(c.by)+"</span></td></tr>").join('')
  ||'<tr><td colspan="5" class="lab">—</td></tr>';
 layoutBugs(d);renderHexMap(d);setVBTarget(computeVB());
 drawChart(d.history);
 document.getElementById('cloud').innerHTML=d.top_words.map(t=>{const col=t.red?'#ef4444':(ec[t.e]||'#60a5fa');
  return '<span style="color:'+col+';font-size:'+(12+Math.min(24,t.c/3))+'px" title="'+esc(MSAY(t.e)||'?')+'">'+t.w+"</span>";}).join('');
 document.getElementById('gates').textContent=sayGates(d.gates,d.wg);
 document.getElementById('twins').innerHTML=(d.twins||[]).length?d.twins.map(t=>{
  let s='<div class="tw"><div><b style="color:'+cport(t.port)+'">'+t.name+'</b> <a href="http://127.0.0.1:'+t.port+'" target="_blank" style="color:#93c5fd">'+F('tw_open',{p:t.port})+"</a> ";
  s+=t.alive?((t.paused?"⏸ ":"")+(t.sleeping?"🌙 ":"")+(t.fog==="roam"?"🌫 ":"")+(MSAY(t.mood)||t.mood)+" E"+t.energy+" A"+t.A):("☠ "+T(t.death_cause||""));
  s+='</div><div class="lab">'+F('tw_row',{d:t.day,f:t.frame,s:t.sites,dc:t.discovered,dr:t.dreams,e:t.episodes})
   +(t.blackouts?F('tw_black',{b:t.blackouts}):"")
   +' · '+T('tw_diet')+'<b style="color:'+(t.jaccard<0.4?'#4ade80':'#f59e0b')+'">'+t.jaccard+"</b></div>";
  s+='<div style="font-family:Consolas,monospace;font-size:11px;margin-top:3px">'+t.log_tail.map(e=>'<div style="color:'+(lc[e.type]||'#94a3b8')+'">['+e.t+"] "+renderLog(e)+"</div>").join('')+"</div></div>";
  return s;}).join('')
  :'<span class="lab">'+T('twins_none')+'</span>';
 document.getElementById('ledger').innerHTML=(d.ledger||[]).map(e=>'<div style="color:#34d399;padding:1px 0">['+e.t+'] <span style="color:'+cport(e.port)+'">'+esc(e.by)+"@"+e.port+'</span> '+F('led_row',{t:"",p:esc(e.parent),d:e.depth,h:'<span title="'+esc(e.url)+'">'+esc(e.host)+"</span>",a:e.avg_gain,by:"",pt:""}).replace(/^\s+/,'')+"</div>").join('')
  ||'<span class="lab">'+T('ledger_none')+'</span>';
 const lg=document.getElementById('log');
 lg.innerHTML=d.log.map(e=>'<div style="color:'+(lc[e.type]||'#94a3b8')+'">['+e.t+"] "+renderLog(e)+"</div>").join('');
 lg.scrollTop=lg.scrollHeight;
 const tb2=document.getElementById('tomb');
 if(!d.alive){if(!tombClosed){tb2.style.display='block';}
  document.getElementById('cause').textContent=T(d.death_cause||"dc_stress");
  document.getElementById('fossil').textContent=d.fossil;}
 else{tb2.style.display='none';tombClosed=false;}
 document.title=d.alive?("["+d.name+"] "+(MOOD_FACE[d.mood]||"")+MSAY(d.mood)+" E"+d.energy+(d.longevity?"⚡":"")+" :"+d.port):("["+d.name+"] ☠");
}catch(e){console.warn("[poll]",e)}}
async function addSite(){const u=document.getElementById('newurl').value.trim();if(!u)return;
 await fetch('/api/add_site?url='+encodeURIComponent(u),{method:'POST'});
 document.getElementById('newurl').value='';setTimeout(poll,300);}
function post(u){fetch(u,{method:'POST'}).then(()=>setTimeout(poll,300));}
applyStatic();
setInterval(poll,1000);poll();
</script></body></html>"""

def make_app(agent,world):
    app=Flask("placozoa_%s"%agent.g.name)
    @app.route("/")
    def index(): return Response(HTML,mimetype="text/html")
    @app.route("/api/state")
    def api_state():
        with agent.lock:
            top=[]
            for w,c in agent.disp.most_common(30):
                wb=agent.word_birth.get(w)
                e_dom=max(wb["e"].items(),key=lambda kv:kv[1])[0] if wb and wb.get("e") else ""
                top.append({"w":w,"c":round(c,1),"red":w in agent.gate and agent.gate[w]>0.5,"e":e_dom})
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
            _r=agent.replay_last
            replay_out=({"words":dict(list((_r["words"] or {}).items())[:8]),
                         "host":_r.get("host","?")} if _r else None)
            payload={
                "ver":"V4.5.0",
                "name":agent.g.name,"port":agent.port,
                "alive":not agent.dead,"paused":not RUN["on"],"sleeping":agent.sleeping,
                "mode":("field" if H.REAL else "lab"),"cur":agent.current,
                "frame":agent.frame,"day":agent.day,
                "mood":(agent.mood_frame or agent.mood),
                "mood_acc":agent.mood,
                "mood_event":(agent.mood_frame_event or ""),
                "energy":round(agent.energy,1),
                "A":round(agent.last_A,3),"tau":round(agent.tau,3),
                "gain":round(agent.last_gain,2),"gain_ema":round(agent.gain_ema,3),
                "satiety":round(agent.satiety,3),
                "harvest":round(agent.day_harvest,1),"nq":round(agent.night_quality,2),
                "blind":agent.blind_streak,"total_blind":agent.total_blind,
                "mem_size":len(agent.disp),"birth_words":len(agent.word_birth),
                "self_err":ms(agent.err_self),"base_err":ms(agent.err_base),
                "j_err_ema":round(agent.j_err_ema,3),
                "gates":{w:round(g,2) for w,g in agent.gate.items()},
                "top_words":top,"sites":agent._site_view(),
                "sites_n":len(agent.order),"sites_cap":H.MAX_SITES,
                "candidates":agent._frontier_view(),
                "expeditions":agent.expeditions,
                "longevity":bool(getattr(H,"LONGEVITY",False)),
                "blackouts":agent.stats.get("blackouts",0),
                "ledger":list(ECO.discoveries)[-25:][::-1],
                "frontier":len(agent.frontier),
                "explored":agent.stats["explored"],"discovered":agent.stats["discovered"],
                "history":list(agent.A_hist)[-240:],"log":list(agent.log)[-120:],
                "replay":replay_out,
                "birth_genes":agent.birth_genes,
                "stats":agent.stats,
                "emotions":{k:round(v,3) for k,v in agent.em.l.items()},
                "emotions_frame":{k:round(v,3) for k,v in agent.em_f_last.items()},
                "appetite":{k:round(v,2) for k,v in agent.em.appetite.items()},
                "decision":dict(agent.last_decision or {}),
                "goal":goal_out,"episodes":len(agent.episodes),
                "dream_payoff":round(agent.dream_payoff,2),"hhi":hhi,
                "death_cause":agent.death_cause,"fossil":agent.fossil_file,
                "sim_text":(agent.sim_text if isinstance(agent.sim_text,dict) else {}),
                "i_surprise":round(float(getattr(agent,"i_surprise",0.0) or 0.0),3),
                "organ_on":bool(getattr(agent,"organ",None)),
                "organ_steps":agent.organ.steps if getattr(agent,"organ",None) else 0,
                "head_err":round(float(agent.organ.head_err_ema),3) if getattr(agent,"organ",None) else None,
                "selftest":getattr(agent,"_selftest_ok",None),
                "thought":agent.organ_thought,"ithought":agent.ithought,
                "jthought":agent.jthought,
                "gthought":{"alpha":round(agent.g.trust_neural,3),
                            "corr":round(agent.gorgan.corr(),3) if agent.gorgan else None,
                            "n":len(agent.gorgan.hist) if agent.gorgan else 0,
                            "ledger":round(agent.gorgan.ledger,1) if agent.gorgan else None,
                            "recent":[(p,a,s) for p,a,s in agent.gorgan.recent] if agent.gorgan else []},
                "curves":{k:[float(x) for x in v] for k,v in agent.curves.items()},
                "genes":{"trust_birth":agent.g.trust_birth,"trust_now":agent.g.trust_neural,
                         "lr_w":agent.g.neural_lr_w,"lr_i":agent.g.neural_lr_i,
                         "corr_gain":agent.g.corr_gain},
                "d_proxy":agent.d_proxy(),
                "sem":dict(SEM.stats(),nb=CHW),
                "wg":agent.wg.stats(),
                "coupling":round(agent.g.coupling_gain,3),
                "listen":agent._listen(),
                "bus":{k:round(float(v),3) for k,v in (agent.bus.prev.items() if getattr(agent,"bus",None) else [])},
                "self_profile":{"bored_ema":round(agent.self_profile["bored_ema"],3),
                                "explore_ratio":round(agent.self_profile["explore_ratio"],3)},
                "profile":{"curiosity":agent.g.CURIOSITY,"stress_k":agent.g.STRESS_K,
                           "lr":agent.g.LR,"boldness":round(1-mean_gate,2),
                           "entropy":round(entropy_np(agent.P),1)}}
            shex={host_of(u):HEX_SPIRAL[i%len(HEX_SPIRAL)] for i,u in enumerate(agent.order)}
            _cands=sorted(agent.frontier,key=lambda u:agent.frontier[u].get("found_at",0))
            chex={u:HEX_SPIRAL[(len(agent.order)+j)%len(HEX_SPIRAL)] for j,u in enumerate(_cands)}
            chex_host={host_of(u):_h for u,_h in chex.items()}
            _fh=agent.frame_act.get("host")
            if not _fh and agent.cur_target: _fh=host_of(agent.cur_target)
            if _fh and _fh in shex: _bq,_br=shex[_fh]
            elif _fh and _fh in chex_host: _bq,_br=chex_host[_fh]
            else: _bq,_br=0,0
            for _s in payload["sites"]:
                _h=shex.get(_s["host"],(0,0)); _s["hex"]={"q":_h[0],"r":_h[1]}
            for _c in payload["candidates"]:
                _h=chex.get(_c["url"],(0,0)); _c["hex"]={"q":_h[0],"r":_h[1]}
            payload["bug"]={"q":_bq,"r":_br,"host":(_fh or None),
                            "mood":(agent.mood_frame or agent.mood),
                            "sleeping":agent.sleeping,
                            "k":agent.frame_act.get("k","init"),
                            "p":agent.frame_act.get("p",{}) or {}}
        with INSTANCES_LOCK:
            others=[it["agent"] for it in INSTANCES.values() if it["agent"] is not agent]
        twins_out=[]
        for a2 in others:
            s2=_agent_snapshot(a2)
            h2=set(s2.pop("order_hosts"))
            jac=(len(me_hosts&h2)/len(me_hosts|h2)) if (me_hosts or h2) else 0.0
            s2["jaccard"]=round(jac,2); s2["paused"]=not RUN["on"]
            s2.update(_twin_fog_view(a2,shex,chex_host))
            twins_out.append(s2)
        twins_out.sort(key=lambda x:x["port"])
        payload["twins"]=twins_out
        return jsonify(payload)
    @app.route("/api/vocab")
    def api_vocab():
        with agent.lock:
            items=[]; seen=set()
            for w,c in agent.disp.items():
                wb=agent.word_birth.get(w) or {}
                e={k:round(float(v),3) for k,v in (wb.get("e") or {}).items()}
                items.append({"w":w,"c":round(float(c),2),"e":e,
                              "g":round(float(wb.get("$g",0.0)),3),
                              "red":bool(w in agent.gate and agent.gate[w]>0.5)})
                seen.add(w)
            for w,wb in agent.word_birth.items():
                if w in seen: continue
                e={k:round(float(v),3) for k,v in (wb.get("e") or {}).items()}
                items.append({"w":w,"c":0.0,"e":e,
                              "g":round(float(wb.get("$g",0.0)),3),
                              "red":bool(w in agent.gate and agent.gate[w]>0.5)})
            items.sort(key=lambda x:-x["c"])
            return jsonify({"n":len(items),"engine":SEM.engine,"items":items})
    @app.route("/api/sem")
    def api_sem():
        with agent.lock:
            with SEM.lock:
                b2w=defaultdict(list)
                for w,b in SEM.w2b.items(): b2w[int(b)].append(w)
                ch=list(SEM.clu_hist)
            tot_n=len(ch); share={}
            for c in ch:
                t=sum(c.values()) or 1
                for b,v in c.items(): share[b]=share.get(b,0.0)+v/t
            if tot_n:
                for b in share: share[b]/=tot_n
            pmass=np.zeros(SEM.nb)
            for ch_i in range(4):
                pmass+=np.asarray(agent.P)[ch_i*CHW:(ch_i+1)*CHW]
            buckets=[]
            for b,ws in b2w.items():
                buckets.append({"b":b,"n":len(ws),
                                "share":round(float(share.get(b,0.0)),4),
                                "p":round(float(pmass[b]),6),
                                "words":ws[:400]})
            buckets.sort(key=lambda x:(-x["n"],-x["p"]))
            return jsonify({"engine":SEM.engine,"model":SEM.model_name,"nb":SEM.nb,
                            "used":len(b2w),"words":len(SEM.w2b),
                            "thresh":round(SEM.THRESH,2),"mu_n":SEM.mu_n,
                            "empty":SEM.nb-len(b2w),"buckets":buckets})
    @app.route("/api/episodes")
    def api_episodes():
        with agent.lock:
            out=[{"key":e.get("key",""),
                  "words":list((e.get("words") or {}).items()),
                  "emo":[(k,round(float(v),2)) for k,v in sorted((e.get("emo") or {}).items(),key=lambda kv:-kv[1])[:4]],
                  "A":e.get("A"),"gain":e.get("gain"),"host":e.get("host"),
                  "day":e.get("day"),"weight":round(float(e.get("weight",1.0)),2)} for e in agent.episodes]
            out.sort(key=lambda x:-x["weight"])
            return jsonify({"n":len(out),"episodes":out})
    @app.route("/api/chain")
    def api_chain():
        q=(request.args.get("q") or "").strip()
        if not q: return jsonify(err="chain_need")
        try: depth=max(1,min(20,int(request.args.get("depth","5"))))
        except Exception: depth=5
        det=request.args.get("det")=="1"
        with agent.lock:
            stats=agent.wg.stats()
            chain,mode=agent.wg.chain(q,depth=depth,det=det)
            if not chain and q not in agent.wg.g:
                if SEM.engine=="hash":
                    return jsonify(err="chain_hash",q=q)
                return jsonify(err="chain_none",q=q)
            return jsonify({"q":q,"chain":chain,"mode":mode,"det":det,
                            "bridge_engine":SEM.engine,"stats":stats})
    @app.route("/api/inject",methods=["POST"])
    def api_inject():
        kind=request.args.get("kind","novelty")
        with agent.lock:
            if kind=="threat": agent.pending_storm=random.sample(THREATS,3)
            elif kind=="noise":
                add=np.zeros(DIM)
                for _ in range(300): add[random.randrange(DIM)]+=2.0
                agent.P+=add; norm_np(agent.P)
                if agent.organ is not None:
                    agent.organ.W2+=agent.organ.rng.normal(0,.5,agent.organ.W2.shape)
                    np.clip(agent.organ.W2,-agent.organ.CLIP,agent.organ.CLIP,out=agent.organ.W2)
                    agent.log_add("world","poison")
            elif kind=="novelty":
                if not H.REAL:
                    agent.world.burst[random.choice(list(SimWorld.TOPICS))]=80
                else:
                    n=0
                    for u in list(agent.frontier):
                        if n>=3: break
                        agent.frontier[u]["score"]*=1.5; n+=1
                    agent.log_add("world","heat",n=n)
        return jsonify(ok=True)
    @app.route("/api/toggle_run",methods=["POST"])
    def api_toggle_run():
        RUN["on"]=not RUN["on"]
        with agent.lock:
            agent.log_add("sys","pause" if not RUN["on"] else "resume")
        return jsonify(ok=True,on=RUN["on"])
    @app.route("/api/add_site",methods=["POST"])
    def api_add_site():
        url=(request.args.get("url") or "").strip()
        if not re.match(r"^https?://\S+$",url): return jsonify(ok=False,err="need_http")
        with agent.lock:
            if host_of(url) in {host_of(x) for x in agent.order}:
                return jsonify(ok=False,err="already_known")
            ECO.register(url,"human",avg_gain=0.3,port=0)
            agent._add_site(url,"human",False,meta={"found_at":time.time(),"parent":"","depth":1,"by_port":0,"seed":0.3})
        return jsonify(ok=True)
    @app.route("/api/snapshot",methods=["POST"])
    def api_snapshot():
        with agent.lock:
            snap={"P":[float(x) for x in agent.P],"gate":dict(agent.gate),"I_w":list(agent.I_w),
                  "energy":agent.energy,"tau":agent.tau,"last_A":agent.last_A,
                  "disp":copy.deepcopy(agent.disp),"appetite":dict(agent.em.appetite),
                  "episodes":copy.deepcopy(agent.episodes),
                  "word_birth":copy.deepcopy(agent.word_birth),
                  "wordgraph":agent.wg.dump(),
                  "dream_payoff":agent.dream_payoff,"name":agent.g.name,
                  "frame_at":agent.frame,"day":agent.day,"site":agent.current,
                  "neural":agent.g.neural_dict(),"saved_at":datetime.now().isoformat(),
                  "ver":"V4.5.0","sem_engine":SEM.engine,"sem_model":SEM.model_name,"chw":CHW,
                  "wage_regime":("credit_shift" if H.CREDIT_SHIFT else "legacy"),
                  "longevity":bool(getattr(H,"LONGEVITY",False)),
                  "blackouts":agent.stats.get("blackouts",0),
                  "mood_metab":bool(H.MOOD_METAB),"mood_frame":agent.mood_frame,
                  "resonance":bool(getattr(H,"RESONANCE",True)),
                  "bus_prev":{k:round(float(v),4) for k,v in agent.bus.prev.items()},
                  "bus_ema":{k:round(float(v),4) for k,v in agent.bus.ema.items()},
                  "emotions":{k:round(float(v),4) for k,v in agent.em.l.items()},
                  "mood":agent.mood,
                  "goal":(copy.deepcopy(agent.goal) if agent.goal else None),
                  "highA":[{k:v for k,v in e.items() if k!="S"} for e in list(agent.highA)[-60:]],
                  "cycle":{"day":agent.day,"awake_frame":agent.awake_frame,
                           "sleeping":agent.sleeping,"sleep_left":agent.sleep_left,
                           "day_harvest":round(agent.day_harvest,2),
                           "harvest_ema":round(agent.harvest_ema,3),
                           "night_quality":round(agent.night_quality,3)},
                  "emas":{"i_errA_ema":agent.i_errA_ema,"i_errT_ema":agent.i_errT_ema,
                          "j_err_ema":agent.j_err_ema,"j_lag_errA":agent.j_lag_errA,
                          "j_lag_errT":agent.j_lag_errT,"j_lag_surp":agent.j_lag_surp,
                          "meta_conf":agent.meta_conf,"gain_ema":agent.gain_ema,
                          "satiety":agent.satiety,"hungry":agent.hungry,
                          "blind_streak":agent.blind_streak,"total_blind":agent.total_blind,
                          "threat_events":agent.threat_events,"dreams":agent.dreams,
                          "expeditions":agent.expeditions},
                  "sites":{"order":list(agent.order),"known":copy.deepcopy(agent.known),
                           "profiles":{u:_prof_save(pr) for u,pr in agent.profiles.items()},
                           "frontier":copy.deepcopy(agent.frontier),
                           "blacklist":dict(agent.blacklist),
                           "last_food_hash":dict(agent.last_food_hash),
                           "site_idx":agent.site_idx,"cur_target":agent.cur_target},
                  "eco":ECO.snapshot()}
            od={}
            if agent.organ is not None and NEURAL_OK:
                od.update(W1=agent.organ.W1,b1=agent.organ.b1,W2=agent.organ.W2,
                          Wemb=agent.organ.Wemb,head=agent.organ.head,
                          last_lat=agent.organ.last_lat,last_bow=agent.organ.last_bow,
                          steps=np.array([agent.organ.steps]),
                          err_ema=np.array([agent.organ.err_ema]),
                          head_err_ema=np.array([agent.organ.head_err_ema]))
                snap["site_emb"]={h:[float(x) for x in v] for h,v in agent.organ.site_emb.items()}
            if agent.iorgan is not None and NEURAL_OK:
                od.update(i_W1=agent.iorgan.W1,i_b1=agent.iorgan.b1,i_W2=agent.iorgan.W2,i_b2=agent.iorgan.b2)
            if agent.jorgan is not None and NEURAL_OK:
                od.update(j_W1=agent.jorgan.W1,j_b1=agent.jorgan.b1,j_W2=agent.jorgan.W2,j_b2=agent.jorgan.b2)
            if agent.gorgan is not None and NEURAL_OK:
                od.update(g_W1=agent.gorgan.W1,g_b1=agent.gorgan.b1,g_W2=agent.gorgan.W2,g_b2=agent.gorgan.b2)
            if od:
                try:
                    ofile="organ450_%d_%s.npz"%(agent.port,datetime.now().strftime("%H%M%S"))
                    np.savez_compressed(ofile,**od)
                    snap["organ_file"]=ofile
                except Exception as e: agent.log_add("anomaly","thought",e=str(e)[:40])
            if SEM.embedder is not None:
                try:
                    with SEM.lock:
                        snap["sem_w2b"]={k:int(v) for k,v in SEM.w2b.items()}
                        snap["sem_thresh"]=SEM.THRESH
                    if SEM.proto is not None:
                        sfile="sem450_%d_%s.npz"%(agent.port,datetime.now().strftime("%H%M%S"))
                        np.savez_compressed(sfile,proto=SEM.proto,
                            count=np.array(SEM.count),
                            mu=(SEM.mu if SEM.mu is not None else np.zeros(1)),
                            mu_n=np.array(SEM.mu_n))
                        snap["sem_file"]=sfile
                except Exception as e: agent.log_add("anomaly","thought",e=str(e)[:40])
            agent.snapshot=snap
            fname="snap450_%d_%s_%s.json"%(agent.port,agent.g.name,datetime.now().strftime("%H%M%S"))
            try:
                with open(fname,"w",encoding="utf-8") as f: json.dump(snap,f,ensure_ascii=False)
                agent.snap_file=fname
                agent.log_add("sys","snap_ok",f=fname,fr=agent.frame,
                              ep=len(agent.episodes),wd=len(agent.disp),
                              en=round(agent.energy),og="full")
            except Exception as e: agent.log_add("anomaly","thought",e=str(e)[:40])
        return jsonify(ok=True)
    @app.route("/api/load_snap",methods=["POST"])
    def api_load_snap():
        f=request.args.get("file")
        if f:
            f=os.path.basename(f)
            if not re.match(r"^snap4\d+_[^/\\]+\.json$",f): return jsonify(ok=False,err="only_snap4")
            if not os.path.exists(f): return jsonify(ok=False,err="no_file")
        with agent.lock:
            ok=agent._load_snapshot("port",path=f) if f else agent._load_snapshot("port")
        return jsonify(ok=bool(ok))
    @app.route("/api/rollback",methods=["POST"])
    def api_rollback():
        with agent.lock:
            s=agent.snapshot
            if not s or agent.dead: return jsonify(ok=False)
            agent.P=np.array(s["P"]); norm_np(agent.P)
            agent.gate=dict(s["gate"]); agent.I_w=list(s["I_w"])
            agent.energy=min(120.0,s["energy"]); agent.tau=s["tau"]; agent.last_A=s["last_A"]
            agent.disp=copy.deepcopy(s["disp"]); agent.em.appetite.update(_mig_moods(s.get("appetite",{})))
            emo=s.get("emotions")
            if isinstance(emo,dict) and emo:
                for k,v in _mig_moods(emo).items():
                    if k in agent.em.l: agent.em.l[k]=max(0.0,min(1.0,float(v)))
            agent.mood=str(MOOD_MIG.get(s.get("mood",agent.mood),s.get("mood",agent.mood)))
            agent.mood_frame=str(MOOD_MIG.get(s.get("mood_frame",agent.mood),
                                              s.get("mood_frame",agent.mood)))
            agent.mood_frame_event="night"
            gl=s.get("goal")
            agent.goal=copy.deepcopy(gl) if isinstance(gl,dict) and gl.get("words") else None
            wgd=s.get("wordgraph")
            if isinstance(wgd,dict): agent.wg.load(wgd)
            if s.get("organ_file") and NEURAL_OK:
                try:
                    z=np.load(s["organ_file"]); _rb=[]
                    if agent.organ is not None:
                        g1=_npz_fit(agent.organ,z,"",["W1","b1","W2","Wemb","head","last_lat","last_bow"])
                        if g1: _rb.append("W%d/%d"%(len(g1),7))
                    if agent.iorgan is not None:
                        g2=_npz_fit(agent.iorgan,z,"i_",["W1","b1","W2","b2"])
                        if g2: _rb.append("I%d/%d"%(len(g2),4))
                    if agent.jorgan is not None:
                        g3=_npz_fit(agent.jorgan,z,"j_",["W1","b1","W2","b2"])
                        if g3: _rb.append("J%d/%d"%(len(g3),4))
                    if agent.gorgan is not None:
                        g4=_npz_fit(agent.gorgan,z,"g_",["W1","b1","W2","b2"])
                        if g4: _rb.append("G%d/%d"%(len(g4),4))
                    agent.log_add("sys","organ_partial",lst=" ".join(_rb) if _rb else "-")
                except Exception as e: agent.log_add("anomaly","thought",e=str(e)[:40])
            bps=s.get("bus_prev") or {}
            for k,v in bps.items():
                if k in agent.bus.prev: agent.bus.prev[k]=max(-1.,min(1.,float(v)))
            agent.stats["rollbacks"]+=1
            agent.log_add("sys","snap_ok",f="rollback",fr=s.get("frame_at",-1),
                          ep=len(agent.episodes),wd=len(agent.disp),en=round(agent.energy),og="rb")
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
            if not agent.dead: agent.die("dc_executed",killed=True)
        return jsonify(ok=True)
    @app.route("/api/shutdown",methods=["POST"])
    def api_shutdown():
        agent.log_add("sys","pause")
        SHUTDOWN.set()
        threading.Timer(2.5,lambda:os._exit(0)).start()
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
        if len(INSTANCES)>=MAX_INSTANCES: return None,"twin_limit"
    port=find_free_port()
    if port is None: return None,"no_port"
    g=Genome()
    with INSTANCES_LOCK:
        if g.name in INSTANCES: g.name=g.name+"·"+str(port)
    a=Agent(g,port=port,load_snap="port")
    threading.Thread(target=engine,args=(a,),daemon=True).start()
    srv=make_server("127.0.0.1",port,make_app(a,a.world),threaded=True)
    with SERVERS_LOCK: SERVERS.append(srv)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    with INSTANCES_LOCK: INSTANCES[g.name]={"agent":a,"port":port}
    a.log_add("sys","twin",cu=g.CURIOSITY,sk=g.STRESS_K,
              al=g.trust_neural,cg=g.coupling_gain)
    print("[twin] %s → http://127.0.0.1:%d"%(g.name,port))
    if AUTO_OPEN:
        try: webbrowser.open("http://127.0.0.1:%d"%port)
        except Exception: pass
    return port,g.name

def write_run_report():
    try:
        with INSTANCES_LOCK:
            agents=[it["agent"] for it in list(INSTANCES.values())]
        inst=[]
        for a in agents:
            with a.lock:
                inst.append({"name":a.g.name,"port":a.port,"genome":a.g.to_dict(),
                             "neural":a.g.neural_dict(),"coupling":a.g.coupling_gain,
                             "bus_listen":a._listen(),"stats":dict(a.stats),
                             "harvest_ema":round(a.harvest_ema,2),"wg":a.wg.stats(),
                             "organ_steps":a.organ.steps if a.organ else 0,
                             "head_err":round(a.organ.head_err_ema,4) if a.organ else None,
                             "ledger_score":round(a.gorgan.ledger,1) if a.gorgan else None,
                             "g_corr":round(a.gorgan.corr(),3) if a.gorgan else None,
                             "j_err":round(a.j_err_ema,3),"d_proxy":a.d_proxy(),
                             "selftest":getattr(a,"_selftest_ok",None),
                             "frames":a.frame,"alive":not a.dead,"death_cause":a.death_cause,
                             "portfolio":[host_of(u) for u in a.order]})
        rep={"run":RUN_TAG,"version":"V4.5.0","seed":GLOBAL_SEED,"chw":CHW,"dim":DIM,
             "sem_model":SEM.model_name,
             "longevity":bool(getattr(H,"LONGEVITY",False)),
             "max_sites":H.MAX_SITES,"frontier_cap":H.FRONTIER_CAP,
             "expedition":{"cost":H.EXPEDITION_COST,"every":H.EXPEDITION_EVERY,
                           "bonus":H.EXPEDITION_BONUS,"bonus_meals":H.EXPEDITION_BONUS_MEALS},
             "mood_metab":bool(H.MOOD_METAB),
             "wage_regime":("credit_shift" if H.CREDIT_SHIFT else "legacy"),
             "resonance":bool(getattr(H,"RESONANCE",True)),
             "sem":dict(SEM.stats(),nb=CHW),"neural":bool(H.NEURAL and NEURAL_OK),
             "discovered":ECO.report(),"ledger":list(ECO.discoveries),"instances":inst}
        fname="run450_%s.json"%RUN_TAG
        with open(fname,"w",encoding="utf-8") as f: json.dump(rep,f,ensure_ascii=False,indent=1)
        print("[archive] → %s"%fname)
    except Exception as e:
        print("[archive] failed: %s"%e)

if __name__=="__main__":
    _argv=sys.argv[1:]
    _ctrl=("--control" in _argv)
    if "--longevity" in _argv: H.LONGEVITY=True
    if "--seed" in _argv:
        try: GLOBAL_SEED=int(_argv[_argv.index("--seed")+1])
        except Exception: GLOBAL_SEED=None
        if GLOBAL_SEED is not None:
            random.seed(GLOBAL_SEED); np.random.seed(GLOBAL_SEED%(2**32))
            print("[seed] global seed=%d (web timing still adds noise)"%GLOBAL_SEED)
    if _ctrl:
        BASE_SITES.clear(); ECO.data.clear(); PORT_BASE=5040
    g=Genome()
    agent=Agent(g,port=PORT_BASE,load_snap="port",allow_sem=True)
    with INSTANCES_LOCK: INSTANCES[g.name]={"agent":agent,"port":PORT_BASE}
    threading.Thread(target=engine,args=(agent,),daemon=True).start()
    try:
        srv=make_server("127.0.0.1",PORT_BASE,make_app(agent,agent.world),threaded=True)
    except OSError as e:
        print("[fatal] port %d busy (%s). Try EDF_PORT=8000 (macOS 5000 = AirPlay)."%(PORT_BASE,e)); sys.exit(1)
    with SERVERS_LOCK: SERVERS.append(srv)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    _zh=sum(1 for u in BASE_SITES if u in BASE_POOL_ZH)
    print("Digital Placozoa V4.5.0 — http://127.0.0.1:%d%s"%(PORT_BASE," (zero-fishery control)" if _ctrl else ""))
    print("[sem] %s | buckets %d | jieba %s | wage %s"%(SEM.engine,CHW,
          "on" if JIEBA_OK else "off","credit-shift" if H.CREDIT_SHIFT else "legacy"))
    print("[map] zoom-to-fit | roam = ring 10 (60 cells) | base sites: %d zh + %d en"%(_zh,len(BASE_SITES)-_zh))
    print("[organs] %s | selftest %s"%("/".join(n for n,o in (("W",agent.organ),("I",agent.iorgan),("J",agent.jorgan),("G",agent.gorgan)) if o) or "none",
          "pass" if getattr(agent,"_selftest_ok",None) else "warn/n-a"))
    if H.LONGEVITY: print("[longevity] dial B active (no starvation death; forced sleep at 0)")
    if SEM.embedder is None or not JIEBA_OK:
        print("[degraded] lex=%s sem=%s — pip install jieba sentence-transformers torch"
              %("jieba" if JIEBA_OK else "bigram","hash" if SEM.embedder is None else SEM.engine))
    if AUTO_OPEN:
        try: webbrowser.open("http://127.0.0.1:%d"%PORT_BASE)
        except Exception: pass
    SHUTDOWN.wait()
    time.sleep(0.8)
    write_run_report()
    with SERVERS_LOCK:
        for s in list(SERVERS):
            try: s.shutdown()
            except Exception: pass
    os._exit(0)
