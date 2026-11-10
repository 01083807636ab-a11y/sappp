"""냉장고 식재료 알림 | 실행: streamlit run app.py
SQLite 로컬 저장. Streamlit Community Cloud 로컬 디스크는 영구 저장소가 아닙니다.
"""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="냉장고 식재료 알림", page_icon="🥬", layout="wide")
DB_PATH = Path(os.environ.get("FRIDGE_DB_PATH", "fridge_data.sqlite3"))
UNITS = ["개", "g", "kg", "ml", "L", "봉", "팩", "병"]
LOCATIONS = ["냉장", "냉동", "실온"]
SOURCES = ["식재료 직접 등록", "영수증 보고 입력"]


def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS receipt_categories(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, category TEXT NOT NULL,
            source TEXT NOT NULL, receipt_category TEXT NOT NULL DEFAULT '',
            receipt_ref TEXT NOT NULL DEFAULT '', quantity REAL NOT NULL CHECK(quantity >= 0),
            unit TEXT NOT NULL, location TEXT NOT NULL,
            purchase_date TEXT NOT NULL, expiry_date TEXT NOT NULL,
            memo TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id),
            item_name TEXT NOT NULL, action TEXT NOT NULL,
            quantity REAL NOT NULL CHECK(quantity > 0), unit TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        """)
        for n in ["채소", "과일", "육류", "수산물", "유제품·달걀", "반찬·가공식품", "양념", "기타"]:
            c.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (n,))
        for n in ["마트", "온라인 장보기", "시장", "편의점", "기타"]:
            c.execute("INSERT OR IGNORE INTO receipt_categories(name) VALUES(?)", (n,))


def records(table):
    # Table names are internal constants, never user input.
    if table not in {"items", "logs", "categories", "receipt_categories"}:
        raise ValueError("허용되지 않은 테이블")
    with connect() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")]


def names(table):
    return [r["name"] for r in records(table)]


def valid_text(value, label):
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        raise ValueError(f"{label}을(를) 입력해 주세요.")
    if len(text) > 500:
        raise ValueError(f"{label}은(는) 500자 이내로 입력해 주세요.")
    return text


def normalize(row):
    q = float(row["수량"])
    if not 0 < q <= 1_000_000:
        raise ValueError("수량은 0 초과 1,000,000 이하여야 합니다.")
    bought = pd.to_datetime(row["구매일"], errors="raise").date()
    expiry = pd.to_datetime(row["기한"], errors="raise").date()
    if pd.isna(bought) or pd.isna(expiry):
        raise ValueError("구매일과 기한을 입력해 주세요.")
    if expiry < bought:
        raise ValueError("기한은 구매일보다 빠를 수 없습니다.")
    cat = valid_text(row["식재료 분류"], "식재료 분류")
    src = valid_text(row["입력 방식"], "입력 방식")
    unit = valid_text(row["단위"], "단위")
    loc = valid_text(row["보관 장소"], "보관 장소")
    rc = str(row.get("영수증 분류", "") or "").strip()
    if cat not in names("categories") or src not in SOURCES or unit not in UNITS or loc not in LOCATIONS:
        raise ValueError("분류·입력 방식·단위·보관 장소를 선택지에서 골라 주세요.")
    if src == SOURCES[1] and rc not in names("receipt_categories"):
        raise ValueError("영수증 분류를 선택해 주세요.")
    return (valid_text(row["상품명"], "상품명"), cat, src, rc,
            str(row.get("영수증 메모", "") or ""), q, unit, loc,
            bought.isoformat(), expiry.isoformat(), str(row.get("메모", "") or ""))


COLUMNS = ["상품명", "식재료 분류", "입력 방식", "영수증 분류", "영수증 메모", "수량", "단위", "보관 장소", "구매일", "기한", "메모"]
DB_FIELDS = ["name", "category", "source", "receipt_category", "receipt_ref", "quantity", "unit", "location", "purchase_date", "expiry_date", "memo"]


def editor_config():
    return {
        "식재료 분류": st.column_config.SelectboxColumn(options=names("categories"), required=True),
        "입력 방식": st.column_config.SelectboxColumn(options=SOURCES, required=True),
        "영수증 분류": st.column_config.SelectboxColumn(options=[""] + names("receipt_categories")),
        "단위": st.column_config.SelectboxColumn(options=UNITS, required=True),
        "보관 장소": st.column_config.SelectboxColumn(options=LOCATIONS, required=True),
        "수량": st.column_config.NumberColumn(min_value=0.001, max_value=1_000_000.0, required=True),
        "구매일": st.column_config.DateColumn(required=True),
        "기한": st.column_config.DateColumn(required=True),
    }


def change_stock(item_id, action, amount, reason):
    if action not in {"사용", "폐기"} or not 0 < amount <= 1_000_000:
        raise ValueError("처리 구분 또는 수량이 올바르지 않습니다.")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        item = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not item or amount > item["quantity"]:
            raise ValueError("현재 재고보다 많은 수량을 처리할 수 없습니다. 새로고침 후 확인해 주세요.")
        c.execute("UPDATE items SET quantity=MAX(0, ROUND(quantity-?, 9)) WHERE id=?", (amount, item_id))
        c.execute("INSERT INTO logs(item_id,item_name,action,quantity,unit,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                  (item_id,item["name"],action,amount,item["unit"],reason,datetime.now().isoformat(timespec="seconds")))


def make_backup():
    with connect() as c:
        c.execute("BEGIN")
        data = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")]
                for t in ["categories", "receipt_categories", "items", "logs"]}
    return json.dumps({"version": 1, "exported_at": datetime.now().isoformat(), "data": data}, ensure_ascii=False, indent=2)


def restore_backup(raw):
    if len(raw) > 10_000_000:
        raise ValueError("백업은 10MB 이하여야 합니다.")
    obj = json.loads(raw)
    if obj.get("version") != 1 or not isinstance(obj.get("data"), dict):
        raise ValueError("지원하지 않는 백업 형식입니다.")
    data = obj["data"]
    specs = {
        "categories": ["id", "name"], "receipt_categories": ["id", "name"],
        "items": ["id"] + DB_FIELDS + ["created_at"],
        "logs": ["id", "item_id", "item_name", "action", "quantity", "unit", "reason", "created_at"]}
    for table, fields in specs.items():
        rows = data.get(table)
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ValueError("백업 테이블이 잘못되었습니다.")
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(fields):
                raise ValueError("백업의 필드가 일치하지 않습니다.")
    if not data["categories"] or not data["receipt_categories"]:
        raise ValueError("분류 목록이 비어 있습니다.")
    for item in data["items"]:
        quantity = float(item["quantity"])
        if not 0 <= quantity <= 1_000_000:
            raise ValueError("잘못된 재고 수량입니다.")
        date.fromisoformat(item["purchase_date"])
        date.fromisoformat(item["expiry_date"])
        if item["unit"] not in UNITS or item["location"] not in LOCATIONS:
            raise ValueError("잘못된 단위 또는 보관 장소입니다.")
    for log in data["logs"]:
        if log["action"] not in {"사용", "폐기"} or not 0 < float(log["quantity"]) <= 1_000_000:
            raise ValueError("잘못된 이력입니다.")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM logs")
        c.execute("DELETE FROM items")
        c.execute("DELETE FROM categories")
        c.execute("DELETE FROM receipt_categories")
        for table, fields in specs.items():
            sql = f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})"
            c.executemany(sql, [[r[k] for k in fields] for r in data[table]])



initialize()
st.title("🥬 냉장고 식재료 알림")
st.caption("재료를 등록하고, 기한을 확인하고, 사용하면 끝!")
if 'notice' in st.session_state:
    st.success(st.session_state.pop('notice'))

with st.expander("⚙️ 설정 · 백업", expanded=False):
    days = st.number_input("기한 며칠 전부터 알려줄까요?", 0, 30, 3)
    st.download_button("전체 데이터 백업", make_backup(), "fridge_backup.json", "application/json")
    st.caption("기존 앱과 같은 데이터베이스·백업 형식을 사용합니다. 클라우드 재배포 전에 백업하세요.")
    uploaded = st.file_uploader("백업 복원", type=['json'])
    confirmed = st.checkbox("복원하면 현재 데이터가 백업 내용으로 교체됨을 확인했어요")
    if st.button("복원하기", disabled=not (uploaded and confirmed)):
        try:
            restore_backup(uploaded.getvalue())
            st.session_state.notice = "백업을 복원했어요."
            st.rerun()
        except (ValueError, KeyError, TypeError, sqlite3.Error) as e:
            st.error(str(e))

st.subheader("➕ 재료 등록")
with st.form("add", clear_on_submit=True):
    name = st.text_input("재료 이름", placeholder="예: 우유, 달걀, 당근")
    a,b = st.columns(2)
    expiry = a.date_input("제품에 표시된 기한", date.today()+timedelta(days=7))
    quantity = b.number_input("수량", min_value=0.001, max_value=1000000.0, value=1.0, step=1.0)
    with st.expander("추가 정보 (선택)"):
        a,b,c = st.columns(3)
        unit = a.selectbox("단위", UNITS)
        location = b.selectbox("보관 장소", LOCATIONS)
        category = c.selectbox("분류", names('categories'))
        source = st.selectbox("입력 방식", SOURCES)
        receipt = st.selectbox("구매처 · 영수증 분류", names('receipt_categories'))
    submitted = st.form_submit_button("등록하기", type="primary", use_container_width=True)
    if submitted:
        try:
            clean_name = valid_text(name, '재료 이름')
            with connect() as conn:
                conn.execute("INSERT INTO items (" + ','.join(DB_FIELDS) + ",created_at) VALUES (" + ','.join(['?']*12) + ")", (clean_name, category, source, receipt if source==SOURCES[1] else '', '', quantity, unit, location, date.today().isoformat(), expiry.isoformat(), '', datetime.now().isoformat(timespec='seconds')))
            st.session_state.notice = f"{clean_name} 등록 완료!"
            st.rerun()
        except (ValueError, sqlite3.Error) as e:
            st.error(str(e))

items = [i for i in records('items') if i['quantity']>0]
today = date.today()
items.sort(key=lambda i:(i['expiry_date'],i['id']))
overdue = [i for i in items if date.fromisoformat(i['expiry_date'])<today]
soon = [i for i in items if 0 <= (date.fromisoformat(i['expiry_date'])-today).days <= days]
if overdue:
    st.error('기한 지남: ' + ', '.join(i['name'] for i in overdue))
if soon:
    st.warning('곧 기한 도래: ' + ', '.join(i['name'] for i in soon))
st.subheader(f"🧊 냉장고 재료 {len(items)}종")
search = st.text_input("재료 검색", placeholder="찾는 재료 이름")
if not items:
    st.info("냉장고가 비어 있어요. 위에서 첫 재료를 등록해 보세요.")
for item in items:
    if search.strip().casefold() not in item['name'].casefold():
        continue
    diff = (date.fromisoformat(item['expiry_date'])-today).days
    badge = f"🔴 {abs(diff)}일 지남" if diff<0 else ('🟠 오늘까지' if diff==0 else f"{'🟡' if diff<=days else '🟢'} {diff}일 남음")
    with st.container(border=True):
        st.markdown(f"**{item['name']}** · {item['quantity']:g}{item['unit']}")
        st.caption(f"{badge} | {item['expiry_date']} | {item['location']}")
        a,b = st.columns(2)
        if a.button("✅ 전부 사용", key=f"use{item['id']}", use_container_width=True):
            try:
                change_stock(item['id'],'사용',item['quantity'],'')
                st.session_state.notice = '사용 기록을 저장했어요.'
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        with b.popover("수정 · 일부 사용 · 폐기", use_container_width=True):
            with st.form(f"manage{item['id']}"):
                new_name = st.text_input("재료 이름", item['name'])
                new_date = st.date_input("기한", date.fromisoformat(item['expiry_date']))
                action = st.selectbox("할 일", ['정보 수정','일부 사용','폐기'])
                amount = st.number_input("처리 수량 (사용·폐기할 때)", min_value=0.001, max_value=float(item['quantity']), value=float(item['quantity']), step=0.001, format='%g')
                if st.form_submit_button("적용"):
                    try:
                        if action=='정보 수정':
                            cleaned = valid_text(new_name,'재료 이름')
                            with connect() as conn:
                                conn.execute('UPDATE items SET name=?, expiry_date=? WHERE id=?',(cleaned,new_date.isoformat(),item['id']))
                        else:
                            change_stock(item['id'],'사용' if action=='일부 사용' else '폐기',amount,'')
                        st.session_state.notice = '저장했어요.'
                        st.rerun()
                    except (ValueError, sqlite3.Error) as e:
                        st.error(str(e))
with st.expander("📋 사용 · 폐기 기록"):
    logs = list(reversed(records('logs')))
    if logs:
        df = pd.DataFrame(logs)[['created_at','item_name','action','quantity','unit']]
        df.columns = ['처리 시간','재료','구분','수량','단위']
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.caption('아직 기록이 없어요.')
st.caption("알림은 앱을 열거나 새로고침할 때 표시됩니다. 기한만으로 섭취 안전성을 보장하지 않습니다.")
st.caption("로그인 없는 공유 앱은 방문자들이 같은 재고를 봅니다. 클라우드 로컬 저장은 유실될 수 있으며 GitHub에 데이터가 자동 저장되지는 않습니다.")
