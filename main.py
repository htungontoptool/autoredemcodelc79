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
def home(): return "🤖 AUTO LỤM CODE LC79 - ONLINE"
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
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "@admin")
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
        # Ẩn danh sách slash-command: thao tác chính được thực hiện bằng nút trong menu.
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
    """Ghi JSON an toàn: ghi file tạm rồi thay thế, trả True nếu lưu thành công."""
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

def require_auth(fn):
    def wr(m, *a, **kw):
        if not check_auth(m.chat.id): bot.reply_to(m, f"🔒 CHƯA KÍCH HOẠT\n👉 /nhapkey KEY\n📩 {ADMIN_USERNAME}"); return
        return fn(m, *a, **kw)
    return wr

def require_admin(fn):
    def wr(m, *a, **kw):
        if m.chat.id != ADMIN_ID: bot.reply_to(m, "⛔ CHỈ ADMIN"); return
        return fn(m, *a, **kw)
    return wr

def format_expire_time(ts):
    r = ts - time.time()
    if r <= 0: return "❌ HẾT HẠN"
    d = int(r // 86400); h = int((r % 86400) // 3600); m = int((r % 3600) // 60)
    return f"✅ CÒN {d}N {h}G {m}P" if d > 0 else f"✅ CÒN {h}G {m}P"

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

    def __init__(self, un, pw, nick):
        self.un = un; self.pw = pw; self.nick = nick
        self.used_codes = set(); self.lock = threading.Lock(); self.jwt = None; self.jwt_exp = 0; self.balance = 0

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

    def redeem_code(self, code, _attempt=0):
        with self.lock:
            if code in self.used_codes: return
            self.used_codes.add(code)
            
        if not self.is_token_valid():
            if not self.refresh_token(): append_redeem_log(code, self.nick, self.balance, None, 0, "LOGIN FAIL"); return
            
        bal_before = self.balance
        last_error = "UNKNOWN"
        
        # VÒNG LẶP 10 LẦN BẤT CHẤP LỖI
        for retry_count in range(10):
            try:
                with requests.Session() as s:
                    res = s.post(self.BASE_URL + self.PARAMS, headers=self._headers(self.jwt), json={"code": code.strip(), "captcha": ""}, timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT))
                try: data = res.json()
                except: data = {}
                
                if res.status_code in (200, 201):
                    amount = (data.get("money") or data.get("amount") or data.get("reward") or (data.get("data") or {}).get("money") or 0)
                    try: amount = int(amount)
                    except: amount = 0
                    self.refresh_token(); bal_after = self.balance
                    append_redeem_log(code, self.nick, bal_before, bal_after, amount, "SUCCESS")
                    return 
                    
                if res.status_code == 401:
                    self.refresh_token()
                    last_error = "TOKEN EXPIRED"
                else:
                    last_error = data.get("message", f"HTTP {res.status_code}") if isinstance(data, dict) else f"HTTP {res.status_code}"

            except Exception as e:
                last_error = "TIMEOUT/NETWORK"
            
            if retry_count < 9: time.sleep(1)

        append_redeem_log(code, self.nick, bal_before, None, 0, f"FAIL (10 LAN): {last_error}")

def build_redeem_accounts():
    accounts = redeem_state.get("accounts", []) if isinstance(redeem_state, dict) else []
    result = []
    for item in accounts:
        if not isinstance(item, dict):
            continue
        un = str(item.get("un") or item.get("username") or "").strip()
        pw = str(item.get("pw") or item.get("password") or "").strip()
        nick = str(item.get("nick") or item.get("nickname") or un).strip()
        if un and pw:
            result.append(RedeemAccount(un, pw, nick or un))
    return result

def sync_redeem_accounts():
    """Đồng bộ lại danh sách acc từ JSON trước khi hiển thị/chạy redeem."""
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

def redeem_all_threaded(code):
    if not REDEEM_ACCOUNTS: logger.warning("Chua co acc redeem"); return
    def worker():
        for acc in REDEEM_ACCOUNTS:
            if not acc.is_token_valid(): acc.refresh_token()
        threads = []
        for i, acc in enumerate(REDEEM_ACCOUNTS):
            def w(a=acc, idx=i):
                if idx > 0: time.sleep(idx * 1.0 + random.uniform(0, 0.3))
                a.redeem_code(code)
            t = threading.Thread(target=w, daemon=True); t.start(); threads.append(t)
        for t in threads: t.join(timeout=120)
        recent = [l for l in redeem_logs if l["code"] == code]
        ok = sum(1 for l in recent if l["status"] == "SUCCESS")
        total = sum(l["amount"] for l in recent if l["status"] == "SUCCESS")
        try: bot.send_message(ADMIN_ID, f"🎁 <b>REDEEM XONG</b>\n📝 Code: <code>{code}</code>\n✅ OK: {ok}/{len(REDEEM_ACCOUNTS)}\n💰 Tổng nhận: <b>{total:,}</b>", parse_mode="HTML")
        except: pass
    threading.Thread(target=worker, daemon=True).start()

# ========================= LOGIN & ADD ACC GỘP =========================
def login_and_get_info(u, p):
    try:
        pw_hashed = normalize_pw(p)
        r = requests.get(f"https://apifo88daigia.tele68.com/api?c=3&un={u}&pw={pw_hashed}&cp=R&cl=R&pf=web&at=", timeout=12).json()
        if not r.get("success"): return {"_error": "Sai Tài Khoản/Mật Khẩu"}
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


def _activate_telethon_client(client, loop):
    """Kích hoạt ngay client vừa đăng nhập; không cần restart bot."""
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
                logger.error("Tele session chưa được Telegram xác thực.")
                return
            me = await client.get_me()
            tele_ready = True
            logger.info("TELE ONLINE sau đăng nhập: @%s", me.username or me.first_name)
            try:
                bot.send_message(ADMIN_ID, f"✅ TELE ONLINE @{me.username or me.first_name}", parse_mode="HTML")
            except Exception:
                pass
            await client.run_until_disconnected()
        except Exception as exc:
            tele_ready = False
            logger.exception("Tele listener lỗi: %s", exc)

    # Chạy listener trên đúng event loop đã dùng để gửi OTP/xác minh.
    loop.run_until_complete(_keep_running())

def extract_clean_code(raw_text):
    """Bắt mã code sau 'Mã code là:' và tự động bỏ chữ LC79 ở đầu nếu có."""
    if not raw_text:
        return None, None
    match = re.search(r"Mã code là:\s*([A-Z0-9]+)", raw_text, re.IGNORECASE)
    if not match:
        return None, None
    raw_code = match.group(1).strip().upper()
    # Bỏ tiền tố LC79 ở đầu (ví dụ: LC79CJBKQLAQI -> CJBKQLAQI)
    clean_code = re.sub(r"^LC79+", "", raw_code) or raw_code
    return raw_code, clean_code

def _handle_tele_message(event):
    if not redeem_state["enabled"] or redeem_state["channel_id"] is None: return
    if event.chat_id != redeem_state["channel_id"]: return
    text = event.raw_text or ""
    raw_code, clean_code = extract_clean_code(text)
    if not clean_code: return
    logger.info(f"[TELE] Raw: {raw_code} -> Clean: {clean_code}")
    try:
        note = f" (bỏ LC79 từ <code>{raw_code}</code>)" if raw_code != clean_code else ""
        bot.send_message(ADMIN_ID, f"🎁 CODE MỚI\n📝 <code>{clean_code}</code>{note}\n🚀 Redeem {len(REDEEM_ACCOUNTS)} acc...", parse_mode="HTML")
    except: pass
    # Chỉ nạp mã đã bỏ chữ LC79 ở đầu
    redeem_all_threaded(clean_code)

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
            try: bot.send_message(ADMIN_ID, f"✅ TELE ONLINE @{me.username or me.first_name}", parse_mode="HTML")
            except: pass
            await tele_client.run_until_disconnected()
        except Exception as e: logger.error(f"Tele run: {e}")
    tele_loop.run_until_complete(_run())

def tele_resolve_entity(target, timeout=15):
    if tele_client is None or not tele_ready: raise RuntimeError("Tele chua san sang")
    f = asyncio.run_coroutine_threadsafe(tele_client.get_entity(target), tele_loop)
    return f.result(timeout=timeout)

# ========================= UI MENU (THAY MÀN HÌNH, KHÔNG CHỒNG TIN) =========================
BOT_NAME = "Auto Lụm Code LC79"
user_flows = {}
SCREEN_MESSAGES = {}


def _btn(text, data):
    return telebot.types.InlineKeyboardButton(text, callback_data=data)


def replace_screen(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    """Xoá màn hình cũ rồi gửi màn hình mới; fallback edit nếu Telegram không cho xoá."""
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        try:
            msg = bot.edit_message_text(text, chat_id, message_id, parse_mode=parse_mode,
                                        reply_markup=reply_markup, disable_web_page_preview=True)
            SCREEN_MESSAGES[chat_id] = message_id
            return msg
        except Exception:
            pass
    msg = bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup,
                           disable_web_page_preview=True)
    SCREEN_MESSAGES[chat_id] = msg.message_id
    return msg


def show_screen(chat_id, message_id, text, keyboard=None, parse_mode=None):
    return replace_screen(chat_id, message_id, text, keyboard, parse_mode)


def flow_prompt(chat_id, message_id, flow, title, detail):
    user_flows[chat_id] = {"step": flow}
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(_btn("✖️ Huỷ thao tác", "flow_cancel"))
    return show_screen(chat_id, message_id, f"{title}\n\n{detail}\n\n<i>Gửi nội dung vào khung chat bên dưới.</i>", kb, "HTML")


def main_menu(cid=None):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🔐 ĐĂNG NHẬP GAME", "login"), _btn("🔑 KÍCH HOẠT KEY", "nhapkey"))
    kb.add(_btn("💎 TÀI KHOẢN / HẠN DÙNG", "tt"))
    if cid == ADMIN_ID:
        kb.add(_btn("👑 QUẢN TRỊ", "admin_panel"))
    return kb


def dashboard_menu(cid=None):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🔐 ĐĂNG NHẬP GAME", "login"), _btn("💎 THÔNG TIN", "tt"))
    if cid == ADMIN_ID:
        kb.add(_btn("🎛️ QUẢN LÝ HỆ THỐNG", "admin_panel"))
    return kb


def admin_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🎁 REDEEM TỰ ĐỘNG", "rd_panel"), _btn("👥 TÀI KHOẢN", "data_panel"))
    kb.add(_btn("🔑 TẠO KEY VIP", "create_key"), _btn("📡 TELEGRAM NGUỒN", "tele_panel"))
    kb.add(_btn("📄 XUẤT TXT TỔNG HỢP", "export_txt"))
    kb.add(_btn("🔄 LÀM MỚI MENU", "admin_refresh"))
    kb.add(_btn("⬅️ MENU CHÍNH", "home"))
    return kb


def redeem_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    toggle = "🟢 TẮT AUTO REDEEM" if redeem_state.get("enabled") else "⚪ BẬT AUTO REDEEM"
    kb.add(_btn(toggle, "rd_toggle"), _btn("📡 ĐẶT KÊNH CODE", "rd_setchannel"))
    kb.add(_btn("➕ THÊM TÀI KHOẢN", "rd_addacc"), _btn("➖ XOÁ TÀI KHOẢN", "rd_delacc"))
    kb.add(_btn("📋 DANH SÁCH ACC", "rd_list"), _btn("🧪 TEST CODE", "rd_test"))
    kb.add(_btn("🔄 LÀM MỚI TOKEN", "rd_refresh"), _btn("🧹 XOÁ LOG", "rd_clear_confirm"))
    kb.add(_btn("🔄 LÀM MỚI", "rd_panel"))
    kb.add(_btn("⬅️ QUẢN TRỊ", "admin_panel"))
    return kb


def data_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🎁 ACC REDEEM", "rd_list"), _btn("🎮 GAME ACC", "ga_status"))
    kb.add(_btn("🧹 XOÁ GAME ACC", "ga_clear_confirm"))
    kb.add(_btn("🔄 LÀM MỚI", "data_panel"))
    kb.add(_btn("⬅️ QUẢN TRỊ", "admin_panel"))
    return kb


def tele_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🔐 KẾT NỐI / ĐĂNG NHẬP", "tele_login"), _btn("📊 TRẠNG THÁI", "tele_status_btn"))
    kb.add(_btn("🗑️ ĐĂNG XUẤT SESSION", "tele_logout_confirm"))
    kb.add(_btn("🔄 LÀM MỚI TRẠNG THÁI", "tele_panel"))
    kb.add(_btn("⬅️ QUẢN TRỊ", "admin_panel"))
    return kb


def send_redeem_log_file(chat_id):
    if not redeem_logs:
        return bot.send_message(chat_id, "📭 Chưa có log redeem nào.")
    content = "=" * 60 + "\n AUTO LỤM CODE LC79 - REDEEM LOG\n" + "=" * 60 + "\n"
    ok = sum(1 for l in redeem_logs if l.get("status") == "SUCCESS")
    total_amount = sum(int(l.get("amount", 0) or 0) for l in redeem_logs if l.get("status") == "SUCCESS")
    content += f"Thời gian xuất: {datetime.now():%d/%m/%Y %H:%M:%S}\nTổng log: {len(redeem_logs)}\nThành công: {ok}\nThất bại: {len(redeem_logs)-ok}\nTổng nhận: {total_amount:,}\n" + "=" * 60 + "\n\n"
    for i, log in enumerate(redeem_logs, 1):
        content += (f"[{i}] {log.get('time','')}\nCode: {log.get('code','')}\nAccount: {log.get('account','')}\n"
                    f"Số dư trước: {int(log.get('balance_before',0) or 0):,}\nSố dư sau: {int(log.get('balance_after',0) or 0):,}\n"
                    f"Tiền nhận: {int(log.get('amount',0) or 0):,}\nTrạng thái: {log.get('status','')}\n" + "-" * 60 + "\n")
    bio = io.BytesIO(content.encode("utf-8")); bio.name = f"lc79_redeem_log_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(chat_id, bio, caption=f"📊 {len(redeem_logs)} log | ✅ {ok} thành công | 💰 {total_amount:,}")


def send_game_accounts_file(chat_id):
    # Hàm tương thích ngược; file tổng hợp mới được dùng trong giao diện.
    if not game_accounts:
        return bot.send_message(chat_id, "📭 Chưa có tài khoản game nào.")
    content = "AUTO LỤM CODE LC79 - GAME ACCOUNTS\n" + "=" * 60 + "\n"
    for i, (key, info) in enumerate(game_accounts.items(), 1):
        content += (f"[{i}] Nickname: {info.get('nickname','')}\nUsername: {info.get('username','')}\n"
                    f"Số dư: {int(info.get('money',0) or 0):,}\nĐăng nhập: {info.get('login_time','')}\n" + "-" * 60 + "\n")
    bio = io.BytesIO(content.encode("utf-8")); bio.name = f"lc79_game_accounts_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(chat_id, bio, caption=f"🎮 {len(game_accounts)} tài khoản game")


def send_all_export_file(chat_id):
    """Một file TXT duy nhất: tổng quan, acc redeem, game acc và lịch sử; không xuất token/mật khẩu."""
    if chat_id != ADMIN_ID:
        return bot.send_message(chat_id, "⛔ Chức năng này chỉ dành cho quản trị viên.")
    ok = sum(1 for item in redeem_logs if item.get("status") == "SUCCESS")
    amount = sum(int(item.get("amount", 0) or 0) for item in redeem_logs if item.get("status") == "SUCCESS")
    lines = [
        "AUTO LỤM CODE LC79 - BAO CAO TONG HOP", "=" * 72,
        f"Thoi gian xuat: {datetime.now():%d/%m/%Y %H:%M:%S}",
        f"Auto redeem: {'BAT' if redeem_state.get('enabled') else 'TAT'}",
        f"Kenh nguon: {redeem_state.get('channel_name') or 'Chua cai dat'}",
        f"Tong acc redeem: {len(REDEEM_ACCOUNTS)}",
        f"Tong game account: {len(game_accounts)}",
        f"Tong log: {len(redeem_logs)} | Thanh cong: {ok} | That bai: {len(redeem_logs)-ok}",
        f"Tong tien nhan ghi nhan: {amount:,}", "=" * 72, "", "[DANH SACH ACC REDEEM]"
    ]
    for i, acc in enumerate(REDEEM_ACCOUNTS, 1):
        lines.append(f"{i}. Nickname: {acc.nick} | Username: {acc.un} | Token: {'VALID' if acc.is_token_valid() else 'NEED REFRESH'}")
    lines += ["", "[DANH SACH GAME ACCOUNT]"]
    for i, (key, info) in enumerate(game_accounts.items(), 1):
        lines.append(f"{i}. Nickname: {info.get('nickname','')} | Username: {info.get('username','')} | So du: {int(info.get('money',0) or 0):,} | Login: {info.get('login_time','')}")
    lines += ["", "[LICH SU REDEEM]"]
    for i, item in enumerate(redeem_logs, 1):
        lines.append(f"{i}. {item.get('time','')} | Code: {item.get('code','')} | Account: {item.get('account','')} | Amount: {int(item.get('amount',0) or 0):,} | Status: {item.get('status','')}")
    lines += ["", "Ghi chu: Bao cao khong chua mat khau hoac access token de tranh lo thong tin dang nhap."]
    bio = io.BytesIO("\n".join(lines).encode("utf-8"))
    bio.name = f"auto_lum_code_lc79_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(chat_id, bio, caption=f"📄 Báo cáo tổng hợp | {len(REDEEM_ACCOUNTS)} acc redeem | {len(game_accounts)} game acc | {len(redeem_logs)} log")


@bot.message_handler(commands=['start', 'menu'])
def cmd_start(m):
    cid = m.chat.id
    try: bot.delete_message(cid, m.message_id)
    except Exception: pass
    old_id = SCREEN_MESSAGES.get(cid)
    if old_id:
        try: bot.delete_message(cid, old_id)
        except Exception: pass
    if check_auth(cid):
        han = "👑 QUẢN TRỊ VIÊN" if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
        msg = bot.send_message(cid, f"🤖 <b>{BOT_NAME}</b>\n✨ Trung tâm điều khiển LC79\n⏳ {han}\n\nChọn chức năng:", parse_mode="HTML", reply_markup=dashboard_menu(cid))
    else:
        msg = bot.send_message(cid, f"🤖 <b>{BOT_NAME}</b>\n\n🔒 Tài khoản chưa kích hoạt.\nChọn đăng nhập hoặc kích hoạt key:", parse_mode="HTML", reply_markup=main_menu(cid))
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
        return flow_prompt(cid, c.message.message_id, "login", "🔐 ĐĂNG NHẬP GAME", "Gửi theo mẫu: <code>TÀI_KHOẢN MẬT_KHẨU</code>\nĐăng nhập thành công sẽ tự lưu tài khoản và thêm vào danh sách redeem.")

    if data == "nhapkey":
        return flow_prompt(cid, c.message.message_id, "key", "🔑 KÍCH HOẠT KEY", "Gửi mã key VIP của bạn.")

    if data == "tt":
        if not check_auth(cid): return go("🔒 Tài khoản chưa kích hoạt.", main_menu(cid))
        han = "👑 QUẢN TRỊ VIÊN" if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
        return go(f"💎 <b>THÔNG TIN TÀI KHOẢN</b>\n\n⏳ Trạng thái: {han}\n🎮 Game account đã lưu: {sum(1 for x in game_accounts.values() if x.get('source') == str(cid))}", dashboard_menu(cid), "HTML")

    if data == "admin_panel":
        sync_redeem_accounts()
        if cid != ADMIN_ID: return go("⛔ Bạn không có quyền quản trị.", dashboard_menu(cid) if check_auth(cid) else main_menu(cid))
        return go(f"👑 <b>TRUNG TÂM QUẢN TRỊ</b>\n\n🎁 Acc redeem: <b>{len(REDEEM_ACCOUNTS)}</b>\n🎮 Game account: <b>{len(game_accounts)}</b>\n📝 Lịch sử: <b>{len(redeem_logs)}</b>\n⚙️ Auto redeem: <b>{'🟢 ĐANG BẬT' if redeem_state.get('enabled') else '🔴 ĐANG TẮT'}</b>", admin_menu(), "HTML")

    if data == "admin_refresh":
        sync_redeem_accounts()
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        return go(f"👑 <b>TRUNG TÂM QUẢN TRỊ</b>\n\n🎁 Acc redeem: <b>{len(REDEEM_ACCOUNTS)}</b>\n🎮 Game account: <b>{len(game_accounts)}</b>\n📝 Lịch sử: <b>{len(redeem_logs)}</b>\n⚙️ Auto redeem: <b>{'🟢 ĐANG BẬT' if redeem_state.get('enabled') else '🔴 ĐANG TẮT'}</b>", admin_menu(), "HTML")

    if data == "create_key":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        return flow_prompt(cid, c.message.message_id, "create_key", "🔑 TẠO KEY VIP", "Nhập số ngày sử dụng, ví dụ: <code>30</code>.")

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
            return go("⚠️ Chưa có tài khoản redeem. Hãy thêm tài khoản trước.", redeem_menu())
        elif redeem_state.get("channel_id") is None:
            return go("⚠️ Chưa thiết lập kênh nhận code. Hãy đặt kênh trước.", redeem_menu())
        else:
            redeem_state["enabled"] = True
        save_json(REDEEM_STATE_FILE, redeem_state)
        text = ("🎁 <b>QUẢN LÝ REDEEM</b>\n\n"
                f"⚙️ Auto: {'🟢 BẬT' if redeem_state['enabled'] else '🔴 TẮT'}\n"
                f"📡 Kênh: {redeem_state.get('channel_name') or 'Chưa thiết lập'}\n👥 Tài khoản: {len(REDEEM_ACCOUNTS)}\n📝 Lịch sử: {len(redeem_logs)}")
        return go(text, redeem_menu(), "HTML")

    if data == "rd_setchannel":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return flow_prompt(cid, c.message.message_id, "set_channel", "📡 THIẾT LẬP KÊNH CODE", "Gửi @username, link t.me/... hoặc ID kênh. Tài khoản Telegram nguồn phải được đăng nhập ở mục Telegram nguồn.")

    if data == "rd_addacc":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return flow_prompt(cid, c.message.message_id, "add_redeem", "➕ THÊM TÀI KHOẢN REDEEM", "Gửi theo mẫu: <code>USERNAME PASSWORD NICKNAME</code>\nNickname không nên chứa dấu cách.")

    if data == "rd_delacc":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return flow_prompt(cid, c.message.message_id, "del_redeem", "➖ XOÁ TÀI KHOẢN REDEEM", "Gửi chính xác nickname của tài khoản cần xoá.")

    if data == "rd_test":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return flow_prompt(cid, c.message.message_id, "test_redeem", "🧪 KIỂM TRA CODE", "Gửi mã code muốn chạy thử trên danh sách tài khoản hiện tại.")

    if data == "rd_list":
        sync_redeem_accounts()
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        if not REDEEM_ACCOUNTS: return go("📭 Chưa có tài khoản redeem.", redeem_menu())
        lines = [f"{i}. {'🟢' if a.is_token_valid() else '⚪'} <b>{a.nick}</b> · <code>{a.un}</code> · {a.balance:,}" for i, a in enumerate(REDEEM_ACCOUNTS, 1)]
        return go("📋 <b>DANH SÁCH ACC REDEEM</b>\n\n" + "\n".join(lines), redeem_menu(), "HTML")

    if data == "rd_clear_confirm":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        kb = telebot.types.InlineKeyboardMarkup(row_width=2)
        kb.add(_btn("🗑️ XÁC NHẬN XOÁ", "rd_clear_yes"), _btn("↩️ GIỮ LẠI", "rd_panel"))
        return go(f"⚠️ Xoá toàn bộ {len(redeem_logs)} dòng log? Thao tác này không thể hoàn tác.", kb)

    if data == "rd_clear_yes":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        redeem_logs = []; save_json(REDEEM_LOGS_FILE, redeem_logs)
        return go("✅ Đã xoá toàn bộ lịch sử redeem.", redeem_menu())

    if data == "rd_refresh":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        msg = go("🔄 Đang làm mới token cho các tài khoản...", redeem_menu())
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

    if data == "ga_clear_confirm":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        kb = telebot.types.InlineKeyboardMarkup(row_width=2)
        kb.add(_btn("🗑️ XÁC NHẬN XOÁ", "ga_clear_yes"), _btn("↩️ GIỮ LẠI", "data_panel"))
        return go(f"⚠️ Xoá toàn bộ {len(game_accounts)} game account đã lưu?", kb)

    if data == "ga_clear_yes":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        game_accounts = {}; save_json(GAME_ACCOUNTS_FILE, game_accounts)
        return go("✅ Đã xoá danh sách game account.", data_menu())

    if data == "export_txt":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", dashboard_menu(cid))
        try:
            bot.delete_message(cid, c.message.message_id)
        except Exception: pass
        send_all_export_file(cid)
        msg = bot.send_message(cid, "📦 Đã xuất TXT tổng hợp.", reply_markup=admin_menu())
        SCREEN_MESSAGES[cid] = msg.message_id
        return

    if data == "tele_panel":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        saved = bool(_load_tele_session())
        status = "🟢 ĐÃ ĐĂNG NHẬP & ĐANG THEO DÕI" if tele_ready else ("🟡 CÓ SESSION, CHƯA KẾT NỐI" if saved else "🔴 CHƯA ĐĂNG NHẬP")
        return go(f"📡 <b>TELEGRAM NGUỒN</b>\n\nTrạng thái: {status}\nKênh code: {redeem_state.get('channel_name') or 'Chưa cài đặt'}\n\nDùng tài khoản Telegram của bạn để theo dõi kênh code.", tele_menu(), "HTML")

    if data == "tele_login":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        return flow_prompt(cid, c.message.message_id, "tele_phone", "🔐 KẾT NỐI TELEGRAM", "Gửi số điện thoại có mã quốc gia, ví dụ <code>+849xxxxxxxx</code>.")

    if data == "tele_status_btn":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        saved = bool(_load_tele_session())
        return go(f"📡 <b>TRẠNG THÁI TELEGRAM</b>\n\nAPI ID: {'✅' if TELETHON_API_ID else '❌'}\nAPI HASH: {'✅' if TELETHON_API_HASH else '❌'}\nSession: {'✅' if saved else '❌'}\nClient: {'🟢 ONLINE' if tele_ready else '🔴 OFFLINE'}\nKênh: {redeem_state.get('channel_name') or 'Chưa cài đặt'}", tele_menu(), "HTML")

    if data == "tele_logout_confirm":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        kb = telebot.types.InlineKeyboardMarkup(row_width=2)
        kb.add(_btn("🗑️ XOÁ SESSION", "tele_logout_yes"), _btn("↩️ QUAY LẠI", "tele_panel"))
        return go("⚠️ Xoá session Telegram đã lưu? Bạn sẽ cần đăng nhập lại.", kb)

    if data == "tele_logout_yes":
        if cid != ADMIN_ID: return go("⛔ Chỉ admin.", admin_menu())
        if os.path.exists(TELE_SESSION_FILE): os.remove(TELE_SESSION_FILE)
        return go("✅ Đã xoá session Telegram. Khởi động lại bot để áp dụng.", tele_menu())

    return go("Chức năng không tồn tại hoặc đã được cập nhật.", dashboard_menu(cid) if check_auth(cid) else main_menu(cid))


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
            return bot.send_message(cid, f"✅ <b>KÍCH HOẠT THÀNH CÔNG</b>\n⏳ Hạn dùng: {expiry}", parse_mode="HTML", reply_markup=dashboard_menu(cid))
        return bot.send_message(cid, "❌ Key không hợp lệ hoặc đã được sử dụng. Hãy kiểm tra và gửi lại key.", reply_markup=main_menu(cid))

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
            save_game_account(cid, un, result["nickname"], result["token"], result["money"], source="user")
            exists = any(a.get("un") == un for a in redeem_state["accounts"])
            if not exists:
                redeem_state.setdefault("accounts", []).append({"un": un, "pw": result["pw_hashed"], "nick": result["nickname"]})
                if save_json(REDEEM_STATE_FILE, redeem_state):
                    sync_redeem_accounts()
                    extra = "\n🎁 Đã lưu và đồng bộ vào danh sách redeem." if any(a.un.lower() == un.lower() for a in REDEEM_ACCOUNTS) else "\n⚠️ Không xác minh được acc trong danh sách redeem."
                else:
                    redeem_state["accounts"] = [a for a in redeem_state["accounts"] if a.get("un") != un]
                    extra = "\n⚠️ Đăng nhập game thành công nhưng chưa lưu được acc redeem."
            else:
                extra = "\n🎁 Tài khoản đã có trong danh sách redeem."
            user_flows.pop(cid, None)
            try:
                bot.edit_message_text(f"✅ <b>ĐĂNG NHẬP THÀNH CÔNG</b>\n\n👤 Nickname: <b>{result['nickname']}</b>\n🔑 Tài khoản: <code>{un}</code>\n💰 Số dư: <b>{result['money']:,}</b>\n💾 Đã lưu tài khoản.{extra}", cid, status.message_id, parse_mode="HTML", reply_markup=dashboard_menu(cid))
            except Exception: pass
        threading.Thread(target=do_login_flow, daemon=True).start()
        return

    if flow == "create_key":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        try: days = int(value)
        except ValueError: return bot.send_message(cid, "⚠️ Hãy nhập số ngày dạng số, ví dụ 30.")
        if not 1 <= days <= 3650: return bot.send_message(cid, "⚠️ Số ngày hợp lệ từ 1 đến 3650.")
        key = "VIP-" + "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=10))
        valid_keys[key] = days; user_flows.pop(cid, None)
        expiry = (datetime.now() + timedelta(days=days)).strftime("%d/%m/%Y %H:%M")
        return bot.send_message(cid, f"🔑 <b>KEY MỚI ĐÃ TẠO</b>\n\n<code>{key}</code>\n⏳ Thời hạn: {days} ngày\n📅 Hết hạn: {expiry}", parse_mode="HTML", reply_markup=admin_menu())

    if flow == "add_redeem":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        parts = value.split(maxsplit=2)
        if len(parts) < 3: return bot.send_message(cid, "⚠️ Gửi đúng mẫu: <code>USERNAME PASSWORD NICKNAME</code>", parse_mode="HTML")
        un, pw, nick = parts
        if any(a.get("nick") == nick or a.get("un") == un for a in redeem_state["accounts"]):
            return bot.send_message(cid, "⚠️ Nickname hoặc username đã tồn tại. Gửi tài khoản khác.")
        redeem_state.setdefault("accounts", [])
        new_account = {"un": un.strip(), "pw": normalize_pw(pw), "nick": nick.strip()}
        redeem_state["accounts"].append(new_account)
        if not save_json(REDEEM_STATE_FILE, redeem_state):
            redeem_state["accounts"].pop()
            return bot.send_message(cid, "❌ Không lưu được tài khoản vào dữ liệu. Kiểm tra quyền ghi thư mục DATA_DIR trên Render rồi thử lại.", reply_markup=redeem_menu())
        # Đọc lại từ file để chắc chắn tài khoản đã thực sự được lưu.
        sync_redeem_accounts()
        saved = any(a.un.lower() == un.strip().lower() for a in REDEEM_ACCOUNTS)
        if not saved:
            user_flows.pop(cid, None)
            return bot.send_message(cid, "⚠️ Đã gửi lệnh lưu nhưng không tìm thấy tài khoản trong file dữ liệu. Hãy kiểm tra DATA_DIR.", reply_markup=redeem_menu())
        user_flows.pop(cid, None)
        return bot.send_message(cid, f"✅ ĐÃ LƯU ACC REDEEM: <b>{nick}</b>\n👥 Tổng tài khoản: <b>{len(REDEEM_ACCOUNTS)}</b>\n📁 File: <code>{REDEEM_STATE_FILE}</code>", parse_mode="HTML", reply_markup=redeem_menu())

    if flow == "del_redeem":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        before = len(redeem_state["accounts"])
        redeem_state["accounts"] = [a for a in redeem_state["accounts"] if a.get("nick", "") != value]
        if len(redeem_state["accounts"]) == before:
            return bot.send_message(cid, "❌ Không tìm thấy nickname này. Hãy gửi lại chính xác.")
        save_json(REDEEM_STATE_FILE, redeem_state); REDEEM_ACCOUNTS = build_redeem_accounts(); user_flows.pop(cid, None)
        return bot.send_message(cid, f"🗑️ Đã xoá {value}. Còn {len(REDEEM_ACCOUNTS)} tài khoản.", reply_markup=redeem_menu())

    if flow == "test_redeem":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        if not REDEEM_ACCOUNTS: return bot.send_message(cid, "⚠️ Chưa có tài khoản redeem.", reply_markup=redeem_menu())
        user_flows.pop(cid, None)
        raw_test = value.strip().upper()
        clean_test = re.sub(r"^LC79+", "", raw_test) or raw_test
        bot.send_message(cid, f"🧪 Đã nhận code <code>{clean_test}</code>. Bắt đầu xử lý {len(REDEEM_ACCOUNTS)} tài khoản.", parse_mode="HTML", reply_markup=redeem_menu())
        return redeem_all_threaded(clean_test)

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
                bot.edit_message_text(f"✅ <b>ĐÃ THIẾT LẬP KÊNH</b>\n📡 {redeem_state['channel_name']}\n🆔 <code>{peer_id}</code>", cid, status.message_id, parse_mode="HTML", reply_markup=redeem_menu())
            except Exception as exc:
                bot.edit_message_text(f"❌ Không thể thiết lập kênh: {exc}\nKiểm tra đăng nhập Telegram nguồn và quyền truy cập kênh.", cid, status.message_id, reply_markup=redeem_menu())
        threading.Thread(target=resolve_channel, daemon=True).start()
        return

    if flow == "tele_phone":
        if cid != ADMIN_ID: user_flows.pop(cid, None); return
        if not TELETHON_API_ID or not TELETHON_API_HASH:
            user_flows.pop(cid, None)
            return bot.send_message(cid, "❌ Thiếu TELETHON_API_ID hoặc TELETHON_API_HASH trong biến môi trường.", reply_markup=tele_menu())
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
        if not pending.get("client"): user_flows.pop(cid, None); return bot.send_message(cid, "❌ Phiên đăng nhập không còn. Hãy kết nối lại.", reply_markup=tele_menu())
        status = bot.send_message(cid, "🔐 Đang xác minh OTP...")
        def verify_otp():
            client, loop = pending["client"], pending["loop"]
            try:
                loop.run_until_complete(client.sign_in(phone=pending["phone"], code=value, phone_code_hash=pending["hash"]))
                _save_tele_session(client.session.save())
                tele_login_pending.clear(); user_flows.pop(cid, None)
                bot.edit_message_text("✅ Đăng nhập Telegram thành công. Đang bật theo dõi kênh ngay...", cid, status.message_id, reply_markup=tele_menu())
                _activate_telethon_client(client, loop)
            except Exception as exc:
                if "SessionPasswordNeeded" in type(exc).__name__ or "password" in str(exc).lower():
                    user_flows[cid] = {"step": "tele_password"}
                    bot.edit_message_text("🔐 Tài khoản bật xác minh 2 bước. Hãy gửi mật khẩu 2FA Telegram.", cid, status.message_id)
                else:
                    bot.edit_message_text(f"❌ OTP chưa hợp lệ: {exc}", cid, status.message_id, reply_markup=tele_menu())
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
                bot.edit_message_text("✅ Đăng nhập Telegram thành công. Đang bật theo dõi kênh ngay...", cid, status.message_id, reply_markup=tele_menu())
                _activate_telethon_client(client, loop)
            except Exception as exc:
                bot.edit_message_text(f"❌ Xác minh 2FA thất bại: {exc}", cid, status.message_id, reply_markup=tele_menu())
        threading.Thread(target=verify_password, daemon=True).start()
        return

    user_flows.pop(cid, None)
    return bot.send_message(cid, "⚠️ Phiên thao tác đã kết thúc. Mở menu để tiếp tục.", reply_markup=dashboard_menu(cid) if check_auth(cid) else main_menu(cid))


@require_auth
def lg(m):
    p = m.text.split()
    if len(p) != 3: return bot.reply_to(m, "📝 Cú pháp: <code>/login TAIKHOAN MATKHAU</code>", parse_mode="HTML")
    un, pw = p[1], p[2]
    mm = bot.reply_to(m, "🔄 Đang kết nối và kiểm tra tài khoản...")
    
    def do_login():
        res = login_and_get_info(un, pw)
        if "_error" in res:
            return bot.edit_message_text(f"❌ LỖI: {res['_error']}", m.chat.id, mm.message_id)
        
        save_game_account(m.chat.id, un, res["nickname"], res["token"], res["money"], source="user")
        
        global REDEEM_ACCOUNTS
        hashed_pw = res["pw_hashed"]
        is_exist = any(a["un"] == un for a in redeem_state["accounts"])
        msg_add = ""
        if not is_exist:
            redeem_state["accounts"].append({"un": un, "pw": hashed_pw, "nick": res["nickname"]})
            save_json(REDEEM_STATE_FILE, redeem_state)
            REDEEM_ACCOUNTS = build_redeem_accounts()
            msg_add = "\n🎁 <i>Đã tự động thêm vào DS Redeem!</i>"
        else:
            msg_add = "\n🎁 <i>Acc này đã có trong DS Redeem.</i>"

        bot.edit_message_text(
            f"✅ <b>ĐĂNG NHẬP THÀNH CÔNG</b>\n"
            f"👤 Nickname: <b>{res['nickname']}</b>\n"
            f"🔑 Tài khoản: <code>{un}</code>\n"
            f"💰 Số dư: <b>{res['money']:,}</b>\n"
            f"💾 Đã lưu vào DS Game Accounts{msg_add}",
            m.chat.id, mm.message_id, parse_mode="HTML", reply_markup=dashboard_menu(m.chat.id)
        )
    threading.Thread(target=do_login, daemon=True).start()

def ak(m):
    p = m.text.split()
    if len(p) < 2: return bot.reply_to(m, "🔑 Cú pháp: /nhapkey VIP-XXX")
    k = p[1].upper()
    if k in valid_keys:
        authorized_users[m.chat.id] = time.time() + valid_keys[k] * 86400; del valid_keys[k]
        ht = datetime.fromtimestamp(authorized_users[m.chat.id]).strftime("%d/%m/%Y %H:%M")
        bot.reply_to(m, f"✅ KÍCH HOẠT VIP THÀNH CÔNG\n⏳ Hết hạn: {ht}", parse_mode="HTML", reply_markup=dashboard_menu(m.chat.id))
    else: bot.reply_to(m, f"❌ KEY SAI\n📩 {ADMIN_USERNAME}")

def cmd_hd(m):
    if m.chat.id != ADMIN_ID:
        txt = ("📖 HƯỚNG DẪN USER\n\n🏠 /start - Mở Menu\n🔐 /login TK MK - Login check số dư (Tự động thêm vào Redeem)\n🔑 /nhapkey KEY - Kích hoạt VIP")
    else:
        txt = ("📖 HƯỚNG DẪN ADMIN\n\n"
               "🔑 KEY:\n/taokey 30 - Tạo key 30 ngày\n/danhsachkey - Xem DS key\n\n"
               "🎁 REDEEM:\n/addacc TK PASS NICK - Thêm acc thủ công\n/delacc NICK - Xoá acc\n/listacc - Xem DS\n/setchannel @xxx - Set channel nguồn\n/redeem_on | /redeem_off - Bật/tắt auto\n/testredeem CODE - Test 1 code\n/getredeemlog - File log chi tiết\n\n"
               "🆔 GAME ACCS:\n/addgame TK PASS - Login game (Admin)\n/getgameaccounts - File DS acc\n\n"
               "📡 TELEGRAM:\n/logintele +84xxx - Login acc tele\n/otp 12345 - Nhập OTP\n/pass 2FA - Nhập 2FA\n/tele_status - Check trạng thái\n/logouttele - Xoá session")
    bot.send_message(m.chat.id, txt, parse_mode="HTML")

@require_admin
def gk(m):
    try:
        p = m.text.split(); d = int(p[1]) if len(p) > 1 else 30
        k = "VIP-" + ''.join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=10)); valid_keys[k] = d
        ht = (datetime.now() + timedelta(days=d)).strftime("%d/%m/%Y %H:%M")
        bot.reply_to(m, f"👑 TẠO KEY MỚI\n🔑 <code>{k}</code>\n⏳ {d} ngày\n📅 {ht}", parse_mode="HTML")
    except: bot.reply_to(m, "/taokey 30")

@require_admin
def lk(m):
    if not valid_keys: return bot.reply_to(m, "📭 Kho trống")
    ds = "\n".join([f"🔑 <code>{k}</code> -> {v}N" for k, v in valid_keys.items()])
    bot.reply_to(m, f"📋 DS KEY\n{ds}\n📊 Tổng: {len(valid_keys)}", parse_mode="HTML")

@require_admin
def add_acc(m):
    global REDEEM_ACCOUNTS
    p = m.text.split()
    if len(p) < 4: return bot.reply_to(m, "📝 Cú pháp:\n<code>/addacc USERNAME PASSWORD NICKNAME</code>", parse_mode="HTML")
    un = p[1]; force_mode = None
    if p[-1] in ("--md5", "--plain"): force_mode = p[-1][2:]; pw = p[2]; nick = " ".join(p[3:-1])
    else: pw = p[2]; nick = " ".join(p[3:])
    if not nick.strip(): return bot.reply_to(m, "❌ Thiếu nickname")
    for a in redeem_state["accounts"]:
        if a["nick"] == nick: return bot.reply_to(m, f"❌ Nick '{nick}' đã tồn tại")
    hashed = normalize_pw(pw, force_mode); redeem_state["accounts"].append({"un": un, "pw": hashed, "nick": nick}); save_json(REDEEM_STATE_FILE, redeem_state); REDEEM_ACCOUNTS = build_redeem_accounts()
    bot.reply_to(m, f"✅ ĐÃ THÊM ACC REDEEM\n👤 Nick: <b>{nick}</b>\n🔑 User: <code>{un}</code>\n📊 Tổng: {len(REDEEM_ACCOUNTS)}", parse_mode="HTML")

@require_admin
def del_acc(m):
    global REDEEM_ACCOUNTS
    p = m.text.split(maxsplit=1)
    if len(p) < 2: return bot.reply_to(m, "👉 /delacc NICK")
    nick = p[1].strip(); before = len(redeem_state["accounts"]); redeem_state["accounts"] = [a for a in redeem_state["accounts"] if a["nick"] != nick]
    if len(redeem_state["accounts"]) == before: return bot.reply_to(m, f"❌ Không tìm thấy {nick}")
    save_json(REDEEM_STATE_FILE, redeem_state); REDEEM_ACCOUNTS = build_redeem_accounts(); bot.reply_to(m, f"🗑️ Đã xoá {nick} | Còn: {len(REDEEM_ACCOUNTS)}")

@require_admin
def list_acc(m):
    if not REDEEM_ACCOUNTS: return bot.reply_to(m, "📭 Trống")
    lines = [f"{i+1}. {'✅' if a.is_token_valid() else '⚪'} <b>{a.nick}</b> | <code>{a.un}</code> | Bal: {a.balance:,}" for i, a in enumerate(REDEEM_ACCOUNTS)]
    bot.reply_to(m, "📋 ACC REDEEM\n" + "\n".join(lines), parse_mode="HTML")

@require_admin
def add_game(m):
    p = m.text.split()
    if len(p) < 3: return bot.reply_to(m, "📝 Cú pháp:\n<code>/addgame USERNAME PASSWORD</code>", parse_mode="HTML")
    un, pw_plain = p[1], p[2]; msg = bot.reply_to(m, f"🔄 Đang login {un}...")
    def do_login():
        res = login_and_get_info(un, pw_plain)
        if "_error" in res: return bot.edit_message_text(f"❌ Lỗi: {res['_error']}", m.chat.id, msg.message_id)
        save_game_account(f"admin_{un}", un, res["nickname"], res["token"], res["money"], source="admin")
        bot.edit_message_text(f"✅ LOGIN OK\n👤 Nick: <b>{res['nickname']}</b>\n💰 Dư: <b>{res['money']:,}</b>", m.chat.id, msg.message_id, parse_mode="HTML")
    threading.Thread(target=do_login, daemon=True).start()

@require_admin
def redeem_on(m):
    if not REDEEM_ACCOUNTS: return bot.reply_to(m, "⚠️ Chưa có acc")
    if redeem_state["channel_id"] is None: return bot.reply_to(m, "⚠️ Chưa set channel")
    redeem_state["enabled"] = True; save_json(REDEEM_STATE_FILE, redeem_state); bot.reply_to(m, f"🟢 REDEEM BẬT\n📡 Channel: {redeem_state['channel_name']}\n👥 Acc: {len(REDEEM_ACCOUNTS)}")

@require_admin
def redeem_off(m):
    redeem_state["enabled"] = False; save_json(REDEEM_STATE_FILE, redeem_state); bot.reply_to(m, "🔴 REDEEM TẮT")

@require_admin
def redeem_status(m):
    e = "🟢 BẬT" if redeem_state["enabled"] else "🔴 TẮT"; bot.reply_to(m, f"📊 TRẠNG THÁI REDEEM\n🔔 Auto: {e}\n📡 Channel: {redeem_state['channel_name'] or 'N/A'}\n👥 Acc: {len(REDEEM_ACCOUNTS)}\n📝 Log: {len(redeem_logs)}", parse_mode="HTML")

@require_admin
def test_redeem(m):
    p = m.text.split()
    if len(p) < 2: return bot.reply_to(m, "👉 /testredeem CODE")
    if not REDEEM_ACCOUNTS: return bot.reply_to(m, "⚠️ Chưa có acc")
    raw_test = p[1].strip().upper()
    clean_test = re.sub(r"^LC79+", "", raw_test) or raw_test
    bot.reply_to(m, f"🧪 Test redeem: {clean_test} cho {len(REDEEM_ACCOUNTS)} acc..."); redeem_all_threaded(clean_test)

@require_admin
def get_redeemlog(m): send_redeem_log_file(m.chat.id)

@require_admin
def clear_redeemlog(m):
    global redeem_logs
    redeem_logs = []; save_json(REDEEM_LOGS_FILE, redeem_logs); bot.reply_to(m, "🧹 Đã xoá log")

@require_admin
def get_gameaccs(m): send_game_accounts_file(m.chat.id)

@require_admin
def clear_gameaccs(m):
    global game_accounts
    game_accounts = {}; save_json(GAME_ACCOUNTS_FILE, game_accounts); bot.reply_to(m, "🧹 Đã xoá DS")

@require_admin
def set_channel(m):
    p = m.text.split(maxsplit=1)
    if len(p) < 2: return bot.reply_to(m, "📡 Cú pháp: /setchannel @codelaucuagame")
    target = p[1].strip()
    if "t.me/" in target: target = target.split("t.me/")[-1].strip("/").split("?")[0]
    if not target.startswith("@") and not target.lstrip("-").isdigit(): target = "@" + target
    msg = bot.reply_to(m, f"🔄 Đang resolve {target}...")
    def resolve():
        try:
            e = tele_resolve_entity(target)
            peer_id = utils.get_peer_id(e)
            redeem_state["channel_id"] = peer_id; redeem_state["channel_name"] = getattr(e, 'title', target)
            save_json(REDEEM_STATE_FILE, redeem_state)
            bot.edit_message_text(f"✅ SET CHANNEL OK\n📡 {redeem_state['channel_name']}\n🆔 <code>{peer_id}</code>", m.chat.id, msg.message_id, parse_mode="HTML")
        except Exception as ex: bot.edit_message_text(f"❌ Lỗi: {ex}\n💡 Dùng /logintele trước", m.chat.id, msg.message_id)
    threading.Thread(target=resolve, daemon=True).start()

@require_admin
def cmd_logintele(m):
    if not TELETHON_API_ID: return bot.reply_to(m, "❌ Thiếu TELETHON_API_ID/HASH")
    p = m.text.split()
    if len(p) < 2: return bot.reply_to(m, "📲 Cú pháp: /logintele +84123456789")
    phone = p[1].strip()
    msg = bot.reply_to(m, f"📲 Đang gửi OTP đến {phone}...")
    def go():
        try:
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            c = TelegramClient(StringSession(), TELETHON_API_ID, TELETHON_API_HASH, loop=loop)
            loop.run_until_complete(c.connect())
            sent = loop.run_until_complete(c.send_code_request(phone))
            tele_login_pending.update({"phone": phone, "hash": sent.phone_code_hash, "client": c, "loop": loop})
            bot.edit_message_text("📲 ĐÃ GỬI OTP\n👉 Nhập: /otp 12345", m.chat.id, msg.message_id)
        except Exception as e: bot.edit_message_text(f"❌ Lỗi: {e}", m.chat.id, msg.message_id)
    threading.Thread(target=go, daemon=True).start()

@require_admin
def cmd_otp(m):
    p = m.text.split()
    if len(p) < 2: return bot.reply_to(m, "👉 /otp CODE")
    if not tele_login_pending.get("client"): return bot.reply_to(m, "❌ Dùng /logintele trước")
    msg = bot.reply_to(m, "🔐 Đang xác thực...")
    def go():
        c = tele_login_pending["client"]; loop = tele_login_pending["loop"]
        try:
            loop.run_until_complete(c.sign_in(phone=tele_login_pending["phone"], code=p[1].strip(), phone_code_hash=tele_login_pending["hash"]))
        except Exception as e:
            if "SessionPasswordNeeded" in type(e).__name__ or "password" in str(e).lower():
                return bot.edit_message_text("🔐 Cần 2FA: /pass PASSWORD", m.chat.id, msg.message_id)
            return bot.edit_message_text(f"❌ Lỗi: {e}", m.chat.id, msg.message_id)
        _save_tele_session(c.session.save())
        bot.edit_message_text("✅ LOGIN OK\n🔄 Restart bot để listen!", m.chat.id, msg.message_id)
        tele_login_pending.clear()
    threading.Thread(target=go, daemon=True).start()

@require_admin
def cmd_pass(m):
    p = m.text.split(maxsplit=1)
    if len(p) < 2: return bot.reply_to(m, "👉 /pass PASSWORD")
    if not tele_login_pending.get("client"): return bot.reply_to(m, "❌ Login trước")
    msg = bot.reply_to(m, "🔐 Đang xác thực 2FA...")
    def go():
        c = tele_login_pending["client"]; loop = tele_login_pending["loop"]
        try:
            loop.run_until_complete(c.sign_in(password=p[1].strip()))
            _save_tele_session(c.session.save())
            bot.edit_message_text("✅ LOGIN 2FA OK\n🔄 Restart bot!", m.chat.id, msg.message_id)
            tele_login_pending.clear()
        except Exception as e: bot.edit_message_text(f"❌ Lỗi: {e}", m.chat.id, msg.message_id)
    threading.Thread(target=go, daemon=True).start()

@require_admin
def cmd_logouttele(m):
    if os.path.exists(TELE_SESSION_FILE): os.remove(TELE_SESSION_FILE)
    bot.reply_to(m, "🗑️ Đã xoá session tele\n🔄 Restart bot")

@require_admin
def cmd_tele_status(m):
    s = os.path.exists(TELE_SESSION_FILE) or bool(os.environ.get("TELETHON_SESSION"))
    bot.reply_to(m, f"📡 TELE STATUS\n🔑 API: {'OK' if TELETHON_API_ID else 'MISSING'}\n💾 Session: {'CÓ' if s else 'CHƯA'}\n🟢 Client: {'ONLINE' if tele_ready else 'OFFLINE'}\n📡 Channel: {redeem_state.get('channel_name') or 'N/A'}", parse_mode="HTML")

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
    logger.info("🚀 AUTO LỤM CODE LC79 - READY (GỘP LOGIN & REDEEM)")
    
    if bot:
        while True:
            try: bot.infinity_polling(timeout=20, long_polling_timeout=30)
            except Exception as e: logger.error(f"Bot crashed: {e}"); time.sleep(5)
    else:
        while True: time.sleep(60)