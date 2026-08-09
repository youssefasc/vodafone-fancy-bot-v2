"""
Vodafone Fancy Numbers Bot - v2
بوت شغال 24/7 على Railway بلوحة تحكم كاملة على تيليجرام
"""
import os
import json
import time
import asyncio
import threading
from datetime import datetime, timezone
from collections import Counter

from playwright.sync_api import sync_playwright
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ── إعدادات ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
STATE_FILE     = "bot_state.json"
ADMIN_ID       = 6055994136  # اليوزر الوحيد المصرح له يتحكم في البوت

DEFAULT_STATE = {
    "interval_minutes": 10,
    "last_run": None,
    "last_result": {"simcard": 0, "esim": 0, "fancy_simcard": [], "fancy_esim": []},
    "seen": {"simcard": [], "esim": []},
    "running": False,
    "paused": False,
}

state_lock = threading.Lock()


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                loaded = json.load(f)
                merged = {**DEFAULT_STATE, **loaded}
                return merged
        except Exception:
            pass
    return dict(DEFAULT_STATE)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


state = load_state()


# ═══════════════════════════════════════════════════════════
# منطق تحديد الأرقام المميزة
# ═══════════════════════════════════════════════════════════
def is_fancy(number: str) -> dict:
    d = number[-8:]

    if len(set(d)) == 1:
        return {"fancy": True, "reason": "متكررة كلها 🔥"}
    if all(int(d[i]) - int(d[i-1]) == 1 for i in range(1, len(d))):
        return {"fancy": True, "reason": "متسلسلة تصاعدي ⬆️"}
    if all(int(d[i-1]) - int(d[i]) == 1 for i in range(1, len(d))):
        return {"fancy": True, "reason": "متسلسلة تنازلي ⬇️"}
    if d[:4] == d[4:]:
        return {"fancy": True, "reason": "نصف متكرر 🔁"}
    if len(set(d[:4])) == 1:
        return {"fancy": True, "reason": "أول 4 متكررة ✨"}
    if len(set(d[4:])) == 1:
        return {"fancy": True, "reason": "آخر 4 متكررة ✨"}
    if len(set(d)) <= 2:
        return {"fancy": True, "reason": "رقمين فريدين فقط 💎"}
    if len(set(d)) <= 3 and d.count(d[0]) >= 4:
        return {"fancy": True, "reason": "شبه متكرر ⭐"}
    for tail_len in [4, 3]:
        tail = d[-tail_len:]
        if len(set(tail)) == 1:
            return {"fancy": True, "reason": f"آخر {tail_len} متكررة 🔢"}

    d6 = d[-6:]
    if len(d6) == 6:
        g1, g2 = int(d6[:3]), int(d6[3:])
        diff = g2 - g1
        if diff != 0 and diff % 100 == 0 and g1 >= 0 and g2 >= 0:
            arrow = "⬆️" if diff > 0 else "⬇️"
            return {"fancy": True, "reason": f"مجموعتين متسلسلتين ({d6[:3]}-{d6[3:]}) {arrow}"}
        if g1 > 0 and g2 == g1 * 2:
            return {"fancy": True, "reason": f"مجموعتين مضاعفة ({d6[:3]}-{d6[3:]}) ✖️"}

    d8 = d[-8:]
    if len(d8) == 8:
        g1, g2 = int(d8[:4]), int(d8[4:])
        diff = g2 - g1
        if diff != 0 and diff % 1000 == 0 and g1 >= 0 and g2 >= 0:
            arrow = "⬆️" if diff > 0 else "⬇️"
            return {"fancy": True, "reason": f"مجموعتين متسلسلتين ({d8[:4]}-{d8[4:]}) {arrow}"}

    if len(d) == 8:
        g1, g2, g3 = d[0:3], d[3:6], d[6:8]
        n1, n2 = int(g1), int(g2)
        diff = n1 - n2
        if diff != 0 and diff % 100 == 0:
            return {"fancy": True, "reason": f"نمط ({g1}-{g2}-{g3}) 🎯"}
        if g1 == g2:
            return {"fancy": True, "reason": f"مجموعتين متطابقتين ({g1}-{g2}-{g3}) 🔁"}

    pairs = [d[i:i+2] for i in range(0, 8, 2)]
    if len(set(pairs)) <= 2:
        return {"fancy": True, "reason": f"أزواج متكررة ({'-'.join(pairs)}) 🔂"}
    pair_counts = Counter(pairs)
    most_common_pair, freq = pair_counts.most_common(1)[0]
    if freq >= 2 and most_common_pair != "":
        return {"fancy": True, "reason": f"الزوج ({most_common_pair}) متكرر {freq} مرات 🔂"}

    if d[-2:] == d[-4:-2]:
        return {"fancy": True, "reason": f"آخر زوجين متطابقين ({d[-4:]}) 🔁"}

    return {"fancy": False}


# ═══════════════════════════════════════════════════════════
# السكرابينج
# ═══════════════════════════════════════════════════════════
def close_cookie_banner(page):
    selectors_to_try = [
        "#onetrust-accept-btn-handler",
        "button#onetrust-accept-btn-handler",
        ".onetrust-close-btn-handler",
        "#onetrust-reject-all-handler",
        "button:has-text('Accept')",
        "button:has-text('Accept All')",
        "button:has-text('I Accept')",
        "button:has-text('موافق')",
    ]
    for sel in selectors_to_try:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click(timeout=3000, force=True)
                time.sleep(1)
                return True
        except Exception:
            continue
    try:
        removed = page.evaluate("""
            () => {
                const selectors = ['#onetrust-consent-sdk', '.onetrust-pc-dark-filter',
                                    '#onetrust-banner-sdk', '.ot-sdk-container'];
                let n = 0;
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => { el.remove(); n++; });
                }
                return n;
            }
        """)
        if removed:
            time.sleep(1)
            return True
    except Exception:
        pass
    return False


def scrape_numbers(page, line_type: str, log) -> list[dict]:
    log(f"📡 بجيب أرقام {line_type}...")
    close_cookie_banner(page)

    target = "text=Sim Card" if line_type == "simcard" else "text=eSim"
    try:
        page.click(target, timeout=10000, force=True)
    except Exception:
        close_cookie_banner(page)
        page.click(target, timeout=10000, force=True)

    time.sleep(2)

    extract_js = """
        () => {
            const allText = document.body.innerText;
            const regex = /01[0-9]\\d{8}/g;
            return [...new Set(allText.match(regex) || [])];
        }
    """

    seen_set = set()
    results = []

    def collect():
        nums = page.evaluate(extract_js)
        added = 0
        for n in nums:
            if n not in seen_set:
                seen_set.add(n)
                results.append(n)
                added += 1
        return added

    find_container_js = """
        () => {
            const phoneRe = /01[0-9]\\d{8}/g;
            const els = [...document.querySelectorAll('*')].filter(el => {
                const s = getComputedStyle(el);
                const canScroll = /(auto|scroll)/.test(s.overflowY);
                return canScroll && el.scrollHeight > el.clientHeight + 20;
            });
            let best = null, bestCount = -1;
            for (const el of els) {
                const c = (el.innerText.match(phoneRe) || []).length;
                if (c > bestCount) { bestCount = c; best = el; }
            }
            if (best) { window.__scrollTarget = best; }
            return best ? {found: true} : {found: false};
        }
    """

    scroll_and_measure_js = """
        () => {
            const el = window.__scrollTarget;
            if (!el) return {scrollHeight: 0};
            el.scrollTop = el.scrollHeight;
            el.dispatchEvent(new Event('scroll', {bubbles: true}));
            el.dispatchEvent(new WheelEvent('wheel', {deltaY: 3000, bubbles: true}));
            return {scrollHeight: el.scrollHeight};
        }
    """

    collect()
    found = page.evaluate(find_container_js)

    if found.get("found"):
        last_h = 0
        no_growth = 0
        for i in range(150):
            prev_count = len(results)
            info = page.evaluate(scroll_and_measure_js)
            new_h = info.get("scrollHeight", last_h)
            time.sleep(0.6)
            collect()
            if new_h <= last_h and len(results) == prev_count:
                time.sleep(1.5)
                info2 = page.evaluate(scroll_and_measure_js)
                new_h = max(new_h, info2.get("scrollHeight", new_h))
                collect()
            grew = new_h > last_h
            got_new = len(results) > prev_count
            if not grew and not got_new:
                no_growth += 1
                if no_growth >= 5:
                    break
            else:
                no_growth = 0
                last_h = max(last_h, new_h)
    else:
        for i in range(20):
            prev = len(results)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)
            collect()
            if len(results) == prev:
                break

    for i in range(3):
        try:
            page.click("text=Shuffle", timeout=5000, force=True)
            time.sleep(1.5)
            collect()
            page.evaluate(find_container_js)
            stagnant = 0
            for _ in range(30):
                prev = len(results)
                page.evaluate(scroll_and_measure_js)
                time.sleep(0.6)
                collect()
                if len(results) == prev:
                    stagnant += 1
                    if stagnant >= 4:
                        break
                else:
                    stagnant = 0
        except Exception:
            break

    log(f"✅ {line_type}: لقيت {len(results)} رقم")
    return [{"number": n, "type": line_type} for n in results]


def scrape_vodafone(log) -> dict:
    all_numbers = {"simcard": [], "esim": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ))
        try:
            page.goto(
                "https://eshop.vodafone.com.eg/en/lines/red/numbers",
                wait_until="networkidle",
                timeout=60000
            )
            time.sleep(3)
            close_cookie_banner(page)

            for lt in ["simcard", "esim"]:
                items = scrape_numbers(page, lt, log)
                all_numbers[lt] = items
        except Exception as e:
            log(f"❌ خطأ: {e}")
        browser.close()
    return all_numbers


# ═══════════════════════════════════════════════════════════
# منطق الفحص الكامل (شغال في thread منفصل عشان مايوقفش البوت)
# ═══════════════════════════════════════════════════════════
_scan_lock = threading.Lock()


def run_scan(bot=None, manual=False, chat_id=None, loop=None):
    """يفحص الموقع، يحدث الحالة، ويبعت رسالة تيليجرام"""
    if not _scan_lock.acquire(blocking=False):
        return {"error": "في فحص شغال بالفعل، استنى يخلص"}

    try:
        with state_lock:
            state["running"] = True
            save_state(state)

        logs = []
        def log(msg):
            print(msg, flush=True)
            logs.append(msg)

        log(f"🚀 بدأ الفحص {'(يدوي)' if manual else '(تلقائي)'}...")
        all_numbers = scrape_vodafone(log)

        with state_lock:
            seen = state.get("seen", {"simcard": [], "esim": []})
            new_fancy = {"simcard": [], "esim": []}
            totals = {"simcard": 0, "esim": 0}

            for lt in ["simcard", "esim"]:
                seen_set = set(seen.get(lt, []))
                totals[lt] = len(all_numbers[lt])
                for item in all_numbers[lt]:
                    num = item["number"]
                    if num not in seen_set:
                        result = is_fancy(num)
                        if result["fancy"]:
                            new_fancy[lt].append({**item, **result})

            has_new = any(new_fancy[lt] for lt in ["simcard", "esim"])

            # حدث الأرقام المشوفة
            for lt in ["simcard", "esim"]:
                seen_list = set(seen.get(lt, []))
                for item in all_numbers[lt]:
                    seen_list.add(item["number"])
                seen[lt] = list(seen_list)

            state["seen"] = seen
            state["last_run"] = datetime.now(timezone.utc).isoformat()
            state["last_result"] = {
                "simcard": totals["simcard"],
                "esim": totals["esim"],
                "fancy_simcard": new_fancy["simcard"],
                "fancy_esim": new_fancy["esim"],
            }
            state["running"] = False
            save_state(state)

        # ابعت رسالة تيليجرام
        target_chat = chat_id or CHAT_ID
        if bot and target_chat:
            LINE_LABELS = {"simcard": "📱 SIM Card (200 EGP)", "esim": "💿 eSIM (350 EGP)"}
            if has_new:
                msg = "🌟 <b>أرقام مميزة جديدة على فودافون!</b>\n\n"
                for lt in ["simcard", "esim"]:
                    if new_fancy[lt]:
                        msg += f"{LINE_LABELS[lt]}\n"
                        for item in new_fancy[lt][:25]:  # حد أقصى عشان مايطولش
                            msg += f"  ├ <code>{item['number']}</code> — {item['reason']}\n"
                        if len(new_fancy[lt]) > 25:
                            msg += f"  ... و{len(new_fancy[lt]) - 25} رقم كمان\n"
                        msg += "\n"
                msg += "🔗 <a href='https://eshop.vodafone.com.eg/en/lines/red/numbers'>شوف واشتري هنا</a>"
            else:
                now_str = datetime.now(timezone.utc).strftime('%H:%M UTC')
                msg = (f"🔍 فحص الساعة {now_str}\n"
                       f"مفحوص: {totals['simcard']} SIM + {totals['esim']} eSIM\n"
                       f"مفيش أرقام مميزة جديدة دلوقتي.")

            try:
                if loop is not None:
                    # bot.send_message async - لازم نستدعيها على الـ event loop الأساسي
                    fut = asyncio.run_coroutine_threadsafe(
                        bot.send_message(chat_id=target_chat, text=msg, parse_mode="HTML"),
                        loop
                    )
                    fut.result(timeout=30)  # ننتظر التأكيد إن الرسالة اتبعتت فعلاً
                else:
                    log("⚠️ مفيش event loop متاح لإرسال الرسالة")
            except Exception as e:
                log(f"⚠️ فشل إرسال الرسالة: {e}")

        return {"ok": True, "has_new": has_new, "totals": totals}

    finally:
        with state_lock:
            state["running"] = False
            save_state(state)
        _scan_lock.release()


# ═══════════════════════════════════════════════════════════
# جدولة الفحص التلقائي (background thread)
# ═══════════════════════════════════════════════════════════
def scheduler_loop(application, loop):
    while True:
        with state_lock:
            interval = state.get("interval_minutes", 10)
            paused = state.get("paused", False)

        if not paused:
            try:
                run_scan(bot=application.bot, manual=False, loop=loop)
            except Exception as e:
                print(f"❌ خطأ في الفحص المجدول: {e}", flush=True)

        # ننام لحد الفحص الجاي (نتأكد من الـ pause كل دقيقة عشان لو المستخدم غيّر الإعداد)
        remaining = interval * 60
        while remaining > 0:
            time.sleep(min(30, remaining))
            remaining -= 30
            with state_lock:
                if state.get("interval_minutes", 10) != interval:
                    break  # الإعداد اتغير، اطلع واعمل loop تاني بالقيمة الجديدة


# ═══════════════════════════════════════════════════════════
# لوحة التحكم على تيليجرام
# ═══════════════════════════════════════════════════════════
def main_menu_keyboard():
    with state_lock:
        paused = state.get("paused", False)
        interval = state.get("interval_minutes", 10)
    pause_label = "▶️ استكمال الفحص" if paused else "⏸️ إيقاف الفحص مؤقتاً"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 افحص دلوقتي", callback_data="scan_now")],
        [InlineKeyboardButton("📊 آخر نتيجة", callback_data="last_result")],
        [InlineKeyboardButton(f"⏱️ مدة الفحص: كل {interval} دقيقة", callback_data="change_interval")],
        [InlineKeyboardButton(pause_label, callback_data="toggle_pause")],
        [InlineKeyboardButton("ℹ️ حالة البوت", callback_data="status")],
    ])


def interval_keyboard():
    options = [5, 10, 15, 30, 60]
    rows = []
    row = []
    for opt in options:
        row.append(InlineKeyboardButton(f"{opt} دقيقة", callback_data=f"set_interval_{opt}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def stranger_reply_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 تواصل معايا", url="tg://user?id=6055994136")]
    ])


STRANGER_MESSAGE = (
    "🤖 أهلاً بيك!\n\n"
    "أنا بوت بصطاد الأرقام المميزة من موقع فودافون مصر، "
    "وبنبّه أول ما ألاقي رقم مميز جديد عشان تشتريه فوراً.\n\n"
    "البوت ده خاص وشغال لصاحبه بس. لو عايز تعرف أكتر أو حابب تطلب بوت زيه، "
    "تواصل معايا من الزرار تحت 👇"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(STRANGER_MESSAGE, reply_markup=stranger_reply_keyboard())
        return
    await update.message.reply_text(
        "👋 أهلاً! أنا بوت أرقام فودافون المميزة.\n\n"
        "استخدم لوحة التحكم تحت عشان تتحكم فيا:",
        reply_markup=main_menu_keyboard()
    )


async def safe_edit(query, text, **kwargs):
    """يعدل الرسالة، ويتجاهل بهدوء لو المحتوى نفس اللي موجود بالفعل"""
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception as e:
        if "Message is not modified" not in str(e):
            raise


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    # ── حماية: بس الأدمن يقدر يستخدم الأزرار ──
    if user_id != ADMIN_ID:
        await query.answer("🚫 البوت ده خاص، تواصل مع الأدمن.", show_alert=True)
        return

    if data == "main_menu":
        await safe_edit(query, "📋 لوحة التحكم:", reply_markup=main_menu_keyboard())

    elif data == "scan_now":
        with state_lock:
            already_running = state.get("running", False)
        if already_running:
            await safe_edit(query, "⏳ في فحص شغال بالفعل دلوقتي، استنى يخلص.",
                                            reply_markup=main_menu_keyboard())
            return
        await safe_edit(query, "🚀 بدأت الفحص... هياخد دقيقة لدقيقتين، هبعتلك النتيجة أول ما يخلص.")
        current_loop = asyncio.get_running_loop()
        threading.Thread(target=run_scan, kwargs={
            "bot": context.bot, "manual": True, "chat_id": chat_id, "loop": current_loop
        }, daemon=True).start()

    elif data == "last_result":
        with state_lock:
            last_run = state.get("last_run")
            result = state.get("last_result", {})
        if not last_run:
            await safe_edit(query, "مفيش فحص اتعمل لسه.", reply_markup=main_menu_keyboard())
            return
        dt = datetime.fromisoformat(last_run)
        msg = f"📊 <b>آخر فحص:</b> {dt.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        msg += f"📱 SIM Card: {result.get('simcard', 0)} رقم متفحوص\n"
        msg += f"💿 eSIM: {result.get('esim', 0)} رقم متفحوص\n\n"
        fancy_sim = result.get("fancy_simcard", [])
        fancy_esim = result.get("fancy_esim", [])
        if fancy_sim or fancy_esim:
            msg += "🌟 آخر أرقام مميزة اتلقت:\n"
            for item in (fancy_sim + fancy_esim)[:15]:
                msg += f"  ├ <code>{item['number']}</code> — {item['reason']}\n"
        else:
            msg += "🔍 مفيش أرقام مميزة جديدة في آخر فحص."
        await safe_edit(query, msg, parse_mode="HTML", reply_markup=main_menu_keyboard())

    elif data == "change_interval":
        await safe_edit(query, "⏱️ اختار كل قد إيه يفحص:", reply_markup=interval_keyboard())

    elif data.startswith("set_interval_"):
        minutes = int(data.replace("set_interval_", ""))
        with state_lock:
            state["interval_minutes"] = minutes
            save_state(state)
        await safe_edit(query, f"✅ تم! هيفحص كل {minutes} دقيقة من دلوقتي.",
                                        reply_markup=main_menu_keyboard())

    elif data == "toggle_pause":
        with state_lock:
            state["paused"] = not state.get("paused", False)
            paused = state["paused"]
            save_state(state)
        msg = "⏸️ تم إيقاف الفحص التلقائي مؤقتاً." if paused else "▶️ تم استكمال الفحص التلقائي."
        await safe_edit(query, msg, reply_markup=main_menu_keyboard())

    elif data == "status":
        with state_lock:
            paused = state.get("paused", False)
            interval = state.get("interval_minutes", 10)
            running = state.get("running", False)
            last_run = state.get("last_run")
            seen_sim = len(state.get("seen", {}).get("simcard", []))
            seen_esim = len(state.get("seen", {}).get("esim", []))
        status_line = "🟡 بيفحص دلوقتي" if running else ("⏸️ متوقف مؤقتاً" if paused else "🟢 شغال")
        last_run_str = datetime.fromisoformat(last_run).strftime('%Y-%m-%d %H:%M UTC') if last_run else "لسه مفيش"
        msg = (
            f"ℹ️ <b>حالة البوت:</b>\n\n"
            f"الحالة: {status_line}\n"
            f"مدة الفحص: كل {interval} دقيقة\n"
            f"آخر فحص: {last_run_str}\n"
            f"إجمالي أرقام محفوظة: {seen_sim + seen_esim} ({seen_sim} SIM + {seen_esim} eSIM)"
        )
        await safe_edit(query, msg, parse_mode="HTML", reply_markup=main_menu_keyboard())


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(STRANGER_MESSAGE, reply_markup=stranger_reply_keyboard())
        return
    await update.message.reply_text("📋 لوحة التحكم:", reply_markup=main_menu_keyboard())


async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أي رسالة تانية (مش أمر معروف) - لو مش الأدمن يرد برسالة التعريف"""
    if not update.message:
        return
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(STRANGER_MESSAGE, reply_markup=stranger_reply_keyboard())
        return
    # لو الأدمن كتب حاجة مش أمر، رجّعله لوحة التحكم
    await update.message.reply_text("📋 لوحة التحكم:", reply_markup=main_menu_keyboard())


# ═══════════════════════════════════════════════════════════
# التشغيل
# ═══════════════════════════════════════════════════════════
def main():
    if not TELEGRAM_TOKEN:
        print("❌ مفيش TELEGRAM_TOKEN!", flush=True)
        return

    # ── نقفل أي جلسة getUpdates عالقة (من instance قديمة) قبل ما نبدأ ──
    # ده بيمنع Conflict error لو فيه اتصال قديم لسه فاتح
    import urllib.request as _ur
    try:
        delete_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook?drop_pending_updates=true"
        with _ur.urlopen(delete_url, timeout=15) as resp:
            print(f"🧹 تنظيف الجلسات القديمة: {resp.read().decode()[:200]}", flush=True)
    except Exception as e:
        print(f"⚠️ فشل تنظيف الجلسات القديمة (مش مشكلة كبيرة): {e}", flush=True)

    # استنى ثانيتين كمان عشان أي instance قديمة تقفل تماماً
    time.sleep(3)

    async def on_startup(app):
        # بيتنفذ بعد ما الـ event loop يبدأ فعلياً - هنا نمسك الـ loop الصح
        loop = asyncio.get_running_loop()
        scheduler_thread = threading.Thread(target=scheduler_loop, args=(app, loop), daemon=True)
        scheduler_thread.start()
        print("🕐 الجدولة التلقائية بدأت", flush=True)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(on_startup).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.ALL, handle_any_message))

    print("🤖 البوت شغال...", flush=True)
    # ملحوظة: drop_pending_updates بيتجاهل أي updates قديمة متراكمة وقت الانقطاع
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
