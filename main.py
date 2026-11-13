import telebot
import requests
import hashlib
import base64
import json
import logging
import threading
import random
import time
import os
import re
import io
import html
import asyncio
from flask import Flask
from datetime import datetime, timedelta
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession

# ========================= FLASK KEEPALIVE =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "✨ LỤM CODE LC79 - ONLINE ✨"

@app.route("/health")
def health():
    return {"status": "online", "bot": "Lum code LC79"}

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ========================= CONFIG & LOGGING =========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('telethon').setLevel(logging.WARNING)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
SUPPORT_HANDLE = "@htungdzvipmax"
TELETHON_API_ID = int(os.environ.get("TELETHON_API_ID", "0"))
TELETHON_API_HASH = os.environ.get("TELETHON_API_HASH", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

bot = None
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    logger.error("❌ LỖI: BOT_TOKEN chưa có hoặc sai định dạng.")
else:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        bot.set_my_commands([
            telebot.types.BotCommand("start", "✨ Mở bảng điều khiển Lụm code LC79"),
            telebot.types.BotCommand("menu", "🏠 Quay về màn hình chính")
        ])
        logger.info("✅ Kết nối Telegram Bot OK!")
    except Exception as e:
        logger.error(f"❌ LỖI TOKEN TELEGRAM: {e}")
        bot = None

if not bot:
    class DummyBot:
        def __getattr__(self, name):
            return lambda *a, **kw: None
    bot = DummyBot()

valid_keys = {}
authorized_users = {}

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
    """Ghi JSON an toàn qua file tạm."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        logger.exception(f"Save {path} failed: {e}")
        try:
            if os.path.exists(path + ".tmp"):
                os.remove(path + ".tmp")
        except Exception:
            pass
        return False

redeem_state = load_json(REDEEM_STATE_FILE, {
    "accounts": [],
    "channel_id": None,
    "channel_name": "",
    "enabled": False
})
redeem_logs = load_json(REDEEM_LOGS_FILE, [])
game_accounts = load_json(GAME_ACCOUNTS_FILE, {})

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

def check_auth(chat_id):
    if chat_id == ADMIN_ID:
        return True
    if chat_id in authorized_users:
        if time.time() <= authorized_users[chat_id]:
            return True
        else:
            del authorized_users[chat_id]
    return False

def format_expire_time(ts):
    r = ts - time.time()
    if r <= 0:
        return "🔴 ĐÃ HẾT HẠN"
    d = int(r // 86400)
    h = int((r % 86400) // 3600)
    m = int((r % 3600) // 60)
    exp_str = datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")
    if d > 0:
        return f"🟢 Còn {d}N {h}G {m}P ({exp_str})"
    return f"🟡 Còn {h}G {m}P ({exp_str})"

def generate_vip_key(length=10):
    """Sinh mã Key VIP 10 ký tự ngẫu nhiên gồm chữ in hoa và số."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choices(chars, k=length))

# ========================= REDEEM & BALANCE SYNC SYSTEM =========================
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

def sync_balance_to_storage(username, new_balance):
    """Đồng bộ số dư mới nhất ngay lập tức vào redeem_state.json và game_accounts.json."""
    updated_state = False
    for item in redeem_state.get("accounts", []):
        if str(item.get("un", "")).lower() == str(username).lower():
            item["balance"] = int(new_balance)
            updated_state = True
    if updated_state:
        save_json(REDEEM_STATE_FILE, redeem_state)

    updated_game = False
    for k, info in game_accounts.items():
        if str(info.get("username", "")).lower() == str(username).lower():
            info["money"] = int(new_balance)
            updated_game = True
    if updated_game:
        save_json(GAME_ACCOUNTS_FILE, game_accounts)

class RedeemAccount:
    CONNECT_TIMEOUT = 3
    READ_TIMEOUT = 15
    API_GET_ACCESS = "https://apifo88daigia.tele68.com/api"
    LOGIN_URL = "https://wlb.tele68.com/v1/lobby/auth/login"
    BASE_URL = "https://gameapi.tele68.com"
    PARAMS = "/v1/giftcode/redemption?cp=R&cl=R&pf=web&at="

    def __init__(self, un, pw, nick, owner=None, balance=0):
        self.un = un
        self.pw = pw
        self.nick = nick
        self.owner = owner
        self.jwt = None
        self.jwt_exp = 0
        self.balance = int(balance or 0)
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=15, pool_maxsize=15)
        self.session.mount("https://", adapter)

    def _headers(self, auth=None):
        h = {
            "content-type": "application/json",
            "user-agent": "Mozilla/5.0",
            "origin": "https://lc79.to",
            "referer": "https://lc79.to/",
            "accept": "application/json, text/plain, */*",
            "connection": "keep-alive"
        }
        if auth:
            h["authorization"] = f"Bearer {auth}"
        return h

    @staticmethod
    def parse_jwt_exp(token):
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
        except Exception:
            return 0

    def is_token_valid(self):
        return self.jwt is not None and time.time() < (self.jwt_exp - 60)

    def get_access_token(self):
        try:
            res = self.session.get(
                self.API_GET_ACCESS,
                params={"c": "3", "un": self.un, "pw": self.pw, "cp": "R", "cl": "R", "pf": "web", "at": ""},
                headers=self._headers(),
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
            )
            data = res.json()
            return data.get("accessToken") if data.get("success") else None
        except Exception as e:
            logger.error(f"[{self.nick}] get_access_token: {e}")
            return None

    def refresh_token(self):
        at = self.get_access_token()
        if not at:
            return False
        try:
            res = self.session.post(
                self.LOGIN_URL,
                headers={
                    "content-type": "application/json",
                    "origin": "https://lc79b.bet",
                    "referer": "https://lc79b.bet/",
                    "user-agent": "Mozilla/5.0",
                    "connection": "keep-alive"
                },
                json={"nickName": self.nick, "accessToken": at},
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
            )
            data = res.json()
            new_tok = data.get("token")
            if new_tok:
                self.jwt = new_tok
                self.jwt_exp = self.parse_jwt_exp(new_tok)
                new_bal = int(data.get("remoteLoginResp", {}).get("money", self.balance) or self.balance)
                self.balance = new_bal
                sync_balance_to_storage(self.un, new_bal)
                return True
            return False
        except Exception as e:
            logger.error(f"[{self.nick}] refresh: {e}")
            return False

    def _single_redeem_request(self, code):
        try:
            res = self.session.post(
                self.BASE_URL + self.PARAMS,
                headers=self._headers(self.jwt),
                json={"code": code.strip(), "captcha": ""},
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)
            )
            try:
                data = res.json()
            except Exception:
                data = {}
            if res.status_code in (200, 201):
                amount = (
                    data.get("money")
                    or data.get("amount")
                    or data.get("reward")
                    or (data.get("data") or {}).get("money")
                    or 0
                )
                try:
                    amount = int(amount)
                except Exception:
                    amount = 0
                return "SUCCESS", amount
            elif res.status_code == 401:
                return "TOKEN EXPIRED", 0
            else:
                err = data.get("message", f"HTTP {res.status_code}") if isinstance(data, dict) else f"HTTP {res.status_code}"
                return f"FAIL: {err}", 0
        except Exception:
            return "TIMEOUT/NETWORK", 0

    def redeem_code_spam_10(self, code):
        """Bắn đồng thời 10 request cho 1 tài khoản và cập nhật số dư ngay khi trúng."""
        if not self.is_token_valid():
            if not self.refresh_token():
                append_redeem_log(code, self.nick, self.balance, self.balance, 0, "LOGIN FAIL")
                return None

        bal_before = self.balance
        threads = []
        results = []
        res_lock = threading.Lock()

        def worker():
            st, amt = self._single_redeem_request(code)
            with res_lock:
                results.append((st, amt))

        for _ in range(10):
            t = threading.Thread(target=worker, daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=15)

        success_runs = [r for r in results if r[0] == "SUCCESS"]
        if success_runs:
            self.refresh_token()
            bal_after = self.balance
            diff = max(0, bal_after - bal_before)
            total_amt = sum(r[1] for r in success_runs) or diff

            sync_balance_to_storage(self.un, bal_after)
            append_redeem_log(code, self.nick, bal_before, bal_after, total_amt, "SUCCESS")
            return {
                "nick": self.nick,
                "un": self.un,
                "owner": self.owner,
                "bal_before": bal_before,
                "bal_after": bal_after,
                "amount": total_amt
            }
        else:
            if any(r[0] == "TOKEN EXPIRED" for r in results):
                self.refresh_token()
            last_st = results[0][0] if results else "UNKNOWN"
            append_redeem_log(code, self.nick, bal_before, self.balance, 0, last_st)
            return None

def build_redeem_accounts():
    accounts = redeem_state.get("accounts", []) if isinstance(redeem_state, dict) else []
    result = []
    for item in accounts:
        if not isinstance(item, dict):
            continue
        un = str(item.get("un") or item.get("username") or "").strip()
        pw = str(item.get("pw") or item.get("password") or "").strip()
        nick = str(item.get("nick") or item.get("nickname") or un).strip()
        owner = item.get("owner")
        bal = item.get("balance", 0)
        if un and pw:
            result.append(RedeemAccount(un, pw, nick or un, owner=owner, balance=bal))
    return result

def sync_redeem_accounts():
    global redeem_state, REDEEM_ACCOUNTS
    disk_state = load_json(REDEEM_STATE_FILE, None)
    if isinstance(disk_state, dict):
        disk_state.setdefault("accounts", [])
        disk_state.setdefault("channel_id", None)
        disk_state.setdefault("channel_name", "")
        disk_state.setdefault("enabled", False)
        redeem_state = disk_state
    REDEEM_ACCOUNTS = build_redeem_accounts()
    return REDEEM_ACCOUNTS

REDEEM_ACCOUNTS = build_redeem_accounts()

def redeem_all_simultaneous(code):
    """Chạy đồng loạt tất cả tài khoản cùng lúc (mỗi tài khoản 10 luồng) không độ trễ."""
    if not REDEEM_ACCOUNTS:
        logger.warning("Chưa có acc redeem")
        return

    def worker():
        prep_threads = []
        for acc in REDEEM_ACCOUNTS:
            if not acc.is_token_valid():
                pt = threading.Thread(target=acc.refresh_token, daemon=True)
                pt.start()
                prep_threads.append(pt)
        for pt in prep_threads:
            pt.join(timeout=8)

        threads = []
        win_accounts = []
        win_lock = threading.Lock()

        def run_acc(account):
            win_info = account.redeem_code_spam_10(code)
            if win_info:
                with win_lock:
                    win_accounts.append(win_info)

        for acc in REDEEM_ACCOUNTS:
            t = threading.Thread(target=run_acc, args=(acc,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=60)

        ok = len(win_accounts)
        total = sum(w["amount"] for w in win_accounts)
        detail_lines = [
            f"• <b>{html.escape(w['nick'])}</b> (<code>{html.escape(w['un'])}</code>): {w['bal_before']:,} ➔ <b>{w['bal_after']:,}</b> (+{w['amount']:,})"
            for w in win_accounts
        ]
        detail_text = ("\n\n📋 <b>Chi tiết acc lụm thành công:</b>\n" + "\n".join(detail_lines)) if detail_lines else ""

        try:
            bot.send_message(
                ADMIN_ID,
                f"🎁 <b>KẾT QUẢ LỤM CODE:</b> <code>{html.escape(code)}</code>\n"
                f"✅ Thành công: <b>{ok}/{len(REDEEM_ACCOUNTS)}</b> acc\n"
                f"💰 Tổng cộng thêm: <b>{total:,}</b>{detail_text}",
                parse_mode="HTML"
            )
        except Exception:
            pass

        for w in win_accounts:
            if w.get("owner") and w["owner"] != ADMIN_ID:
                try:
                    bot.send_message(
                        w["owner"],
                        f"🎉 <b>CHÚC MỪNG! ACC CỦA BẠN ĐÃ LỤM ĐƯỢC CODE</b>\n"
                        f"📝 Mã code: <code>{html.escape(code)}</code>\n"
                        f"👤 Tài khoản: <b>{html.escape(w['nick'])}</b> (<code>{html.escape(w['un'])}</code>)\n"
                        f"💰 Số dư mới: {w['bal_before']:,} ➔ <b>{w['bal_after']:,}</b> (+{w['amount']:,})",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

    threading.Thread(target=worker, daemon=True).start()

# ========================= LOGIN & LẤY NICKNAME TỰ ĐỘNG =========================
def login_and_get_info(u, p):
    try:
        pw_hashed = normalize_pw(p)
        r = requests.get(
            f"https://apifo88daigia.tele68.com/api?c=3&un={u}&pw={pw_hashed}&cp=R&cl=R&pf=web&at=",
            timeout=10
        ).json()
        if not r.get("success"):
            return {"_error": "Sai Tài Khoản hoặc Mật Khẩu"}
        sk = r["sessionKey"] + "=" * ((4 - len(r["sessionKey"]) % 4) % 4)
        nn = json.loads(base64.b64decode(sk)).get("nickname")
        r2 = requests.post(
            "https://wlb.tele68.com/v1/lobby/auth/login?cp=R&cl=R&pf=web&at=",
            json={"nickName": nn, "accessToken": r["accessToken"]},
            timeout=10
        ).json()
        if not r2.get("token"):
            return {"_error": "Không nhận được token (Acc đang kẹt phiên?)"}
        return {
            "token": r2["token"],
            "nickname": nn,
            "money": int(r2.get("remoteLoginResp", {}).get("money", 0) or 0),
            "pw_hashed": pw_hashed
        }
    except Exception as e:
        return {"_error": str(e)}

def save_game_account(owner_id, username, nickname, token, money, source="user"):
    key = f"{owner_id}_{username.lower()}"
    game_accounts[key] = {
        "owner": owner_id,
        "username": username,
        "nickname": nickname,
        "token": token,
        "money": int(money or 0),
        "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source
    }
    save_json(GAME_ACCOUNTS_FILE, game_accounts)

def upsert_redeem_account(un, pw_hashed, nick, owner_id, balance=0):
    global REDEEM_ACCOUNTS
    redeem_state.setdefault("accounts", [])
    updated = False
    for acc in redeem_state["accounts"]:
        if acc.get("un", "").lower() == un.lower():
            acc["pw"] = pw_hashed
            acc["nick"] = nick
            acc["owner"] = owner_id
            acc["balance"] = int(balance or 0)
            updated = True
            break
    if not updated:
        redeem_state["accounts"].append({
            "un": un,
            "pw": pw_hashed,
            "nick": nick,
            "owner": owner_id,
            "balance": int(balance or 0)
        })
    save_json(REDEEM_STATE_FILE, redeem_state)
    REDEEM_ACCOUNTS = build_redeem_accounts()

# ========================= TELETHON & TRÍCH XUẤT CODE =========================
tele_client = None
tele_loop = None
tele_ready = False
tele_login_pending = {}

def _load_tele_session():
    if os.path.exists(TELE_SESSION_FILE):
        try:
            with open(TELE_SESSION_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return os.environ.get("TELETHON_SESSION", "").strip()

def _save_tele_session(s):
    with open(TELE_SESSION_FILE, "w", encoding="utf-8") as f:
        f.write(s)

def extract_clean_code(raw_text):
    """Tự động cắt bỏ tiền tố LC79 ở đầu mã code (ví dụ LC79CJBKQLAQI -> CJBKQLAQI)."""
    if not raw_text:
        return None
    match = re.search(r"Mã\s*code\s*là\s*:\s*([A-Z0-9]+)", raw_text, re.IGNORECASE)
    if not match:
        return None
    code = match.group(1).strip().upper()
    if code.startswith("LC79") and len(code) > 4:
        code = code[4:]
    return code

def _activate_telethon_client(client, loop):
    global tele_client, tele_loop, tele_ready
    tele_client, tele_loop = client, loop

    @client.on(events.NewMessage())
    async def _on_msg(event):
        _handle_tele_message(event)

    async def _keep_running():
        global tele_ready
        try:
            if not await client.is_user_authorized():
                tele_ready = False
                return
            me = await client.get_me()
            tele_ready = True
            session_str = client.session.save()
            full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "N/A"
            uname = f"@{me.username}" if me.username else "Không có"
            phone = f"+{me.phone}" if getattr(me, "phone", None) else "Ẩn"
            try:
                bot.send_message(
                    ADMIN_ID,
                    f"✨ <b>TELE NGUỒN ĐÃ KẾT NỐI THÀNH CÔNG</b> ✨\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Tên Tele:</b> {html.escape(full_name)}\n"
                    f"🆔 <b>ID Tele:</b> <code>{me.id}</code>\n"
                    f"🔗 <b>Username:</b> {html.escape(uname)}\n"
                    f"📱 <b>SĐT:</b> <code>{html.escape(phone)}</code>\n"
                    f"🟢 <b>Trạng thái:</b> Đang trực canh kênh code\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔑 <b>Session String:</b>\n<code>{html.escape(session_str)}</code>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await client.run_until_disconnected()
        except Exception as exc:
            tele_ready = False
            logger.exception("Tele listener lỗi: %s", exc)

    loop.run_until_complete(_keep_running())

def _handle_tele_message(event):
    if not redeem_state.get("enabled") or redeem_state.get("channel_id") is None:
        return
    if event.chat_id != redeem_state["channel_id"]:
        return
    clean_code = extract_clean_code(event.raw_text or "")
    if not clean_code:
        return
    logger.info(f"[TELE] Bắt được code sạch: {clean_code}")
    try:
        bot.send_message(
            ADMIN_ID,
            f"⚡ <b>PHÁT HIỆN CODE MỚI!</b>\n"
            f"🎁 Mã sạch: <code>{clean_code}</code>\n"
            f"🚀 Kích hoạt đồng loạt <b>{len(REDEEM_ACCOUNTS)}</b> acc (10 luồng/acc)...",
            parse_mode="HTML"
        )
    except Exception:
        pass
    redeem_all_simultaneous(clean_code)

def _start_telethon_listener():
    global tele_client, tele_loop, tele_ready
    if TELETHON_API_ID == 0 or not TELETHON_API_HASH:
        return
    session_str = _load_tele_session()
    if not session_str:
        return
    tele_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(tele_loop)
    try:
        tele_client = TelegramClient(StringSession(session_str), TELETHON_API_ID, TELETHON_API_HASH, loop=tele_loop)
    except Exception as e:
        logger.error(f"Tele init: {e}")
        return

    @tele_client.on(events.NewMessage())
    async def _on_msg(event):
        _handle_tele_message(event)

    async def _run():
        global tele_ready
        try:
            await tele_client.connect()
            if not await tele_client.is_user_authorized():
                return
            tele_ready = True
            await tele_client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Tele run: {e}")

    tele_loop.run_until_complete(_run())

def tele_resolve_entity(target, timeout=15):
    if tele_client is None or not tele_ready:
        raise RuntimeError("Tele nguồn chưa đăng nhập hoặc chưa online")
    f = asyncio.run_coroutine_threadsafe(tele_client.get_entity(target), tele_loop)
    return f.result(timeout=timeout)

def get_tele_account_details():
    session_str = _load_tele_session()
    if not session_str:
        return None, None
    if tele_client and tele_ready and tele_loop:
        try:
            fut = asyncio.run_coroutine_threadsafe(tele_client.get_me(), tele_loop)
            me = fut.result(timeout=8)
            return me, session_str
        except Exception:
            pass
    return None, session_str

# ========================= UI MENU PHÂN QUYỀN & GIAO DIỆN ĐẸP =========================
BOT_TITLE = "✨ 𝐋ụ𝐦 𝐜𝐨𝐝𝐞 𝐋𝐂𝟕𝟗 ✨"
user_flows = {}
SCREEN_MESSAGES = {}

def _btn(text, data):
    return telebot.types.InlineKeyboardButton(text, callback_data=data)

def build_home_header(user_obj, chat_id):
    first = getattr(user_obj, "first_name", "") or ""
    last = getattr(user_obj, "last_name", "") or ""
    full_name = f"{first} {last}".strip() or "Người dùng"
    uname = f"@{user_obj.username}" if getattr(user_obj, "username", None) else "Không có"

    if chat_id == ADMIN_ID:
        han_key = "👑 ADMIN TOÀN QUYỀN (Vĩnh viễn)"
        role_badge = "🛡️ Quản Trị Viên Hệ Thống"
    elif check_auth(chat_id):
        han_key = format_expire_time(authorized_users[chat_id])
        role_badge = "💎 Thành Viên VIP"
    else:
        han_key = "🔒 Chưa kích hoạt Key"
        role_badge = "👤 Khách Trải Nghiệm"

    return (
        f"╔════════════════════════╗\n"
        f"     {BOT_TITLE}\n"
        f"╚════════════════════════╝\n"
        f"🏷️ <b>Vai trò:</b> {role_badge}\n"
        f"👤 <b>Tên Tele:</b> {html.escape(full_name)}\n"
        f"🔗 <b>Username:</b> {html.escape(uname)}\n"
        f"🆔 <b>ID User:</b> <code>{chat_id}</code>\n"
        f"⏳ <b>Hạn Key:</b> {han_key}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>BẢNG GIÁ DỊCH VỤ & MÃ NGUỒN:</b>\n"
        f"  • 🎟️ <b>Key VIP 2 Ngày:</b> <code>10.000đ</code>\n"
        f"  • 🎟️ <b>Key VIP 5 Ngày:</b> <code>20.000đ</code>\n"
        f"  • 💻 <b>Full Mã Nguồn Bot (All Src):</b> <code>30.000đ</code>\n"
        f"🛟 <b>Hỗ trợ & Mua Key/Src:</b> {SUPPORT_HANDLE}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 <i>Chọn chức năng bên dưới để thao tác:</i>"
    )

def replace_screen(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        try:
            msg = bot.edit_message_text(
                text, chat_id, message_id,
                parse_mode=parse_mode, reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            SCREEN_MESSAGES[chat_id] = message_id
            return msg
        except Exception:
            pass
    msg = bot.send_message(
        chat_id, text, parse_mode=parse_mode,
        reply_markup=reply_markup, disable_web_page_preview=True
    )
    SCREEN_MESSAGES[chat_id] = msg.message_id
    return msg

def flow_prompt(chat_id, message_id, flow, title, detail):
    user_flows[chat_id] = {"step": flow}
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(_btn("❌ Huỷ Thao Tác", "flow_cancel"))
    return replace_screen(
        chat_id, message_id,
        f"╔════════════════════════╗\n"
        f"   {title}\n"
        f"╚════════════════════════╝\n\n"
        f"{detail}\n\n"
        f"💬 <i>Vui lòng nhập và gửi trực tiếp vào khung chat...</i>",
        kb, "HTML"
    )

def main_menu(cid):
    """Menu chính phân tách rạch ròi: Admin thấy quản trị & auto hệ thống, User chỉ thao tác tài khoản của mình."""
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    if cid == ADMIN_ID:
        kb.add(_btn("⚡ AUTO REDEEM", "rd_panel"), _btn("🔐 LOGIN TK GAME", "rd_login_acc"))
        kb.add(_btn("📋 DANH SÁCH ACC", "rd_list"), _btn("➖ XOÁ TÀI KHOẢN", "rd_delacc"))
        kb.add(_btn("📡 TELE NGUỒN", "tele_panel"), _btn("🔑 TẠO KEY VIP", "key_panel"))
        kb.add(_btn("📄 XUẤT TOKEN", "export_tokens"), _btn("📊 XUẤT LỊCH SỬ", "export_history"))
        kb.add(_btn("💎 BẢNG GIÁ & SRC", "pricing_info"), _btn("🛟 HỖ TRỢ ADMIN", "support_info"))
    elif check_auth(cid):
        kb.add(_btn("🔐 LOGIN TK GAME", "user_login_acc"), _btn("📋 DS ACC CỦA TÔI", "user_my_accs"))
        kb.add(_btn("➖ XOÁ ACC CỦA TÔI", "user_del_acc"), _btn("🔑 NHẬP KEY MỚI", "nhapkey"))
        kb.add(_btn("💎 BẢNG GIÁ VIP", "pricing_info"), _btn("💻 MUA SOURCE BOT", "source_info"))
        kb.add(_btn("🛟 HỖ TRỢ 24/7", "support_info"), _btn("🔄 LÀM MỚI MENU", "home"))
    else:
        kb.add(_btn("🔑 KÍCH HOẠT KEY VIP", "nhapkey"), _btn("💎 BẢNG GIÁ DỊCH VỤ", "pricing_info"))
        kb.add(_btn("💻 MUA SOURCE BOT (30K)", "source_info"), _btn("🛟 LIÊN HỆ ADMIN", "support_info"))
        kb.add(_btn("🔄 LÀM MỚI", "home"))
    return kb

def redeem_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    toggle = "🟢 ĐANG BẬT (BẤM TẮT)" if redeem_state.get("enabled") else "🔴 ĐANG TẮT (BẤM BẬT)"
    kb.add(_btn(toggle, "rd_toggle"), _btn("📡 SET CHANNEL", "rd_setchannel"))
    kb.add(_btn("🔐 LOGIN TK (AUTO NICK)", "rd_login_acc"), _btn("📋 DANH SÁCH ACC", "rd_list"))
    kb.add(_btn("➖ XOÁ TÀI KHOẢN", "rd_delacc"), _btn("🔄 CẬP NHẬT SỐ DƯ", "rd_refresh"))
    kb.add(_btn("⬅️ QUAY LẠI MENU", "home"))
    return kb

def tele_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🔐 ĐĂNG NHẬP TELE", "tele_login"), _btn("🔑 XUẤT SESSION & INFO", "tele_export_session"))
    kb.add(_btn("📊 KIỂM TRA TRẠNG THÁI", "tele_status_btn"), _btn("🗑️ ĐĂNG XUẤT SESSION", "tele_logout_confirm"))
    kb.add(_btn("⬅️ QUAY LẠI MENU", "home"))
    return kb

def key_manage_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🎟️ TẠO KEY 2 NGÀY (10K)", "gen_key_2"), _btn("🎟️ TẠO KEY 5 NGÀY (20K)", "gen_key_5"))
    kb.add(_btn("✏️ NHẬP SỐ NGÀY KHÁC", "create_key_custom"), _btn("📋 DS KEY CHƯA DÙNG", "list_keys"))
    kb.add(_btn("⬅️ QUAY LẠI MENU", "home"))
    return kb

# ========================= XUẤT FILE TXT =========================
def send_tokens_txt(chat_id):
    if chat_id != ADMIN_ID:
        return
    sync_redeem_accounts()
    if not REDEEM_ACCOUNTS and not game_accounts:
        return bot.send_message(chat_id, "📭 Chưa có tài khoản hoặc token nào để xuất.")

    lines = [
        "========================================================================",
        "              LỤM CODE LC79 - DANH SÁCH TOKEN TÀI KHOẢN",
        "========================================================================",
        f"Thời gian xuất : {datetime.now():%d/%m/%Y %H:%M:%S}",
        f"Tổng tài khoản : {len(REDEEM_ACCOUNTS)}",
        "========================================================================\n"
    ]
    token_map = {str(v.get("username", "")).lower(): v.get("token", "") for v in game_accounts.values()}

    for i, acc in enumerate(REDEEM_ACCOUNTS, 1):
        if not acc.is_token_valid():
            acc.refresh_token()
        tok = acc.jwt or token_map.get(acc.un.lower(), "CHƯA CÓ TOKEN")
        lines.append(
            f"[{i}] Nickname: {acc.nick} | Username: {acc.un} | Số dư: {acc.balance:,}\n"
            f"Token: {tok}\n"
            + "-" * 72
        )

    bio = io.BytesIO("\n".join(lines).encode("utf-8"))
    bio.name = f"LumCodeLC79_Tokens_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(
        chat_id, bio,
        caption=f"📄 <b>XUẤT TOKEN THÀNH CÔNG</b>\n👥 Tổng cộng: <b>{len(REDEEM_ACCOUNTS)}</b> tài khoản",
        parse_mode="HTML"
    )

def send_full_history_txt(chat_id):
    if chat_id != ADMIN_ID:
        return
    if not redeem_logs:
        return bot.send_message(chat_id, "📭 Chưa có dữ liệu lịch sử redeem nào.")

    ok_count = sum(1 for x in redeem_logs if x.get("status") == "SUCCESS")
    fail_count = len(redeem_logs) - ok_count
    total_money = sum(int(x.get("amount", 0) or 0) for x in redeem_logs if x.get("status") == "SUCCESS")

    lines = [
        "========================================================================",
        "             LỤM CODE LC79 - LỊCH SỬ REDEEM FULL THÔNG TIN",
        "========================================================================",
        f"Thời gian xuất  : {datetime.now():%d/%m/%Y %H:%M:%S}",
        f"Tổng lượt chạy  : {len(redeem_logs)}",
        f"Thành công      : {ok_count}",
        f"Thất bại        : {fail_count}",
        f"Tổng tiền nhận  : {total_money:,} VNĐ",
        "========================================================================\n"
    ]
    for i, item in enumerate(redeem_logs, 1):
        lines.append(
            f"[{i}] Thời gian   : {item.get('time', '')}\n"
            f"    Mã Code     : {item.get('code', '')}\n"
            f"    Tài khoản   : {item.get('account', '')}\n"
            f"    Số dư trước : {int(item.get('balance_before', 0) or 0):,}\n"
            f"    Số dư sau   : {int(item.get('balance_after', 0) or 0):,}\n"
            f"    Tiền nhận   : +{int(item.get('amount', 0) or 0):,}\n"
            f"    Trạng thái  : {item.get('status', '')}\n"
            + "-" * 72
        )

    bio = io.BytesIO("\n".join(lines).encode("utf-8"))
    bio.name = f"LumCodeLC79_LichSuFull_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(
        chat_id, bio,
        caption=(
            f"📊 <b>XUẤT LỊCH SỬ REDEEM FULL</b>\n"
            f"📝 Tổng lượt: <b>{len(redeem_logs)}</b> | ✅ OK: <b>{ok_count}</b>\n"
            f"💰 Tổng tiền nhận: <b>{total_money:,}</b>"
        ),
        parse_mode="HTML"
    )

# ========================= HANDLERS CHÍNH =========================
@bot.message_handler(commands=['start', 'menu'])
def cmd_start(m):
    cid = m.chat.id
    user_flows.pop(cid, None)
    try:
        bot.delete_message(cid, m.message_id)
    except Exception:
        pass
    old_id = SCREEN_MESSAGES.get(cid)
    if old_id:
        try:
            bot.delete_message(cid, old_id)
        except Exception:
            pass
    header = build_home_header(m.from_user, cid)
    msg = bot.send_message(cid, header, parse_mode="HTML", reply_markup=main_menu(cid), disable_web_page_preview=True)
    SCREEN_MESSAGES[cid] = msg.message_id

@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    global REDEEM_ACCOUNTS
    cid = c.message.chat.id
    data = c.data
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass

    def go(text, kb=None, mode="HTML"):
        return replace_screen(cid, c.message.message_id, text, kb, mode)

    if data in ("home", "flow_cancel"):
        user_flows.pop(cid, None)
        return go(build_home_header(c.from_user, cid), main_menu(cid), "HTML")

    # --- BẢNG GIÁ, MÃ NGUỒN & HỖ TRỢ ---
    if data == "pricing_info":
        txt = (
            f"╔════════════════════════╗\n"
            f"   💎 <b>BẢNG GIÁ KEY & SOURCE</b>\n"
            f"╚════════════════════════╝\n\n"
            f"🎟️ <b>Gói Key VIP 2 Ngày:</b> <code>10.000đ</code>\n"
            f"🎟️ <b>Gói Key VIP 5 Ngày:</b> <code>20.000đ</code>\n"
            f"💻 <b>Full Mã Nguồn Bot (All Src):</b> <code>30.000đ</code>\n\n"
            f"⚡ <i>Key dạng 10 ký tự ngẫu nhiên, kích hoạt tự động 24/7.</i>\n"
            f"📩 <b>Liên hệ Admin để mua Key / Source:</b> {SUPPORT_HANDLE}"
        )
        return go(txt, main_menu(cid))

    if data == "source_info":
        txt = (
            f"╔════════════════════════╗\n"
            f"   💻 <b>MÃ NGUỒN BOT (ALL SRC)</b>\n"
            f"╚════════════════════════╝\n\n"
            f"💰 <b>Giá trọn bộ Full Source:</b> <code>30.000đ</code>\n"
            f"✨ <b>Tính năng bao gồm:</b>\n"
            f"  • Full source code Python tối ưu đa luồng\n"
            f"  • Auto bắt code Tele & cắt tiền tố LC79 thông minh\n"
            f"  • Bắn đồng loạt nhiều acc (10 luồng/acc) không trễ\n"
            f"  • Hệ thống Key VIP 10 ký tự & UI chuyên nghiệp\n\n"
            f"📩 <b>Liên hệ Admin ngay để sở hữu:</b> {SUPPORT_HANDLE}"
        )
        return go(txt, main_menu(cid))

    if data == "support_info":
        txt = (
            f"╔════════════════════════╗\n"
            f"   🛟 <b>TRUNG TÂM HỖ TRỢ</b>\n"
            f"╚════════════════════════╝\n\n"
            f"👨‍💻 <b>Admin hỗ trợ trực tiếp:</b> {SUPPORT_HANDLE}\n"
            f"📌 Hỗ trợ mua Key (10k/2N - 20k/5N), mua Full Source Bot (30k) và xử lý lỗi 24/7."
        )
        return go(txt, main_menu(cid))

    if data == "nhapkey":
        return flow_prompt(
            cid, c.message.message_id, "key",
            "🔑 <b>KÍCH HOẠT KEY VIP</b>",
            f"Gửi mã Key VIP (10 ký tự) của bạn vào đây.\nChưa có key? Liên hệ {SUPPORT_HANDLE}"
        )

    # --- CHỨC NĂNG DÀNH RIÊNG CHO USER ---
    if data == "user_login_acc":
        if not check_auth(cid):
            return go("🔒 <b>Bạn chưa kích hoạt Key VIP!</b>\nVui lòng nhập Key để sử dụng.", main_menu(cid))
        return flow_prompt(
            cid, c.message.message_id, "user_login_game",
            "🔐 <b>LOGIN TÀI KHOẢN GAME</b>",
            "Gửi theo mẫu: <code>TÀI_KHOẢN MẬT_KHẨU</code>\n"
            "✨ <i>Hệ thống tự động lấy Nickname & đưa vào danh sách săn code của Admin.</i>"
        )

    if data == "user_my_accs":
        if not check_auth(cid):
            return go("🔒 <b>Bạn chưa kích hoạt Key VIP!</b>", main_menu(cid))
        sync_redeem_accounts()
        my_accs = [a for a in REDEEM_ACCOUNTS if a.owner == cid]
        if not my_accs:
            return go("📭 <b>Bạn chưa thêm tài khoản game nào.</b>\nBấm <b>Login TK Game</b> để thêm ngay!", main_menu(cid))
        lines = [
            f"<b>{i}.</b> 🟢 <b>{html.escape(a.nick)}</b> (<code>{html.escape(a.un)}</code>)\n    💰 Số dư hiện tại: <b>{a.balance:,}</b>"
            for i, a in enumerate(my_accs, 1)
        ]
        return go("📋 <b>DANH SÁCH TÀI KHOẢN CỦA BẠN</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines), main_menu(cid))

    if data == "user_del_acc":
        if not check_auth(cid):
            return go("🔒 <b>Bạn chưa kích hoạt Key VIP!</b>", main_menu(cid))
        sync_redeem_accounts()
        my_accs = [a for a in REDEEM_ACCOUNTS if a.owner == cid]
        if not my_accs:
            return go("📭 <b>Bạn không có tài khoản nào để xoá.</b>", main_menu(cid))
        accs_preview = "\n".join([f"• <code>{html.escape(a.un)}</code> ({html.escape(a.nick)})" for a in my_accs])
        return flow_prompt(
            cid, c.message.message_id, "user_del_redeem",
            "➖ <b>XOÁ TÀI KHOẢN CỦA BẠN</b>",
            f"Danh sách tài khoản của bạn:\n{accs_preview}\n\nGửi chính xác <b>Username</b> hoặc <b>Nickname</b> muốn xoá."
        )

    # --- CHỨC NĂNG DÀNH RIÊNG CHO ADMIN ---
    if cid != ADMIN_ID:
        return go("⛔ <b>Chức năng này chỉ dành cho Quản Trị Viên!</b>", main_menu(cid))

    if data == "rd_panel":
        sync_redeem_accounts()
        status_str = "🟢 ĐANG BẬT" if redeem_state.get("enabled") else "🔴 ĐANG TẮT"
        ch_name = html.escape(redeem_state.get("channel_name") or "Chưa thiết lập")
        txt = (
            f"╔════════════════════════╗\n"
            f"   ⚡ <b>BẢNG ĐIỀU KHIỂN AUTO REDEEM</b>\n"
            f"╚════════════════════════╝\n"
            f"🔔 <b>Trạng thái:</b> {status_str}\n"
            f"📡 <b>Kênh nguồn:</b> {ch_name}\n"
            f"👥 <b>Tổng Acc sẵn sàng:</b> <b>{len(REDEEM_ACCOUNTS)}</b> acc\n"
            f"⚡ <b>Chế độ bắn:</b> Đồng loạt toàn bộ acc (10 luồng/acc)"
        )
        return go(txt, redeem_menu())

    if data == "rd_toggle":
        sync_redeem_accounts()
        if redeem_state.get("enabled"):
            redeem_state["enabled"] = False
        elif not REDEEM_ACCOUNTS:
            return go("⚠️ <b>Chưa có tài khoản nào!</b> Hãy bấm <b>Login TK</b> trước.", redeem_menu())
        elif redeem_state.get("channel_id") is None:
            return go("⚠️ <b>Chưa đặt kênh nhận code!</b> Hãy bấm <b>Set Channel</b> trước.", redeem_menu())
        else:
            redeem_state["enabled"] = True
        save_json(REDEEM_STATE_FILE, redeem_state)
        status_str = "🟢 ĐANG BẬT" if redeem_state.get("enabled") else "🔴 ĐANG TẮT"
        ch_name = html.escape(redeem_state.get("channel_name") or "Chưa thiết lập")
        txt = (
            f"╔════════════════════════╗\n"
            f"   ⚡ <b>BẢNG ĐIỀU KHIỂN AUTO REDEEM</b>\n"
            f"╚════════════════════════╝\n"
            f"🔔 <b>Trạng thái:</b> {status_str}\n"
            f"📡 <b>Kênh nguồn:</b> {ch_name}\n"
            f"👥 <b>Tổng Acc sẵn sàng:</b> <b>{len(REDEEM_ACCOUNTS)}</b> acc\n"
            f"⚡ <b>Chế độ bắn:</b> Đồng loạt toàn bộ acc (10 luồng/acc)"
        )
        return go(txt, redeem_menu())

    if data == "rd_setchannel":
        return flow_prompt(
            cid, c.message.message_id, "set_channel",
            "📡 <b>THIẾT LẬP KÊNH NHẬN CODE</b>",
            "Gửi <code>@username</code>, link <code>t.me/...</code> hoặc <code>ID kênh</code>."
        )

    if data == "rd_login_acc":
        return flow_prompt(
            cid, c.message.message_id, "admin_login_game",
            "🔐 <b>LOGIN TK (TỰ ĐỘNG LẤY NICKNAME)</b>",
            "Gửi theo mẫu: <code>TÀI_KHOẢN MẬT_KHẨU</code>\n"
            "✨ <i>Không cần nhập Nickname, hệ thống tự đăng nhập lấy tên nhân vật và cập nhật số dư thực tế.</i>"
        )

    if data == "rd_delacc":
        sync_redeem_accounts()
        if not REDEEM_ACCOUNTS:
            return go("📭 Danh sách tài khoản đang trống.", redeem_menu())
        return flow_prompt(
            cid, c.message.message_id, "admin_del_redeem",
            "➖ <b>XOÁ TÀI KHOẢN REDEEM</b>",
            "Gửi chính xác <b>Nickname</b> hoặc <b>Username</b> cần xoá khỏi danh sách."
        )

    if data == "rd_list":
        sync_redeem_accounts()
        if not REDEEM_ACCOUNTS:
            return go("📭 <b>Chưa có tài khoản nào trong danh sách.</b>", redeem_menu())
        lines = [
            f"<b>{i}.</b> {'🟢' if a.is_token_valid() else '🟡'} <b>{html.escape(a.nick)}</b> (<code>{html.escape(a.un)}</code>) • 💰 <b>{a.balance:,}</b>"
            for i, a in enumerate(REDEEM_ACCOUNTS, 1)
        ]
        return go("📋 <b>DANH SÁCH TÀI KHOẢN & SỐ DƯ</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines), redeem_menu())

    if data == "rd_refresh":
        sync_redeem_accounts()
        if not REDEEM_ACCOUNTS:
            return go("📭 Không có tài khoản nào để cập nhật.", redeem_menu())
        go("🔄 <b>Đang làm mới Token & cập nhật số dư mới nhất...</b>", redeem_menu())

        def refresh_all_worker():
            ok = 0
            for acc in REDEEM_ACCOUNTS:
                try:
                    if acc.refresh_token():
                        ok += 1
                except Exception:
                    pass
            lines = [f"• <b>{html.escape(a.nick)}</b>: 💰 <b>{a.balance:,}</b>" for a in REDEEM_ACCOUNTS]
            try:
                bot.send_message(
                    cid,
                    f"✅ <b>ĐÃ CẬP NHẬT SỐ DƯ ({ok}/{len(REDEEM_ACCOUNTS)} ACC)</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines),
                    parse_mode="HTML",
                    reply_markup=redeem_menu()
                )
            except Exception:
                pass

        threading.Thread(target=refresh_all_worker, daemon=True).start()
        return

    # --- XUẤT TXT ---
    if data == "export_tokens":
        send_tokens_txt(cid)
        return

    if data == "export_history":
        send_full_history_txt(cid)
        return

    # --- QUẢN LÝ KEY ---
    if data == "key_panel":
        return go(
            f"╔════════════════════════╗\n"
            f"   🔑 <b>QUẢN LÝ KEY VIP (10 KÝ TỰ)</b>\n"
            f"╚════════════════════════╝\n"
            f"📦 <b>Key đang chờ kích hoạt:</b> <b>{len(valid_keys)}</b> key\n"
            f"👥 <b>User đang hoạt động:</b> <b>{len(authorized_users)}</b> user\n\n"
            f"Chọn gói tạo nhanh bên dưới:",
            key_manage_menu()
        )

    if data in ("gen_key_2", "gen_key_5"):
        days = 2 if data == "gen_key_2" else 5
        price_label = "10.000đ" if days == 2 else "20.000đ"
        k = generate_vip_key(10)
        valid_keys[k] = days
        exp = (datetime.now() + timedelta(days=days)).strftime("%d/%m/%Y %H:%M")
        return go(
            f"✅ <b>ĐÃ TẠO KEY VIP THÀNH CÔNG</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>Mã Key:</b> <code>{k}</code>\n"
            f"⏳ <b>Thời hạn:</b> {days} Ngày ({price_label})\n"
            f"📅 <b>Hết hạn dự kiến:</b> {exp}",
            key_manage_menu()
        )

    if data == "create_key_custom":
        return flow_prompt(cid, c.message.message_id, "create_key", "✏️ <b>TẠO KEY TUỲ CHỈNH</b>", "Nhập số ngày sử dụng (ví dụ: <code>7</code> hoặc <code>30</code>).")

    if data == "list_keys":
        if not valid_keys:
            return go("📭 Hiện không có key nào chưa sử dụng.", key_manage_menu())
        ds = "\n".join([f"• <code>{k}</code> ➔ <b>{v} ngày</b>" for k, v in valid_keys.items()])
        return go("📋 <b>DANH SÁCH KEY CHƯA SỬ DỤNG</b>\n━━━━━━━━━━━━━━━━━━━━\n" + ds, key_manage_menu())

    # --- TELE NGUỒN ---
    if data == "tele_panel":
        me, session_str = get_tele_account_details()
        if me:
            full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "N/A"
            uname = f"@{me.username}" if me.username else "Không có"
            phone = f"+{me.phone}" if getattr(me, "phone", None) else "Ẩn"
            info_block = (
                f"🟢 <b>Trạng thái:</b> ONLINE & ĐANG THEO DÕI\n"
                f"👤 <b>Tên Tele:</b> {html.escape(full_name)}\n"
                f"🆔 <b>ID Tele:</b> <code>{me.id}</code>\n"
                f"🔗 <b>Username:</b> {html.escape(uname)}\n"
                f"📱 <b>SĐT:</b> <code>{html.escape(phone)}</code>"
            )
        elif session_str:
            info_block = "🟡 <b>Trạng thái:</b> Đã lưu Session (Đang chờ kết nối)"
        else:
            info_block = "🔴 <b>Trạng thái:</b> Chưa đăng nhập tài khoản Tele nguồn"

        ch_name = html.escape(redeem_state.get("channel_name") or "Chưa thiết lập")
        return go(
            f"╔════════════════════════╗\n"
            f"   📡 <b>QUẢN LÝ TELE NGUỒN</b>\n"
            f"╚════════════════════════╝\n"
            f"{info_block}\n"
            f"📢 <b>Kênh code đang theo dõi:</b> {ch_name}",
            tele_menu()
        )

    if data == "tele_login":
        return flow_prompt(cid, c.message.message_id, "tele_phone", "🔐 <b>ĐĂNG NHẬP TELE NGUỒN</b>", "Gửi số điện thoại kèm mã vùng quốc gia (ví dụ: <code>+849xxxxxxxx</code>).")

    if data == "tele_export_session":
        me, session_str = get_tele_account_details()
        if not session_str:
            return go("❌ <b>Chưa có Session nào được lưu!</b> Vui lòng đăng nhập Tele trước.", tele_menu())
        if me:
            full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "N/A"
            uname = f"@{me.username}" if me.username else "Không có"
            phone = f"+{me.phone}" if getattr(me, "phone", None) else "Ẩn"
            detail = (
                f"👤 <b>Tên Tele:</b> {html.escape(full_name)}\n"
                f"🆔 <b>ID Tele:</b> <code>{me.id}</code>\n"
                f"🔗 <b>Username:</b> {html.escape(uname)}\n"
                f"📱 <b>SĐT:</b> <code>{html.escape(phone)}</code>\n"
            )
        else:
            detail = "ℹ️ <b>Thông tin tài khoản:</b> Đã lưu chuỗi phiên đăng nhập\n"
        return go(
            f"🔑 <b>THÔNG TIN & SESSION STRING TELE NGUỒN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{detail}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>Session String (Bấm để sao chép):</b>\n<code>{html.escape(session_str)}</code>",
            tele_menu()
        )

    if data == "tele_status_btn":
        me, session_str = get_tele_account_details()
        return go(
            f"📊 <b>TRẠNG THÁI KẾT NỐI TELE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>API ID / HASH:</b> {'✅ Đã cấu hình' if TELETHON_API_ID and TELETHON_API_HASH else '❌ Thiếu biến môi trường'}\n"
            f"💾 <b>Session String:</b> {'✅ Đã lưu' if session_str else '❌ Chưa có'}\n"
            f"🟢 <b>Client Online:</b> {'✅ Đang hoạt động' if tele_ready else '🔴 Ngoại tuyến'}\n"
            f"📡 <b>Kênh code:</b> {html.escape(redeem_state.get('channel_name') or 'Chưa đặt')}",
            tele_menu()
        )

    if data == "tele_logout_confirm":
        kb = telebot.types.InlineKeyboardMarkup(row_width=2)
        kb.add(_btn("🗑️ XÁC NHẬN XOÁ", "tele_logout_yes"), _btn("↩️ QUAY LẠI", "tele_panel"))
        return go("⚠️ <b>Bạn có chắc muốn xoá Session Tele nguồn hiện tại?</b>", kb)

    if data == "tele_logout_yes":
        if os.path.exists(TELE_SESSION_FILE):
            os.remove(TELE_SESSION_FILE)
        return go("✅ <b>Đã xoá Session Tele nguồn.</b>", tele_menu())

# ========================= XỬ LÝ NHẬP LIỆU TỪ CHAT =========================
@bot.message_handler(func=lambda m: bool(m and m.chat and m.chat.id in user_flows), content_types=['text'])
def handle_menu_input(m):
    global REDEEM_ACCOUNTS
    cid = m.chat.id
    flow = user_flows.get(cid, {}).get("step")
    value = (m.text or "").strip()

    try:
        bot.delete_message(cid, m.message_id)
    except Exception:
        pass

    if value.lower() in ("huy", "huỷ", "cancel", "/cancel"):
        user_flows.pop(cid, None)
        return bot.send_message(cid, "↩️ Đã huỷ thao tác.", reply_markup=main_menu(cid))

    # 1. Kích hoạt Key VIP
    if flow == "key":
        key = value.upper()
        if key in valid_keys:
            days = valid_keys.pop(key)
            authorized_users[cid] = time.time() + days * 86400
            user_flows.pop(cid, None)
            exp = datetime.fromtimestamp(authorized_users[cid]).strftime("%d/%m/%Y %H:%M")
            return bot.send_message(
                cid,
                f"🎉 <b>KÍCH HOẠT KEY VIP THÀNH CÔNG!</b>\n"
                f"⏳ <b>Thời hạn:</b> {days} Ngày (Đến {exp})\n"
                f"👇 <i>Bấm nút bên dưới để mở Menu sử dụng:</i>",
                parse_mode="HTML",
                reply_markup=main_menu(cid)
            )
        return bot.send_message(cid, f"❌ <b>Key không hợp lệ hoặc đã sử dụng!</b>\nLiên hệ {SUPPORT_HANDLE} để mua key.", parse_mode="HTML", reply_markup=main_menu(cid))

    # 2. Login TK Game (User hoặc Admin) - Không cần Nickname
    if flow in ("user_login_game", "admin_login_game"):
        is_admin_flow = (flow == "admin_login_game")
        if is_admin_flow and cid != ADMIN_ID:
            user_flows.pop(cid, None)
            return
        if not is_admin_flow and not check_auth(cid):
            user_flows.pop(cid, None)
            return bot.send_message(cid, "🔒 Key của bạn đã hết hạn.", reply_markup=main_menu(cid))

        parts = value.split(maxsplit=1)
        if len(parts) != 2:
            return bot.send_message(cid, "⚠️ <b>Sai định dạng!</b> Vui lòng gửi: <code>TÀI_KHOẢN MẬT_KHẨU</code>", parse_mode="HTML")

        un, pw = parts[0].strip(), parts[1].strip()
        status_msg = bot.send_message(cid, f"🔄 <b>Đang đăng nhập tài khoản</b> <code>{html.escape(un)}</code>...", parse_mode="HTML")

        def do_login_task():
            res = login_and_get_info(un, pw)
            if "_error" in res:
                try:
                    bot.edit_message_text(
                        f"❌ <b>Đăng nhập thất bại:</b> {html.escape(res['_error'])}",
                        cid, status_msg.message_id, parse_mode="HTML",
                        reply_markup=redeem_menu() if is_admin_flow else main_menu(cid)
                    )
                except Exception:
                    pass
                return

            nick = res["nickname"]
            money = res["money"]
            save_game_account(cid, un, nick, res["token"], money, source="admin" if is_admin_flow else "user")
            upsert_redeem_account(un, res["pw_hashed"], nick, owner_id=cid, balance=money)
            user_flows.pop(cid, None)

            try:
                bot.edit_message_text(
                    f"✅ <b>ĐĂNG NHẬP & ĐỒNG BỘ THÀNH CÔNG!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 <b>Nickname (Tự động):</b> <b>{html.escape(nick)}</b>\n"
                    f"🔑 <b>Tài khoản:</b> <code>{html.escape(un)}</code>\n"
                    f"💰 <b>Số dư hiện tại:</b> <b>{money:,}</b>\n"
                    f"⚡ <b>Trạng thái:</b> Đã sẵn sàng lụm code!",
                    cid, status_msg.message_id, parse_mode="HTML",
                    reply_markup=redeem_menu() if is_admin_flow else main_menu(cid)
                )
            except Exception:
                pass

        threading.Thread(target=do_login_task, daemon=True).start()
        return

    # 3. Xoá tài khoản (User chỉ xoá acc của mình, Admin xoá mọi acc)
    if flow in ("user_del_redeem", "admin_del_redeem"):
        is_admin_flow = (flow == "admin_del_redeem")
        if is_admin_flow and cid != ADMIN_ID:
            user_flows.pop(cid, None)
            return

        target = value.lower()
        new_list = []
        removed = []
        for acc in redeem_state.get("accounts", []):
            match = acc.get("un", "").lower() == target or acc.get("nick", "").lower() == target
            can_remove = is_admin_flow or (acc.get("owner") == cid)
            if match and can_remove:
                removed.append(acc.get("nick") or acc.get("un"))
            else:
                new_list.append(acc)

        if not removed:
            return bot.send_message(
                cid,
                "❌ <b>Không tìm thấy tài khoản phù hợp!</b> Hãy kiểm tra lại tên và gửi lại.",
                parse_mode="HTML",
                reply_markup=redeem_menu() if is_admin_flow else main_menu(cid)
            )

        redeem_state["accounts"] = new_list
        save_json(REDEEM_STATE_FILE, redeem_state)
        REDEEM_ACCOUNTS = build_redeem_accounts()
        user_flows.pop(cid, None)
        return bot.send_message(
            cid,
            f"🗑️ <b>Đã xoá thành công:</b> {', '.join(removed)}",
            parse_mode="HTML",
            reply_markup=redeem_menu() if is_admin_flow else main_menu(cid)
        )

    # 4. Admin tạo Key tuỳ chỉnh
    if flow == "create_key" and cid == ADMIN_ID:
        try:
            days = int(value)
            if not 1 <= days <= 3650:
                raise ValueError
        except ValueError:
            return bot.send_message(cid, "⚠️ Vui lòng nhập số ngày hợp lệ (từ 1 đến 3650).")
        k = generate_vip_key(10)
        valid_keys[k] = days
        user_flows.pop(cid, None)
        exp = (datetime.now() + timedelta(days=days)).strftime("%d/%m/%Y %H:%M")
        return bot.send_message(
            cid,
            f"✅ <b>ĐÃ TẠO KEY VIP</b>\n🔑 <code>{k}</code>\n⏳ Thời hạn: {days} Ngày (Đến {exp})",
            parse_mode="HTML",
            reply_markup=key_manage_menu()
        )

    # 5. Admin Set Channel
    if flow == "set_channel" and cid == ADMIN_ID:
        target = value
        if "t.me/" in target:
            target = target.split("t.me/")[-1].strip("/").split("?")[0]
        if not target.startswith("@") and not target.lstrip("-").isdigit():
            target = "@" + target
        status_msg = bot.send_message(cid, f"🔄 Đang kết nối tới kênh <code>{html.escape(target)}</code>...", parse_mode="HTML")

        def resolve_ch():
            try:
                entity = tele_resolve_entity(target)
                peer_id = utils.get_peer_id(entity)
                redeem_state["channel_id"] = peer_id
                redeem_state["channel_name"] = getattr(entity, "title", None) or target
                save_json(REDEEM_STATE_FILE, redeem_state)
                user_flows.pop(cid, None)
                bot.edit_message_text(
                    f"✅ <b>ĐÃ ĐẶT KÊNH NHẬN CODE</b>\n📡 Tên kênh: <b>{html.escape(redeem_state['channel_name'])}</b>\n🆔 ID: <code>{peer_id}</code>",
                    cid, status_msg.message_id, parse_mode="HTML", reply_markup=redeem_menu()
                )
            except Exception as exc:
                bot.edit_message_text(
                    f"❌ <b>Lỗi đặt kênh:</b> {html.escape(str(exc))}\n💡 Hãy chắc chắn đã đăng nhập Tele nguồn.",
                    cid, status_msg.message_id, parse_mode="HTML", reply_markup=redeem_menu()
                )

        threading.Thread(target=resolve_ch, daemon=True).start()
        return

    # 6. Admin đăng nhập Tele nguồn (SĐT -> OTP -> 2FA)
    if flow == "tele_phone" and cid == ADMIN_ID:
        if not TELETHON_API_ID or not TELETHON_API_HASH:
            user_flows.pop(cid, None)
            return bot.send_message(cid, "❌ Thiếu TELETHON_API_ID hoặc TELETHON_API_HASH.", reply_markup=tele_menu())
        status_msg = bot.send_message(cid, "📲 Đang gửi mã xác minh OTP Telegram...")

        def send_otp_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                client = TelegramClient(StringSession(), TELETHON_API_ID, TELETHON_API_HASH, loop=loop)
                loop.run_until_complete(client.connect())
                sent = loop.run_until_complete(client.send_code_request(value))
                tele_login_pending.update({"phone": value, "hash": sent.phone_code_hash, "client": client, "loop": loop})
                user_flows[cid] = {"step": "tele_otp"}
                bot.edit_message_text("📲 <b>Đã gửi OTP!</b> Vui lòng nhập mã OTP vào khung chat:", cid, status_msg.message_id, parse_mode="HTML")
            except Exception as exc:
                user_flows.pop(cid, None)
                bot.edit_message_text(f"❌ Lỗi gửi OTP: {html.escape(str(exc))}", cid, status_msg.message_id, parse_mode="HTML", reply_markup=tele_menu())

        threading.Thread(target=send_otp_task, daemon=True).start()
        return

    if flow == "tele_otp" and cid == ADMIN_ID:
        pending = tele_login_pending
        if not pending.get("client"):
            user_flows.pop(cid, None)
            return bot.send_message(cid, "❌ Phiên OTP đã hết hạn.", reply_markup=tele_menu())
        status_msg = bot.send_message(cid, "🔐 Đang xác thực mã OTP...")

        def verify_otp_task():
            client, loop = pending["client"], pending["loop"]
            try:
                loop.run_until_complete(client.sign_in(phone=pending["phone"], code=value, phone_code_hash=pending["hash"]))
                _save_tele_session(client.session.save())
                tele_login_pending.clear()
                user_flows.pop(cid, None)
                bot.edit_message_text("✅ <b>Đăng nhập Tele thành công!</b> Đang bật canh kênh...", cid, status_msg.message_id, parse_mode="HTML", reply_markup=tele_menu())
                _activate_telethon_client(client, loop)
            except Exception as exc:
                if "SessionPasswordNeeded" in type(exc).__name__ or "password" in str(exc).lower():
                    user_flows[cid] = {"step": "tele_password"}
                    bot.edit_message_text("🔐 <b>Tài khoản có bật 2FA.</b> Vui lòng nhập mật khẩu 2FA:", cid, status_msg.message_id, parse_mode="HTML")
                else:
                    bot.edit_message_text(f"❌ Lỗi OTP: {html.escape(str(exc))}", cid, status_msg.message_id, parse_mode="HTML", reply_markup=tele_menu())

        threading.Thread(target=verify_otp_task, daemon=True).start()
        return

    if flow == "tele_password" and cid == ADMIN_ID:
        pending = tele_login_pending
        status_msg = bot.send_message(cid, "🔐 Đang xác thực mật khẩu 2FA...")

        def verify_2fa_task():
            try:
                client, loop = pending["client"], pending["loop"]
                loop.run_until_complete(client.sign_in(password=value))
                _save_tele_session(client.session.save())
                tele_login_pending.clear()
                user_flows.pop(cid, None)
                bot.edit_message_text("✅ <b>Đăng nhập Tele 2FA thành công!</b> Đang bật canh kênh...", cid, status_msg.message_id, parse_mode="HTML", reply_markup=tele_menu())
                _activate_telethon_client(client, loop)
            except Exception as exc:
                bot.edit_message_text(f"❌ Sai mật khẩu 2FA: {html.escape(str(exc))}", cid, status_msg.message_id, parse_mode="HTML", reply_markup=tele_menu())

        threading.Thread(target=verify_2fa_task, daemon=True).start()
        return

    user_flows.pop(cid, None)
    return bot.send_message(cid, "⚠️ Phiên thao tác đã kết thúc.", reply_markup=main_menu(cid))

def init_redeem_tokens():
    if not REDEEM_ACCOUNTS:
        return
    for acc in REDEEM_ACCOUNTS:
        try:
            acc.refresh_token()
        except Exception:
            pass

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=init_redeem_tokens, daemon=True).start()
    if TELETHON_API_ID and TELETHON_API_HASH:
        threading.Thread(target=_start_telethon_listener, daemon=True).start()
    logger.info("✨ LỤM CODE LC79 - HỆ THỐNG SẴN SÀNG ✨")

    if bot:
        while True:
            try:
                bot.infinity_polling(timeout=20, long_polling_timeout=30)
            except Exception as e:
                logger.error(f"Bot crashed: {e}")
                time.sleep(5)
    else:
        while True:
            time.sleep(60)