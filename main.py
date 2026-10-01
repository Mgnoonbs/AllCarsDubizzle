from __future__ import annotations

from datetime import datetime
from html import escape
import os
import re
import sqlite3
import time
from typing import Any
from urllib.parse import urljoin

import pytz
import requests
from bs4 import BeautifulSoup, Tag

UAE_TZ = pytz.timezone("Asia/Dubai")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SCRAPER_KEY_NAMES = (
    "SCRAPER_API_KEY",
    "SCRAPER_API_KEY2",
    "SCRAPER_API_KEY3",
    "SCRAPER_API_KEY4",
)
SCRAPINGANT_KEY_NAMES = (
    "SCRAPINGANT_API_KEY",
    "SCRAPINGANT_API_KEY2",
    "SCRAPINGANT_API_KEY3",
    "SCRAPINGANT_API_KEY4",
    "SCRAPINGANT_API_KEY5",
    "SCRAPINGANT_API_KEY6",
    "SCRAPINGANT_API_KEY7",
    "SCRAPINGANT_API_KEY8",
)
SCRAPER_API_KEYS = [(n, os.getenv(n)) for n in SCRAPER_KEY_NAMES if os.getenv(n)]
SCRAPINGANT_API_KEYS = [(n, os.getenv(n)) for n in SCRAPINGANT_KEY_NAMES if os.getenv(n)]
DB_FILE = os.getenv("DB_FILE", "sent_ads.db")
MAX_ADS_PER_RUN = int(os.getenv("MAX_ADS_PER_RUN", "5"))
TARGET_URL = "https://uae.dubizzle.com/ar/motors/used-cars/toyota/?seller_type=OW&sorting=date_desc"
BASE_URL = "https://uae.dubizzle.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def now_uae() -> str:
    return datetime.now(UAE_TZ).strftime("%Y-%m-%d %H:%M:%S")


def clean_text(node: Tag | None, default: str = "غير محدد") -> str:
    if not node:
        return default
    value = " ".join(node.get_text(" ", strip=True).split())
    return value or default


def response_has_listings(html: str) -> bool:
    return bool(re.search(r"data-testid=[\"']listing-|listing-card-wrapper|listing-price", html))


def fetch_direct(url: str) -> str | None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ar,en;q=0.8"},
            timeout=45,
        )
        response.raise_for_status()
        if response_has_listings(response.text):
            return response.text
        print(f"[{now_uae()}] الجلب المباشر أعاد صفحة بلا بطاقات، سيتم استخدام ScrapingAnt.")
    except requests.RequestException as exc:
        print(f"[{now_uae()}] فشل الجلب المباشر: {exc}")
    return None


def fetch_with_fallback(url: str) -> str | None:
    html = fetch_direct(url)
    if html:
        return html
    if not SCRAPER_API_KEYS and not SCRAPINGANT_API_KEYS:
        print(f"[{now_uae()}] لا توجد مفاتيح ScraperAPI أو ScrapingAnt مهيأة.")
        return None

    for name, api_key in SCRAPER_API_KEYS:
        try:
            print(f"[{now_uae()}] محاولة دوبيزل عبر مفتاح {name}...")
            response = requests.get(
                "https://api.scraperapi.com/",
                params={"api_key": api_key, "url": url, "render": "true", "country_code": "ae"},
                timeout=90,
            )
            if response.ok and response.text and response_has_listings(response.text):
                print(f"[{now_uae()}] نجح الجلب عبر {name}.")
                return response.text
            print(f"[{now_uae()}] فشل أو استنفد {name} (HTTP {response.status_code})، الانتقال للتالي.")
        except requests.RequestException as exc:
            print(f"[{now_uae()}] خطأ في {name}: {exc}، الانتقال للتالي.")
        time.sleep(2)

    for name, api_key in SCRAPINGANT_API_KEYS:
        try:
            print(f"[{now_uae()}] محاولة دوبيزل عبر مفتاح {name}...")
            response = requests.get(
                "https://api.scrapingant.com/v2/general",
                params={"url": url, "x-api-key": api_key, "browser": "true"},
                timeout=90,
            )
            if response.ok and response.text and response_has_listings(response.text):
                print(f"[{now_uae()}] نجح الجلب عبر {name}.")
                return response.text
            print(f"[{now_uae()}] فشل أو استنفد {name} (HTTP {response.status_code})، الانتقال للتالي.")
        except requests.RequestException as exc:
            print(f"[{now_uae()}] خطأ في {name}: {exc}، الانتقال للتالي.")
        time.sleep(2)
    return None


def first_attr(node: Tag, selectors: list[str], attr: str) -> str | None:
    for selector in selectors:
        found = node.select_one(selector)
        if found and found.get(attr):
            return found.get(attr)
    return None


def parse_card(card: Tag) -> dict[str, Any] | None:
    href = card.get("href", "")
    if not href:
        link = card.select_one("a[href]")
        href = link.get("href", "") if link else ""
    if not href or "/motors/" not in href:
        return None
    full_link = urljoin(BASE_URL, href.split("?")[0])
    parts = [p for p in full_link.rstrip("/").split("/") if p]
    ad_id = parts[-1] if parts else full_link

    title = clean_text(card.select_one("[data-testid='subheading-text'], h2, h3"), "تويوتا")
    price = clean_text(card.select_one("[data-testid='listing-price'], [class*='price']"), "غير معلن")
    year = clean_text(card.select_one("[data-testid='listing-year']"), "غير محدد")
    km = clean_text(card.select_one("[data-testid='listing-kilometers']"), "غير محدد")
    location = clean_text(card.select_one("[data-testid='listing-location']"), "الإمارات")

    # fallback: استخراج الخصائص الظاهرة داخل البطاقة
    all_text = " ".join(card.stripped_strings)
    if year == "غير محدد":
        found_year = re.search(r"\b(?:19|20)\d{2}\b", all_text)
        year = found_year.group(0) if found_year else year
    if km == "غير محدد":
        found_km = re.search(r"[\d,]+\s*كم", all_text)
        km = found_km.group(0) if found_km else km

    image_url = first_attr(card, ["[data-testid='image-gallery'] img", "img"], "src")
    if not image_url:
        image_url = first_attr(card, ["img"], "data-src")

    return {
        "id": ad_id,
        "title": title,
        "price": price,
        "year": year,
        "km": km,
        "location": location,
        "image": urljoin(BASE_URL, image_url) if image_url else None,
        "link": full_link,
    }


def parse_ads(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", attrs={"data-testid": lambda v: v and v.startswith("listing-")})
    if not anchors:
        anchors = soup.select("a[href*='/motors/']")
    ads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in anchors:
        ad = parse_card(card)
        if ad and ad["id"] not in seen:
            seen.add(ad["id"])
            ads.append(ad)
        if len(ads) >= MAX_ADS_PER_RUN:
            break
    return ads


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS sent_ads (ad_id TEXT PRIMARY KEY)")
    conn.commit()
    return conn


def send_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN أو CHAT_ID غير موجود.")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
        return response.ok
    except requests.RequestException as exc:
        print(f"خطأ إرسال الرسالة: {exc}")
        return False


def send_photo(image: str, caption: str) -> bool:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "photo": image, "caption": caption, "parse_mode": "HTML"},
            timeout=25,
        )
        if response.ok:
            return True
        print(f"فشل sendPhoto: HTTP {response.status_code} {response.text[:250]}")
    except requests.RequestException as exc:
        print(f"خطأ إرسال الصورة: {exc}")
    return send_message(caption)


def caption(ad: dict[str, Any]) -> str:
    text = (
        "🚘 <b>إعلان جديد من دوبيزل - تويوتا</b>\n\n"
        f"🚗 <b>السيارة:</b> {escape(ad['title'])}\n"
        f"💰 <b>السعر:</b> {escape(ad['price'])}\n"
        f"📅 <b>الموديل:</b> {escape(ad['year'])}\n"
        f"🛣️ <b>الممشى:</b> {escape(ad['km'])}\n"
        f"📍 <b>الموقع:</b> {escape(ad['location'])}\n\n"
        f"🔗 <a href=\"{escape(ad['link'], quote=True)}\">مشاهدة تفاصيل الإعلان</a>"
    )
    return text[:1024]


def process_and_send() -> None:
    print(f"[{now_uae()}] بدء فحص دوبيزل...")
    html = fetch_with_fallback(TARGET_URL)
    if not html:
        print("تعذر جلب صفحة دوبيزل.")
        return
    ads = parse_ads(html)
    print(f"[{now_uae()}] تم العثور على {len(ads)} إعلان حديث.")
    conn = open_db()
    try:
        for ad in ads:
            if conn.execute("SELECT 1 FROM sent_ads WHERE ad_id=?", (ad["id"],)).fetchone():
                print(f"الإعلان {ad['id']} مكرر.")
                continue
            sent = send_photo(ad["image"], caption(ad)) if ad["image"] else send_message(caption(ad))
            if sent:
                conn.execute("INSERT OR IGNORE INTO sent_ads(ad_id) VALUES(?)", (ad["id"],))
                conn.commit()
                print(f"[{now_uae()}] تم إرسال: {ad['title']}")
                time.sleep(2)
    finally:
        conn.close()


if __name__ == "__main__":
    process_and_send()
