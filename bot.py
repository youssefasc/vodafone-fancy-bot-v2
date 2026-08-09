"""
Vodafone Fancy Numbers Bot - v2
بوت شغال 24/7 على Railway بلوحة تحكم كاملة على تيليجرام
"""
import os
import json
import time
import base64
import asyncio
import threading
import urllib.request
import urllib.error
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

# ── تخزين الحالة بشكل دائم على GitHub (عشان Railway بيمسح الملفات المحلية مع كل redeploy) ──
GH_TOKEN = os.environ.get("GH_STATE_TOKEN", "")
GH_REPO  = os.environ.get("GH_STATE_REPO", "youssefasc/vodafone-fancy-bot-v2")
GH_STATE_PATH = "bot_state.json"

DEFAULT_STATE = {
    "interval_minutes": 10,
    "last_run": None,
    "last_result": {"simcard": 0, "esim": 0, "fancy_simcard": [], "fancy_esim": []},
    "seen": {"simcard": [], "esim": []},
    "running": False,
    "paused": False,
}

state_lock = threading.Lock()


def _gh_request(method, url, data=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {GH_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "vodafone-bot")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def load_state_from_github():
    """يجيب الحالة المحفوظة من GitHub (بتفضل موجودة حتى بعد أي redeploy)"""
    if not GH_TOKEN:
        return None
    try:
        url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_STATE_PATH}"
        result = _gh_request("GET", url)
        content = base64.b64decode(result["content"]).decode()
        loaded = json.loads(content)
        return {**DEFAULT_STATE, **loaded}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("ℹ️ مفيش ملف حالة على GitHub لسه (أول مرة) - هيتعمل جديد", flush=True)
        else:
            print(f"⚠️ فشل تحميل الحالة من GitHub: {e}", flush=True)
        return None
    except Exception as e:
        print(f"⚠️ فشل تحميل الحالة من GitHub: {e}", flush=True)
        return None


def save_state_to_github(state_data):
    """يرفع الحالة الحالية على GitHub عشان تفضل محفوظة حتى بعد أي redeploy"""
    if not GH_TOKEN:
        return
    try:
        content = json.dumps(state_data, ensure_ascii=False, indent=2)
        encoded = base64.b64encode(content.encode()).decode()

        url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_STATE_PATH}"
        sha = None
        try:
            existing = _gh_request("GET", url)
            sha = existing.get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

        payload = {"message": "Update bot state", "content": encoded}
        if sha:
            payload["sha"] = sha

        _gh_request("PUT", url, data=json.dumps(payload).encode())
    except Exception as e:
        print(f"⚠️ فشل حفظ الحالة على GitHub: {e}", flush=True)


def load_state():
    # أولوية للحالة المحفوظة على GitHub (دائمة)، وإلا نجرب الملف المحلي، وإلا حالة افتراضية
    github_state = load_state_from_github()
    if github_state is not None:
        return github_state

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
    # نحفظ محلياً فوراً (سريع) ونرفع على GitHub في الخلفية (دائم، مش حرج التوقيت)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    threading.Thread(target=save_state_to_github, args=(dict(state),), daemon=True).start()


def save_state_sync(state):
    """زي save_state بس بتستنى رفع GitHub يخلص فعلاً - تستخدم بعد لحظات حرجة
    (زي تحديث الأرقام المشوفة) عشان نضمن الحفظ قبل أي redeploy محتمل."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    save_state_to_github(dict(state))


state = load_state()


# ═══════════════════════════════════════════════════════════
# منطق تحديد الأرقام المميزة
# ═══════════════════════════════════════════════════════════
def _check_pattern_in_window(w: str, is_tail: bool = False) -> str | None:
    """يفحص نافذة أرقام (substring) وبيرجع سبب لو لقى نمط مميز جواها، أو None لو مفيش.
    is_tail: النافذة دي هي فعلاً آخر أرقام الرقم (تسمح بتكرار من 3 أرقام بس هنا)."""
    n = len(w)

    # كل الأرقام في النافذة متطابقة: 0000 / 7777 (أو 3 أرقام لو في آخر الرقم فعلاً: 777)
    min_repeat = 3 if is_tail else 4
    if len(set(w)) == 1 and n >= min_repeat:
        return f"متكررة ({w}) 🔥"

    # متسلسلة تصاعدي كاملة: 1234 / 34567
    if n >= 4 and all(int(w[i]) - int(w[i-1]) == 1 for i in range(1, n)):
        return f"متسلسلة تصاعدي ({w}) ⬆️"

    # متسلسلة تنازلي كاملة: 4321 / 76543
    if n >= 4 and all(int(w[i-1]) - int(w[i]) == 1 for i in range(1, n)):
        return f"متسلسلة تنازلي ({w}) ⬇️"

    # نص النافذة زي نصها التاني بالظبط (لازم زوجي الطول): 12341234 / 70707070
    if n >= 6 and n % 2 == 0:
        half = n // 2
        if w[:half] == w[half:]:
            return f"نصف متكرر ({w[:half]}-{w[half:]}) 🔁"

    return None


def _is_true_sequence(nums: list, allowed_steps: tuple = (1, 10, 100, 1000)) -> tuple:
    """يتأكد إن الأرقام دي تسلسل حقيقي (كل رقم = اللي قبله + خطوة ثابتة من allowed_steps)
    يرجع (True, step) لو تسلسل حقيقي، وإلا (False, None)"""
    if len(nums) < 2:
        return False, None
    diffs = [nums[i+1] - nums[i] for i in range(len(nums)-1)]
    if len(set(diffs)) != 1:
        return False, None
    step = diffs[0]
    if step == 0:
        return False, None
    if abs(step) in allowed_steps:
        return True, step
    return False, None


def _check_group_patterns(d: str) -> str | None:
    """يفحص أنماط المجموعات - بس تسلسل رقمي حقيقي (زي 100-200-300 أو 10-20-30)
    مش أي فرق عشوائي بين مجموعتين"""
    n = len(d)

    # مجموعتين من 3 أرقام: تسلسل حقيقي بخطوة 10 أو 100 (700-800) أو تطابق كامل (700-700)
    if n == 6:
        g1, g2 = int(d[:3]), int(d[3:])
        is_seq, step = _is_true_sequence([g1, g2], allowed_steps=(10, 100))
        if is_seq:
            arrow = "⬆️" if step > 0 else "⬇️"
            return f"مجموعتين متسلسلتين ({d[:3]}-{d[3:]}) {arrow}"
        if g1 == g2 and g1 > 0:
            return f"مجموعتين متطابقتين ({d[:3]}-{d[3:]}) 🔁"

    # مجموعتين من 4 أرقام: تسلسل حقيقي (1000-2000 بخطوة 1000، أو 1234-1235 بخطوة 1)
    if n == 8:
        g1, g2 = int(d[:4]), int(d[4:])
        is_seq, step = _is_true_sequence([g1, g2])
        if is_seq:
            arrow = "⬆️" if step > 0 else "⬇️"
            return f"مجموعتين متسلسلتين ({d[:4]}-{d[4:]}) {arrow}"

    # 3 مجموعات (3-3-2): تسلسل حقيقي بين أول مجموعتين بخطوة 10 أو 100: 100-200 / 700-600
    if n == 8:
        g1, g2, g3 = d[0:3], d[3:6], d[6:8]
        n1, n2 = int(g1), int(g2)
        is_seq, step = _is_true_sequence([n1, n2], allowed_steps=(10, 100))
        if is_seq:
            return f"نمط ({g1}-{g2}-{g3}) 🎯"

    # مجموعات من رقمين (4 مجموعات): تسلسل حقيقي (10-20-30-40) أو تطابق كامل
    if n == 8:
        quads = [d[i:i+2] for i in range(0, 8, 2)]
        quad_nums = [int(q) for q in quads]
        is_seq, step = _is_true_sequence(quad_nums)
        if is_seq:
            arrow = "⬆️" if step > 0 else "⬇️"
            return f"مجموعات متتالية ({'-'.join(quads)}) {arrow}"
        if len(set(quads)) == 1:
            return f"مجموعات متكررة ({'-'.join(quads)}) 🔁"

    return None


def is_fancy(number: str) -> dict:
    """
    يفحص جسم الرقم (آخر 8 أرقام بعد كود الشبكة 010/011/012/015) عن أي نمط مميز
    جوه أي جزء منه: البداية، النص، أو النهاية - مش بس آخره.
    كود الشبكة نفسه (أول 3 أرقام) مستبعد من الفحص عشان مايلخبطش النتيجة.
    """
    full = number.strip()
    # جسم الرقم الحقيقي: آخر 8 أرقام (بعد كود الشبكة 010/011/012/015)
    d = full[-8:]
    if len(d) < 8:
        return {"fancy": False}

    # ── 1) فحص التكرار/التسلسل/النصف المتكرر في أي نافذة من 3 لـ 8 أرقام جوه جسم الرقم ──
    best_match = None
    best_len = 0
    for window_len in range(8, 2, -1):  # نبدأ بأطول نافذة عشان أقوى نمط يتلقط الأول
        for start in range(0, len(d) - window_len + 1):
            w = d[start:start + window_len]
            is_tail = (start + window_len == len(d))  # النافذة دي بتوصل لآخر الرقم فعلاً
            reason = _check_pattern_in_window(w, is_tail=is_tail)
            if reason and window_len > best_len:
                best_match = reason
                best_len = window_len
    if best_match:
        return {"fancy": True, "reason": best_match}

    # ── 2) فحص أنماط المجموعات (3-3-2 / 4-4 / 2-2-2-2) على تقسيم الرقم الطبيعي بس ──
    # (مش أي نافذة فرعية عشوائية - عشان منمسكش أرقام زي "447-448" اللي طالعة بالصدفة من نص الرقم)
    reason = _check_group_patterns(d)
    if reason:
        return {"fancy": True, "reason": reason}

    # ── 3) جسم الرقم بالكامل من رقمين فريدين بس: 10101010 ──
    if len(set(d)) <= 2:
        return {"fancy": True, "reason": f"رقمين فريدين فقط ({d}) 💎"}

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
            save_state_sync(state)  # حفظ متزامن هنا عشان الأرقام المشوفة تفضل محفوظة أكيد

        # ابعت رسالة/رسائل تيليجرام
        target_chat = chat_id or CHAT_ID
        if bot and target_chat:
            LINE_LABELS = {"simcard": "📱 SIM Card (200 EGP)", "esim": "💿 eSIM (350 EGP)"}
            messages = []

            if has_new:
                total_new = len(new_fancy["simcard"]) + len(new_fancy["esim"])
                current = f"🌟 <b>أرقام مميزة جديدة على فودافون!</b> ({total_new} رقم)\n\n"

                for lt in ["simcard", "esim"]:
                    if not new_fancy[lt]:
                        continue
                    section_header = f"{LINE_LABELS[lt]}\n"
                    # لو إضافة الهيدر هتخلي الرسالة تقرب من الحد الأقصى، ابدأ رسالة جديدة
                    if len(current) + len(section_header) > 3800:
                        messages.append(current)
                        current = ""
                    current += section_header

                    for item in new_fancy[lt]:
                        line = f"  ├ <code>{item['number']}</code> — {item['reason']}\n"
                        if len(current) + len(line) > 3800:
                            messages.append(current)
                            current = ""
                        current += line
                    current += "\n"

                current += "🔗 <a href='https://eshop.vodafone.com.eg/en/lines/red/numbers'>شوف واشتري هنا</a>"
                messages.append(current)
            else:
                now_str = datetime.now(timezone.utc).strftime('%H:%M UTC')
                messages.append(
                    f"🔍 فحص الساعة {now_str}\n"
                    f"مفحوص: {totals['simcard']} SIM + {totals['esim']} eSIM\n"
                    f"مفيش أرقام مميزة جديدة دلوقتي."
                )

            for i, msg in enumerate(messages):
                try:
                    if loop is not None:
                        fut = asyncio.run_coroutine_threadsafe(
                            bot.send_message(chat_id=target_chat, text=msg, parse_mode="HTML"),
                            loop
                        )
                        fut.result(timeout=30)
                    else:
                        log("⚠️ مفيش event loop متاح لإرسال الرسالة")
                except Exception as e:
                    log(f"⚠️ فشل إرسال الرسالة {i+1}/{len(messages)}: {e}")

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
        [InlineKeyboardButton("🗑️ مسح كل الأرقام المخزنة", callback_data="clear_seen_confirm")],
    ])


def clear_seen_confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ أيوة امسح", callback_data="clear_seen_yes"),
         InlineKeyboardButton("❌ لأ رجّعني", callback_data="main_menu")],
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

STRANGER_MESSAGE_NO_BUTTON = STRANGER_MESSAGE.replace(
    "تواصل معايا من الزرار تحت 👇",
    f"تواصل معايا مباشرة: (ID: {ADMIN_ID})"
)


async def reply_to_stranger(message):
    """يرد على أي حد مش الأدمن، مع fallback لو زرار الـ deep link فشل"""
    try:
        await message.reply_text(STRANGER_MESSAGE, reply_markup=stranger_reply_keyboard())
    except Exception:
        # لو فشل الزرار (مثلاً بسبب خصوصية المستخدم) نبعت من غير زرار
        await message.reply_text(STRANGER_MESSAGE_NO_BUTTON)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await reply_to_stranger(update.message)
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
        header = f"📊 <b>آخر فحص:</b> {dt.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        header += f"📱 SIM Card: {result.get('simcard', 0)} رقم متفحوص\n"
        header += f"💿 eSIM: {result.get('esim', 0)} رقم متفحوص\n\n"
        fancy_sim = result.get("fancy_simcard", [])
        fancy_esim = result.get("fancy_esim", [])
        all_fancy = fancy_sim + fancy_esim

        if not all_fancy:
            await safe_edit(query, header + "🔍 مفيش أرقام مميزة جديدة في آخر فحص.",
                             parse_mode="HTML", reply_markup=main_menu_keyboard())
            return

        # نبني الرسائل مقسمة لو العدد كبير (حد أقصى تيليجرام 4096 حرف)
        messages = []
        current = header + "🌟 آخر أرقام مميزة اتلقت:\n"
        for item in all_fancy:
            line = f"  ├ <code>{item['number']}</code> — {item['reason']}\n"
            if len(current) + len(line) > 3800:
                messages.append(current)
                current = ""
            current += line
        messages.append(current)

        # آخر رسالة بس بتاخد الأزرار عشان مايتكررش المنيو
        for i, msg in enumerate(messages):
            if i == len(messages) - 1:
                await safe_edit(query, msg, parse_mode="HTML", reply_markup=main_menu_keyboard())
            elif i == 0:
                await safe_edit(query, msg, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

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

    elif data == "clear_seen_confirm":
        with state_lock:
            seen_sim = len(state.get("seen", {}).get("simcard", []))
            seen_esim = len(state.get("seen", {}).get("esim", []))
        msg = (
            f"⚠️ <b>متأكد إنك عايز تمسح كل الأرقام المخزنة؟</b>\n\n"
            f"هيتمسح {seen_sim + seen_esim} رقم ({seen_sim} SIM + {seen_esim} eSIM).\n\n"
            f"بعد المسح، البوت هيعتبر كل الأرقام الموجودة على الموقع دلوقتي "
            f"\"جديدة\" وهيبعتلك بيها تاني في أول فحص جاي."
        )
        await safe_edit(query, msg, parse_mode="HTML", reply_markup=clear_seen_confirm_keyboard())

    elif data == "clear_seen_yes":
        with state_lock:
            state["seen"] = {"simcard": [], "esim": []}
            save_state_sync(state)  # حفظ متزامن عشان المسح يفضل مؤكد حتى لو حصل redeploy فوراً
        await safe_edit(
            query,
            "🗑️ تم مسح كل الأرقام المخزنة.\n\n"
            "أول فحص جاي هيبعتلك كل الأرقام المميزة الموجودة على الموقع حالياً.",
            reply_markup=main_menu_keyboard()
        )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await reply_to_stranger(update.message)
        return
    await update.message.reply_text("📋 لوحة التحكم:", reply_markup=main_menu_keyboard())


async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أي رسالة تانية (مش أمر معروف) - لو مش الأدمن يرد برسالة التعريف"""
    if not update.message:
        return
    if update.effective_user.id != ADMIN_ID:
        await reply_to_stranger(update.message)
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
