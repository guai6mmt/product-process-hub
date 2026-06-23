# -*- coding: utf-8 -*-
"""产品流程与文件管理系统 — 本机版 v2
零依赖（仅 Python 标准库）。逻辑依据《产品流程与文件管理_逻辑规格.md》。
v2：多流程 + 可配置模板（产品类型×流程，通用兜底，软提示门禁）；每项多文件块
（主文件/消保/法审/用印/其他，消保通过须先传消保文件）；按结构导出 zip。含 v1→v2 迁移。
运行：python app.py  → 浏览器自动打开 http://localhost:8765
数据：data/app.db + data/files/。备份=复制 data 文件夹。
"""
import os, io, sys, csv, json, base64, sqlite3, datetime, zipfile, urllib.parse, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
FILES = os.path.join(DATA, "files")
DB = os.path.join(DATA, "app.db")
os.makedirs(FILES, exist_ok=True)

CATS = ["主文件", "消保", "法审", "用印", "其他"]
XB_FS = ["未启动", "送审中", "通过", "退回"]
YY = ["待用印", "已用印"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS institution(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, has_poster INTEGER DEFAULT 0, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS product_type(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS process(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS blueprint_item(id INTEGER PRIMARY KEY AUTOINCREMENT, ptype TEXT DEFAULT '通用', process_id INTEGER,
  category TEXT, name TEXT, required INTEGER DEFAULT 1, needs_xb INTEGER DEFAULT 0, needs_fs INTEGER DEFAULT 0,
  needs_yy INTEGER DEFAULT 0, seal_party TEXT DEFAULT '', cond TEXT DEFAULT 'always', repeatable INTEGER DEFAULT 0, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS product(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, ptype TEXT DEFAULT '通用',
  institution_id INTEGER, contract_form TEXT DEFAULT '合并', created_at TEXT);
CREATE TABLE IF NOT EXISTS requirement(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, process_id INTEGER,
  category TEXT, name TEXT, required INTEGER DEFAULT 1, seal_party TEXT DEFAULT '',
  needs_xb INTEGER, needs_fs INTEGER, needs_yy INTEGER, xb_status TEXT, fs_status TEXT, yy_status TEXT, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS docfile(id INTEGER PRIMARY KEY AUTOINCREMENT, requirement_id INTEGER, category TEXT,
  version_no INTEGER DEFAULT 1, parent_id INTEGER, filename TEXT, filepath TEXT, size INTEGER, uploaded_at TEXT, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS review_log(id INTEGER PRIMARY KEY AUTOINCREMENT, requirement_id INTEGER, field TEXT, value TEXT, note TEXT DEFAULT '', at TEXT);
"""

SEED_PROCESSES = [("产品准入", 1), ("销售准入", 2), ("上架发售", 3), ("存续管理", 4)]
SEED_TYPES = ["通用", "私募证券投资基金", "固收+理财"]
# (ptype, process, category, name, required, xb, fs, yy, seal, cond, repeatable, sort)
SEED_BP = [
    ("通用", "产品准入", "合同文件", "基金合同", 1, 1, 1, 0, "", "always", 0, 1),
    ("通用", "产品准入", "合同文件", "托管协议", 1, 1, 1, 0, "", "always", 0, 2),
    ("通用", "产品准入", "说明文件", "产品说明书", 1, 1, 0, 0, "", "always", 0, 3),
    ("通用", "产品准入", "评估文件", "风险评级报告", 1, 0, 0, 0, "", "always", 0, 4),
    ("通用", "销售准入", "代销协议", "总对总协议", 1, 1, 1, 1, "双方", "always", 0, 1),
    ("通用", "销售准入", "代销协议", "代销协议补充协议", 1, 1, 1, 1, "双方", "always", 1, 2),
    ("通用", "销售准入", "委托代销函", "委托代销函", 1, 0, 0, 1, "对方机构", "always", 0, 3),
    ("通用", "销售准入", "宣传材料", "PPT", 1, 1, 0, 0, "", "always", 0, 4),
    ("通用", "销售准入", "宣传材料", "一页通", 1, 1, 0, 0, "", "always", 0, 5),
    ("通用", "销售准入", "宣传材料", "海报", 1, 1, 0, 0, "", "poster", 0, 6),
    ("通用", "销售准入", "宣传材料", "双录话术", 1, 1, 0, 0, "", "always", 0, 7),
    ("通用", "销售准入", "宣传材料", "线上智能播报", 1, 1, 0, 0, "", "always", 0, 8),
    ("通用", "销售准入", "合同", "合同（三合一·合并版）", 1, 1, 1, 0, "", "merge", 0, 9),
    ("通用", "销售准入", "合同", "合同主体（拆分版）", 1, 1, 1, 0, "", "split", 0, 10),
    ("通用", "销售准入", "合同", "风险揭示书（拆分版）", 1, 1, 1, 0, "", "split", 0, 11),
    ("通用", "销售准入", "合同", "计划说明书（拆分版）", 1, 1, 1, 0, "", "split", 0, 12),
]


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def pid_of(conn, name):
    r = conn.execute("SELECT id FROM process WHERE name=?", (name,)).fetchone()
    return r["id"] if r else None


def init_db():
    conn = db()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 0:
        for n, s in SEED_PROCESSES:
            conn.execute("INSERT INTO process(name,sort) VALUES(?,?)", (n, s))
    if conn.execute("SELECT COUNT(*) FROM product_type").fetchone()[0] == 0:
        for n in SEED_TYPES:
            conn.execute("INSERT INTO product_type(name) VALUES(?)", (n,))
    if conn.execute("SELECT COUNT(*) FROM institution").fetchone()[0] == 0:
        conn.execute("INSERT INTO institution(name,has_poster) VALUES(?,?)", ("示例机构A（有海报）", 1))
        conn.execute("INSERT INTO institution(name,has_poster) VALUES(?,?)", ("示例机构B（无海报）", 0))
    if conn.execute("SELECT COUNT(*) FROM blueprint_item").fetchone()[0] == 0:
        for t in SEED_BP:
            conn.execute("""INSERT INTO blueprint_item(ptype,process_id,category,name,required,needs_xb,needs_fs,needs_yy,seal_party,cond,repeatable,sort)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (t[0], pid_of(conn, t[1]), t[2], t[3], t[4], t[5], t[6], t[7], t[8], t[9], t[10], t[11]))
    conn.commit()
    migrate_v1(conn)
    conn.commit()
    conn.close()


def migrate_v1(conn):
    tabs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "version" not in tabs:
        return
    if conn.execute("SELECT v FROM meta WHERE k='migrated_v2'").fetchone():
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(requirement)")}
    if "process_id" not in cols:
        conn.execute("ALTER TABLE requirement ADD COLUMN process_id INTEGER")
    sale = pid_of(conn, "销售准入")
    conn.execute("UPDATE requirement SET process_id=? WHERE process_id IS NULL", (sale,))
    if conn.execute("SELECT COUNT(*) FROM docfile").fetchone()[0] == 0:
        for v in conn.execute("SELECT * FROM version").fetchall():
            k = v.keys()
            conn.execute("""INSERT INTO docfile(requirement_id,category,version_no,parent_id,filename,filepath,size,uploaded_at,note)
                VALUES(?,?,?,?,?,?,?,?,?)""", (v["requirement_id"], "主文件", v["version_no"],
                v["parent_version_id"] if "parent_version_id" in k else None, v["filename"], v["filepath"],
                v["size"], v["uploaded_at"], v["note"] if "note" in k else ""))
    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('migrated_v2','1')")


# ---------- 业务逻辑 ----------
def blueprint_for(conn, ptype, process_id):
    items = conn.execute("SELECT * FROM blueprint_item WHERE ptype=? AND process_id=? ORDER BY sort,id", (ptype, process_id)).fetchall()
    if not items and ptype != "通用":
        items = conn.execute("SELECT * FROM blueprint_item WHERE ptype='通用' AND process_id=? ORDER BY sort,id", (process_id,)).fetchall()
    return items


def add_requirement(conn, pid, proc_id, category, name, xb, fs, yy, seal, sort=0):
    conn.execute("""INSERT INTO requirement(product_id,process_id,category,name,required,seal_party,needs_xb,needs_fs,needs_yy,xb_status,fs_status,yy_status,sort)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (pid, proc_id, category, name, 1, seal, xb, fs, yy,
        "未启动" if xb else "不适用", "未启动" if fs else "不适用", "待用印" if yy else "不适用", sort))


def instantiate(conn, pid, ptype, n_supp, has_poster, form):
    for p in conn.execute("SELECT * FROM process ORDER BY sort,id").fetchall():
        for it in blueprint_for(conn, ptype, p["id"]):
            c = it["cond"]
            if c == "poster" and not has_poster:
                continue
            if c == "merge" and form != "合并":
                continue
            if c == "split" and form != "拆分":
                continue
            cnt = n_supp if (it["repeatable"] and n_supp and n_supp > 0) else 1
            for k in range(cnt):
                suf = (" #%d" % (k + 1)) if (it["repeatable"] and cnt > 1) else ""
                add_requirement(conn, pid, p["id"], it["category"], it["name"] + suf,
                                it["needs_xb"], it["needs_fs"], it["needs_yy"], it["seal_party"], it["sort"])


def has_cat(conn, rid, cat):
    return conn.execute("SELECT COUNT(*) FROM docfile WHERE requirement_id=? AND category=?", (rid, cat)).fetchone()[0] > 0


def next_action(conn, r):
    if not has_cat(conn, r["id"], "主文件"):
        return "上传" + r["name"]
    if r["needs_xb"] and r["xb_status"] != "通过":
        s = r["xb_status"]
        return "按消保意见改后重传" if s == "退回" else ("等消保结果/催办" if s == "送审中" else "送消保")
    if r["needs_fs"] and r["fs_status"] != "通过":
        s = r["fs_status"]
        return "按法审意见改后重传" if s == "退回" else ("等法审结果/催办" if s == "送审中" else "送法审")
    if r["needs_yy"] and r["yy_status"] != "已用印":
        return "推动" + (r["seal_party"] or "对方") + "用印"
    return "已到位"


def can_set(conn, r, field, value):
    if field == "xb" and value == "通过" and not has_cat(conn, r["id"], "消保"):
        return False, "请先在“消保”处上传消保文件，再标记通过"
    if field == "fs" and value in ("送审中", "通过"):
        if r["needs_xb"] and r["xb_status"] != "通过":
            return False, "需先通过消保，才能送审/通过法审"
    if field == "yy" and value == "已用印":
        if r["needs_xb"] and r["xb_status"] != "通过":
            return False, "需先通过消保，才能用印"
        if r["needs_fs"] and r["fs_status"] != "通过":
            return False, "需先通过法审，才能用印"
    return True, ""


def req_full(conn, r):
    d = dict(r)
    files = conn.execute("SELECT id,category,version_no,filename,size,uploaded_at FROM docfile WHERE requirement_id=? ORDER BY category,version_no DESC,id DESC", (r["id"],)).fetchall()
    byc = {}
    for f in files:
        byc.setdefault(f["category"], []).append(dict(f))
    d["files"] = byc
    d["next_action"] = next_action(conn, r)
    d["done"] = (d["next_action"] == "已到位")
    pr = conn.execute("SELECT name FROM process WHERE id=?", (r["process_id"],)).fetchone()
    d["process_name"] = pr["name"] if pr else ""
    for fld in ("xb", "fs", "yy"):
        lg = conn.execute("SELECT note FROM review_log WHERE requirement_id=? AND field=? ORDER BY id DESC LIMIT 1", (r["id"], fld)).fetchone()
        d[fld + "_note"] = lg["note"] if lg else ""
    return d


def product_overview(conn, p):
    reqs = conn.execute("SELECT * FROM requirement WHERE product_id=? AND required=1", (p["id"],)).fetchall()
    total = len(reqs)
    done = sum(1 for r in reqs if next_action(conn, r) == "已到位")
    d = dict(p)
    inst = conn.execute("SELECT name FROM institution WHERE id=?", (p["institution_id"],)).fetchone()
    d["institution_name"] = inst["name"] if inst else ""
    d["total"], d["done"] = total, done
    d["progress"] = round(done * 100 / total) if total else 0
    d["ready"] = (total > 0 and done == total)
    return d


def safe(s):
    s = "" if s is None else str(s)
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "_")
    return s.strip() or "_"


def build_zip(conn, products, history):
    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    sio = io.StringIO()
    w = csv.writer(sio)
    w.writerow(["产品", "流程", "大类", "文件项", "类别", "文件名", "版本", "上传时间", "消保", "法审", "用印", "是否到位"])
    for p in products:
        proot = safe(p["name"])
        for pr in conn.execute("SELECT * FROM process ORDER BY sort,id").fetchall():
            reqs = conn.execute("SELECT * FROM requirement WHERE product_id=? AND process_id=? ORDER BY sort,id", (p["id"], pr["id"])).fetchall()
            for r in reqs:
                base = "%s/%s/%s/%s" % (proot, safe(pr["name"]), safe(r["category"]), safe(r["name"]))
                fdone = "是" if next_action(conn, r) == "已到位" else "否"
                files = conn.execute("SELECT * FROM docfile WHERE requirement_id=? ORDER BY category,version_no", (r["id"],)).fetchall()
                maxmain = max([f["version_no"] for f in files if f["category"] == "主文件"], default=0)
                for f in files:
                    if not f["filepath"] or not os.path.exists(f["filepath"]):
                        continue
                    if f["category"] == "主文件":
                        if f["version_no"] == maxmain:
                            arc = "%s/主文件_V%s_%s" % (base, f["version_no"], safe(f["filename"]))
                        else:
                            if not history:
                                continue
                            arc = "%s/历史/主文件_V%s_%s" % (base, f["version_no"], safe(f["filename"]))
                    else:
                        arc = "%s/%s/%s" % (base, safe(f["category"]), safe(f["filename"]))
                    try:
                        with open(f["filepath"], "rb") as fh:
                            zf.writestr(arc, fh.read())
                    except Exception:
                        pass
                    w.writerow([p["name"], pr["name"], r["category"], r["name"], f["category"], f["filename"],
                                f["version_no"], f["uploaded_at"], r["xb_status"], r["fs_status"], r["yy_status"], fdone])
    zf.writestr("_清单.csv", "﻿" + sio.getvalue())
    zf.close()
    return buf.getvalue()


# ===================== HTTP =====================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        q = urllib.parse.parse_qs(u.query)
        try:
            if path in ("/", "/index.html"):
                return self._file("index.html", "text/html; charset=utf-8")
            if path == "/api/config":
                conn = db()
                out = {"processes": [dict(r) for r in conn.execute("SELECT * FROM process ORDER BY sort,id")],
                       "product_types": [dict(r) for r in conn.execute("SELECT * FROM product_type ORDER BY id")],
                       "institutions": [dict(r) for r in conn.execute("SELECT * FROM institution ORDER BY id")]}
                conn.close()
                return self._json(out)
            if path == "/api/products":
                conn = db()
                rows = [product_overview(conn, p) for p in conn.execute("SELECT * FROM product ORDER BY id DESC")]
                conn.close()
                return self._json(rows)
            if path == "/api/product":
                return self._product(int(q["id"][0]))
            if path == "/api/todo":
                return self._todo()
            if path == "/api/blueprint":
                conn = db()
                rows = [dict(r) for r in conn.execute("SELECT * FROM blueprint_item WHERE ptype=? AND process_id=? ORDER BY sort,id", (q["ptype"][0], int(q["process_id"][0])))]
                conn.close()
                return self._json(rows)
            if path == "/api/blueprint_matrix":
                conn = db()
                rows = [{"ptype": r["ptype"], "process_id": r["process_id"], "count": r["c"]}
                        for r in conn.execute("SELECT ptype, process_id, COUNT(*) c FROM blueprint_item GROUP BY ptype, process_id")]
                conn.close()
                return self._json(rows)
            if path == "/api/file":
                return self._download(int(q["fid"][0]))
            if path == "/api/export":
                return self._export(int(q["id"][0]), q.get("history", ["0"])[0] == "1")
            if path == "/api/export_all":
                return self._export(None, q.get("history", ["0"])[0] == "1")
            self.send_error(404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            b = self._body()
            conn = db()
            if path == "/api/products":
                iid = b.get("institution_id")
                pidv = conn.execute("INSERT INTO product(name,ptype,institution_id,contract_form,created_at) VALUES(?,?,?,?,?)",
                                    (b.get("name", "").strip(), b.get("ptype", "通用"), iid, b.get("contract_form", "合并"), now())).lastrowid
                instantiate(conn, pidv, b.get("ptype", "通用"), int(b.get("n_supp", 1)), bool(b.get("has_poster")), b.get("contract_form", "合并"))
                conn.commit(); conn.close(); return self._json({"id": pidv})
            if path == "/api/product_delete":
                pidv = int(b["id"])
                conn.execute("DELETE FROM docfile WHERE requirement_id IN (SELECT id FROM requirement WHERE product_id=?)", (pidv,))
                conn.execute("DELETE FROM requirement WHERE product_id=?", (pidv,))
                conn.execute("DELETE FROM product WHERE id=?", (pidv,))
                conn.commit(); conn.close(); return self._json({"ok": True})
            if path == "/api/review":
                return self._review(conn, b)
            if path == "/api/upload":
                return self._upload(conn, b)
            if path == "/api/file_delete":
                f = conn.execute("SELECT * FROM docfile WHERE id=?", (int(b["id"]),)).fetchone()
                if f:
                    try:
                        os.remove(f["filepath"])
                    except Exception:
                        pass
                    conn.execute("DELETE FROM docfile WHERE id=?", (int(b["id"]),))
                    conn.commit()
                conn.close(); return self._json({"ok": True})
            if path == "/api/req_add":
                add_requirement(conn, int(b["product_id"]), int(b["process_id"]), b.get("category", "其他"),
                                b.get("name", "新文件"), 1 if b.get("needs_xb") else 0, 1 if b.get("needs_fs") else 0,
                                1 if b.get("needs_yy") else 0, b.get("seal_party", ""), 999)
                conn.commit(); conn.close(); return self._json({"ok": True})
            if path == "/api/req_duplicate":
                r = conn.execute("SELECT * FROM requirement WHERE id=?", (int(b["id"]),)).fetchone()
                if r:
                    add_requirement(conn, r["product_id"], r["process_id"], r["category"], r["name"] + "（副本）",
                                    r["needs_xb"], r["needs_fs"], r["needs_yy"], r["seal_party"], r["sort"])
                    conn.commit()
                conn.close(); return self._json({"ok": True})
            if path == "/api/req_delete":
                rid = int(b["id"])
                conn.execute("DELETE FROM docfile WHERE requirement_id=?", (rid,))
                conn.execute("DELETE FROM requirement WHERE id=?", (rid,))
                conn.commit(); conn.close(); return self._json({"ok": True})
            if path == "/api/institutions":
                conn.execute("INSERT INTO institution(name,has_poster) VALUES(?,?)", (b.get("name", "").strip(), 1 if b.get("has_poster") else 0))
                conn.commit(); conn.close(); return self._json({"ok": True})
            if path == "/api/process_add":
                mx = conn.execute("SELECT MAX(sort) m FROM process").fetchone()["m"] or 0
                conn.execute("INSERT INTO process(name,sort) VALUES(?,?)", (b.get("name", "").strip(), mx + 1))
                conn.commit(); conn.close(); return self._json({"ok": True})
            if path == "/api/ptype_add":
                conn.execute("INSERT INTO product_type(name) VALUES(?)", (b.get("name", "").strip(),))
                conn.commit(); conn.close(); return self._json({"ok": True})
            if path == "/api/blueprint_save":
                return self._bp_save(conn, b)
            if path == "/api/blueprint_delete":
                conn.execute("DELETE FROM blueprint_item WHERE id=?", (int(b["id"]),))
                conn.commit(); conn.close(); return self._json({"ok": True})
            conn.close(); self.send_error(404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _bp_save(self, conn, b):
        f = (b.get("ptype", "通用"), int(b["process_id"]), b.get("category", ""), b.get("name", ""),
             1 if b.get("required", 1) else 0, 1 if b.get("needs_xb") else 0, 1 if b.get("needs_fs") else 0,
             1 if b.get("needs_yy") else 0, b.get("seal_party", ""), b.get("cond", "always"), 1 if b.get("repeatable") else 0, int(b.get("sort", 0)))
        if b.get("id"):
            conn.execute("""UPDATE blueprint_item SET ptype=?,process_id=?,category=?,name=?,required=?,needs_xb=?,needs_fs=?,needs_yy=?,seal_party=?,cond=?,repeatable=?,sort=? WHERE id=?""", f + (int(b["id"]),))
        else:
            conn.execute("""INSERT INTO blueprint_item(ptype,process_id,category,name,required,needs_xb,needs_fs,needs_yy,seal_party,cond,repeatable,sort) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", f)
        conn.commit(); conn.close(); return self._json({"ok": True})

    def _product(self, pid):
        conn = db()
        p = conn.execute("SELECT * FROM product WHERE id=?", (pid,)).fetchone()
        if not p:
            conn.close(); return self._json({"error": "产品不存在"}, 404)
        d = product_overview(conn, p)
        procs = []
        prior_done = True
        for pr in conn.execute("SELECT * FROM process ORDER BY sort,id").fetchall():
            reqs = conn.execute("SELECT * FROM requirement WHERE product_id=? AND process_id=? ORDER BY sort,id", (pid, pr["id"])).fetchall()
            if not reqs:
                continue
            full = [req_full(conn, r) for r in reqs]
            done = sum(1 for x in full if x["done"])
            procs.append({"id": pr["id"], "name": pr["name"], "requirements": full, "total": len(full),
                          "done": done, "ready": done == len(full), "prior_ready": prior_done})
            prior_done = prior_done and (done == len(full))
        d["processes"] = procs
        conn.close()
        return self._json(d)

    def _review(self, conn, b):
        rid, field, value = int(b["rid"]), b["field"], b["value"]
        note = (b.get("note") or "").strip()
        col = {"xb": "xb_status", "fs": "fs_status", "yy": "yy_status"}[field]
        allowed = YY if field == "yy" else XB_FS
        if value not in allowed:
            conn.close(); return self._json({"error": "非法状态"}, 400)
        r = conn.execute("SELECT * FROM requirement WHERE id=?", (rid,)).fetchone()
        if not r:
            conn.close(); return self._json({"error": "文件不存在"}, 404)
        if not {"xb": r["needs_xb"], "fs": r["needs_fs"], "yy": r["needs_yy"]}[field]:
            conn.close(); return self._json({"error": "该文件无需此审查"}, 400)
        if value == "退回" and not note:
            conn.close(); return self._json({"error": "退回必须填写退回意见"}, 400)
        ok, msg = can_set(conn, r, field, value)
        if not ok:
            conn.close(); return self._json({"error": msg}, 400)
        conn.execute("UPDATE requirement SET %s=? WHERE id=?" % col, (value, rid))
        conn.execute("INSERT INTO review_log(requirement_id,field,value,note,at) VALUES(?,?,?,?,?)", (rid, field, value, note, now()))
        conn.commit(); conn.close(); return self._json({"ok": True})

    def _upload(self, conn, b):
        rid = int(b["rid"])
        cat = b.get("category", "主文件")
        if cat not in CATS:
            cat = "其他"
        fn = b.get("filename", "file")
        raw = base64.b64decode(b.get("data_base64", "")) if b.get("data_base64") else b""
        prev = conn.execute("SELECT MAX(version_no) m FROM docfile WHERE requirement_id=? AND category=?", (rid, cat)).fetchone()["m"]
        vno = (prev or 0) + 1
        diskname = "r%d_%s_v%d_%s" % (rid, {"主文件": "main", "消保": "xb", "法审": "fs", "用印": "yy", "其他": "etc"}[cat], vno, safe(fn))
        fp = os.path.join(FILES, diskname)
        with open(fp, "wb") as fh:
            fh.write(raw)
        conn.execute("INSERT INTO docfile(requirement_id,category,version_no,filename,filepath,size,uploaded_at) VALUES(?,?,?,?,?,?,?)",
                     (rid, cat, vno, fn, fp, len(raw), now()))
        if cat == "主文件":
            conn.execute("""UPDATE requirement SET
                xb_status=CASE WHEN needs_xb THEN '未启动' ELSE '不适用' END,
                fs_status=CASE WHEN needs_fs THEN '未启动' ELSE '不适用' END,
                yy_status=CASE WHEN needs_yy THEN '待用印' ELSE '不适用' END WHERE id=?""", (rid,))
        conn.commit(); conn.close(); return self._json({"ok": True, "version_no": vno})

    def _todo(self):
        conn = db()
        out = []
        for p in conn.execute("SELECT * FROM product ORDER BY id DESC").fetchall():
            for r in conn.execute("SELECT * FROM requirement WHERE product_id=? AND required=1 ORDER BY process_id,sort,id", (p["id"],)).fetchall():
                na = next_action(conn, r)
                if na != "已到位":
                    pr = conn.execute("SELECT name FROM process WHERE id=?", (r["process_id"],)).fetchone()
                    out.append({"product": p["name"], "process": pr["name"] if pr else "", "category": r["category"], "name": r["name"], "next_action": na})
        conn.close()
        return self._json(out)

    def _download(self, fid):
        conn = db()
        f = conn.execute("SELECT * FROM docfile WHERE id=?", (fid,)).fetchone()
        conn.close()
        if not f or not os.path.exists(f["filepath"]):
            return self.send_error(404)
        with open(f["filepath"], "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%s" % urllib.parse.quote(f["filename"]))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _export(self, pid, history):
        conn = db()
        if pid is None:
            products = conn.execute("SELECT * FROM product ORDER BY id").fetchall()
            zipname = "全部产品_文件包.zip"
        else:
            products = conn.execute("SELECT * FROM product WHERE id=?", (pid,)).fetchall()
            zipname = (safe(products[0]["name"]) if products else "product") + "_文件包.zip"
        data = build_zip(conn, products, history)
        conn.close()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%s" % urllib.parse.quote(zipname))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, name, ctype):
        with open(os.path.join(BASE, name), "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = "http://localhost:%d" % PORT
    print("产品流程与文件管理系统 v2 已启动：%s" % url)
    print("数据目录：%s（备份=复制此文件夹）" % DATA)
    print("关闭：本窗口按 Ctrl+C")
    if not os.environ.get("NO_BROWSER") and "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
