import json
import os                       # อ่านค่าจาก .env + สลับไฟล์
import threading                # Lock กันเขียนไฟล์ชนกัน
import uuid                     # สร้าง id ให้บันทึก
from datetime import date       # วันที่วันนี้
from functools import lru_cache # จำผลเรียก TMDB ไว้
from pathlib import Path        # path ของไฟล์

import requests                 # เรียก TMDB API
from dash import ALL, MATCH, Dash, Input, Output, State, ctx, dcc, html, no_update
from dotenv import load_dotenv  # โหลดค่าลับจาก .env
from flask import redirect      # เด้งไปลิงก์ตัวอย่างหนัง

# อ่านไฟล์ .env
load_dotenv()
APP_PASSWORD = os.getenv("APP_PASSWORD")
SECRET_MESSAGE = os.getenv("SECRET_MESSAGE")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

app = Dash(suppress_callback_exceptions=True)   # แต่ละหน้าสร้างทีหลัง → ไม่ต้องเตือนว่าหา id ไม่เจอ
app.layout = html.Div([
    dcc.Location(id="url", refresh="callback-nav"),                # เปลี่ยน URL ของเบราว์เซอร์
    dcc.Store(id="logged-in", data=False, storage_type="memory"),  # เก็บสถานะ login
    dcc.Store(id="last-page", data=None, storage_type="memory"),   # หน้าก่อนหน้า (ใช้เลือกว่าจะพลิกหน้ากระดาษไหม)
    dcc.Store(id="last-query", data="", storage_type="memory"),    # คำค้นหาล่าสุด (กลับจากหน้ารายละเอียดหนังแล้วผลยังอยู่)
    html.Div(id="page"),                                           # พื้นที่แสดงหน้าปัจจุบัน
])


# หน้าปก (Login)
login_layout = html.Div([
    html.Div([
        # แถบ index
        html.Div([
            html.Span("Home", className="book-tab active"),
            html.Span("Search a movie", className="book-tab"),
            html.Span("My Journal", className="book-tab"),
        ], className="book-tabs login-tabs"),
        html.Div(className="book-back"),     # ปกหลัง
        html.Div(className="book-inside"),   # หน้าใน
        # ปกหน้า
        html.Div([
            html.Div([
                html.Div([
                    dcc.Input(id="password", type="password", placeholder="your secret"),   # ช่องใส่รหัส
                    html.Button(id="btn-submit", title="Open"),                              # ปุ่มเปิด
                ], className="password-row"),
                html.Div(id="output"),   # ข้อความผลลัพธ์
            ], className="book-cover"),
            html.Div(className="book-cover-back"),   # ด้านในปก
        ], className="book-flip"),
        html.Img(src=app.get_asset_url("decor_earphone.png"), className="book-earphone"),
        html.Img(src=app.get_asset_url("decor_clip.png"), className="book-clip"),
    ], id="book", className="book"),
    
    # เริ่มนับตอนรหัสถูก → รอปกเปิด + ซูมเสร็จแล้วค่อยไปหน้า Home
    dcc.Interval(id="open-timer", interval=2050, max_intervals=1, disabled=True),
], className="login-page")


# index
def tab_link(label, href, active): # แถบ 1 อัน (หน้าที่เปิดอยู่ = active → ยื่นออกมา)
    cls = "book-tab active" if href == active else "book-tab"
    return dcc.Link(label, href=href, className=cls)

# ลำดับแถบ index
TAB_ORDER = ["/journal", "/search", "/my-journal"]

def journal_page_number(path): # "/my-journal/p1" → 1, "/my-journal/p2" → 2 (หน้าอื่น → None)
    if path and path.startswith("/my-journal/p") and path.removeprefix("/my-journal/p").isdigit():
        return int(path.removeprefix("/my-journal/p")) or None
    return None


def journal_page_path(n): # 1 → "/my-journal/p1", 2 → "/my-journal/p2"
    return f"/my-journal/p{n}"


def page_order(path): # ลำดับหน้า: Home 0 → Search 1 → หน้าหนัง 1.5 → Wishlist 2 → รีวิว 2.001, 2.002...
    if path and path.startswith("/movie/"):   # หน้ารายละเอียดหนัง
        return 1.5
    n = journal_page_number(path)
    if n:   # หน้าคู่รีวิว
        return 2 + n / 1000
    if path == WISHLIST_PATH:   # Wishlist = หน้าแรกของ My Journal (ก่อนหน้าคู่รีวิว)
        return 2
    return TAB_ORDER.index(path) if path in TAB_ORDER else None


def neighbor_pages(active): # (หน้าก่อนหน้า, หน้าถัดไป) ของหมุดดาว
    if active.startswith("/movie/"):   # หน้าหนัง
        return "/search", "/my-journal"
    n = journal_page_number(active)
    pages = journal_page_count()
    if n:   # หน้าคู่รีวิว (หน้าแรก ← Wishlist)
        return (WISHLIST_PATH if n == 1 else journal_page_path(n - 1),
                journal_page_path(n + 1) if n < pages else None)
    if active == WISHLIST_PATH:   # Wishlist: Search ← Wishlist → หน้าคู่รีวิวแรก
        return "/search", journal_page_path(1)
    if active not in TAB_ORDER:
        return None, None
    # แถบหลัก: ก่อนหน้า/ถัดไปตาม TAB_ORDER
    i = TAB_ORDER.index(active)
    return (TAB_ORDER[i - 1] if i > 0 else None,
            TAB_ORDER[i + 1] if i + 1 < len(TAB_ORDER) else None)


def page_pin(href, side): # กดแล้วพลิกไปหน้าก่อนหน้า / ถัดไป
    if href is None:
        return None
    return dcc.Link(html.Img(src=app.get_asset_url("decor_pin.png"), className="page-pin-img"),
                    href=href, title="Previous page" if side == "prev" else "Next page",
                    className=f"page-pin page-pin-{side}")


def right_paper(path): # หน้ากระดาษตอนผลิกตรงกับหน้าที่เปิด
    if path and path.startswith("/movie/"):
        return " paper-blue"
    return ""


def left_paper(path): # พื้นกระดาษซ้ายตอนพลิก (หน้าหนัง = รูปพิเศษ)
    if path and path.startswith("/movie/"):
        return " paper-movie-left"
    return ""


def turn_direction(last, active): # ผลิกหน้ากระดาษซ้ายขวา
    last_order, active_order = page_order(last), page_order(active)
    if last_order is None or active_order is None or last == active:
        return None
    return "forward" if active_order > last_order else "back"


def page(active, *content, left=None, last=None, overlay=None): # โครงสมุดของทุกหน้าหลัง login
    # active = หน้านี้, content = กระดาษขวา, left = กระดาษซ้าย, last = หน้าก่อนหน้า, overlay = ของที่เด้งกลางจอ
    profile = load_profile() if active == "/journal" else {}    # ข้อมูลบนการ์ดในซอง (หน้า Home)
    turn = turn_direction(last, active)
    # แถบที่ไฮไลต์: หน้าหนังอยู่ใต้แถบ Search, หน้าคู่ที่ 2 ของ My Journal อยู่ใต้แถบ My Journal
    tab = "/search" if active.startswith("/movie/") else "/my-journal" if active.startswith("/my-journal") else active
    prev_page, next_page = neighbor_pages(active)   # หมุดดาวมุมล่างซ้าย/ขวา
    turn_back = turn == "back"
    # พลิกไปซ้ายจากหน้าที่มีกระดาษซ้ายอยู่แล้ว (เช่น Search → My Journal): กระดาษซ้ายต้องอยู่ตลอด
    # ไม่งั้นระหว่างพลิกจะเห็นปกน้ำตาลแวบหนึ่ง → ซ่อนแค่เนื้อหาจนหน้าที่พลิกตกลงมา
    keep_left = turn == "forward" and last != "/journal"
    cover_class = "home-cover"   # เติม class ตามทิศพลิก → CSS เลือก animation
    # ต้องมีช่องว่างนำหน้าชื่อ class เสมอ → ต่อแล้วได้ "home-cover turning-back"
    # (ถ้าไม่มีช่องว่างจะติดกันเป็น "home-coverturning-back" = class ใหม่ที่ CSS ไม่รู้จัก)
    if turn_back:
        cover_class += " turning-back"   # พลิกกลับ (ไปหน้าก่อนหน้า)
    if keep_left:
        cover_class += " keep-left"      # พลิกไปข้างหน้าแต่กระดาษซ้ายต้องอยู่ตลอด
    return html.Div([
        html.Div([
            # ---- กระดาษซ้าย ----
            html.Div([
                html.Div(left, className="home-content home-content-left") if left is not None else None,
                # หน้ากระดาษที่พลิกกลับไปขวา: ด้านหน้า = กระดาษซ้ายของหน้าก่อนหน้า
                # พลิกข้ามสันไปขวา → ด้านหลังกลายเป็นหน้าขวาของหน้าใหม่
                html.Div([
                    # ด้านหน้า = หน้าซ้ายของหน้าก่อนหน้า → ใช้พื้นแบบเดียวกับหน้านั้น
                    html.Div(className="page-turn-front page-turn-right-front" + left_paper(last)),
                    # ด้านหลัง = หน้าขวาของหน้าใหม่ → ใช้พื้นแบบเดียวกับหน้าใหม่
                    html.Div(className="page-turn-back page-turn-right-back" + right_paper(active)),
                ], className="page-turn-right") if turn_back else None,
                # หมุดดาวมุมซ้ายล่าง → หน้าก่อนหน้า (เฉพาะหน้าที่มีกระดาษซ้าย)
                page_pin(prev_page, "prev") if left is not None else None,
                # หน้า Home: ด้านในปกซ้าย = ดอกไม้ขาว ดาวทอง เพชร (รูป cover_left_decor.png จาก Figma: Book → open_book_spread_no_holder)
                # บัตร MOVIE LOVER CLUB วางบนปกตรง ๆ ทับรูปตกแต่ง (กดแล้วเด้งขึ้นกลางจอ)
                html.Img(src=app.get_asset_url("cover_left_decor.png"), className="cover-decor") if active == "/journal" else None,
                html.Div(id_card(profile, after_turn=turn_back), className="id-on-cover") if active == "/journal" else None,
            ], className=cover_class),
            # ---- กระดาษขวา ----
            html.Div([
                # แถบอินเด็กซ์ด้านขวา (แบบเดียวกับหน้า Login)
                # Home / Search a movie / My Journal กดแล้วไปหน้านั้น (แถบของหน้าที่เปิดอยู่ได้ class "active")
                html.Div([
                    tab_link("Home", "/journal", tab),
                    tab_link("Search a movie", "/search", tab),
                    tab_link("My Journal", "/my-journal", tab),
                ], className="book-tabs"),
                html.Div(list(content), className="home-content"),
                # หมุดดาวมุมขวาล่าง → หน้าถัดไป
                page_pin(next_page, "next"),
                # หน้า Home: 3 แผ่นวางซ้อนแบบคอลลาจ (ไม่มีซองแล้ว)
                # ล่างสุด = กระดาษ This or That เต็มหน้า, ทับขวาบน = โปสการ์ดหนัง (บัตร MOVIE LOVER CLUB อยู่ในช่องปกซ้าย)
                this_or_that_preview(profile, after_turn=turn_back) if active == "/journal" else None,
                movie_postcard(profile, after_turn=turn_back) if active == "/journal" else None,
                # หน้ากระดาษที่กำลังพลิกไปซ้าย: ด้านหน้า = หน้าขวาของหน้าก่อนหน้า
                # พลิกข้ามสันไปซ้าย → ด้านหลังกลายเป็นกระดาษซ้ายของหน้าใหม่
                html.Div([
                    # ด้านหน้า = หน้าขวาของหน้าก่อนหน้า → ใช้พื้นแบบเดียวกับหน้านั้น
                    html.Div(className="page-turn-front" + right_paper(last)),
                    # ด้านหลัง = หน้าซ้ายของหน้าใหม่ → ใช้พื้นแบบเดียวกับหน้าใหม่
                    html.Div(className="page-turn-back" + left_paper(active)),
                ], className="page-turn") if turn == "forward" else None,
            ], className="home-sheet"),
        ], className="home-book"),
        # การ์ดที่เด้งออกมากลางจอ
        id_card_modal(profile) if active == "/journal" else None,
        movie_postcard_modal(profile) if active == "/journal" else None,
        this_or_that_modal(profile) if active == "/journal" else None,
        overlay,
    ], className="home-page")



# หน้า Home (/journal)
# ข้อมูลบน id card
PROFILE_FILE = Path(__file__).parent / "data" / "profile.json"
PROFILE_FIELDS = ["name", "birth", "since", "favorite"]

def load_profile(): # อ่าน data/profile.json
    if PROFILE_FILE.exists():
        return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    return {}

def save_profile(profile): # บันทึก profile
    write_json(PROFILE_FILE, profile)

def photo_preview(photo): # รูปบนบัตร
    if photo:
        return html.Img(src=photo, className="id-card-photo-img")
    return html.Div("+ photo", className="id-card-photo-empty")


def member_number(name): # เปลี่ยนตามชื่อ
    # รวมรหัสตัวอักษร × ตำแหน่ง → เหลือ 4 หลัก
    return f"{sum(ord(c) * (i + 7) for i, c in enumerate(name or 'me')) % 10000:04d}"


def id_card_body(profile, editable):
    """ข้อมูลบนการ์ด MOVIE LOVER CLUB — พื้นบัตรทั้งใบ (ลายกันปลอม, หัวบัตร, ช่อง, MEMBER, VALID ฯลฯ)
    เป็นรูปเดียว id_card_template.jpg (export จาก Figma: หน้า Components → id_card_license)
    ฟังก์ชันนี้วางเฉพาะข้อมูลทับลงในช่องของรูป (ตำแหน่งแต่ละชิ้นอยู่ใน style.css ตามตำแหน่งใน Figma)
    ใช้ทั้งการ์ดในซองและการ์ดที่เด้งออกมากลางจอ
    editable=False: การ์ดในซอง แสดงข้อมูลอย่างเดียว (กดแล้วเด้งออกมา)
    editable=True:  การ์ดกลางจอ กรอกชื่อ/เลือกรูปได้ (บันทึกลง data/profile.json)"""

    def value(key, placeholder):
        # ค่าในช่อง (ตัวพิมพ์ดีด) วางทับช่องว่างในรูป
        # กลางจอ = ช่องกรอก (debounce → บันทึกตอน Enter/คลิกออก), ในซอง = ข้อความเฉย ๆ
        if editable:
            return dcc.Input(id=f"id-{key}", type="text", value=profile.get(key, ""), placeholder=placeholder,
                             debounce=True, className=f"id-card-input isic-{key}")
        return html.Div(profile.get(key, ""), id=f"id-preview-{key}", className=f"id-card-input isic-{key}")

    name = profile.get("name", "")
    photo = photo_preview(profile.get("photo"))
    number = member_number(name)

    def stamp(cls):
        return html.Div(html.Div(" ".join(number), className="mlc-stamp-no"), className=f"mlc-stamp {cls}")

    return [
        # รูป (การ์ดกลางจอ: คลิกรูปแล้วเลือกรูปจากเครื่อง) + คลิปหนีบด้านบน
        html.Div([
            dcc.Upload(photo, id="id-photo-upload", accept="image/*", className="id-card-photo")
            if editable else html.Div(photo, id="id-preview-photo", className="id-card-photo"),
            # คลิปหนีบกระดาษหนีบขอบบนของรูป (decor_clip_3.png)
            html.Img(src=app.get_asset_url("decor_clip_3.png"), className="isic-clip"),
        ], className="id-card-photo-wrap"),
        html.Div(name, id="id-sig" if editable else "id-preview-sig", className="lic-sig"),   # ลายเซ็น
        html.Div(number + "26", className="isic-no"),                                         # เลขสมาชิกสีแดง
        value("name", "your name"),
        value("birth", "dd. mm. yyyy"),
        value("since", "2026"),
        value("favorite", "..."),
        html.Div(f"BKK  {date.today():%-m/%-d/%y}", className="isic-hand-date"),
        stamp("isic-stamp-photo"),
        stamp("isic-stamp-right"),
        html.Div(str(date.today().year), className="isic-stamp-year"),
        html.Div("DEC 31", className="isic-stamp-valid"),
    ]


def id_card(profile, after_turn=False):
    """การ์ดสีขาวเสียบอยู่หลังซอง — กดแล้วเด้งออกมากลางจอ"""
    return html.Div(id_card_body(profile, editable=False), id="id-card-preview", n_clicks=0,
                    className="id-card id-card-preview after-turn" if after_turn else "id-card id-card-preview")


def postcard_image(movie):
    """รูปบนโปสการ์ดหนัง: ภาพฉากหนัง (ไม่มี → โปสเตอร์) ครอปกลางภาพเป็นกรอบแนวตั้ง (ใน CSS)
    ยังไม่ได้เลือกหนัง → ข้อความให้กดเลือก"""
    if not movie:   # ยังไม่เลือกหนัง
        return html.Div("pick a movie ✎", className="mpc-img mpc-img-empty")
    image = movie.get("backdrop") or movie.get("poster")   # ใช้ภาพฉากก่อน ไม่มีค่อยใช้โปสเตอร์
    return html.Img(src=f"{TMDB_IMAGE}/w780{image}", className="mpc-img", title=movie.get("title"))


def movie_postcard(profile, after_turn=False):
    """โปสการ์ดหนังกรอบขาว เสียบอยู่ในซองทับการ์ด MOVIE LOVER CLUB — กดแล้วเด้งขึ้นมาเลือกหนังได้
    หนังที่เลือกเก็บใน data/profile.json ("postcard")"""
    movie = profile.get("postcard")
    # กรอบขาว + ดาวเงิน = รูป postcard_template.png (Figma: Components → postcard_template) วางทับใน CSS
    # โค้ดวางแค่รูปหนังให้โผล่ตรงช่องใสของกรอบ
    return html.Div(html.Div(postcard_image(movie), id="mpc-preview-img", className="mpc-photo"),
                    id="mpc-preview", n_clicks=0,
                    className="mpc mpc-preview after-turn" if after_turn else "mpc mpc-preview")


def movie_postcard_modal(profile):
    """โปสการ์ดหนังกลางจอ + ช่องค้นหาหนัง: เลือกผลค้นหา → โปสการ์ดเปลี่ยนเป็นหนังเรื่องนั้นทันที"""
    movie = profile.get("postcard")
    return html.Div([
        html.Div(id="mpc-backdrop", n_clicks=0, className="id-backdrop"),   # พื้นหลังมืด (กดเพื่อปิด)
        html.Div([
            # โปสการ์ดใบใหญ่
            html.Div(html.Div(postcard_image(movie), id="mpc-modal-img", className="mpc-photo"),
                     className="mpc mpc-big"),
            html.Div(movie.get("title", "") if movie else "", id="mpc-title", className="mpc-caption"),   # ชื่อหนังใต้โปสการ์ด
            # ค้นหาหนังมาใส่โปสการ์ด (ช่องแคปซูลแบบหน้า Search)
            html.Div([
                html.Button(id="mpc-search-btn", title="Search", className="search-icon mpc-search-icon"),
                dcc.Input(id="mpc-search", type="text", placeholder="search a movie for this postcard",
                          debounce=True, className="mpc-search-input"),
            ], className="mpc-search"),
            html.Div(id="mpc-results", className="mpc-results"),   # ผลค้นหา (โปสเตอร์เล็กให้กดเลือก)
        ], className="mpc-modal-body"),
    ], id="mpc-modal", className="id-modal")


# This or That
THIS_OR_THAT = [
    ("CINEMA", "HOME"), ("SUBTITLES", "DUBBED"), ("POPCORN", "NACHOS"), ("HORROR", "ROMCOM"),
    ("BOOK FIRST", "MOVIE FIRST"), ("MARATHON", "ONE A NIGHT"), ("FRONT ROW", "BACK ROW"),
    ("SPOILERS OK", "NO SPOILERS"), ("STAY FOR CREDITS", "LEAVE EARLY"),
]


# ตำแหน่งคำตอบแต่ละคำบนรูป this_or_that_template.png:
TOT_BOXES = [
    ((90, 123, 57, 17), (161, 123, 42, 17)), ((90, 148, 81, 17), (185, 148, 57, 17)),
    ((90, 173, 65, 17), (169, 173, 57, 17)), ((90, 198, 57, 17), (161, 198, 57, 17)),
    ((90, 223, 88, 17), (192, 223, 96, 17)), ((90, 248, 73, 17), (177, 248, 96, 17)),
    ((90, 273, 81, 17), (185, 273, 73, 17)), ((90, 298, 96, 17), (200, 298, 96, 17)),
    ((90, 323, 135, 17), (239, 323, 96, 17)),
]


def tot_class(picks, i, pick):
    """class ของคำตอบ: คำที่เลือกได้ "tot-on" → มีวงหมึกฟ้า"""
    return "tot-opt" + (" tot-on" if i < len(picks) and picks[i] == pick else "")


def this_or_that_sheet(profile, editable):
    """ข้อมูลบนกระดาษ This or That — ตัวกระดาษ (รูเจาะ, หัวข้อ, คู่คำ 9 ข้อ, โน้ตแดง) เป็นรูปเดียว
    this_or_that_template.png (export จาก Figma: หน้า Assets → export/this_or_that_template)
    ฟังก์ชันนี้วางเฉพาะ: วงคำตอบ (จุดกดโปร่งใสทับคำในรูป), โน้ตหมึกฟ้าที่มีชื่อ, วันที่ข้างรูเจาะ
    editable=False: แผ่นที่เสียบในซอง (ดูอย่างเดียว), editable=True: แผ่นกลางจอ กดคำเพื่อวงคำตอบ"""
    picks = profile.get("this_or_that") or []
    answers = []
    # 9 ข้อ × 2 คำ = 18 จุดกด (i = ข้อที่, pick = 0 ซ้าย / 1 ขวา)
    for i, (pair, boxes) in enumerate(zip(THIS_OR_THAT, TOT_BOXES)):
        for pick, (label, (x, y, w, h)) in enumerate(zip(pair, boxes)):
            answers.append(html.Div(
                id={"type": "tot" if editable else "tot-prev", "index": i, "pick": pick}, title=label,
                className=tot_class(picks, i, pick), style={"left": x, "top": y, "width": w, "height": h},
                **({"n_clicks": 0} if editable else {})))
    name = (profile.get("name") or "me").lower()
    return [
        html.Div(["Basic Date ", html.U("\u00a0" * 10), "  Changed ", html.U(date.today().strftime("%m/%d/%y"))],
                 className="tot-side"),
        html.Div(f"my movie personality notes made by {name} — circled & signed", className="tot-blue-note"),
        *answers,
    ]


def this_or_that_preview(profile, after_turn=False):
    return html.Div(this_or_that_sheet(profile, editable=False), id="tot-preview", n_clicks=0,
                    className="tot-sheet tot-preview" + (" after-turn" if after_turn else ""))


def this_or_that_modal(profile): # กระดาษ This or That กลางจอ (กดคำเพื่อวง, กดพื้นหลังเพื่อปิด)
    return html.Div([
        html.Div(id="tot-backdrop", n_clicks=0, className="id-backdrop"),
        html.Div(this_or_that_sheet(profile, editable=True), className="tot-sheet tot-modal-sheet"),
    ], id="tot-modal", className="id-modal")


def id_card_modal(profile):
    """การ์ดที่เด้งออกมากลางจอ + พื้นหลังมืด (กดพื้นหลังเพื่อเก็บการ์ดกลับเข้าซอง)
    อยู่นอกสมุด เพราะสมุดถูก transform: scale ไว้ → position: fixed ข้างในจะไม่อิงกับจอ"""
    return html.Div([
        html.Div(id="id-backdrop", n_clicks=0, className="id-backdrop"),
        html.Div(id_card_body(profile, editable=True), className="id-card id-card-modal"),
    ], id="id-modal", className="id-modal")



def home_layout(last):
    return page("/journal", last=last)



# หน้า Search 
# ค้นหาหนังจาก TMDB
TMDB_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p"


def tmdb_get(path, **params):
    """เรียก TMDB API แล้วคืนค่าเป็น dict (ใส่ api_key + ภาษาให้เอง)"""
    response = requests.get(f"{TMDB_URL}{path}",
                            params={"api_key": TMDB_API_KEY, "language": "en-US", **params}, timeout=10)   # รอไม่เกิน 10 วิ
    response.raise_for_status()   # error (เช่น key ผิด) → โยน exception
    return response.json()        # JSON → dict


@lru_cache(maxsize=1)
def genre_map():
    """รหัสประเภทหนัง → ชื่อ (เช่น 28 → "Action") ดึงครั้งเดียวแล้วจำไว้"""
    return {g["id"]: g["name"] for g in tmdb_get("/genre/movie/list")["genres"]}


def genres_of(movie, limit=2): # ประเภทหนัง เช่น "Drama", "Romance"
    names = genre_map()
    return [names[g] for g in movie.get("genre_ids", []) if g in names][:limit]


@lru_cache(maxsize=256)
def trailer_url(movie_id):
    """ลิงก์ตัวอย่างหนังบน YouTube (ไม่มี/เชื่อมต่อไม่ได้ → ลิงก์หน้าหนังบน TMDB แทน)"""
    try:
        for video in tmdb_get(f"/movie/{movie_id}/videos").get("results", []):
            if video.get("site") == "YouTube" and video.get("type") == "Trailer":
                return f"https://www.youtube.com/watch?v={video['key']}"
    except requests.RequestException:
        pass
    return f"https://www.themoviedb.org/movie/{movie_id}"


@app.server.route("/trailer/<int:movie_id>")
def open_trailer(movie_id):
    """ปุ่ม ▶ ลิงก์มาที่นี่ → หาตัวอย่างหนังตอนกดเท่านั้น แล้วเด้งไป YouTube
    (ไม่ต้องดึงตัวอย่างของทุกเรื่องตอนค้นหา → ค้นหาเร็ว)"""
    return redirect(trailer_url(movie_id))


def movie_year(movie): # "2005-09-16" → "2005"
    return (movie.get("release_date") or "")[:4]


def movie_row(number, movie):
    """1 แถวในรายการแบบ playlist: เลข 01 + โปสเตอร์เล็ก (สัดส่วนเต็ม ไม่ครอป) + ชื่อตัวพิมพ์ใหญ่ + ปี · ประเภท (สีเทา)
    กดเลข/รูป/ชื่อ → หน้ารายละเอียดหนัง, ปุ่ม ▶ Trailer ด้านขวา → เปิดตัวอย่างหนัง"""
    poster = movie.get("poster_path")
    sub = " · ".join(x for x in [movie_year(movie), *genres_of(movie, limit=1)] if x)   # เช่น "2009 · Animation"
    return html.Div([
        dcc.Link([
            html.Span(f"{number:02d}", className="row-no"),
            html.Img(src=f"{TMDB_IMAGE}/w154{poster}", className="row-img") if poster
            else html.Span(className="row-img row-img-empty"),
            html.Span([
                html.Span(movie["title"], className="row-title"),
                html.Span(sub, className="row-sub"),
            ], className="row-text"),
        ], href=f"/movie/{movie['id']}", className="row-link"),
        html.A("▶ Trailer", href=f"/trailer/{movie['id']}", target="_blank", className="row-trailer"),
    ], className="movie-row")


@lru_cache(maxsize=256)
def suggest_movies(query):
    """หนัง 6 เรื่องแรกที่ชื่อตรงกับคำค้น (จำผลไว้ พิมพ์ซ้ำไม่ต้องถาม TMDB ใหม่)"""
    return tuple(tmdb_get("/search/movie", query=query)["results"][:6])



def search_layout(last, query=""):
    """หน้า Search — ค้นหาหนังจาก TMDB (กดแถบ "Search a movie")
    ซ้าย = รูปจาก Figma (search_left_page.png) + ช่องพิมพ์ชื่อหนังบนเส้นแบบเซ็นชื่อ
           ระหว่างพิมพ์มีรายชื่อหนังเด้งลงมาให้เลือก (โปสเตอร์เล็ก + ชื่อ + ปี) กดแล้วไปหน้ารายละเอียดหนัง
    ขวา = กด Enter / แว่นขยาย → รายการหนังแบบ playlist 10 เรื่อง (ยังไม่ค้นหา → หนังมาแรงสัปดาห์นี้)"""
    return page(
        "/search",
        # ลายพื้นทั้งหน้า (ตัวเขียนสีฟ้าจาง ๆ + ดาว) export จาก Figma (หน้า Book → search_right_page)
        html.Img(src=app.get_asset_url("search_right_page.png"), className="search-page-art"),
        # รายการหนังแบบ playlist วางบนหน้ากระดาษตรง ๆ (มีจุดหมุนตอนกำลังโหลด)
        html.Div(
            dcc.Loading(html.Div(id="movie-info"), type="dot", color="#5C4436", parent_className="movie-info-loading"),
            className="list-card"),
        left=[
            # ทั้งหน้าเป็นรูปเดียว export จาก Figma (หน้า Book → search_left_page):
            # ตัวเขียนสีฟ้า, ดาว, การ์ดชมพู + หัวข้อ, ดอกลิลลี่, 💭, เส้นเซ็นชื่อ, แว่นขยาย, quote Hugo
            html.Img(src=app.get_asset_url("search_left_page.png"), className="search-page-art"),
            # โค้ดเฉพาะส่วนที่กดได้: ช่องพิมพ์บนเส้น + ปุ่มใส (ทับแว่นขยายในรูป) + รายชื่อหนังที่เด้งลงมา
            html.Div([
                dcc.Input(id="movie-title", type="text", value=query, placeholder="tell me a movie you love...",
                          autoComplete="off", className="cert-input"),
                html.Button(id="search-button", title="Search", className="cert-search-btn"),
                html.Div(id="search-suggest", className="cert-suggest"),
            ], className="cert-line"),
        ],
        last=last,
    )



# ============================================================
# Callbacks: หน้าปก + เลือกหน้าตาม URL
# ============================================================

# ---------- 6.2 ตรวจรหัสผ่านหน้า Login ----------
@app.callback(
    Output("logged-in", "data"),        # บันทึกสถานะ login เป็น True
    Output("output", "children"),       # แสดงข้อความผลลัพธ์
    Output("book", "className"),        # เพิ่ม class "opening" → CSS พลิกปกเปิด
    Output("open-timer", "disabled"),   # เริ่มนับเวลาก่อนเปลี่ยนหน้า
    Input("btn-submit", "n_clicks"),    # ทำงานเมื่อกดปุ่ม Submit
    State("password", "value"),         # อ่านรหัสผ่านที่ผู้ใช้กรอก
)
def check_password(n_clicks, pw):
    # ยังไม่เคยกดปุ่ม → ไม่ต้องอัปเดตอะไร
    if not n_clicks:
        return no_update, no_update, no_update, no_update

    # รหัสผ่านถูก → ตั้งสถานะ login, แสดงข้อความลับ, เปิดปกสมุด, เริ่มนับเวลา
    if pw == APP_PASSWORD:
        return True, SECRET_MESSAGE, "book opening", False

    # ผู้ใช้ยังไม่ได้กรอกรหัสผ่าน
    if pw is None:
        return no_update, "Password not set", no_update, no_update

    # รหัสผ่านผิด
    return no_update, "Wrong password", no_update, no_update



# ---------- 6.2.1 ปกเปิดเสร็จ → ไปหน้า Home ----------
@app.callback(
    Output("url", "pathname"),          # เปลี่ยน URL ไปหน้า /journal
    # ใส่หน้า Home ลงไปเองด้วย: ถ้า URL เป็น /journal อยู่แล้ว (เช่นรีเฟรชหน้า Home แล้วโดนเด้งมาหน้า Login)
    # URL จะไม่เปลี่ยน → ต้องไม่พึ่ง show_page อย่างเดียว ถึงจะได้หน้า Home เสมอ
    Output("page", "children", allow_duplicate=True),
    Output("last-page", "data", allow_duplicate=True),
    Input("open-timer", "n_intervals"), # ทำงานเมื่อนับเวลาครบ (ปกเปิดเสร็จ)
    prevent_initial_call=True,
)
def go_home(n_intervals):
    # Dash เรียก callback นี้ทันทีที่หน้า Login โผล่ (n_intervals ยังเป็น None) → ยังไม่ได้ใส่รหัส ห้ามเข้า
    if not n_intervals:
        return no_update, no_update, no_update
    # last=None → หน้า Home แบบไม่พลิกกระดาษ (ต่อจากตอนเปิดปก)
    return "/journal", home_layout(None), "/journal"



# ---------- 6.3 เลือกหน้าที่จะแสดงตาม URL ----------
@app.callback(
    Output("page", "children"),         # ใส่ layout ของหน้าลงใน Div "page"
    Output("last-page", "data"),        # จำหน้าที่เปิดอยู่ไว้ เป็น "หน้าก่อนหน้า" ของรอบถัดไป
    Input("url", "pathname"),           # ทำงานทุกครั้งที่ URL เปลี่ยน
    State("logged-in", "data"),         # ตรวจว่า login แล้วหรือยัง
    State("last-page", "data"),         # หน้าก่อนหน้า
    State("last-query", "data"),        # คำค้นหาล่าสุด (ใส่กลับในช่องค้นหาหน้า Search)
)
def show_page(pathname, logged_in, last_page, last_query):
    # เข้าหน้าในสมุดได้เฉพาะเมื่อ login แล้ว (กระดาษพลิกไปทางไหนขึ้นกับหน้าก่อนหน้า)
    if logged_in:
        if pathname == "/journal":
            return home_layout(last_page), pathname
        if pathname == "/search":
            return search_layout(last_page, last_query), pathname
        if pathname and pathname.startswith("/movie/") and pathname.removeprefix("/movie/").isdigit():
            return movie_layout(int(pathname.removeprefix("/movie/")), last_page), pathname
        if pathname == WISHLIST_PATH:
            return wishlist_layout(last_page), pathname
        if journal_page_number(pathname) and journal_page_number(pathname) <= journal_page_count():
            return journal_spread_layout(last_page, journal_page_number(pathname)), pathname

    # กรณีอื่นทั้งหมด → กลับไปหน้า Login
    # จำหน้าก่อนหน้าเป็น "/login" → ใส่รหัสแล้วไปหน้า Home เสมอ แบบไม่พลิกกระดาษ (ต่อจากตอนเปิดปก)
    return login_layout, "/login"



# ============================================================
# Callbacks: หน้า Home
# ============================================================

# ---------- 6.2.0 การ์ดในซอง: กดการ์ด → เด้งออกมากลางจอ, กดพื้นหลัง → เก็บกลับ ----------
@app.callback(
    Output("id-modal", "className"),    # เพิ่ม class "open" → CSS ให้การ์ดเด้งออกมา
    Input("id-card-preview", "n_clicks"),
    Input("id-backdrop", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_id_card(open_clicks, close_clicks):
    # กดการ์ด → เปิด, กดพื้นหลัง → ปิด (ctx.triggered_id = สิ่งที่ถูกกด)
    return "id-modal open" if ctx.triggered_id == "id-card-preview" else "id-modal"



# ---------- 6.2.0.1 บันทึกข้อมูลบนการ์ด แล้วอัปเดตการ์ดในซองให้ตรงกัน ----------
@app.callback(
    Output("id-photo-upload", "children"),         # รูปบนการ์ดกลางจอ
    Output("id-preview-photo", "children"),        # รูปบนการ์ดในซอง
    *[Output(f"id-preview-{key}", "children") for key in PROFILE_FIELDS],   # ข้อความบนการ์ดในซอง
    Output("id-preview-sig", "children"),          # ลายเซ็น (ชื่อ) บนการ์ดในซอง
    Output("id-sig", "children"),                  # ลายเซ็น (ชื่อ) บนการ์ดกลางจอ
    Input("id-photo-upload", "contents"),          # รูปที่อัปโหลด (data URL แบบ base64)
    *[Input(f"id-{key}", "value") for key in PROFILE_FIELDS],   # ช่องข้อความทุกช่อง
    prevent_initial_call=True,
)
def update_profile(photo, *values):
    profile = load_profile()
    profile.update(zip(PROFILE_FIELDS, values))   # จับคู่ชื่อช่องกับค่าที่กรอก
    if ctx.triggered_id == "id-photo-upload" and photo:   # บันทึกรูปเฉพาะตอนอัปโหลด
        profile["photo"] = photo
    save_profile(profile)
    preview = photo_preview(profile.get("photo"))
    # ลำดับต้องตรงกับ Output ด้านบน
    return preview, preview, *values, profile.get("name", ""), profile.get("name", "")



# ---------- 6.2.0.6 หน้า Home: โปสการ์ดหนังในซอง — เปิด/ปิด, ค้นหา, เลือกหนัง ----------
@app.callback(
    Output("mpc-modal", "className"),    # เพิ่ม class "open" → โปสการ์ดเด้งกลางจอ
    Input("mpc-preview", "n_clicks"),    # กดโปสการ์ดในซอง
    Input("mpc-backdrop", "n_clicks"),   # กดพื้นหลังมืด
    prevent_initial_call=True,
)
def toggle_postcard(open_clicks, close_clicks):
    return "id-modal open" if ctx.triggered_id == "mpc-preview" else "id-modal"


@app.callback(
    Output("mpc-results", "children"),     # ผลค้นหา
    Input("mpc-search-btn", "n_clicks"),   # กดแว่นขยาย
    Input("mpc-search", "value"),          # กด Enter (debounce)
    prevent_initial_call=True,
)
def search_postcard(n_clicks, title):
    """ผลค้นหา 6 เรื่องแรก เป็นโปสเตอร์เล็ก ๆ ให้กดเลือก"""
    if not title:   # ช่องว่าง → ไม่แสดง
        return None
    try:
        # เอาเฉพาะเรื่องที่มีโปสเตอร์
        movies = [m for m in tmdb_get("/search/movie", query=title)["results"] if m.get("poster_path")][:6]
    except requests.RequestException:   # เน็ตหลุด
        return html.Div("Couldn't reach TMDB.", className="mpc-note")
    if not movies:
        return html.Div("No results found.", className="mpc-note")
    # ปุ่มละ 1 เรื่อง: id มีรหัสหนัง → pick_postcard รู้ว่าเลือกเรื่องไหน
    return [html.Button([html.Img(src=f"{TMDB_IMAGE}/w154{m['poster_path']}"),
                         html.Span(f"{m['title']} ({movie_year(m)})")],
                        id={"type": "mpc-pick", "index": m["id"]}, className="mpc-pick", title=m["title"])
            for m in movies]


@app.callback(
    Output("mpc-preview-img", "children"),                   # รูปโปสการ์ดในซอง
    Output("mpc-modal-img", "children"),                     # รูปโปสการ์ดกลางจอ
    Output("mpc-title", "children"),                         # ชื่อหนังใต้โปสการ์ด
    Input({"type": "mpc-pick", "index": ALL}, "n_clicks"),   # ปุ่มผลค้นหาทุกปุ่ม
    prevent_initial_call=True,
)
def pick_postcard(clicks):
    """กดโปสเตอร์ในผลค้นหา → บันทึกลง profile.json แล้วเปลี่ยนรูปบนโปสการ์ด (ทั้งในซองและกลางจอ)"""
    if not ctx.triggered_id or not any(clicks):   # ปุ่มเพิ่งโผล่ (ยังไม่ได้กด)
        return no_update, no_update, no_update
    movie = tmdb_get(f"/movie/{ctx.triggered_id['index']}")   # ดึงข้อมูลเต็มของเรื่องที่กด
    chosen = {"id": movie["id"], "title": movie["title"],
              "backdrop": movie.get("backdrop_path"), "poster": movie.get("poster_path")}
    profile = load_profile()
    profile["postcard"] = chosen
    save_profile(profile)
    image = postcard_image(chosen)
    return image, image, chosen["title"]



# ---------- 6.2.0.7 หน้า Home: กระดาษ This or That — เปิด/ปิด, วงคำตอบแล้วบันทึก ----------
@app.callback(
    Output("tot-modal", "className"),
    Input("tot-preview", "n_clicks"),
    Input("tot-backdrop", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_this_or_that(open_clicks, close_clicks):
    # กดแผ่นในซอง → เปิด, กดพื้นหลัง → ปิด
    return "id-modal open" if ctx.triggered_id == "tot-preview" else "id-modal"


@app.callback(
    Output({"type": "tot", "index": ALL, "pick": ALL}, "className"),
    Output({"type": "tot-prev", "index": ALL, "pick": ALL}, "className"),
    Input({"type": "tot", "index": ALL, "pick": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def save_this_or_that(clicks):
    """กดคำบนกระดาษกลางจอ → บันทึกลง profile.json แล้ววงคำนั้น (ทั้งแผ่นกลางจอและแผ่นในซอง)"""
    if not any(clicks):   # ยังไม่ได้กด
        return no_update, no_update
    profile = load_profile()
    picks = ((profile.get("this_or_that") or []) + [None] * len(THIS_OR_THAT))[:len(THIS_OR_THAT)]   # เติมให้ครบ 9 ข้อ
    picks[ctx.triggered_id["index"]] = ctx.triggered_id["pick"]   # ข้อที่กด = คำที่เลือก
    profile["this_or_that"] = picks
    save_profile(profile)
    classes = [tot_class(picks, i, pick) for i in range(len(THIS_OR_THAT)) for pick in (0, 1)]
    return classes, classes



# Callbacks: หน้า Search
# รายชื่อหนังเด้งลงมาระหว่างพิมพ์
@app.callback(
    Output("search-suggest", "children"),   # รายชื่อหนังที่เด้งลงมา
    Input("movie-title", "value"),          # พิมพ์
    Input("movie-title", "n_submit"),       # กด Enter → ซ่อน
    Input("search-button", "n_clicks"),     # กดแว่นขยาย → ซ่อน
    prevent_initial_call=True,
)
def live_suggest(title, n_submit, n_clicks):
    """ระหว่างพิมพ์ (2 ตัวอักษรขึ้นไป) → รายชื่อหนังเด้งลงมาใต้เส้น, กด Enter/แว่นขยาย → ซ่อนรายการ"""
    # ไม่ได้มาจากการพิมพ์ / กด Enter / พิมพ์ไม่ถึง 2 ตัว → ซ่อน
    if ctx.triggered_id != "movie-title" or ctx.triggered_prop_ids.get("movie-title.n_submit") or not title \
            or len(title.strip()) < 2:
        return None
    try:
        movies = suggest_movies(title.strip().lower())
    except requests.RequestException:   # เน็ตหลุด → ไม่แสดง
        return None
    if not movies:
        return html.Div("no movie found", className="cert-suggest-empty")
    return [dcc.Link([
        html.Img(src=f"{TMDB_IMAGE}/w92{m['poster_path']}", className="cert-suggest-img") if m.get("poster_path")
        else html.Div(className="cert-suggest-img cert-suggest-noimg"),
        html.Span(m["title"], className="cert-suggest-title"),
        html.Span(movie_year(m), className="cert-suggest-year"),
    ], href=f"/movie/{m['id']}", className="cert-suggest-row") for m in movies]


# ---------- ผลค้นหา 10 เรื่องบนกระดาษขวา (เปิดหน้ามาก็ทำงานเลย → หนังมาแรง) ----------
@app.callback(
    Output("movie-info", "children"),   # รายการหนังแบบ playlist (กระดาษขวา)
    Output("last-query", "data"),       # จำคำค้นหาไว้ (กลับมาจากหน้ารายละเอียดหนังแล้วผลยังอยู่)
    Input("search-button", "n_clicks"), # ทำงานเมื่อกดปุ่มค้นหา
    Input("movie-title", "n_submit"),   # หรือกด Enter ในช่องค้นหา
    State("movie-title", "value"),      # อ่านชื่อหนังจากช่องค้นหา (ไม่ trigger เอง)
)
def search_movie(n_clicks, n_submit, title):
    try:
        if title:   # มีคำค้น → ค้นจากชื่อ
            movies = tmdb_get("/search/movie", query=title)["results"]
            heading = f'Results for "{title}"'
        else:
            # ยังไม่ได้พิมพ์ (เช่นตอนเพิ่งเปิดหน้า) → หนังมาแรงสัปดาห์นี้
            movies = tmdb_get("/trending/movie/week")["results"]
            heading = "Trending this week"
    except requests.RequestException:
        return html.P("Couldn't reach TMDB. Please try again.", className="search-message"), title

    # ไม่พบหนังที่ค้นหา
    if not movies:
        return html.P("No results found.", className="search-message"), title

    # หัวข้อ + 10 เรื่องแรก (เลขเริ่ม 01)
    return [
        html.H3(heading, className="results-heading"),
        html.Div([movie_row(i, m) for i, m in enumerate(movies[:10], start=1)], className="movie-list"),
    ], title


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ---------- ส่วนที่เหลือ (Movie detail / My Journal / Watchlist / Calendar) — ไว้ก่อน ----------
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------


# ============================================================
# ไฟล์ข้อมูล journal / watchlist + ตั๋วรีวิว
# ============================================================

JOURNAL_FILE = Path(__file__).parent / "data" / "journal.json"


def load_journal():
    """หนังที่ดูแล้ว: [{"date": "2026-10-02", "title": ..., "rating": ..., "review": ...}, ...]"""
    if JOURNAL_FILE.exists():
        return json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
    return []


def write_json(path, data):
    """เขียนลงไฟล์ชั่วคราวก่อนแล้วค่อยสลับแทนไฟล์จริง → ไฟล์ไม่มีทางเสียครึ่ง ๆ กลาง ๆ"""
    path.parent.mkdir(exist_ok=True)   # data/ ไม่ขึ้น git → clone ไปใหม่ยังไม่มีโฟลเดอร์ ให้สร้างเอง
    tmp = path.with_suffix(".tmp")   # journal.json → journal.tmp
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)            # สลับไฟล์ทีเดียว


# อ่าน → แก้ → เขียน journal.json ทีละงาน (กดดาวแล้วคลิกออกจากช่องรีวิวพร้อมกัน → callback 2 ตัวทำงานพร้อมกัน)
DATA_LOCK = threading.Lock()


def save_journal(entries): # บันทึกลง data/journal.json
    write_json(JOURNAL_FILE, entries)


# รายการหนังที่อยากดู: [{"genre": ..., "title": ..., "queue": ลำดับ, "id": ...}, ...] (รูปแบบเดียวกับ app_v2.py)
WATCHLIST_FILE = Path(__file__).parent / "data" / "watchlist.json"


def load_watchlist(): # อ่านหนังที่อยากดู (ยังไม่มีไฟล์ → [])
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    return []


def save_watchlist(items): # บันทึกลง data/watchlist.json
    write_json(WATCHLIST_FILE, items)


def list_buttons(title):
    """ปุ่ม 2 ปุ่มในหน้ารายละเอียดหนัง (ข้อความ, ปิดปุ่มไหม) ตามสถานะปัจจุบันของหนังเรื่องนี้
    ปุ่ม "ดูแล้ว" กดได้เสมอ: บันทึกลง journal (ถ้ายังไม่มี) แล้วพลิกไปหน้า My Journal"""
    watched = any(e["title"] == title for e in load_journal())    # ดูแล้ว
    wanted = any(w["title"] == title for w in load_watchlist())   # อยากดู
    # (ข้อความปุ่ม Watchlist, ปิดปุ่มไหม, ข้อความปุ่มดูแล้ว)
    return (("✓ Watchlist" if wanted else "+ Watchlist"), wanted or watched,
            ("✓ Watched · see journal" if watched else "Mark as watched"))


# ส่วนประกอบที่ใช้ซ้ำ
def navbar(active):
    """สร้างแถบเมนูด้านบน โดยไฮไลต์ลิงก์ของหน้าที่กำลังเปิดอยู่ (active)"""

    def link(label, href):
        # ถ้าเป็นหน้าปัจจุบัน ให้เพิ่ม class "active" เพื่อให้ CSS ไฮไลต์
        cls = "nav-link active" if href == active else "nav-link"
        return dcc.Link(label, href=href, className=cls)

    return html.Div([
        # ลิงก์ไปยังหน้าต่าง ๆ
        html.Div([
            link("Home", "/journal"),
            link("Library", "/library"),
            link("Challenge", "/challenge"),
            link("Calendar", "/calendar"),
        ], className="nav-links"),

        # ช่องค้นหาหนัง + ปุ่มค้นหา
        html.Div([
            dcc.Input(id="movie-title", type="text", placeholder="Search movie"),
            html.Button("🔍", id="search-button"),
        ], className="nav-search"),

        # ปุ่มไปหน้า Journal ส่วนตัว
        dcc.Link("Journal", href="/my-journal", className="nav-button"),
    ], className="navbar")



# ============================================================
# หน้ารายละเอียดหนัง (/movie/<id>)
# ============================================================

def movie_layout(movie_id, last):
    """หน้ารายละเอียดหนัง (กดการ์ดหนังในหน้า Search → พลิกหน้าต่อมาหน้านี้)
    กระดาษสองฝั่ง (Figma: Book → คู่ที่ 2 movie_left_page_b / movie_right_page_b):
                  ซ้าย = movie_left_page.jpg + โปสเตอร์ในซองพลาสติก + ปุ่ม
                  ขวา = movie_right_page.jpg
                        โค้ดเติมเฉพาะข้อมูลจาก TMDB ลงช่องในกระดาษชมพู"""
    try:
        movie = tmdb_get(f"/movie/{movie_id}", append_to_response="credits")   # ได้ผู้กำกับ/นักแสดงมาด้วยในครั้งเดียว
    except requests.RequestException:   # โหลดไม่ได้ → แจ้ง error
        return page(f"/movie/{movie_id}", html.P("Couldn't load this movie. Please try again.", className="search-message"),
                    left=[dcc.Link("← Back to search", href="/search", className="detail-back")], last=last)

    poster = movie.get("poster_path")
    runtime = movie.get("runtime") or 0                                # ความยาว (นาที)
    want_text, want_off, watched_text = list_buttons(movie["title"])   # สถานะปุ่ม
    credits = movie.get("credits", {})
    directors = [c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"]   # ผู้กำกับ
    cast = [c["name"] for c in credits.get("cast", [])[:4]]                                    # นักแสดง 4 คนแรก

    def rating_stars(vote):
        # คะแนน 0–10 → ดาว 5 ดวง (ปัดเป็นดวงเต็ม) เช่น 7.4 → ★★★★☆
        full = round(vote / 2)
        return "★" * full + "☆" * (5 - full)

    def value(text, cls):
        # ข้อมูลลายมือวางทับช่องในรูป (ตำแหน่งตาม Figma อยู่ใน CSS .mrp-<ชื่อช่อง>)
        return html.Div(text or "—", className=f"mrp-val {cls}")

    return page(
        f"/movie/{movie_id}",
        html.Div([
            # กระดาษชมพู + หัวข้อช่อง + ครั่ง + กระต่าย + ดาว + เครดิต TMDB = รูปเดียวจาก Figma
            html.Img(src=app.get_asset_url("movie_right_page.jpg"), className="search-page-art"),
            value(movie["title"], "mrp-title"),
            value(movie_year(movie), "mrp-year"),
            value(", ".join(directors), "mrp-director"),
            value(f"{runtime // 60}h {runtime % 60}m" if runtime else None, "mrp-runtime"),   # 125 → "2h 5m"
            value(", ".join(cast), "mrp-starring"),
            value(movie.get("overview") or "No plot available", "mrp-synopsis"),
            html.Div([
                " / ".join(g["name"] for g in movie.get("genres", [])[:2]),
                html.Span(rating_stars(movie.get("vote_average", 0)), className="mrp-stars"),
            ], className="mrp-val mrp-genre"),
        ], className="mrp"),
        left=html.Div([
            # พื้นฟ้า + ดาวเงิน + เงาสันสมุด = รูปเดียวจาก Figma (Book → movie_left_page_b)
            html.Img(src=app.get_asset_url("movie_left_page.jpg"), className="search-page-art"),
            dcc.Link("← Back to search", href="/search", className="mlp-back"),
            # ชื่อหนังตัวปั๊มนูนบรรทัดเดียว: ชื่อยาว → ย่อฟอนต์ให้กว้างไม่เกิน ~620px (ตัวละ ≈ 0.62em + ระยะห่าง 3px)
            html.H2(movie["title"].upper(), className="mlp-title",
                    style={"--fs": round(min(56, (620 / max(len(movie["title"]), 1) - 3) / 0.62), 1)}),
            html.Img(src=f"{TMDB_IMAGE}/w500{poster}", className="mlp-poster") if poster
            else html.Div(className="mlp-poster mlp-poster-empty"),
            # ซองพลาสติกใสครอบโปสเตอร์ (Figma: plastic wrap overlay)
            html.Img(src=app.get_asset_url("movie_plastic_wrap.png"), className="mlp-wrap"),
            html.A("▶  Watch trailer", href=f"/trailer/{movie_id}", target="_blank", className="mlp-trailer"),
            # เพิ่มเข้า "อยากดู" (watchlist.json) / "ดูแล้ว" (journal.json — วันที่ = วันนี้) → พลิกไปหน้า My Journal
            dcc.Store(id="movie-data", data={"title": movie["title"], "tmdb_id": movie_id, "poster": poster,
                                            "backdrop": movie.get("backdrop_path"),
                                            "genre": (movie.get("genres") or [{"name": ""}])[0]["name"]}),
            html.Div([
                html.Button(want_text, id="add-want", disabled=want_off, className="detail-list-btn"),
                html.Button(watched_text, id="add-watched", className="detail-list-btn"),
            ], className="detail-list-btns"),
        ], className="mlp"),
        last=last,
    )


@lru_cache(maxsize=256)
def tmdb_lookup(title):
    """ค้นหนังจากชื่อใน TMDB ครั้งเดียวแล้วจำไว้ → (id, poster_path, backdrop_path) ไม่เจอ → (None, None, None)"""
    try:
        results = tmdb_get("/search/movie", query=title)["results"]
    except requests.RequestException:
        return None, None, None
    if not results:
        return None, None, None
    return results[0]["id"], results[0].get("poster_path"), results[0].get("backdrop_path")



# ============================================================
# My Journal (/my-journal = Wishlist, /my-journal/p1, p2.. = รีวิว)
# ============================================================

# ตำแหน่งรีวิว 6 ช่องตาม Figma (Book → journal_spread, หน้าละ 828px): (ซ้าย/บน/กว้าง ของตัวหนังสือ), (ซ้าย/บน/กว้าง/สูง/เอียง ของรูป)
JOURNAL_SLOTS = [
    # หน้าซ้าย (เรื่องที่ 1–3): ตัวหนังสือกับรูปสลับซ้าย-ขวากันไปทีละแถว
    ((70, 110, 360), (470, 100, 280, 190, 3)),      # แถวบน: ตัวหนังสือซ้าย รูปขวา
    ((370, 440, 380), (80, 450, 240, 170, -3)),     # แถวกลาง: รูปซ้าย ตัวหนังสือขวา
    ((70, 790, 380), (480, 800, 270, 180, 2.5)),    # แถวล่าง: ตัวหนังสือซ้าย รูปขวา
    # หน้าขวา (เรื่องที่ 4–6): สลับฝั่งกับหน้าซ้าย
    ((442, 130, 330), (82, 110, 320, 210, -2)),     # แถวบน: รูปซ้าย ตัวหนังสือขวา
    ((82, 450, 330), (452, 440, 290, 200, 3)),      # แถวกลาง: ตัวหนังสือซ้าย รูปขวา
    ((442, 800, 330), (82, 790, 320, 210, 2.5)),    # แถวล่าง: รูปซ้าย ตัวหนังสือขวา
]   # ค่าเอียงเป็นองศา (+ = หมุนตามเข็ม) ให้รูปดูเหมือนแปะเทปเบี้ยว ๆ


JOURNAL_PER_PAGE = 6   # รีวิวต่อหน้าคู่ (ซ้าย 3 ขวา 3)


def journal_entries():
    """บันทึกทั้งหมดเรียงตามวันที่ดู (วันเดียวกัน → ตามลำดับที่เพิ่ม)"""
    return sorted(load_journal(), key=lambda e: e.get("date", ""))


def journal_page_count(): # จำนวนหน้าคู่รีวิว (หน้าละ 6 ปัดขึ้น, อย่างน้อย 1)
    # -(-a // b) = หารปัดขึ้น เช่น 7 เรื่อง → -(-7 // 6) = -(-2) = 2 หน้า
    # max(1, ...) → ยังไม่มีบันทึกเลยก็ยังมีหน้าคู่ว่าง 1 หน้า
    return max(1, -(-len(journal_entries()) // JOURNAL_PER_PAGE))


def journal_card(entry, slot):
    """รีวิว 1 เรื่องในหน้าคู่ My Journal: วันที่ · ชื่อหนัง (ลายมือแดงอิฐ) + ดาว + รีวิว + รูปฉากหนังแปะเทป
    วางตามช่องที่ slot ใน JOURNAL_SLOTS (หน่วย px ของ Figma → CSS คูณ --u)
    ดาวกับรีวิวเขียน/แก้ได้ตรงหน้ากระดาษเลย (บันทึกเองตอนกดดาว / คลิกออกจากช่องรีวิว)"""
    backdrop = entry.get("backdrop") or tmdb_lookup(entry["title"])[2]
    d = date.fromisoformat(entry["date"]) if entry.get("date") else None
    # ชื่อหนังเป็นก้อนเดียว (inline-block): ยาวไม่พอดีบรรทัดวันที่ → ลงไปขึ้นบรรทัดใหม่ทั้งชื่อ
    # และซ่อนจุดคั่นหน้าชื่อ (assets/journal.js ใส่ class "jr-wrapped" ให้ตอนชื่อขึ้นบรรทัดใหม่)
    title = html.Span([html.Span("·  ", className="jr-sep") if d else None, entry["title"]], className="jr-movie")
    heading = [html.Span(f"{d.day} {d:%B %Y}  ", className="jr-date"), title] if d else [title]
    key = entry.get("id") or entry["title"]   # ตัวระบุบันทึก (ไม่มี id → ใช้ชื่อหนัง)
    rating = entry.get("rating") or 0
    (tx, ty, tw), (px, py, pw, ph, rot) = JOURNAL_SLOTS[slot]   # ตำแหน่งตัวหนังสือ + รูปของช่องนี้
    return [
        html.Div([
            html.Div(heading, className="jr-head"),
            # คะแนนครึ่งดาว (เช่น 3.5) → เลือกดาวเต็ม 3 ดวง + ดวงถัดไปแสดงครึ่งดวง (class jr-half)
            dcc.RadioItems(id={"type": "jr-rating", "index": key},
                           options=[{"label": "★", "value": i} for i in range(1, 6)],
                           value=int(rating) or None, inline=True,
                           className="jr-stars jr-half" if rating % 1 else "jr-stars"),
            dcc.Textarea(id={"type": "jr-review", "index": key}, value=entry.get("review") or "",
                         placeholder="write your review…", className="jr-review"),
        ], className="jr-text", style={"--x": tx, "--y": ty, "--w": tw}),
        # กดรูป → เลือกฉากที่ชอบจาก TMDB มาแปะแทน
        html.Div(html.Img(id={"type": "jr-photo-img", "index": key},
                          src=f"{TMDB_IMAGE}/w500{backdrop}" if backdrop else ""),
                 id={"type": "jr-photo", "index": key}, n_clicks=0, title="pick your favorite scene",
                 className="jr-photo", style={"--x": px, "--y": py, "--w": pw, "--h": ph, "--r": f"{rot}deg"}),
    ]


def journal_spread_layout(last, n=1):
    """หน้าคู่รีวิวหน้าที่ n (/my-journal/p1, p2, ...): ซ้าย 3 เรื่อง ขวา 3 เรื่อง + หน้าต่างเลือกฉากหนัง
    last = path หน้าก่อนหน้า (ใช้กำหนดทิศพลิกหน้า)"""
    active = journal_page_path(n)
    # ตัดเอาเฉพาะ 6 เรื่องของหน้านี้ เช่น n=2 → เรื่องที่ index 6–11
    entries = journal_entries()[(n - 1) * JOURNAL_PER_PAGE:n * JOURNAL_PER_PAGE]
    if not entries:
        return page(active, html.P("Mark a movie as watched to start your journal.", className="je-empty"),
                    left=[], last=last)
    cards = [journal_card(e, i) for i, e in enumerate(entries)]
    # เรื่องที่ 1–3 = ซ้าย, 4–6 = ขวา (sum(..., []) ต่อ list ให้เป็นแถวเดียว)
    return page(active, html.Div(sum(cards[3:], []), className="jr"),
                left=[html.Div(sum(cards[:3], []), className="jr")], last=last,
                # หน้าต่างเลือกฉากหนัง (เด้งขึ้นกลางจอตอนกดรูป) — กดพื้นหลังมืดเพื่อปิด
                overlay=html.Div([
                    html.Div(id="scene-backdrop", n_clicks=0, className="id-backdrop"),
                    html.Div([
                        html.Div(id="scene-title", className="scene-title"),
                        html.Div(id="scene-grid", className="scene-grid"),
                    ], className="scene-sheet"),
                    dcc.Store(id="scene-key"),
                ], id="scene-modal", className="id-modal"))


# ---------- Wishlist (/my-journal = หน้าแรกของ My Journal): หนังที่อยากดู 6 เรื่องล่าสุด ----------
# หน้าซ้าย = ตะกร้า (พื้น wishlist_basket_page.jpg จาก Figma: Book → wishlist_basket_page)
#   โค้ดวางโปสเตอร์เอียงบนตะกร้า + วงกลมเลข 01–06 + คำบรรยายรายชื่อด้านล่าง
# หน้าขวา = ว่างไว้ก่อน
WISHLIST_PATH = "/my-journal"
WISHLIST_MAX = 6
# โปสเตอร์บนตะกร้า (ซ้าย, บน, กว้าง, สูง, องศาหมุนแบบ Figma) — x,y = มุมบนซ้ายของโปสเตอร์หลังหมุน (หน้ากว้าง 828)
BASKET_SLOTS = [
    (144.3, 523.2, 143.5, 208.1, -8), (342.7, 509.7, 143.5, 208.1, 6), (541.2, 527.7, 143.5, 208.1, 11),
    (166.9, 708.1, 143.5, 208.1, 9), (360.8, 717.2, 143.5, 208.1, -5), (550.2, 703.6, 143.5, 208.1, -11),
]
BASKET_NUMBERS = [(268, 529.4), (467, 480.9), (663.6, 486.5), (290.1, 671.9), (485.3, 715.8), (672.6, 717.2)]   # วงกลมเลข (ซ้าย, บน)


def basket_poster(item, slot):
    """โปสเตอร์เอียงบนตะกร้า + วงกลมเลข (กดโปสเตอร์แล้วไปหน้ารายละเอียดหนัง)
    ยังไม่เก็บโปสเตอร์ไว้ → ค้นจากชื่อใน TMDB"""
    movie_id, poster = item.get("tmdb_id"), item.get("poster")
    if not (movie_id and poster):
        movie_id, poster = tmdb_lookup(item["title"])[:2]
    x, y, w, h, rot = BASKET_SLOTS[slot]
    nx, ny = BASKET_NUMBERS[slot]
    img = html.Img(src=f"{TMDB_IMAGE}/w342{poster}", className="wb-poster", title=item["title"],
                   style={"--x": x, "--y": y, "--w": w, "--h": h, "--r": f"{-rot}deg"}) if poster else None
    if img is not None and movie_id:
        img = dcc.Link(img, href=f"/movie/{movie_id}")
    return [img, html.Div(f"{slot + 1:02d}", className="wb-num", style={"--x": nx, "--y": ny})]


def wishlist_layout(last):
    """Wishlist: ซ้าย = ตะกร้า 6 เรื่อง (เรียงตามลำดับที่กด + Watchlist), ขวา = ว่าง"""
    items = load_watchlist()[-WISHLIST_MAX:]
    caption = "; ".join(f"{i + 1:02d}: {item['title']}" for i, item in enumerate(items))
    left = html.Div([
        html.Img(src=app.get_asset_url("wishlist_basket_page.jpg"), className="search-page-art"),
        *sum((basket_poster(item, i) for i, item in enumerate(items)), []),
        html.P(caption, className="wb-caption") if caption else None,
    ], className="wl")
    return page(WISHLIST_PATH, left=[left], last=last)





# Callbacks: หน้ารายละเอียดหนัง + My Journal

# หน้ารายละเอียดหนัง: เพิ่มเข้า "อยากดู"
@app.callback(
    Output("add-want", "children"),   # ข้อความปุ่ม
    Output("add-want", "disabled"),   # ปิดปุ่ม
    Input("add-want", "n_clicks"),
    State("movie-data", "data"),      # ข้อมูลหนังเรื่องนี้
    prevent_initial_call=True,
)
def add_to_watchlist(want_clicks, movie):
    """เพิ่มหนังเข้า watchlist ต่อท้ายคิว แล้วเปลี่ยนปุ่มเป็น "เพิ่มแล้ว" (กดซ้ำไม่ได้)"""
    title = movie["title"]
    items = load_watchlist()
    if not any(w["title"] == title for w in items):   # กันเพิ่มซ้ำ
        items.append({"genre": movie["genre"], "title": title,
                      "queue": max((w.get("queue", 0) for w in items), default=0) + 1,   # คิวถัดไป
                      "id": uuid.uuid4().hex[:8],
                      "tmdb_id": movie.get("tmdb_id"), "poster": movie.get("poster"),
                      "backdrop": movie.get("backdrop")})
        save_watchlist(items)
    return list_buttons(title)[:2]   # (ข้อความ, disabled) ของปุ่มอยากดู ตามสถานะล่าสุด


# หน้ารายละเอียดหนัง: "ดูแล้ว" → บันทึกลง journal แล้วพลิกไปหน้า My Journal
@app.callback(
    Output("url", "pathname", allow_duplicate=True),
    Input("add-watched", "n_clicks"),
    State("movie-data", "data"),
    prevent_initial_call=True,
)
def mark_watched(n_clicks, movie):
    """date/title/rating/review  (rating 0 = ยังไม่ให้ดาว) — ดูแล้วอยู่แล้ว → แค่พลิกไปดู"""
    if not n_clicks:    # ปุ่มเพิ่งโผล่มาตอนเปิดหน้าหนัง (ยังไม่ได้กด) → ไม่ทำอะไร
        return no_update
    with DATA_LOCK:   # ล็อกระหว่างอ่าน-แก้-เขียน
        entries = load_journal()
        if not any(e["title"] == movie["title"] for e in entries):
            entries.append({"date": date.today().isoformat(), "title": movie["title"], "rating": 0, "review": "",
                            "id": uuid.uuid4().hex[:8], "tmdb_id": movie["tmdb_id"],
                            "poster": movie.get("poster"), "backdrop": movie.get("backdrop")})
            save_journal(entries)
    # ดูแล้ว → เอาออกจากรายการอยากดู
    items = load_watchlist()
    if any(w["title"] == movie["title"] for w in items):
        save_watchlist([w for w in items if w["title"] != movie["title"]])
    # พลิกไปหน้าคู่ที่มีหนังเรื่องนี้ แล้วเขียนรีวิวต่อตรงนั้น
    i = next(i for i, e in enumerate(journal_entries()) if e["title"] == movie["title"])
    return journal_page_path(i // JOURNAL_PER_PAGE + 1)


# My Journal: กดรูป → เลือกฉากที่ชอบจาก TMDB
@app.callback(
    Output("scene-modal", "className"),                      # เปิด/ปิดหน้าต่าง
    Output("scene-title", "children"),                       # หัวข้อ
    Output("scene-grid", "children"),                        # รูปฉากให้เลือก
    Output("scene-key", "data"),                             # จำว่าเลือกให้เรื่องไหน
    Input({"type": "jr-photo", "index": ALL}, "n_clicks"),   # รูปทุกใบบนหน้าคู่
    Input("scene-backdrop", "n_clicks"),                     # พื้นหลังมืด
    prevent_initial_call=True,
)
def open_scene_picker(photo_clicks, backdrop_clicks):
    """กดรูปบนหน้าคู่ → เปิดหน้าต่างพร้อมฉากหนังเรื่องนั้นจาก TMDB / กดพื้นหลังมืด → ปิด
    ("id-modal open" = แสดง, "id-modal" = ซ่อน — ควบคุมด้วย CSS)"""
    # ctx.triggered[0]["value"] = n_clicks ของตัวที่กด: 0 = แค่ถูกสร้างใหม่ตอนเปลี่ยนหน้า ไม่ได้กดจริง
    if ctx.triggered_id == "scene-backdrop" or not ctx.triggered or not ctx.triggered[0]["value"]:   # กดพื้นหลัง / ยังไม่ได้กด → ปิด
        return "id-modal", no_update, no_update, no_update
    key = ctx.triggered_id["index"]   # รูปของเรื่องไหน
    entry = next((e for e in load_journal() if (e.get("id") or e["title"]) == key), None)
    if entry is None:
        return no_update, no_update, no_update, no_update
    movie_id = entry.get("tmdb_id") or tmdb_lookup(entry["title"])[0]
    try:
        # include_image_language="null" → เฉพาะภาพไม่มีตัวหนังสือ, สูงสุด 24 ภาพ
        backdrops = [b["file_path"] for b in tmdb_get(f"/{entry.get('media', 'movie')}/{movie_id}/images",
                                                     include_image_language="null")["backdrops"]][:24]
    except requests.RequestException:
        backdrops = []
    # รูปที่ใช้อยู่ = มีกรอบไฮไลต์ (scene-thumb-on)
    grid = [html.Img(id={"type": "scene-pick", "index": path}, n_clicks=0, src=f"{TMDB_IMAGE}/w300{path}",
                     className="scene-thumb" + (" scene-thumb-on" if path == entry.get("backdrop") else ""))
            for path in backdrops] or [html.P("No scenes found for this movie.", className="scene-empty")]
    return "id-modal open", f"pick your favorite scene · {entry['title']}", grid, key


# My Journal: กดเลือกฉากในหน้าต่าง → ใช้เป็นรูปของรีวิวนั้น
# ใช้ ALL เพราะรูปบนหน้าคู่มีหลายใบ (id แบบ dict) — ต้องส่งค่ากลับครบทุกใบ ใบที่ไม่เปลี่ยน = no_update
@app.callback(
    Output({"type": "jr-photo-img", "index": ALL}, "src"),       # รูปบนหน้าคู่
    Output("scene-modal", "className", allow_duplicate=True),   # ปิดหน้าต่าง
    Input({"type": "scene-pick", "index": ALL}, "n_clicks"),     # รูปฉากที่กดเลือก
    State("scene-key", "data"),                                  # เรื่องที่กำลังเลือกให้
    State({"type": "jr-photo-img", "index": ALL}, "id"),         # id รูปทุกใบ
    prevent_initial_call=True,
)
def pick_scene(pick_clicks, key, img_ids):
    """เลือกฉากแล้ว: บันทึกเป็นรูปของบันทึกนั้น + เปลี่ยนรูปบนหน้ากระดาษ + ปิดหน้าต่าง"""
    if not ctx.triggered or not ctx.triggered[0]["value"] or not key:   # ยังไม่ได้กด
        return [no_update] * len(img_ids), no_update
    path = ctx.triggered_id["index"]   # รูปที่เลือก
    update_entry(key, backdrop=path)
    # เปลี่ยนเฉพาะรูปของเรื่องนี้
    return ([f"{TMDB_IMAGE}/w500{path}" if i["index"] == key else no_update for i in img_ids],
            "id-modal")


# My Journal: ให้ดาว / เขียนรีวิวตรงหน้ากระดาษ → บันทึกลง journal.json
def update_entry(key, **fields): # แก้บันทึก 1 เรื่อง เช่น rating=3, review="good", backdrop="/abc.jpg"
    with DATA_LOCK:   # กัน callback หลายตัวเขียนไฟล์ชนกัน
        entries = load_journal()
        for e in entries:
            if (e.get("id") or e["title"]) == key:
                e.update(fields)   # แก้เฉพาะช่องที่ส่งมา
        save_journal(entries)


# MATCH = จับคู่ Input/Output ที่ index เดียวกัน → กดดาวของเรื่องไหน ก็แก้แค่ดาวของเรื่องนั้น
@app.callback(
    Output({"type": "jr-rating", "index": MATCH}, "className"),   # กดดาวแล้ว = ดาวเต็มดวง (ไม่มีครึ่งดวงแล้ว)
    Input({"type": "jr-rating", "index": MATCH}, "value"),
    prevent_initial_call=True,
)
def save_rating(rating): # กดดาว → บันทึกทันที
    update_entry(ctx.triggered_id["index"], rating=rating or 0)
    return "jr-stars"


@app.callback(
    Input({"type": "jr-review", "index": MATCH}, "n_blur"),   # คลิกออกจากช่องรีวิว = บันทึก
    State({"type": "jr-review", "index": MATCH}, "value"),
    prevent_initial_call=True,
)
def save_review(n_blur, review): # คลิกออกจากช่องรีวิว → บันทึก (ไม่มี Output)
    if n_blur:
        update_entry(ctx.triggered_id["index"], review=review or "", reviewed=True)   # reviewed → ขึ้นในปฏิทิน


# ตัว Flask ที่อยู่ข้างใน Dash — บน Render ใช้รันด้วย gunicorn ("gunicorn app:server")
server = app.server

# รันแอปในเครื่อง (python app.py) — บน Render ไม่ได้รันบรรทัดนี้
if __name__ == "__main__":
    app.run(debug=True)