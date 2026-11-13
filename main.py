import os
import math
import json
import base64
import hashlib
import html
import logging
import random
import string
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timedelta

import requests
import socketio
import telebot
from flask import Flask

BOT_NAME = "Auto Cược LC79"

# =========================
# FLASK KEEP ALIVE (RENDER)
# =========================
app = Flask(__name__)


@app.route("/")
def home():
    return f"🤖 {BOT_NAME} ONLINE"


@app.route("/health")
def health():
    return {"status": "online", "bot": "LC79", "server": "Render"}


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# =========================
# CẤU HÌNH
# =========================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("lc79")
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "@admin")
bot = telebot.TeleBot(BOT_TOKEN)

HISTORY_API_URL = "https://wtxmd52.tele68.com/v1/txmd5/sessions"
API_URL_FALLBACK = "https://living-telecommunications-start-consoles.trycloudflare.com/api/txmd5"
WS_URL = "https://wtxmd52.tele68.com"
MAX_HISTORY = 500
SYNC_INTERVAL = 20
MAX_MARTINGALE_LEVEL = 6
MARTINGALE_MULTIPLIER = 2

# Tỷ lệ trả thưởng: thắng lãi 1.98 lần số tiền cược, thua mất 1 lần.
WIN_PROFIT = 1.98
LOSE_LOSS = 1.0

TAI, XIU = "TAI", "XIU"
TAI_LABEL, XIU_LABEL = "TÀI", "XỈU"

# Chế độ: ngưỡng độ chắc chắn (xác suất hậu nghiệm P(đúng phía > 50%)) và số phiên tối thiểu.
MODES = {
    "antoan":   {"label": "🟢 AN TOÀN",  "post": 0.97, "min_sessions": 80},
    "canbang":  {"label": "🟡 CÂN BẰNG", "post": 0.92, "min_sessions": 50},
    "maohiem":  {"label": "🔴 MẠO HIỂM", "post": 0.0, "min_sessions": 1, "never_skip": True},
}
DEFAULT_MODE = "canbang"

bot.set_my_commands([
    telebot.types.BotCommand("batdau", "🏠 Giới thiệu và trạng thái"),
    telebot.types.BotCommand("trogiup", "📖 Hướng dẫn đầy đủ"),
    telebot.types.BotCommand("nhapkey", "🔑 /nhapkey MÃ_KEY"),
    telebot.types.BotCommand("dangnhap", "🔐 /dangnhap TÀI_KHOẢN MẬT_KHẨU"),
    telebot.types.BotCommand("tudong", "⚡ /tudong bat [tiền] | tat"),
    telebot.types.BotCommand("x2", "💸 /x2 bat | tat"),
    telebot.types.BotCommand("caidat", "⚙️ /caidat cuoc= sl= sw= limit= mode= thongbao="),
    telebot.types.BotCommand("thongtin", "💎 Tài khoản và thống kê"),
    telebot.types.BotCommand("dung", "⏹️ Ngắt kết nối an toàn"),
    telebot.types.BotCommand("taokey", "👑 /taokey [số_ngày] (ADMIN)"),
    telebot.types.BotCommand("danhsachkey", "📋 Danh sách key (ADMIN)"),
])

active_sockets = {}    # chat_id -> socketio.Client
user_states = {}       # chat_id -> state dict
valid_keys = {}        # KEY -> số ngày
authorized_users = {}  # chat_id -> timestamp hết hạn


# ============================================================
# TIỆN ÍCH
# ============================================================
def norm(s):
    """Bỏ dấu tiếng Việt và viết thường để nhận cả 'bật' lẫn 'bat'."""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def parse_amount(s):
    s = s.lower().replace(",", "").strip()
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    value = int(s) * mult
    if value < 0:
        raise ValueError("số tiền không được âm")
    return value


def parse_kv(text):
    out = {}
    for tok in text.split()[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[norm(k)] = v.strip()
    return out


def fmt(n):
    return f"{int(round(n)):,}".replace(",", ".")


def signed(n):
    n = int(round(n))
    return f"+{fmt(n)}" if n > 0 else (f"-{fmt(-n)}" if n < 0 else "0")


def bar(p, width=10):
    p = max(0.0, min(1.0, p))
    filled = round(p * width)
    return "█" * filled + "░" * (width - filled)


def side_label(side):
    return TAI_LABEL if side == TAI else XIU_LABEL


def trail(seq, n=10):
    return "".join("🔵" if x == TAI else "🔴" for x in seq[-n:])


# ============================================================
# THUẬT TOÁN
# ============================================================
def beta_prob_above(a, b, x0=0.5, steps=400):
    """P(X > x0) với X ~ Beta(a, b), tính bằng tích phân Simpson. a, b >= 1."""
    if a <= 0 or b <= 0:
        return 0.5
    lnB = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)

    def f(x):
        if x <= 0.0 or x >= 1.0:
            return 0.0
        return math.exp((a - 1) * math.log(x) + (b - 1) * math.log(1 - x) - lnB)

    h = (1.0 - x0) / steps
    total = f(x0) + f(1.0)
    for i in range(1, steps):
        total += (4 if i % 2 else 2) * f(x0 + i * h)
    return min(1.0, max(0.0, total * h / 3))


class CauEngine:
    """Ước lượng xác suất TÀI bằng chuỗi Markov bậc 1-3 với backoff nội suy.

    - Trọng số giảm theo thời gian, tính bằng hai nửa đời khác nhau (nhanh và chậm).
    - Backoff: ngữ cảnh bậc cao được kéo về ước lượng bậc thấp hơn, tránh tin mẫu quá ít.
    - Hậu nghiệm Beta: đo độ chắc chắn P(xác suất thật của phía chọn > 50%).
    - Hai mô hình phải cùng chọn một phía, nếu không thì bỏ qua (chặn bắt cầu nhiễu).
    - Chỉ đánh khi độ chắc chắn vượt ngưỡng chế độ và kỳ vọng dương.
    """

    ORDERS = (1, 2, 3)
    HALF_LIFE_FAST = 40.0
    HALF_LIFE_SLOW = 200.0
    BETA = 4.0

    def __init__(self):
        self.recs = {}                 # session_id -> (kết quả, tổng)
        self.perf = deque(maxlen=80)   # (phía đã đoán, kết quả thực, p ước lượng)
        self.skips = 0
        self.extra_post = 0.0          # cộng thêm vào ngưỡng khi độ chính xác thấp

    def ingest(self, records):
        added = 0
        for rec in records:
            sid, res = rec.get("session"), rec.get("result")
            if sid is None or res not in (TAI, XIU) or sid in self.recs:
                continue
            self.recs[sid] = (res, rec.get("total", 0))
            added += 1
        if added:
            ordered = sorted(self.recs.items(), key=lambda kv: kv[0])[-MAX_HISTORY:]
            self.recs = dict(ordered)
        return added

    def results(self):
        return [v[0] for v in self.recs.values()]

    @staticmethod
    def _decayed_counts(seq, order, half_life):
        n = len(seq)
        w = {TAI: 0.0, XIU: 0.0}
        if order == 0:
            for i in range(n):
                w[seq[i]] += 0.5 ** ((n - 1 - i) / half_life)
            return w
        ctx = seq[-order:]
        for i in range(order, n):
            if seq[i - order:i] == ctx:
                w[seq[i]] += 0.5 ** ((n - 1 - i) / half_life)
        return w

    def _estimate(self, seq, half_life):
        """Trả về (p_TAI đã backoff, n hiệu dụng ở bậc cao nhất có dữ liệu)."""
        base = self._decayed_counts(seq, 0, half_life)
        p = (base[TAI] + 1) / (base[TAI] + base[XIU] + 2)
        n_last = 0.0
        for k in self.ORDERS:
            if len(seq) <= k:
                break
            w = self._decayed_counts(seq, k, half_life)
            n_k = w[TAI] + w[XIU]
            p = (w[TAI] + self.BETA * p) / (n_k + self.BETA)
            n_last = n_k
        return p, n_last

    def predict(self, mode=DEFAULT_MODE):
        cfg = MODES.get(mode, MODES[DEFAULT_MODE])
        never_skip = cfg.get("never_skip", False)
        seq = self.results()
        n = len(seq)
        out = {"side": None, "p": 0.5, "post": 0.5, "n_eff": 0.0, "ev": 0.0,
               "reason": "", "agree": True, "ctx": "Markov 1→2→3 · backoff"}
        if n < cfg["min_sessions"] and not never_skip:
            out["reason"] = f"Cần ít nhất {cfg['min_sessions']} phiên, đang có {n}"
            return out

        p_f, n_f = self._estimate(seq, self.HALF_LIFE_FAST)
        p_s, n_s = self._estimate(seq, self.HALF_LIFE_SLOW)
        p_tai = (p_f + p_s) / 2
        n_eff = (n_f + n_s) / 2
        agree = (p_f >= 0.5) == (p_s >= 0.5)
        side = TAI if p_tai >= 0.5 else XIU
        p_side = max(p_tai, 1 - p_tai)
        post_tai = beta_prob_above(p_tai * n_eff + 1, (1 - p_tai) * n_eff + 1)
        post = post_tai if side == TAI else 1 - post_tai
        ev_unit = p_side * WIN_PROFIT - (1 - p_side) * LOSE_LOSS
        out.update({"p": p_tai, "n_eff": n_eff, "post": post, "ev": ev_unit, "agree": agree})

        if never_skip:
            out["side"] = side
            out["reason"] = ("Hai mô hình đồng thuận" if agree
                             else "Hai mô hình lệch, lấy trung bình của cả hai")
            if n < cfg["min_sessions"]:
                out["reason"] += f" · mới có {n} phiên"
            return out

        need = cfg["post"] + self.extra_post
        if not agree:
            self.skips += 1
            out["reason"] = "Hai mô hình nhanh và chậm không đồng thuận"
            return out
        if post < need:
            self.skips += 1
            out["reason"] = f"Độ chắc chắn {post:.1%} dưới ngưỡng {need:.1%}"
            return out
        if ev_unit <= 0:
            self.skips += 1
            out["reason"] = "Kỳ vọng âm, bỏ qua"
            return out
        out["side"] = side
        out["reason"] = f"Đồng thuận hai mô hình, độ chắc chắn {post:.1%}"
        return out

    def record(self, pred, p_side, actual):
        if pred is None:
            return
        self.perf.append((pred, actual, p_side))
        if len(self.perf) >= 20:
            acc = self.accuracy() / 100
            if acc < 0.47:
                self.extra_post = min(0.04, self.extra_post + 0.01)
            elif acc > 0.55:
                self.extra_post = max(0.0, self.extra_post - 0.005)

    def accuracy(self):
        if not self.perf:
            return 0.0
        hits = sum(1 for pr, ac, _ in self.perf if pr == ac)
        return hits / len(self.perf) * 100

    def expected_roi(self):
        """ROI kỳ vọng theo xác suất ước lượng của các lần đã đánh (đơn vị: phần cược)."""
        if not self.perf:
            return 0.0
        return sum(p * WIN_PROFIT - (1 - p) * LOSE_LOSS for _, _, p in self.perf) / len(self.perf)


# ============================================================
# TRẠNG THÁI NGƯỜI DÙNG
# ============================================================
def new_state():
    return {
        "engine": CauEngine(),
        "running": False,
        "gen": 0,
        "auto": False,
        "martingale": False,
        "mart_level": 0,
        "base_bet": 10000,
        "mode": DEFAULT_MODE,
        "notify": True,
        "stop_loss": 0,
        "stop_win": 0,
        "daily_limit": 0,
        "day": datetime.now().strftime("%Y-%m-%d"),
        "day_bet": 0,
        "session_id": None,
        "pred_session": None,
        "prediction": None,
        "pred_info": None,
        "bet_placed": False,
        "last_bet": 0,
        "balance": 0,
        "profit": 0,
        "total_win": 0,
        "total_lose": 0,
        "win_streak": 0,
        "lose_streak": 0,
        "total_bet": 0,
    }


def get_state(cid):
    if cid not in user_states:
        user_states[cid] = new_state()
    return user_states[cid]


def current_bet(st):
    if not st["martingale"] or st["mart_level"] <= 0:
        return st["base_bet"]
    lv = min(st["mart_level"], MAX_MARTINGALE_LEVEL)
    return st["base_bet"] * (MARTINGALE_MULTIPLIER ** lv)


def risk_block(st, amount):
    today = datetime.now().strftime("%Y-%m-%d")
    if st["day"] != today:
        st["day"], st["day_bet"] = today, 0
    if st["stop_loss"] and st["profit"] <= -st["stop_loss"]:
        return "Đã chạm mức DỪNG LỖ"
    if st["stop_win"] and st["profit"] >= st["stop_win"]:
        return "Đã chạm mức DỪNG LÃI"
    if st["daily_limit"] and st["day_bet"] + amount > st["daily_limit"]:
        return "Vượt GIỚI HẠN CƯỢC NGÀY"
    if st["balance"] and amount > st["balance"]:
        return "Không đủ số dư"
    return None


def notify(cid, text, force=False):
    st = get_state(cid)
    if not (force or st["notify"]):
        return
    try:
        bot.send_message(cid, text, parse_mode="HTML")
    except Exception as e:
        log.warning("Không gửi được tin cho %s: %s", cid, e)


# ============================================================
# GIAO DIỆN THẺ DỰ ĐOÁN
# ============================================================
LINE = "━━━━━━━━━━━━━━━━━━━━"


def esc(s):
    return html.escape(str(s), quote=False)


def prediction_card(sid, info, st):
    mode = MODES[st["mode"]]
    recent = trail(st["engine"].results(), 10) or "—"
    head = f"🎯 <b>DỰ ĐOÁN PHIÊN #{sid}</b>"
    if info["side"] is None:
        return (f"{head}\n{mode['label']}\n{LINE}\n"
                f"🚫 <b>BỎ QUA PHIÊN NÀY</b>\n"
                f"📋 {esc(info['reason'])}\n{LINE}\n"
                f"🧾 {recent}")

    side = info["side"]
    p_side = info["p"] if side == TAI else 1 - info["p"]
    bet = current_bet(st)
    x2 = f"X2 cấp {st['mart_level']}/{MAX_MARTINGALE_LEVEL}" if st["martingale"] else "Mức gốc"
    extra = "\n🔥 Đánh mọi phiên, không bỏ phiên nào" if mode.get("never_skip") else ""
    return (f"{head}\n{mode['label']}\n{LINE}\n"
            f"🎲 Đánh: <b>{side_label(side)}</b> {'⚫' if side == TAI else '⚪'}\n"
            f"<pre>"
            f"Xác suất   {p_side:6.1%}  {bar(p_side)}\n"
            f"Chắc chắn  {info['post']:6.1%}  {'không chặn' if mode.get('never_skip') else 'ngưỡng ' + format(mode['post'], '.0%')}\n"
            f"Mẫu hiệu   {info['n_eff']:6.1f}"
            f"</pre>\n"
            f"{LINE}\n"
            f"💰 Cược      <b>{fmt(bet)}</b> · {x2}\n"
            f"✅ Thắng     <b>{signed(bet * WIN_PROFIT)}</b>  x{WIN_PROFIT:g}\n"
            f"❌ Thua      <b>-{fmt(bet * LOSE_LOSS)}</b>  x{LOSE_LOSS:g}\n"
            f"📉 Kỳ vọng   {signed(bet * info['ev'])}\n"
            f"{LINE}\n"
            f"🧠 {esc(info['ctx'])}\n"
            f"ℹ️ {esc(info['reason'])}{extra}\n"
            f"🧾 {recent}")


def result_card(sid, res, pred, bet_done, won, bet, st):
    head = f"🎲 <b>KẾT QUẢ PHIÊN #{sid}: {side_label(res)}</b>"
    recent = trail(st["engine"].results(), 10) or "—"
    if not bet_done:
        return f"{head}\n{LINE}\n⏭️ Không đặt cược phiên này\n🧾 {recent}"
    delta = bet * WIN_PROFIT if won else -bet * LOSE_LOSS
    verdict = "✅ <b>THẮNG</b>" if won else "❌ <b>THUA</b>"
    return (f"{head}\n{LINE}\n"
            f"🎯 Đã đoán {side_label(pred)} · Cược {fmt(bet)}\n"
            f"{verdict}  <b>{signed(delta)}</b>\n"
            f"{LINE}\n"
            f"<pre>"
            f"Lợi nhuận phiên   {signed(st['profit'])}\n"
            f"Chuỗi thắng       {st['win_streak']}\n"
            f"Chuỗi thua        {st['lose_streak']}"
            f"</pre>\n"
            f"🧾 {recent}")


def setup_text(st):
    return (f"⚙️ CÀI ĐẶT HIỆN TẠI\n"
            f"💰 cuoc = {fmt(st['base_bet'])}\n"
            f"🛑 sl = {fmt(st['stop_loss'])} (0 là tắt)\n"
            f"🏆 sw = {fmt(st['stop_win'])} (0 là tắt)\n"
            f"📅 limit = {fmt(st['daily_limit'])} (0 là tắt)\n"
            f"🎯 mode = {st['mode']} (antoan | canbang | maohiem)\n"
            f"🔔 thongbao = {'bat' if st['notify'] else 'tat'} (bat | tat)")


def account_text(cid, st):
    han = "👑 ADMIN VĨNH VIỄN" if cid == ADMIN_ID else format_expire_time(authorized_users.get(cid, 0))
    total = st["total_win"] + st["total_lose"]
    wr = st["total_win"] / total * 100 if total else 0
    eng = st["engine"]
    x2 = f"BẬT · cấp {st['mart_level']}/{MAX_MARTINGALE_LEVEL}" if st["martingale"] else "TẮT"
    return (f"💎 {BOT_NAME} · THÔNG TIN\n{LINE}\n"
            f"🆔 {cid}\n"
            f"⏳ Hạn dùng: {han}\n"
            f"🔐 Kết nối: {'🟢 ĐANG CHẠY NGẦM' if st['running'] else '🔴 CHƯA ĐĂNG NHẬP'}\n"
            f"⚡ Tự động: {'🟢 BẬT' if st['auto'] else '🔴 TẮT'} · X2: {x2}\n"
            f"🎯 Chế độ: {MODES[st['mode']]['label']}\n"
            f"💰 Cược gốc: {fmt(st['base_bet'])} · Cược hiện tại: {fmt(current_bet(st))}\n"
            f"{LINE}\n"
            f"💵 Số dư: {fmt(st['balance'])}\n"
            f"📈 Lợi nhuận phiên: {signed(st['profit'])}\n"
            f"✅ Thắng: {st['total_win']} · ❌ Thua: {st['total_lose']} · Tỷ lệ: {wr:.1f}%\n"
            f"🎯 Độ chính xác dự đoán: {eng.accuracy():.1f}% ({len(eng.perf)} lần)\n"
            f"📐 ROI kỳ vọng mô hình: {eng.expected_roi() * 100:+.1f}% mỗi cược\n"
            f"📚 Phiên đã nạp: {len(eng.recs)} · Phiên đã bỏ qua: {eng.skips}\n"
            f"🧾 {trail(eng.results(), 10)}")


COMMAND_LIST = """🔑 <b>KÍCH HOẠT</b>
<code>/nhapkey MÃ_KEY</code> — Kích hoạt VIP bằng key admin cấp
🔐 <b>KẾT NỐI</b>
<code>/dangnhap TK MK</code> — Đăng nhập, nạp lịch sử, chạy ngầm
<code>/dung</code> — Ngắt kết nối an toàn
⚡ <b>ĐẶT CƯỢC</b>
<code>/tudong bat 10k</code> — Bật tự động, mỗi phiên cược 10.000
<code>/tudong tat</code> — Tắt tự động
<code>/x2 bat</code> — Thua thì nhân đôi cược, tối đa 6 cấp
<code>/x2 tat</code> — Tắt X2, về mức gốc
⚙️ <b>CÀI ĐẶT</b>
<code>/caidat</code> — Xem cài đặt hiện tại
<code>/caidat cuoc=10k sl=1m sw=5m limit=50m mode=maohiem thongbao=bat</code> — Đổi nhiều mục cùng lúc
📊 <b>TÀI KHOẢN</b>
<code>/thongtin</code> — Số dư, lợi nhuận, độ chính xác
📖 <b>TRỢ GIÚP</b>
<code>/batdau</code> — Màn hình này
<code>/trogiup</code> — Hướng dẫn chi tiết
👑 <b>ADMIN</b>
<code>/taokey 30</code> — Tạo key VIP 30 ngày
<code>/danhsachkey</code> — Xem key chưa dùng"""

HELP_TEXT = f"""📖 <b>{BOT_NAME} · HƯỚNG DẪN CHI TIẾT</b>
Gõ lệnh bắt đầu bằng dấu /. Phần tham số có dấu hoặc không dấu đều được.

🔑 <b>Kích hoạt</b>
<code>/nhapkey MÃ_KEY</code>: nhập key admin cấp để mở VIP.

🔐 <b>Kết nối</b>
<code>/dangnhap TÀI_KHOẢN MẬT_KHẨU</code>: đăng nhập, tải lịch sử và chạy ngầm nạp phiên mới mỗi {SYNC_INTERVAL} giây.
<code>/dung</code>: ngắt kết nối, tắt tự động và X2.

⚡ <b>Đặt cược</b>
<code>/tudong bat 10k</code>: bật tự động, mỗi phiên cược 10.000. Viết được 10k hoặc 1m.
<code>/tudong tat</code>: tắt tự động.
<code>/x2 bat</code>: thua thì nhân đôi cược cho đến khi thắng, tối đa {MAX_MARTINGALE_LEVEL} cấp.
<code>/x2 tat</code>: tắt X2.

⚙️ <b>Cài đặt</b>
<code>/caidat cuoc=10k sl=1m sw=5m limit=50m mode=canbang thongbao=bat</code>
• cuoc: mức cược gốc
• sl: dừng khi lỗ đủ số tiền này trong phiên
• sw: dừng khi lãi đủ số tiền này trong phiên
• limit: tổng tiền cược tối đa mỗi ngày
• mode: antoan, canbang hoặc maohiem
• thongbao: bat hoặc tat, bật tắt thông báo từng phiên

🎯 <b>Chế độ</b>
🟢 antoan: chỉ đánh khi độ chắc chắn từ 97%.
🟡 canbang: chỉ đánh khi độ chắc chắn từ 92%.
🔴 maohiem: đánh mọi phiên, không bỏ phiên nào.

📐 <b>Tính tiền</b>
Thắng: lãi x{WIN_PROFIT:g} số tiền cược. Thua: mất x{LOSE_LOSS:g}.

📝 Kết quả Tài/Xỉu về cơ bản là ngẫu nhiên. Bot không đảm bảo thắng.
📩 Hỗ trợ: {ADMIN_USERNAME}"""


# ============================================================
# NẠP PHIÊN
# ============================================================
def parse_lines(data):
    if not data or not isinstance(data.get("list"), list):
        return []
    arr = sorted(data["list"], key=lambda x: x["id"])
    out = []
    for it in arr:
        dices = it.get("dices", [0, 0, 0])
        total = it.get("point", sum(dices))
        r = it.get("resultTruyenThong")
        if r in (TAI, XIU):
            out.append({"session": it["id"], "dice": dices, "total": total, "result": r})
    return out


def fetch_history_from_api(limit=MAX_HISTORY):
    for url in (HISTORY_API_URL, API_URL_FALLBACK):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Origin": "https://lc79b.bet"}, timeout=10)
            if r.status_code != 200:
                continue
            arr = parse_lines(r.json())
            if arr:
                return arr[-limit:]
        except Exception:
            continue
    return []


def sync_loop(cid, gen):
    st = get_state(cid)
    while st["running"] and st["gen"] == gen:
        try:
            arr = fetch_history_from_api(MAX_HISTORY)
            if arr:
                added = st["engine"].ingest(arr)
                if added:
                    log.info("[%s] nạp thêm %d phiên, tổng %d", cid, added, len(st["engine"].recs))
        except Exception:
            log.exception("Lỗi nạp phiên ngầm cho %s", cid)
        for _ in range(SYNC_INTERVAL):
            if not (st["running"] and st["gen"] == gen):
                return
            time.sleep(1)


# ============================================================
# AUTH
# ============================================================
def check_auth(chat_id):
    if chat_id == ADMIN_ID:
        return True
    if chat_id in authorized_users:
        if time.time() <= authorized_users[chat_id]:
            return True
        del authorized_users[chat_id]
    return False


def require_auth(fn):
    def wr(m, *a, **kw):
        if not check_auth(m.chat.id):
            bot.reply_to(m, f"🔒 CHƯA KÍCH HOẠT\n👉 /nhapkey MÃ_KEY\n📩 {ADMIN_USERNAME}")
            return
        return fn(m, *a, **kw)
    return wr


def format_expire_time(ts):
    r = ts - time.time()
    if r <= 0:
        return "❌ HẾT HẠN"
    d = int(r // 86400)
    h = int((r % 86400) // 3600)
    m = int((r % 3600) // 60)
    if d > 0:
        return f"✅ CÒN {d} NGÀY {h}G {m}P"
    return f"✅ CÒN {h}G {m}P"


# ============================================================
# ĐĂNG NHẬP + SOCKET
# ============================================================
def md5_hash(t):
    return hashlib.md5(t.encode()).hexdigest()


def login_and_get_token(u, p):
    try:
        r = requests.get(f"https://apifo88daigia.tele68.com/api?c=3&un={u}&pw={md5_hash(p)}&cp=R&cl=R&pf=web&at=", timeout=12).json()
        if not r.get("success"):
            return {"_error": "Sai tài khoản hoặc mật khẩu"}
        sk = r["sessionKey"] + "=" * ((4 - len(r["sessionKey"]) % 4) % 4)
        nn = json.loads(base64.b64decode(sk)).get("nickname")
        r2 = requests.post("https://wlb.tele68.com/v1/lobby/auth/login?cp=R&cl=R&pf=web&at=",
                           json={"nickName": nn, "accessToken": r["accessToken"]}, timeout=12).json()
        if not r2.get("token"):
            return {"_error": "Không nhận được token"}
        return {"token": r2["token"], "nickname": nn, "money": r2.get("remoteLoginResp", {}).get("money", 0)}
    except Exception as e:
        return {"_error": str(e)}


def start_websocket(cid, token):
    st = get_state(cid)
    old = active_sockets.pop(cid, None)
    if old is not None:
        try:
            old.disconnect()
        except Exception:
            pass

    sio = socketio.Client(reconnection=True, reconnection_attempts=99999, reconnection_delay=3,
                          logger=False, engineio_logger=False)
    active_sockets[cid] = sio

    @sio.event(namespace="/txmd5")
    def connect():
        arr = fetch_history_from_api(MAX_HISTORY)
        if arr:
            st["engine"].ingest(arr)
            notify(cid, f"🟢 ĐÃ KẾT NỐI · nạp {len(arr)} phiên · bộ nhớ {len(st['engine'].recs)} phiên\n"
                        f"👉 /tudong bat [tiền] để bật tự động", force=True)
        else:
            notify(cid, "🟢 Đã kết nối · chưa lấy được lịch sử, luồng ngầm sẽ thử lại", force=True)

    @sio.on("new-session", namespace="/txmd5")
    def new_sess(d):
        sid = d.get("id")
        st["session_id"] = sid
        st["pred_session"] = sid
        st["bet_placed"] = False
        info = st["engine"].predict(st["mode"])
        st["prediction"] = info["side"]
        st["pred_info"] = info
        notify(cid, prediction_card(sid, info, st))

    @sio.on("tick-update", namespace="/txmd5")
    def tk(d):
        if d.get("state") != "BETTING" or not st["auto"] or st["bet_placed"]:
            return
        if st["prediction"] is None or st["pred_session"] != st["session_id"]:
            return
        amount = current_bet(st)
        block = risk_block(st, amount)
        if block:
            st["auto"] = False
            notify(cid, f"🛑 {block} → ĐÃ TẮT TỰ ĐỘNG", force=True)
            return
        sio.emit("bet", {"type": st["prediction"], "amount": amount}, namespace="/txmd5")
        st["bet_placed"] = True
        st["last_bet"] = amount
        st["day_bet"] += amount
        st["total_bet"] += amount
        notify(cid, f"🚀 ĐÃ ĐẶT {side_label(st['prediction'])} · {fmt(amount)}", force=True)

    @sio.on("bet-result", namespace="/txmd5")
    def br(d):
        if "postBalance" in d:
            st["balance"] = d["postBalance"]

    @sio.on("session-result", namespace="/txmd5")
    def sr(d):
        res = d.get("resultTruyenThong")
        if res not in (TAI, XIU):
            return
        sid = d.get("id")
        dices = d.get("dices") or [0, 0, 0]
        st["engine"].ingest([{"session": sid, "result": res, "total": sum(dices)}])

        if st["pred_session"] == sid and st["pred_info"] is not None:
            info = st["pred_info"]
            pred = st["prediction"]
            if pred is not None:
                st["engine"].record(pred, info["p"] if pred == TAI else 1 - info["p"], res)
            bet_done = st["bet_placed"]
            bet = st["last_bet"]
            won = bet_done and pred == res
            if bet_done:
                if won:
                    st["total_win"] += 1
                    st["win_streak"] += 1
                    st["lose_streak"] = 0
                    st["profit"] += round(bet * WIN_PROFIT)
                    st["mart_level"] = 0
                else:
                    st["total_lose"] += 1
                    st["lose_streak"] += 1
                    st["win_streak"] = 0
                    st["profit"] -= round(bet * LOSE_LOSS)
                    if st["martingale"] and st["mart_level"] < MAX_MARTINGALE_LEVEL:
                        st["mart_level"] += 1
            notify(cid, result_card(sid, res, pred, bet_done, won, bet, st), force=bet_done)
            st["bet_placed"] = False
            st["prediction"] = None
            st["pred_info"] = None
        else:
            notify(cid, f"🎲 KẾT QUẢ PHIÊN #{sid}: {side_label(res)}")

    try:
        sio.connect(WS_URL, socketio_path="txmd5/", namespaces=["/txmd5"],
                    transports=["websocket"], auth={"token": token},
                    headers={"User-Agent": "Mozilla/5.0", "Origin": "https://lc79b.bet"})
        sio.wait()
    except Exception as e:
        notify(cid, f"⚠️ Lỗi WebSocket: {esc(e)}", force=True)


def stop_user(cid):
    st = get_state(cid)
    st["running"] = False
    st["gen"] += 1
    st["auto"] = False
    st["martingale"] = False
    st["mart_level"] = 0
    sio = active_sockets.pop(cid, None)
    if sio is not None:
        try:
            sio.disconnect()
        except Exception:
            pass


# ============================================================
# LỆNH
# ============================================================
@bot.message_handler(commands=["batdau"])
def cmd_batdau(m):
    cid = m.chat.id
    if check_auth(cid):
        han = "👑 QUẢN TRỊ VIÊN VĨNH VIỄN" if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
        status = f"✅ <b>Đã kích hoạt</b> · {han}"
    else:
        status = "🔒 <b>Chưa kích hoạt</b> · gõ /nhapkey MÃ_KEY"
    bot.reply_to(m, f"🏠 <b>{BOT_NAME}</b>\n{status}\n{LINE}\n{COMMAND_LIST}\n{LINE}\n"
                    f"📩 Hỗ trợ: {ADMIN_USERNAME}", parse_mode="HTML")


@bot.message_handler(commands=["trogiup", "huongdan"])
def cmd_trogiup(m):
    bot.reply_to(m, HELP_TEXT, parse_mode="HTML")


@bot.message_handler(commands=["nhapkey"])
def cmd_nhapkey(m):
    p = m.text.split()
    if len(p) < 2:
        return bot.reply_to(m, "👉 Dùng: /nhapkey MÃ_KEY")
    k = p[1].upper()
    if k in valid_keys:
        authorized_users[m.chat.id] = time.time() + valid_keys[k] * 86400
        del valid_keys[k]
        ht = datetime.fromtimestamp(authorized_users[m.chat.id]).strftime("%d/%m/%Y %H:%M")
        bot.reply_to(m, f"✅ KÍCH HOẠT VIP THÀNH CÔNG\n📅 Hết hạn: {ht}\n👉 /dangnhap TÀI_KHOẢN MẬT_KHẨU để bắt đầu")
    else:
        bot.reply_to(m, f"❌ KEY KHÔNG HỢP LỆ\n📩 {ADMIN_USERNAME}")


@bot.message_handler(commands=["taokey"])
def cmd_taokey(m):
    if m.chat.id != ADMIN_ID:
        return bot.reply_to(m, "⛔ Chỉ ADMIN")
    p = m.text.split()
    try:
        d = int(p[1]) if len(p) > 1 else 30
        k = "VIP-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
        valid_keys[k] = d
        ht = (datetime.now() + timedelta(days=d)).strftime("%d/%m/%Y %H:%M")
        bot.reply_to(m, f"👑 TẠO KEY THÀNH CÔNG\n🔑 {k}\n⏳ {d} ngày\n📅 Hết hạn: {ht}")
    except ValueError:
        bot.reply_to(m, "👉 Dùng: /taokey 30")


@bot.message_handler(commands=["danhsachkey"])
def cmd_danhsachkey(m):
    if m.chat.id != ADMIN_ID:
        return
    if not valid_keys:
        return bot.reply_to(m, "📭 Chưa có key nào còn hiệu lực")
    ds = "\n".join(f"🔑 {k} · {v} ngày" for k, v in valid_keys.items())
    bot.reply_to(m, f"📋 DANH SÁCH KEY CHƯA DÙNG\n{ds}\n📊 Tổng: {len(valid_keys)}")


@bot.message_handler(commands=["dangnhap"])
@require_auth
def cmd_dangnhap(m):
    cid = m.chat.id
    p = m.text.split()
    if len(p) != 3:
        return bot.reply_to(m, "👉 Dùng: /dangnhap TÀI_KHOẢN MẬT_KHẨU")
    msg = bot.reply_to(m, "🔄 Đang đăng nhập...")
    res = login_and_get_token(p[1], p[2])
    if "_error" in res:
        return bot.edit_message_text(f"❌ {res['_error']}", cid, msg.message_id)

    st = get_state(cid)
    st["balance"] = res["money"]
    st["profit"] = 0
    st["running"] = True
    st["gen"] += 1
    gen = st["gen"]
    bot.edit_message_text(f"✅ ĐĂNG NHẬP THÀNH CÔNG\n📛 {res['nickname']}\n💰 Số dư: {fmt(res['money'])}\n"
                          f"🟢 Đang chạy ngầm, nạp phiên mỗi {SYNC_INTERVAL} giây\n"
                          f"👉 /tudong bat [tiền] để bật tự động", cid, msg.message_id)
    threading.Thread(target=start_websocket, args=(cid, res["token"]), daemon=True).start()
    threading.Thread(target=sync_loop, args=(cid, gen), daemon=True).start()


@bot.message_handler(commands=["dung"])
@require_auth
def cmd_dung(m):
    stop_user(m.chat.id)
    bot.reply_to(m, "⏹️ ĐÃ NGẮT AN TOÀN\n✅ Đóng WebSocket, dừng nạp phiên ngầm\n✅ Tắt tự động và X2")


@bot.message_handler(commands=["tudong"])
@require_auth
def cmd_tudong(m):
    cid = m.chat.id
    st = get_state(cid)
    p = m.text.split()
    if not st["running"]:
        return bot.reply_to(m, "⚠️ Chưa đăng nhập. Dùng: /dangnhap TÀI_KHOẢN MẬT_KHẨU")
    action = norm(p[1]) if len(p) > 1 else ""
    if action not in ("bat", "tat"):
        return bot.reply_to(m, "👉 Dùng: /tudong bat [tiền] hoặc /tudong tat")

    if action == "tat":
        st["auto"] = False
        return bot.reply_to(m, "🔴 TỰ ĐỘNG ĐÃ TẮT")

    if len(p) > 2:
        try:
            st["base_bet"] = parse_amount(p[2])
        except ValueError:
            return bot.reply_to(m, "❌ Số tiền không hợp lệ. Ví dụ: /tudong bat 10k")
    if st["base_bet"] <= 0:
        return bot.reply_to(m, "❌ Mức cược phải lớn hơn 0")

    block = risk_block(st, current_bet(st))
    if block:
        return bot.reply_to(m, f"🛑 Không bật được: {block}. Chỉnh /caidat rồi thử lại")

    st["auto"] = True
    st["mart_level"] = 0
    bot.reply_to(m, f"🟢 TỰ ĐỘNG ĐÃ BẬT\n💰 {fmt(st['base_bet'])} mỗi phiên · {MODES[st['mode']]['label']}\n"
                    f"🧠 Chỉ đặt khi thuật toán đủ bằng chứng\n👉 Tắt: /tudong tat")


@bot.message_handler(commands=["x2"])
@require_auth
def cmd_x2(m):
    cid = m.chat.id
    st = get_state(cid)
    p = m.text.split()
    action = norm(p[1]) if len(p) > 1 else ""
    if action not in ("bat", "tat"):
        status = f"BẬT · cấp {st['mart_level']}" if st["martingale"] else "TẮT"
        return bot.reply_to(m, f"💸 X2 KHI THUA\n📌 Trạng thái: {status}\n💰 Gốc: {fmt(st['base_bet'])}\n"
                               f"🔢 Tối đa: {MAX_MARTINGALE_LEVEL} cấp\n👉 /x2 bat hoặc /x2 tat")
    st["martingale"] = action == "bat"
    st["mart_level"] = 0
    if st["martingale"]:
        bot.reply_to(m, f"💸 X2 ĐÃ BẬT\nThua thì nhân {MARTINGALE_MULTIPLIER} lần, thắng thì về gốc\n"
                        f"Tối đa {MAX_MARTINGALE_LEVEL} cấp")
    else:
        bot.reply_to(m, "🔴 X2 ĐÃ TẮT, về mức cược gốc")


@bot.message_handler(commands=["caidat"])
@require_auth
def cmd_caidat(m):
    cid = m.chat.id
    st = get_state(cid)
    kv = parse_kv(m.text)
    if not kv:
        return bot.reply_to(m, setup_text(st))

    updates = {}
    try:
        for k, v in kv.items():
            if k in ("cuoc", "bet"):
                amt = parse_amount(v)
                if amt <= 0:
                    raise ValueError("cuoc phải lớn hơn 0")
                updates["base_bet"] = amt
            elif k == "sl":
                updates["stop_loss"] = parse_amount(v)
            elif k == "sw":
                updates["stop_win"] = parse_amount(v)
            elif k == "limit":
                updates["daily_limit"] = parse_amount(v)
            elif k == "mode":
                key = norm(v)
                if key not in MODES:
                    raise ValueError("mode chỉ nhận: antoan, canbang, maohiem")
                updates["mode"] = key
            elif k in ("thongbao", "notify"):
                val = norm(v)
                if val not in ("bat", "tat"):
                    raise ValueError("thongbao chỉ nhận: bat, tat")
                updates["notify"] = val == "bat"
            else:
                raise ValueError(f"không rõ mục '{k}'")
    except ValueError as e:
        return bot.reply_to(m, f"❌ {e}\n👉 Ví dụ: /caidat cuoc=10k mode=canbang sl=1m")

    st.update(updates)
    bot.reply_to(m, "✅ ĐÃ LƯU\n" + setup_text(st))


@bot.message_handler(commands=["thongtin"])
@require_auth
def cmd_thongtin(m):
    cid = m.chat.id
    bot.reply_to(m, account_text(cid, get_state(cid)))


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    log.info("%s khởi động", BOT_NAME)
    bot.infinity_polling()