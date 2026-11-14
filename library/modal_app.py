"""Scan-to-library back end: every processed drawing is kept, a phone photo of the paper finds it again.

One Modal web app (HTTPS, so a phone browser may use the camera) with a MariaDB server inside the container:
  workspaces -> projects -> sheets (image, converted sheet, picture fingerprint in a VECTOR column)
  answers (every question asked, with what it cost), scans (every phone photo and what it matched), ledger (cost, reuse)

Photo matching: a DINOv2 fingerprint of the photo is compared with the sheets' fingerprints by MariaDB's vector index
(VEC_DISTANCE_COSINE), then the best candidates are confirmed by matching local details (SIFT + RANSAC), which also
gives the outline of the sheet inside the photo.

    MODAL_PROFILE=oliver-universitapps modal deploy library/modal_app.py
API: library/README.md. The database is dumped to the volume after changes and restored on start.
"""
import os, pathlib

import modal

app = modal.App("bob-library")
VOL = modal.Volume.from_name("bob-library", create_if_missing=True)
HERE = pathlib.Path(__file__).resolve().parent
V = "/vol"
SOCK = "/tmp/mysql.sock"
DIM = 384
PASSWORD = os.environ.get("BOB_PASSWORD", "123456789")   # demo only: one shared workspace password


def _fetch_weights():
    import timm
    timm.create_model("vit_small_patch14_dinov2.lvd142m", pretrained=True, num_classes=0)


image = (
    modal.Image.from_registry("mariadb:11.8", add_python="3.11")
    .pip_install("torch==2.5.1", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install("timm==1.0.12", "huggingface_hub==0.26.5", "safetensors", "fastapi[standard]==0.115.6", "pymysql==1.1.1", "pillow",
                 "numpy<2", "opencv-python-headless", "pymupdf", "segno")
    .run_function(_fetch_weights)
    .add_local_file(str(HERE / "phone.html"), "/root/phone.html")
)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS workspaces (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(190) UNIQUE, password_sha CHAR(64));
CREATE TABLE IF NOT EXISTS projects (id INT AUTO_INCREMENT PRIMARY KEY, workspace_id INT NOT NULL, name VARCHAR(190) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY (workspace_id, name));
CREATE TABLE IF NOT EXISTS sheets (id INT AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, name VARCHAR(255) NOT NULL,
  file_name VARCHAR(255), revision VARCHAR(40) DEFAULT '', superseded_by INT NULL, width INT, height INT, sheet_json LONGTEXT NULL,
  convert_seconds FLOAT DEFAULT 0, convert_cost_usd DOUBLE DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  embedding VECTOR({DIM}) NOT NULL, VECTOR INDEX (embedding) M=8 DISTANCE=cosine, KEY (project_id));
CREATE TABLE IF NOT EXISTS answers (id INT AUTO_INCREMENT PRIMARY KEY, sheet_id INT NOT NULL, question TEXT, question_norm VARCHAR(500),
  status VARCHAR(12) DEFAULT 'done', answer_json LONGTEXT NULL, seconds FLOAT DEFAULT 0, cost_usd DOUBLE DEFAULT 0, hits INT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY (sheet_id, question_norm(190)), KEY (status));
CREATE TABLE IF NOT EXISTS scans (id INT AUTO_INCREMENT PRIMARY KEY, workspace_id INT NOT NULL, sheet_id INT NULL, similarity FLOAT,
  details INT, seconds FLOAT, device VARCHAR(255), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY (sheet_id));
CREATE TABLE IF NOT EXISTS ledger (id INT AUTO_INCREMENT PRIMARY KEY, workspace_id INT NOT NULL, sheet_id INT NULL, kind VARCHAR(20),
  cost_usd DOUBLE DEFAULT 0, saved_usd DOUBLE DEFAULT 0, seconds FLOAT DEFAULT 0, saved_seconds FLOAT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
"""


@app.function(image=image, volumes={V: VOL}, cpu=4, memory=8192, min_containers=1, max_containers=1, timeout=900, scaledown_window=1200)
@modal.concurrent(max_inputs=16)
@modal.asgi_app()
def web():
    import hashlib, io, json, re, subprocess, threading, time
    import cv2, numpy as np, pymysql, segno, timm, torch
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse, Response
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    FILES = f"{V}/library/files"
    DUMP = f"{V}/library/db.sql"
    os.makedirs(FILES, exist_ok=True)

    # ---- MariaDB inside the container -------------------------------------------------------------------------------
    if not os.path.exists("/tmp/db/mysql"):
        subprocess.run(["mariadb-install-db", "--user=root", "--datadir=/tmp/db", "--auth-root-authentication-method=normal"],
                       check=True, capture_output=True)
    subprocess.Popen(["mariadbd", "--user=root", "--datadir=/tmp/db", f"--socket={SOCK}", "--skip-networking", "--max-allowed-packet=256M"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        if os.path.exists(SOCK):
            break
        time.sleep(0.5)
    local = threading.local()

    def db():
        c = getattr(local, "c", None)
        if c is None or not c.open:
            c = local.c = pymysql.connect(unix_socket=SOCK, user="root", database="bob", autocommit=True, cursorclass=pymysql.cursors.DictCursor)
        return c

    def q(sql, args=None, one=False):
        with db().cursor() as cur:
            cur.execute(sql, args)
            if cur.description is None:
                return cur.lastrowid
            return cur.fetchone() if one else cur.fetchall()

    boot = pymysql.connect(unix_socket=SOCK, user="root", autocommit=True)
    with boot.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS bob CHARACTER SET utf8mb4")
    boot.close()
    if os.path.exists(DUMP) and not q("SHOW TABLES"):
        subprocess.run(f"mariadb --socket={SOCK} -uroot bob < {DUMP}", shell=True, check=True)
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            q(stmt)
    if not q("SELECT id FROM workspaces WHERE name='Demo'"):
        q("INSERT INTO workspaces (name, password_sha) VALUES ('Demo', %s)", (hashlib.sha256(PASSWORD.encode()).hexdigest(),))

    dirty = threading.Event()

    def saver():
        while True:
            dirty.wait()
            time.sleep(5)
            dirty.clear()
            try:
                subprocess.run(f"mariadb-dump --socket={SOCK} -uroot --single-transaction bob > {DUMP}.tmp && mv {DUMP}.tmp {DUMP}", shell=True, check=True)
                VOL.commit()
            except Exception as e:
                print("dump failed", e)
    threading.Thread(target=saver, daemon=True).start()

    # ---- picture fingerprint and detail matching --------------------------------------------------------------------
    net = timm.create_model("vit_small_patch14_dinov2.lvd142m", pretrained=True, num_classes=0, img_size=448).eval()
    torch.set_num_threads(4)
    mean, std = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)
    sift = cv2.SIFT_create(nfeatures=2500)
    net_lock = threading.Lock()

    def fingerprint(im):
        """384 numbers describing what the picture looks like, the same for a clean sheet and a photo of it."""
        g = im.convert("L")
        g.thumbnail((448, 448), Image.LANCZOS)
        canvas = Image.new("L", (448, 448), 255)
        canvas.paste(g, ((448 - g.width) // 2, (448 - g.height) // 2))
        a = np.repeat(np.asarray(canvas, np.float32)[:, :, None] / 255, 3, 2)
        x = torch.from_numpy(((a - mean) / std).transpose(2, 0, 1))[None]
        with net_lock, torch.no_grad():
            v = net(x)[0].numpy()
        return v / (np.linalg.norm(v) + 1e-9)

    def vec(v):
        return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

    def grey(im, side=1100):
        g = im.convert("L")
        g.thumbnail((side, side), Image.LANCZOS)
        return np.asarray(g)

    feats = {}

    def sheet_feats(sid):
        """Local details of a stored sheet (computed once, kept in memory)."""
        if sid not in feats:
            g = cv2.imread(f"{FILES}/{sid}/match.png", cv2.IMREAD_GRAYSCALE)
            k, d = sift.detectAndCompute(g, None)
            feats[sid] = (np.float32([p.pt for p in k]) if k else np.zeros((0, 2), np.float32), d, g.shape)
        return feats[sid]

    def details(photo, sid):
        """How many local details of the photo line up with the sheet under one perspective, and the sheet's outline in the photo.
        Details that sit where every sheet of a set has the same thing (title block, legend) match many sheets equally, so the
        count alone is not proof: the caller compares the best sheet with the runner-up."""
        pts1, d1, (ph, pw) = photo
        pts2, d2, (h, w) = sheet_feats(sid)
        if d1 is None or d2 is None or len(pts1) < 8 or len(pts2) < 8:
            return 0, None
        m = cv2.BFMatcher().knnMatch(d2, d1, k=2)
        good = [a for a, b in (p for p in m if len(p) == 2) if a.distance < 0.72 * b.distance]
        if len(good) < 8:
            return len(good), None
        src = np.float32([pts2[a.queryIdx] for a in good]).reshape(-1, 1, 2)
        dst = np.float32([pts1[a.trainIdx] for a in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if H is None:
            return 0, None
        quad = cv2.perspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2), H).reshape(-1, 2)
        return int(mask.sum()), [[round(float(x) / pw, 4), round(float(y) / ph, 4)] for x, y in quad]

    def load_image(data, name):
        if name.lower().endswith(".pdf") or data[:4] == b"%PDF":
            import pymupdf
            page = pymupdf.open(stream=data, filetype="pdf")[0]
            k = min(4000 / max(page.rect.width, page.rect.height), 3.0)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(k, k), alpha=False)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return Image.open(io.BytesIO(data)).convert("RGB")

    norm = lambda s: re.sub(r"[^\w]+", " ", s.lower(), flags=re.UNICODE).strip()[:500]

    # ---- API ----------------------------------------------------------------------------------------------------------
    api = FastAPI(title="Bob library")

    def ws(request: Request):
        pw = request.headers.get("x-bob-password") or request.query_params.get("pw") or ""
        row = q("SELECT id FROM workspaces WHERE password_sha=%s", (hashlib.sha256(pw.encode()).hexdigest(),), one=True)
        if not row:
            raise HTTPException(401, "wrong or missing password")
        return row["id"]

    def sheet_row(w, sid):
        r = q("SELECT s.*, p.name project, p.workspace_id FROM sheets s JOIN projects p ON p.id=s.project_id WHERE s.id=%s", (sid,), one=True)
        if not r or r["workspace_id"] != w:
            raise HTTPException(404, "no such sheet in this workspace")
        return r

    def brief(r):
        return dict(id=r["id"], name=r["name"], file_name=r["file_name"], revision=r["revision"], superseded_by=r["superseded_by"],
                    width=r["width"], height=r["height"], project_id=r["project_id"], has_sheet=r.get("has_sheet", r.get("sheet_json") is not None),
                    convert_seconds=r["convert_seconds"], convert_cost_usd=r["convert_cost_usd"], created_at=str(r["created_at"]))

    @api.get("/", response_class=HTMLResponse)
    @api.get("/phone", response_class=HTMLResponse)
    def phone():
        return open("/root/phone.html").read()

    @api.get("/api/qr.svg")
    def qr(request: Request):
        url = str(request.base_url).replace("http://", "https://") + "phone"
        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2)
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"x-phone-url": url, "access-control-expose-headers": "x-phone-url"})

    @api.post("/api/login")
    def login(request: Request):
        w = ws(request)
        return dict(ok=True, workspace=q("SELECT name FROM workspaces WHERE id=%s", (w,), one=True)["name"])

    @api.get("/api/projects")
    def projects(request: Request):
        w = ws(request)
        return q("SELECT p.id, p.name, p.created_at, COUNT(s.id) sheets FROM projects p LEFT JOIN sheets s ON s.project_id=p.id "
                 "WHERE p.workspace_id=%s GROUP BY p.id ORDER BY p.created_at DESC", (w,))

    @api.get("/api/projects/{pid}")
    def project(pid: int, request: Request):
        w = ws(request)
        p = q("SELECT id, name, created_at FROM projects WHERE id=%s AND workspace_id=%s", (pid, w), one=True)
        if not p:
            raise HTTPException(404, "no such project in this workspace")
        rows = q("SELECT id, name, file_name, revision, superseded_by, width, height, project_id, sheet_json IS NOT NULL has_sheet, convert_seconds, "
                 "convert_cost_usd, created_at FROM sheets WHERE project_id=%s ORDER BY name", (pid,))
        return dict(p, sheets=[brief(r) for r in rows])

    @api.post("/api/sheets")
    async def add_sheet(request: Request, project: str, name: str = "", file_name: str = "", revision: str = "", supersedes: int = 0,
                        convert_seconds: float = 0.0, convert_cost_usd: float = 0.0):
        """Body: the file's bytes (PDF, PNG or JPG). ?project= names the project: an existing one of this workspace is added to, a new name creates one."""
        w = ws(request)
        data = await request.body()
        im = load_image(data, file_name or name)
        pid = q("SELECT id FROM projects WHERE workspace_id=%s AND name=%s", (w, project), one=True)
        pid = pid["id"] if pid else q("INSERT INTO projects (workspace_id, name) VALUES (%s, %s)", (w, project))
        sid = q("INSERT INTO sheets (project_id, name, file_name, revision, width, height, convert_seconds, convert_cost_usd, embedding) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, VEC_FromText(%s))",
                (pid, name or os.path.splitext(file_name)[0], file_name, revision, im.width, im.height, convert_seconds, convert_cost_usd, vec(fingerprint(im))))
        d = f"{FILES}/{sid}"
        os.makedirs(d, exist_ok=True)
        open(f"{d}/original", "wb").write(data)
        big = im.copy()
        big.thumbnail((2400, 2400), Image.LANCZOS)
        big.save(f"{d}/view.jpg", quality=85)
        Image.fromarray(grey(im)).save(f"{d}/match.png")
        th = im.copy()
        th.thumbnail((480, 480), Image.LANCZOS)
        th.save(f"{d}/thumb.jpg", quality=80)
        if supersedes:
            sheet_row(w, supersedes)
            q("UPDATE sheets SET superseded_by=%s WHERE id=%s", (sid, supersedes))
        if convert_seconds or convert_cost_usd:
            q("INSERT INTO ledger (workspace_id, sheet_id, kind, cost_usd, seconds) VALUES (%s,%s,'convert',%s,%s)", (w, sid, convert_cost_usd, convert_seconds))
        dirty.set()
        return dict(id=sid, project_id=pid, width=im.width, height=im.height)

    @api.put("/api/sheets/{sid}/sheet")
    async def put_sheet(sid: int, request: Request, convert_seconds: float = 0.0, convert_cost_usd: float = 0.0):
        """Body: the converted sheet (drawqa-sheet JSON) so nobody has to convert this drawing again."""
        w = ws(request)
        sheet_row(w, sid)
        q("UPDATE sheets SET sheet_json=%s, convert_seconds=%s, convert_cost_usd=%s WHERE id=%s", ((await request.body()).decode(), convert_seconds, convert_cost_usd, sid))
        q("INSERT INTO ledger (workspace_id, sheet_id, kind, cost_usd, seconds) VALUES (%s,%s,'convert',%s,%s)", (w, sid, convert_cost_usd, convert_seconds))
        dirty.set()
        return dict(ok=True)

    @api.get("/api/sheets/{sid}")
    def get_sheet(sid: int, request: Request, full: int = 0):
        w = ws(request)
        r = sheet_row(w, sid)
        out = dict(brief(r), project=r["project"])
        if full and r["sheet_json"]:
            out["sheet"] = json.loads(r["sheet_json"])
        return out

    @api.get("/api/sheets/{sid}/{kind}")
    def sheet_file(sid: int, kind: str, request: Request):
        w = ws(request)
        sheet_row(w, sid)
        f = {"thumb": "thumb.jpg", "view": "view.jpg", "original": "original"}.get(kind)
        if not f or not os.path.exists(f"{FILES}/{sid}/{f}"):
            raise HTTPException(404, "no such file")
        return FileResponse(f"{FILES}/{sid}/{f}", media_type="image/jpeg" if f.endswith(".jpg") else "application/octet-stream")

    @api.post("/api/scan")
    async def scan(request: Request):
        """Body: a photo (JPG or PNG) of a paper drawing. Finds the stored sheet, its project and everything already known about it."""
        w = ws(request)
        t0 = time.time()
        im = Image.open(io.BytesIO(await request.body()))
        try:
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        im = im.convert("RGB")
        v = vec(fingerprint(im))
        cands = q("SELECT s.id, s.name, s.project_id, VEC_DISTANCE_COSINE(s.embedding, VEC_FromText(%s)) dist FROM sheets s JOIN projects p ON p.id=s.project_id "
                  "WHERE p.workspace_id=%s ORDER BY dist LIMIT 24", (v, w))
        pg = grey(im)
        k1, d1 = sift.detectAndCompute(pg, None)
        photo = (np.float32([p.pt for p in k1]) if k1 else np.zeros((0, 2), np.float32), d1, pg.shape)
        for c in cands:
            c["details"], c["outline"] = details(photo, c["id"])
        cands.sort(key=lambda c: -c["details"])
        best = cands[0] if cands else None
        second = cands[1]["details"] if len(cands) > 1 else 0
        # sure: plenty of matching details AND clearly more than any other sheet (sheets of one set share title blocks and
        # legends; storeys of one building can be near copies). Otherwise the user picks among the look-alikes.
        sure = best is not None and best["details"] >= 25 and best["details"] >= 1.35 * second
        alike = [c for c in cands if best and best["details"] >= 25 and c["details"] >= best["details"] / 1.35][:6]
        sec = round(time.time() - t0, 2)
        scan_id = q("INSERT INTO scans (workspace_id, sheet_id, similarity, details, seconds, device) VALUES (%s,%s,%s,%s,%s,%s)",
                    (w, best["id"] if sure else None, 1 - best["dist"] if best else None, best["details"] if best else 0, sec, request.headers.get("user-agent", "")[:250]))
        os.makedirs(f"{V}/library/scans", exist_ok=True)
        small = im.copy()
        small.thumbnail((1600, 1600))
        small.save(f"{V}/library/scans/{scan_id}.jpg", quality=80)
        q("INSERT INTO ledger (workspace_id, sheet_id, kind, seconds) VALUES (%s,%s,'scan',%s)", (w, best["id"] if sure else None, sec))
        dirty.set()
        out = dict(scan_id=scan_id, matched=sure, seconds=sec, look_alikes=[] if sure else [dict(id=c["id"], name=c["name"], details=c["details"]) for c in alike],
                   candidates=[dict(id=c["id"], name=c["name"], similarity=round(1 - c["dist"], 3), details=c["details"]) for c in cands[:6]])
        if sure:
            out.update(opened(w, best["id"], request), outline=best["outline"], details=best["details"])
        return out

    def opened(w, sid, request):
        r = sheet_row(w, sid)
        newer = q("SELECT id, name, revision FROM sheets WHERE id=%s", (r["superseded_by"],), one=True) if r["superseded_by"] else None
        return dict(sheet=dict(brief(r), project=r["project"]), newer_revision=newer, project=project(r["project_id"], request), saved_answers=answers_of(w, sid))

    @api.get("/api/open/{sid}")
    def open_sheet(sid: int, request: Request, scan_id: int = 0):
        """Everything known about one sheet (used after the user picks among look-alike sheets)."""
        w = ws(request)
        if scan_id:
            q("UPDATE scans SET sheet_id=%s WHERE id=%s AND workspace_id=%s", (sid, scan_id, w))
            dirty.set()
        return opened(w, sid, request)

    def answers_of(w, sid):
        rows = q("SELECT id, question, answer_json, seconds, cost_usd, hits, created_at FROM answers WHERE sheet_id=%s AND status='done' ORDER BY hits DESC, id", (sid,))
        return [dict(id=r["id"], question=r["question"], answer=json.loads(r["answer_json"]), seconds=r["seconds"], cost_usd=r["cost_usd"], hits=r["hits"]) for r in rows]

    @api.get("/api/sheets/{sid}/answers/all")
    def sheet_answers(sid: int, request: Request):
        w = ws(request)
        sheet_row(w, sid)
        return answers_of(w, sid)

    @api.post("/api/answers")
    async def save_answer(request: Request):
        """Body JSON: {sheet_id, question, answer: {...engine result...}, seconds, cost_usd}. Stores an answer the engine computed."""
        w = ws(request)
        b = await request.json()
        sheet_row(w, b["sheet_id"])
        old = q("SELECT id FROM answers WHERE sheet_id=%s AND question_norm=%s", (b["sheet_id"], norm(b["question"])), one=True)
        args = (json.dumps(b["answer"], ensure_ascii=False), b.get("seconds", 0), b.get("cost_usd", 0))
        if old:
            q("UPDATE answers SET status='done', answer_json=%s, seconds=%s, cost_usd=%s WHERE id=%s", args + (old["id"],))
            aid = old["id"]
        else:
            aid = q("INSERT INTO answers (sheet_id, question, question_norm, answer_json, seconds, cost_usd) VALUES (%s,%s,%s,%s,%s,%s)",
                    (b["sheet_id"], b["question"], norm(b["question"])) + args)
        q("INSERT INTO ledger (workspace_id, sheet_id, kind, cost_usd, seconds) VALUES (%s,%s,'answer',%s,%s)", (w, b["sheet_id"], b.get("cost_usd", 0), b.get("seconds", 0)))
        dirty.set()
        return dict(id=aid)

    def ask_one(w, sid, question):
        r = q("SELECT * FROM answers WHERE sheet_id=%s AND question_norm=%s", (sid, norm(question)), one=True)
        if r and r["status"] == "done":
            q("UPDATE answers SET hits=hits+1 WHERE id=%s", (r["id"],))
            q("INSERT INTO ledger (workspace_id, sheet_id, kind, saved_usd, saved_seconds) VALUES (%s,%s,'reuse',%s,%s)", (w, sid, r["cost_usd"], r["seconds"]))
            return dict(sheet_id=sid, status="saved", answer_id=r["id"], answer=json.loads(r["answer_json"]), first_cost_usd=r["cost_usd"], first_seconds=r["seconds"])
        aid = r["id"] if r else q("INSERT INTO answers (sheet_id, question, question_norm, status) VALUES (%s,%s,%s,'pending')", (sid, question, norm(question)))
        return dict(sheet_id=sid, status="pending", answer_id=aid)

    @api.post("/api/ask")
    async def ask(request: Request):
        """Body JSON: {question, sheet_id} or {question, project_id}. A saved answer comes back at once; a new question is queued for the engine."""
        w = ws(request)
        b = await request.json()
        if b.get("project_id"):
            sheets = project(b["project_id"], request)["sheets"]
        else:
            sheets = [brief(sheet_row(w, b["sheet_id"]))]
        parts = [dict(ask_one(w, s["id"], b["question"]), sheet=s["name"]) for s in sheets]
        dirty.set()
        vals = [p["answer"].get("answer") for p in parts if p["status"] == "saved"]
        units = {p["answer"].get("unit") for p in parts if p["status"] == "saved"}
        total = None
        if len(vals) == len(parts) and len(parts) > 1 and all(isinstance(v, (int, float)) for v in vals) and len(units) == 1:
            total = dict(answer=round(sum(vals), 3), unit=units.pop())
        return dict(question=b["question"], parts=parts, total=total, pending=sum(p["status"] == "pending" for p in parts))

    @api.get("/api/answers/{aid}")
    def answer(aid: int, request: Request):
        w = ws(request)
        r = q("SELECT a.*, p.workspace_id FROM answers a JOIN sheets s ON s.id=a.sheet_id JOIN projects p ON p.id=s.project_id WHERE a.id=%s", (aid,), one=True)
        if not r or r["workspace_id"] != w:
            raise HTTPException(404, "no such answer")
        return dict(id=aid, sheet_id=r["sheet_id"], status=r["status"], question=r["question"], answer=json.loads(r["answer_json"]) if r["answer_json"] else None,
                    seconds=r["seconds"], cost_usd=r["cost_usd"])

    @api.get("/api/engine/next")
    def engine_next(request: Request):
        """For the answering engine: the oldest question still waiting, with where to get the drawing."""
        w = ws(request)
        r = q("SELECT a.id, a.sheet_id, a.question, s.file_name, s.sheet_json IS NOT NULL has_sheet FROM answers a JOIN sheets s ON s.id=a.sheet_id "
              "JOIN projects p ON p.id=s.project_id WHERE a.status='pending' AND p.workspace_id=%s ORDER BY a.id LIMIT 1", (w,), one=True)
        return r or {}

    @api.get("/api/stats")
    def stats(request: Request):
        w = ws(request)
        t = q("SELECT COALESCE(SUM(cost_usd),0) spent_usd, COALESCE(SUM(saved_usd),0) saved_usd, COALESCE(SUM(seconds),0) spent_seconds, "
              "COALESCE(SUM(saved_seconds),0) saved_seconds, SUM(kind='reuse') reused_answers, SUM(kind='answer') computed_answers, SUM(kind='scan') scans "
              "FROM ledger WHERE workspace_id=%s", (w,), one=True)
        n = q("SELECT COUNT(DISTINCT p.id) projects, COUNT(s.id) sheets FROM projects p LEFT JOIN sheets s ON s.project_id=p.id WHERE p.workspace_id=%s", (w,), one=True)
        return {k: (float(v) if v is not None else 0) for k, v in {**t, **n}.items()}

    @api.get("/api/scans")
    def scans(request: Request, limit: int = 50):
        w = ws(request)
        return q("SELECT c.id, c.sheet_id, s.name sheet, c.similarity, c.details, c.seconds, c.device, c.created_at FROM scans c LEFT JOIN sheets s ON s.id=c.sheet_id "
                 "WHERE c.workspace_id=%s ORDER BY c.id DESC LIMIT %s", (w, limit))

    from fastapi.middleware.cors import CORSMiddleware
    api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    return api
