import telebot
import requests
import hashlib
import base64
import json
import logging
import threading
import socketio
import random
import time
import string
import math
import os
import re
import io
import statistics
import asyncio
import sys
from flask import Flask
from collections import deque, defaultdict
from datetime import datetime, timedelta
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession

# ========================= FLASK KEEPALIVE =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "LC79 BOT ONLINE (Check Render Logs for Bot Status)"

@app.route("/health")
def health():
    return {"status": "online", "bot": "LC79"}

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ========================= LOGGING =========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('engineio').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('telethon').setLevel(logging.WARNING)

# ========================= CONFIG & BOT INIT =========================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "@admin")
TELETHON_API_ID = int(os.environ.get("TELETHON_API_ID", "0"))
TELETHON_API_HASH = os.environ.get("TELETHON_API_HASH", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

bot = None
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    logger.error("❌ LỖI: BOT_TOKEN chưa có hoặc sai định dạng. Vào Render -> Environment để thêm BOT_TOKEN (lấy từ @BotFather)!")
else:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        bot.set_my_commands([
            telebot.types.BotCommand("/start", "Menu chính"),
            telebot.types.BotCommand("/login", "Login game"),
            telebot.types.BotCommand("/stop", "Ngắt kết nối"),
            telebot.types.BotCommand("/nhapkey", "Nhập key VIP"),
            telebot.types.BotCommand("/huongdan", "Hướng dẫn"),
        ])
        logger.info("✅ Kết nối Telegram Bot OK!")
    except Exception as e:
        logger.error(f"❌ LỖI TOKEN TELEGRAM: {e}. Hãy kiểm tra lại BOT_TOKEN trên Render!")
        bot = None

if not bot:
    logger.error("🚨 BOT CHƯA SẴN SÀNG. Vui lòng sửa BOT_TOKEN trên Render Environment Variables và Restart.")
    # Tạo bot giả để code không lỗi, tránh bị sập Render
    class DummyBot:
        def __init__(self): pass
        def __getattr__(self, name):
            def dummy(*args, **kwargs):
                def dummy_wrapper(*args, **kwargs): pass
                return dummy_wrapper
            return dummy
    bot = DummyBot()

HISTORY_API_URL = "https://wtxmd52.tele68.com/v1/txmd5/sessions"
API_URL_FALLBACK = "https://living-telecommunications-start-consoles.trycloudflare.com/api/txmd5"
MAX_HISTORY_STORE = 500
MIN_CONFIDENCE_AUTO_BET = 55
AUTO_BET_RUN_UNTIL_STOP = True
MAX_MARTINGALE_LEVEL = 6
MARTINGALE_MULTIPLIER = 2

active_sockets = {}
user_states = {}
valid_keys = {}
authorized_users = {}

# ========================= PERSISTENCE =========================
REDEEM_STATE_FILE = os.path.join(DATA_DIR, "redeem_state.json")
REDEEM_LOGS_FILE = os.path.join(DATA_DIR, "redeem_logs.json")
GAME_ACCOUNTS_FILE = os.path.join(DATA_DIR, "game_accounts.json")
TELE_SESSION_FILE = os.path.join(DATA_DIR, "tele_session.txt")

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Load {path}: {e}")
    return default

def save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Save {path}: {e}")

redeem_state = load_json(REDEEM_STATE_FILE, {"accounts": [], "channel_id": None, "channel_name": "", "enabled": False})
redeem_logs = load_json(REDEEM_LOGS_FILE, [])
game_accounts = load_json(GAME_ACCOUNTS_FILE, {})

# ========================= HELPERS =========================
def md5_hash(t):
    return hashlib.md5(t.encode()).hexdigest()

def normalize_pw(pw, force_mode=None):
    pw = pw.strip()
    if force_mode == "md5":
        return pw.lower()
    if force_mode == "plain":
        return md5_hash(pw)
    if len(pw) == 32 and all(c in '0123456789abcdef' for c in pw.lower()):
        return pw.lower()
    return md5_hash(pw)

# ========================= BRAIN =========================
class RealBrainUltra:
    def __init__(self):
        self.results = deque(maxlen=2000)
        self.sums = deque(maxlen=2000)
        self.perf = deque(maxlen=500)
        self.pattern_memory = defaultdict(lambda: defaultdict(int))
        self.pattern_total = defaultdict(int)
        self.streak_memory = defaultdict(lambda: {"THEO": 0, "BE": 0})
        self.markov_chain = defaultdict(lambda: defaultdict(int))
        self.transition_matrix = defaultdict(lambda: defaultdict(int))
        self.ngram_weights = {2: 0.08, 3: 0.15, 4: 0.25, 5: 0.32, 6: 0.20}
        self.adaptive_threshold = 0.58
        self.confidence_history = deque(maxlen=200)
        self.super_pattern = defaultdict(lambda: defaultdict(int))
        self.volatility = deque(maxlen=100)
        self.trend_strength = 0.0

    def push(self, data):
        for h in data:
            if isinstance(h, dict) and 'result' in h:
                res = h['result']
            elif isinstance(h, str):
                res = h
            else:
                continue
            if 'TÀI' in str(res).upper() or 'TAI' in str(res).upper() or res == 'T':
                self.results.append('TAI')
            elif 'XỈU' in str(res).upper() or 'XIU' in str(res).upper() or res == 'X':
                self.results.append('XIU')
            else:
                self.results.append(res)
            if isinstance(h, dict) and 'total' in h:
                self.sums.append(h['total'])
            else:
                self.sums.append(10)

    def learn(self):
        all_res = list(self.results)
        n = len(all_res)
        if n < 15:
            return
        self.pattern_memory.clear(); self.pattern_total.clear()
        self.markov_chain.clear(); self.transition_matrix.clear(); self.super_pattern.clear()
        for i in range(n):
            for plen in [2, 3, 4, 5, 6]:
                if i >= plen:
                    pat = tuple(all_res[i-plen:i])
                    self.pattern_memory[pat][all_res[i]] += 1
                    self.pattern_total[pat] += 1
            if i >= 1:
                self.markov_chain[all_res[i-1]][all_res[i]] += 1
            if i >= 2:
                self.transition_matrix[(all_res[i-2], all_res[i-1])][all_res[i]] += 1
            if i >= 4:
                self.super_pattern[tuple(all_res[i-4:i])][all_res[i]] += 1
        self.streak_memory.clear()
        i = 0
        while i < n:
            last = all_res[i]; s = 1; j = i + 1
            while j < n and all_res[j] == last:
                s += 1; j += 1
            if j < n:
                self.streak_memory[s]["THEO" if all_res[j] == last else "BE"] += 1
            i = j
        if n >= 20:
            recent = all_res[-20:]
            self.trend_strength = (recent.count('TAI') - recent.count('XIU')) / 20
        if n >= 10:
            seg = all_res[-50:]
            changes = sum(1 for i in range(1, len(seg)) if seg[i] != seg[i-1])
            self.volatility.append(changes / min(50, n))

    def _q(self, r):
        if len(r) < 20: return None
        t = r[-20:].count('TAI'); x = r[-20:].count('XIU')
        ratio = t / (t + x) if (t + x) > 0 else 0.5
        if ratio > 0.75: return 'XIU', 0.87, "Quantum dao cuc TAI"
        if ratio < 0.25: return 'TAI', 0.87, "Quantum dao cuc XIU"
        return None

    def _fib(self, r):
        if len(r) < 21: return None
        fibs = [1, 2, 3, 5, 8, 13, 21]; weights = []
        for f in fibs:
            if len(r) >= f:
                weights.append((r[-f:].count('TAI') - r[-f:].count('XIU')) / f)
        if not weights: return None
        score = sum(w * (i+1) for i, w in enumerate(weights)) / sum(range(1, len(weights)+1))
        if score > 0.3: return 'TAI', min(0.90, 0.62 + abs(score) * 0.30), "Fibonacci TAI"
        if score < -0.3: return 'XIU', min(0.90, 0.62 + abs(score) * 0.30), "Fibonacci XIU"
        return None

    def _mc(self, r):
        if len(r) < 15: return None
        t = r.count('TAI'); x = r.count('XIU')
        if t + x == 0: return None
        sims = [random.choices(['TAI', 'XIU'], weights=[t/(t+x), x/(t+x)], k=15).count('TAI') for _ in range(500)]
        avg = statistics.mean(sims); std = statistics.stdev(sims) if len(sims) > 1 else 2
        if std > 0:
            z = (r[-7:].count('TAI') - avg) / std
            if z > 1.5: return 'XIU', min(0.89, 0.62 + abs(z) * 0.08), f"MonteCarlo Z={z:.2f}"
            if z < -1.5: return 'TAI', min(0.89, 0.62 + abs(z) * 0.08), f"MonteCarlo Z={z:.2f}"
        return None

    def _ent(self, r):
        if len(r) < 30: return None
        p_t = r[-30:].count('TAI') / 30; p_x = r[-30:].count('XIU') / 30
        entropy = -sum(p * math.log2(p) for p in [p_t, p_x] if p > 0)
        if entropy < 0.75:
            return ('XIU' if p_t > p_x else 'TAI'), 0.82, "Entropy thap"
        return None

    def _sp(self, r):
        if len(r) < 5: return None
        sp = tuple(r[-4:])
        if sp in self.super_pattern:
            data = self.super_pattern[sp]; total = sum(data.values())
            if total >= 3:
                for side in ('TAI', 'XIU'):
                    rate = data.get(side, 0) / total
                    if rate >= 0.70: return side, min(0.91, 0.68 + rate * 0.20), "SuperPattern"
        return None

    def _vol(self, r):
        if len(self.volatility) < 10: return None
        avg = statistics.mean(self.volatility)
        recent = r[-10:]
        cur = sum(1 for i in range(1, len(recent)) if recent[i] != recent[i-1]) / 10
        if cur > avg * 1.5:
            return ('XIU' if r[-1] == 'TAI' else 'TAI'), 0.78, "Volatility cao"
        return None

    def _cyc(self, r):
        if len(r) < 30: return None
        seq = r[-30:]
        for cl in [3, 4, 5, 6, 7]:
            if len(seq) >= cl * 3:
                matches = sum(1 for i in range(len(seq) - cl * 2, len(seq) - cl) if seq[i] == seq[i + cl])
                if matches >= cl * 2 - 1:
                    idx = len(seq) - cl
                    if 0 <= idx < len(seq): return seq[idx], 0.83, f"Chu ky {cl}"
        return None

    def _trend(self, r):
        if len(r) < 25 or abs(self.trend_strength) <= 0.4: return None
        return ('TAI' if self.trend_strength > 0 else 'XIU'), 0.76, f"Xu huong {self.trend_strength:.2f}"

    def predict(self, history):
        if len(history) < 10: return None, 0, "Can them du lieu..."
        self.push(history); self.learn()
        r = list(self.results); s_list = list(self.sums); n = len(r)
        if n < 5: return None, 0, "..."
        last = r[-1]; last_sum = s_list[-1] if s_list else 10
        s = 1
        for x in reversed(r[:-1]):
            if x == last: s += 1
            else: break
        C = []
        for plen in [2, 3, 4, 5, 6]:
            if n >= plen:
                pat = tuple(r[-plen:])
                if pat in self.pattern_memory and self.pattern_total[pat] >= 3:
                    data = self.pattern_memory[pat]; total = self.pattern_total[pat]
                    for side in ('TAI', 'XIU'):
                        cnt = data.get(side, 0); rate = cnt / total
                        if rate >= self.adaptive_threshold and cnt >= 2:
                            C.append((side, min(0.94, 0.55 + rate * 0.42) * self.ngram_weights.get(plen, 0.15), f"Mau{plen}"))
        if n >= 2 and (r[-2], r[-1]) in self.transition_matrix:
            data = self.transition_matrix[(r[-2], r[-1])]; total = sum(data.values())
            if total >= 3:
                for side in ('TAI', 'XIU'):
                    rate = data.get(side, 0) / total
                    if rate >= 0.68: C.append((side, 0.78 + rate * 0.15, "Markov2"))
        if last in self.markov_chain:
            data = self.markov_chain[last]; total = sum(data.values())
            if total >= 3:
                for side in ('TAI', 'XIU'):
                    rate = data.get(side, 0) / total
                    if rate >= 0.65: C.append((side, 0.74 + rate * 0.15, "Markov1"))
        if s in self.streak_memory:
            data = self.streak_memory[s]; theo, be = data["THEO"], data["BE"]; total = theo + be
            if total >= 3:
                if be / total >= 0.62: C.append((('XIU' if last == 'TAI' else 'TAI'), min(0.89, 0.55 + (be/total)*0.40), f"Be{s}"))
                elif theo / total >= 0.62: C.append((last, min(0.86, 0.55 + (theo/total)*0.40), f"Theo{s}"))
        if n >= 20:
            try:
                avg = statistics.mean(s_list[-30:]) if len(s_list) >= 30 else statistics.mean(s_list)
                std = statistics.stdev(s_list[-30:]) if len(s_list) >= 5 else 3.0
                if std > 0:
                    z = (last_sum - avg) / std
                    if z >= 2.5: C.append(('XIU', 0.95, f"Z={z:.1f} cao"))
                    elif z <= -2.5: C.append(('TAI', 0.95, f"Z={z:.1f} thap"))
                    elif z >= 1.8: C.append(('XIU', 0.82, f"Z={z:.1f}"))
                    elif z <= -1.8: C.append(('TAI', 0.82, f"Z={z:.1f}"))
            except: pass
        for fn in [self._sp, self._q, self._fib, self._mc, self._ent, self._vol, self._cyc, self._trend]:
            res = fn(r)
            if res: C.append(res)
        if n >= 20:
            t30 = r[-30:].count('TAI'); x30 = r[-30:].count('XIU'); tot = t30 + x30
            if tot >= 20:
                if t30/tot >= 0.65: C.append(('TAI', 0.77, "TAI dai han"))
                elif t30/tot <= 0.35: C.append(('XIU', 0.77, "XIU dai han"))
        if C:
            C.sort(key=lambda x: x[1], reverse=True)
            p, c, rs = C[0]
            self.confidence_history.append(c)
            return p, min(c, 0.97), rs
        r50 = r[-50:] if n >= 50 else r
        t50 = r50.count('TAI'); x50 = r50.count('XIU'); tot50 = t50 + x50
        if tot50 > 0:
            return ("TAI" if t50 > x50 else "XIU", 0.55, "Theo so dong")
        return ("TAI" if last_sum >= 11 else "XIU", 0.52, f"Theo tong {last_sum}")

    def update(self, pred, actual):
        if pred is None: return
        self.perf.append(1 if pred == actual else 0)
        if len(self.perf) >= 20:
            acc = sum(self.perf) / len(self.perf)
            if acc < 0.45: self.adaptive_threshold = min(0.72, self.adaptive_threshold + 0.02)
            elif acc > 0.60: self.adaptive_threshold = max(0.50, self.adaptive_threshold - 0.01)

_brain = RealBrainUltra()
def feed_history(h): _brain.push(h)
def pro_predict(h, **kw): return _brain.predict(h)
def update_result(p, a, **kw): _brain.update(p, a)

# ========================= REDEEM =========================
def append_redeem_log(code, account, bal_before, bal_after, amount, status):
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code,
        "account": account,
        "balance_before": int(bal_before or 0),
        "balance_after": int(bal_after or 0),
        "amount": int(amount or 0),
        "status": status
    }
    redeem_logs.append(entry)
    save_json(REDEEM_LOGS_FILE, redeem_logs)
    logger.info(f"[REDEEM] {code} | {account} | {status} | amount={amount}")

class RedeemAccount:
    CONNECT_TIMEOUT = 5; READ_TIMEOUT = 45; MAX_RETRY = 3
    API_GET_ACCESS = "https://apifo88daigia.tele68.com/api"
    LOGIN_URL = "https://wlb.tele68.com/v1/lobby/auth/login"
    BASE_URL = "https://gameapi.tele68.com"
    PARAMS = "/v1/giftcode/redemption?cp=R&cl=R&pf=web&at="

    def __init__(self, un, pw, nick):
        self.un = un; self.pw = pw; self.nick = nick
        self.used_codes = set(); self.lock = threading.Lock()
        self.jwt = None; self.jwt_exp = 0; self.balance = 0

    def _headers(self, auth=None):
        h = {"content-type": "application/json", "user-agent": "Mozilla/5.0",
             "origin": "https://lc79.to", "referer": "https://lc79.to/",
             "accept": "application/json, text/plain, */*", "connection": "close"}
        if auth: h["authorization"] = f"Bearer {auth}"
        return h

    @staticmethod
    def parse_jwt_exp(token):
        try:
            payload = token.split(".")[1]; payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
        except: return 0

    def is_token_valid(self):
        return self.jwt is not None and time.time() < (self.jwt_exp - 60)

    def get_access_token(self):
        try:
            with requests.Session() as s:
                res = s.get(self.API_GET_ACCESS, params={
                    "c": "3", "un": self.un, "pw": self.pw, "cp": "R", "cl": "R", "pf": "web", "at": ""
                }, headers=self._headers(), timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT))
            data = res.json()
            return data.get("accessToken") if data.get("success") else None
        except Exception as e:
            logger.error(f"[{self.nick}] get_access_token: {e}"); return None

    def refresh_token(self):
        at = self.get_access_token()
        if not at: return False
        try:
            with requests.Session() as s:
                res = s.post(self.LOGIN_URL,
                             headers={"content-type": "application/json", "origin": "https://lc79b.bet",
                                      "referer": "https://lc79b.bet/", "user-agent": "Mozilla/5.0", "connection": "close"},
                             json={"nickName": self.nick, "accessToken": at},
                             timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT))
            data = res.json()
            new_tok = data.get("token")
            if new_tok:
                self.jwt = new_tok
                self.jwt_exp = self.parse_jwt_exp(new_tok)
                self.balance = int(data.get("remoteLoginResp", {}).get("money", self.balance) or self.balance)
                return True
            return False
        except Exception as e:
            logger.error(f"[{self.nick}] refresh: {e}"); return False

    def redeem_code(self, code, _attempt=0):
        with self.lock:
            if code in self.used_codes: return
            self.used_codes.add(code)
        if not self.is_token_valid():
            if not self.refresh_token():
                append_redeem_log(code, self.nick, self.balance, None, 0, "LOGIN FAIL")
                return
        bal_before = self.balance
        logger.info(f"[{self.nick}] Redeem: {code}")
        try:
            with requests.Session() as s:
                res = s.post(self.BASE_URL + self.PARAMS,
                             headers=self._headers(self.jwt),
                             json={"code": code.strip(), "captcha": ""},
                             timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT))
            try: data = res.json()
            except: data = {}
            if res.status_code == 401:
                if self.refresh_token():
                    with self.lock: self.used_codes.discard(code)
                    return self.redeem_code(code, _attempt)
                append_redeem_log(code, self.nick, bal_before, None, 0, "TOKEN EXPIRED")
                return
            if res.status_code in (200, 201):
                amount = (data.get("money") or data.get("amount") or data.get("reward")
                          or (data.get("data") or {}).get("money") or 0)
                try: amount = int(amount)
                except: amount = 0
                self.refresh_token()
                bal_after = self.balance
                append_redeem_log(code, self.nick, bal_before, bal_after, amount, "SUCCESS")
                logger.info(f"[{self.nick}] SUCCESS +{amount:,} | Bal {bal_before:,} -> {bal_after:,}")
            else:
                msg = data.get("message", f"HTTP {res.status_code}") if isinstance(data, dict) else f"HTTP {res.status_code}"
                append_redeem_log(code, self.nick, bal_before, None, 0, f"FAIL: {msg}")
                logger.warning(f"[{self.nick}] FAIL: {msg}")
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            if _attempt < self.MAX_RETRY:
                time.sleep(1 + _attempt + random.uniform(0, 0.5))
                return self.redeem_code(code, _attempt=_attempt+1)
            append_redeem_log(code, self.nick, bal_before, None, 0, "TIMEOUT")
        except Exception as e:
            append_redeem_log(code, self.nick, bal_before, None, 0, f"ERROR: {e}")

def build_redeem_accounts():
    return [RedeemAccount(a["un"], a["pw"], a["nick"]) for a in redeem_state["accounts"]]

REDEEM_ACCOUNTS = build_redeem_accounts()

def redeem_all_threaded(code):
    if not REDEEM_ACCOUNTS:
        logger.warning("Chua co acc redeem"); return
    def worker():
        logger.info(f"CODE: {code} -> {len(REDEEM_ACCOUNTS)} acc")
        for acc in REDEEM_ACCOUNTS:
            if not acc.is_token_valid(): acc.refresh_token()
        threads = []
        for i, acc in enumerate(REDEEM_ACCOUNTS):
            def w(a=acc, idx=i):
                if idx > 0: time.sleep(idx * 1.0 + random.uniform(0, 0.3))
                a.redeem_code(code)
            t = threading.Thread(target=w, daemon=True); t.start(); threads.append(t)
        for t in threads: t.join(timeout=60)
        recent = [l for l in redeem_logs if l["code"] == code]
        ok = sum(1 for l in recent if l["status"] == "SUCCESS")
        total = sum(l["amount"] for l in recent if l["status"] == "SUCCESS")
        try:
            bot.send_message(ADMIN_ID,
                f"<b>REDEEM XONG</b>\nCode: <code>{code}</code>\n"
                f"OK: {ok}/{len(REDEEM_ACCOUNTS)}\nTong nhan: <b>{total:,}</b>\n\n"
                f"<code>/getredeemlog</code> de xuat file",
                parse_mode="HTML")
        except: pass
    threading.Thread(target=worker, daemon=True).start()

# ========================= USER STATE =========================
def init_user_state(chat_id):
    if chat_id not in user_states:
        user_states[chat_id] = {
            "raw_hist": [], "history": [], "points_history": [],
            "auto_bet_enabled": False, "bet_amount": 10000, "base_bet": 10000,
            "martingale_enabled": False, "martingale_level": 0,
            "current_prediction": None, "has_bet_this_session": False,
            "session_id": None, "balance": 0,
            "win_streak": 0, "lose_streak": 0, "total_win": 0, "total_lose": 0,
            "last_prediction_conf": 0, "auto_bet_mode": "NORMAL",
            "daily_bet_limit": 0, "stop_loss": 0, "stop_win": 0, "profit": 0,
            "notify_mode": "FULL", "awaiting": None
        }

def parse_lines(data):
    if not data or not isinstance(data.get("list"), list): return []
    arr = sorted(data["list"], key=lambda x: x["id"]); out = []
    for it in arr:
        p = it.get("point", sum(it.get("dices", [0, 0, 0])))
        r = it.get("resultTruyenThong")
        if r in ("TAI", "XIU"):
            out.append({"session": it["id"], "dice": it["dices"], "total": p, "result": r})
    return out

def fetch_history_from_api(limit=60):
    for url in (HISTORY_API_URL, API_URL_FALLBACK):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Origin": "https://lc79b.bet"}, timeout=10)
            if r.status_code != 200: continue
            arr = parse_lines(r.json())
            if arr: return arr[-limit:]
        except: continue
    return []

def check_auth(chat_id):
    if chat_id == ADMIN_ID: return True
    if chat_id in authorized_users:
        if time.time() <= authorized_users[chat_id]: return True
        else: del authorized_users[chat_id]
    return False

def require_auth(fn):
    def wr(m, *a, **kw):
        if not check_auth(m.chat.id):
            bot.reply_to(m, f"CHUA KICH HOAT\n/nhapkey KEY\n{ADMIN_USERNAME}", parse_mode="HTML"); return
        return fn(m, *a, **kw)
    return wr

def require_admin(fn):
    def wr(m, *a, **kw):
        if m.chat.id != ADMIN_ID:
            bot.reply_to(m, "CHI ADMIN"); return
        return fn(m, *a, **kw)
    return wr

def format_expire_time(ts):
    r = ts - time.time()
    if r <= 0: return "HET HAN"
    d = int(r // 86400); h = int((r % 86400) // 3600); m = int((r % 3600) // 60)
    return f"CON {d}N {h}G {m}P" if d > 0 else f"CON {h}G {m}P"

def tinh_do_tin_cay(cid=None):
    if cid and cid in user_states and user_states[cid]["raw_hist"]:
        r = pro_predict(user_states[cid]["raw_hist"])
        if r and r[1]: return int(r[1] * 100)
    return 50

def tinh_tien_cuoc(cid):
    st = user_states[cid]
    if not st["martingale_enabled"]: return st["bet_amount"]
    lv = st["martingale_level"]
    if lv <= 0: return st["base_bet"]
    return min(st["base_bet"] * (MARTINGALE_MULTIPLIER ** lv),
               st["base_bet"] * (MARTINGALE_MULTIPLIER ** MAX_MARTINGALE_LEVEL))

def ai_tu_hoc(cid, pred, actual):
    st = user_states[cid]; update_result(pred, actual)
    if pred == actual:
        st["win_streak"] += 1; st["lose_streak"] = 0
        st["total_win"] += 1; st["martingale_level"] = 0
        st["profit"] += tinh_tien_cuoc(cid)
    else:
        st["lose_streak"] += 1; st["win_streak"] = 0
        st["total_lose"] += 1; st["profit"] -= tinh_tien_cuoc(cid)
        if st["martingale_enabled"] and st["martingale_level"] < MAX_MARTINGALE_LEVEL:
            st["martingale_level"] += 1

# ========================= LOGIN + WS =========================
def login_and_get_token(u, p):
    try:
        pw_hashed = normalize_pw(p)
        r = requests.get(f"https://apifo88daigia.tele68.com/api?c=3&un={u}&pw={pw_hashed}&cp=R&cl=R&pf=web&at=", timeout=12).json()
        if not r.get("success"): return {"_error": "Sai TK/MK"}
        sk = r["sessionKey"] + "=" * ((4 - len(r["sessionKey"]) % 4) % 4)
        nn = json.loads(base64.b64decode(sk)).get("nickname")
        r2 = requests.post("https://wlb.tele68.com/v1/lobby/auth/login?cp=R&cl=R&pf=web&at=",
                           json={"nickName": nn, "accessToken": r["accessToken"]}, timeout=12).json()
        if not r2.get("token"): return {"_error": "Khong nhan token"}
        return {"token": r2["token"], "nickname": nn, "money": int(r2.get("remoteLoginResp", {}).get("money", 0) or 0)}
    except Exception as e:
        return {"_error": str(e)}

def save_game_account(key, username, nickname, token, money, source="user"):
    game_accounts[str(key)] = {
        "username": username, "nickname": nickname, "token": token,
        "money": int(money or 0),
        "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source
    }
    save_json(GAME_ACCOUNTS_FILE, game_accounts)

def start_websocket(cid, token):
    if cid in active_sockets:
        try: active_sockets[cid].disconnect()
        except: pass
    sio = socketio.Client(reconnection=True, reconnection_attempts=99999, reconnection_delay=3, logger=False, engineio_logger=False)
    active_sockets[cid] = sio
    init_user_state(cid)

    @sio.event(namespace='/txmd5')
    def connect():
        arr = fetch_history_from_api(60); st = user_states[cid]
        if arr:
            st["raw_hist"] = arr; st["history"] = [x["result"] for x in arr]; st["points_history"] = [x["total"] for x in arr]
            feed_history(arr)
            bot.send_message(cid, f"KET NOI OK\n{len(arr)} phien\nAI SAN SANG", parse_mode="HTML")
        else:
            bot.send_message(cid, "Ket noi OK, dang tai du lieu", parse_mode="HTML")

    @sio.on('new-session', namespace='/txmd5')
    def new_sess(d):
        st = user_states[cid]; st["session_id"] = d.get("id"); st["has_bet_this_session"] = False
        result = pro_predict(st["raw_hist"]) if st["raw_hist"] else None
        if result and result[0]:
            st["current_prediction"] = result[0]; dt = int(result[1] * 100); st["last_prediction_conf"] = dt
            tien = tinh_tien_cuoc(cid)
            x2 = f"C{st['martingale_level']}" if st["martingale_enabled"] else "OFF"
            bot.send_message(cid, f"PHIEN #{st['session_id']}\n<b>{result[0]}</b> | {dt}%\n{result[2]}\nCuoc: {tien:,}\nX2: {x2}", parse_mode="HTML")
        elif result is None:
            st["current_prediction"] = random.choice(["TAI", "XIU"])
            bot.send_message(cid, f"PHIEN #{st['session_id']}\nRandom: {st['current_prediction']}", parse_mode="HTML")

    @sio.on('tick-update', namespace='/txmd5')
    def tk(d):
        st = user_states[cid]
        if d.get("state") == "BETTING" and st["auto_bet_enabled"] and st["current_prediction"] and AUTO_BET_RUN_UNTIL_STOP:
            if not st["has_bet_this_session"] and tinh_do_tin_cay(cid) >= MIN_CONFIDENCE_AUTO_BET:
                tien = tinh_tien_cuoc(cid)
                sio.emit("bet", {"type": st["current_prediction"], "amount": tien}, namespace="/txmd5")
                st["has_bet_this_session"] = True
                bot.send_message(cid, f"DAT {st['current_prediction']} | {tien:,}", parse_mode="HTML")

    @sio.on('bet-result', namespace='/txmd5')
    def br(d):
        if "postBalance" in d: user_states[cid]["balance"] = d["postBalance"]

    @sio.on('session-result', namespace='/txmd5')
    def sr(d):
        res = d.get("resultTruyenThong"); st = user_states[cid]
        if res in ("TAI", "XIU"):
            rec = {"session": d.get("id"), "dice": d["dices"], "total": sum(d["dices"]), "result": res}
            st["raw_hist"].append(rec); st["history"].append(res); st["points_history"].append(rec["total"])
            if len(st["raw_hist"]) > MAX_HISTORY_STORE:
                st["raw_hist"].pop(0); st["history"].pop(0); st["points_history"].pop(0)
            feed_history([rec]); ai_tu_hoc(cid, st["current_prediction"], res)
            tt = "THANG" if st["current_prediction"] == res else "THUA"
            bot.send_message(cid, f"KQ: {res} -> {tt}\nThua lien: {st['lose_streak']} | Cap X2: {st['martingale_level']}", parse_mode="HTML")

    try:
        sio.connect("https://wtxmd52.tele68.com", socketio_path="txmd5/", namespaces=["/txmd5"],
                    transports=["websocket"], auth={"token": token},
                    headers={"User-Agent": "Mozilla/5.0", "Origin": "https://lc79b.bet"})
        sio.wait()
    except Exception as e:
        bot.send_message(cid, f"Loi WS: {e}")

# ========================= MENUS =========================
def main_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("DIEU KHIEN", callback_data="dashboard"),
        telebot.types.InlineKeyboardButton("HUONG DAN", callback_data="hd"),
        telebot.types.InlineKeyboardButton("NHAP KEY", callback_data="nhapkey"),
        telebot.types.InlineKeyboardButton("INFO", callback_data="tt")
    )
    return kb

def dashboard_menu(cid=None):
    kb = telebot.types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        telebot.types.InlineKeyboardButton("LOGIN", callback_data="login"),
        telebot.types.InlineKeyboardButton("AUTO", callback_data="auto"),
        telebot.types.InlineKeyboardButton("X2", callback_data="x2"),
        telebot.types.InlineKeyboardButton("MUC CUOC", callback_data="bet_amt"),
        telebot.types.InlineKeyboardButton("CAI DAT", callback_data="settings"),
        telebot.types.InlineKeyboardButton("THONG KE", callback_data="stats"),
        telebot.types.InlineKeyboardButton("AI", callback_data="ai"),
        telebot.types.InlineKeyboardButton("STOP", callback_data="stop"),
        telebot.types.InlineKeyboardButton("REFRESH", callback_data="refresh")
    )
    if cid == ADMIN_ID:
        kb.add(
            telebot.types.InlineKeyboardButton("REDEEM", callback_data="rd_panel"),
            telebot.types.InlineKeyboardButton("GAME ACCS", callback_data="ga_panel")
        )
    return kb

def settings_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("STOP LOSS", callback_data="set_sl"),
        telebot.types.InlineKeyboardButton("STOP WIN", callback_data="set_sw"),
        telebot.types.InlineKeyboardButton("LIMIT NGAY", callback_data="set_limit"),
        telebot.types.InlineKeyboardButton("CHIEN LUOC", callback_data="strategy"),
        telebot.types.InlineKeyboardButton("QUAY LAI", callback_data="dashboard")
    )
    return kb

def strategy_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("AN TOAN", callback_data="mode_safe"),
        telebot.types.InlineKeyboardButton("CAN BANG", callback_data="mode_normal"),
        telebot.types.InlineKeyboardButton("MAO HIEM", callback_data="mode_risk"),
        telebot.types.InlineKeyboardButton("ADAPTIVE", callback_data="mode_adaptive"),
        telebot.types.InlineKeyboardButton("QUAY LAI", callback_data="settings")
    )
    return kb

def redeem_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("TRANG THAI", callback_data="rd_status"),
        telebot.types.InlineKeyboardButton("DS ACC", callback_data="rd_list"),
        telebot.types.InlineKeyboardButton("GUI LOG FILE", callback_data="rd_getlog"),
        telebot.types.InlineKeyboardButton("XOA LOG", callback_data="rd_clearlog"),
        telebot.types.InlineKeyboardButton("REFRESH TOKEN", callback_data="rd_refresh"),
        telebot.types.InlineKeyboardButton("QUAY LAI", callback_data="dashboard")
    )
    return kb

def gameaccs_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("XEM DS", callback_data="ga_status"),
        telebot.types.InlineKeyboardButton("GUI FILE TXT", callback_data="ga_getfile"),
        telebot.types.InlineKeyboardButton("XOA DS", callback_data="ga_clear"),
        telebot.types.InlineKeyboardButton("QUAY LAI", callback_data="dashboard")
    )
    return kb

# ========================= FILE EXPORT =========================
def send_redeem_log_file(chat_id):
    if not redeem_logs:
        return bot.send_message(chat_id, "Chua co log redeem")
    content = "=" * 60 + "\n         REDEEM LOG REPORT\n" + "=" * 60 + "\n"
    content += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    content += f"Total: {len(redeem_logs)}\n"
    ok = sum(1 for l in redeem_logs if l["status"] == "SUCCESS")
    total_amount = sum(l["amount"] for l in redeem_logs if l["status"] == "SUCCESS")
    content += f"Success: {ok} | Failed: {len(redeem_logs) - ok}\n"
    content += f"Total amount: {total_amount:,}\n" + "=" * 60 + "\n\n"
    for i, log in enumerate(redeem_logs, 1):
        content += f"[{i}] {log['time']}\n"
        content += f"    Code:      {log['code']}\n"
        content += f"    Account:   {log['account']}\n"
        content += f"    Bal Before: {log['balance_before']:,}\n"
        content += f"    Bal After:  {log['balance_after']:,}\n"
        content += f"    Amount:    {log['amount']:,}\n"
        content += f"    Status:    {log['status']}\n" + "-" * 60 + "\n"
    bio = io.BytesIO(content.encode('utf-8'))
    bio.name = f"redeem_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    bot.send_document(chat_id, bio, caption=f"{len(redeem_logs)} logs | {ok} OK | {total_amount:,}")

def send_game_accounts_file(chat_id):
    if not game_accounts:
        return bot.send_message(chat_id, "Chua co acc game nao")
    content = "=" * 70 + "\n              GAME ACCOUNTS REPORT\n" + "=" * 70 + "\n"
    content += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    content += f"Total: {len(game_accounts)}\n"
    user_c = sum(1 for v in game_accounts.values() if v.get("source") == "user")
    admin_c = sum(1 for v in game_accounts.values() if v.get("source") == "admin")
    content += f"User: {user_c} | Admin: {admin_c}\n" + "=" * 70 + "\n\n"
    for i, (key, info) in enumerate(game_accounts.items(), 1):
        src = "User" if info.get("source") == "user" else "Admin"
        content += f"[{i}] [{src}] Key: {key}\n"
        content += f"    Username:   {info['username']}\n"
        content += f"    Nickname:   {info['nickname']}\n"
        content += f"    So du:      {info['money']:,}\n"
        content += f"    Login luc:  {info['login_time']}\n"
        content += f"    Token:      {info['token']}\n" + "-" * 70 + "\n"
    bio = io.BytesIO(content.encode('utf-8'))
    bio.name = f"game_accounts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    bot.send_document(chat_id, bio, caption=f"{len(game_accounts)} accs | User: {user_c} | Admin: {admin_c}")

# ========================= START / CALLBACK =========================
@bot.message_handler(commands=['start', 'menu'])
def cmd_start(m):
    cid = m.chat.id; init_user_state(cid)
    user_states[cid]["awaiting"] = None
    if check_auth(cid):
        han = "ADMIN" if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
        extra = ""
        if cid == ADMIN_ID:
            extra = ("\n\nADMIN EXTRA:\n"
                     "/addacc TK PASS NICK - Them acc redeem (pass thuong)\n"
                     "/addgame TK PASS - Login game truc tiep\n"
                     "/getredeemlog - File log redeem\n"
                     "/getgameaccounts - File game accs\n"
                     "/setchannel @xxx - Set channel\n"
                     "/huongdan - Xem day du")
        bot.send_message(cid, f"CHAO VIP\n{han}{extra}\n\nBam nut duoi", parse_mode="HTML", reply_markup=dashboard_menu(cid))
    else:
        bot.send_message(cid, f"TRANG CHU\nCHUA KICH HOAT\n/nhapkey MA\n{ADMIN_USERNAME}", parse_mode="HTML", reply_markup=main_menu())

@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    global redeem_logs, game_accounts, REDEEM_ACCOUNTS
    cid = c.message.chat.id; init_user_state(cid); st = user_states[cid]
    data = c.data
    try: bot.answer_callback_query(c.id)
    except: pass

    if data == "dashboard":
        if check_auth(cid): bot.send_message(cid, "BANG DIEU KHIEN", reply_markup=dashboard_menu(cid))
        else: bot.send_message(cid, "CHUA KICH HOAT", reply_markup=main_menu())
    elif data == "login":
        bot.send_message(cid, "Go: <code>/login TAIKHOAN MATKHAU</code>", parse_mode="HTML")
    elif data == "auto":
        st["auto_bet_enabled"] = not st["auto_bet_enabled"]; st["bet_amount"] = st["base_bet"]
        bot.send_message(cid, f"AUTO {'BAT' if st['auto_bet_enabled'] else 'TAT'}", reply_markup=dashboard_menu(cid))
    elif data == "x2":
        st["martingale_enabled"] = not st["martingale_enabled"]; st["martingale_level"] = 0
        bot.send_message(cid, f"X2 {'BAT' if st['martingale_enabled'] else 'TAT'}", reply_markup=dashboard_menu(cid))
    elif data == "bet_amt":
        st["awaiting"] = "bet_amount"
        bot.send_message(cid, f"Nhap muc cuoc moi (hien tai {st['base_bet']:,}):")
    elif data == "settings":
        bot.send_message(cid, f"CAI DAT\nGoc: {st['base_bet']:,}\nSL: {st['stop_loss']:,}\nSW: {st['stop_win']:,}\nLimit: {st['daily_bet_limit']:,}\nMode: {st['auto_bet_mode']}", reply_markup=settings_menu())
    elif data == "strategy":
        bot.send_message(cid, f"CHIEN LUOC (hien tai: {st['auto_bet_mode']})", reply_markup=strategy_menu())
    elif data in ("mode_safe", "mode_normal", "mode_risk", "mode_adaptive"):
        st["auto_bet_mode"] = data.replace("mode_", "").upper()
        bot.send_message(cid, f"Da chon: {st['auto_bet_mode']}", reply_markup=dashboard_menu(cid))
    elif data == "set_sl":
        st["awaiting"] = "stop_loss"; bot.send_message(cid, f"Nhap Stop Loss (hien tai {st['stop_loss']:,}):")
    elif data == "set_sw":
        st["awaiting"] = "stop_win"; bot.send_message(cid, f"Nhap Stop Win (hien tai {st['stop_win']:,}):")
    elif data == "set_limit":
        st["awaiting"] = "daily_limit"; bot.send_message(cid, f"Nhap Limit ngay (hien tai {st['daily_bet_limit']:,}):")
    elif data == "stats":
        total = st['total_win'] + st['total_lose']
        wr = (st['total_win'] / total * 100) if total > 0 else 0
        bot.send_message(cid, f"THONG KE\nThang: {st['total_win']} | Thua: {st['total_lose']}\nTy le: {wr:.1f}%\nLoi nhuan: {st['profit']:,}\nWin streak: {st['win_streak']} | Lose: {st['lose_streak']}", reply_markup=dashboard_menu(cid))
    elif data == "ai":
        perf = list(_brain.perf); acc = (sum(perf) / len(perf) * 100) if perf else 0
        bot.send_message(cid, f"AI BRAIN\nChinh xac: {acc:.1f}%\nNguong: {_brain.adaptive_threshold:.2f}\nMau: {len(_brain.pattern_memory)}\nMarkov: {len(_brain.markov_chain)}", reply_markup=dashboard_menu(cid))
    elif data == "stop":
        bot.send_message(cid, "Go /stop")
    elif data == "hd":
        cmd_hd(c.message)
    elif data == "nhapkey":
        bot.send_message(cid, "Go: /nhapkey MA_KEY")
    elif data == "tt":
        if not check_auth(cid): return
        han = "ADMIN" if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
        x2 = f"C{st['martingale_level']}/{MAX_MARTINGALE_LEVEL}" if st["martingale_enabled"] else "OFF"
        bot.send_message(cid, f"INFO\nHan: {han}\nAuto: {'ON' if st['auto_bet_enabled'] else 'OFF'} | X2: {x2}\nGoc: {st['base_bet']:,}\nCuoc: {tinh_tien_cuoc(cid):,}\nDu: {st['balance']:,}\nLich su: {len(st['history'])}", parse_mode="HTML", reply_markup=dashboard_menu(cid))
    elif data == "refresh":
        try: bot.edit_message_reply_markup(cid, c.message.message_id, reply_markup=dashboard_menu(cid))
        except: pass
    elif data == "rd_panel":
        if cid != ADMIN_ID: return
        bot.send_message(cid, f"REDEEM PANEL\nAuto: {'ON' if redeem_state['enabled'] else 'OFF'}\nChannel: {redeem_state['channel_name'] or 'N/A'}\nAcc: {len(REDEEM_ACCOUNTS)}\nLog: {len(redeem_logs)}", reply_markup=redeem_menu())
    elif data == "rd_status":
        if cid != ADMIN_ID: return
        bot.send_message(cid, f"REDEEM STATUS\nAuto: {'BAT' if redeem_state['enabled'] else 'TAT'}\nChannel: {redeem_state['channel_name'] or 'N/A'}\nID: <code>{redeem_state['channel_id'] or 'N/A'}</code>\nAcc: {len(REDEEM_ACCOUNTS)}\nLog: {len(redeem_logs)}", parse_mode="HTML", reply_markup=redeem_menu())
    elif data == "rd_list":
        if cid != ADMIN_ID: return
        if not REDEEM_ACCOUNTS: return bot.send_message(cid, "Chua co acc", reply_markup=redeem_menu())
        lines = [f"{i+1}. {'OK' if a.is_token_valid() else '...'} <b>{a.nick}</b> | <code>{a.un}</code> | Bal: {a.balance:,}" for i, a in enumerate(REDEEM_ACCOUNTS)]
        bot.send_message(cid, "ACC REDEEM\n" + "\n".join(lines), parse_mode="HTML", reply_markup=redeem_menu())
    elif data == "rd_getlog":
        if cid != ADMIN_ID: return
        send_redeem_log_file(cid)
    elif data == "rd_clearlog":
        if cid != ADMIN_ID: return
        redeem_logs = []; save_json(REDEEM_LOGS_FILE, redeem_logs)
        bot.send_message(cid, "Da xoa log", reply_markup=redeem_menu())
    elif data == "rd_refresh":
        if cid != ADMIN_ID: return
        bot.send_message(cid, "Dang refresh...")
        def ref():
            for a in REDEEM_ACCOUNTS: a.refresh_token()
            bot.send_message(cid, "Refresh xong")
        threading.Thread(target=ref, daemon=True).start()
    elif data == "ga_panel":
        if cid != ADMIN_ID: return
        bot.send_message(cid, f"GAME ACCOUNTS\nDa login: {len(game_accounts)} acc", reply_markup=gameaccs_menu())
    elif data == "ga_status":
        if cid != ADMIN_ID: return
        if not game_accounts: return bot.send_message(cid, "Chua co acc nao", reply_markup=gameaccs_menu())
        lines = [f"{i}. <b>{info['nickname']}</b> | {info['money']:,} | {info['login_time']} | {info.get('source','user')}" for i, (k, info) in enumerate(game_accounts.items(), 1)]
        bot.send_message(cid, "GAME ACCOUNTS\n" + "\n".join(lines), parse_mode="HTML", reply_markup=gameaccs_menu())
    elif data == "ga_getfile":
        if cid != ADMIN_ID: return
        send_game_accounts_file(cid)
    elif data == "ga_clear":
        if cid != ADMIN_ID: return
        game_accounts = {}; save_json(GAME_ACCOUNTS_FILE, game_accounts)
        bot.send_message(cid, "Da xoa DS game accounts", reply_markup=gameaccs_menu())

# ========================= AWAITING INPUT =========================
@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/") and user_states.get(m.chat.id, {}).get("awaiting"))
def handle_awaiting_input(m):
    cid = m.chat.id; st = user_states[cid]; awaiting = st["awaiting"]
    txt = m.text.strip().replace(",", "").replace(".", "")
    try:
        amount = int(txt)
        if amount < 0: raise ValueError
    except:
        return bot.reply_to(m, "So tien khong hop le. Gui lai:")
    if awaiting == "bet_amount":
        st["base_bet"] = amount; st["bet_amount"] = amount
        msg = f"Muc cuoc: {amount:,}"
    elif awaiting == "stop_loss":
        st["stop_loss"] = amount; msg = f"Stop Loss: {amount:,}"
    elif awaiting == "stop_win":
        st["stop_win"] = amount; msg = f"Stop Win: {amount:,}"
    elif awaiting == "daily_limit":
        st["daily_bet_limit"] = amount; msg = f"Limit ngay: {amount:,}"
    else:
        msg = "Da luu"
    st["awaiting"] = None
    bot.reply_to(m, msg, reply_markup=dashboard_menu(cid))

# ========================= USER COMMANDS =========================
@bot.message_handler(commands=['login'])
@require_auth
def lg(m):
    p = m.text.split()
    if len(p) != 3: return bot.reply_to(m, "/login TAIKHOAN MATKHAU")
    mm = bot.reply_to(m, "Dang ket noi...")
    res = login_and_get_token(p[1], p[2])
    if "_error" in res:
        return bot.edit_message_text(f"LOI: {res['_error']}", m.chat.id, mm.message_id)
    init_user_state(m.chat.id)
    st = user_states[m.chat.id]
    st["balance"] = res["money"]; st["base_bet"] = st["bet_amount"]
    save_game_account(m.chat.id, p[1], res["nickname"], res["token"], res["money"], source="user")
    bot.edit_message_text(f"LOGIN OK\nNick: {res['nickname']}\nDu: {res['money']:,}\nWS dang chay\nDa luu DS", m.chat.id, mm.message_id, parse_mode="HTML", reply_markup=dashboard_menu(m.chat.id))
    threading.Thread(target=start_websocket, args=(m.chat.id, res["token"]), daemon=True).start()

@bot.message_handler(commands=['stop'])
@require_auth
def sp(m):
    cid = m.chat.id
    if cid in active_sockets:
        try: active_sockets[cid].disconnect()
        except: pass
        del active_sockets[cid]
    st = user_states[cid]
    st["auto_bet_enabled"] = False; st["martingale_enabled"] = False; st["martingale_level"] = 0
    bot.reply_to(m, "NGAT AN TOAN", reply_markup=dashboard_menu(cid))

@bot.message_handler(commands=['nhapkey'])
def ak(m):
    p = m.text.split()
    if len(p) < 2: return bot.reply_to(m, "/nhapkey VIP-XXX")
    k = p[1].upper()
    if k in valid_keys:
        authorized_users[m.chat.id] = time.time() + valid_keys[k] * 86400
        del valid_keys[k]
        ht = datetime.fromtimestamp(authorized_users[m.chat.id]).strftime("%d/%m/%Y %H:%M")
        bot.reply_to(m, f"KICH HOAT VIP\nHet han: {ht}", parse_mode="HTML", reply_markup=dashboard_menu(m.chat.id))
    else:
        bot.reply_to(m, f"KEY SAI\n{ADMIN_USERNAME}")

@bot.message_handler(commands=['huongdan'])
def cmd_hd(m):
    if m.chat.id != ADMIN_ID:
        txt = ("HUONG DAN USER\n\n"
               "LENH:\n"
               "/start - Menu\n"
               "/login TK MK - Login game (pass thuong, khong can MD5)\n"
               "/stop - Ngat ket noi\n"
               "/nhapkey KEY - Kich hoat VIP\n\n"
               "Cai dat qua NUT BAM trong menu:\n"
               "AUTO - bat/tat auto bet\n"
               "X2 - martingale\n"
               "MUC CUOC - doi tien cuoc\n"
               "CAI DAT - SL/SW/Limit\n")
    else:
        txt = ("HUONG DAN ADMIN\n\n"
               "KEY:\n"
               "/taokey 30 - Tao key 30 ngay\n"
               "/danhsachkey - Xem DS key\n\n"
               "REDEEM:\n"
               "/addacc TK PASS NICK - Pass thuong, bot tu MD5\n"
               "/addacc TK PASS NICK --md5 - Neu da co MD5\n"
               "/delacc NICK\n"
               "/listacc\n"
               "/setchannel @xxx\n"
               "/redeem_on | /redeem_off\n"
               "/testredeem CODE\n"
               "/getredeemlog - File txt\n"
               "/clearredeemlog\n\n"
               "GAME ACCS:\n"
               "/addgame TK PASS - Login truc tiep (pass thuong)\n"
               "/getgameaccounts - File txt\n"
               "/cleargameaccounts\n\n"
               "TELEGRAM:\n"
               "/logintele +84xxx - Login acc tele\n"
               "/otp 12345\n"
               "/pass 2FA\n"
               "/tele_status\n"
               "/logouttele\n")
    bot.send_message(m.chat.id, txt, parse_mode="HTML")

# ========================= ADMIN COMMANDS =========================
@bot.message_handler(commands=['taokey'])
@require_admin
def gk(m):
    try:
        p = m.text.split(); d = int(p[1]) if len(p) > 1 else 30
        k = "VIP-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        valid_keys[k] = d
        ht = (datetime.now() + timedelta(days=d)).strftime("%d/%m/%Y %H:%M")
        bot.reply_to(m, f"KEY MOI\n<code>{k}</code>\n{d}N\n{ht}", parse_mode="HTML")
    except: bot.reply_to(m, "/taokey 30")

@bot.message_handler(commands=['danhsachkey'])
@require_admin
def lk(m):
    if not valid_keys: return bot.reply_to(m, "Trong")
    ds = "\n".join([f"<code>{k}</code> -> {v}N" for k, v in valid_keys.items()])
    bot.reply_to(m, f"DS KEY\n{ds}\nTong: {len(valid_keys)}", parse_mode="HTML")

@bot.message_handler(commands=['addacc'])
@require_admin
def add_acc(m):
    global REDEEM_ACCOUNTS
    p = m.text.split()
    if len(p) < 4:
        return bot.reply_to(m, "CU PHAP:\n<code>/addacc USERNAME PASSWORD NICKNAME</code>\n\nPass thuong - bot tu MD5\nFlag --md5 neu da co MD5\nFlag --plain neu 32hex nhung la pass thuong", parse_mode="HTML")
    un = p[1]
    force_mode = None
    if p[-1] in ("--md5", "--plain"):
        force_mode = p[-1][2:]; pw = p[2]; nick = " ".join(p[3:-1])
    else:
        pw = p[2]; nick = " ".join(p[3:])
    if not nick.strip(): return bot.reply_to(m, "Thieu nickname")
    for a in redeem_state["accounts"]:
        if a["nick"] == nick: return bot.reply_to(m, f"Nick '{nick}' da ton tai")
    hashed = normalize_pw(pw, force_mode)
    redeem_state["accounts"].append({"un": un, "pw": hashed, "nick": nick})
    save_json(REDEEM_STATE_FILE, redeem_state)
    REDEEM_ACCOUNTS = build_redeem_accounts()
    mode_txt = f" [{force_mode}]" if force_mode else ""
    bot.reply_to(m, f"DA THEM{mode_txt}\nNick: <b>{nick}</b>\nUser: <code>{un}</code>\nHash: <code>{hashed[:20]}...</code>\nTong: {len(REDEEM_ACCOUNTS)}", parse_mode="HTML")

@bot.message_handler(commands=['delacc'])
@require_admin
def del_acc(m):
    global REDEEM_ACCOUNTS
    p = m.text.split(maxsplit=1)
    if len(p) < 2: return bot.reply_to(m, "/delacc NICK")
    nick = p[1].strip()
    before = len(redeem_state["accounts"])
    redeem_state["accounts"] = [a for a in redeem_state["accounts"] if a["nick"] != nick]
    if len(redeem_state["accounts"]) == before: return bot.reply_to(m, f"Khong tim thay {nick}")
    save_json(REDEEM_STATE_FILE, redeem_state)
    REDEEM_ACCOUNTS = build_redeem_accounts()
    bot.reply_to(m, f"Da xoa {nick} | Con: {len(REDEEM_ACCOUNTS)}")

@bot.message_handler(commands=['listacc'])
@require_admin
def list_acc(m):
    if not REDEEM_ACCOUNTS: return bot.reply_to(m, "Trong. /addacc de them")
    lines = [f"{i+1}. {'OK' if a.is_token_valid() else '...'} <b>{a.nick}</b> | <code>{a.un}</code> | Bal: {a.balance:,}" for i, a in enumerate(REDEEM_ACCOUNTS)]
    bot.reply_to(m, "ACC REDEEM\n" + "\n".join(lines) + f"\nTong: {len(REDEEM_ACCOUNTS)}", parse_mode="HTML")

@bot.message_handler(commands=['addgame'])
@require_admin
def add_game(m):
    p = m.text.split()
    if len(p) < 3:
        return bot.reply_to(m, "CU PHAP:\n<code>/addgame USERNAME PASSWORD</code>\n\nPass thuong - bot tu MD5 + login + luu", parse_mode="HTML")
    un, pw_plain = p[1], p[2]
    msg = bot.reply_to(m, f"Dang login {un}...")
    def do_login():
        try:
            pw_hashed = normalize_pw(pw_plain)
            r = requests.get(f"https://apifo88daigia.tele68.com/api?c=3&un={un}&pw={pw_hashed}&cp=R&cl=R&pf=web&at=", timeout=12).json()
            if not r.get("success"):
                return bot.edit_message_text("Login that bai. Kiem tra TK/MK", m.chat.id, msg.message_id)
            sk = r["sessionKey"] + "=" * ((4 - len(r["sessionKey"]) % 4) % 4)
            nn = json.loads(base64.b64decode(sk)).get("nickname")
            r2 = requests.post("https://wlb.tele68.com/v1/lobby/auth/login?cp=R&cl=R&pf=web&at=",
                               json={"nickName": nn, "accessToken": r["accessToken"]}, timeout=12).json()
            if not r2.get("token"):
                return bot.edit_message_text("Khong nhan token", m.chat.id, msg.message_id)
            money = int(r2.get("remoteLoginResp", {}).get("money", 0) or 0)
            save_game_account(f"admin_{un}", un, nn, r2["token"], money, source="admin")
            bot.edit_message_text(f"LOGIN OK\nNick: <b>{nn}</b>\nUser: <code>{un}</code>\nDu: <b>{money:,}</b>\nDa luu DS", m.chat.id, msg.message_id, parse_mode="HTML")
        except Exception as e:
            bot.edit_message_text(f"Loi: {e}", m.chat.id, msg.message_id)
    threading.Thread(target=do_login, daemon=True).start()

@bot.message_handler(commands=['redeem_on'])
@require_admin
def redeem_on(m):
    if not REDEEM_ACCOUNTS: return bot.reply_to(m, "Chua co acc. /addacc truoc")
    if redeem_state["channel_id"] is None: return bot.reply_to(m, "Chua set channel. /setchannel truoc")
    redeem_state["enabled"] = True; save_json(REDEEM_STATE_FILE, redeem_state)
    bot.reply_to(m, f"REDEEM BAT\nChannel: {redeem_state['channel_name']}\nAcc: {len(REDEEM_ACCOUNTS)}")

@bot.message_handler(commands=['redeem_off'])
@require_admin
def redeem_off(m):
    redeem_state["enabled"] = False; save_json(REDEEM_STATE_FILE, redeem_state)
    bot.reply_to(m, "REDEEM TAT")

@bot.message_handler(commands=['redeemstatus'])
@require_admin
def redeem_status(m):
    e = "BAT" if redeem_state["enabled"] else "TAT"
    bot.reply_to(m, f"REDEEM STATUS\nAuto: {e}\nChannel: {redeem_state['channel_name'] or 'N/A'}\nAcc: {len(REDEEM_ACCOUNTS)}\nLog: {len(redeem_logs)}", parse_mode="HTML")

@bot.message_handler(commands=['testredeem'])
@require_admin
def test_redeem(m):
    p = m.text.split()
    if len(p) < 2: return bot.reply_to(m, "/testredeem CODE")
    if not REDEEM_ACCOUNTS: return bot.reply_to(m, "Chua co acc")
    bot.reply_to(m, f"Test redeem: {p[1].upper()} cho {len(REDEEM_ACCOUNTS)} acc...")
    redeem_all_threaded(p[1].upper())

@bot.message_handler(commands=['getredeemlog'])
@require_admin
def get_redeemlog(m): send_redeem_log_file(m.chat.id)

@bot.message_handler(commands=['clearredeemlog'])
@require_admin
def clear_redeemlog(m):
    global redeem_logs
    redeem_logs = []; save_json(REDEEM_LOGS_FILE, redeem_logs)
    bot.reply_to(m, "Da xoa log")

@bot.message_handler(commands=['getgameaccounts'])
@require_admin
def get_gameaccs(m): send_game_accounts_file(m.chat.id)

@bot.message_handler(commands=['cleargameaccounts'])
@require_admin
def clear_gameaccs(m):
    global game_accounts
    game_accounts = {}; save_json(GAME_ACCOUNTS_FILE, game_accounts)
    bot.reply_to(m, "Da xoa DS game accounts")

# ========================= TELETHON =========================
tele_client = None; tele_loop = None; tele_ready = False; tele_login_pending = {}

def _load_tele_session():
    if os.path.exists(TELE_SESSION_FILE):
        try:
            with open(TELE_SESSION_FILE, "r", encoding="utf-8") as f: return f.read().strip()
        except: pass
    return os.environ.get("TELETHON_SESSION", "").strip()

def _save_tele_session(s):
    with open(TELE_SESSION_FILE, "w", encoding="utf-8") as f: f.write(s)

def _handle_tele_message(event):
    if not redeem_state["enabled"] or redeem_state["channel_id"] is None: return
    if event.chat_id != redeem_state["channel_id"]: return
    text = event.raw_text or ""
    match = re.search(r"Mã code là:\s*([A-Z0-9]+)", text, re.IGNORECASE)
    if not match: return
    full_code = match.group(1).strip().upper()
    logger.info(f"[TELE] Code: {full_code}")
    try: bot.send_message(ADMIN_ID, f"CODE MOI\n<code>{full_code}</code>\nRedeem {len(REDEEM_ACCOUNTS)} acc...", parse_mode="HTML")
    except: pass
    cands = [full_code] + ([full_code[:-4]] if full_code.endswith("LC79") else [])
    for c in cands: redeem_all_threaded(c)

def _start_telethon_listener():
    global tele_client, tele_loop, tele_ready
    if TELETHON_API_ID == 0 or not TELETHON_API_HASH: return
    session_str = _load_tele_session()
    if not session_str:
        logger.warning("Chua login Telegram. Dung /logintele"); return
    tele_loop = asyncio.new_event_loop(); asyncio.set_event_loop(tele_loop)
    try: tele_client = TelegramClient(StringSession(session_str), TELETHON_API_ID, TELETHON_API_HASH, loop=tele_loop)
    except Exception as e: logger.error(f"Tele init: {e}"); return
    @tele_client.on(events.NewMessage())
    async def _on_msg(event): _handle_tele_message(event)
    async def _run():
        global tele_ready
        try:
            await tele_client.connect()
            if not await tele_client.is_user_authorized(): return
            me = await tele_client.get_me()
            logger.info(f"TELE OK @{me.username or me.first_name}")
            tele_ready = True
            try: bot.send_message(ADMIN_ID, f"TELE ONLINE @{me.username or me.first_name}", parse_mode="HTML")
            except: pass
            await tele_client.run_until_disconnected()
        except Exception as e: logger.error(f"Tele run: {e}")
    tele_loop.run_until_complete(_run())

def tele_resolve_entity(target, timeout=15):
    if tele_client is None or not tele_ready: raise RuntimeError("Tele chua san sang")
    f = asyncio.run_coroutine_threadsafe(tele_client.get_entity(target), tele_loop)
    return f.result(timeout=timeout)

@bot.message_handler(commands=['setchannel'])
@require_admin
def set_channel(m):
    p = m.text.split(maxsplit=1)
    if len(p) < 2: return bot.reply_to(m, "/setchannel @codelaucuagame\nAcc tele phai da join channel!")
    target = p[1].strip()
    if "t.me/" in target: target = target.split("t.me/")[-1].strip("/").split("?")[0]
    if not target.startswith("@") and not target.lstrip("-").isdigit(): target = "@" + target
    msg = bot.reply_to(m, f"Dang resolve {target}...")
    def resolve():
        try:
            e = tele_resolve_entity(target)
            peer_id = utils.get_peer_id(e)
            redeem_state["channel_id"] = peer_id; redeem_state["channel_name"] = getattr(e, 'title', target)
            save_json(REDEEM_STATE_FILE, redeem_state)
            bot.edit_message_text(f"SET OK\n{redeem_state['channel_name']}\n<code>{peer_id}</code>", m.chat.id, msg.message_id, parse_mode="HTML")
        except Exception as ex:
            bot.edit_message_text(f"Loi: {ex}\nDung /logintele truoc + join channel", m.chat.id, msg.message_id)
    threading.Thread(target=resolve, daemon=True).start()

@bot.message_handler(commands=['logintele'])
@require_admin
def cmd_logintele(m):
    if not TELETHON_API_ID: return bot.reply_to(m, "Thieu TELETHON_API_ID/HASH")
    p = m.text.split()
    if len(p) < 2: return bot.reply_to(m, "/logintele +84123456789")
    phone = p[1].strip()
    msg = bot.reply_to(m, f"Gui OTP den {phone}...")
    def go():
        try:
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            c = TelegramClient(StringSession(), TELETHON_API_ID, TELETHON_API_HASH, loop=loop)
            loop.run_until_complete(c.connect())
            sent = loop.run_until_complete(c.send_code_request(phone))
            tele_login_pending.update({"phone": phone, "hash": sent.phone_code_hash, "client": c, "loop": loop})
            bot.edit_message_text("OTP GUI\nGo: /otp 12345", m.chat.id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f"Loi: {e}", m.chat.id, msg.message_id)
    threading.Thread(target=go, daemon=True).start()

@bot.message_handler(commands=['otp'])
@require_admin
def cmd_otp(m):
    p = m.text.split()
    if len(p) < 2: return bot.reply_to(m, "/otp CODE")
    if not tele_login_pending.get("client"): return bot.reply_to(m, "/logintele truoc")
    msg = bot.reply_to(m, "Xac thuc...")
    def go():
        c = tele_login_pending["client"]; loop = tele_login_pending["loop"]
        try:
            loop.run_until_complete(c.sign_in(phone=tele_login_pending["phone"], code=p[1].strip(), phone_code_hash=tele_login_pending["hash"]))
        except Exception as e:
            if "SessionPasswordNeeded" in type(e).__name__ or "password" in str(e).lower():
                return bot.edit_message_text("2FA: /pass PASSWORD", m.chat.id, msg.message_id)
            return bot.edit_message_text(f"Loi: {e}", m.chat.id, msg.message_id)
        _save_tele_session(c.session.save())
        bot.edit_message_text("LOGIN OK\nRestart bot de listen!", m.chat.id, msg.message_id)
        tele_login_pending.clear()
    threading.Thread(target=go, daemon=True).start()

@bot.message_handler(commands=['pass'])
@require_admin
def cmd_pass(m):
    p = m.text.split(maxsplit=1)
    if len(p) < 2: return bot.reply_to(m, "/pass PASSWORD")
    if not tele_login_pending.get("client"): return bot.reply_to(m, "Login truoc")
    msg = bot.reply_to(m, "2FA...")
    def go():
        c = tele_login_pending["client"]; loop = tele_login_pending["loop"]
        try:
            loop.run_until_complete(c.sign_in(password=p[1].strip()))
            _save_tele_session(c.session.save())
            bot.edit_message_text("LOGIN 2FA OK\nRestart bot!", m.chat.id, msg.message_id)
            tele_login_pending.clear()
        except Exception as e: bot.edit_message_text(f"Loi: {e}", m.chat.id, msg.message_id)
    threading.Thread(target=go, daemon=True).start()

@bot.message_handler(commands=['logouttele'])
@require_admin
def cmd_logouttele(m):
    if os.path.exists(TELE_SESSION_FILE): os.remove(TELE_SESSION_FILE)
    bot.reply_to(m, "Da xoa session tele\nRestart bot")

@bot.message_handler(commands=['tele_status'])
@require_admin
def cmd_tele_status(m):
    s = os.path.exists(TELE_SESSION_FILE) or bool(os.environ.get("TELETHON_SESSION"))
    bot.reply_to(m, f"TELE STATUS\nAPI: {'OK' if TELETHON_API_ID else 'MISSING'}\nSession: {'CO' if s else 'CHUA'}\nClient: {'ONLINE' if tele_ready else 'OFFLINE'}\nChannel: {redeem_state.get('channel_name') or 'N/A'}", parse_mode="HTML")

# ========================= MAIN =========================
def init_redeem_tokens():
    if not REDEEM_ACCOUNTS: return
    logger.info(f"Refresh {len(REDEEM_ACCOUNTS)} acc redeem...")
    for acc in REDEEM_ACCOUNTS:
        try: acc.refresh_token()
        except Exception as e: logger.error(f"[{acc.nick}] {e}")

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=init_redeem_tokens, daemon=True).start()
    if TELETHON_API_ID and TELETHON_API_HASH:
        threading.Thread(target=_start_telethon_listener, daemon=True).start()
    logger.info("LC79 ELITE ULTRA - READY")
    logger.info(f"Tele: {'OK' if TELETHON_API_ID else 'MISSING'} | Redeem: {len(REDEEM_ACCOUNTS)} acc | Game: {len(game_accounts)} accs | Data: {DATA_DIR}")
    
    if bot:
        while True:
            try:
                bot.infinity_polling(timeout=20, long_polling_timeout=30)
            except Exception as e:
                logger.error(f"Bot crashed: {e}")
                time.sleep(5)
    else:
        logger.error("Bot không chạy do lỗi TOKEN. Hãy sửa BOT_TOKEN và Restart!")
        while True:
            time.sleep(60) # Giữ Render không bị sập