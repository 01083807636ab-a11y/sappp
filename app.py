"""냉장고 식재료 알 | 실행: streamlit run app.py
SQLite 로컬 저장. Streamlit Community Cloud 로컬 디스크는 영구 저장소가 아닙니다.
"""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="냉장고 식재료 알", page_icon="🥬", layout="wide")
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
st.markdown("""<style>
.stApp {background-color:#f7f9f5;} h1,h2,h3 {color:#244d38;}
[data-testid="stMetric"] {background:white;border:1px solid #dde8de;border-radius:14px;padding:16px;}
.stButton>button[kind="primary"] {background:#28734b;border-color:#28734b;}
</style>""", unsafe_allow_html=True)
st.title("🥬 냉장고 식재료 알")
st.caption("무엇이 있는지 한눈에 · 기한 전에 맛있게 · 사용과 폐기까지 기록")
if "notice" in st.session_state:
    st.success(st.session_state.pop("notice"))
st.info("가족 한 가구용 공유 재고입니다. 공개 배포하면 방문자 모두 같은 데이터를 보고 수정할 수 있으므로 비공개 접근 설정을 권장합니다.")

with st.expander("⚙️ 분류 관리 · 저장 안내 · 백업/복원"):
    st.caption("식재료 분류는 채소·육류 등의 품목군, 영수증 분류는 마트·온라인 등의 구매 경로입니다. 영수증은 직접 읽어 입력하며 사진 인식 기능은 포함하지 않습니다.")
    with st.form("category_add", clear_on_submit=True):
        a,b = st.columns(2)
        target = a.selectbox("추가할 분류 종류", ["식재료 분류", "영수증 분류"])
        new_category = b.text_input("새 분류명", max_chars=40)
        if st.form_submit_button("분류 등록"):
            try:
                n = valid_text(new_category, "분류명")
                table = "categories" if target == "식재료 분류" else "receipt_categories"
                with connect() as c:
                    c.execute(f"INSERT INTO {table}(name) VALUES(?)", (n,))
                st.session_state.notice = "분류를 등록했습니다."
                st.rerun()
            except (ValueError, sqlite3.Error) as e:
                st.error(f"등록하지 못했습니다: {e}")
    st.write("식재료: " + " · ".join(names("categories")))
    st.write("영수증: " + " · ".join(names("receipt_categories")))
    st.warning("자동 저장은 SQLite 파일 기준입니다. Streamlit Cloud의 재시작·재배포 시 로컬 데이터가 유실될 수 있습니다. 주기적으로 백업하세요. 상시 영구 보관은 외부 DB 연동이 필요합니다.")
    st.download_button("📥 전체 데이터 백업(JSON)", make_backup(), file_name=f"fridge_backup_{date.today()}.json", mime="application/json")
    with st.form("restore"):
        uploaded = st.file_uploader("이 앱에서 받은 백업 JSON", type=["json"])
        confirmed = st.checkbox("기존 재고와 이력을 모두 백업 내용으로 교체하는 데 동의합니다.")
        if st.form_submit_button("백업 복원"):
            if not confirmed or uploaded is None:
                st.error("백업 파일과 교체 동의를 확인해 주세요.")
            else:
                try:
                    restore_backup(uploaded.getvalue())
                    st.session_state.pop("draft", None)
                    st.session_state["editor_revision"] = st.session_state.get("editor_revision", 0) + 1
                    st.session_state.notice = "백업을 복원했습니다."
                    st.rerun()
                except (ValueError, TypeError, KeyError, sqlite3.Error) as e:
                    st.error(f"복원하지 못했습니다. 기존 데이터는 유지됩니다: {e}")

items = records("items")
active = [i for i in items if i["quantity"] > 0]
today = date.today()
c1,c2,c3 = st.columns([2,1,1])
with c1:
    st.subheader("🔔 오늘의 냉장고")
with c2:
    days = st.number_input("임박 알림 기준(일)", min_value=0, max_value=30, value=3)
with c3:
    if st.button("↻ 재고 새로고침", use_container_width=True):
        st.rerun()
expired = [i for i in active if date.fromisoformat(i["expiry_date"]) < today]
soon = [i for i in active if 0 <= (date.fromisoformat(i["expiry_date"]) - today).days <= days]
m1,m2,m3,m4 = st.columns(4)
m1.metric("보유 품목", f"{len(active)}건")
m2.metric("기한 임박", f"{len(soon)}건")
m3.metric("기한 경과", f"{len(expired)}건")
logs = records("logs")
m4.metric("사용 / 폐기 기록", f"{sum(x['action']=='사용' for x in logs)} / {sum(x['action']=='폐기' for x in logs)}건")
if expired:
    st.error("기한 경과: " + ", ".join(f"{i['name']} ({i['expiry_date']})" for i in expired))
if soon:
    st.warning("먼저 확인하세요: " + ", ".join(f"{i['name']} (D-{(date.fromisoformat(i['expiry_date'])-today).days})" for i in soon))
if not expired and not soon:
    st.success("설정한 기준 내에 기한이 임박하거나 지난 식재료가 없습니다.")
st.caption("알림은 앱을 열거나 새로고침할 때 계산되는 화면 내 알림입니다. 앱 종료 중 푸시·문자·이메일은 발송하지 않습니다. 기한만으로 식품의 안전성을 판정할 수 없습니다.")

st.divider()
st.subheader("① 식재료 입력 → 확인 → 저장")
with st.form("add_item", clear_on_submit=False):
    a,b,c = st.columns(3)
    name = a.text_input("상세 상품명 *", placeholder="예: 서울우유 저지방 1L", max_chars=150)
    category = b.selectbox("식재료 분류", names("categories"))
    source = c.selectbox("입력 방식", SOURCES)
    a,b,c = st.columns(3)
    receipt_category = a.selectbox("영수증 분류", names("receipt_categories"))
    receipt_ref = b.text_input("매장명 / 영수증 메모", placeholder="예: 동네마트, 토요일 장보기", max_chars=150)
    location = c.selectbox("보관 장소", LOCATIONS)
    a,b,c,d = st.columns(4)
    quantity = a.number_input("수량", min_value=0.001, max_value=1_000_000.0, value=1.0, step=1.0)
    unit = b.selectbox("단위", UNITS)
    bought = c.date_input("구매일", value=today)
    expiry = d.date_input("소비기한 / 관리 기한", value=today + timedelta(days=7))
    memo = st.text_input("보관 메모", placeholder="예: 개봉 후 3일 이내 확인", max_chars=300)
    if st.form_submit_button("확인 목록에 추가", type="primary"):
        row = dict(zip(COLUMNS, [name, category, source, receipt_category if source == SOURCES[1] else "", receipt_ref, quantity, unit, location, bought, expiry, memo]))
        try:
            normalize(row)
            st.session_state.setdefault("draft", []).append(row)
            st.session_state["editor_revision"] = st.session_state.get("editor_revision", 0) + 1
            st.success("아래 확인 목록에 추가했습니다. 확인 후 저장을 눌러 주세요.")
        except (ValueError, TypeError) as e:
            st.error(str(e))

if st.session_state.get("draft"):
    st.caption("아래 표를 직접 수정할 수 있습니다. 삭제 체크 후 ‘확인한 식재료 저장’을 누르면 체크한 행은 제외됩니다. 같은 상품도 구매 묶음별로 따로 저장합니다.")
    draft = pd.DataFrame(st.session_state.draft)
    draft.insert(0, "제외", False)
    edited = st.data_editor(draft, column_config=editor_config(), hide_index=True, use_container_width=True, key=f"draft_editor_{st.session_state.get('editor_revision', 0)}")
    a,b = st.columns([1,3])
    if a.button("확인한 식재료 저장", type="primary"):
        try:
            rows = [normalize(r) for r in edited.to_dict("records") if not r["제외"]]
            if not rows:
                raise ValueError("저장할 행이 없습니다.")
            with connect() as c:
                c.executemany("INSERT INTO items(" + ",".join(DB_FIELDS) + ",created_at) VALUES(" + ",".join("?" for _ in range(12)) + ")",
                              [r + (datetime.now().isoformat(timespec="seconds"),) for r in rows])
            st.session_state.draft = []
            st.session_state.notice = f"{len(rows)}개 품목을 저장했습니다."
            st.rerun()
        except (ValueError, TypeError, sqlite3.Error) as e:
            st.error(f"저장하지 못했습니다: {e}")
    if b.button("확인 목록 비우기"):
        st.session_state.draft = []
        st.rerun()

st.divider()
st.subheader("② 재고 목록 · 정보 수정")
a,b,c,d = st.columns(4)
query = a.text_input("상품명 검색", placeholder="상품명 일부")
cat_filter = b.selectbox("분류 필터", ["전체"] + names("categories"))
loc_filter = c.selectbox("보관 필터", ["전체"] + LOCATIONS)
status_filter = d.selectbox("기한 필터", ["전체", "기한 임박", "기한 경과", "기한 여유"])
view=[]
for i in active:
    delta=(date.fromisoformat(i["expiry_date"])-today).days
    status="기한 경과" if delta < 0 else "기한 임박" if delta <= days else "기한 여유"
    if query.casefold() not in i["name"].casefold() or (cat_filter != "전체" and i["category"] != cat_filter) or (loc_filter != "전체" and i["location"] != loc_filter) or (status_filter != "전체" and status != status_filter):
        continue
    view.append({"ID":i["id"], "상품명":i["name"], "분류":i["category"], "잔여 수량":i["quantity"], "단위":i["unit"], "보관":i["location"], "기한":i["expiry_date"], "남은 일수":delta, "상태":status, "입력 방식":i["source"], "영수증 분류":i["receipt_category"], "메모":i["memo"]})
if view:
    frame=pd.DataFrame(view).sort_values(["기한", "상품명"])
    st.dataframe(frame, hide_index=True, use_container_width=True)
    st.download_button("현재 조회 결과 CSV", frame.to_csv(index=False).encode("utf-8-sig"), "fridge_inventory.csv", "text/csv")
else:
    st.info("조회 조건에 맞는 재고가 없습니다. 식재료를 등록하거나 필터를 바꿔 주세요.")

if active:
    by_id={i["id"]: i for i in active}
    with st.expander("✏️ 저장한 상품 정보 수정 (수량은 아래 사용·폐기로 변경)"):
        selected=st.selectbox("수정할 품목", list(by_id), format_func=lambda k:f"#{k} {by_id[k]['name']} · {by_id[k]['expiry_date']}", key="edit_id")
        i=by_id[selected]
        with st.form(f"edit_{selected}_{st.session_state.get('editor_revision', 0)}"):
            base=dict(zip(COLUMNS, [i[k] for k in DB_FIELDS]))
            base["구매일"]=date.fromisoformat(base["구매일"])
            base["기한"]=date.fromisoformat(base["기한"])
            changed=st.data_editor(pd.DataFrame([base]), disabled=["수량", "단위"], column_config=editor_config(), hide_index=True, use_container_width=True, key=f"saved_{selected}_{st.session_state.get('editor_revision', 0)}")
            st.caption("사용·폐기 이력의 일관성을 위해 저장된 수량과 단위는 이 표에서 수정하지 않습니다. 잘못 등록한 수량은 사유를 적어 폐기 처리한 뒤 재등록해 주세요.")
            if st.form_submit_button("상품 정보 수정 저장"):
                try:
                    vals=normalize(changed.to_dict("records")[0])
                    # Never overwrite quantity/unit: another session may have consumed stock.
                    fields=[k for k in DB_FIELDS if k not in {"quantity", "unit"}]
                    updates=[v for k,v in zip(DB_FIELDS, vals) if k in fields]
                    with connect() as conn:
                        conn.execute("UPDATE items SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?", updates+[selected])
                    st.session_state["editor_revision"]=st.session_state.get("editor_revision",0)+1
                    st.session_state.notice="상품 정보를 수정했습니다."
                    st.rerun()
                except (ValueError, TypeError, sqlite3.Error) as e:
                    st.error(f"수정하지 못했습니다: {e}")

st.divider()
st.subheader("③ 사용 · 폐기 처리")
if active:
    by_id={i["id"]:i for i in active}
    chosen=st.selectbox("처리할 재고", list(by_id), format_func=lambda k:f"#{k} {by_id[k]['name']} | {by_id[k]['quantity']:g}{by_id[k]['unit']} | 기한 {by_id[k]['expiry_date']}", key="process_id")
    i=by_id[chosen]
    with st.form(f"process_{chosen}", clear_on_submit=True):
        a,b,c=st.columns([1,1,2])
        action=a.radio("처리 구분", ["사용", "폐기"], horizontal=True)
        amount=b.number_input(f"처리 수량 ({i['unit']})", min_value=0.0, max_value=float(i["quantity"]), value=float(i["quantity"]), format="%.3f")
        reason=c.text_input("사용처 / 폐기 사유", placeholder="예: 저녁 카레 / 변질 / 오등록 수량 정정", max_chars=300)
        confirm=st.checkbox("선택한 품목과 처리 수량을 확인했습니다.")
        if st.form_submit_button("재고 차감 및 기록", type="primary"):
            if not confirm:
                st.error("처리 내용을 확인해 주세요.")
            else:
                try:
                    change_stock(chosen, action, amount, reason)
                    st.session_state.notice=f"{i['name']} {amount:g}{i['unit']} {action} 기록을 저장했습니다."
                    st.rerun()
                except (ValueError, sqlite3.Error) as e:
                    st.error(str(e))
else:
    st.caption("보유 재고를 등록하면 사용·폐기를 기록할 수 있습니다.")

st.subheader("④ 사용 · 폐기 기록")
a,b=st.columns(2)
log_kind=a.selectbox("기록 구분", ["전체", "사용", "폐기"])
log_query=b.text_input("기록 상품명 검색")
history=[{"일시":r["created_at"].replace("T"," "), "상품명":r["item_name"], "구분":r["action"], "수량":r["quantity"], "단위":r["unit"], "사용처 / 사유":r["reason"], "재고 ID":r["item_id"]} for r in reversed(logs) if (log_kind=="전체" or r["action"]==log_kind) and log_query.casefold() in r["item_name"].casefold()]
if history:
    history_df=pd.DataFrame(history)
    st.dataframe(history_df, hide_index=True, use_container_width=True)
    st.download_button("조회한 기록 CSV", history_df.to_csv(index=False).encode("utf-8-sig"), "fridge_history.csv", "text/csv")
else:
    st.caption("조회 조건에 맞는 기록이 없습니다. 재고를 사용하거나 폐기하면 자동으로 남습니다.")
st.caption("🥬 냉장고 식재료 알 · 재고는 구매 묶음별 관리 · 수량 0 품목은 목록에서 숨기고 이력은 유지합니다.")
