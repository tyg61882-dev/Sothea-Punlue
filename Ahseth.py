import asyncio
import logging
import time
import io
import os
import json
import re
import qrcode
import aiohttp
from datetime import datetime
from typing import Any, Dict, Optional, List, Tuple
from functools import wraps

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, LinkPreviewOptions,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramRetryAfter,
    TelegramForbiddenError,
    TelegramBadRequest,
)

# ====================================================================================================
# [ CONFIGURATION ]
# ====================================================================================================

BOT_TOKEN = "9038599787:AAGCPEwqz1TmOWra5H2-czOXkfgX2lnJuZo"
OWNER_ID  = 8371081776

KHQR_API_KEY    = "sk_98c99cdfff01a78707b83590dd181d709c04869a721ce304"
KHQR_CREATE_URL = "https://khqrpay.site/api/v1/aba/create"
KHQR_CHECK_URL  = "https://khqrpay.site/api/v1/payment"

DB_JSON_PATH = "AIDAA.json"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)
logger = logging.getLogger("BSLOUN")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

background_tasks = set()

def run_bg_task(coro):
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


# ====================================================================================================
# [ ⚡ SMOOTH CALLBACK HELPERS ]
# ====================================================================================================

async def quick_answer(call: CallbackQuery, text: Optional[str] = None, show_alert: bool = False):
    """Answer callback ភ្លាមៗ — ធ្វើឱ្យ button ចុចរលូន គ្មាន spinner យឺត"""
    try:
        await bot.answer_callback_query(
            callback_query_id=call.id,
            text=text,
            show_alert=show_alert,
        )
    except TelegramBadRequest:
        pass
    except Exception:
        pass


def auto_answer(text: Optional[str] = None, alert: bool = False):
    """Decorator: answer callback ភ្លាមៗមុន handler"""
    def decorator(handler):
        @wraps(handler)
        async def wrapper(call: CallbackQuery, *args, **kwargs):
            await quick_answer(call, text, alert)
            return await handler(call, *args, **kwargs)
        return wrapper
    return decorator


async def safe_edit_or_send(
    call: CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    disable_preview: bool = True
):
    """Edit message បើអាច — បើមិនបាន delete+send (smooth)"""
    try:
        await call.message.edit_text(
            text,
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=disable_preview)
        )
        return
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return
        if "message to edit not found" in err or "message can't be edited" in err:
            pass
    except Exception:
        pass

    try:
        await call.message.delete()
    except Exception:
        pass
    try:
        await bot.send_message(
            call.message.chat.id,
            text,
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=disable_preview)
        )
    except Exception as e:
        logger.error(f"safe_edit_or_send failed: {e}")


fast_answer = quick_answer

cancelled_payments = set()
_processed_payments: Dict[str, float] = {}

# 📌 តាមដាន QR Message សកម្មតាម User (uid -> (chat_id, msg_id, payment_id))
_active_user_qrs: Dict[int, Tuple[int, int, str]] = {}

# ✅ Premium Icon IDs
ICON_CREATE_PRODUCT = "5904258298764334001"
ICON_DELETE_PRODUCT = "6039404727542747508"
ICON_ADD_PIC_VIDEO  = "6039451237743595514"
ICON_DEL_PIC_VIDEO  = "6039779802741739617"
ICON_ADD_PLAN       = "5944753741512052670"
ICON_DEL_PLAN       = "6028205772117118673"
ICON_ADD_FILE_LINK  = "6039522349517115015"
ICON_DEL_FILE_LINK  = "5983580310292402968"
ICON_BACK           = "5447506720316225765"  # ✅ Back Button Icon
ICON_BUY_NOW        = "5447319442562251569"
ICON_ADMIN_PANEL    = "5445372439397691719"
ICON_HELLO          = "6129863473305230077"
ICON_STORE          = "6039451237743595514"

# 👑 Admin Icons
ICON_ADD_ADMIN      = "5985596818912712352"
ICON_DEL_ADMIN      = "5985346521103604145"
ICON_VIEW_ADMINS    = "5985346521103604145"

# 🛠️ Maintenance Icons
ICON_MAINT_TITLE    = "6021401276904905698"
ICON_MAINT_STATUS   = "6195074326832619538"
ICON_MAINT_OPEN     = "5215685881989442149"
ICON_MAINT_CLOSE    = "5215313353706057331"

# 📦 Stock Plan Icons
ICON_STOCK_PLAN     = "6024106569430472546"
ICON_STK_PRODUCT    = "6017174676898321263"
ICON_STK_PLAN       = "5447187153274567373"
ICON_STK_PRICE      = "6030352469786105758"
ICON_STK_RESELLER   = "5886763041541853781"
ICON_STK_STOCK      = "5931472654660800739"

# 👥 Reseller List Icons
ICON_RES_TITLE       = "6021690418398239007"
ICON_RES_ID          = "5884366771913233289"
ICON_RES_USER        = "6008118472066732010"
ICON_RES_ORDERS      = "6024106569430472546"
ICON_RES_PRICE       = "6030352469786105758"
ICON_RES_TOTAL_MONEY = "5444960062407732826"
ICON_REFRESH         = "5447282724886839705"

# 🎉 Delivery & Status Emojis
ICON_CREATING_KHQR    = "5386367538735104399"
ICON_BUY_SUCCESS      = "5312361253610475399"
ICON_KEY_LIST         = "6284816251143331422"
ICON_THANK_YOU        = "5285070644864628879"
ICON_INSTALL_LINK     = "5274099962655816924"
ICON_INSTALL_BTN      = "6147890838320979470"
ICON_PAY_CANCELLED    = "5445092669522996408"
ICON_STOCK_OUT        = "5445005936953424165"
ICON_QR_EXPIRED       = "5445350406215465190"

# 📢 Order Group Notification Emojis
ICON_GP_ORDER_NEW   = "5444860552310457690"
ICON_GP_PRODUCT     = "5447319442562251569"
ICON_GP_TOTAL_KEY   = "5447579253723918909"
ICON_GP_USERNAME    = "5447311106030726740"
ICON_GP_TIME_BUY    = "5445358884480916784"

# 🛒 Confirm Order / Quantity Emojis
ICON_CONFIRM_TITLE      = "5444860552310457690"
ICON_CONFIRM_PRODUCT    = "5447319442562251569"
ICON_CONFIRM_PLAN       = "5445033158456145975"
ICON_CONFIRM_UNITPRICE  = "5447579253723918909"
ICON_CONFIRM_STOCK      = "5445137435967126342"
ICON_CONFIRM_QTY        = "5447387942995655277"
ICON_CONFIRM_TOTAL      = "5447298551841322535"

# ⚙️ Setup Group Icons
ICON_SETUP_GROUP    = "5445059250382469069"
ICON_ADD_GROUP      = "5445372439397691719"
ICON_DEL_GROUP      = "5445092669522996408"
ICON_VIEW_GROUP     = "5444869180899752137"

# ====================================================================================================
# [ HTTP SESSION ]
# ====================================================================================================

KHQR_TIMEOUT = aiohttp.ClientTimeout(total=40, connect=15, sock_read=25)
_http_session: Optional[aiohttp.ClientSession] = None


def get_http() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=20,
            ttl_dns_cache=300,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
        )
        _http_session = aiohttp.ClientSession(
            timeout=KHQR_TIMEOUT, connector=connector
        )
    return _http_session


async def close_http():
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None

# ====================================================================================================
# [ JSON DATABASE ]
# ====================================================================================================

DEFAULT_DB = {
    "users": {},
    "categories": [],
    "items": [],
    "stock": [],
    "orders": [],
    "settings": {
        "contact_admin": "@BLACK_Khmer",
        "bot_name": "AIDAA MOD STORE",
        "maintenance": "0",
        "order_group_id": ""
    },
    "counters": {"categories": 0, "items": 0, "stock": 0, "orders": 0}
}

_db_cache: Optional[dict] = None
_item_index: Dict[int, dict] = {}
_cat_index:  Dict[int, dict] = {}
_stock_by_item: Dict[int, List[dict]] = {}


def _write_db_file(data: dict):
    tmp = DB_JSON_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DB_JSON_PATH)


def _rebuild_indexes(data: dict):
    _item_index.clear()
    _cat_index.clear()
    _stock_by_item.clear()
    for c in data.get("categories", []):
        _cat_index[c["id"]] = c
    for i in data.get("items", []):
        _item_index[i["id"]] = i
        _stock_by_item[i["id"]] = []
    for s in data.get("stock", []):
        if s["item_id"] in _stock_by_item:
            _stock_by_item[s["item_id"]].append(s)


def _load_db() -> dict:
    global _db_cache
    if _db_cache is not None:
        return _db_cache

    if not os.path.exists(DB_JSON_PATH):
        logger.info(f"📁 Creating new JSON DB: {DB_JSON_PATH}")
        data = json.loads(json.dumps(DEFAULT_DB))
        _write_db_file(data)
    else:
        try:
            with open(DB_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"❌ JSON DB corrupted: {e} — recreating")
            data = json.loads(json.dumps(DEFAULT_DB))
            _write_db_file(data)

    for k, v in DEFAULT_DB.items():
        if k not in data:
            data[k] = json.loads(json.dumps(v))

    for item in data.get("items", []):
        if "reseller_price" not in item:
            item["reseller_price"] = float(item.get("price", 0.0))

    _db_cache = data
    _rebuild_indexes(data)
    return _db_cache


def _save_db(data: dict):
    global _db_cache
    _db_cache = data
    _rebuild_indexes(data)
    try:
        _write_db_file(data)
    except Exception as e:
        logger.error(f"❌ DB save failed: {e}")


def _next_id(data: dict, table: str) -> int:
    data["counters"][table] = data["counters"].get(table, 0) + 1
    return data["counters"][table]


def init_db():
    data = _load_db()
    owner_key = str(OWNER_ID)
    if owner_key not in data["users"]:
        data["users"][owner_key] = {
            "is_admin": 1, "is_reseller": 0,
            "first_name": "Owner", "username": "",
            "total_orders": 0, "total_spent": 0.0
        }
    else:
        data["users"][owner_key]["is_admin"] = 1
    _save_db(data)
    logger.info(f"✅ JSON DB ready: {DB_JSON_PATH} (items={len(data['items'])}, stock={len(data['stock'])})")


def db_get_user(uid: int) -> dict:
    return _load_db()["users"].get(str(uid), {})


def db_upsert_user(uid: int, **fields):
    data = _load_db()
    key = str(uid)
    if key not in data["users"]:
        data["users"][key] = {
            "is_admin": 0, "is_reseller": 0,
            "first_name": "", "username": "",
            "total_orders": 0, "total_spent": 0.0
        }
    data["users"][key].update(fields)
    _save_db(data)


def db_all_users():
    return list(_load_db()["users"].keys())


def is_user_reseller(uid: int) -> bool:
    u = db_get_user(uid)
    val = u.get("is_reseller", 0)
    return bool(val) or str(val).strip().lower() in ("1", "true")


def get_item_price(item: dict, is_reseller: bool) -> float:
    if not item:
        return 0.0
    if is_reseller:
        res_p = item.get("reseller_price")
        if res_p is not None:
            try:
                return float(res_p)
            except (ValueError, TypeError):
                pass
    return float(item.get("price", 0.0))


def db_get_resellers():
    data = _load_db()
    resellers = []
    for uid, u in data["users"].items():
        if is_user_reseller(int(uid)):
            resellers.append({
                "id": uid,
                "first_name": u.get("first_name", "Unknown"),
                "username": u.get("username", ""),
                "total_orders": u.get("total_orders", 0),
                "total_spent": u.get("total_spent", 0.0),
            })
    resellers.sort(key=lambda x: x["total_spent"], reverse=True)
    return resellers


def db_get_admins():
    data = _load_db()
    admins = []
    for uid, u in data.get("users", {}).items():
        if int(uid) == OWNER_ID or bool(u.get("is_admin", 0)):
            admins.append({
                "id": uid,
                "first_name": u.get("first_name", "Unknown"),
                "username": u.get("username", ""),
                "is_owner": int(uid) == OWNER_ID
            })
    return admins


def db_set_setting(key: str, value: str):
    data = _load_db()
    data["settings"][key] = value
    _save_db(data)


def get_setting(key, default=None):
    return _load_db()["settings"].get(key, default)


def add_category(name: str, icon: str) -> int:
    data = _load_db()
    cid = _next_id(data, "categories")
    cat = {"id": cid, "name": name, "icon": icon, "media_data": None}
    data["categories"].append(cat)
    _save_db(data)
    return cid


def get_categories():
    return [(c["id"], c["name"], c.get("icon")) for c in _load_db()["categories"]]


def del_category(cid: int):
    data = _load_db()
    data["categories"] = [c for c in data["categories"] if c["id"] != cid]
    item_ids = [i["id"] for i in data["items"] if i["cat_id"] == cid]
    data["items"] = [i for i in data["items"] if i["cat_id"] != cid]
    data["stock"] = [s for s in data["stock"] if s["item_id"] not in item_ids]
    _save_db(data)


def set_category_media(cid: int, media):
    data = _load_db()
    for c in data["categories"]:
        if c["id"] == cid:
            c["media_data"] = media
            break
    _save_db(data)


def get_category(cid: int):
    return _cat_index.get(cid)


def add_item(cat_id, name, price, reseller_price, icon) -> int:
    data = _load_db()
    iid = _next_id(data, "items")
    item = {
        "id": iid, "cat_id": cat_id, "name": name,
        "price": float(price), "reseller_price": float(reseller_price),
        "icon": icon, "post_media": None
    }
    data["items"].append(item)
    _save_db(data)
    return iid


def update_item_prices(item_id: int, price: float, reseller_price: float):
    data = _load_db()
    for i in data.get("items", []):
        if i["id"] == item_id:
            i["price"] = float(price)
            i["reseller_price"] = float(reseller_price)
            break
    _save_db(data)


def get_items(cat_id: int):
    return [i for i in _load_db()["items"] if i["cat_id"] == cat_id]


def get_all_items():
    return _load_db()["items"]


def get_item(item_id: int):
    return _item_index.get(item_id)


def del_item(item_id: int):
    data = _load_db()
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    data["stock"] = [s for s in data["stock"] if s["item_id"] != item_id]
    _save_db(data)


def set_item_post_media(item_id: int, media):
    data = _load_db()
    for i in data["items"]:
        if i["id"] == item_id:
            i["post_media"] = media
            break
    _save_db(data)


def add_stock_row(item_id: int, key_data: str, stype: str) -> int:
    return add_stock_rows_bulk(item_id, [(key_data, stype)])


def add_stock_rows_bulk(item_id: int, rows: List[Tuple[str, str]]) -> int:
    data = _load_db()
    n = 0
    for key_data, stype in rows:
        sid = _next_id(data, "stock")
        data["stock"].append({
            "id": sid, "item_id": item_id, "key_data": key_data,
            "is_sold": 0, "type": stype, "sold_to": None, "sold_at": None
        })
        n += 1
    if n:
        _save_db(data)
    return n


def count_stock(item_id: int) -> int:
    bucket = _stock_by_item.get(item_id)
    if not bucket:
        return 0
    return sum(1 for s in bucket if s["is_sold"] == 0)


def get_stock_list(item_id: int):
    bucket = _stock_by_item.get(item_id) or []
    rows = [s for s in bucket if s["is_sold"] == 0]
    rows.sort(key=lambda x: x["id"])
    return [(s["id"], s["key_data"], s["type"]) for s in rows]


def del_stock_row(stock_id: int):
    data = _load_db()
    target = None
    for s in data["stock"]:
        if s["id"] == stock_id and s["is_sold"] == 0:
            target = s
            break
    if target is None:
        return
    data["stock"] = [s for s in data["stock"] if s["id"] != stock_id]
    _save_db(data)


def reserve_stock(item_id: int, qty: int):
    data = _load_db()
    bucket = _stock_by_item.get(item_id) or []
    available = [s for s in bucket if s["is_sold"] == 0]
    available.sort(key=lambda x: x["id"])
    if len(available) < qty:
        return []
    picked = available[:qty]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for s in picked:
        s["is_sold"] = 1
        s["sold_at"] = now
    _save_db(data)
    return [(s["id"], s["key_data"], s["type"]) for s in picked]


def record_order(uid, item_id, item_name, qty, total, payment_id=""):
    data = _load_db()
    oid = _next_id(data, "orders")
    data["orders"].append({
        "id": oid, "user_id": uid, "item_id": item_id,
        "item_name": item_name, "qty": qty, "total": total,
        "payment_id": payment_id,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    key = str(uid)
    if key in data["users"]:
        data["users"][key]["total_orders"] = data["users"][key].get("total_orders", 0) + 1
        data["users"][key]["total_spent"] = data["users"][key].get("total_spent", 0.0) + total
    _save_db(data)


def get_user_orders(uid: int, limit: int = 20):
    rows = [o for o in _load_db()["orders"] if o["user_id"] == uid]
    rows.sort(key=lambda x: x["id"], reverse=True)
    return rows[:limit]


init_db()

# ====================================================================================================
# [ UI HELPERS ]
# ====================================================================================================

def _is_premium_id(s) -> bool:
    try:
        s2 = str(s).strip()
        return s2.isdigit() and len(s2) >= 15
    except Exception:
        return False


_IKB_FIELDS = None

def _ikb_has(field: str) -> bool:
    global _IKB_FIELDS
    if _IKB_FIELDS is None:
        try:
            _IKB_FIELDS = set(InlineKeyboardButton.model_fields.keys())
        except Exception:
            _IKB_FIELDS = set()
    return field in _IKB_FIELDS


def IBtn(text, callback_data=None, style=None, icon_custom_emoji_id=None, url=None):
    display_text = text
    real_icon = None

    if icon_custom_emoji_id:
        s = str(icon_custom_emoji_id).strip()
        if _is_premium_id(s):
            real_icon = s
        elif s:
            display_text = f"{s} {text}".strip()

    kwargs = {"text": display_text}
    if url:
        kwargs["url"] = url
    else:
        kwargs["callback_data"] = callback_data

    if real_icon and _ikb_has("icon_custom_emoji_id"):
        kwargs["icon_custom_emoji_id"] = real_icon
    if style and _ikb_has("style"):
        kwargs["style"] = style

    return InlineKeyboardButton(**kwargs)


def RBtn(text, style=None, icon_custom_emoji_id=None):
    display_text = text
    real_icon = None

    if icon_custom_emoji_id:
        s = str(icon_custom_emoji_id).strip()
        if _is_premium_id(s):
            real_icon = s
        elif s:
            display_text = f"{s} {text}".strip()

    btn = KeyboardButton(text=display_text)
    if style:
        try:
            object.__setattr__(btn, 'style', style)
        except Exception:
            pass
    if real_icon:
        try:
            object.__setattr__(btn, 'icon_custom_emoji_id', real_icon)
        except Exception:
            pass
    return btn


def cancel_inline():
    return InlineKeyboardMarkup(inline_keyboard=[[
        IBtn(text="Cancel", callback_data="cancel_action", style="danger")
    ]])


def get_main_reply_kb(uid: int) -> ReplyKeyboardMarkup:
    kb = [[RBtn(text="BUY NOW", style="primary", icon_custom_emoji_id=ICON_BUY_NOW)]]
    if is_admin_user(uid):
        kb.append([RBtn(text="Admin Panel", style="danger", icon_custom_emoji_id=ICON_ADMIN_PANEL)])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


async def cancel_and_delete_user_qr(uid: int):
    info = _active_user_qrs.pop(uid, None)
    if info:
        chat_id, msg_id, payment_id = info
        cancelled_payments.add(payment_id)
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass


async def alert_to_channel(text):
    chan_id = get_setting('order_group_id') or get_setting('channel_id')
    if not chan_id:
        return
    try:
        await bot.send_message(int(chan_id), text)
    except Exception as e:
        logger.warning(f"alert_to_channel failed: {e}")


def is_admin_user(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    return bool(db_get_user(uid).get("is_admin", 0))


def admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="Manage Products", callback_data="adm_manage_products", style="primary",
              icon_custom_emoji_id=ICON_CREATE_PRODUCT)],
        [IBtn(text="View Stock", callback_data="adm_view_stock", style="primary",
              icon_custom_emoji_id=ICON_DELETE_PRODUCT),
         IBtn(text="Manage Stock", callback_data="adm_manage_stock", style="primary",
              icon_custom_emoji_id=ICON_ADD_PIC_VIDEO)],
        [IBtn(text="Broadcast", callback_data="adm_broadcast", style="primary",
              icon_custom_emoji_id=ICON_ADD_PLAN),
         IBtn(text="Manage Reseller", callback_data="adm_reseller", style="primary",
              icon_custom_emoji_id=ICON_DEL_PLAN)],
        [IBtn(text="Manage Admins", callback_data="adm_manage_admins", style="primary",
              icon_custom_emoji_id=ICON_ADD_ADMIN),
         IBtn(text="Set Start Media", callback_data="adm_start_media", style="primary",
              icon_custom_emoji_id=ICON_DEL_FILE_LINK)],
        [IBtn(text="Setup Group", callback_data="adm_setup_group", style="primary",
              icon_custom_emoji_id=ICON_SETUP_GROUP),
         IBtn(text="Maintenance", callback_data="adm_maintenance", style="primary",
              icon_custom_emoji_id=ICON_MAINT_OPEN)]
    ])


def setup_group_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="Add Group", callback_data="adm_add_group", style="primary",
              icon_custom_emoji_id=ICON_ADD_GROUP),
         IBtn(text="Del Grop", callback_data="adm_del_group", style="danger",
              icon_custom_emoji_id=ICON_DEL_GROUP)],
        [IBtn(text="View Gp Id", callback_data="adm_view_group", style="success",
              icon_custom_emoji_id=ICON_VIEW_GROUP)],
        [IBtn(text="Back", callback_data="back_admin", style="primary",
              icon_custom_emoji_id=ICON_BACK)]
    ])


def manage_products_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="Create Product", callback_data="adm_add_cat", style="primary",
              icon_custom_emoji_id=ICON_CREATE_PRODUCT),
         IBtn(text="Delete Product", callback_data="adm_del_cat", style="primary",
              icon_custom_emoji_id=ICON_DELETE_PRODUCT)],
        [IBtn(text="Add Picture/Video", callback_data="adm_add_cat_media", style="primary",
              icon_custom_emoji_id=ICON_ADD_PIC_VIDEO),
         IBtn(text="Delete Picture/Video", callback_data="adm_del_cat_media", style="primary",
              icon_custom_emoji_id=ICON_DEL_PIC_VIDEO)],
        [IBtn(text="Add Plan", callback_data="adm_add_item", style="primary",
              icon_custom_emoji_id=ICON_ADD_PLAN),
         IBtn(text="Delete Plan", callback_data="adm_del_item", style="primary",
              icon_custom_emoji_id=ICON_DEL_PLAN)],
        [IBtn(text="Edit Price Plan", callback_data="adm_edit_item_price", style="primary",
              icon_custom_emoji_id=ICON_ADD_PLAN)],
        [IBtn(text="Add File/Link", callback_data="adm_add_post_media", style="primary",
              icon_custom_emoji_id=ICON_ADD_FILE_LINK),
         IBtn(text="Delete File/Link", callback_data="adm_del_post_media", style="primary",
              icon_custom_emoji_id=ICON_DEL_FILE_LINK)],
        [IBtn(text="Back", callback_data="back_admin", style="primary",
              icon_custom_emoji_id=ICON_BACK)]
    ])


def manage_admins_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="View Admins", callback_data="adm_view_admins", style="success",
              icon_custom_emoji_id=ICON_VIEW_ADMINS)],
        [IBtn(text="Add Admin", callback_data="adm_add_admin", style="primary",
              icon_custom_emoji_id=ICON_ADD_ADMIN),
         IBtn(text="Del Admin", callback_data="adm_del_admin", style="danger",
              icon_custom_emoji_id=ICON_DEL_ADMIN)],
        [IBtn(text="Back", callback_data="back_admin", style="primary",
              icon_custom_emoji_id=ICON_BACK)]
    ])


# ✅ កែសម្រួល៖ BOT MAINTENANCE ជាមួយនឹង Custom Emoji ID
def maintenance_kb():
    is_closed = get_setting("maintenance", "0") == "1"
    status_label = (
        f"<tg-emoji emoji-id=\"{ICON_MAINT_CLOSE}\">🔴</tg-emoji> <b>CLOSED (បិទ)</b>"
        if is_closed else
        f"<tg-emoji emoji-id=\"{ICON_MAINT_OPEN}\">🟢</tg-emoji> <b>OPEN (បើកដំណើរការ)</b>"
    )

    text = (
        f"<tg-emoji emoji-id=\"{ICON_MAINT_TITLE}\">🛠️</tg-emoji> <b>BOT MAINTENANCE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id=\"{ICON_MAINT_STATUS}\">🚦</tg-emoji> ស្ថានភាពបច្ចុប្បន្ន៖ {status_label}\n\n"
        f"<tg-emoji emoji-id=\"{ICON_MAINT_OPEN}\">🟢</tg-emoji> • <b>Open</b> : បើកឱ្យ User ទិញទំនិញធម្មតា\n"
        f"<tg-emoji emoji-id=\"{ICON_MAINT_CLOSE}\">🔴</tg-emoji> • <b>Close</b> : បិទ Bot (ពេល User ចុច Buy Now នឹងលោត \"Bot Update សូមរងចាំ\")\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="Open", callback_data="adm_maint_open", style="success",
              icon_custom_emoji_id=ICON_MAINT_OPEN),
         IBtn(text="Close", callback_data="adm_maint_close", style="danger",
              icon_custom_emoji_id=ICON_MAINT_CLOSE)],
        [IBtn(text="Back", callback_data="back_admin", style="primary",
              icon_custom_emoji_id=ICON_BACK)]
    ])
    return text, kb


MANAGE_PRODUCTS_TEXT = "<b>Manage Products & Items</b>"
ADMIN_PANEL_TEXT = "<b>ADMIN PANEL</b>"

# ====================================================================================================
# [ QR CODE GENERATION ]
# ====================================================================================================

def render_khqr_png(qr_string: str) -> Optional[io.BytesIO]:
    if not qr_string:
        return None
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(qr_string)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception as e:
        logger.error(f"QRCode render failed: {e}")
        return None

# ====================================================================================================
# [ GLOBAL CANCEL ]
# ====================================================================================================

@dp.callback_query(F.data == "cancel_action")
@auto_answer()
async def cancel_action_handler(call: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    state_data = await state.get_data()
    await state.clear()
    await cancel_and_delete_user_qr(call.from_user.id)
    try:
        await call.message.delete()
    except Exception:
        pass

    if current_state == OrderFSM.custom_qty.state:
        item_id = state_data.get("item_id")
        if item_id:
            await render_checkout(call, item_id, 1, is_edit=False)
        else:
            await send_categories(call)
        return

    if is_admin_user(call.from_user.id):
        await bot.send_message(call.message.chat.id, ADMIN_PANEL_TEXT,
                               reply_markup=admin_main_kb())
    else:
        await send_categories(call)


@dp.callback_query(F.data.startswith("cancelpay_"))
async def cancel_payment_handler(call: CallbackQuery, state: FSMContext):
    payment_id = call.data.split("_", 1)[1]
    final_status = await khqr_check_payment(payment_id)
    if final_status and final_status.lower() in PAID_STATUSES:
        return await quick_answer(call, "⚠️ ការទូទាត់បានជោគជ័យរួចហើយ! សូមរង់ចាំទំនិញ...", show_alert=True)

    cancelled_payments.add(payment_id)
    _active_user_qrs.pop(call.from_user.id, None)
    await quick_answer(call)
    await state.clear()

    try:
        await call.message.delete()
    except Exception:
        pass

    rk = get_main_reply_kb(call.from_user.id)
    await bot.send_message(
        call.message.chat.id,
        f"<tg-emoji emoji-id=\"{ICON_PAY_CANCELLED}\">❌</tg-emoji> <b>Payment cancelled.</b>",
        reply_markup=rk
    )
    await send_categories(call)


@dp.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await cancel_and_delete_user_qr(msg.from_user.id)
    rk = get_main_reply_kb(msg.from_user.id)
    await msg.answer(
        f"<tg-emoji emoji-id=\"{ICON_PAY_CANCELLED}\">❌</tg-emoji> <b>Payment cancelled.</b>",
        reply_markup=rk
    )

# ====================================================================================================
# [ KHQR API ]
# ====================================================================================================

PAID_STATUSES   = {"paid", "success", "completed", "approved", "successful"}
FAILED_STATUSES = {"expired", "failed", "underpaid", "cancelled", "canceled", "rejected"}


async def khqr_create_payment(amount: float) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {KHQR_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        amt = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"ok": False, "error_text": "Invalid amount"}

    if amt < 0.01:
        return {"ok": False, "error_text": "Amount too small (min $0.01)"}

    payload = {"amount": amt}

    try:
        async with get_http().post(KHQR_CREATE_URL, json=payload, headers=headers) as resp:
            raw_text = await resp.text()
            try:
                data = json.loads(raw_text)
            except Exception:
                logger.warning(f"KHQR create non-JSON [{resp.status}]: {raw_text[:200]}")
                return {"ok": False, "error_text": f"HTTP {resp.status}: invalid JSON response"}

            if not isinstance(data, dict):
                return {"ok": False, "error_text": "Malformed API response"}

            if "ok" not in data:
                data["ok"] = bool(data.get("id") or data.get("qr"))

            logger.info(f"KHQR create [{resp.status}]: ok={data.get('ok')}, id={data.get('id')}, amount=${amt}")

            if not data.get("ok"):
                data.setdefault("error_text", f"HTTP {resp.status}: {data.get('msg') or raw_text[:150]}")
            if data.get("ok") and not data.get("qr"):
                return {"ok": False, "error_text": "API returned ok but missing QR string"}
            return data

    except asyncio.TimeoutError:
        return {"ok": False, "error_text": "Gateway timeout"}
    except aiohttp.ClientError as e:
        return {"ok": False, "error_text": f"Network error: {e}"}
    except Exception as e:
        logger.error(f"KHQR Create Error: {e}", exc_info=True)
        return {"ok": False, "error_text": str(e)}


async def khqr_check_payment(payment_id: str) -> str:
    if not payment_id:
        return ""
    headers = {"Authorization": f"Bearer {KHQR_API_KEY}"}
    try:
        async with get_http().get(KHQR_CHECK_URL, headers=headers,
                                  params={"id": payment_id}) as resp:
            if resp.status != 200:
                logger.warning(f"KHQR check HTTP {resp.status} for {payment_id}")
                return ""
            try:
                data = await resp.json(content_type=None)
            except Exception:
                return ""
            if not isinstance(data, dict):
                return ""

            payment_obj = data.get("payment") if isinstance(data.get("payment"), dict) else data
            status = str(payment_obj.get("status") or "").strip().lower()
            return status
    except asyncio.TimeoutError:
        return ""
    except Exception as e:
        logger.error(f"KHQR Check Error: {e}")
        return ""


def _mark_processed(payment_id: str) -> bool:
    if not payment_id:
        return True
    now = time.time()
    stale = [k for k, t in _processed_payments.items() if now - t > 1800]
    for k in stale:
        _processed_payments.pop(k, None)
    if payment_id in _processed_payments:
        return False
    _processed_payments[payment_id] = now
    return True

# ====================================================================================================
# [ FSM STATES ]
# ====================================================================================================

class OrderFSM(StatesGroup):
    custom_qty = State()

class AdminAddCat(StatesGroup):
    name = State(); icon = State()

class AdminAddItem(StatesGroup):
    cat_id = State(); name = State(); price = State()
    reseller_price = State(); icon = State()

class AdminEditItemPrice(StatesGroup):
    item_id = State()
    price = State()
    reseller_price = State()

class AdminAddStock(StatesGroup):
    item_id = State(); keys = State()

class AdminBroadcast(StatesGroup):
    msg = State()

class AdminReseller(StatesGroup):
    add_id = State(); del_id = State()

class AdminAdmin(StatesGroup):
    add_id = State(); del_id = State()

class AdminGroup(StatesGroup):
    add_id = State()

class AdminStartMedia(StatesGroup):
    media = State()

class AdminCatMedia(StatesGroup):
    cat_id = State(); media = State()

class AdminPostMedia(StatesGroup):
    item_id = State(); media = State()

# ====================================================================================================
# [ START ]
# ====================================================================================================

@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await cancel_and_delete_user_qr(msg.from_user.id)
    fname = msg.from_user.first_name or "Unknown"
    uname = msg.from_user.username or ""

    db_upsert_user(msg.from_user.id, first_name=fname, username=uname)

    bot_name = get_setting('bot_name', 'AIDA MOD STORE')
    rk = get_main_reply_kb(msg.from_user.id)

    text = (
        f"<tg-emoji emoji-id='5328024204770044648'>👋</tg-emoji> <b>.𝗛𝗲𝗹𝗹𝗼, 𝘄𝗲𝗹𝗰𝗼𝗺𝗲 𝘁𝗼 𝗯𝗼𝘁 <b>\n\n"
        f"<tg-emoji emoji-id='6210512226000839036'>🛒</tg-emoji> ការដឹកជញ្ជូនភ្លាមៗ\n"
        f"<tg-emoji emoji-id='5213414681808480994'>🛍</tg-emoji> ការទូទាត់ប្រកបដោយសុវត្ថិភាព\n"
        f"<tg-emoji emoji-id='5870991681112772430'>🕘</tg-emoji> សេវាកម្ម 24/7\n\n"
        f"សូមចុច (<tg-emoji emoji-id='6210512226000839036'>🛒</tg-emoji> <b>BUY NOW</b>) ខាងក្រោម"
    )

    start_media = get_setting('start_media')
    if start_media:
        try:
            m_type, m_id = start_media.split(':', 1)
            if m_type == 'photo':
                await msg.answer_photo(m_id, caption=text, reply_markup=rk)
                return
            elif m_type == 'video':
                await msg.answer_video(m_id, caption=text, reply_markup=rk)
                return
        except Exception:
            pass
    await msg.answer(text, reply_markup=rk)

# ====================================================================================================
# [ SHOP FLOW ]
# ====================================================================================================

@dp.message(F.text == "BUY NOW")
async def shop_categories(msg: Message, state: FSMContext):
    if get_setting("maintenance", "0") == "1" and not is_admin_user(msg.from_user.id):
        return await msg.answer("Bot Update សូមរងចាំ")

    await state.clear()
    await cancel_and_delete_user_qr(msg.from_user.id)
    await send_categories(msg)


async def send_categories(event):
    cats = get_categories()
    if not cats:
        txt = "❌ not have product !"
        if isinstance(event, CallbackQuery):
            return await safe_edit_or_send(event, txt)
        return await event.answer(txt)

    kb = [[IBtn(text=c[1], callback_data=f"sel_cat_{c[0]}",
                style="primary", icon_custom_emoji_id=c[2])] for c in cats]
    text = '<tg-emoji emoji-id="5312361253610475399">🛒</tg-emoji> <b>Categories all ៖</b>'
    mk = InlineKeyboardMarkup(inline_keyboard=kb)

    if isinstance(event, CallbackQuery):
        await safe_edit_or_send(event, text, mk)
    else:
        await event.answer(text, reply_markup=mk)


@dp.callback_query(F.data.startswith("sel_cat_"))
@auto_answer()
async def shop_items(call: CallbackQuery):
    if get_setting("maintenance", "0") == "1" and not is_admin_user(call.from_user.id):
        return await quick_answer(call, "Bot Update សូមរងចាំ", show_alert=True)

    try:
        cat_id = int(call.data.split("_")[2])
    except (ValueError, IndexError):
        return
    cat = get_category(cat_id)
    if not cat:
        return
    items = get_items(cat_id)

    uid = call.from_user.id
    is_res = is_user_reseller(uid)

    kb = []
    for i in items:
        stock_count = count_stock(i["id"])
        effective_price = get_item_price(i, is_res)

        if is_res:
            suffix = f"${effective_price:.2f} reseller" + (f" ({stock_count})" if stock_count > 0 else " (Out)")
        else:
            suffix = f"${effective_price:.2f}" + (f" ({stock_count})" if stock_count > 0 else " (Out)")

        btn_style = "success" if stock_count > 0 else "danger"
        kb.append([IBtn(text=f"{i['name']} - {suffix}", callback_data=f"sel_item_{i['id']}",
                        style=btn_style, icon_custom_emoji_id=i.get("icon"))])

    kb.append([IBtn(text="Back ( Category )", callback_data="back_to_cat",
                    style="danger", icon_custom_emoji_id="5352759161945867747")])
    mk = InlineKeyboardMarkup(inline_keyboard=kb)

    # 🔹 ចាប់យក Icon ID ពី Category (Custom Emoji ឬ Regular Emoji)
    cat_icon = str(cat.get("icon") or "").strip()
    if cat_icon.isdigit() and len(cat_icon) >= 15:
        icon_display = f'<tg-emoji emoji-id="{cat_icon}">📦</tg-emoji> '
    elif cat_icon:
        icon_display = f"{cat_icon} "
    else:
        icon_display = ""

    # ✅ ចេញ Icon នៅពីមុខឈ្មោះ Category (ឧ. [AmongUs] bdk)
    text = f"{icon_display}<b>{cat['name']}</b>"

    if cat.get("media_data"):
        try:
            await call.message.delete()
        except Exception:
            pass
        try:
            m_type, m_id = cat["media_data"].split(':', 1)
            if m_type == 'photo':
                await bot.send_photo(call.message.chat.id, m_id, caption=text, reply_markup=mk)
            elif m_type == 'video':
                await bot.send_video(call.message.chat.id, m_id, caption=text, reply_markup=mk)
            else:
                raise ValueError
        except Exception:
            await bot.send_message(call.message.chat.id, text, reply_markup=mk)
        return

    await safe_edit_or_send(call, text, mk)




@dp.callback_query(F.data == "back_to_cat")
@auto_answer()
async def back_to_cat_handler(call: CallbackQuery):
    await send_categories(call)


@dp.callback_query(F.data.startswith("sel_item_"))
@auto_answer()
async def pre_checkout_item(call: CallbackQuery, state: FSMContext):
    if get_setting("maintenance", "0") == "1" and not is_admin_user(call.from_user.id):
        return await quick_answer(call, "Bot Update សូមរងចាំ", show_alert=True)

    try:
        item_id = int(call.data.split("_")[2])
    except (ValueError, IndexError):
        return
    if count_stock(item_id) <= 0:
        return await bot.send_message(
            call.message.chat.id,
            f"<tg-emoji emoji-id=\"{ICON_STOCK_OUT}\">⚠️</tg-emoji> <b>Stock Out! Please try again later.</b>"
        )
    await render_checkout(call, item_id, 1, is_edit=True)


@dp.callback_query(F.data.startswith("setqty_"))
@auto_answer()
async def ask_custom_qty(call: CallbackQuery, state: FSMContext):
    if get_setting("maintenance", "0") == "1" and not is_admin_user(call.from_user.id):
        return await quick_answer(call, "Bot Update សូមរងចាំ", show_alert=True)

    try:
        item_id = int(call.data.split("_")[1])
    except (ValueError, IndexError):
        return
    await state.update_data(item_id=item_id, msg_id=call.message.message_id)
    try:
        await call.message.edit_text("សូមវាយបញ្ចូលចំនួនដែលអ្នកចង់ទិញ (ជាលេខ):",
                                      reply_markup=cancel_inline())
    except Exception:
        pass
    await state.set_state(OrderFSM.custom_qty)


@dp.message(OrderFSM.custom_qty)
async def process_custom_qty(msg: Message, state: FSMContext):
    data = await state.get_data()
    try:
        qty = int((msg.text or "").strip())
        if qty <= 0:
            raise ValueError
    except Exception:
        return await msg.answer("❌ សូមបញ្ចូលចំនួនជាលេខត្រឹមត្រូវ!", reply_markup=cancel_inline())
    try:
        await msg.delete()
    except Exception:
        pass
    await render_checkout(msg, data['item_id'], qty, is_edit=False,
                          old_msg_id=data.get('msg_id'))
    await state.clear()


async def render_checkout(event, item_id, qty, is_edit=True, old_msg_id=None):
    uid = event.from_user.id
    chat_id = (
        event.message.chat.id
        if isinstance(event, CallbackQuery) and event.message
        else (event.chat.id if isinstance(event, Message) else event.from_user.id)
    )

    is_res = is_user_reseller(uid)
    item = get_item(item_id)
    if not item:
        return

    cat = get_category(item.get("cat_id"))
    cat_name = cat["name"] if cat else "ទំនិញ"

    u_price = get_item_price(item, is_res)
    total = u_price * qty
    stock_count = count_stock(item_id)

    pay_enabled = stock_count >= qty and stock_count > 0

    text = (
        f"<tg-emoji emoji-id=\"{ICON_CONFIRM_TITLE}\">🛒</tg-emoji> <b>កំណត់ចំនួនទំនិញដែលចង់ទិញ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id=\"{ICON_CONFIRM_PRODUCT}\">📦</tg-emoji> • <b>ទំនិញ៖</b> {cat_name}\n"
        f"<tg-emoji emoji-id=\"{ICON_CONFIRM_PLAN}\">🏷</tg-emoji> • <b>កញ្ចប់៖</b> {item['name']}\n"
        f"<tg-emoji emoji-id=\"{ICON_CONFIRM_UNITPRICE}\">💵</tg-emoji> • <b>តម្លៃរាយ៖</b> ${u_price:.2f} / មួយ\n"
        f"<tg-emoji emoji-id=\"{ICON_CONFIRM_STOCK}\">📊</tg-emoji> • <b>ស្តុកនៅសល់៖</b> {stock_count}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id=\"{ICON_CONFIRM_QTY}\">🔢</tg-emoji> • <b>ចំនួនជ្រើសរើស៖</b> {qty}\n"
        f"<tg-emoji emoji-id=\"{ICON_CONFIRM_TOTAL}\">💰</tg-emoji> • <b>តម្លៃសរុប៖</b> ${total:.2f}\n\n"
        f"ចុច 'បញ្ចូលចំនួនផ្ទាល់ខ្លួន' ដើម្បីកែប្រែចំនួន ឬចុច 'បង់ប្រាក់' ដើម្បីបន្ត៖"
    )

    kb_rows = [[IBtn(text="បញ្ចូលចំនួនផ្ទាល់ខ្លួន", callback_data=f"setqty_{item_id}",
                     style="primary", icon_custom_emoji_id="5258215635996908355")]]

    if pay_enabled:
        kb_rows.append([IBtn(text="បង់ប្រាក់ (ABA PAY)", callback_data=f"abapay_{item_id}_{qty}",
                             style="success", icon_custom_emoji_id="6316599954026535998")])
    else:
        kb_rows.append([IBtn(text="បង់ប្រាក់ (Out of Stock)", callback_data="out_of_stock",
                             style="danger", icon_custom_emoji_id="6316599954026535998")])

    kb_rows.append([IBtn(text="Back", callback_data=f"sel_cat_{item['cat_id']}",
                         style="danger", icon_custom_emoji_id="5352759161945867747")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    if old_msg_id:
        try:
            await bot.delete_message(chat_id, old_msg_id)
        except Exception:
            pass

    if is_edit and isinstance(event, CallbackQuery) and event.message:
        await safe_edit_or_send(event, text, kb)
        return

    await bot.send_message(chat_id, text, reply_markup=kb)


@dp.callback_query(F.data == "out_of_stock")
@auto_answer("Stock Out! Please try again later.", alert=True)
async def out_of_stock_handler(call: CallbackQuery):
    pass

# ====================================================================================================
# [ ABA KHQR CHECKOUT ]
# ====================================================================================================

def _parse_expires_at(expires_at) -> Optional[float]:
    if expires_at is None:
        return None

    if isinstance(expires_at, (int, float)):
        v = float(expires_at)
        return v / 1000 if v > 1e12 else v

    s = str(expires_at).strip()
    if not s:
        return None

    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    return None


async def _live_qr_countdown(chat_id: int, msg_id: int, payment_id: str,
                              expires_ts: float, caption_builder,
                              reply_markup: Optional[InlineKeyboardMarkup] = None):
    """⚡ Smooth countdown — update 200ms ជិត expiry, 500ms ធម្មតា"""
    last_cap = None
    last_edit_time = 0

    while True:
        if payment_id in cancelled_payments:
            return

        remaining = int(expires_ts - time.time())
        if remaining < 0:
            remaining = 0

        cap = caption_builder(remaining)
        now = time.time()

        if cap != last_cap and (now - last_edit_time >= 0.9 or remaining <= 5):
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=msg_id,
                    caption=cap,
                    reply_markup=reply_markup,
                )
                last_cap = cap
                last_edit_time = now
            except TelegramBadRequest as e:
                low = str(e).lower()
                if "message to edit not found" in low or "message can't be edited" in low:
                    return
                if "message is not modified" in low:
                    last_cap = cap
                elif "flood" in low or "retry" in low:
                    await asyncio.sleep(1.5)
            except Exception:
                pass

        if remaining <= 0:
            return

        if remaining > 10:
            await asyncio.sleep(0.5)
        else:
            await asyncio.sleep(0.2)


@dp.callback_query(F.data.startswith("abapay_"))
@auto_answer()
async def handle_aba_pay(call: CallbackQuery):
    uid = call.from_user.id

    if get_setting("maintenance", "0") == "1" and not is_admin_user(uid):
        return await bot.send_message(call.message.chat.id, "Bot Update សូមរងចាំ")

    parts = call.data.split("_")
    try:
        item_id, qty = int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return await bot.send_message(call.message.chat.id, "❌ Invalid request.")

    stock_qty = count_stock(item_id)
    if qty > stock_qty:
        return await bot.send_message(
            call.message.chat.id,
            f"<tg-emoji emoji-id=\"{ICON_STOCK_OUT}\">⚠️</tg-emoji> <b>Stock Out! Please try again later.</b> (មាន {stock_qty})")

    is_res = is_user_reseller(uid)
    item = get_item(item_id)
    if not item:
        return await bot.send_message(call.message.chat.id, "Item not found.")

    u_price = get_item_price(item, is_res)
    total = round(u_price * qty, 2)

    await cancel_and_delete_user_qr(uid)

    try:
        await call.message.delete()
    except Exception:
        pass

    try:
        loading_msg = await bot.send_message(
            call.message.chat.id,
            f'<tg-emoji emoji-id="{ICON_CREATING_KHQR}">⏱️</tg-emoji> '
            f'<b>Creating your secure KHQR…</b>',
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception:
        loading_msg = None

    res = await khqr_create_payment(total)

    if not res or not res.get("ok"):
        err = (
            (res or {}).get("msg")
            or (res or {}).get("error_text")
            or (res or {}).get("error")
            or "Unknown error"
        )
        if loading_msg:
            try:
                await loading_msg.delete()
            except Exception:
                pass
        rk = get_main_reply_kb(uid)
        return await bot.send_message(
            call.message.chat.id, f"❌ Failed to create QR!\n<code>{err}</code>",
            reply_markup=rk)

    payment_id   = str(res.get("id"))
    qr_string    = res.get("qr")
    expires_at   = res.get("expires_at")
    aba_deep     = res.get("aba_deeplink")

    expires_ts = _parse_expires_at(expires_at)
    if expires_ts is None or expires_ts <= time.time():
        expires_ts = time.time() + 600

    cap_template = (
        f"<tg-emoji emoji-id=\"6158997026747983036\">🏦</tg-emoji> <b>ABA PAY WAY (KHQR)</b>\n"
        f"- <tg-emoji emoji-id=\"5197434882321567830\">💵</tg-emoji> ចំនួនត្រូវ pay : <b>${total:.2f}</b>\n"
        f"- <tg-emoji emoji-id=\"5345987742276797245\">📦</tg-emoji> ទំនិញ : <b>{item['name']}</b> x{qty}\n"
        f"- <tg-emoji emoji-id=\"5444856076954520455\">🧾</tg-emoji> Ref : <code>{payment_id}</code>\n"
        f"- <tg-emoji emoji-id=\"5386367538735104399\">⏱️</tg-emoji> <b>QR Expires in :</b> <b>{{COUNTDOWN}}</b>\n"
        f"- <tg-emoji emoji-id=\"6129570565125577536\">⚡️</tg-emoji> ពេលផេររួច ទំនិញនឹងលូតដោយស្វ័យប្រវត្តិ"
    )

    def build_cap(remaining: int) -> str:
        mm, ss = divmod(max(0, remaining), 60)
        return cap_template.replace("{COUNTDOWN}", f"{mm:02d}:{ss:02d}")

    initial_cap = build_cap(int(expires_ts - time.time()))

    kb_arr = []
    if aba_deep:
        kb_arr.append([IBtn(text="Open ABA Mobile", url=aba_deep, style="primary",
                            icon_custom_emoji_id="6316599954026535998")])

    kb_arr.append([IBtn(text="Cancel QR / បោះបង់", callback_data=f"cancelpay_{payment_id}",
                        style="danger", icon_custom_emoji_id="6102914615107133393")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_arr)

    if loading_msg:
        try:
            await loading_msg.delete()
        except Exception:
            pass

    png_buf = await asyncio.to_thread(render_khqr_png, qr_string)

    try:
        if png_buf:
            sent_msg = await bot.send_photo(
                call.message.chat.id,
                photo=BufferedInputFile(png_buf.getvalue(), filename="aba_khqr.png"),
                caption=initial_cap, reply_markup=kb)
        else:
            sent_msg = await bot.send_message(
                call.message.chat.id, initial_cap, reply_markup=kb,
                link_preview_options=LinkPreviewOptions(is_disabled=False))
    except Exception as e:
        logger.error(f"send QR failed: {e}")
        rk = get_main_reply_kb(uid)
        return await bot.send_message(call.message.chat.id, "❌ Failed to send QR. Try again.", reply_markup=rk)

    _active_user_qrs[uid] = (call.message.chat.id, sent_msg.message_id, payment_id)

    run_bg_task(_live_qr_countdown(
        chat_id=call.message.chat.id,
        msg_id=sent_msg.message_id,
        payment_id=payment_id,
        expires_ts=expires_ts,
        caption_builder=build_cap,
        reply_markup=kb,
    ))

    run_bg_task(poll_payment(
        uid=uid,
        user_snapshot={"id": uid,
                       "first_name": call.from_user.first_name,
                       "username": call.from_user.username},
        chat_id=call.message.chat.id,
        payment_id=payment_id, item_id=item_id, qty=qty,
        msg_id=sent_msg.message_id, total_paid=total,
        item_name=item["name"]))


async def poll_payment(uid, user_snapshot, chat_id, payment_id, item_id, qty, msg_id,
                       total_paid, item_name):
    """⚡ Adaptive polling: 500ms → 200ms → 100ms (smooth + safe API)"""
    timeout = time.time() + 600
    consecutive_errors = 0
    start_time = time.time()

    while time.time() < timeout:
        elapsed = time.time() - start_time

        if elapsed < 60:
            interval = 0.5
        elif elapsed < 180:
            interval = 0.2
        else:
            interval = 0.1

        if payment_id in cancelled_payments:
            cancelled_payments.discard(payment_id)
            _active_user_qrs.pop(uid, None)
            return

        if payment_id in _processed_payments:
            _active_user_qrs.pop(uid, None)
            return

        status = await khqr_check_payment(payment_id)

        if not status:
            consecutive_errors += 1
            if consecutive_errors >= 10:
                logger.warning(f"Payment {payment_id}: 10 consecutive check failures")
                await asyncio.sleep(min(2.0, interval * 4))
                continue
        else:
            consecutive_errors = 0

        if status in PAID_STATUSES:
            cancelled_payments.discard(payment_id)
            _active_user_qrs.pop(uid, None)
            await deliver_item(uid, user_snapshot, chat_id, item_id, qty, msg_id,
                               total_paid, item_name, payment_id=payment_id)
            return

        if status in FAILED_STATUSES:
            cancelled_payments.discard(payment_id)
            _active_user_qrs.pop(uid, None)
            try:
                await bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
            rk = get_main_reply_kb(uid)
            if status == "underpaid":
                await bot.send_message(chat_id,
                    "⚠️ Payment received but amount did not match. Please contact admin.",
                    reply_markup=rk)
            elif status in ("cancelled", "canceled"):
                await bot.send_message(
                    chat_id,
                    f"<tg-emoji emoji-id=\"{ICON_PAY_CANCELLED}\">❌</tg-emoji> <b>Payment cancelled.</b>",
                    reply_markup=rk
                )
            elif status == "expired":
                await bot.send_message(
                    chat_id,
                    f"<tg-emoji emoji-id=\"{ICON_QR_EXPIRED}\">⌛</tg-emoji> <b>QR payment expired.</b>",
                    reply_markup=rk
                )
            else:
                await bot.send_message(chat_id, f"⌛ Payment {status}.", reply_markup=rk)
            return

        await asyncio.sleep(interval)

    cancelled_payments.discard(payment_id)
    _active_user_qrs.pop(uid, None)
    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass
    rk = get_main_reply_kb(uid)
    await bot.send_message(
        chat_id,
        f"<tg-emoji emoji-id=\"{ICON_QR_EXPIRED}\">⌛</tg-emoji> <b>QR payment expired.</b>",
        reply_markup=rk
    )


# ====================================================================================================
# [ DELIVERY & KEY / LINK FORMATTING ]
# ====================================================================================================

async def deliver_item(uid, user_snapshot, chat_id, item_id, qty, msg_id,
                       total_paid, item_name, payment_id=""):
    if payment_id and not _mark_processed(payment_id):
        logger.warning(f"deliver_item: payment {payment_id} already processed, skipping.")
        return

    _active_user_qrs.pop(uid, None)

    stocks = reserve_stock(item_id, qty)
    rk = get_main_reply_kb(uid)
    if not stocks:
        return await bot.send_message(
            chat_id,
            f"<tg-emoji emoji-id=\"{ICON_STOCK_OUT}\">⚠️</tg-emoji> <b>Stock Out! Please try again later.</b>",
            reply_markup=rk
        )

    record_order(uid, item_id, item_name, qty, total_paid, payment_id=payment_id)

    file_tasks = []
    key_lines = []
    extracted_urls: List[str] = []

    for s in stocks:
        if s[2] == 'text':
            k_val = str(s[1]).strip()
            found_urls = re.findall(r'https?://[^\s<>"]+|t\.me/[^\s<>"]+', k_val)
            for u in found_urls:
                u_full = u if u.startswith("http") else f"https://{u}"
                if u_full not in extracted_urls:
                    extracted_urls.append(u_full)

            key_lines.append(f"- <code>{k_val}</code>")
        else:
            m_type, m_id = s[1].split(':', 1)
            coro = None
            if m_type == 'photo':
                coro = bot.send_photo(chat_id, m_id, caption="នេះគឺជាឯកសាររបស់អ្នក!")
            elif m_type == 'video':
                coro = bot.send_video(chat_id, m_id, caption="នេះគឺជាឯកសាររបស់អ្នក!")
            elif m_type == 'document':
                coro = bot.send_document(chat_id, m_id, caption="នេះគឺជាឯកសាររបស់អ្នក!")
            if coro is not None:
                file_tasks.append(coro)

    if file_tasks:
        results = await asyncio.gather(*file_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"send file failed: {r}")

    item = get_item(item_id)
    post_media_text = ""
    if item and item.get("post_media"):
        try:
            m_type, content = item["post_media"].split(':', 1)
            senders = {
                'photo': bot.send_photo, 'video': bot.send_video,
                'document': bot.send_document, 'audio': bot.send_audio,
                'voice': bot.send_voice,
            }
            if m_type == 'text':
                post_media_text = content.strip()
                found_urls = re.findall(r'https?://[^\s<>"]+|t\.me/[^\s<>"]+', post_media_text)
                for u in found_urls:
                    u_full = u if u.startswith("http") else f"https://{u}"
                    if u_full not in extracted_urls:
                        extracted_urls.append(u_full)
            elif m_type in senders:
                await senders[m_type](chat_id, content)
        except Exception as e:
            logger.warning(f"post_media failed: {e}")

    link_display = ""
    if extracted_urls:
        link_display = "\n".join([f"- 👉 <a href=\"{u}\">{u}</a>" for u in extracted_urls])
    elif post_media_text:
        link_display = f"- {post_media_text}"
    else:
        link_display = "- <i>(គ្មានលីងបន្ថែមទេ)</i>"

    keys_formatted = "\n".join(key_lines) if key_lines else "- <code>[ឯកសារ File ត្រូវបានផ្ញើជូន]</code>"

    msg = (
        f"<tg-emoji emoji-id=\"{ICON_BUY_SUCCESS}\">✅</tg-emoji> <b>ទំនិញបានជោគជ័យ</b>\n\n"
        f"<tg-emoji emoji-id=\"{ICON_KEY_LIST}\">🔑</tg-emoji> <b>បញ្ជី Key របស់អ្នក [ សរុប {len(stocks)} Key ]</b>\n"
        f"{keys_formatted}\n\n"
        f"<tg-emoji emoji-id=\"{ICON_THANK_YOU}\">🙏</tg-emoji> <b>អរគុណសម្រាប់ការទិញទំនិញ</b> <tg-emoji emoji-id=\"{ICON_THANK_YOU}\">🙏</tg-emoji>\n\n"
        f"<tg-emoji emoji-id=\"{ICON_INSTALL_LINK}\">📲</tg-emoji> <b>ចុចលីងខាងក្រោមដើម្បីដំឡើង</b> <tg-emoji emoji-id=\"{ICON_INSTALL_LINK}\">📲</tg-emoji>\n"
        f"{link_display}"
    )

    try:
        await bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

    link_buttons = []
    if extracted_urls:
        for idx, u in enumerate(extracted_urls, 1):
            btn_title = "ចុចទីនេះដើម្បីដំឡើង" if len(extracted_urls) == 1 else f"ចុចទីនេះដើម្បីដំឡើង {idx}"
            link_buttons.append([IBtn(text=btn_title, url=u, icon_custom_emoji_id=ICON_INSTALL_BTN)])

    continue_shopping_text = (
        f"<tg-emoji emoji-id=\"{ICON_BUY_NOW}\">🛒</tg-emoji> "
        f"<b>Please choose BUY NOW to continue shopping</b>"
    )

    try:
        if link_buttons:
            await bot.send_message(
                chat_id,
                msg,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=link_buttons),
                link_preview_options=LinkPreviewOptions(is_disabled=False)
            )
            await bot.send_message(chat_id, continue_shopping_text, reply_markup=rk)
        else:
            await bot.send_message(
                chat_id,
                msg,
                link_preview_options=LinkPreviewOptions(is_disabled=False)
            )
            await bot.send_message(chat_id, continue_shopping_text, reply_markup=rk)
    except Exception as e:
        logger.error(f"deliver_item send failed: {e}")

    run_bg_task(_send_order_notifications(
        uid, user_snapshot, item_name, total_paid, qty, item_id))


async def _send_order_notifications(uid, user_snapshot, item_name, total_paid, qty, item_id):
    if user_snapshot.get("username"):
        u_user = f"@{user_snapshot['username']}"
    else:
        u_user = user_snapshot.get("first_name") or f"User_{uid}"

    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    item = get_item(item_id)
    cat_name = "Product"
    if item:
        cat = get_category(item.get("cat_id"))
        if cat:
            cat_name = cat.get("name", "Product")

    group_msg = (
        f"<tg-emoji emoji-id=\"{ICON_GP_ORDER_NEW}\">🛒</tg-emoji> <b>ORDER NEW</b>\n"
        f"<tg-emoji emoji-id=\"{ICON_GP_PRODUCT}\">📦</tg-emoji> <b>PRODUCT :</b> {cat_name} ({item_name})\n"
        f"<tg-emoji emoji-id=\"{ICON_GP_TOTAL_KEY}\">🔑</tg-emoji> <b>BUY TOTAL KEY :</b> {qty}\n"
        f"<tg-emoji emoji-id=\"{ICON_GP_USERNAME}\">👤</tg-emoji> <b>USER NAME :</b> {u_user}\n"
        f"<tg-emoji emoji-id=\"{ICON_GP_TIME_BUY}\">⏰</tg-emoji> <b>TIME BUY :</b> {date_str}"
    )

    await alert_to_channel(group_msg)

    rem_stock = count_stock(item_id)
    if rem_stock <= 3:
        alert = (f'<tg-emoji emoji-id="6143084439204275121">🚨</tg-emoji> '
                 f'ស្តុកជិតអស់ហើយ !\n- ទំនិញ : {item_name}\n'
                 f'- <tg-emoji emoji-id="6138675041094606406">📦</tg-emoji> '
                 f'ស្តុកនៅសល់ : <b>{rem_stock}</b> ប៉ុណ្ណោះ')
        if rem_stock == 0:
            alert = alert.replace("ស្តុកជិតអស់ហើយ", "ស្តុកត្រូវបានលក់អស់ហើយ")
        kb = InlineKeyboardMarkup(inline_keyboard=[[IBtn(
            text="BUY NOW", callback_data="buy_now_direct",
            style="primary", icon_custom_emoji_id="5312361253610475399")]])
        await broadcast_users(alert, kb)


async def broadcast_users(text: str, kb: Optional[InlineKeyboardMarkup] = None):
    users = db_all_users()
    if not users:
        return
    for uid_str in users:
        try:
            await bot.send_message(int(uid_str), text, reply_markup=kb)
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            pass
        except Exception:
            pass


@dp.callback_query(F.data == "buy_now_direct")
@auto_answer()
async def buy_now_direct_btn(call: CallbackQuery):
    if get_setting("maintenance", "0") == "1" and not is_admin_user(call.from_user.id):
        return await bot.send_message(call.message.chat.id, "Bot Update សូមរងចាំ")

    await cancel_and_delete_user_qr(call.from_user.id)
    await send_categories(call)

# ====================================================================================================
# [ ADMIN PANEL ]
# ====================================================================================================

@dp.message(F.text == "Admin Panel")
async def open_admin(msg: Message):
    if not is_admin_user(msg.from_user.id):
        return
    await cancel_and_delete_user_qr(msg.from_user.id)
    await msg.answer(ADMIN_PANEL_TEXT, reply_markup=admin_main_kb())


@dp.callback_query(F.data == "back_admin")
@auto_answer()
async def back_to_admin(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await cancel_and_delete_user_qr(call.from_user.id)
    await safe_edit_or_send(call, ADMIN_PANEL_TEXT, admin_main_kb())

# ====================================================================================================
# [ ⚙️ SETUP GROUP SYSTEM ]
# ====================================================================================================

@dp.callback_query(F.data == "adm_setup_group")
@auto_answer()
async def adm_setup_group_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    gp_id = get_setting("order_group_id")
    gp_display = f"<code>{gp_id}</code>" if gp_id else "<i>(មិនទាន់កំណត់)</i>"
    text = (
        f"<tg-emoji emoji-id=\"{ICON_SETUP_GROUP}\">⚙️</tg-emoji> <b>SETUP GROUP NOTIFICATION</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Group ID បច្ចុប្បន្ន៖ {gp_display}\n\n"
        f"សូមជ្រើសរើសជម្រើសខាងក្រោម៖"
    )
    await safe_edit_or_send(call, text, setup_group_kb())


@dp.callback_query(F.data == "adm_add_group")
@auto_answer()
async def adm_add_group_prompt(call: CallbackQuery, state: FSMContext):
    text = (
        f"<tg-emoji emoji-id=\"{ICON_ADD_GROUP}\">➕</tg-emoji> <b>Add Group ID:</b>\n\n"
        f"👉 សូមផ្ញើ <b>Group ID</b> ឬ <b>Channel ID</b> (ឧ. <code>-1002345678901</code>)៖"
    )
    await safe_edit_or_send(call, text, cancel_inline())
    await state.set_state(AdminGroup.add_id)


@dp.message(AdminGroup.add_id)
async def adm_add_group_save(msg: Message, state: FSMContext):
    val = (msg.text or "").strip()

    if not val.startswith("-100"):
        if val.startswith("-"):
            val = f"-100{val[1:]}"
        else:
            val = f"-100{val}"

    try:
        gid = int(val)
        db_set_setting("order_group_id", str(gid))
        await state.clear()
        await msg.answer(
            f"✅ បានកំណត់ Group ID: <code>{gid}</code> ដោយជោគជ័យ!\n\n"
            f"<i>រាល់ពេលមានការបញ្ជាទិញជោគជ័យ បតនឹងផ្ញើសារចូល Group នេះភ្លាមៗ។</i>"
        )
    except ValueError:
        await msg.answer("❌ Invalid Group ID! សូមបញ្ចូលលេខសម្គាល់ Group ជាលេខត្រឹមត្រូវ (ឧ. -100xxxxxxxxxx)៖", reply_markup=cancel_inline())


@dp.callback_query(F.data == "adm_del_group")
@auto_answer("✅ បានលុប Group ID រួចរាល់!", alert=True)
async def adm_del_group_handler(call: CallbackQuery):
    db_set_setting("order_group_id", "")
    text = (
        f"<tg-emoji emoji-id=\"{ICON_SETUP_GROUP}\">⚙️</tg-emoji> <b>SETUP GROUP NOTIFICATION</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Group ID បច្ចុប្បន្ន៖ <i>(មិនទាន់កំណត់)</i>\n\n"
        f"សូមជ្រើសរើសជម្រើសខាងក្រោម៖"
    )
    await safe_edit_or_send(call, text, setup_group_kb())


@dp.callback_query(F.data == "adm_view_group")
@auto_answer()
async def adm_view_group_handler(call: CallbackQuery):
    gp_id = get_setting("order_group_id")
    gp_display = f"<code>{gp_id}</code>" if gp_id else "<i>(មិនទាន់មាន Group ID នៅឡើយទេ)</i>"
    text = (
        f"<tg-emoji emoji-id=\"{ICON_VIEW_GROUP}\">🆔</tg-emoji> <b>CURRENT GROUP ID</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📢 Group ID ទទួល Order: {gp_display}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="Back", callback_data="adm_setup_group", style="primary",
              icon_custom_emoji_id=ICON_BACK)]
    ])
    await safe_edit_or_send(call, text, kb)

# ----- VIEW STOCK -----

@dp.callback_query(F.data == "adm_view_stock")
@auto_answer()
async def view_stock_cmd(call: CallbackQuery):
    cats = get_categories()
    if not cats:
        return await bot.send_message(call.message.chat.id, "No products yet!")

    kb_rows = []
    for cat in cats:
        for i in get_items(cat[0]):
            cnt = count_stock(i["id"])
            p_normal = float(i.get("price", 0.0))
            p_res = float(i.get("reseller_price", p_normal))
            kb_rows.append([IBtn(
                text=f"{cat[1]} | {i['name']} (${p_normal:.2f}/R:${p_res:.2f}) : {cnt}",
                callback_data=f"viewkey_{i['id']}",
                style="danger",
                icon_custom_emoji_id=ICON_ADD_PLAN)])

    kb_rows.append([IBtn(text="Back", callback_data="back_admin",
                         style="primary", icon_custom_emoji_id=ICON_BACK)])

    text = "📊 <b>VIEW STOCK</b>\n━━━━━━━━━━━━━━━━━━━\n<i>Tap a plan below to see its keys</i>"
    mk = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await safe_edit_or_send(call, text, mk)

# ----- MANAGE PRODUCTS -----

@dp.callback_query(F.data == "adm_manage_products")
@auto_answer()
async def adm_products(call: CallbackQuery):
    await safe_edit_or_send(call, MANAGE_PRODUCTS_TEXT, manage_products_kb())


def extract_emoji(msg: Message):
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "custom_emoji":
                return ent.custom_emoji_id
    text = (msg.text or "").strip()
    if not text:
        return None
    if text.isdigit() and len(text) >= 5:
        return text
    if len(text) <= 20:
        return text
    return None


@dp.callback_query(F.data == "adm_add_cat")
@auto_answer()
async def adm_add_cat_1(call: CallbackQuery, state: FSMContext):
    await safe_edit_or_send(call, "📝 Enter Product Name (Category):", cancel_inline())
    await state.set_state(AdminAddCat.name)


@dp.message(AdminAddCat.name)
async def adm_add_cat_2(msg: Message, state: FSMContext):
    if not msg.text or not msg.text.strip():
        return await msg.answer("❌ Please send text name.", reply_markup=cancel_inline())
    await state.update_data(name=msg.text.strip())
    await msg.answer("✨ Send Premium Emoji, Regular Emoji, or ID for this product:",
                     reply_markup=cancel_inline())
    await state.set_state(AdminAddCat.icon)


@dp.message(AdminAddCat.icon)
async def adm_add_cat_3(msg: Message, state: FSMContext):
    icon_id = extract_emoji(msg)
    if not icon_id:
        return await msg.answer(
            "❌ Invalid! Send a Premium Emoji, Regular Emoji, or its ID number (max 20 chars):",
            reply_markup=cancel_inline())
    data = await state.get_data()
    add_category(data['name'], icon_id)
    await msg.answer("✅ Product created successfully!")
    await state.clear()


@dp.callback_query(F.data == "adm_del_cat")
@auto_answer()
async def adm_del_cat(call: CallbackQuery):
    cats = get_categories()
    kb = [[IBtn(text=c[1], callback_data=f"docdelcat_{c[0]}", style="primary")] for c in cats]
    kb.append([IBtn(text="Back", callback_data="adm_manage_products",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, "Select product to delete:",
                             InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("docdelcat_"))
@auto_answer("✅ Deleted!", alert=True)
async def doc_del_cat(call: CallbackQuery):
    try:
        del_category(int(call.data.split("_")[1]))
    except (ValueError, IndexError):
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    await bot.send_message(call.message.chat.id, MANAGE_PRODUCTS_TEXT,
                           reply_markup=manage_products_kb())


@dp.callback_query(F.data == "adm_add_item")
@auto_answer()
async def adm_add_item_1(call: CallbackQuery, state: FSMContext):
    cats = get_categories()
    if not cats:
        return await bot.send_message(call.message.chat.id, "Create a Product first!")
    kb = [[IBtn(text=c[1], callback_data=f"setcat_{c[0]}", style="primary")] for c in cats]
    kb.append([IBtn(text="Back", callback_data="adm_manage_products",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, "📂 Select Product:",
                             InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("setcat_"))
@auto_answer()
async def adm_add_item_2(call: CallbackQuery, state: FSMContext):
    try:
        await state.update_data(cat_id=int(call.data.split("_")[1]))
    except (ValueError, IndexError):
        return
    await safe_edit_or_send(call, "📝 Enter Plan Name:", cancel_inline())
    await state.set_state(AdminAddItem.name)


@dp.message(AdminAddItem.name)
async def adm_add_item_3(msg: Message, state: FSMContext):
    if not msg.text or not msg.text.strip():
        return await msg.answer("❌ Please send text name.", reply_markup=cancel_inline())
    await state.update_data(name=msg.text.strip())
    await msg.answer("💵 Enter regular price (e.g. 5.5):", reply_markup=cancel_inline())
    await state.set_state(AdminAddItem.price)


@dp.message(AdminAddItem.price)
async def adm_add_item_4(msg: Message, state: FSMContext):
    try:
        await state.update_data(price=float((msg.text or "").strip().replace("$", "")))
        await msg.answer("🏷 Enter Reseller price (e.g. 4.0):", reply_markup=cancel_inline())
        await state.set_state(AdminAddItem.reseller_price)
    except Exception:
        await msg.answer("❌ Invalid number! Enter a valid price (e.g. 5.5):", reply_markup=cancel_inline())


@dp.message(AdminAddItem.reseller_price)
async def adm_add_item_5(msg: Message, state: FSMContext):
    try:
        await state.update_data(reseller_price=float((msg.text or "").strip().replace("$", "")))
        await msg.answer("✨ Send Premium Emoji, Regular Emoji, or ID for this Plan:",
                         reply_markup=cancel_inline())
        await state.set_state(AdminAddItem.icon)
    except Exception:
        await msg.answer("❌ Invalid number! Enter a valid reseller price (e.g. 4.0):", reply_markup=cancel_inline())


@dp.message(AdminAddItem.icon)
async def adm_add_item_6(msg: Message, state: FSMContext):
    icon_id = extract_emoji(msg)
    if not icon_id:
        return await msg.answer(
            "❌ Invalid! Send a Premium Emoji, Regular Emoji, or its ID number (max 20 chars):",
            reply_markup=cancel_inline())
    d = await state.get_data()
    add_item(d['cat_id'], d['name'], d['price'], d['reseller_price'], icon_id)
    await msg.answer("✅ Plan added successfully!")
    await state.clear()

# ====================================================================================================
# [ EDIT PRICE PLAN FLOW ]
# ====================================================================================================

@dp.callback_query(F.data == "adm_edit_item_price")
@auto_answer()
async def adm_edit_price_pick(call: CallbackQuery):
    items = get_all_items()
    if not items:
        return await bot.send_message(call.message.chat.id, "No plans yet!")
    kb = []
    for i in items:
        p_norm = float(i.get("price", 0.0))
        p_res = float(i.get("reseller_price", p_norm))
        kb.append([IBtn(text=f"{i['name']} (${p_norm:.2f} / R:${p_res:.2f})",
                        callback_data=f"edtp_{i['id']}", style="primary",
                        icon_custom_emoji_id=ICON_ADD_PLAN)])
    kb.append([IBtn(text="Back", callback_data="adm_manage_products",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, "✏️ <b>ជ្រើសរើស Plan ដែលអ្នកចង់កែប្រែតម្លៃ៖</b>",
                             InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("edtp_"))
@auto_answer()
async def adm_edit_price_enter_norm(call: CallbackQuery, state: FSMContext):
    try:
        item_id = int(call.data.split("_")[1])
    except (ValueError, IndexError):
        return
    item = get_item(item_id)
    if not item:
        return
    await state.update_data(item_id=item_id)
    p_norm = float(item.get("price", 0.0))
    p_res = float(item.get("reseller_price", p_norm))
    text = (
        f"📌 Plan: <b>{item['name']}</b>\n"
        f"💵 តម្លៃធម្មតាបច្ចុប្បន្ន: <b>${p_norm:.2f}</b>\n"
        f"🏷 តម្លៃ Reseller បច្ចុប្បន្ន: <b>${p_res:.2f}</b>\n\n"
        f"👉 <b>សូមបញ្ចូលតម្លៃធម្មតាថ្មី ($) (ឧ. 5.5):</b>"
    )
    await safe_edit_or_send(call, text, cancel_inline())
    await state.set_state(AdminEditItemPrice.price)


@dp.message(AdminEditItemPrice.price)
async def adm_edit_price_enter_res(msg: Message, state: FSMContext):
    try:
        val = float((msg.text or "").strip().replace("$", ""))
        if val < 0:
            raise ValueError
        await state.update_data(price=val)
        await msg.answer("🏷 <b>សូមបញ្ចូលតម្លៃ Reseller ថ្មី ($) (ឧ. 4.0):</b>", reply_markup=cancel_inline())
        await state.set_state(AdminEditItemPrice.reseller_price)
    except Exception:
        await msg.answer("❌ សូមបញ្ចូលតម្លៃជាលេខត្រឹមត្រូវ (ឧ. 5.5):", reply_markup=cancel_inline())


@dp.message(AdminEditItemPrice.reseller_price)
async def adm_edit_price_save(msg: Message, state: FSMContext):
    try:
        res_val = float((msg.text or "").strip().replace("$", ""))
        if res_val < 0:
            raise ValueError
        data = await state.get_data()
        item_id = data["item_id"]
        norm_val = data["price"]
        update_item_prices(item_id, norm_val, res_val)
        item = get_item(item_id)
        plan_name = item["name"] if item else f"ID {item_id}"
        await state.clear()
        await msg.answer(
            f"✅ <b>កែប្រែតម្លៃជោគជ័យ!</b>\n\n"
            f"📌 Plan: <b>{plan_name}</b>\n"
            f"💵 តម្លៃធម្មតាថ្មី: <b>${norm_val:.2f}</b>\n"
            f"🏷 តម្លៃ Reseller ថ្មី: <b>${res_val:.2f}</b>"
        )
    except Exception:
        await msg.answer("❌ សូមបញ្ចូលតម្លៃជាលេខត្រឹមត្រូវ (ឧ. 4.0):", reply_markup=cancel_inline())


@dp.callback_query(F.data == "adm_del_item")
@auto_answer()
async def adm_del_item(call: CallbackQuery):
    items = get_all_items()
    kb = [[IBtn(text=i["name"], callback_data=f"docdelitem_{i['id']}", style="primary")] for i in items]
    kb.append([IBtn(text="Back", callback_data="adm_manage_products",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, "Select plan to delete:",
                             InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("docdelitem_"))
@auto_answer("✅ Deleted!", alert=True)
async def doc_del_item(call: CallbackQuery):
    try:
        del_item(int(call.data.split("_")[1]))
    except (ValueError, IndexError):
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    await bot.send_message(call.message.chat.id, MANAGE_PRODUCTS_TEXT,
                           reply_markup=manage_products_kb())


@dp.callback_query(F.data == "adm_add_cat_media")
@auto_answer()
async def set_cat_media(call: CallbackQuery):
    cats = get_categories()
    kb = [[IBtn(text=c[1], callback_data=f"setcatmd_{c[0]}", style="primary")] for c in cats]
    kb.append([IBtn(text="Back", callback_data="adm_manage_products",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, "Select Product to add Picture/Video:",
                             InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("setcatmd_"))
@auto_answer()
async def set_cat_media2(call: CallbackQuery, state: FSMContext):
    try:
        await state.update_data(cat_id=int(call.data.split("_")[1]))
    except (ValueError, IndexError):
        return
    await safe_edit_or_send(call, "Send a Picture or Video:", cancel_inline())
    await state.set_state(AdminCatMedia.media)


@dp.message(AdminCatMedia.media)
async def set_cat_media3(msg: Message, state: FSMContext):
    cat_id = (await state.get_data()).get('cat_id')
    if msg.photo:
        val = f"photo:{msg.photo[-1].file_id}"
    elif msg.video:
        val = f"video:{msg.video.file_id}"
    else:
        return await msg.answer("❌ Only Picture or Video allowed!", reply_markup=cancel_inline())
    set_category_media(cat_id, val)
    await msg.answer("✅ Saved!")
    await state.clear()


@dp.callback_query(F.data == "adm_del_cat_media")
@auto_answer()
async def del_cat_media(call: CallbackQuery):
    cats = get_categories()
    kb = [[IBtn(text=c[1], callback_data=f"delcatmd_{c[0]}", style="primary")] for c in cats]
    kb.append([IBtn(text="Back", callback_data="adm_manage_products",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, "Select Product to delete Picture/Video:",
                             InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("delcatmd_"))
@auto_answer("✅ Deleted!", alert=True)
async def del_cat_media2(call: CallbackQuery):
    try:
        set_category_media(int(call.data.split("_")[1]), None)
    except (ValueError, IndexError):
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    await bot.send_message(call.message.chat.id, MANAGE_PRODUCTS_TEXT,
                           reply_markup=manage_products_kb())


@dp.callback_query(F.data == "adm_add_post_media")
@auto_answer()
async def set_post_media(call: CallbackQuery):
    items = get_all_items()
    kb = [[IBtn(text=i["name"], callback_data=f"setpostmd_{i['id']}", style="primary")] for i in items]
    kb.append([IBtn(text="Back", callback_data="adm_manage_products",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, "Select Plan to add File/Link (sent after purchase):",
                             InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("setpostmd_"))
@auto_answer()
async def set_post_media2(call: CallbackQuery, state: FSMContext):
    try:
        await state.update_data(item_id=int(call.data.split("_")[1]))
    except (ValueError, IndexError):
        return
    await safe_edit_or_send(call, "Send Video/Picture/File/Text/Voice:", cancel_inline())
    await state.set_state(AdminPostMedia.media)


@dp.message(AdminPostMedia.media)
async def set_post_media3(msg: Message, state: FSMContext):
    item_id = (await state.get_data()).get('item_id')
    if msg.photo:
        val = f"photo:{msg.photo[-1].file_id}"
    elif msg.video:
        val = f"video:{msg.video.file_id}"
    elif msg.document:
        val = f"document:{msg.document.file_id}"
    elif msg.audio:
        val = f"audio:{msg.audio.file_id}"
    elif msg.voice:
        val = f"voice:{msg.voice.file_id}"
    else:
        val = f"text:{msg.html_text or ''}"
    set_item_post_media(item_id, val)
    await msg.answer("✅ Saved!")
    await state.clear()


@dp.callback_query(F.data == "adm_del_post_media")
@auto_answer()
async def del_post_media(call: CallbackQuery):
    items = get_all_items()
    kb = [[IBtn(text=i["name"], callback_data=f"delpostmd_{i['id']}", style="primary")] for i in items]
    kb.append([IBtn(text="Back", callback_data="adm_manage_products",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, "Select Plan to delete File/Link:",
                             InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("delpostmd_"))
@auto_answer("✅ Deleted!", alert=True)
async def del_post_media2(call: CallbackQuery):
    try:
        set_item_post_media(int(call.data.split("_")[1]), None)
    except (ValueError, IndexError):
        return
    try:
        await call.message.delete()
    except Exception:
        pass
    await bot.send_message(call.message.chat.id, MANAGE_PRODUCTS_TEXT,
                           reply_markup=manage_products_kb())

# ====================================================================================================
# [ STOCK SYSTEM ]
# ====================================================================================================

# ✅ កែសម្រួល៖ STOCK PLAN (Product Selection)
@dp.callback_query(F.data == "adm_manage_stock")
@auto_answer()
async def adm_manage_stock(call: CallbackQuery):
    cats = get_categories()
    if not cats:
        return await bot.send_message(call.message.chat.id, "No products yet!")

    kb = []
    for c in cats:
        items = get_items(c[0])
        plan_names = ", ".join([i["name"] for i in items]) if items else "No plans"
        kb.append([IBtn(text=f"{c[1]}: {plan_names}", callback_data=f"stkcat_{c[0]}",
                        style="primary", icon_custom_emoji_id=ICON_ADD_PLAN)])

    kb.append([IBtn(text="Back", callback_data="back_admin",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    
    text = (
        f"<tg-emoji emoji-id=\"{ICON_STOCK_PLAN}\">📦</tg-emoji> <b>STOCK PLAN</b>\n\n"
        f"Select a product :"
    )
    await safe_edit_or_send(call, text, InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("stkcat_"))
@auto_answer()
async def stk_plan_pick(call: CallbackQuery):
    try:
        cat_id = int(call.data.split("_")[1])
    except (ValueError, IndexError):
        return
    cat = get_category(cat_id)
    if not cat:
        return
    items = get_items(cat_id)
    if not items:
        return await bot.send_message(call.message.chat.id, "No plans in this product!")

    kb = [[IBtn(text=i["name"], callback_data=f"stkopt_{i['id']}",
                style="primary", icon_custom_emoji_id=ICON_ADD_PLAN)] for i in items]
    kb.append([IBtn(text="Back", callback_data="adm_manage_stock",
                    style="primary", icon_custom_emoji_id=ICON_BACK)])
    await safe_edit_or_send(call, f"📂 <b>{cat['name']}</b>\n\nSelect a Plan:",
                             InlineKeyboardMarkup(inline_keyboard=kb))


# ✅ កែសម្រួល៖ STOCK PLAN (Plan Details)
@dp.callback_query(F.data.startswith("stkopt_"))
@auto_answer()
async def stk_options(call: CallbackQuery):
    try:
        item_id = int(call.data.split("_")[1])
    except (ValueError, IndexError):
        return
    item = get_item(item_id)
    if not item:
        return
    cat = get_category(item["cat_id"])
    cat_name = cat["name"] if cat else "?"
    current_stock = count_stock(item_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="Add Stock", callback_data=f"addstk_{item_id}", style="primary",
              icon_custom_emoji_id=ICON_ADD_FILE_LINK),
         IBtn(text="View Key", callback_data=f"viewkey_{item_id}", style="primary",
              icon_custom_emoji_id=ICON_ADD_PLAN)],
        [IBtn(text="Back", callback_data=f"stkcat_{item['cat_id']}",
              style="primary", icon_custom_emoji_id=ICON_BACK)]
    ])

    p_norm = float(item.get("price", 0.0))
    p_res = float(item.get("reseller_price", p_norm))

    text = (
        f"<tg-emoji emoji-id=\"{ICON_STOCK_PLAN}\">📦</tg-emoji><b>STOCK PLAN</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id=\"{ICON_STK_PRODUCT}\">🏷</tg-emoji> Product: <b>{cat_name}</b>\n"
        f"<tg-emoji emoji-id=\"{ICON_STK_PLAN}\">📌</tg-emoji> Plan: <b>{item['name']}</b>\n"
        f"<tg-emoji emoji-id=\"{ICON_STK_PRICE}\">💵</tg-emoji> Price: <b>${p_norm:.2f}</b> | "
        f"<tg-emoji emoji-id=\"{ICON_STK_RESELLER}\">🏷</tg-emoji> Reseller: <b>${p_res:.2f}</b>\n"
        f"<tg-emoji emoji-id=\"{ICON_STK_STOCK}\">📊</tg-emoji> Available Stock: <b>{current_stock}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )
    await safe_edit_or_send(call, text, kb)


@dp.callback_query(F.data.startswith("addstk_"))
@auto_answer()
async def add_stock_start(call: CallbackQuery, state: FSMContext):
    try:
        item_id = int(call.data.split("_")[1])
    except (ValueError, IndexError):
        return
    await state.update_data(item_id=item_id)
    text = ("📥 <b>Please send stock now</b>\n"
            "(as files / video / picture / link / text)\n\n"
            "<i>If text: one key per line</i>")
    await safe_edit_or_send(call, text, cancel_inline())
    await state.set_state(AdminAddStock.keys)


@dp.message(AdminAddStock.keys)
async def add_stock_save(msg: Message, state: FSMContext):
    item_id = (await state.get_data()).get('item_id')
    if not item_id:
        await state.clear()
        return
    await state.clear()

    rows: List[Tuple[str, str]] = []
    sample_keys = []

    if msg.photo:
        rows.append((f"photo:{msg.photo[-1].file_id}", 'file'))
        sample_keys.append("(photo file)")
    elif msg.video:
        rows.append((f"video:{msg.video.file_id}", 'file'))
        sample_keys.append("(video file)")
    elif msg.document:
        rows.append((f"document:{msg.document.file_id}", 'file'))
        sample_keys.append("(document file)")
    elif msg.text:
        lines = [L.strip() for L in msg.text.split('\n') if L.strip()]
        for k in lines:
            rows.append((k, 'text'))
            sample_keys.append(k)
    else:
        return await msg.answer("❌ Unsupported format!", reply_markup=cancel_inline())

    added = add_stock_rows_bulk(item_id, rows)

    key_list = "\n".join([f"<code>{k}</code>" for k in sample_keys[:20]])
    if len(sample_keys) > 20:
        key_list += f"\n... and {len(sample_keys)-20} more"

    await msg.answer(f"✅ <b>DONE FOR ADD STOCK {added}!</b>\n\n<b>NAME KEY</b>\n{key_list}")

    item = get_item(item_id)
    if not item:
        return

    cat = get_category(item["cat_id"])
    cat_name = cat["name"] if cat else item["name"]

    alert = (
        f'<tg-emoji emoji-id="6143006459778049445">📢</tg-emoji> '
        f'<b>ស្តុកថ្មីបានមកដល់ហើយ!</b>\n\n'
        f'<tg-emoji emoji-id="6142952094082015848">📦</tg-emoji> • ទំនិញ៖ <b>{cat_name}</b>\n'
        f'<tg-emoji emoji-id="6142952094082015848">📦</tg-emoji> • កញ្ចប់៖ <b>{item["name"]}</b>\n'
        f'<tg-emoji emoji-id="6142952094082015848">📦</tg-emoji> • ចំនួនបន្ថែមថ្មី៖ <b>{added}</b>\n\n'
        f'<tg-emoji emoji-id="5312441427764989435">✅</tg-emoji> '
        f'<b>បានបន្ថែមស្តុបរួចរាល់អាចធ្វើការទិញបាន!</b>\n'
        f'<tg-emoji emoji-id="6129433877791382400">👉</tg-emoji> '
        f'ចុចប៊ូតុងខាងក្រោមដើម្បីទិញ៖'
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[IBtn(
        text="BUY NOW", callback_data="buy_now_direct",
        style="primary", icon_custom_emoji_id="5312361253610475399")]])
    run_bg_task(broadcast_users(alert, kb))


async def _render_view_key(call: CallbackQuery, item_id: int):
    item = get_item(item_id)
    if not item:
        return await bot.send_message(call.message.chat.id, "Item not found.")
    cat = get_category(item["cat_id"])
    cat_name = cat["name"] if cat else "?"

    stocks = get_stock_list(item_id)

    if not stocks:
        text = (f"🔑 <b>VIEW KEY</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🏷 Product: <b>{cat_name}</b>\n"
                f"📌 Plan: <b>{item['name']}</b>\n\n"
                f"❌ <i>No keys in stock.</i>")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [IBtn(text="Back", callback_data=f"stkopt_{item_id}",
                  style="primary", icon_custom_emoji_id=ICON_BACK)]
        ])
        await safe_edit_or_send(call, text, kb)
        return

    key_lines = [s[1] for s in stocks]
    txt_content = "\n".join(key_lines)
    file_bytes = txt_content.encode("utf-8")

    clean_filename = f"{item['name'].replace(' ', '_')}_keys.txt"
    file = BufferedInputFile(file_bytes, filename=clean_filename)

    caption = (
        f"🔑 <b>VIEW ALL KEYS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 Product: <b>{cat_name}</b>\n"
        f"📌 Plan: <b>{item['name']}</b>\n"
        f"📊 Total Keys: <b>{len(stocks)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="Back", callback_data=f"stkopt_{item_id}",
              style="primary", icon_custom_emoji_id=ICON_BACK)]
    ])

    try:
        await call.message.delete()
    except Exception:
        pass

    await bot.send_document(
        chat_id=call.message.chat.id,
        document=file,
        caption=caption,
        reply_markup=kb
    )


@dp.callback_query(F.data.startswith("viewkey_"))
@auto_answer()
async def view_key_handler(call: CallbackQuery):
    try:
        item_id = int(call.data.split("_")[1])
    except (ValueError, IndexError):
        return
    await _render_view_key(call, item_id)

# ====================================================================================================
# [ BROADCAST / RESELLER / ADMINS / MAINTENANCE / START MEDIA ]
# ====================================================================================================

@dp.callback_query(F.data == "adm_broadcast")
@auto_answer()
async def adm_broadcast(call: CallbackQuery, state: FSMContext):
    await safe_edit_or_send(call,
        "✍️ Send a message to broadcast to all users:\n(Link / Video / File / Picture / Voice / Text):",
        cancel_inline())
    await state.set_state(AdminBroadcast.msg)


@dp.message(AdminBroadcast.msg)
async def execute_broadcast(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Sending...")
    run_bg_task(_do_broadcast(msg))


async def _do_broadcast(msg: Message):
    users = db_all_users()
    success, fail = 0, 0
    html_caption = msg.html_text or ""

    for uid_str in users:
        try:
            uid = int(uid_str)
            if msg.text:
                await bot.send_message(uid, html_caption)
            elif msg.photo:
                await bot.send_photo(uid, msg.photo[-1].file_id, caption=html_caption)
            elif msg.video:
                await bot.send_video(uid, msg.video.file_id, caption=html_caption)
            elif msg.document:
                await bot.send_document(uid, msg.document.file_id, caption=html_caption)
            elif msg.audio:
                await bot.send_audio(uid, msg.audio.file_id, caption=html_caption)
            elif msg.voice:
                await bot.send_voice(uid, msg.voice.file_id, caption=html_caption)
            success += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            fail += 1
        except TelegramForbiddenError:
            fail += 1
        except Exception as e:
            logger.warning(f"Broadcast to {uid_str} failed: {e}")
            fail += 1
        await asyncio.sleep(0.05)

    try:
        await bot.send_message(
            msg.chat.id,
            f"📊 Sent to <b>{success+fail}</b> users \n"
            f"- ✅ Success: <b>{success}</b> \n- ❌ Failed: <b>{fail}</b>")
    except Exception:
        pass


# ----- MANAGE RESELLER -----

@dp.callback_query(F.data == "adm_reseller")
@auto_answer()
async def adm_reseller(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="View Resellers (Total Buy)", callback_data="view_resellers",
              style="success", icon_custom_emoji_id=ICON_RES_TITLE)],
        [IBtn(text="Add Reseller", callback_data="add_reseller", style="primary",
              icon_custom_emoji_id=ICON_CREATE_PRODUCT),
         IBtn(text="Del Reseller", callback_data="del_reseller", style="danger",
              icon_custom_emoji_id=ICON_DELETE_PRODUCT)],
        [IBtn(text="Back", callback_data="back_admin", style="primary",
              icon_custom_emoji_id=ICON_BACK)]
    ])
    await safe_edit_or_send(call, "<b>Manage Reseller</b>\n\nChoose an option:", kb)


# ✅ កែសម្រួល៖ RESELLER LIST
@dp.callback_query(F.data == "view_resellers")
@auto_answer()
async def view_resellers(call: CallbackQuery):
    resellers = db_get_resellers()
    if not resellers:
        return await bot.send_message(call.message.chat.id, "❌ No resellers yet.")

    lines = [
        f"<tg-emoji emoji-id=\"{ICON_RES_TITLE}\">👥</tg-emoji><b>RESELLER LIST</b>",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
    grand_orders = 0
    grand_money = 0.0

    for idx, r in enumerate(resellers, 1):
        uname = f"@{r['username']}" if r["username"] else "—"
        lines.append(
            f"\n<b>{idx}. {r['first_name']}</b>\n"
            f"   <tg-emoji emoji-id=\"{ICON_RES_ID}\">🆔</tg-emoji> ID: <code>{r['id']}</code>\n"
            f"   <tg-emoji emoji-id=\"{ICON_RES_USER}\">📛</tg-emoji> User: {uname}\n"
            f"   <tg-emoji emoji-id=\"{ICON_RES_ORDERS}\">📦</tg-emoji> Orders: <b>{r['total_orders']}</b>\n"
            f"   <tg-emoji emoji-id=\"{ICON_RES_PRICE}\">💵</tg-emoji> Total Buy: <b>${r['total_spent']:.2f}</b>"
        )
        grand_orders += r["total_orders"]
        grand_money += r["total_spent"]

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<tg-emoji emoji-id=\"{ICON_RES_TITLE}\">👥</tg-emoji> <b>Total Resellers:</b> <b>{len(resellers)}</b>")
    lines.append(f"<tg-emoji emoji-id=\"{ICON_RES_ORDERS}\">📦</tg-emoji> <b>Total Buy (Orders):</b> <b>{grand_orders}</b>")
    lines.append(f"<tg-emoji emoji-id=\"{ICON_RES_TOTAL_MONEY}\">💰</tg-emoji> <b>Total Buy (Money):</b> <b>${grand_money:.2f}</b>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n\n<i>... truncated</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="Refresh", callback_data="view_resellers", style="primary",
              icon_custom_emoji_id=ICON_REFRESH),
         IBtn(text="Back", callback_data="adm_reseller", style="primary",
              icon_custom_emoji_id=ICON_BACK)]
    ])
    await safe_edit_or_send(call, text, kb)


@dp.callback_query(F.data == "add_reseller")
@auto_answer()
async def add_reseller_st(call: CallbackQuery, state: FSMContext):
    await safe_edit_or_send(call, "Enter User ID to add as Reseller:", cancel_inline())
    await state.set_state(AdminReseller.add_id)


@dp.message(AdminReseller.add_id)
async def add_reseller_save(msg: Message, state: FSMContext):
    try:
        rid = int((msg.text or "").strip())
        db_upsert_user(rid, is_reseller=1)
        await msg.answer(f"✅ User <code>{rid}</code> is now a Reseller (ទទួលបានតម្លៃពិសេស)!")
    except ValueError:
        await msg.answer("❌ Invalid ID.")
    except Exception as e:
        await msg.answer(f"❌ Error: {e}")
    await state.clear()


@dp.callback_query(F.data == "del_reseller")
@auto_answer()
async def del_reseller_st(call: CallbackQuery, state: FSMContext):
    await safe_edit_or_send(call, "Enter User ID to remove from Reseller:", cancel_inline())
    await state.set_state(AdminReseller.del_id)


@dp.message(AdminReseller.del_id)
async def del_reseller_save(msg: Message, state: FSMContext):
    try:
        rid = int((msg.text or "").strip())
        db_upsert_user(rid, is_reseller=0)
        await msg.answer(f"✅ User <code>{rid}</code> removed from Reseller.")
    except ValueError:
        await msg.answer("❌ Invalid ID.")
    except Exception as e:
        await msg.answer(f"❌ Error: {e}")
    await state.clear()


# ====================================================================================================
# [ 👑 MANAGE ADMINS SYSTEM ]
# ====================================================================================================

@dp.callback_query(F.data == "adm_manage_admins")
@auto_answer()
async def adm_manage_admins(call: CallbackQuery):
    await safe_edit_or_send(call, "<b>MANAGE ADMINS</b>\n\nសូមជ្រើសរើសជម្រើសខាងក្រោម៖",
                             manage_admins_kb())


@dp.callback_query(F.data == "adm_view_admins")
@auto_answer()
async def adm_view_admins(call: CallbackQuery):
    admins = db_get_admins()
    lines = [
        f"<tg-emoji emoji-id=\"{ICON_VIEW_ADMINS}\"></tg-emoji> <b>បញ្ជីរាយនាម ADMIN ទាំងអស់</b>",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]
    for idx, a in enumerate(admins, 1):
        uname = f"@{a['username']}" if a["username"] else "—"
        role = " <b>Owner</b>" if a["is_owner"] else "<b>Admin</b>"
        lines.append(
            f"<b>{idx}. {a['first_name']}</b> ({role})\n"
            f"   🆔 ID: <code>{a['id']}</code>\n"
            f"   📛 User: {uname}\n"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"👥 <b>សរុប Admin:</b> <b>{len(admins)}</b> នាក់")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [IBtn(text="🔄 Refresh", callback_data="adm_view_admins", style="primary"),
         IBtn(text="Back", callback_data="adm_manage_admins", style="primary",
              icon_custom_emoji_id=ICON_BACK)]
    ])
    await safe_edit_or_send(call, "\n".join(lines), kb)


@dp.callback_query(F.data == "adm_add_admin")
@auto_answer()
async def adm_add_admin_st(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await quick_answer(call, "⚠️ មានតែ Owner ប៉ុណ្ណោះដែលអាចតែងតាំង Admin បាន!", show_alert=True)
    text = (f"<tg-emoji emoji-id=\"{ICON_ADD_ADMIN}\">➕</tg-emoji> "
            f"<b>Add Admin:</b>\n\nសូមផ្ញើ <b>User ID</b> របស់បុគ្គលដែលអ្នកចង់តែងតាំងជា Admin:")
    await safe_edit_or_send(call, text, cancel_inline())
    await state.set_state(AdminAdmin.add_id)


@dp.message(AdminAdmin.add_id)
async def adm_add_admin_save(msg: Message, state: FSMContext):
    try:
        target_id = int((msg.text or "").strip())
        db_upsert_user(target_id, is_admin=1)
        await msg.answer(f"✅ User ID <code>{target_id}</code> ត្រូវបានតែងតាំងជា <b>Admin</b> ដោយជោគជ័យ!")
    except ValueError:
        await msg.answer("❌ Invalid ID! សូមបញ្ចូល User ID ជាលេខត្រឹមត្រូវ។")
    except Exception as e:
        await msg.answer(f"❌ Error: {e}")
    await state.clear()


@dp.callback_query(F.data == "adm_del_admin")
@auto_answer()
async def adm_del_admin_st(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID:
        return await quick_answer(call, "មានតែ Owner ប៉ុណ្ណោះដែលអាចដកសិទ្ធិ Admin បាន!", show_alert=True)
    text = (f"<tg-emoji emoji-id=\"{ICON_DEL_ADMIN}\">🗑️</tg-emoji> "
            f"<b>Del Admin:</b>\n\nសូមផ្ញើ <b>User ID</b> របស់ Admin ដែលអ្នកចង់ដកសិទ្ធិចេញ (មិនអាចដក Owner បានទេ):")
    await safe_edit_or_send(call, text, cancel_inline())
    await state.set_state(AdminAdmin.del_id)


@dp.message(AdminAdmin.del_id)
async def adm_del_admin_save(msg: Message, state: FSMContext):
    try:
        target_id = int((msg.text or "").strip())
        if target_id == OWNER_ID:
            await state.clear()
            return await msg.answer("❌ <b>មិនអាចដកសិទ្ធិ Owner បានទេ!</b>")
        db_upsert_user(target_id, is_admin=0)
        await msg.answer(f"✅ User ID <code>{target_id}</code> ត្រូវបានដកសិទ្ធិពី Admin រួចរាល់។")
    except ValueError:
        await msg.answer("❌ Invalid ID! សូមបញ្ចូល User ID ជាលេខត្រឹមត្រូវ។")
    except Exception as e:
        await msg.answer(f"❌ Error: {e}")
    await state.clear()


# ====================================================================================================
# [ 🛠️ MAINTENANCE SYSTEM ]
# ====================================================================================================

@dp.callback_query(F.data == "adm_maintenance")
@auto_answer()
async def adm_maintenance_menu(call: CallbackQuery):
    text, kb = maintenance_kb()
    await safe_edit_or_send(call, text, kb)


@dp.callback_query(F.data == "adm_maint_open")
@auto_answer("✅ Bot ត្រូវបានបើកដំណើរការធម្មតាវិញ!", alert=True)
async def adm_maint_open_handler(call: CallbackQuery):
    db_set_setting("maintenance", "0")
    text, kb = maintenance_kb()
    await safe_edit_or_send(call, text, kb)


@dp.callback_query(F.data == "adm_maint_close")
@auto_answer("🔴 Bot ត្រូវបានបិទ (Maintenance Mode)!", alert=True)
async def adm_maint_close_handler(call: CallbackQuery):
    db_set_setting("maintenance", "1")
    text, kb = maintenance_kb()
    await safe_edit_or_send(call, text, kb)


# ----- START MEDIA -----

@dp.callback_query(F.data == "adm_start_media")
@auto_answer()
async def adm_start_media(call: CallbackQuery, state: FSMContext):
    await safe_edit_or_send(call,
        "Send Video or Picture for /start (or type 'del' to remove):",
        cancel_inline())
    await state.set_state(AdminStartMedia.media)


@dp.message(AdminStartMedia.media)
async def save_start_media(msg: Message, state: FSMContext):
    if msg.text and msg.text.lower() == 'del':
        data = _load_db()
        data["settings"].pop("start_media", None)
        _save_db(data)
    elif msg.photo:
        db_set_setting('start_media', f"photo:{msg.photo[-1].file_id}")
    elif msg.video:
        db_set_setting('start_media', f"video:{msg.video.file_id}")
    await msg.answer("✅ Saved!")
    await state.clear()


@dp.callback_query(F.data == "ignore")
async def ignore_cb(call: CallbackQuery):
    await quick_answer(call)


# ====================================================================================================
# [ MAIN ]
# ====================================================================================================

async def main():
    logger.info("🚀 BOT IS RUNNING...")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning(f"delete_webhook failed: {e}")

    try:
        await dp.start_polling(
            bot,
            polling_timeout=30,
            backoff=0.02,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await close_http()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
