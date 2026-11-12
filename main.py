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
import asyncio
from flask import Flask
from datetime import datetime, timedelta
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession

# ========================= FLASK KEEPALIVE =========================
app = Flask(__name__)
@app.route("/")
def home(): return "🤖 𝐋Ụ𝐌 𝐂Ó𝐃𝐄 𝐋𝐂𝟕𝟗 - 𝐎𝐍𝐋𝐈𝐍𝐄"
@app.route("/health")
def health(): return {"status": "online"}
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ========================= CONFIG & LOGGING =========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('telethon').setLevel(logging.WARNING)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "@htungdzvipmax")
SUPPORT_CONTACT = "@htungdzvipmax"
TELETHON_API_ID = int(os.environ.get("TELETHON_API_ID", "0"))
TELETHON_API_HASH = os.environ.get("TELETHON_API_HASH", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

BOT_NAME = "✨ 𝐋ụ𝐦 𝐜𝐨𝐝𝐞 𝐋𝐂𝟕𝟗 ✨"

bot = None
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    logger.error("❌ LỖI: BOT_TOKEN chưa có hoặc sai định dạng.")
else:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        bot.set_my_commands([])
        logger.info("✅ Kết nối Telegram Bot OK!")
    except Exception as e:
        logger.error(f"❌ LỖI TOKEN TELEGRAM: {e}")
        bot = None

if not bot:
    class DummyBot:
        def __getattr__(self, name): return lambda *a, **kw: None
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
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception as e: logger.error(f"Load {path}: {e}")
    return default

def save_json(path, data):
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
            if os.path.exists(path + ".tmp"): os.remove(path + ".tmp")
        except Exception: pass
        return False

redeem_state = load_json(REDEEM_STATE_FILE, {"accounts": [], "channel_id": None, "channel_name": "", "enabled": False})
redeem_logs = load_json(REDEEM_LOGS_FILE, [])
game_accounts = load_json(GAME_ACCOUNTS_FILE, {})

def md5_hash(t): return hashlib.md5(t.encode()).hexdigest()
def normalize_pw(pw, force_mode=None):
    pw = pw.strip()
    if force_mode == "md5": return pw.lower()
    if force_mode == "plain": return md5_hash(pw)
    if len(pw) == 32 and all(c in '0123456789abcdef' for c in pw.lower()): return pw.lower()
    return md5_hash(pw)

def check_auth(chat_id):
    if chat_id == ADMIN_ID: return True
    if chat_id in authorized_users:
        if time.time() <= authorized_users[chat_id]: return True
        else: del authorized_users[chat_id]
    return False

def format_expire_time(ts):
    r = ts - time.time()
    if r <= 0: return "❌ HẾT HẠN"
    d = int(r // 86400); h = int((r % 86400) // 3600); m = int((r % 3600) // 60)
    return f"✅ CÒN {d}N {h}G {m}P" if d > 0 else f"✅ CÒN {h}G {m}P"

PRICE_LIST_TEXT = (
    "💎 <b>BẢNG GIÁ KÍCH HOẠT VIP</b>\n"
    "• <b>10.000đ / 2 Ngày</b> (Trải nghiệm nhanh)\n"
    "• <b>20.000đ / 5 Ngày</b> (Tiết kiệm nhất)\n"
    "• <b>30.000đ / Full Source Bot (All Src)</b> (Sở hữu mã nguồn toàn diện)\n\n"
    f"☎️ <b>Hỗ trợ & Giao dịch:</b> {SUPPORT_CONTACT}"
)

# ========================= REDEEM SYSTEM =========================
def append_redeem_log(code, account, bal_before, bal_after, amount, status):
    entry = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "code": code, "account": account,
             "balance_before": int(bal_before or 0), "balance_after": int(bal_after or 0), "amount": int(amount or 0), "status": status}
    redeem_logs.append(entry); save_json(REDEEM_LOGS_FILE, redeem_logs)
    logger.info(f"[REDEEM] {code} | {account} | {status} | amount={amount}")

class RedeemAccount:
    CONNECT_TIMEOUT = 5; READ_TIMEOUT = 45
    API_GET_ACCESS = "https://apifo88daigia.tele68.com/api"
    LOGIN_URL = "https://wlb.tele68.com/v1/lobby/auth/login"
    BASE_URL = "https://gameapi.tele68.com"
    PARAMS = "/v1/giftcode/redemption?cp=R&cl=R&pf=web&at="

    def __init__(self, un, pw, nick, owner=None):
        self.un = un; self.pw = pw; self.nick = nick; self.owner = owner
        self.jwt = None; self.jwt_exp = 0; self.balance = 0

    def _headers(self, auth=None):
        h = {"content-type": "application/json", "user-agent": "Mozilla/5.0", "origin": "https://lc79.to", "referer": "https://lc79.to/", "accept": "application/json, text/plain, */*", "connection": "close"}
        if auth: h["authorization"] = f"Bearer {auth}"
        return h

    @staticmethod
    def parse_jwt_exp(token):
        try:
            payload = token.split(".")[1]; payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
        except: return 0

    def is_token_valid(self): return self.jwt is not None and time.time() < (self.jwt_exp - 60)

    def get_access_token(self):
        try:
            with requests.Session() as s:
                res = s.get(self.API_GET_ACCESS, params={"c": "3", "un": self.un, "pw": self.pw, "cp": "R", "cl": "R", "pf": "web", "at": ""}, headers=self._headers(), timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT))
            data = res.json()
            return data.get("accessToken") if data.get("success") else None
        except Exception as e: logger.error(f"[{self.nick}] get_access_token: {e}"); return None

    def refresh_token(self):
        at = self.get_access_token()
        if not at: return False
        try:
            with requests.Session() as s:
                res = s.post(self.LOGIN_URL, headers={"content-type": "application/json", "origin": "https://lc79b.bet", "referer": "https://lc79b.bet/", "user-agent": "Mozilla/5.0", "connection": "close"},
                             json={"nickName": self.nick, "accessToken": at}, timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT))
            data = res.json()
            new_tok = data.get("token")
            if new_tok:
                self.jwt = new_tok; self.jwt_exp = self.parse_jwt_exp(new_tok)
                self.balance = int(data.get("remoteLoginResp", {}).get("money", self.balance) or self.balance)
                return True
            return False
        except Exception as e: logger.error(f"[{self.nick}] refresh: {e}"); return False

    def _single_redeem_request(self, code, bal_before):
        try:
            if not self.is_token_valid():
                if not self.refresh_token(): return "LOGIN FAIL", 0
            with requests.Session() as s:
                res = s.post(self.BASE_URL + self.PARAMS, headers=self._headers(self.jwt), json={"code": code.strip(), "captcha": ""}, timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT))
            try: data = res.json()
            except: data = {}
            if res.status_code in (200, 201):
                amount = (data.get("money") or data.get("amount") or data.get("reward") or (data.get("data") or {}).get("money") or 0)
                try: amount = int(amount)
                except: amount = 0
                return "SUCCESS", amount
            elif res.status_code == 401:
                self.refresh_token()
                return "TOKEN EXPIRED", 0
            else:
                err = data.get("message", f"HTTP {res.status_code}") if isinstance(data, dict) else f"HTTP {res.status_code}"
                return f"FAIL: {err}", 0
        except Exception:
            return "TIMEOUT/NETWORK", 0

    def redeem_code_spam_10(self, code):
        if not self.is_token_valid(): self.refresh_token()
        bal_before = self.balance
        threads = []
        results = []

        def worker():
            st, amt = self._single_redeem_request(code, bal_before)
            results.append((st, amt))

        for _ in range(10):
            t = threading.Thread(target=worker, daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=30)

        # Cập nhật lại token & số dư thực tế sau khi spam
        self.refresh_token()
        bal_after = self.balance

        success_runs = [r for r in results if r[0] == "SUCCESS"]
        if success_runs:
            total_amt = sum(r[1] for r in success_runs)
            append_redeem_log(code, self.nick, bal_before, bal_after, total_amt if total_amt > 0 else 0, "SUCCESS")
        else:
            last_st = results[0][0] if results else "UNKNOWN"
            append_redeem_log(code, self.nick, bal_before, bal_after, 0, last_st)

def build_redeem_accounts():
    accounts = redeem_state.get("accounts", []) if isinstance(redeem_state, dict) else []
    result = []
    for item in accounts:
        if not isinstance(item, dict): continue
        un = str(item.get("un") or item.get("username") or "").strip()
        pw = str(item.get("pw") or item.get("password") or "").strip()
        nick = str(item.get("nick") or item.get("nickname") or un).strip()
        owner = item.get("owner")
        if un and pw:
            result.append(RedeemAccount(un, pw, nick or un, owner))
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
    elif not isinstance(redeem_state, dict):
        redeem_state = {"accounts": [], "channel_id": None, "channel_name": "", "enabled": False}
    REDEEM_ACCOUNTS = build_redeem_accounts()
    return REDEEM_ACCOUNTS

REDEEM_ACCOUNTS = build_redeem_accounts()

def redeem_all_simultaneous(code):
    if not REDEEM_ACCOUNTS: logger.warning("Chưa có acc redeem"); return
    def worker():
        for acc in REDEEM_ACCOUNTS:
            if not acc.is_token_valid(): acc.refresh_token()
        threads = []
        for acc in REDEEM_ACCOUNTS:
            t = threading.Thread(target=acc.redeem_code_spam_10, args=(code,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads: t.join(timeout=120)
        recent = [l for l in redeem_logs if l["code"] == code]
        ok = sum(1 for l in recent if l["status"] == "SUCCESS")
        total = sum(l["amount"] for l in recent if l["status"] == "SUCCESS")
        try: bot.send_message(ADMIN_ID, f"🎁 <b>ĐÃ REDEEM ĐỒNG LOẠT (SPAM 10 LẦN/ACC)</b>\n📝 Code: <code>{code}</code>\n✅ Thành công: {ok}/{len(REDEEM_ACCOUNTS)} acc\n💰 Tổng nhận: <b>{total:,}</b>", parse_mode="HTML")
        except: pass
    threading.Thread(target=worker, daemon=True).start()

# ========================= LOGIN & ADD ACC =========================
def login_and_get_info(u, p):
    try:
        pw_hashed = normalize_pw(p)
        r = requests.get(f"https://apifo88daigia.tele68.com/api?c=3&un={u}&pw={pw_hashed}&cp=R&cl=R&pf=web&at=", timeout=12).json()
        if not r.get("success"): return {"_error": "Sai Tài Khoản hoặc Mật Khẩu"}
        sk = r["sessionKey"] + "=" * ((4 - len(r["sessionKey"]) % 4) % 4)
        nn = json.loads(base64.b64decode(sk)).get("nickname")
        r2 = requests.post("https://wlb.tele68.com/v1/lobby/auth/login?cp=R&cl=R&pf=web&at=", json={"nickName": nn, "accessToken": r["accessToken"]}, timeout=12).json()
        if not r2.get("token"): return {"_error": "Không nhận được token (Acc đang kẹt phiên?)"}
        return {"token": r2["token"], "nickname": nn, "money": int(r2.get("remoteLoginResp", {}).get("money", 0) or 0), "pw_hashed": pw_hashed}
    except Exception as e: return {"_error": str(e)}

def save_game_account(key, username, nickname, token, money, source="user"):
    game_accounts[str(key)] = {"username": username, "nickname": nickname, "token": token, "money": int(money or 0), "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": source}
    save_json(GAME_ACCOUNTS_FILE, game_accounts)

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

def extract_clean_code(text):
    match = re.search(r"Mã code là:\s*([A-Z0-9]+)", text, re.IGNORECASE)
    if not match: return None
    full_code = match.group(1).strip().upper()
    if full_code.startswith("LC79") and len(full_code) > 4:
        return full_code[4:]
    return full_code

def _handle_tele_message(event):
    if not redeem_state["enabled"] or redeem_state["channel_id"] is None: return
    if event.chat_id != redeem_state["channel_id"]: return
    text = event.raw_text or ""
    clean_code = extract_clean_code(text)
    if not clean_code: return
    logger.info(f"[TELE] Lụm Code sạch: {clean_code}")
    try: bot.send_message(ADMIN_ID, f"🎁 <b>LỤM CODE MỚI TỪ TELEGRAM</b>\n📝 Mã sạch: <code>{clean_code}</code>\n🚀 Đang kích hoạt spam đồng loạt {len(REDEEM_ACCOUNTS)} acc...", parse_mode="HTML")
    except: pass
    redeem_all_simultaneous(clean_code)

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
            logger.info("TELE ONLINE sau đăng nhập: @%s", me.username or me.first_name)
            try:
                bot.send_message(ADMIN_ID, f"✅ <b>TELEGRAM NGUỒN ĐÃ ONLINE</b>\n👤 Tên: {me.first_name}\n📌 Username: @{me.username or 'Không có'}\n🆔 ID: <code>{me.id}</code>", parse_mode="HTML")
            except Exception: pass
            await client.run_until_disconnected()
        except Exception as exc:
            tele_ready = False
            logger.exception("Tele listener lỗi: %s", exc)

    loop.run_until_complete(_keep_running())

def _start_telethon_listener():
    global tele_client, tele_loop, tele_ready
    if TELETHON_API_ID == 0 or not TELETHON_API_HASH: return
    session_str = _load_tele_session()
    if not session_str: return
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
            tele_ready = True
            try: bot.send_message(ADMIN_ID, f"✅ <b>TELEGRAM NGUỒN ONLINE</b>\n👤 Tên: {me.first_name}\n📌 Username: @{me.username or 'Không có'}\n🆔 ID: <code>{me.id}</code>", parse_mode="HTML")
            except: pass
            await tele_client.run_until_disconnected()
        except Exception as e: logger.error(f"Tele run: {e}")
    tele_loop.run_until_complete(_run())

def tele_resolve_entity(target, timeout=15):
    if tele_client is None or not tele_ready: raise RuntimeError("Tele chưa sẵn sàng")
    f = asyncio.run_coroutine_threadsafe(tele_client.get_entity(target), tele_loop)
    return f.result(timeout=timeout)

# ========================= UI MENU & SCREENS =========================
user_flows = {}
SCREEN_MESSAGES = {}

def _btn(text, data):
    return telebot.types.InlineKeyboardButton(text, callback_data=data)

def replace_screen(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    try: bot.delete_message(chat_id, message_id)
    except Exception:
        try:
            msg = bot.edit_message_text(text, chat_id, message_id, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)
            SCREEN_MESSAGES[chat_id] = message_id
            return msg
        except Exception: pass
    msg = bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)
    SCREEN_MESSAGES[chat_id] = msg.message_id
    return msg

def show_screen(chat_id, message_id, text, keyboard=None, parse_mode=None):
    return replace_screen(chat_id, message_id, text, keyboard, parse_mode)

def flow_prompt(chat_id, message_id, flow, title, detail):
    user_flows[chat_id] = {"step": flow}
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(_btn("✖️ Huỷ thao tác", "flow_cancel"))
    return show_screen(chat_id, message_id, f"<b>{title}</b>\n\n{detail}\n\n<i>Gửi nội dung vào khung chat bên dưới.</i>", kb, "HTML")

def main_menu(cid=None):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🔐 ĐĂNG NHẬP GAME", "login"), _btn("🔑 KÍCH HOẠT KEY", "nhapkey"))
    kb.add(_btn("💎 BẢNG GIÁ VIP", "buy_info"), _btn("💻 MÃ NGUỒN BOT", "buy_source"))
    kb.add(_btn("☎️ HỖ TRỢ 24/7", "support_info"))
    return kb

def dashboard_menu(cid=None):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    if cid == ADMIN_ID:
        kb.add(_btn("🎁 AUTO REDEEM", "rd_panel"), _btn("🔐 ĐĂNG NHẬP GAME", "login"))
        kb.add(_btn("👑 QUẢN TRỊ HỆ THỐNG", "admin_panel"), _btn("💎 BẢNG GIÁ VIP", "buy_info"))
        kb.add(_btn("💻 MÃ NGUỒN BOT", "buy_source"), _btn("☎️ HỖ TRỢ 24/7", "support_info"))
    else:
        kb.add(_btn("🔐 ĐĂNG NHẬP GAME", "login"), _btn("📋 DS ACC CỦA TÔI", "user_my_accs"))
        kb.add(_btn("➖ XOÁ ACC CỦA TÔI", "user_del_acc"), _btn("🔑 NHẬP KEY MỚI", "nhapkey"))
        kb.add(_btn("💎 BẢNG GIÁ VIP", "buy_info"), _btn("💻 MÃ NGUỒN BOT", "buy_source"))
        kb.add(_btn("☎️ HỖ TRỢ 24/7", "support_info"))
    return kb

def admin_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🎁 REDEEM TỰ ĐỘNG", "rd_panel"), _btn("👥 QUẢN LÝ ACC", "data_panel"))
    kb.add(_btn("🔑 TẠO KEY VIP", "create_key_menu"), _btn("📡 TELEGRAM NGUỒN", "tele_panel"))
    kb.add(_btn("📦 XUẤT FILE BÁO CÁO", "export_menu"))
    kb.add(_btn("⬅️ MENU CHÍNH", "home"))
    return kb

def create_key_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🎟️ Gói 2 Ngày (10k)", "mk_2d"), _btn("🎟️ Gói 5 Ngày (20k)", "mk_5d"))
    kb.add(_btn("🎟️ Tuỳ chọn số ngày", "mk_custom"), _btn("⬅️ QUẢN TRỊ", "admin_panel"))
    return kb

def redeem_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    toggle = "🟢 TẮT AUTO" if redeem_state.get("enabled") else "⚪ BẬT AUTO"
    kb.add(_btn(toggle, "rd_toggle"), _btn("📡 ĐẶT KÊNH CODE", "rd_setchannel"))
    kb.add(_btn("➕ LOGIN TK MỚI", "login"), _btn("📋 DANH SÁCH ACC", "rd_list"))
    kb.add(_btn("🔄 LÀM MỚI TOKEN", "rd_refresh"), _btn("⬅️ QUẢN TRỊ", "admin_panel"))
    return kb

def data_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🎁 ACC REDEEM", "rd_list"), _btn("🎮 GAME ACC", "ga_status"))
    kb.add(_btn("➖ XOÁ ACC GAME", "admin_del_acc_prompt"), _btn("⬅️ QUẢN TRỊ", "admin_panel"))
    return kb

def tele_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🔐 KẾT NỐI TELE", "tele_login"), _btn("📊 TRẠNG THÁI", "tele_status_btn"))
    kb.add(_btn("📦 XUẤT SESSION STRING", "tele_export_session"), _btn("🗑️ ĐĂNG XUẤT", "tele_logout_confirm"))
    kb.add(_btn("⬅️ QUẢN TRỊ", "admin_panel"))
    return kb

def export_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🔑 XUẤT TOKEN (TXT)", "export_token_file"), _btn("📊 XUẤT LỊCH SỬ REDEEM (TXT)", "export_log_file"))
    kb.add(_btn("⬅️ QUẢN TRỊ", "admin_panel"))
    return kb

def send_token_export_file(chat_id):
    if not REDEEM_ACCOUNTS:
        return bot.send_message(chat_id, "📭 Chưa có tài khoản redeem nào.")
    content = "=" * 60 + "\n 𝐋Ụ𝐌 𝐂Ó𝐃𝐄 𝐋𝐂𝟕𝟗 - ACCESS TOKEN EXPORT\n" + "=" * 60 + "\n"
    for i, a in enumerate(REDEEM_ACCOUNTS, 1):
        content += f"[{i}] Nick: {a.nick} | User: {a.un} | Token Valid: {a.is_token_valid()}\nJWT: {a.jwt or 'N/A'}\n" + "-" * 60 + "\n"
    bio = io.BytesIO(content.encode("utf-8")); bio.name = f"lc79_tokens_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(chat_id, bio, caption=f"🔑 Danh sách Token của {len(REDEEM_ACCOUNTS)} tài khoản.")

def send_full_redeem_log_file(chat_id):
    if not redeem_logs:
        return bot.send_message(chat_id, "📭 Chưa có lịch sử redeem nào.")
    content = "=" * 60 + "\n 𝐋Ụ𝐌 𝐂Ó𝐃𝐄 𝐋𝐂𝟕𝟗 - FULL REDEEM LOG HISTORY\n" + "=" * 60 + "\n"
    ok = sum(1 for l in redeem_logs if l.get("status") == "SUCCESS")
    total_amount = sum(int(l.get("amount", 0) or 0) for l in redeem_logs if l.get("status") == "SUCCESS")
    content += f"Thời gian xuất: {datetime.now():%d/%m/%Y %H:%M:%S}\nTổng log: {len(redeem_logs)} | Thành công: {ok} | Tổng nhận: {total_amount:,}\n" + "=" * 60 + "\n\n"
    for i, log in enumerate(redeem_logs, 1):
        content += (f"[{i}] {log.get('time','')}\nCode: {log.get('code','')}\nAccount: {log.get('account','')}\n"
                    f"Trước: {int(log.get('balance_before',0) or 0):,} | Sau: {int(log.get('balance_after',0) or 0):,} | Nhận: {int(log.get('amount',0) or 0):,}\n"
                    f"Trạng thái: {log.get('status','')}\n" + "-" * 60 + "\n")
    bio = io.BytesIO(content.encode("utf-8")); bio.name = f"lc79_full_redeem_history_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(chat_id, bio, caption=f"📊 Lịch sử redeem đầy đủ ({len(redeem_logs)} bản ghi).")

@bot.message_handler(commands=['start', 'menu'])
def cmd_start(m):
    cid = m.chat.id
    try: bot.delete_message(cid, m.message_id)
    except Exception: pass
    old_id = SCREEN_MESSAGES.get(cid)
    if old_id:
        try: bot.delete_message(cid, old_id)
        except Exception: pass
    
    # Lấy thông tin user Telegram
    user = m.from_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Thành viên"
    username_str = f"@{user.username}" if user.username else "Không có"
    
    if check_auth(cid):
        han = "👑 QUẢN TRỊ VIÊN" if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
        header_text = (
            f"🤖 <b>{BOT_NAME}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Họ tên:</b> {full_name}\n"
            f"📌 <b>Username:</b> {username_str}\n"
            f"🆔 <b>ID User:</b> <code>{cid}</code>\n"
            f"⏳ <b>Hạn Key:</b> {han}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <i>Chọn chức năng bên dưới:</i>"
        )
        msg = bot.send_message(cid, header_text, parse_mode="HTML", reply_markup=dashboard_menu(cid))
    else:
        header_text = (
            f"🤖 <b>{BOT_NAME}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Họ tên:</b> {full_name}\n"
            f"📌 <b>Username:</b> {username_str}\n"
            f"🆔 <b>ID User:</b> <code>{cid}</code>\n"
            f"🔒 <b>Trạng thái:</b> Chưa kích hoạt Key\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <i>Chọn đăng nhập hoặc kích hoạt key:</i>"
        )
        msg = bot.send_message(cid, header_text, parse_mode="HTML", reply_markup=main_menu(cid))
    SCREEN_MESSAGES[cid] = msg.message_id

@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    global redeem_logs, game_accounts, REDEEM_ACCOUNTS
    cid, data = c.message.chat.id, c.data
    try: bot.answer_callback_query(c.id)
    except Exception: pass

    def go(text, kb=None, mode=None):
        return show_screen(cid, c.message.message_id, text, kb, mode)

    if data == "flow_cancel":
        user_flows.pop(cid, None)
        if check_auth(cid): return go(f"🤖 <b>{BOT_NAME}</b>\nChọn chức năng:", dashboard_menu(cid), "HTML")
        return go(f"🤖 <b>{BOT_NAME}</b>\nChọn chức năng:", main_menu(cid), "HTML")

    if data in ("home", "dashboard", "refresh"):
        user_flows.pop(cid, None)
        if check_auth(cid): return go(f"🤖 <b>{BOT_NAME}</b>\n✨ Bảng điều khiển\n⏳ {'👑 ADMIN' if cid == ADMIN_ID else format_expire_time(authorized_users[cid])}", dashboard_menu(cid), "HTML")
        return go(f"🤖 <b>{BOT_NAME}</b>\n🔒 Chưa kích hoạt.", main_menu(cid), "HTML")

    if data == "login":
        if not check_auth(cid): return go("🔒 Bạn cần kích hoạt key trước khi đăng nhập game.", main_menu(cid))
        return flow_prompt(cid, c.message.message_id, "login", "🔐 ĐĂNG NHẬP GAME", "Gửi theo mẫu: <code>TÀI_KHOẢN MẬT_KHẨU</code>\nHệ thống sẽ tự động xác thực và đưa vào danh sách chạy lụm code.")

    if data == "nhapkey":
        return flow_prompt(cid, c.message.message_id, "key", "🔑 KÍCH HOẠT KEY VIP", "Gửi mã key 10 ký tự của bạn.")

    if data in ("buy_info", "buy_source", "support_info"):
        kb = telebot.types.InlineKeyboardMarkup(row_width=2)
        kb.add(_btn("🔑 NHẬP KEY NGAY", "nhapkey"), _btn("⬅️ QUAY LẠI", "home"))
        extra_note = (
            "💻 <b>GÓI MÃ NGUỒN BOT (ALL SOURCE CODE - 30K)</b>\n"
            "• Giao toàn bộ source code Python hoàn chỉnh, sạch đẹp.\n"
            "• Chạy mượt 24/7 trên VPS / Render / Termux.\n"
            "• Hướng dẫn cài đặt từ A-Z.\n"
            f"👉 <b>Liên hệ chốt đơn:</b> {SUPPORT_CONTACT}\n\n"
            if data == "buy_source" else ""
        )
        return go(f"{BOT_NAME}\n\n{extra_note}{PRICE_LIST_TEXT}", kb, "HTML")

    if data == "user_my_accs":
        if not check_auth(cid): return go("🔒 Bạn cần kích hoạt key trước.", main_menu(cid))
        sync_redeem_accounts()
        my_accs = [a for a in redeem_state.get("accounts", []) if a.get("owner") == cid or (cid == ADMIN_ID and not a.get("owner"))]
        if not my_accs:
            return go("📭 <b>DANH SÁCH ACC CỦA BẠN</b>\n\nBạn chưa thêm tài khoản game nào. Hãy bấm <b>Đăng nhập game</b> để thêm.", dashboard_menu(cid), "HTML")
        lines = []
        for i, item in enumerate(my_accs, 1):
            acc_obj = next((x for x in REDEEM_ACCOUNTS if x.un.lower() == item.get("un", "").lower()), None)
            bal = acc_obj.balance if acc_obj else item.get("balance", 0)
            st = "🟢" if (acc_obj and acc_obj.is_token_valid()) else "⚪"
            lines.append(f"{i}. {st} <b>{item.get('nick', '')}</b> · <code>{item.get('un', '')}</code> · 💰 <b>{int(bal or 0):,}</b>")
        auto_st = "🟢 Đang chạy tự động" if redeem_state.get("enabled") else "🔴 Đang tạm dừng"
        return go(f"📋 <b>TÀI KHOẢN CỦA BẠN ({len(my_accs)})</b>\n⚙️ Hệ thống Auto: {auto_st}\n\n" + "\n".join(lines), dashboard_menu(cid), "HTML")

    if data == "user_del_acc":
        if not check_auth(cid): return go("🔒 Bạn cần kích hoạt key trước.", main_menu(cid))
        return flow_prompt(cid, c.message.message_id, "user_del_acc", "➖ XOÁ TÀI KHOẢN CỦA BẠN", "Gửi chính xác <b>Username</b> hoặc <b>Nickname</b> tài khoản game của bạn cần xoá khỏi hệ thống.")

    if data == "admin_panel":
        sync_redeem_accounts()
        if cid != ADMIN_ID: return go("⛔ Bạn không có quyền quản trị.", dashboard_menu(cid) if check_auth(cid) else main_menu(cid))
        return go(f"👑 <b>TRUNG TÂM QUẢN TRỊ</b>\n\n🎁 Acc redeem: <b>{len(REDEEM_ACCOUNTS)}</b>\n🎮 Game account: <b>{len(game_accounts)}</b>\n📝 Lịch sử: <b>{len(redeem_logs)}</b>\n⚙️ Auto redeem: <b>{'🟢 ĐANG BẬT' if redeem_state.get('enabled') else '🔴 ĐANG TẮT'}</b>", admin_menu(), "HTML")

    if data == "create_key_menu":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        return go("🔑 <b>TẠO KEY VIP</b>\n\nChọn gói thời hạn bên dưới:", create_key_menu(), "HTML")

    if data == "mk_2d":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        key = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=10))
        valid_keys[key] = 2
        exp = (datetime.now() + timedelta(days=2)).strftime("%d/%m/%Y %H:%M")
        return go(f"🔑 <b>ĐÃ TẠO KEY (10k / 2 Ngày)</b>\n\n<code>{key}</code>\n⏳ Hạn: 2 ngày\n📅 Hết hạn: {exp}", create_key_menu(), "HTML")

    if data == "mk_5d":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        key = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=10))
        valid_keys[key] = 5
        exp = (datetime.now() + timedelta(days=5)).strftime("%d/%m/%Y %H:%M")
        return go(f"🔑 <b>ĐÃ TẠO KEY (20k / 5 Ngày)</b>\n\n<code>{key}</code>\n⏳ Hạn: 5 ngày\n📅 Hết hạn: {exp}", create_key_menu(), "HTML")

    if data == "mk_custom":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        return flow_prompt(cid, c.message.message_id, "create_key_custom", "🔑 TẠO KEY TUỲ CHỌN", "Nhập số ngày sử dụng (ví dụ: <code>30</code>).")

    if data == "rd_panel":
        sync_redeem_accounts()
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        text = ("🎁 <b>QUẢN LÝ REDEEM</b>\n\n"
                f"⚙️ Auto: {'🟢 BẬT' if redeem_state.get('enabled') else '🔴 TẮT'}\n"
                f"📡 Kênh: {redeem_state.get('channel_name') or 'Chưa thiết lập'}\n"
                f"👥 Tài khoản: {len(REDEEM_ACCOUNTS)}\n📝 Lịch sử: {len(redeem_logs)}")
        return go(text, redeem_menu(), "HTML")

    if data == "rd_toggle":
        sync_redeem_accounts()
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        if redeem_state.get("enabled"):
            redeem_state["enabled"] = False
        elif not REDEEM_ACCOUNTS:
            return go("⚠️ Chưa có tài khoản redeem.", redeem_menu())
        elif redeem_state.get("channel_id") is None:
            return go("⚠️ Chưa đặt kênh code.", redeem_menu())
        else:
            redeem_state["enabled"] = True
        save_json(REDEEM_STATE_FILE, redeem_state)
        text = ("🎁 <b>QUẢN LÝ REDEEM</b>\n\n"
                f"⚙️ Auto: {'🟢 BẬT' if redeem_state['enabled'] else '🔴 TẮT'}\n"
                f"📡 Kênh: {redeem_state.get('channel_name') or 'Chưa thiết lập'}\n👥 Tài khoản: {len(REDEEM_ACCOUNTS)}")
        return go(text, redeem_menu(), "HTML")

    if data == "rd_setchannel":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return flow_prompt(cid, c.message.message_id, "set_channel", "📡 THIẾT LẬP KÊNH CODE", "Gửi @username hoặc link t.me/... của kênh nhận code.")

    if data == "rd_list":
        sync_redeem_accounts()
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        if not REDEEM_ACCOUNTS: return go("📭 Chưa có tài khoản redeem.", redeem_menu())
        lines = [f"{i}. {'🟢' if a.is_token_valid() else '⚪'} <b>{a.nick}</b> · <code>{a.un}</code> · 💰 {a.balance:,}" for i, a in enumerate(REDEEM_ACCOUNTS, 1)]
        return go("📋 <b>DANH SÁCH ACC REDEEM</b>\n\n" + "\n".join(lines), redeem_menu(), "HTML")

    if data == "rd_refresh":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        msg = go("🔄 Đang làm mới token toàn bộ tài khoản...", redeem_menu())
        def refresh_worker():
            success = 0
            for account in REDEEM_ACCOUNTS:
                try:
                    if account.refresh_token(): success += 1
                except Exception: pass
            try: bot.send_message(cid, f"✅ Đã làm mới token: {success}/{len(REDEEM_ACCOUNTS)} tài khoản.", reply_markup=redeem_menu())
            except Exception: pass
        threading.Thread(target=refresh_worker, daemon=True).start()
        return

    if data == "data_panel":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return go(f"👥 <b>QUẢN LÝ DỮ LIỆU</b>\n\n🎁 Redeem account: {len(REDEEM_ACCOUNTS)}\n🎮 Game account: {len(game_accounts)}\n📝 Redeem log: {len(redeem_logs)}", data_menu(), "HTML")

    if data == "ga_status":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        if not game_accounts: return go("📭 Chưa có game account nào.", data_menu())
        lines = [f"{i}. <b>{info.get('nickname','')}</b> · {info.get('username','')} · 💰 {int(info.get('money',0) or 0):,}" for i, (k, info) in enumerate(game_accounts.items(), 1)]
        return go("🎮 <b>DANH SÁCH GAME ACCOUNT</b>\n\n" + "\n".join(lines), data_menu(), "HTML")

    if data == "admin_del_acc_prompt":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return flow_prompt(cid, c.message.message_id, "admin_del_acc", "➖ XOÁ TÀI KHOẢN HỆ THỐNG", "Gửi chính xác <b>Username</b> hoặc <b>Nickname</b> tài khoản cần xoá khỏi hệ thống.")

    if data == "export_menu":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        return go("📦 <b>XUẤT FILE BÁO CÁO & DỮ LIỆU</b>\n\nChọn loại file cần xuất:", export_menu(), "HTML")

    if data == "export_token_file":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        try: bot.delete_message(cid, c.message.message_id)
        except Exception: pass
        send_token_export_file(cid)
        msg = bot.send_message(cid, "📦 Đã xuất file Token.", reply_markup=admin_menu())
        SCREEN_MESSAGES[cid] = msg.message_id
        return

    if data == "export_log_file":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        try: bot.delete_message(cid, c.message.message_id)
        except Exception: pass
        send_full_redeem_log_file(cid)
        msg = bot.send_message(cid, "📦 Đã xuất file Lịch sử Redeem.", reply_markup=admin_menu())
        SCREEN_MESSAGES[cid] = msg.message_id
        return

    if data == "tele_panel":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        saved = bool(_load_tele_session())
        status = "🟢 ĐÃ ĐĂNG NHẬP & ĐANG THEO DÕI" if tele_ready else ("🟡 CÓ SESSION, CHƯA KẾT NỐI" if saved else "🔴 CHƯA ĐĂNG NHẬP")
        return go(f"📡 <b>TELEGRAM NGUỒN</b>\n\nTrạng thái: {status}\nKênh code: {redeem_state.get('channel_name') or 'Chưa cài đặt'}", tele_menu(), "HTML")

    if data == "tele_login":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return flow_prompt(cid, c.message.message_id, "tele_phone", "🔐 KẾT NỐI TELEGRAM", "Gửi số điện thoại quốc gia, ví dụ <code>+849xxxxxxxx</code>.")

    if data == "tele_status_btn":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        saved = bool(_load_tele_session())
        return go(f"📡 <b>TRẠNG THÁI TELEGRAM</b>\n\nAPI ID: {'✅' if TELETHON_API_ID else '❌'}\nAPI HASH: {'✅' if TELETHON_API_HASH else '❌'}\nSession: {'✅' if saved else '❌'}\nClient: {'🟢 ONLINE' if tele_ready else '🔴 OFFLINE'}\nKênh: {redeem_state.get('channel_name') or 'Chưa cài đặt'}", tele_menu(), "HTML")

    if data == "tele_export_session":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        sess = _load_tele_session()
        if not sess: return go("❌ Chưa có session Telegram nào được lưu.", tele_menu())
        bio = io.BytesIO(sess.encode("utf-8")); bio.name = f"tele_session_string_{datetime.now():%Y%m%d_%H%M%S}.txt"
        try: bot.send_document(cid, bio, caption="📦 String Session Telegram nguồn đầy đủ.")
        except Exception: pass
        return go("✅ Đã gửi file String Session.", tele_menu(), "HTML")

    if data == "tele_logout_confirm":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        kb = telebot.types.InlineKeyboardMarkup(row_width=2)
        kb.add(_btn("🗑️ XÁC NHẬN ĐĂNG XUẤT", "tele_logout_yes"), _btn("↩️ QUAY LẠI", "tele_panel"))
        return go("⚠️ Xoá session Telegram đã lưu?", kb)

    if data == "tele_logout_yes":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        if os.path.exists(TELE_SESSION_FILE): os.remove(TELE_SESSION_FILE)
        return go("✅ Đã đăng xuất Telegram.", tele_menu())

    return go("Chức năng không tồn tại.", dashboard_menu(cid) if check_auth(cid) else main_menu(cid))


@bot.message_handler(func=lambda m: bool(m and m.chat and m.chat.id in user_flows), content_types=['text'])
def handle_menu_input(m):
    global REDEEM_ACCOUNTS
    cid = m.chat.id
    flow = user_flows.get(cid, {}).get("step")
    value = (m.text or "").strip()
    try: bot.delete_message(cid, m.message_id)
    except Exception: pass
    if value.lower() in ("huy", "huỷ", "cancel", "/cancel"):
        user_flows.pop(cid, None)
        return bot.send_message(cid, "↩️ Đã huỷ thao tác.", reply_markup=dashboard_menu(cid) if check_auth(cid) else main_menu(cid))

    if flow == "key":
        key = value.upper()
        if key in valid_keys:
            authorized_users[cid] = time.time() + valid_keys[key] * 86400
            del valid_keys[key]
            user_flows.pop(cid, None)
            expiry = datetime.fromtimestamp(authorized_users[cid]).strftime("%d/%m/%Y %H:%M")
            return bot.send_message(cid, f"✅ <b>KÍCH HOẠT VIP THÀNH CÔNG</b>\n⏳ Hạn dùng: {expiry}", parse_mode="HTML", reply_markup=dashboard_menu(cid))
        return bot.send_message(cid, "❌ Key không hợp lệ hoặc đã được sử dụng.", reply_markup=main_menu(cid))

    if flow == "login":
        parts = value.split(maxsplit=1)
        if len(parts) != 2:
            return bot.send_message(cid, "⚠️ Sai định dạng. Gửi lại theo mẫu: <code>TÀI_KHOẢN MẬT_KHẨU</code>", parse_mode="HTML")
        if not check_auth(cid):
            user_flows.pop(cid, None)
            return bot.send_message(cid, "🔒 Key của bạn chưa kích hoạt hoặc đã hết hạn.", reply_markup=main_menu(cid))
        un, pw = parts
        status = bot.send_message(cid, "🔄 Đang đăng nhập và đồng bộ tài khoản...")
        def do_login_flow():
            result = login_and_get_info(un, pw)
            if "_error" in result:
                try: bot.edit_message_text(f"❌ Đăng nhập thất bại: {result['_error']}", cid, status.message_id, reply_markup=dashboard_menu(cid))
                except Exception: pass
                return
            save_game_account(f"{cid}_{un}", un, result["nickname"], result["token"], result["money"], source=str(cid))
            exists = any(a.get("un", "").lower() == un.lower() for a in redeem_state["accounts"])
            if not exists:
                redeem_state.setdefault("accounts", []).append({"un": un, "pw": result["pw_hashed"], "nick": result["nickname"], "owner": cid, "balance": result["money"]})
                if save_json(REDEEM_STATE_FILE, redeem_state):
                    sync_redeem_accounts()
                    extra = "\n🎁 Đã tự động đưa vào hệ thống lụm code!" if any(a.un.lower() == un.lower() for a in REDEEM_ACCOUNTS) else "\n⚠️ Chưa xác thực được trong danh sách redeem."
                else:
                    redeem_state["accounts"] = [a for a in redeem_state["accounts"] if a.get("un") != un]
                    extra = "\n⚠️ Đăng nhập game thành công nhưng chưa lưu được."
            else:
                extra = "\n🎁 Tài khoản đã có sẵn trong hệ thống."
            user_flows.pop(cid, None)
            try:
                bot.edit_message_text(f"✅ <b>ĐĂNG NHẬP THÀNH CÔNG</b>\n\n👤 Nickname: <b>{result['nickname']}</b>\n🔑 Tài khoản: <code>{un}</code>\n💰 Số dư: <b>{result['money']:,}</b>\n💾 Đã lưu tài khoản.{extra}", cid, status.message_id, parse_mode="HTML", reply_markup=dashboard_menu(cid))
            except Exception: pass
        threading.Thread(target=do_login_flow, daemon=True).start()
        return

    if flow == "user_del_acc":
        if not check_auth(cid):
            user_flows.pop(cid, None)
            return bot.send_message(cid, "🔒 Key chưa kích hoạt.", reply_markup=main_menu(cid))
        target_acc = value.strip().lower()
        before = len(redeem_state.get("accounts", []))
        redeem_state["accounts"] = [
            a for a in redeem_state.get("accounts", [])
            if not (
                (a.get("owner") == cid or (cid == ADMIN_ID and not a.get("owner")))
                and (a.get("un", "").lower() == target_acc or a.get("nick", "").lower() == target_acc)
            )
        ]
        if len(redeem_state["accounts"]) == before:
            return bot.send_message(cid, "❌ Không tìm thấy tài khoản trong danh sách của bạn.", reply_markup=dashboard_menu(cid))
        save_json(REDEEM_STATE_FILE, redeem_state); sync_redeem_accounts(); user_flows.pop(cid, None)
        return bot.send_message(cid, f"🗑️ Đã xoá tài khoản <b>{value}</b> khỏi hệ thống.", parse_mode="HTML", reply_markup=dashboard_menu(cid))

    if flow == "create_key_custom":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        try: days = int(value)
        except ValueError: return bot.send_message(cid, "⚠️ Hãy nhập số ngày dạng số nguyên.")
        if not 1 <= days <= 3650: return bot.send_message(cid, "⚠️ Số ngày từ 1 đến 3650.")
        key = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=10))
        valid_keys[key] = days; user_flows.pop(cid, None)
        exp = (datetime.now() + timedelta(days=days)).strftime("%d/%m/%Y %H:%M")
        return bot.send_message(cid, f"🔑 <b>KEY VIP MỚI ĐÃ TẠO</b>\n\n<code>{key}</code>\n⏳ Hạn: {days} ngày\n📅 Hết hạn: {exp}", parse_mode="HTML", reply_markup=admin_menu())

    if flow == "admin_del_acc":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        target_acc = value.strip().lower()
        before = len(redeem_state.get("accounts", []))
        redeem_state["accounts"] = [
            a for a in redeem_state.get("accounts", [])
            if not (a.get("un", "").lower() == target_acc or a.get("nick", "").lower() == target_acc)
        ]
        if len(redeem_state["accounts"]) == before:
            return bot.send_message(cid, "❌ Không tìm thấy tài khoản này trong hệ thống.", reply_markup=admin_menu())
        save_json(REDEEM_STATE_FILE, redeem_state); sync_redeem_accounts(); user_flows.pop(cid, None)
        return bot.send_message(cid, f"🗑️ Đã xoá tài khoản <b>{value}</b>.", parse_mode="HTML", reply_markup=admin_menu())

    if flow == "set_channel":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        target = value
        if "t.me/" in target: target = target.split("t.me/")[-1].strip("/").split("?")[0]
        if not target.startswith("@") and not target.lstrip("-").isdigit(): target = "@" + target
        status = bot.send_message(cid, f"🔄 Đang xác thực kênh {target}...")
        def resolve_channel():
            try:
                entity = tele_resolve_entity(target)
                peer_id = utils.get_peer_id(entity)
                redeem_state["channel_id"] = peer_id
                redeem_state["channel_name"] = getattr(entity, "title", None) or target
                save_json(REDEEM_STATE_FILE, redeem_state); user_flows.pop(cid, None)
                bot.edit_message_text(f"✅ <b>ĐÃ ĐẶT KÊNH THÀNH CÔNG</b>\n📡 {redeem_state['channel_name']}\n🆔 <code>{peer_id}</code>", cid, status.message_id, parse_mode="HTML", reply_markup=admin_menu())
            except Exception as exc:
                bot.edit_message_text(f"❌ Không thể đặt kênh: {exc}", cid, status.message_id, reply_markup=admin_menu())
        threading.Thread(target=resolve_channel, daemon=True).start()
        return

    if flow == "tele_phone":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        phone = value
        status = bot.send_message(cid, "📲 Đang gửi mã xác minh Telegram...")
        def send_otp():
            try:
                loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
                client = TelegramClient(StringSession(), TELETHON_API_ID, TELETHON_API_HASH, loop=loop)
                loop.run_until_complete(client.connect())
                sent = loop.run_until_complete(client.send_code_request(phone))
                tele_login_pending.update({"phone": phone, "hash": sent.phone_code_hash, "client": client, "loop": loop})
                user_flows[cid] = {"step": "tele_otp"}
                bot.edit_message_text("📲 Đã gửi mã xác minh. Hãy gửi mã OTP Telegram vào đây.", cid, status.message_id)
            except Exception as exc:
                user_flows.pop(cid, None)
                bot.edit_message_text(f"❌ Không gửi được OTP: {exc}", cid, status.message_id, reply_markup=tele_menu())
        threading.Thread(target=send_otp, daemon=True).start()
        return

    if flow == "tele_otp":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        pending = tele_login_pending
        if not pending.get("client"): user_flows.pop(cid, None); return bot.send_message(cid, "❌ Phiên không còn. Hãy kết nối lại.", reply_markup=tele_menu())
        status = bot.send_message(cid, "🔐 Đang xác minh OTP...")
        def verify_otp():
            client, loop = pending["client"], pending["loop"]
            try:
                loop.run_until_complete(client.sign_in(phone=pending["phone"], code=value, phone_code_hash=pending["hash"]))
                _save_tele_session(client.session.save())
                tele_login_pending.clear(); user_flows.pop(cid, None)
                bot.edit_message_text("✅ Đăng nhập Telegram thành công!", cid, status.message_id, reply_markup=tele_menu())
                _activate_telethon_client(client, loop)
            except Exception as exc:
                if "SessionPasswordNeeded" in type(exc).__name__ or "password" in str(exc).lower():
                    user_flows[cid] = {"step": "tele_password"}
                    bot.edit_message_text("🔐 Tài khoản có 2FA. Hãy gửi mật khẩu bảo mật Telegram.", cid, status.message_id)
                else:
                    bot.edit_message_text(f"❌ OTP chưa đúng: {exc}", cid, status.message_id, reply_markup=tele_menu())
        threading.Thread(target=verify_otp, daemon=True).start()
        return

    if flow == "tele_password":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        pending = tele_login_pending
        status = bot.send_message(cid, "🔐 Đang xác minh mật khẩu 2FA...")
        def verify_password():
            try:
                client, loop = pending["client"], pending["loop"]
                loop.run_until_complete(client.sign_in(password=value))
                _save_tele_session(client.session.save())
                tele_login_pending.clear(); user_flows.pop(cid, None)
                bot.edit_message_text("✅ Đăng nhập Telegram 2FA thành công!", cid, status.message_id, reply_markup=tele_menu())
                _activate_telethon_client(client, loop)
            except Exception as exc:
                bot.edit_message_text(f"❌ Xác minh 2FA thất bại: {exc}", cid, status.message_id, reply_markup=tele_menu())
        threading.Thread(target=verify_password, daemon=True).start()
        return

    user_flows.pop(cid, None)
    return bot.send_message(cid, "⚠️ Đã hết phiên thao tác.", reply_markup=dashboard_menu(cid) if check_auth(cid) else main_menu(cid))

def init_redeem_tokens():
    if not REDEEM_ACCOUNTS: return
    for acc in REDEEM_ACCOUNTS:
        try: acc.refresh_token()
        except: pass

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=init_redeem_tokens, daemon=True).start()
    if TELETHON_API_ID and TELETHON_API_HASH:
        threading.Thread(target=_start_telethon_listener, daemon=True).start()
    logger.info("🚀 𝐋Ụ𝐌 𝐂Ó𝐃𝐄 𝐋𝐂𝟕𝟗 - READY")
    
    if bot:
        while True:
            try: bot.infinity_polling(timeout=20, long_polling_timeout=30)
            except Exception as e: logger.error(f"Bot crashed: {e}"); time.sleep(5)
    else:
        while True: time.sleep(60)