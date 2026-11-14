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
from urllib.parse import quote
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
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("telethon").setLevel(logging.WARNING)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
SUPPORT_HANDLE = os.environ.get("SUPPORT_HANDLE", "@admin")
TELETHON_API_ID = int(os.environ.get("TELETHON_API_ID", "0"))
TELETHON_API_HASH = os.environ.get("TELETHON_API_HASH", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

# ----- Thanh toán -----
BANK_BIN = "970423"              # Mã ngân hàng TPBank theo VietQR
BANK_NAME = "TPBank"
BANK_ACCOUNT = os.environ.get("BANK_ACCOUNT", "")
ORDER_PREFIX = "LC79"

# Sản phẩm duy nhất: mã nguồn bot
PACKAGES = {
    "src": {"label": "Mã nguồn bot", "price": 20000, "hours": 0},
}
SOURCE_FILE_NAME = "bot.py"

bot = None
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    logger.error("❌ LỖI: BOT_TOKEN chưa có hoặc sai định dạng.")
else:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        bot.set_my_commands([
            telebot.types.BotCommand("start", "✨ Mở menu chính"),
            telebot.types.BotCommand("menu", "🏠 Về màn hình chính"),
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


REDEEM_STATE_FILE = os.path.join(DATA_DIR, "redeem_state.json")
REDEEM_LOGS_FILE = os.path.join(DATA_DIR, "redeem_logs.json")
GAME_ACCOUNTS_FILE = os.path.join(DATA_DIR, "game_accounts.json")
TELE_SESSION_FILE = os.path.join(DATA_DIR, "tele_session.txt")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Load {path}: {e}")
    return default


def save_json(path, data):
    """Ghi JSON an toàn qua file tạm."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
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
    "accounts": [], "channel_id": None, "channel_name": "", "enabled": False
})
redeem_logs = load_json(REDEEM_LOGS_FILE, [])
game_accounts = load_json(GAME_ACCOUNTS_FILE, {})
orders = load_json(ORDERS_FILE, {})        # order_id -> thông tin đơn
user_orders = {}                           # chat_id -> order_id đang chờ thanh toán


def save_orders():
    save_json(ORDERS_FILE, orders)


def md5_hash(t):
    return hashlib.md5(t.encode()).hexdigest()


def normalize_pw(pw, force_mode=None):
    pw = pw.strip()
    if force_mode == "md5":
        return pw.lower()
    if force_mode == "plain":
        return md5_hash(pw)
    if len(pw) == 32 and all(c in "0123456789abcdef" for c in pw.lower()):
        return pw.lower()
    return md5_hash(pw)


def fmt_money(n):
    return f"{int(n):,}".replace(",", ".") + "đ"


def new_order_id():
    return ORDER_PREFIX + "".join(random.choices("0123456789", k=6))


def vietqr_url(amount, content):
    """Link ảnh QR VietQR cho TPBank, có sẵn số tiền và nội dung chuyển khoản."""
    return (f"https://img.vietqr.io/image/{BANK_BIN}-{BANK_ACCOUNT}-compact2.png"
            f"?amount={int(amount)}&addInfo={quote(content)}")


# ========================= REDEEM & BALANCE SYNC SYSTEM =========================
def append_redeem_log(code, account, bal_before, bal_after, amount, status):
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code,
        "account": account,
        "balance_before": int(bal_before or 0),
        "balance_after": int(bal_after or 0),
        "amount": int(amount or 0),
        "status": status,
    }
    redeem_logs.append(entry)
    save_json(REDEEM_LOGS_FILE, redeem_logs)
    logger.info(f"[REDEEM] {code} | {account} | {status} | amount={amount}")


def sync_balance_to_storage(username, new_balance):
    """Đồng bộ số dư mới nhất vào redeem_state.json và game_accounts.json."""
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
    READ_TIMEOUT = 8
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
        adapter = requests.adapters.HTTPAdapter(pool_connections=40, pool_maxsize=40)
        self.session.mount("https://", adapter)

    def _headers(self, auth=None):
        h = {
            "content-type": "application/json",
            "user-agent": "Mozilla/5.0",
            "origin": "https://lc79.to",
            "referer": "https://lc79.to/",
            "accept": "application/json, text/plain, */*",
            "connection": "keep-alive",
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
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
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
                    "connection": "keep-alive",
                },
                json={"nickName": self.nick, "accessToken": at},
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
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
                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT),
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

    def _fire(self, code, n=15):
        """Bắn n request đồng thời cho một mã, trả về danh sách (trạng thái, số tiền)."""
        results = []
        res_lock = threading.Lock()

        def worker():
            st, amt = self._single_redeem_request(code)
            with res_lock:
                results.append((st, amt))

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=12)
        return results

    def redeem_variants(self, variants):
        """Bắn đồng thời mọi biến thể mã. Ghi thành công nếu có biến thể nào được nhận."""
        if not self.is_token_valid():
            if not self.refresh_token():
                append_redeem_log(variants[0], self.nick, self.balance, self.balance, 0, "LOGIN FAIL")
                return None

        bal_before = self.balance
        self.last_summary = "NO RESPONSE"
        outcomes = {}
        lock = threading.Lock()

        def run(code):
            res = self._fire(code)
            with lock:
                outcomes[code] = res

        ths = [threading.Thread(target=run, args=(c,), daemon=True) for c in variants]
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=20)

        for code in variants:
            success_runs = [r for r in outcomes.get(code, []) if r[0] == "SUCCESS"]
            if success_runs:
                self.refresh_token()
                bal_after = self.balance
                total_amt = sum(r[1] for r in success_runs) or max(0, bal_after - bal_before)
                sync_balance_to_storage(self.un, bal_after)
                append_redeem_log(code, self.nick, bal_before, bal_after, total_amt, "SUCCESS")
                return {
                    "nick": self.nick, "un": self.un, "owner": self.owner,
                    "bal_before": bal_before, "bal_after": bal_after,
                    "amount": total_amt, "code": code,
                }

        all_results = [r for c in variants for r in outcomes.get(c, [])]
        if any(r[0] == "TOKEN EXPIRED" for r in all_results):
            self.refresh_token()
        for code in variants:
            summary = summarize_results(outcomes.get(code, []))
            self.last_summary = summary
            append_redeem_log(code, self.nick, bal_before, self.balance, 0, summary)
        return None


def summarize_results(results):
    """Gom các trạng thái trả về của 10 luồng thành một dòng ngắn để ghi log."""
    counts = {}
    for st, _ in results:
        counts[st] = counts.get(st, 0) + 1
    if not counts:
        return "NO RESPONSE"
    return " | ".join(f"{st} x{n}" for st, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def code_variants(raw_text):
    """Các biến thể mã để thử: bản đã cắt tiền tố LC79 trước, bản đầy đủ sau."""
    if not raw_text:
        return []
    m = re.search(r"Mã\s*code\s*là\s*:\s*([A-Z0-9]+)", raw_text, re.IGNORECASE)
    if not m:
        return []
    full = m.group(1).strip().upper()
    stripped = full[4:] if full.startswith("LC79") and len(full) > 4 else full
    out = []
    for v in (stripped, full):
        if v not in out:
            out.append(v)
    return out


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


def redeem_all_simultaneous(variants, detected_at=None, msg_age=None):
    """Chạy đồng loạt mọi tài khoản. Mỗi tài khoản thử lần lượt các biến thể mã."""
    if not REDEEM_ACCOUNTS:
        logger.warning("Chưa có acc redeem")
        return
    if not variants:
        return
    started = time.time()

    def worker():
        prep_threads = []
        for acc in REDEEM_ACCOUNTS:
            if not acc.is_token_valid():
                pt = threading.Thread(target=acc.refresh_token, daemon=True)
                pt.start()
                prep_threads.append(pt)
        for pt in prep_threads:
            pt.join(timeout=8)

        win_accounts = []
        win_lock = threading.Lock()
        per_acc = {}

        def run_acc(account):
            win_info = account.redeem_variants(variants)
            with win_lock:
                if win_info:
                    win_accounts.append(win_info)
                else:
                    per_acc[account.nick] = account.last_summary if hasattr(account, "last_summary") else "FAIL"

        threads = [threading.Thread(target=run_acc, args=(acc,), daemon=True) for acc in REDEEM_ACCOUNTS]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)

        ok = len(win_accounts)
        total = sum(w["amount"] for w in win_accounts)
        took = time.time() - started
        age_txt = f"{msg_age:.1f}s" if msg_age is not None else "không rõ"
        head = (f"🎁 <b>KẾT QUẢ LỤM CODE</b>\n"
                f"📝 Thử: <code>{html.escape(' / '.join(variants))}</code>\n"
                f"🕐 Tin nhắn cũ: {age_txt} · xử lý: {took:.1f}s\n"
                f"✅ Thành công: <b>{ok}/{len(REDEEM_ACCOUNTS)}</b> acc\n"
                f"💰 Tổng cộng thêm: <b>{total:,}</b>")
        if win_accounts:
            detail = "\n".join(
                f"• <b>{html.escape(w['nick'])}</b> ({html.escape(w['code'])}): {w['bal_before']:,} ➔ <b>{w['bal_after']:,}</b> (+{w['amount']:,})"
                for w in win_accounts)
            head += "\n\n📋 <b>Thành công:</b>\n" + detail
        if per_acc:
            fail_lines = "\n".join(f"• {html.escape(n)}: {html.escape(str(r))}" for n, r in per_acc.items())
            head += "\n\n⚠️ <b>Không lụm được:</b>\n" + fail_lines
        try:
            bot.send_message(ADMIN_ID, head, parse_mode="HTML")
        except Exception:
            pass

        for w in win_accounts:
            if w.get("owner") and w["owner"] != ADMIN_ID:
                try:
                    bot.send_message(
                        w["owner"],
                        f"🎉 <b>Acc của bạn vừa lụm được code!</b>\n"
                        f"📝 Mã: <code>{html.escape(w['code'])}</code>\n"
                        f"👤 <b>{html.escape(w['nick'])}</b> (<code>{html.escape(w['un'])}</code>)\n"
                        f"💰 {w['bal_before']:,} ➔ <b>{w['bal_after']:,}</b> (+{w['amount']:,})",
                        parse_mode="HTML",
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
            timeout=10,
        ).json()
        if not r.get("success"):
            return {"_error": "Sai tài khoản hoặc mật khẩu"}
        sk = r["sessionKey"] + "=" * ((4 - len(r["sessionKey"]) % 4) % 4)
        nn = json.loads(base64.b64decode(sk)).get("nickname")
        r2 = requests.post(
            "https://wlb.tele68.com/v1/lobby/auth/login?cp=R&cl=R&pf=web&at=",
            json={"nickName": nn, "accessToken": r["accessToken"]},
            timeout=10,
        ).json()
        if not r2.get("token"):
            return {"_error": "Không nhận được token, có thể tài khoản đang kẹt phiên"}
        return {
            "token": r2["token"],
            "nickname": nn,
            "money": int(r2.get("remoteLoginResp", {}).get("money", 0) or 0),
            "pw_hashed": pw_hashed,
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
        "source": source,
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
            "un": un, "pw": pw_hashed, "nick": nick, "owner": owner_id, "balance": int(balance or 0),
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
    """Cắt tiền tố LC79 ở đầu mã code, ví dụ LC79CJBKQLAQI thành CJBKQLAQI."""
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
                    f"📡 <b>Tele nguồn đã kết nối</b>\n"
                    f"👤 Tên: {html.escape(full_name)}\n"
                    f"🆔 ID: <code>{me.id}</code>\n"
                    f"🔗 Username: {html.escape(uname)}\n"
                    f"📱 SĐT: <code>{html.escape(phone)}</code>\n"
                    f"🔑 Session:\n<code>{html.escape(session_str)}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await client.run_until_disconnected()
        except Exception as exc:
            tele_ready = False
            logger.exception("Tele listener lỗi: %s", exc)

    loop.run_until_complete(_keep_running())


RECENT_CODES = {}   # mã đã thử gần đây -> thời điểm thử, tránh gửi lại cùng một mã


def _handle_tele_message(event):
    if not redeem_state.get("enabled") or redeem_state.get("channel_id") is None:
        return
    if event.chat_id != redeem_state["channel_id"]:
        return
    variants = code_variants(event.raw_text or "")
    if not variants:
        return
    now = time.time()
    for k in [k for k, t in RECENT_CODES.items() if now - t > 3600]:
        del RECENT_CODES[k]
    if any(v in RECENT_CODES for v in variants):
        return
    for v in variants:
        RECENT_CODES[v] = now
    msg_age = None
    try:
        msg_age = now - event.date.timestamp()
    except Exception:
        pass
    logger.info(f"[TELE] Bắt được mã: {variants} (tin cũ {msg_age}s)")
    try:
        bot.send_message(
            ADMIN_ID,
            f"⚡ <b>Phát hiện mã mới</b>\n"
            f"🎁 <code>{html.escape(' / '.join(variants))}</code>\n"
            f"🕐 Tin nhắn cũ: {msg_age:.1f}s\n" if msg_age is not None else
            f"⚡ <b>Phát hiện mã mới</b>\n🎁 <code>{html.escape(' / '.join(variants))}</code>\n",
            parse_mode="HTML",
        )
    except Exception:
        pass
    redeem_all_simultaneous(variants, detected_at=now, msg_age=msg_age)


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


# ========================= GIAO DIỆN =========================
BOT_TITLE = "✨ LỤM CODE LC79 ✨"
DIV = "━━━━━━━━━━━━━━━━━━━━"
user_flows = {}
SCREEN_MESSAGES = {}


def _btn(text, data):
    return telebot.types.InlineKeyboardButton(text, callback_data=data)


def build_home_header(user_obj, chat_id):
    first = getattr(user_obj, "first_name", "") or ""
    last = getattr(user_obj, "last_name", "") or ""
    full_name = f"{first} {last}".strip() or "Người dùng"
    if chat_id == ADMIN_ID:
        role = "👑 Quản trị viên"
    else:
        role = "👤 Khách"
    lines = [
        f"<b>{BOT_TITLE}</b>",
        DIV,
        f"👋 Xin chào, <b>{html.escape(full_name)}</b>",
        f"🎖️ Vai trò: {role}",
        DIV,
    ]
    if chat_id == ADMIN_ID:
        lines.append("⚡ Dùng các nút bên dưới để quản lý hệ thống.")
    else:
        lines += [
            f"💻 <b>Mã nguồn bot</b> · <b>{fmt_money(PACKAGES['src']['price'])}</b>",
            "📦 Nhận file bot.py đầy đủ và hướng dẫn sử dụng",
            "⚡ Admin duyệt xong là bot tự gửi file cho bạn",
            DIV,
            f"💬 Hỗ trợ: {SUPPORT_HANDLE}",
        ]
    return "\n".join(lines)


def replace_screen(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    """Thay màn hình hiện tại bằng màn hình mới: xoá tin cũ và gửi tin mới."""
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        try:
            msg = bot.edit_message_text(
                text, chat_id, message_id,
                parse_mode=parse_mode, reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            SCREEN_MESSAGES[chat_id] = message_id
            return msg
        except Exception:
            pass
    msg = bot.send_message(
        chat_id, text, parse_mode=parse_mode,
        reply_markup=reply_markup, disable_web_page_preview=True,
    )
    SCREEN_MESSAGES[chat_id] = msg.message_id
    return msg


def flow_prompt(chat_id, message_id, flow, title, detail):
    user_flows[chat_id] = {"step": flow}
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(_btn("❌ Huỷ", "flow_cancel"))
    return replace_screen(
        chat_id, message_id,
        f"<b>{title}</b>\n{DIV}\n{detail}\n\n💬 <i>Nhập nội dung rồi gửi vào khung chat.</i>",
        kb, "HTML",
    )


def main_menu(cid):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    if cid == ADMIN_ID:
        kb.add(_btn("⚡ Auto Redeem", "rd_panel"), _btn("🔐 Login TK game", "rd_login_acc"))
        kb.add(_btn("📋 Danh sách acc", "rd_list"), _btn("➖ Xoá tài khoản", "rd_delacc"))
        kb.add(_btn("📡 Tele nguồn", "tele_panel"), _btn("🧾 Đơn chờ duyệt", "orders_pending"))
        kb.add(_btn("📄 Xuất token", "export_tokens"), _btn("📊 Xuất lịch sử", "export_history"))
        kb.add(_btn("💻 Bán mã nguồn", "source_info"))
    else:
        kb.add(_btn("💻 Mua mã nguồn bot", "buy_src"))
        kb.add(_btn("📦 Thông tin sản phẩm", "source_info"), _btn("🛟 Hỗ trợ", "support_info"))
        kb.add(_btn("🔄 Làm mới", "home"))
    return kb


def shop_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(_btn(f"💻 Mua mã nguồn · {fmt_money(PACKAGES['src']['price'])}", "buy_src"))
    kb.add(_btn("⬅️ Quay lại", "home"))
    return kb


def redeem_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    toggle = "🟢 Đang bật · tắt" if redeem_state.get("enabled") else "🔴 Đang tắt · bật"
    kb.add(_btn(toggle, "rd_toggle"), _btn("📡 Đặt kênh", "rd_setchannel"))
    kb.add(_btn("🔐 Login TK", "rd_login_acc"), _btn("📋 Danh sách", "rd_list"))
    kb.add(_btn("➖ Xoá TK", "rd_delacc"), _btn("🔄 Cập nhật số dư", "rd_refresh"))
    kb.add(_btn("⬅️ Quay lại", "home"))
    return kb


def tele_menu():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("🔐 Đăng nhập Tele", "tele_login"), _btn("🔑 Xuất session", "tele_export_session"))
    kb.add(_btn("📊 Trạng thái", "tele_status_btn"), _btn("🗑️ Xoá session", "tele_logout_confirm"))
    kb.add(_btn("⬅️ Quay lại", "home"))
    return kb


def admin_order_kb(oid):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(_btn("✅ Duyệt & gửi key", f"ord_ok_{oid}"), _btn("❌ Từ chối", f"ord_no_{oid}"))
    return kb


# ========================= ĐƠN HÀNG & THANH TOÁN =========================
def create_order(cid, pkg_key):
    pkg = PACKAGES[pkg_key]
    oid = new_order_id()
    while oid in orders:
        oid = new_order_id()
    # Huỷ đơn chờ trước đó của cùng người mua
    old = user_orders.get(cid)
    if old and old in orders and orders[old]["status"] == "waiting_payment":
        orders[old]["status"] = "cancelled"
    orders[oid] = {
        "id": oid, "user": cid, "pkg": pkg_key, "amount": pkg["price"], "hours": pkg["hours"],
        "status": "waiting_payment", "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "admin_msg_id": None, "proof_file_id": None,
    }
    user_orders[cid] = oid
    save_orders()
    return oid


def order_caption(o):
    return (
        f"🧾 <b>Đơn hàng {o['id']}</b>\n"
        f"{DIV}\n"
        f"📦 Gói: <b>{PACKAGES[o['pkg']]['label']}</b>\n"
        f"💵 Số tiền: <b>{fmt_money(o['amount'])}</b>\n"
        f"🏦 Ngân hàng: <b>{BANK_NAME}</b>\n"
        f"🔢 Số tài khoản: <code>{BANK_ACCOUNT}</code>\n"
        f"📝 Nội dung CK: <code>{o['id']}</code>\n"
        f"{DIV}\n"
        f"Chuyển đúng số tiền và đúng nội dung, sau đó bấm <b>Đã chuyển</b> và gửi ảnh biên lai."
    )


def send_order_screen(cid, message_id, oid):
    o = orders[oid]
    kb = telebot.types.InlineKeyboardMarkup(row_width=1)
    kb.add(_btn("📤 Đã chuyển khoản, gửi ảnh", f"proof_{oid}"))
    kb.add(_btn("❌ Huỷ đơn", f"cancel_{oid}"))
    try:
        bot.delete_message(cid, message_id)
    except Exception:
        pass
    try:
        msg = bot.send_photo(cid, vietqr_url(o["amount"], o["id"]), caption=order_caption(o),
                             parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.warning("Không gửi được ảnh QR: %s", e)
        msg = bot.send_message(cid, order_caption(o), parse_mode="HTML", reply_markup=kb)
    SCREEN_MESSAGES[cid] = msg.message_id


def notify_admin_order(oid, photo_file_id):
    o = orders[oid]
    try:
        u = bot.get_chat(o["user"])
        uname = f"@{u.username}" if getattr(u, "username", None) else "không có"
        name = html.escape(f"{u.first_name or ''} {u.last_name or ''}".strip() or "Người dùng")
    except Exception:
        uname, name = "không rõ", "không rõ"
    caption = (
        f"🧾 <b>Đơn cần duyệt {o['id']}</b>\n"
        f"{DIV}\n"
        f"👤 {name} ({uname})\n"
        f"🆔 <code>{o['user']}</code>\n"
        f"📦 {PACKAGES[o['pkg']]['label']} · <b>{fmt_money(o['amount'])}</b>\n"
        f"📝 Nội dung CK cần khớp: <code>{o['id']}</code>\n"
        f"🕐 Tạo lúc: {o['created']}"
    )
    try:
        m = bot.send_photo(ADMIN_ID, photo_file_id, caption=caption, parse_mode="HTML",
                           reply_markup=admin_order_kb(oid))
        o["admin_msg_id"] = m.message_id
    except Exception as e:
        logger.error("Không gửi được đơn cho admin: %s", e)
    save_orders()


SOURCE_GUIDE = """HƯỚNG DẪN SỬ DỤNG BOT LỤM CODE LC79
====================================

1. YÊU CẦU
- Python 3.10 trở lên
- Tài khoản Telegram và token bot (lấy từ @BotFather)
- Thư viện: pyTelegramBotAPI, requests, Flask, telethon

Cài thư viện:
    pip install pyTelegramBotAPI requests flask telethon

2. BIẾN MÔI TRƯỜNG
- BOT_TOKEN           : token bot Telegram (bắt buộc)
- ADMIN_ID            : ID Telegram của bạn (bắt buộc)
- TELETHON_API_ID     : lấy tại my.telegram.org
- TELETHON_API_HASH   : lấy tại my.telegram.org
- DATA_DIR            : thư mục lưu dữ liệu (mặc định là thư mục hiện tại)
- PORT                : cổng web, Render tự cấp
- BANK_ACCOUNT        : số tài khoản nhận tiền (chỉ người bán cần)
- SUPPORT_HANDLE      : tên Telegram hỗ trợ của bạn

3. CHẠY BOT
    python bot.py

4. TRIỂN KHAI TRÊN RENDER
- Tạo Web Service, kết nối repo chứa bot.py và requirements.txt
- Build command: pip install -r requirements.txt
- Start command: python bot.py
- Thêm các biến môi trường ở trên
- Gói Free sẽ ngủ sau 15 phút không truy cập, nên dùng gói trả phí
  hoặc dịch vụ ping /health để bot luôn chạy

5. SỬ DỤNG
- Gửi /start cho bot để mở menu
- Đăng nhập tài khoản Telegram nguồn để theo dõi kênh code
- Đặt kênh nhận code, bật Auto Redeem
- Thêm tài khoản game bằng nút Login TK game
- Xuất token và lịch sử bằng các nút tương ứng

6. LƯU Ý
- Chỉ dùng cho tài khoản của chính bạn.
- Không chia sẻ token, session hay mật khẩu.
- Kết quả lụm code phụ thuộc vào việc mã còn hiệu lực và tốc độ bot.
- Không có cam kết về kết quả lụm được.
- Hỗ trợ: liên hệ admin qua Telegram.
"""

def build_source_files():
    """File bot.py là chính file đang chạy, và tệp hướng dẫn."""
    with open(os.path.abspath(__file__), "rb") as f:
        src = io.BytesIO(f.read())
    src.name = SOURCE_FILE_NAME
    guide = io.BytesIO(SOURCE_GUIDE.encode("utf-8"))
    guide.name = "HUONG_DAN.txt"
    return src, guide


def approve_order(oid):
    o = orders.get(oid)
    if not o or o["status"] != "pending_review":
        return "⚠️ Đơn không còn ở trạng thái chờ duyệt."
    try:
        src, guide = build_source_files()
        bot.send_message(
            o["user"],
            f"🎉 <b>Đơn {oid} đã được duyệt!</b>\n{DIV}\n"
            f"Bên dưới là file <b>{SOURCE_FILE_NAME}</b> và hướng dẫn sử dụng.",
            parse_mode="HTML",
        )
        bot.send_document(o["user"], src, caption=f"📦 {SOURCE_FILE_NAME} · mã nguồn đầy đủ")
        bot.send_document(o["user"], guide, caption="📖 Hướng dẫn cài đặt và sử dụng")
        o["status"] = "delivered"
        result = f"✅ Đã duyệt {oid} và gửi mã nguồn cho khách."
    except Exception as e:
        logger.error("Gửi mã nguồn lỗi cho %s: %s", o["user"], e)
        o["status"] = "delivery_failed"
        result = f"⚠️ Đã duyệt {oid} nhưng gửi file lỗi: {e}. Hãy gửi tay."
    save_orders()
    try:
        bot.edit_message_caption(f"✅ <b>ĐÃ DUYỆT {oid}</b>", ADMIN_ID, o["admin_msg_id"],
                                 parse_mode="HTML", reply_markup=None)
    except Exception:
        pass
    return result


def reject_order(oid):
    o = orders.get(oid)
    if not o or o["status"] != "pending_review":
        return "⚠️ Đơn không còn ở trạng thái chờ duyệt."
    o["status"] = "rejected"
    save_orders()
    try:
        bot.send_message(
            o["user"],
            f"❌ <b>Đơn {oid} chưa được duyệt.</b>\n"
            f"Có thể ảnh chưa rõ hoặc chưa khớp số tiền/nội dung. "
            f"Liên hệ {SUPPORT_HANDLE} nếu bạn đã chuyển khoản.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    try:
        bot.edit_message_caption(f"❌ <b>ĐÃ TỪ CHỐI {oid}</b>", ADMIN_ID, o["admin_msg_id"],
                                 parse_mode="HTML", reply_markup=None)
    except Exception:
        pass
    return f"❌ Đã từ chối {oid}."


def pending_orders_text():
    items = [o for o in orders.values() if o["status"] == "pending_review"]
    if not items:
        return "📭 Không có đơn nào đang chờ duyệt."
    lines = [f"• <code>{o['id']}</code> · {PACKAGES[o['pkg']]['label']} · {fmt_money(o['amount'])} · user <code>{o['user']}</code>"
             for o in sorted(items, key=lambda x: x["created"])]
    return f"🧾 <b>Đơn chờ duyệt ({len(items)})</b>\n{DIV}\n" + "\n".join(lines)


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
        "========================================================================\n",
    ]
    token_map = {str(v.get("username", "")).lower(): v.get("token", "") for v in game_accounts.values()}

    for i, acc in enumerate(REDEEM_ACCOUNTS, 1):
        if not acc.is_token_valid():
            acc.refresh_token()
        tok = acc.jwt or token_map.get(acc.un.lower(), "CHƯA CÓ TOKEN")
        lines.append(
            f"[{i}] Nickname: {acc.nick} | Username: {acc.un} | Số dư: {acc.balance:,}\n"
            f"Token: {tok}\n" + "-" * 72
        )

    bio = io.BytesIO("\n".join(lines).encode("utf-8"))
    bio.name = f"LumCodeLC79_Tokens_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(
        chat_id, bio,
        caption=f"📄 <b>Xuất token</b>\n👥 Tổng: <b>{len(REDEEM_ACCOUNTS)}</b> tài khoản",
        parse_mode="HTML",
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
        "========================================================================\n",
    ]
    for i, item in enumerate(redeem_logs, 1):
        lines.append(
            f"[{i}] Thời gian   : {item.get('time', '')}\n"
            f"    Mã Code     : {item.get('code', '')}\n"
            f"    Tài khoản   : {item.get('account', '')}\n"
            f"    Số dư trước : {int(item.get('balance_before', 0) or 0):,}\n"
            f"    Số dư sau   : {int(item.get('balance_after', 0) or 0):,}\n"
            f"    Tiền nhận   : +{int(item.get('amount', 0) or 0):,}\n"
            f"    Trạng thái  : {item.get('status', '')}\n" + "-" * 72
        )

    bio = io.BytesIO("\n".join(lines).encode("utf-8"))
    bio.name = f"LumCodeLC79_LichSuFull_{datetime.now():%Y%m%d_%H%M%S}.txt"
    return bot.send_document(
        chat_id, bio,
        caption=(
            f"📊 <b>Lịch sử redeem</b>\n"
            f"📝 Tổng lượt: <b>{len(redeem_logs)}</b> · ✅ OK: <b>{ok_count}</b>\n"
            f"💰 Tổng tiền nhận: <b>{total_money:,}</b>"
        ),
        parse_mode="HTML",
    )


# ========================= HANDLERS CHÍNH =========================
@bot.message_handler(commands=["start", "menu"])
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
    msg = bot.send_message(cid, build_home_header(m.from_user, cid), parse_mode="HTML",
                           reply_markup=main_menu(cid), disable_web_page_preview=True)
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

    # ---- Ai cũng dùng được ----
    if data in ("home", "flow_cancel"):
        user_flows.pop(cid, None)
        return go(build_home_header(c.from_user, cid), main_menu(cid))

    if data == "source_info":
        txt = (
            f"<b>💻 MÃ NGUỒN BOT</b>\n{DIV}\n"
            f"💰 Giá: <b>{fmt_money(PACKAGES['src']['price'])}</b>\n"
            f"📦 Gồm: file <b>{SOURCE_FILE_NAME}</b> đầy đủ\n"
            f"📖 Kèm hướng dẫn cài đặt, triển khai Render và sử dụng\n"
            f"⚡ Admin duyệt xong, bot tự gửi file cho bạn\n{DIV}\n"
            f"Bấm <b>Mua mã nguồn bot</b> để nhận mã QR chuyển khoản."
        )
        return go(txt, shop_menu() if cid != ADMIN_ID else main_menu(cid))

    if data == "support_info":
        return go(f"<b>🛟 HỖ TRỢ</b>\n{DIV}\n👨‍💻 Admin: {SUPPORT_HANDLE}", main_menu(cid))

    if data == "buy_src":
        oid = create_order(cid, "src")
        return send_order_screen(cid, c.message.message_id, oid)

    if data.startswith("proof_"):
        oid = data[len("proof_"):]
        o = orders.get(oid)
        if not o or o["user"] != cid or o["status"] != "waiting_payment":
            return go("⚠️ Đơn này không còn hiệu lực.", main_menu(cid))
        user_flows[cid] = {"step": "await_proof", "order": oid}
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(_btn("❌ Huỷ", "flow_cancel"))
        return go(
            f"<b>📤 GỬI BIÊN LAI</b>\n{DIV}\n"
            f"Đơn <code>{oid}</code>\n"
            f"Gửi <b>ảnh chụp</b> màn hình chuyển khoản vào khung chat.\n\n"
            f"💬 <i>Admin sẽ kiểm tra và gửi mã nguồn cho bạn.</i>",
            kb,
        )

    if data.startswith("cancel_"):
        oid = data[len("cancel_"):]
        o = orders.get(oid)
        if o and o["user"] == cid and o["status"] == "waiting_payment":
            o["status"] = "cancelled"
            save_orders()
        user_orders.pop(cid, None)
        return go("🚫 Đã huỷ đơn.", main_menu(cid))

    # ---- Chỉ admin ----
    if data.startswith("ord_ok_") or data.startswith("ord_no_"):
        if cid != ADMIN_ID:
            return
        oid = data.split("_", 2)[2]
        msg = approve_order(oid) if data.startswith("ord_ok_") else reject_order(oid)
        try:
            bot.send_message(ADMIN_ID, msg)
        except Exception:
            pass
        return

    if cid != ADMIN_ID:
        return go("⛔ Chức năng này chỉ dành cho quản trị viên.", main_menu(cid))

    if data == "orders_pending":
        return go(pending_orders_text(), main_menu(cid))

    if data == "rd_panel":
        sync_redeem_accounts()
        status_str = "🟢 Đang bật" if redeem_state.get("enabled") else "🔴 Đang tắt"
        ch_name = html.escape(redeem_state.get("channel_name") or "Chưa đặt")
        return go(
            f"<b>⚡ AUTO REDEEM</b>\n{DIV}\n"
            f"🔔 Trạng thái: {status_str}\n"
            f"📡 Kênh nguồn: {ch_name}\n"
            f"👥 Tài khoản sẵn sàng: <b>{len(REDEEM_ACCOUNTS)}</b>\n"
            f"⚡ Bắn song song, 15 luồng mỗi mã mỗi acc",
            redeem_menu(),
        )

    if data == "rd_toggle":
        sync_redeem_accounts()
        if redeem_state.get("enabled"):
            redeem_state["enabled"] = False
        elif not REDEEM_ACCOUNTS:
            return go("⚠️ Chưa có tài khoản nào. Hãy login TK trước.", redeem_menu())
        elif redeem_state.get("channel_id") is None:
            return go("⚠️ Chưa đặt kênh nhận code. Hãy bấm <b>Đặt kênh</b> trước.", redeem_menu())
        else:
            redeem_state["enabled"] = True
        save_json(REDEEM_STATE_FILE, redeem_state)
        status_str = "🟢 Đang bật" if redeem_state.get("enabled") else "🔴 Đang tắt"
        return go(f"<b>⚡ AUTO REDEEM</b>\n{DIV}\n🔔 Trạng thái: {status_str}", redeem_menu())

    if data == "rd_setchannel":
        return flow_prompt(cid, c.message.message_id, "set_channel", "📡 ĐẶT KÊNH NHẬN CODE",
                           "Gửi <code>@username</code>, link <code>t.me/...</code> hoặc ID kênh.")

    if data == "rd_login_acc":
        return flow_prompt(cid, c.message.message_id, "admin_login_game", "🔐 LOGIN TK GAME",
                           "Gửi theo mẫu: <code>TÀI_KHOẢN MẬT_KHẨU</code>\n"
                           "Hệ thống tự lấy nickname và số dư.")

    if data == "rd_delacc":
        sync_redeem_accounts()
        if not REDEEM_ACCOUNTS:
            return go("📭 Danh sách tài khoản đang trống.", redeem_menu())
        return flow_prompt(cid, c.message.message_id, "admin_del_redeem", "➖ XOÁ TÀI KHOẢN",
                           "Gửi đúng <b>nickname</b> hoặc <b>username</b> cần xoá.")

    if data == "rd_list":
        sync_redeem_accounts()
        if not REDEEM_ACCOUNTS:
            return go("📭 Chưa có tài khoản nào.", redeem_menu())
        lines = [f"{i}. {'🟢' if a.is_token_valid() else '🟡'} <b>{html.escape(a.nick)}</b> "
                 f"(<code>{html.escape(a.un)}</code>) · 💰 {a.balance:,}"
                 for i, a in enumerate(REDEEM_ACCOUNTS, 1)]
        return go(f"<b>📋 DANH SÁCH TÀI KHOẢN</b>\n{DIV}\n" + "\n".join(lines), redeem_menu())

    if data == "rd_refresh":
        sync_redeem_accounts()
        if not REDEEM_ACCOUNTS:
            return go("📭 Không có tài khoản nào để cập nhật.", redeem_menu())
        go("🔄 <b>Đang làm mới token và số dư...</b>", redeem_menu())

        def refresh_all_worker():
            ok = 0
            for acc in REDEEM_ACCOUNTS:
                try:
                    if acc.refresh_token():
                        ok += 1
                except Exception:
                    pass
            lines = [f"• <b>{html.escape(a.nick)}</b>: 💰 {a.balance:,}" for a in REDEEM_ACCOUNTS]
            try:
                bot.send_message(
                    cid,
                    f"✅ <b>Đã cập nhật {ok}/{len(REDEEM_ACCOUNTS)} tài khoản</b>\n{DIV}\n" + "\n".join(lines),
                    parse_mode="HTML", reply_markup=redeem_menu(),
                )
            except Exception:
                pass

        threading.Thread(target=refresh_all_worker, daemon=True).start()
        return

    if data == "export_tokens":
        send_tokens_txt(cid)
        return

    if data == "export_history":
        send_full_history_txt(cid)
        return

    if data == "tele_panel":
        me, session_str = get_tele_account_details()
        if me:
            full_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "N/A"
            uname = f"@{me.username}" if me.username else "Không có"
            info_block = (f"🟢 Online, đang theo dõi\n👤 {html.escape(full_name)}\n"
                          f"🔗 {html.escape(uname)}")
        elif session_str:
            info_block = "🟡 Đã lưu session, đang chờ kết nối"
        else:
            info_block = "🔴 Chưa đăng nhập Tele nguồn"
        ch_name = html.escape(redeem_state.get("channel_name") or "Chưa đặt")
        return go(f"<b>📡 TELE NGUỒN</b>\n{DIV}\n{info_block}\n\n📢 Kênh theo dõi: {ch_name}", tele_menu())

    if data == "tele_login":
        return flow_prompt(cid, c.message.message_id, "tele_phone", "🔐 ĐĂNG NHẬP TELE NGUỒN",
                           "Gửi số điện thoại kèm mã vùng, ví dụ <code>+849xxxxxxxx</code>.")

    if data == "tele_export_session":
        me, session_str = get_tele_account_details()
        if not session_str:
            return go("❌ Chưa có session nào. Hãy đăng nhập Tele trước.", tele_menu())
        return go(f"<b>🔑 SESSION TELE NGUỒN</b>\n{DIV}\n<code>{html.escape(session_str)}</code>", tele_menu())

    if data == "tele_status_btn":
        me, session_str = get_tele_account_details()
        return go(
            f"<b>📊 TRẠNG THÁI TELE</b>\n{DIV}\n"
            f"🔑 API ID/HASH: {'✅ Đã cấu hình' if TELETHON_API_ID and TELETHON_API_HASH else '❌ Thiếu'}\n"
            f"💾 Session: {'✅ Đã lưu' if session_str else '❌ Chưa có'}\n"
            f"🟢 Client: {'✅ Đang hoạt động' if tele_ready else '🔴 Ngoại tuyến'}\n"
            f"📡 Kênh: {html.escape(redeem_state.get('channel_name') or 'Chưa đặt')}",
            tele_menu(),
        )

    if data == "tele_logout_confirm":
        kb = telebot.types.InlineKeyboardMarkup(row_width=2)
        kb.add(_btn("🗑️ Xác nhận xoá", "tele_logout_yes"), _btn("↩️ Quay lại", "tele_panel"))
        return go("⚠️ Bạn chắc chắn muốn xoá session Tele nguồn?", kb)

    if data == "tele_logout_yes":
        if os.path.exists(TELE_SESSION_FILE):
            os.remove(TELE_SESSION_FILE)
        return go("✅ Đã xoá session Tele nguồn.", tele_menu())


# ========================= ẢNH BIÊN LAI =========================
@bot.message_handler(content_types=["photo"])
def handle_photo(m):
    cid = m.chat.id
    flow = user_flows.get(cid, {})
    if flow.get("step") != "await_proof":
        return bot.send_message(cid, "📷 Nếu bạn đang thanh toán, hãy mở đơn và bấm <b>Đã chuyển khoản, gửi ảnh</b>.",
                                parse_mode="HTML")
    oid = flow.get("order")
    o = orders.get(oid)
    if not o or o["user"] != cid or o["status"] != "waiting_payment":
        user_flows.pop(cid, None)
        return bot.send_message(cid, "⚠️ Đơn không còn hiệu lực.", reply_markup=main_menu(cid))
    file_id = m.photo[-1].file_id
    o["status"] = "pending_review"
    o["proof_file_id"] = file_id
    user_flows.pop(cid, None)
    save_orders()
    notify_admin_order(oid, file_id)
    bot.send_message(
        cid,
        f"✅ <b>Đã nhận biên lai đơn {oid}</b>\n{DIV}\n"
        f"Admin sẽ kiểm tra và gửi key cho bạn ngay khi duyệt xong.",
        parse_mode="HTML", reply_markup=main_menu(cid),
    )


# ========================= NHẬP LIỆU TỪ CHAT =========================
@bot.message_handler(func=lambda m: bool(m and m.chat and m.chat.id in user_flows), content_types=["text"])
def handle_menu_input(m):
    global REDEEM_ACCOUNTS
    cid = m.chat.id
    flow_state = user_flows.get(cid, {})
    flow = flow_state.get("step")
    value = (m.text or "").strip()

    try:
        bot.delete_message(cid, m.message_id)
    except Exception:
        pass

    if flow == "await_proof":
        return bot.send_message(cid, "📷 Hãy gửi <b>ảnh</b> biên lai chuyển khoản.", parse_mode="HTML")

    if value.lower() in ("huy", "huỷ", "cancel", "/cancel"):
        user_flows.pop(cid, None)
        return bot.send_message(cid, "↩️ Đã huỷ.", reply_markup=main_menu(cid))

    if flow == "admin_login_game":
        is_admin_flow = (flow == "admin_login_game")
        if cid != ADMIN_ID:
            user_flows.pop(cid, None)
            return

        parts = value.split(maxsplit=1)
        if len(parts) != 2:
            return bot.send_message(cid, "⚠️ Sai định dạng. Gửi: <code>TÀI_KHOẢN MẬT_KHẨU</code>", parse_mode="HTML")

        un, pw = parts[0].strip(), parts[1].strip()
        status_msg = bot.send_message(cid, f"🔄 Đang đăng nhập <code>{html.escape(un)}</code>...", parse_mode="HTML")

        def do_login_task():
            res = login_and_get_info(un, pw)
            back_kb = redeem_menu() if is_admin_flow else main_menu(cid)
            if "_error" in res:
                try:
                    bot.edit_message_text(f"❌ Đăng nhập thất bại: {html.escape(res['_error'])}",
                                          cid, status_msg.message_id, parse_mode="HTML", reply_markup=back_kb)
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
                    f"✅ <b>Đăng nhập thành công</b>\n{DIV}\n"
                    f"👤 Nickname: <b>{html.escape(nick)}</b>\n"
                    f"🔑 Tài khoản: <code>{html.escape(un)}</code>\n"
                    f"💰 Số dư: <b>{money:,}</b>\n"
                    f"⚡ Đã sẵn sàng lụm code.",
                    cid, status_msg.message_id, parse_mode="HTML", reply_markup=back_kb,
                )
            except Exception:
                pass

        threading.Thread(target=do_login_task, daemon=True).start()
        return

    if flow == "admin_del_redeem":
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

        back_kb = redeem_menu() if is_admin_flow else main_menu(cid)
        if not removed:
            return bot.send_message(cid, "❌ Không tìm thấy tài khoản phù hợp. Gửi lại tên khác.",
                                    reply_markup=back_kb)

        redeem_state["accounts"] = new_list
        save_json(REDEEM_STATE_FILE, redeem_state)
        REDEEM_ACCOUNTS = build_redeem_accounts()
        user_flows.pop(cid, None)
        return bot.send_message(cid, f"🗑️ Đã xoá: {', '.join(removed)}", reply_markup=back_kb)

    if flow == "set_channel" and cid == ADMIN_ID:
        target = value
        if "t.me/" in target:
            target = target.split("t.me/")[-1].strip("/").split("?")[0]
        if not target.startswith("@") and not target.lstrip("-").isdigit():
            target = "@" + target
        status_msg = bot.send_message(cid, f"🔄 Đang kết nối kênh <code>{html.escape(target)}</code>...",
                                      parse_mode="HTML")

        def resolve_ch():
            try:
                entity = tele_resolve_entity(target)
                peer_id = utils.get_peer_id(entity)
                redeem_state["channel_id"] = peer_id
                redeem_state["channel_name"] = getattr(entity, "title", None) or target
                save_json(REDEEM_STATE_FILE, redeem_state)
                user_flows.pop(cid, None)
                bot.edit_message_text(
                    f"✅ <b>Đã đặt kênh nhận code</b>\n📡 {html.escape(redeem_state['channel_name'])}\n🆔 <code>{peer_id}</code>",
                    cid, status_msg.message_id, parse_mode="HTML", reply_markup=redeem_menu(),
                )
            except Exception as exc:
                bot.edit_message_text(
                    f"❌ Lỗi đặt kênh: {html.escape(str(exc))}\n💡 Hãy chắc chắn đã đăng nhập Tele nguồn.",
                    cid, status_msg.message_id, parse_mode="HTML", reply_markup=redeem_menu(),
                )

        threading.Thread(target=resolve_ch, daemon=True).start()
        return

    if flow == "tele_phone" and cid == ADMIN_ID:
        if not TELETHON_API_ID or not TELETHON_API_HASH:
            user_flows.pop(cid, None)
            return bot.send_message(cid, "❌ Thiếu TELETHON_API_ID hoặc TELETHON_API_HASH.", reply_markup=tele_menu())
        status_msg = bot.send_message(cid, "📲 Đang gửi mã OTP...")

        def send_otp_task():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                client = TelegramClient(StringSession(), TELETHON_API_ID, TELETHON_API_HASH, loop=loop)
                loop.run_until_complete(client.connect())
                sent = loop.run_until_complete(client.send_code_request(value))
                tele_login_pending.update({"phone": value, "hash": sent.phone_code_hash, "client": client, "loop": loop})
                user_flows[cid] = {"step": "tele_otp"}
                bot.edit_message_text("📲 Đã gửi OTP. Nhập mã vào khung chat:", cid, status_msg.message_id)
            except Exception as exc:
                user_flows.pop(cid, None)
                bot.edit_message_text(f"❌ Lỗi gửi OTP: {html.escape(str(exc))}", cid, status_msg.message_id,
                                      parse_mode="HTML", reply_markup=tele_menu())

        threading.Thread(target=send_otp_task, daemon=True).start()
        return

    if flow == "tele_otp" and cid == ADMIN_ID:
        pending = tele_login_pending
        if not pending.get("client"):
            user_flows.pop(cid, None)
            return bot.send_message(cid, "❌ Phiên OTP đã hết hạn.", reply_markup=tele_menu())
        status_msg = bot.send_message(cid, "🔐 Đang xác thực OTP...")

        def verify_otp_task():
            client, loop = pending["client"], pending["loop"]
            try:
                loop.run_until_complete(client.sign_in(phone=pending["phone"], code=value,
                                                       phone_code_hash=pending["hash"]))
                _save_tele_session(client.session.save())
                tele_login_pending.clear()
                user_flows.pop(cid, None)
                bot.edit_message_text("✅ Đăng nhập Tele thành công. Đang bật canh kênh...",
                                      cid, status_msg.message_id, reply_markup=tele_menu())
                _activate_telethon_client(client, loop)
            except Exception as exc:
                if "SessionPasswordNeeded" in type(exc).__name__ or "password" in str(exc).lower():
                    user_flows[cid] = {"step": "tele_password"}
                    bot.edit_message_text("🔐 Tài khoản có bật 2FA. Nhập mật khẩu 2FA:",
                                          cid, status_msg.message_id)
                else:
                    bot.edit_message_text(f"❌ Lỗi OTP: {html.escape(str(exc))}", cid, status_msg.message_id,
                                          parse_mode="HTML", reply_markup=tele_menu())

        threading.Thread(target=verify_otp_task, daemon=True).start()
        return

    if flow == "tele_password" and cid == ADMIN_ID:
        pending = tele_login_pending
        status_msg = bot.send_message(cid, "🔐 Đang xác thực 2FA...")

        def verify_2fa_task():
            try:
                client, loop = pending["client"], pending["loop"]
                loop.run_until_complete(client.sign_in(password=value))
                _save_tele_session(client.session.save())
                tele_login_pending.clear()
                user_flows.pop(cid, None)
                bot.edit_message_text("✅ Đăng nhập Tele thành công. Đang bật canh kênh...",
                                      cid, status_msg.message_id, reply_markup=tele_menu())
                _activate_telethon_client(client, loop)
            except Exception as exc:
                bot.edit_message_text(f"❌ Sai mật khẩu 2FA: {html.escape(str(exc))}",
                                      cid, status_msg.message_id, parse_mode="HTML", reply_markup=tele_menu())

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