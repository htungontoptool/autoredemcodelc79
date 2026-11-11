"""
Bot auto-redeem giftcode cho Telegram channel @codelaucuagame.
Chạy trên Render (headless, không menu).

ENV VARS cần thiết:
  TG_SESSION_STRING  ← từ gen_session.py (chạy local 1 lần)
  ACCOUNTS_JSON      ← [{"nick":"acc1","un":"user1","pw":"hash1"}, ...]
  TG_API_ID          ← optional, default 36655605
  TG_API_HASH        ← optional, default đã set
  DATA_DIR           ← optional, default ./data
  PORT               ← Render tự set
"""

import os
import re
import sys
import json
import time
import random
import base64
import signal
import threading
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════
API_ID = int(os.getenv("TG_API_ID", "36655605"))
API_HASH = os.getenv("TG_API_HASH", "98ac382a42aca5e63d0578a9a4e52c8c")
SESSION_STRING = os.getenv("TG_SESSION_STRING", "").strip()
ACCOUNTS_JSON_ENV = os.getenv("ACCOUNTS_JSON", "").strip()

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = DATA_DIR / "tokens.txt"
HISTORY_FILE = DATA_DIR / "history.json"

TARGET_CHAT_LINK = "https://t.me/codelaucuagame"
TARGET_CHAT_ID = None

API_GET_ACCESS = "https://apifo88daigia.tele68.com/api"
LOGIN_URL = "https://wlb.tele68.com/v1/lobby/auth/login"
BASE_URL = "https://gameapi.tele68.com"
PARAMS = "/v1/giftcode/redemption?cp=R&cl=R&pf=web&at="
BALANCE_URL = "https://wlb.tele68.com/v1/lobby/user/info"

STAGGER_SEC = 1.0
STAGGER_JITTER = 0.3
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 45
MAX_RETRY = 3

# ═══════════════════════════════════════════════════════════════════
# VALIDATE
# ═══════════════════════════════════════════════════════════════════
if not SESSION_STRING:
    print("❌ Thiếu env TG_SESSION_STRING. Chạy gen_session.py local trước.")
    sys.exit(1)

if not ACCOUNTS_JSON_ENV:
    print("❌ Thiếu env ACCOUNTS_JSON. Set trên Render dashboard.")
    sys.exit(1)

try:
    _acc_data = json.loads(ACCOUNTS_JSON_ENV)
    ACCOUNTS_CONFIG = [
        a for a in _acc_data
        if a.get("nick") and a.get("un") and a.get("pw")
    ]
    print(f"📥 Load {len(ACCOUNTS_CONFIG)} acc từ ENV ACCOUNTS_JSON")
except json.JSONDecodeError as e:
    print(f"❌ ACCOUNTS_JSON sai format JSON: {e}")
    sys.exit(1)

if not ACCOUNTS_CONFIG:
    print("❌ Không có account nào hợp lệ.")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════
# HISTORY PERSIST
# ═══════════════════════════════════════════════════════════════════
_history_lock = threading.Lock()


def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_history(history):
    with _history_lock:
        try:
            HISTORY_FILE.write_text(
                json.dumps(history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"⚠️ save_history: {e}")


HISTORY = load_history()

# ═══════════════════════════════════════════════════════════════════
# ACCOUNT
# ═══════════════════════════════════════════════════════════════════
class Account:
    def __init__(self, un, pw, nick):
        self.un = un
        self.pw = pw
        self.nick = nick
        self.used_codes = set()
        self.lock = threading.Lock()
        self.jwt = None
        self.jwt_exp = 0
        self.balance = 0

    def _headers(self, auth=None):
        h = {
            "content-type": "application/json",
            "user-agent": "Mozilla/5.0",
            "origin": "https://lc79.to",
            "referer": "https://lc79.to/",
            "accept": "application/json, text/plain, */*",
            "connection": "close",
        }
        if auth:
            h["authorization"] = f"Bearer {auth}"
        return h

    @staticmethod
    def parse_jwt_exp(token):
        try:
            if "." not in token:
                return 0
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            return data.get("exp", 0)
        except Exception:
            return 0

    def is_token_valid(self):
        return self.jwt is not None and time.time() < (self.jwt_exp - 60)

    @staticmethod
    def extract_balance(data):
        if not isinstance(data, dict):
            return None
        for key in ("balance", "money", "amount", "wallet"):
            v = data.get(key)
            if isinstance(v, (int, float)):
                return v
        for nest in ("user", "data", "member", "account"):
            sub = data.get(nest)
            if isinstance(sub, dict):
                for key in ("balance", "money", "amount", "wallet"):
                    v = sub.get(key)
                    if isinstance(v, (int, float)):
                        return v
        return None

    def record_success(self, code, amount):
        entry = {
            "code": code,
            "amount": int(amount) if amount else 0,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with _history_lock:
            HISTORY.setdefault(self.nick, []).append(entry)
            save_history(HISTORY)

    def check_balance(self):
        if not self.is_token_valid():
            if not self.refresh_token():
                return None
        try:
            with requests.Session() as s:
                res = s.get(
                    BALANCE_URL,
                    headers=self._headers(self.jwt),
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
            if res.status_code == 200:
                bal = self.extract_balance(res.json())
                if bal is not None:
                    self.balance = bal
                    return bal
            return None
        except Exception:
            return None

    def get_access_token(self):
        try:
            with requests.Session() as s:
                res = s.get(API_GET_ACCESS, params={
                    "c": "3", "un": self.un, "pw": self.pw,
                    "cp": "R", "cl": "R", "pf": "web", "at": "",
                }, headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            data = res.json()
            if data.get("success"):
                return data.get("accessToken")
            print(f"[{self.nick}] ❌ get_access_token fail")
            return None
        except Exception as e:
            print(f"[{self.nick}] ❌ get_access_token: {e}")
            return None

    def refresh_token(self):
        access_token = self.get_access_token()
        if not access_token:
            return False
        try:
            with requests.Session() as s:
                res = s.post(
                    LOGIN_URL,
                    headers={
                        "content-type": "application/json",
                        "origin": "https://lc79b.bet",
                        "referer": "https://lc79b.bet/",
                        "user-agent": "Mozilla/5.0",
                        "connection": "close",
                    },
                    json={"nickName": self.nick, "accessToken": access_token},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
            data = res.json()
            new_token = data.get("token")
            if new_token:
                self.jwt = new_token
                self.jwt_exp = self.parse_jwt_exp(new_token)
                bal = self.extract_balance(data)
                if bal is not None:
                    self.balance = bal
                remain = int((self.jwt_exp - time.time()) / 60) if self.jwt_exp else 0
                print(f"[{self.nick}] ✅ JWT OK (còn {remain} phút) | 💵 {int(self.balance):,}")
                save_all_tokens()
                return True
            print(f"[{self.nick}] ❌ login fail")
            return False
        except Exception as e:
            print(f"[{self.nick}] ❌ refresh_token: {e}")
            return False

    def redeem_code(self, code, _attempt=0):
        with self.lock:
            if code in self.used_codes:
                return
            self.used_codes.add(code)

        if not self.is_token_valid():
            if not self.refresh_token():
                print(f"[{self.nick}] ❌ Không login được → bỏ qua {code}")
                return

        print(f"[{self.nick}] 🚀 Redeem: {code}")
        try:
            with requests.Session() as s:
                res = s.post(
                    BASE_URL + PARAMS,
                    headers=self._headers(self.jwt),
                    json={"code": code.strip(), "captcha": ""},
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                )
            try:
                data = res.json()
            except Exception:
                data = {}

            if res.status_code == 401:
                if _attempt < MAX_RETRY and self.refresh_token():
                    with self.lock:
                        self.used_codes.discard(code)
                    return self.redeem_code(code, _attempt=_attempt + 1)
                return

            if res.status_code in [200, 201]:
                amount = (
                    data.get("money")
                    or data.get("amount")
                    or data.get("reward")
                    or (data.get("data") or {}).get("money")
                )
                if amount:
                    self.balance += int(amount)
                    self.record_success(code, amount)
                    print(f"[{self.nick}] 💰 SUCCESS: +{int(amount):,} | 💵 {int(self.balance):,}")
                else:
                    self.record_success(code, 0)
                    print(f"[{self.nick}] ✅ SUCCESS")
                save_all_tokens()
            elif res.status_code == 400:
                msg = data.get("message") if isinstance(data, dict) else res.text
                print(f"[{self.nick}] ❌ FAIL (400): {msg}")
            elif res.status_code == 428:
                print(f"[{self.nick}] ⚠️ CAPTCHA")
            elif res.status_code == 429:
                print(f"[{self.nick}] ⚠️ RATE LIMIT")
            else:
                print(f"[{self.nick}] ❌ FAIL ({res.status_code})")
        except (requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            print(f"[{self.nick}] 🔌 TIMEOUT/CONN (lần {_attempt+1}): {e}")
            if _attempt < MAX_RETRY:
                time.sleep(1 + _attempt + random.uniform(0, 0.5))
                return self.redeem_code(code, _attempt=_attempt + 1)
        except Exception as e:
            print(f"[{self.nick}] ❌ ERROR: {e}")


ACCOUNT_OBJS = [Account(a["un"], a["pw"], a["nick"]) for a in ACCOUNTS_CONFIG]

# ═══════════════════════════════════════════════════════════════════
# TOKEN FILE
# ═══════════════════════════════════════════════════════════════════
_token_lock = threading.Lock()


def save_all_tokens():
    lines = [
        "# tokens.txt - " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "# nick | username | balance | jwt_exp | jwt",
    ]
    for a in ACCOUNT_OBJS:
        if a.jwt:
            exp_str = (
                datetime.fromtimestamp(a.jwt_exp).strftime("%Y-%m-%d %H:%M:%S")
                if a.jwt_exp else "?"
            )
            lines.append(f"{a.nick} | {a.un} | {int(a.balance)} | {exp_str} | {a.jwt}")
    with _token_lock:
        try:
            TOKEN_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:
            print(f"⚠️ save_tokens: {e}")

# ═══════════════════════════════════════════════════════════════════
# LOGIN / REDEEM
# ═══════════════════════════════════════════════════════════════════
def refresh_all():
    results = {}

    def worker(acc):
        results[acc.nick] = acc.refresh_token()

    threads = [threading.Thread(target=worker, args=(a,)) for a in ACCOUNT_OBJS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sum(1 for v in results.values() if v)


def redeem_all(code):
    print(f"\n🎯 CODE: {code} → {len(ACCOUNT_OBJS)} acc")
    threads = []
    for i, a in enumerate(ACCOUNT_OBJS):
        def worker(acc=a, idx=i):
            if idx > 0:
                time.sleep(idx * STAGGER_SEC + random.uniform(0, STAGGER_JITTER))
            acc.redeem_code(code)
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    save_all_tokens()

# ═══════════════════════════════════════════════════════════════════
# HEALTH SERVER (giữ Render free tier không sleep)
# ═══════════════════════════════════════════════════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - autoreedem-bot running")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"🌐 Health server: 0.0.0.0:{port}")
    server.serve_forever()


threading.Thread(target=start_health_server, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════
client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID, API_HASH,
    connection_retries=5,
    retry_delay=10,
    auto_reconnect=True,
    request_retries=5,
    flood_sleep_threshold=60,
)


async def resolve_chat_link():
    global TARGET_CHAT_ID
    username = TARGET_CHAT_LINK.rstrip("/").split("/")[-1].lstrip("@")
    print(f"🔍 Resolve @{username}...")
    try:
        entity = await client.get_entity(username)
        TARGET_CHAT_ID = utils.get_peer_id(entity)
        print(f"✅ ID: {TARGET_CHAT_ID}")
    except Exception as e:
        print(f"❌ Resolve fail: {e}")


@client.on(events.NewMessage)
async def handler(event):
    if TARGET_CHAT_ID is None or event.chat_id != TARGET_CHAT_ID:
        return
    text = event.raw_text or ""
    match = re.search(r"Mã code là:\s*([A-Z0-9]+)", text)
    if match:
        full_code = match.group(1)
        candidates = [full_code]
        if full_code.endswith("LC79"):
            candidates.append(full_code[:-4])
        for c in candidates:
            redeem_all(c)

# ═══════════════════════════════════════════════════════════════════
# SHUTDOWN
# ═══════════════════════════════════════════════════════════════════
def _shutdown(sig, frame):
    print(f"\n⏹️ Signal {sig} — đang tắt...")
    try:
        save_all_tokens()
        save_history(HISTORY)
        client.disconnect()
    except Exception:
        pass
    sys.exit(0)


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
print(f"\n🚀 Render | {len(ACCOUNT_OBJS)} acc | API ID {API_ID}")
print(f"📁 DATA_DIR = {DATA_DIR.absolute()}")

ok = refresh_all()
if ok == 0:
    print("❌ Không acc nào login được → thoát.")
    sys.exit(1)
print(f"✅ {ok}/{len(ACCOUNT_OBJS)} acc login OK")
save_all_tokens()

client.start()
client.loop.run_until_complete(resolve_chat_link())
print("🚀 Bot listening...\n")
client.run_until_disconnected()