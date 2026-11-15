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
    .pip_install("timm==1.0.12", "huggingface_hub==0.26.5", "safetensors", "fastapi[standard]==0.115.6", "pymysql==1.1.1", "pillow", "anthropic",
                 "numpy<2", "opencv-python-headless", "pymupdf", "segno")
    .run_function(_fetch_weights)
    .add_local_file(str(HERE / "phone.html"), "/root/phone.html")
    .add_local_file(str(HERE / "keypool.py"), "/root/keypool.py")
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
CREATE TABLE IF NOT EXISTS names (id INT AUTO_INCREMENT PRIMARY KEY, original VARCHAR(190) NOT NULL UNIQUE, english VARCHAR(190),
  room_type VARCHAR(40), source VARCHAR(20), created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS corrections (id INT AUTO_INCREMENT PRIMARY KEY, sheet_id INT NOT NULL, kind VARCHAR(16) NOT NULL,
  item_id VARCHAR(64) NULL, cls VARCHAR(40) NULL, bbox_json TEXT NULL, value VARCHAR(255) NULL, question_norm VARCHAR(500) NULL,
  note TEXT NULL, author VARCHAR(80) DEFAULT '', source VARCHAR(20) DEFAULT 'person', status VARCHAR(12) DEFAULT 'applied',
  note_id INT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY (sheet_id, status));
CREATE TABLE IF NOT EXISTS ledger (id INT AUTO_INCREMENT PRIMARY KEY, workspace_id INT NOT NULL, sheet_id INT NULL, kind VARCHAR(20),
  cost_usd DOUBLE DEFAULT 0, saved_usd DOUBLE DEFAULT 0, seconds FLOAT DEFAULT 0, saved_seconds FLOAT DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
"""


@app.function(image=image, volumes={V: VOL}, cpu=4, memory=8192, min_containers=1, max_containers=1, timeout=900, scaledown_window=1200,
              secrets=[modal.Secret.from_name("bob-anthropic"), modal.Secret.from_name("bob-anthropic-pool")])
@modal.concurrent(max_inputs=16)
@modal.asgi_app()
def web():
    import hashlib, io, json, re, subprocess, threading, time
    import cv2, numpy as np, pymysql, segno, timm, torch
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse, Response
    from PIL import Image

    import sys
    sys.path.insert(0, "/root")
    import keypool
    print("keypool:", keypool.install())             # Claude calls: Oliver's key, then kokoro's (bob-anthropic-pool)
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
    for stmt in ("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS counter_since DATETIME NULL",
                 "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS kind VARCHAR(40) DEFAULT ''",
                 "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS level_name VARCHAR(120) DEFAULT ''",
                 "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS level_order INT NULL",
                 "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS title VARCHAR(255) DEFAULT ''",
                 "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS survey_json TEXT NULL",
                 "ALTER TABLE projects ADD COLUMN IF NOT EXISTS summary_json LONGTEXT NULL",
                 "ALTER TABLE projects ADD COLUMN IF NOT EXISTS surveyed_at DATETIME NULL",
                 "ALTER TABLE answers ADD COLUMN IF NOT EXISTS asked_from VARCHAR(20) DEFAULT ''",
                 "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS sheet_no VARCHAR(40) DEFAULT ''",
                 "ALTER TABLE sheets ADD COLUMN IF NOT EXISTS revision_date VARCHAR(20) DEFAULT ''"):
        q(stmt)
    if not q("SELECT id FROM workspaces WHERE name='Demo'"):
        q("INSERT INTO workspaces (name, password_sha) VALUES ('Demo', %s)", (hashlib.sha256(PASSWORD.encode()).hexdigest(),))

    dirty = threading.Event()
    for r in q("SELECT id FROM sheets"):
        if not os.path.exists(f"{FILES}/{r['id']}/match.png"):
            print("dropping sheet record without files:", r["id"])
            q("UPDATE sheets SET superseded_by=NULL WHERE superseded_by=%s", (r["id"],))
            q("DELETE FROM sheets WHERE id=%s", (r["id"],))
    q("DELETE FROM projects WHERE id NOT IN (SELECT DISTINCT project_id FROM sheets)")

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

    def save_now():
        subprocess.run(f"mariadb-dump --socket={SOCK} -uroot --single-transaction bob > {DUMP}.tmp && mv {DUMP}.tmp {DUMP}", shell=True, check=True)
        VOL.commit()

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
            if g is None:                              # a record whose files are gone (never let one sheet break every scan)
                return np.zeros((0, 2), np.float32), None, (1, 1)
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

    ENGINE = modal.Function.from_name("bob-engine", "answer")
    FULL = modal.Function.from_name("bob-engine", "solve_full")
    SPLIT = modal.Function.from_name("bob-engine", "split")
    FILLER = re.compile(r"\b(in total|altogether|all together|together|total|please|shown|drawn|there|exactly)\b")

    def norm(s):
        """Question key for the library: same words, ignoring case, punctuation and filler ("in total", "shown")."""
        t = re.sub(r"[^\w]+", " ", s.lower(), flags=re.UNICODE)
        return re.sub(r"\s+", " ", FILLER.sub(" ", t)).strip()[:500]

    for row in q("SELECT id, question FROM answers"):
        q("UPDATE answers SET question_norm=%s WHERE id=%s", (norm(row["question"]), row["id"]))

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
                    convert_seconds=r["convert_seconds"], convert_cost_usd=r["convert_cost_usd"], created_at=str(r["created_at"]),
                    sheet_no=r.get("sheet_no", ""), revision_date=r.get("revision_date", ""), kind=r.get("kind", ""), level_name=r.get("level_name", ""),
                    level_order=r.get("level_order"), title=r.get("title", ""))

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
        rows = q("SELECT p.id, p.name, p.created_at, p.surveyed_at, COUNT(s.id) sheets, SUBSTRING_INDEX(GROUP_CONCAT(s.id ORDER BY s.level_order DESC, s.id), ',', 4) firsts "
                 "FROM projects p LEFT JOIN sheets s ON s.project_id=p.id WHERE p.workspace_id=%s GROUP BY p.id ORDER BY p.created_at DESC", (w,))
        for r in rows:
            r["created_at"], r["surveyed_at"] = str(r["created_at"]), (str(r["surveyed_at"]) if r["surveyed_at"] else None)
            r["thumbs"] = [int(x) for x in (r.pop("firsts") or "").split(",") if x]
            r["questions"] = q("SELECT COUNT(*) n FROM answers a JOIN sheets s ON s.id=a.sheet_id WHERE s.project_id=%s", (r["id"],), one=True)["n"]
        return rows

    @api.post("/api/projects")
    def new_project(request: Request, name: str):
        """Create an empty project (blueprints are added with POST /api/sheets?project=<name>)."""
        w = ws(request)
        name = name.strip()[:190]
        if not name:
            raise HTTPException(400, "a project needs a name")
        if q("SELECT id FROM projects WHERE workspace_id=%s AND name=%s", (w, name), one=True):
            raise HTTPException(409, "a project with this name exists")
        pid = q("INSERT INTO projects (workspace_id, name) VALUES (%s, %s)", (w, name))
        dirty.set()
        return dict(id=pid, name=name)

    @api.get("/api/projects/{pid}")
    def project(pid: int, request: Request):
        w = ws(request)
        p = q("SELECT id, name, created_at FROM projects WHERE id=%s AND workspace_id=%s", (pid, w), one=True)
        if not p:
            raise HTTPException(404, "no such project in this workspace")
        rows = q("SELECT id, name, file_name, revision, superseded_by, width, height, project_id, sheet_json IS NOT NULL has_sheet, convert_seconds, "
                 "convert_cost_usd, created_at, sheet_no, revision_date, kind, level_name, level_order, title FROM sheets WHERE project_id=%s "
                 "ORDER BY level_order IS NULL, level_order DESC, name", (pid,))
        sm = q("SELECT summary_json, surveyed_at FROM projects WHERE id=%s", (pid,), one=True)
        return dict(p, sheets=[brief(r) for r in rows], survey=json.loads(sm["summary_json"]) if sm and sm["summary_json"] else None,
                    surveyed_at=str(sm["surveyed_at"]) if sm and sm["surveyed_at"] else None)

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
        no, date = title_block(data, file_name or name)
        q("UPDATE sheets SET sheet_no=%s, revision_date=%s WHERE id=%s", (no, date, sid))
        if supersedes:
            sheet_row(w, supersedes)
            q("UPDATE sheets SET superseded_by=%s WHERE id=%s", (sid, supersedes))
        if convert_seconds or convert_cost_usd:
            q("INSERT INTO ledger (workspace_id, sheet_id, kind, cost_usd, seconds) VALUES (%s,%s,'convert',%s,%s)", (w, sid, convert_cost_usd, convert_seconds))
        VOL.commit()                                   # the engine reads the file next: make it visible on the volume now
        dirty.set()
        sug = None
        if not supersedes:
            try:
                sug, _ = revision_candidates(w, sid, pid)
            except Exception as e:
                print("revision check:", e)
        return dict(id=sid, project_id=pid, width=im.width, height=im.height, sheet_no=no, revision_date=date, revision_suggestion=sug)

    @api.put("/api/sheets/{sid}/sheet")
    async def put_sheet(sid: int, request: Request, convert_seconds: float = 0.0, convert_cost_usd: float = 0.0):
        """Body: the converted sheet (drawqa-sheet JSON) so nobody has to convert this drawing again."""
        w = ws(request)
        sheet_row(w, sid)
        q("UPDATE sheets SET sheet_json=%s, convert_seconds=%s, convert_cost_usd=%s WHERE id=%s", ((await request.body()).decode(), convert_seconds, convert_cost_usd, sid))
        q("INSERT INTO ledger (workspace_id, sheet_id, kind, cost_usd, seconds) VALUES (%s,%s,'convert',%s,%s)", (w, sid, convert_cost_usd, convert_seconds))
        dirty.set()
        return dict(ok=True)

    @api.patch("/api/sheets/{sid}")
    def rename_sheet(sid: int, request: Request, name: str = "", revision: str = ""):
        """Change a sheet's display name (and revision label)."""
        w = ws(request)
        sheet_row(w, sid)
        if name:
            q("UPDATE sheets SET name=%s WHERE id=%s", (name, sid))
        if revision:
            q("UPDATE sheets SET revision=%s WHERE id=%s", (revision, sid))
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
        return [dict(id=r["id"], question=r["question"], answer=present(sid, r["question"], json.loads(r["answer_json"])), seconds=r["seconds"], cost_usd=r["cost_usd"], hits=r["hits"]) for r in rows]

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
            return dict(sheet_id=sid, status="saved", answer_id=r["id"], answer=present(sid, question, json.loads(r["answer_json"])), first_cost_usd=r["cost_usd"], first_seconds=r["seconds"])
        aid = r["id"] if r else q("INSERT INTO answers (sheet_id, question, question_norm, status, asked_from) VALUES (%s,%s,%s,'pending','phone')", (sid, question, norm(question)))
        try:                                    # the engine on Modal (library/modal_engine.py) answers it and posts the result back
            f = q("SELECT file_name FROM sheets WHERE id=%s", (sid,), one=True)["file_name"]
            ENGINE.spawn(aid, sid, f, question)
        except Exception as e:
            print("engine not reachable:", e)
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
        return dict(id=aid, sheet_id=r["sheet_id"], status=r["status"], question=r["question"], answer=present(r["sheet_id"], r["question"], json.loads(r["answer_json"])) if r["answer_json"] else None,
                    seconds=r["seconds"], cost_usd=r["cost_usd"])

    @api.get("/api/find")
    def find_sheet(request: Request, file_name: str):
        """The sheet of this workspace stored from a file of this name, if any."""
        w = ws(request)
        r = q("SELECT s.id FROM sheets s JOIN projects p ON p.id=s.project_id WHERE p.workspace_id=%s AND s.file_name=%s ORDER BY s.id LIMIT 1", (w, file_name), one=True)
        if not r:
            raise HTTPException(404, "not in the library")
        return dict(id=r["id"])

    def store(w, sid, question, result, seconds, parts=None, asked_from=""):
        old = q("SELECT id FROM answers WHERE sheet_id=%s AND question_norm=%s", (sid, norm(question)), one=True)
        body = json.dumps(dict(result, part_ids=parts) if parts else result, ensure_ascii=False, default=str)
        cost = 0.0 if parts else audit_cost(result)          # a combined answer's parts were each stored (and costed) already
        if old:
            q("UPDATE answers SET status='done', answer_json=%s, seconds=%s, cost_usd=%s WHERE id=%s", (body, seconds, cost, old["id"]))
            aid = old["id"]
        else:
            aid = q("INSERT INTO answers (sheet_id, question, question_norm, answer_json, seconds, cost_usd, asked_from) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (sid, question, norm(question), body, seconds, cost, asked_from))
        q("INSERT INTO ledger (workspace_id, sheet_id, kind, cost_usd, seconds) VALUES (%s,%s,'answer',%s,%s)", (w, sid, cost, seconds))
        return aid

    def cached(w, sid, question):
        r = q("SELECT * FROM answers WHERE sheet_id=%s AND question_norm=%s AND status='done'", (sid, norm(question)), one=True)
        if not r:
            return None
        res = json.loads(r["answer_json"])
        if "audit" not in res:                  # saved before audit trails (no evidence to draw): answer it again, properly
            return None
        q("UPDATE answers SET hits=hits+1 WHERE id=%s", (r["id"],))
        q("INSERT INTO ledger (workspace_id, sheet_id, kind, saved_usd, saved_seconds) VALUES (%s,%s,'reuse',%s,%s)", (w, sid, r["cost_usd"], r["seconds"]))
        return dict(id=r["id"], result=res, seconds=r["seconds"], created=str(r["created_at"]), hits=r["hits"] + 1)

    @api.post("/api/solve")
    async def solve_api(request: Request):
        """Body {sheet_id, question}. The engine's full answer (evidence for the blueprint view) with its audit trail.
        A question with several parts is split; each part is answered (or taken from the library) and kept on its own,
        so a later question that is one of the parts is answered from the library."""
        import asyncio
        w = ws(request)
        b = await request.json()
        sid, question = int(b["sheet_id"]), b["question"].strip()
        frm = (b.get("asked_from") or "laptop")[:20]
        sh = sheet_row(w, sid)
        t0 = time.time()
        hit = cached(w, sid, question)
        if hit:
            r = hit["result"]
            r["library"] = dict(from_library=True, answer_id=hit["id"], first_seconds=hit["seconds"], first_asked=hit["created"], times_reused=hit["hits"], now_seconds=round(time.time() - t0, 3))
            r.setdefault("audit", {}).setdefault("steps", [])
            r["audit"]["reused"] = f"answered from the library (reused {hit['hits']} times; first answer took {hit['seconds']:.2f} s of engine time, now $0)"
            dirty.set()
            return present(sid, question, r)
        sp = await SPLIT.remote.aio(question)
        parts = sp["parts"]
        done = []
        for pq in parts:
            hit = cached(w, sid, pq) if len(parts) > 1 else None
            if hit:
                done.append(dict(question=pq, result=hit["result"], seconds=hit["seconds"], from_library=True, answer_id=hit["id"]))
                continue
            out = await FULL.remote.aio(sid, sh["file_name"], pq)
            aid = store(w, sid, pq, out["result"], out["seconds"], asked_from=frm) if len(parts) > 1 else None
            done.append(dict(question=pq, result=out["result"], seconds=out["seconds"], from_library=False, answer_id=aid))
        if len(done) == 1:
            r = done[0]["result"]
            aid = store(w, sid, question, r, done[0]["seconds"], asked_from=frm)
            r["library"] = dict(from_library=False, answer_id=aid, first_seconds=done[0]["seconds"], times_asked=1, cost_usd=audit_cost(r))
            dirty.set()
            return present(sid, question, r)
        rs = [d["result"] for d in done]
        vals = [x.get("answer") for x in rs]
        units = {x.get("unit") for x in rs}
        numeric = all(isinstance(v, (int, float)) for v in vals)
        order = ["read", "measured", "detected", "estimated", "cannot determine"]
        tiers = [(x.get("honesty") or {}).get("tier") or "cannot determine" for x in rs]
        weakest = max(tiers, key=lambda t: order.index(t) if t in order else 4)
        if sp["total"] and numeric and len(units) == 1:
            answer, unit = round(sum(vals), 3), units.pop()
            how = "the parts added up, because the question asks for a total"
        else:
            answer, unit = " · ".join(f"{v if v is not None else 'cannot determine'}" for v in vals), None
            how = "each part answered on its own"
        ev, seen = [], set()
        for k, (d, x) in enumerate(zip(done, rs)):         # the engine's own ids: the blueprint view places items by them
            for e in x.get("evidence") or []:
                if e.get("id") in seen:
                    continue
                seen.add(e.get("id"))
                ev.append(dict(e, part=k + 1))
        combined = dict(answer=answer, unit=unit, evidence=ev, source_type=rs[0].get("source_type"),
                        honesty=dict(tier=weakest, basis=f"weakest of the parts ({', '.join(tiers)}); {how}"),
                        confidence=min((x.get("confidence") or 0) for x in rs) if all(x.get("confidence") is not None for x in rs) else None,
                        explanation=" | ".join(f"{d['question']} -> {x.get('answer')}" for d, x in zip(done, rs)),
                        method="split into parts", parts=[dict(question=d["question"], answer=x.get("answer"), unit=x.get("unit"),
                                                             tier=(x.get("honesty") or {}).get("tier"), from_library=d["from_library"],
                                                             seconds=d["seconds"], audit=x.get("audit")) for d, x in zip(done, rs)])
        combined["audit"] = dict(question=question, split=[d["question"] for d in done],
                                 steps=[dict(stage="Question split", actor="splitter (patterns, no language model)",
                                             detail=f"{len(done)} parts: " + "; ".join(d["question"] for d in done))]
                                 + [dict(stage=f"Part {k + 1}", actor="library" if d["from_library"] else "engine on Modal",
                                         detail=("taken from the library, $0" if d["from_library"] else f"answered by the engine in {d['seconds']:.2f} s, kept for next time")
                                         + f": {d['question']} -> {x.get('answer')}. "
                                         + " · ".join(f"{st['stage']}: {st['actor']}" for st in (x.get("audit") or {}).get("steps", [])),
                                         confidence=x.get("confidence")) for k, (d, x) in enumerate(zip(done, rs))]
                                 + [dict(stage="Combined", actor="library", detail=how)])
        aid = store(w, sid, question, combined, sum(d["seconds"] for d in done if not d["from_library"]), parts=[d["answer_id"] for d in done], asked_from=frm)
        combined["library"] = dict(from_library=False, answer_id=aid, parts_from_library=sum(d["from_library"] for d in done), times_asked=1)
        dirty.set()
        return present(sid, question, combined)

    # ---- printed room names in English (kept with the original; one translation per name, stored) -------------------
    WORDS = {
        "Bedroom": "MH MAKUUH MAKUUHUONE MHUONE BR BEDROOM SCHLAFZIMMER SCHLAFEN CHAMBRE CAMERA SOVEROM SOVRUM SLAAPKAMER DORMITORIO",
        "Kitchen": "K KK KEITTIO KEITIO KTIO KITCHEN KUCHE KUECHE CUISINE CUCINA KOKKEN KEUKEN COCINA KOCHEN",
        "Bathroom": "KH KPH PH PESUH PESUHUONE SH BATH BATHROOM BAD BAGNO SDB BAIN BADKAMER BANO DOUCHE DUSCHE",
        "WC": "WC TOILET TOILETTE TOILETTEN TUALETTRUUM",
        "Sauna": "SAUNA",
        "Living room": "OH OLOH OLOHUONE LIVING WOHNZIMMER WOHNEN SOGGIORNO SEJOUR STUE WOONKAMER SALON ELUTUBA",
        "Dining": "RUOKAILU RT DINING ESSZIMMER ESSEN REPAS PRANZO",
        "Entrance": "ET ETEINEN TK TUULIK TUULIKAAPPI HALL ENTRY ENTREE INGRESSO ESIK DIELE EINGANG",
        "Corridor": "COULOIR KORRIDOR CORRIDOIO CORRIDOR GANG GANGE HALLWAY KORIDOR FLUR",
        "Storage": "VH VAR VARASTO KOMERO STORAGE ABSTELLRAUM RIPOSTIGLIO CLOS CLOSET CLO REDUIT REDUIT ABIRUUM BERGING",
        "Utility room": "KHH KODINHOITOH TEKN LAUNDRY UTILITY WD HVAC MECH MECHANICAL BUANDERIE WASCHKUCHE",
        "Dressing room": "PUKUH PUKUHUONE",
        "Balcony": "PARVEKE PARV BALCONY BALKON BALCONE BALC BALCON",
        "Terrace": "TERASSI TERRACE TERRASSE TERRAZZA",
        "Loggia": "LOGGIA",
        "Room": "PIECE ZIMMER ROOM STANZA KAMER HUONE",
        "Stairwell": "TREPPENHAUS ESCALIER CAGE TREPIKODA STAIR STAIRS SCALA",
        "Office": "BURO BUERO BUREAU UFFICIO OFFICE",
        "Apartment": "WHG WOHNUNG APPARTEMENT LOGEMENT APARTMENT",
    }
    WORD = {w: en for en, ws in WORDS.items() for w in ws.split()}

    def _key(t):
        import unicodedata
        u = unicodedata.normalize("NFKD", str(t).upper())
        return "".join(ch for ch in u if ch.isascii() and ch.isalnum())

    def from_words(name):
        """Every word of the name found in the word list -> its English, joined ("Séjour/repas" -> "Living room / Dining")."""
        whole = WORD.get(_key(name))
        if whole:
            return whole
        parts = [w for w in re.split(r"[\s/\-–,()+.]+", str(name)) if _key(w)]
        found = [WORD.get(_key(w)) for w in parts]
        if parts and all(found):
            return " / ".join(dict.fromkeys(found))
        return None

    def from_model(names):
        """Names the word list does not know: Claude Haiku, once, marked as a guess."""
        import anthropic
        prompt = ("These are room names printed on architectural floor plans (any language, often abbreviated). For each, give the "
                  "plain English room name a builder would use. Reply with JSON only: {\"<name>\": \"<english>\", ...}. "
                  "If a name is not a room name (a code, a number), give it unchanged.\n" + json.dumps(names, ensure_ascii=False))
        r = anthropic.Anthropic(timeout=40.0, max_retries=1).messages.create(model="claude-haiku-5-5", max_tokens=1500,
                                                                            messages=[{"role": "user", "content": prompt}])
        t = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return json.loads(t[t.index("{"):t.rindex("}") + 1])

    @api.post("/api/names")
    async def names(request: Request):
        """Body {"names": [...]} -> {name: {english, source}} (source: stored, words, model guess, unchanged)."""
        ws(request)
        b = await request.json()
        want = [str(n)[:190] for n in dict.fromkeys(b.get("names") or []) if str(n).strip()][:200]
        out, todo = {}, []
        for n in want:
            row = q("SELECT english, source FROM names WHERE original=%s", (n,), one=True)
            if row:
                out[n] = dict(english=row["english"], source=row["source"])
                continue
            en = from_words(n)
            if en:
                q("INSERT IGNORE INTO names (original, english, source) VALUES (%s,%s,'words')", (n, en))
                out[n] = dict(english=en, source="words")
            else:
                todo.append(n)
        if todo:
            try:
                got = from_model(todo)
            except Exception as e:
                got = {}
                print("names: model unavailable", e)
            for n in todo:
                en = str(got.get(n) or n)[:190]
                src = "model guess" if got.get(n) and got.get(n) != n else "unchanged"
                q("INSERT IGNORE INTO names (original, english, source) VALUES (%s,%s,%s)", (n, en, src))
                out[n] = dict(english=en, source=src)
        dirty.set()
        return out

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

    # ---- prices (Claude API, per million tokens; claude-api reference cached 2026-11-03) -----------------------------
    PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50)}

    def call_cost(model, tin, tout):
        a, b = PRICE.get(model, PRICE["claude-opus-5-5"])
        return (tin * a + tout * b) / 1e6

    def audit_cost(result):
        """What the language-model calls behind one answer cost (the engine records model and tokens per call)."""
        return round(sum(call_cost(c.get("model", ""), c.get("in", 0), c.get("out", 0)) for c in ((result or {}).get("audit") or {}).get("model_calls") or []), 6)

    # ---- corrections: a person's fixes to what the system read, applied to every answer on that blueprint ----------
    def corrections_of(sid, status="applied", upto=None):
        if upto is not None:
            return q("SELECT * FROM corrections WHERE sheet_id=%s AND status=%s AND id<=%s ORDER BY id", (sid, status, upto))
        return q("SELECT * FROM corrections WHERE sheet_id=%s AND status=%s ORDER BY id", (sid, status))

    def counted_classes(result):
        it = result.get("intent") or {}
        cl = {str(e.get("cls") or "").lower() for e in result.get("evidence") or [] if e.get("cls")}
        if it.get("cls"):
            cl.add(str(it["cls"]).lower())
        return {c for c in cl if c}

    def present(sid, question, result, upto=None):
        """The answer as the library shows it: the system's answer with every applied correction for this blueprint on top.
        The system's own number is kept (engine_answer) and the audit says who changed what."""
        if not isinstance(result, dict):
            return result
        cs = [c for c in corrections_of(sid, upto=upto) if c["kind"] != "note"]
        if not cs:
            return result
        r = json.loads(json.dumps(result, default=str))
        steps = r.setdefault("audit", {}).setdefault("steps", [])
        qn = norm(question or "")
        over = [c for c in cs if c["kind"] == "answer" and c["question_norm"] == qn]
        ev = r.get("evidence") or []
        is_count = r.get("unit") == "count" or (isinstance(r.get("answer"), int) and not r.get("unit"))
        if over:
            c = over[-1]
            try:
                v = float(c["value"]); v = int(v) if v.is_integer() else v
            except (TypeError, ValueError):
                v = c["value"]
            r["engine_answer"], r["answer"] = r.get("answer"), v
            r["honesty"] = dict(tier="corrected", basis=f"corrected by {c['author'] or 'a person'}" + (f": {c['note']}" if c["note"] else ""))
            r["corrected"] = dict(by=c["author"], at=str(c["created_at"]), kind="answer")
            steps.append(dict(stage="Corrected", actor=c["author"] or "a person", detail=f"answer set to {v} (the system said {r['engine_answer']})" + (f"; note: {c['note']}" if c["note"] else "")))
            return r
        if is_count and isinstance(r.get("answer"), (int, float)):
            cls = counted_classes(r)
            removed = [c for c in cs if c["kind"] == "remove" and (c["cls"] or "").lower() in cls]
            added = [c for c in cs if c["kind"] == "add" and (c["cls"] or "").lower() in cls]
            if removed or added:
                gone = {c["item_id"] for c in removed if c["item_id"]}
                r["evidence"] = [e for e in ev if e.get("id") not in gone]
                r["engine_answer"] = r["answer"]
                r["answer"] = int(r["answer"]) - len(removed) + len(added)
                r["added_by_person"] = [dict(id=f"c{c['id']}", cls=c["cls"], bbox=json.loads(c["bbox_json"]) if c["bbox_json"] else None, by=c["author"], source=c["source"]) for c in added]
                who = sorted({c["author"] or "a person" for c in removed + added})
                r["honesty"] = dict((r.get("honesty") or {}), tier="corrected",
                                    basis=f"{r['engine_answer']} found by the system; {len(removed)} removed and {len(added)} added by {', '.join(who)}")
                r["corrected"] = dict(by=", ".join(who), removed=len(removed), added=len(added), kind="items")
                steps.append(dict(stage="Corrected", actor=", ".join(who), detail=f"system {r['engine_answer']} -> {r['answer']}: "
                                  + "; ".join(f"{'-' if c['kind'] == 'remove' else '+'}1 {c['cls']}" + (f" ({c['note']})" if c['note'] else "")  for c in removed + added)))
        if not is_count and len(ev) == 1:
            ar = [c for c in cs if c["kind"] == "area" and c["item_id"] == ev[0].get("id")]
            if ar:
                c = ar[-1]
                r["engine_answer"], r["answer"] = r.get("answer"), float(c["value"])
                r["honesty"] = dict(tier="corrected", basis=f"area set by {c['author'] or 'a person'}")
                r["corrected"] = dict(by=c["author"], kind="area")
                steps.append(dict(stage="Corrected", actor=c["author"] or "a person", detail=f"area of {ev[0].get('id')} set to {c['value']} (system: {r['engine_answer']})"))
        return r

    def _box(v):
        try:
            b = json.loads(v) if v else None
            return b if isinstance(b, list) and len(b) == 4 else None
        except ValueError:
            return None

    @api.get("/api/sheets/{sid}/edits")
    def edits(sid: int, request: Request):
        """What the reading of this blueprint looks like after the applied corrections (for the viewer): items removed by id,
        items added with their box in view-image pixels, and the view image's size (to convert to sheet pixels)."""
        w = ws(request)
        r = sheet_row(w, sid)
        k = min(1.0, 2400 / max(r["width"] or 1, r["height"] or 1))
        cs = [c for c in corrections_of(sid) if c["kind"] in ("add", "remove")]
        return dict(width=r["width"], height=r["height"], view_scale=k,
                    removed=[dict(id=c["item_id"], cls=c["cls"], correction=c["id"], bbox=_box(c["value"])) for c in cs if c["kind"] == "remove" and c["item_id"]],
                    added=[dict(id=f"c{c['id']}", cls=c["cls"], bbox=json.loads(c["bbox_json"]) if c["bbox_json"] else None, by=c["author"]) for c in cs if c["kind"] == "add"])

    @api.get("/api/sheets/{sid}/timeline")
    def timeline(sid: int, request: Request):
        """Every change to what the system read on this blueprint, oldest first, with the answers before and after each one.
        Linear: going back to a step undoes everything after it (kept as 'undone', nothing is deleted)."""
        w = ws(request)
        sheet_row(w, sid)
        cs = q("SELECT * FROM corrections WHERE sheet_id=%s AND kind<>'note' AND status IN ('applied','undone') ORDER BY id", (sid,))
        notes = {n["id"]: n for n in q("SELECT id, note, author FROM corrections WHERE sheet_id=%s AND kind='note'", (sid,))}
        ans = q("SELECT question, answer_json FROM answers WHERE sheet_id=%s AND status='done' ORDER BY hits DESC, id LIMIT 12", (sid,))
        def values(upto):
            out = []
            for a in ans:
                r = present(sid, a["question"], json.loads(a["answer_json"]), upto=upto)
                out.append(dict(question=a["question"], answer=r.get("answer"), unit=r.get("unit")))
            return out
        steps = [dict(id=0, what="the system's own reading (no corrections)", when=None, by="the system", status="applied", answers=values(0))]
        for c in cs:
            what = {"add": f"+1 {c['cls']}", "remove": f"-1 {c['cls']}" + (f" ({c['item_id']})" if c["item_id"] else ""), "answer": f"answer set to {c['value']}",
                    "area": f"area of {c['item_id']} set to {c['value']}", "rename": f"renamed {c['item_id']} to {c['value']}"}.get(c["kind"], c["kind"])
            n = notes.get(c["note_id"])
            steps.append(dict(id=c["id"], what=what, why=c["note"], from_note=(n["note"] if n else None), when=str(c["created_at"]), by=c["author"],
                              source=c["source"], status=c["status"], answers=values(c["id"]) if c["status"] == "applied" else None))
        return dict(steps=steps)

    @api.post("/api/sheets/{sid}/revert_to/{cid}")
    def revert_to(sid: int, cid: int, request: Request):
        """Go back to the state right after change cid (0 = the system's own reading): later changes become 'undone'."""
        w = ws(request)
        sheet_row(w, sid)
        q("UPDATE corrections SET status='undone' WHERE sheet_id=%s AND id>%s AND status='applied' AND kind<>'note'", (sid, cid))
        dirty.set()
        return dict(ok=True)

    @api.get("/api/sheets/{sid}/corrections")
    def list_corrections(sid: int, request: Request):
        w = ws(request)
        sheet_row(w, sid)
        return [dict(r, created_at=str(r["created_at"]), bbox=json.loads(r["bbox_json"]) if r["bbox_json"] else None) for r in
                q("SELECT * FROM corrections WHERE sheet_id=%s AND status<>'undone' ORDER BY id DESC", (sid,))]

    @api.post("/api/sheets/{sid}/corrections")
    async def add_correction(sid: int, request: Request):
        """Body {kind: add|remove|answer|area|rename, cls?, item_id?, bbox?, value?, question?, note?, author?}."""
        w = ws(request)
        sheet_row(w, sid)
        b = await request.json()
        if b.get("kind") not in ("add", "remove", "answer", "area", "rename"):
            raise HTTPException(400, "kind must be add, remove, answer, area or rename")
        cid = q("INSERT INTO corrections (sheet_id, kind, item_id, cls, bbox_json, value, question_norm, note, author, source, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'person','applied')",
                (sid, b["kind"], b.get("item_id"), (b.get("cls") or "").lower() or None, json.dumps(b["bbox"]) if b.get("bbox") else None,
                 None if b.get("value") is None else str(b["value"]), norm(b["question"]) if b.get("question") else None,
                 b.get("note") or None, (b.get("author") or "")[:80]))
        dirty.set()
        return dict(id=cid)

    @api.post("/api/corrections/{cid}/{action}")
    def act_correction(cid: int, action: str, request: Request, by: str = ""):
        """accept (a proposal becomes applied), reject (a proposal is declined), undo (an applied correction is taken back)."""
        w = ws(request)
        c = q("SELECT * FROM corrections WHERE id=%s", (cid,), one=True)
        if not c:
            raise HTTPException(404, "no such correction")
        sheet_row(w, c["sheet_id"])
        new = {"accept": "applied", "reject": "rejected", "undo": "undone"}.get(action)
        if not new:
            raise HTTPException(400, "action must be accept, reject or undo")
        q("UPDATE corrections SET status=%s WHERE id=%s", (new, cid))
        if action == "accept" and c["source"] == "opus":
            q("UPDATE corrections SET author=%s WHERE id=%s", (f"{by or 'a person'} (proposed by Opus, accepted)"[:80], cid))
        dirty.set()
        return dict(id=cid, status=new)

    # ---- circle and comment: Opus looks at the circled part and proposes fixes; a person accepts or rejects ---------
    NOTE_PROMPT = """You check one part of a construction blueprint that a person circled because our automatic reading
missed something or got it wrong. The image is the circled part only.
Their comment: {comment}
{context}
Propose concrete fixes to the reading, only what you can actually see in the image. Kinds:
  "add"    an item of class cls that is drawn here but missing from the reading (box = where it is)
  "remove" an item of class cls that the reading has here but is not really there / is something else (box = where)
  "answer" the right value for the question, if the image alone settles it (value)
Classes: door, window, wall, wc, sink, shower, bathtub, stair, column, room, other (use the closest).
Boxes are fractions of the image: [x0, y0, x1, y1] with 0,0 top left.
Reply with JSON only: {{"summary": "<one sentence on what you see>", "proposals": [{{"kind": "...", "cls": "...", "box": [..], "value": null, "reason": "<short>"}}]}}
If nothing needs fixing, give an empty proposals list and say why in the summary."""

    @api.post("/api/sheets/{sid}/notes")
    async def add_note(sid: int, request: Request):
        """Body {image: base64 JPEG of the circled part, box: [x0,y0,x1,y1] in view-image pixels, comment, author, question?, answer?,
        evidence_classes?}. Stores the note, asks Opus for proposals, stores them as proposed corrections, returns them."""
        import anthropic, base64
        w = ws(request)
        sheet_row(w, sid)
        b = await request.json()
        box = b.get("box") or [0, 0, 1, 1]
        nid = q("INSERT INTO corrections (sheet_id, kind, bbox_json, note, author, source, status) VALUES (%s,'note',%s,%s,%s,'person','applied')",
                (sid, json.dumps(box), b.get("comment") or "", (b.get("author") or "")[:80]))
        counted = [x for x in (b.get("counted_inside") or []) if x.get("id")][:30]   # counted for the answer, mark inside the circle
        others = [x for x in (b.get("items_inside") or []) if x.get("id") and x["id"] not in {c["id"] for c in counted}][:40]
        inside = counted + others
        ctx = ""
        if b.get("counted_inside") is not None or b.get("items_inside") is not None:
            ctx += ("Items our reading has inside the circled part: " + ", ".join(f"{x.get('cls')}" + (f" (counted, mark {x.get('mark')})" if x in counted else "") for x in inside)
                    if inside else "Our reading has NO items inside the circled part.") + " A 'remove' can only refer to one of these.\n"
        if b.get("question"):
            ctx = f"The question on screen: {b['question']}\nThe system's answer: {b.get('answer')}"
            if b.get("evidence_classes"):
                ctx += f" (counted items: {', '.join(b['evidence_classes'])})"
        os.makedirs(f"{FILES}/{sid}/notes", exist_ok=True)
        img = base64.b64decode(b["image"].split(",")[-1])
        open(f"{FILES}/{sid}/notes/{nid}.jpg", "wb").write(img)
        t0 = time.time()
        client = anthropic.Anthropic(timeout=90.0, max_retries=1)
        msgs = [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(img).decode()}},
                                             {"type": "text", "text": NOTE_PROMPT.format(comment=b.get("comment") or "(none)", context=ctx)}]}]
        try:
            r = client.beta.messages.create(model="claude-opus-5-5", max_tokens=4000, betas=["server-side-fallback-2026-07-01"], fallbacks="default", messages=msgs)
        except TypeError:                                  # an SDK without server-side fallbacks
            r = client.messages.create(model="claude-opus-5-5", max_tokens=4000, messages=msgs)
        if r.stop_reason == "refusal":
            raise HTTPException(502, "the model declined to look at this part")
        text = "".join(getattr(x, "text", "") for x in r.content if getattr(x, "type", "") == "text")
        try:
            out = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except ValueError:
            out = dict(summary=text[:300], proposals=[])
        cost = call_cost("claude-opus-5-5", r.usage.input_tokens, r.usage.output_tokens)
        sec = round(time.time() - t0, 2)
        q("INSERT INTO ledger (workspace_id, sheet_id, kind, cost_usd, seconds) VALUES (%s,%s,'opus note',%s,%s)", (w, sid, cost, sec))
        x0, y0, x1, y1 = box
        props = []
        for p in (out.get("proposals") or [])[:12]:
            if p.get("kind") not in ("add", "remove", "answer"):
                continue
            item_id = None
            if p["kind"] == "remove" and (b.get("counted_inside") is not None or b.get("items_inside") is not None):   # bind to an item in the circle, or drop
                same = [x for x in inside if (x.get("cls") or "").lower() == (p.get("cls") or "").lower()]
                if not same:
                    out["summary"] = (out.get("summary") or "") + " (A removal was proposed, but nothing counted is inside the circle, so it was left out.)"
                    continue
                item_id, p["cls"] = same[0]["id"], same[0].get("cls") or p.get("cls")
                p["value"] = json.dumps(same[0].get("bbox")) if same[0].get("bbox") else None
                inside = [x for x in inside if x["id"] != item_id]
            pb = p.get("box")
            vb = [x0 + pb[0] * (x1 - x0), y0 + pb[1] * (y1 - y0), x0 + pb[2] * (x1 - x0), y0 + pb[3] * (y1 - y0)] if isinstance(pb, list) and len(pb) == 4 else None
            cid = q("INSERT INTO corrections (sheet_id, kind, item_id, cls, bbox_json, value, question_norm, note, author, source, status, note_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'opus','proposed',%s)",
                    (sid, p["kind"], item_id, (p.get("cls") or "").lower() or None, json.dumps(vb) if vb else None, None if p.get("value") is None else str(p["value"]),
                     norm(b["question"]) if p["kind"] == "answer" and b.get("question") else None, (p.get("reason") or "")[:500],
                     "proposed by Opus", nid))
            props.append(dict(id=cid, kind=p["kind"], cls=p.get("cls"), item_id=item_id, bbox=vb, value=p.get("value"), reason=p.get("reason")))
        dirty.set()
        return dict(note_id=nid, summary=out.get("summary"), proposals=props, model=r.model, cost_usd=round(cost, 5), seconds=sec, paid_by=keypool.last(),
                    tokens=dict(input=r.usage.input_tokens, output=r.usage.output_tokens))

    # ---- project survey: Opus looks at all the blueprints of a project at once ------------------------------------
    SURVEY_PROMPT = """These are the blueprints of one construction project, one image each, in upload order. Each image is
preceded by its label "SHEET <id>". Tell us what the set is, so the files can be organised by floor and questions can be
asked per floor or for the whole project. Use only what you can see (title blocks, labels, the drawings).
Reply with JSON only:
{{"project": {{"building": "<what the building is, if shown>", "summary": "<2-3 sentences: what the set contains>",
  "levels": ["<floor names from the TOP floor down, as printed>"], "scale_notes": "<printed scales, or that none are printed>"}},
 "sheets": [{{"id": <id>, "kind": "floor plan|elevation|section|site plan|roof plan|schedule|detail|cover|photo|other",
   "level": "<floor this sheet shows, as printed, or empty>", "level_order": <integer, higher = higher floor; basement < 0; null if not a floor>,
   "title": "<sheet title or number as printed, short>", "notes": "<one short line: what is on it>"}}]}}
{names}"""

    @api.post("/api/projects/{pid}/survey")
    def survey(pid: int, request: Request):
        """One Opus call over small images of every blueprint in the project: kind, floor and its order, title; and a
        project summary. Stored on the sheets and the project (re-run after adding blueprints)."""
        import anthropic, base64
        w = ws(request)
        p = q("SELECT id, name FROM projects WHERE id=%s AND workspace_id=%s", (pid, w), one=True)
        if not p:
            raise HTTPException(404, "no such project")
        rows = q("SELECT id, name, file_name FROM sheets WHERE project_id=%s ORDER BY id LIMIT 40", (pid,))
        if not rows:
            raise HTTPException(400, "the project has no blueprints yet")
        content = []
        for r in rows:
            im = Image.open(f"{FILES}/{r['id']}/view.jpg").convert("RGB")
            im.thumbnail((1000, 1000), Image.LANCZOS)
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=80)
            content.append({"type": "text", "text": f"SHEET {r['id']} (file {r['file_name']})"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(buf.getvalue()).decode()}})
        content.append({"type": "text", "text": SURVEY_PROMPT.format(names="")})
        t0 = time.time()
        client = anthropic.Anthropic(timeout=180.0, max_retries=1)
        msgs = [{"role": "user", "content": content}]
        try:
            r = client.beta.messages.create(model="claude-opus-5-5", max_tokens=6000, betas=["server-side-fallback-2026-07-01"], fallbacks="default", messages=msgs)
        except TypeError:
            r = client.messages.create(model="claude-opus-5-5", max_tokens=6000, messages=msgs)
        text = "".join(getattr(x, "text", "") for x in r.content if getattr(x, "type", "") == "text")
        try:
            out = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except ValueError:
            raise HTTPException(502, "the survey reply could not be read")
        cost = call_cost("claude-opus-5-5", r.usage.input_tokens, r.usage.output_tokens)
        sec = round(time.time() - t0, 1)
        ids = {x["id"] for x in rows}
        for sh in out.get("sheets") or []:
            try:
                sid = int(sh.get("id"))
            except (TypeError, ValueError):
                continue
            if sid not in ids:
                continue
            lo = sh.get("level_order")
            q("UPDATE sheets SET kind=%s, level_name=%s, level_order=%s, title=%s, survey_json=%s WHERE id=%s",
              (str(sh.get("kind") or "")[:40], str(sh.get("level") or "")[:120], int(lo) if isinstance(lo, (int, float)) else None,
               str(sh.get("title") or "")[:255], json.dumps(sh, ensure_ascii=False), sid))
        summary = dict(out.get("project") or {}, model=r.model, cost_usd=round(cost, 4), seconds=sec, sheets=len(rows), paid_by=keypool.last(),
                       tokens=dict(input=r.usage.input_tokens, output=r.usage.output_tokens))
        q("UPDATE projects SET summary_json=%s, surveyed_at=NOW() WHERE id=%s", (json.dumps(summary, ensure_ascii=False), pid))
        q("INSERT INTO ledger (workspace_id, kind, cost_usd, seconds) VALUES (%s,'opus survey',%s,%s)", (w, cost, sec))
        dirty.set()
        return dict(project(pid, request), survey=summary)

    # ---- history: every question asked and every photo taken ---------------------------------------------------------
    @api.get("/api/history")
    def history(request: Request, limit: int = 200):
        w = ws(request)
        rows = q("SELECT a.id, a.sheet_id, a.question, a.answer_json, a.seconds, a.cost_usd, a.hits, a.asked_from, a.created_at, a.status, s.name sheet, "
                 "s.revision, p.name project FROM answers a JOIN sheets s ON s.id=a.sheet_id JOIN projects p ON p.id=s.project_id "
                 "WHERE p.workspace_id=%s ORDER BY a.id DESC LIMIT %s", (w, limit))
        out = []
        for r in rows:
            a = present(r["sheet_id"], r["question"], json.loads(r["answer_json"])) if r["answer_json"] else {}
            au = a.get("audit") or {}
            out.append(dict(id=r["id"], sheet_id=r["sheet_id"], sheet=r["sheet"], revision=r["revision"], project=r["project"], question=r["question"],
                            answer=a.get("answer"), unit=a.get("unit"), tier=(a.get("honesty") or {}).get("tier"), corrected=bool(a.get("corrected")),
                            engine_answer=a.get("engine_answer"), language_model=bool(au.get("language_model_used")), status=r["status"],
                            seconds=r["seconds"], cost_usd=r["cost_usd"], times_reused=r["hits"], asked_from=r["asked_from"] or "laptop",
                            created_at=str(r["created_at"])))
        scans_ = q("SELECT c.id, c.sheet_id, s.name sheet, c.details, c.seconds, c.device, c.created_at FROM scans c LEFT JOIN sheets s ON s.id=c.sheet_id "
                   "WHERE c.workspace_id=%s ORDER BY c.id DESC LIMIT %s", (w, limit))
        for c in scans_:
            c["created_at"] = str(c["created_at"])
            c["test"] = "mozilla" not in (c.get("device") or "").lower()    # sent by a script (tests), not from a phone browser
            c["photo"] = f"/api/scans/{c['id']}/photo" if os.path.exists(f"{V}/library/scans/{c['id']}.jpg") else None
        notes = q("SELECT c.id, c.sheet_id, s.name sheet, c.note, c.author, c.created_at, (SELECT COUNT(*) FROM corrections x WHERE x.note_id=c.id) proposals, "
                  "(SELECT COUNT(*) FROM corrections x WHERE x.note_id=c.id AND x.status='applied') accepted FROM corrections c JOIN sheets s ON s.id=c.sheet_id "
                  "JOIN projects p ON p.id=s.project_id WHERE c.kind='note' AND p.workspace_id=%s ORDER BY c.id DESC LIMIT %s", (w, limit))
        for n in notes:
            n["created_at"] = str(n["created_at"])
        return dict(questions=out, scans=scans_, notes=notes)

    @api.delete("/api/sheets/{sid}/answers")
    def clear_answers(sid: int, request: Request, only_unread: int = 0):
        """Forget this blueprint's stored answers so they are worked out again (only_unread=1: only those made when the
        drawing could not be read: 'no element found by the trained detector')."""
        w = ws(request)
        sheet_row(w, sid)
        rows = q("SELECT id, answer_json FROM answers WHERE sheet_id=%s", (sid,))
        gone = [r["id"] for r in rows if not only_unread or "no element found by the trained detector" in (r["answer_json"] or "")]
        for aid in gone:
            q("DELETE FROM answers WHERE id=%s", (aid,))
        save_now()
        return dict(cleared=len(gone))

    @api.delete("/api/scans/{scan_id}")
    def delete_scan(scan_id: int, request: Request):
        """Remove one scan and its photo (test scans)."""
        w = ws(request)
        q("DELETE FROM scans WHERE id=%s AND workspace_id=%s", (scan_id, w))
        try:
            os.remove(f"{V}/library/scans/{scan_id}.jpg")
        except OSError:
            pass
        save_now()
        return dict(deleted=scan_id)

    @api.get("/api/scans/{scan_id}/photo")
    def scan_photo(scan_id: int, request: Request):
        w = ws(request)
        if not q("SELECT id FROM scans WHERE id=%s AND workspace_id=%s", (scan_id, w), one=True) or not os.path.exists(f"{V}/library/scans/{scan_id}.jpg"):
            raise HTTPException(404, "no such photo")
        return FileResponse(f"{V}/library/scans/{scan_id}.jpg", media_type="image/jpeg")

    @api.delete("/api/sheets/{sid}")
    def delete_sheet(sid: int, request: Request):
        """Remove a sheet with its files, answers and corrections (test uploads; a sheet it replaced becomes current again)."""
        import shutil
        w = ws(request)
        r = sheet_row(w, sid)
        q("UPDATE sheets SET superseded_by=NULL WHERE superseded_by=%s", (sid,))
        for t in ("answers", "corrections"):
            q(f"DELETE FROM {t} WHERE sheet_id=%s", (sid,))
        q("UPDATE scans SET sheet_id=NULL WHERE sheet_id=%s", (sid,))
        q("DELETE FROM sheets WHERE id=%s", (sid,))
        if not q("SELECT id FROM sheets WHERE project_id=%s LIMIT 1", (r["project_id"],)):
            q("DELETE FROM projects WHERE id=%s", (r["project_id"],))
        shutil.rmtree(f"{FILES}/{sid}", ignore_errors=True)
        feats.pop(sid, None)
        save_now()
        return dict(deleted=sid)

    # ---- money saved ------------------------------------------------------------------------------------------------
    OPUS_PER_QUESTION = dict(input_tokens=2500, output_tokens=800)   # assumption: one Opus call per question, blueprint image + question

    @api.get("/api/savings")
    def savings(request: Request, eur_per_hour: float = 60.0, minutes_per_answer: float = 6.0):
        w = ws(request)
        since = q("SELECT counter_since FROM workspaces WHERE id=%s", (w,), one=True)["counter_since"]
        t = q("SELECT COALESCE(SUM(cost_usd),0) spent, SUM(kind='answer') computed, SUM(kind='reuse') reused, SUM(kind='scan') scans, "
              "SUM(kind='opus note') notes FROM ledger WHERE workspace_id=%s AND created_at >= COALESCE(%s, '1970-01-01')", (w, since), one=True)
        n_ans = int(t["computed"] or 0) + int(t["reused"] or 0)
        per_q = call_cost("claude-opus-5-5", OPUS_PER_QUESTION["input_tokens"], OPUS_PER_QUESTION["output_tokens"])
        spent = float(t["spent"] or 0)
        hours = n_ans * minutes_per_answer / 60
        return dict(answers=n_ans, computed=int(t["computed"] or 0), reused=int(t["reused"] or 0), scans=int(t["scans"] or 0), since=str(since) if since else None,
                    person=dict(hours_saved=round(hours, 2), eur_saved=round(hours * eur_per_hour, 2), eur_per_hour=eur_per_hour, minutes_per_answer=minutes_per_answer,
                                assumption=f"a person takes {minutes_per_answer:g} minutes to count or measure one item on a plan, at EUR {eur_per_hour:g}/hour"),
                    ai=dict(spent_usd=round(spent, 4), opus_for_everything_usd=round(n_ans * per_q, 4), saved_usd=round(n_ans * per_q - spent, 4), per_question_usd=round(per_q, 4),
                            assumption=f"sending every question to Claude Opus 5.5 with the blueprint: about {OPUS_PER_QUESTION['input_tokens']} input and "
                                       f"{OPUS_PER_QUESTION['output_tokens']} output tokens at $4/$20 per million = ${per_q:.3f} each; we spent what the ledger shows"))

    @api.post("/api/savings/start")
    def savings_start(request: Request):
        """Count from now on (the ledger keeps everything; only the counter's start moves)."""
        w = ws(request)
        q("UPDATE workspaces SET counter_since=NOW() WHERE id=%s", (w,))
        save_now()
        return dict(ok=True)

    # ---- revisions ----------------------------------------------------------------------------------------------------
    SHEET_NO = re.compile(r"\b([A-Z]{1,2}[-.]?\d{1,3}(?:\.\d{1,2})?)\b")
    DATE = re.compile(r"\b(20\d{2}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]20\d{2}|20\d{6})\b")

    def title_block(data, name):
        """Sheet number and date as printed (vector PDFs: the text layer; the bottom-right corner first, where title blocks sit)."""
        no, date = "", ""
        m = DATE.search(name or "")
        if m:
            date = m.group(1)
        if (name or "").lower().endswith(".pdf") or data[:4] == b"%PDF":
            try:
                import pymupdf
                page = pymupdf.open(stream=data, filetype="pdf")[0]
                W, H = page.rect.width, page.rect.height
                words = page.get_text("words")
                corner = " ".join(x[4] for x in words if x[0] > 0.6 * W and x[1] > 0.55 * H)
                for txt in (corner, " ".join(x[4] for x in words)):
                    cands = [c for c in SHEET_NO.findall(txt) if re.search(r"[A-Z]", c) and re.search(r"\d", c) and c[0] in "ASMEPLCDGI"]
                    if cands:
                        no = cands[-1].replace(".", "-")
                        break
                if not date:
                    m = DATE.search(corner) or DATE.search(" ".join(x[4] for x in words))
                    date = m.group(1) if m else ""
            except Exception as e:
                print("title block:", e)
        return no, date

    def revision_candidates(w, sid, pid, exclude_self=True):
        """Sheets of the same project that look like an earlier version of this one: same printed sheet number, or the same
        picture (fingerprint close and many matching details)."""
        me = q("SELECT id, sheet_no, VEC_ToText(embedding) e FROM sheets WHERE id=%s", (sid,), one=True)
        rows = q("SELECT id, name, revision, revision_date, sheet_no, superseded_by, VEC_DISTANCE_COSINE(embedding, VEC_FromText(%s)) dist FROM sheets "
                 "WHERE project_id=%s AND id<>%s ORDER BY dist LIMIT 8", (me["e"], pid, sid))
        g = cv2.imread(f"{FILES}/{sid}/match.png", cv2.IMREAD_GRAYSCALE)
        k1, d1 = sift.detectAndCompute(g, None)
        photo = (np.float32([p.pt for p in k1]) if k1 else np.zeros((0, 2), np.float32), d1, g.shape)
        out = []
        for r in rows:
            r["details"], _ = details(photo, r["id"])
            r["same_number"] = bool(me["sheet_no"]) and me["sheet_no"] == r["sheet_no"]
            r["similarity"] = round(1 - r.pop("dist"), 3)
            out.append(r)
        out.sort(key=lambda r: (-r["same_number"], -r["details"]))
        best = out[0] if out else None
        if best and (best["same_number"] or (best["details"] >= 60 and best["similarity"] >= 0.8)) and not best["superseded_by"]:
            why = (f"same sheet number {me['sheet_no']}" if best["same_number"] else "") + (" and " if best["same_number"] and best["details"] >= 60 else "") \
                  + (f"the same drawing ({best['details']} matching details)" if best["details"] >= 60 else "")
            return dict(id=best["id"], name=best["name"], revision=best["revision"], revision_date=best["revision_date"], sheet_no=best["sheet_no"],
                        details=best["details"], similarity=best["similarity"], reason=why), out
        return None, out

    @api.get("/api/sheets/{sid}/revision_check")
    def revision_check(sid: int, request: Request):
        """Does this sheet look like a newer version of one already in the project? (asked after an upload; the user confirms)"""
        w = ws(request)
        r = sheet_row(w, sid)
        sug, cands = revision_candidates(w, sid, r["project_id"])
        return dict(sheet_id=sid, sheet_no=r["sheet_no"], revision_date=r["revision_date"], suggestion=sug,
                    candidates=[dict(id=c["id"], name=c["name"], details=c["details"], same_number=c["same_number"]) for c in cands[:4]])

    @api.post("/api/sheets/{sid}/revision_of/{old}")
    def set_revision(sid: int, old: int, request: Request, revision: str = "", old_revision: str = ""):
        """Confirm: sheet sid replaces sheet old (old gets 'superseded by sid')."""
        w = ws(request)
        sheet_row(w, sid); sheet_row(w, old)
        q("UPDATE sheets SET superseded_by=%s WHERE id=%s", (sid, old))
        if revision:
            q("UPDATE sheets SET revision=%s WHERE id=%s", (revision, sid))
        if old_revision:
            q("UPDATE sheets SET revision=%s WHERE id=%s", (old_revision, old))
        dirty.set()
        return dict(ok=True)

    @api.get("/api/sheets/{sid}/revisions")
    def revisions(sid: int, request: Request):
        """The whole chain this sheet belongs to, oldest first, and which one is current."""
        w = ws(request)
        sheet_row(w, sid)
        cur = sid
        for _ in range(50):                                      # walk back to the oldest
            prev = q("SELECT id FROM sheets WHERE superseded_by=%s", (cur,), one=True)
            if not prev:
                break
            cur = prev["id"]
        chain = []
        for _ in range(50):
            r = q("SELECT id, name, revision, revision_date, sheet_no, superseded_by, created_at FROM sheets WHERE id=%s", (cur,), one=True)
            chain.append(dict(r, created_at=str(r["created_at"])))
            if not r["superseded_by"]:
                break
            cur = r["superseded_by"]
        return dict(chain=chain, current=chain[-1]["id"], this_is_current=chain[-1]["id"] == sid)

    def diff_image(new, old):
        """old aligned onto new by matching details; ink only in old = red (removed), only in new = blue (added), both = grey."""
        gn = cv2.imread(f"{FILES}/{new}/match.png", cv2.IMREAD_GRAYSCALE)
        go = cv2.imread(f"{FILES}/{old}/match.png", cv2.IMREAD_GRAYSCALE)
        kn, dn = sift.detectAndCompute(gn, None)
        ko, do = sift.detectAndCompute(go, None)
        m = cv2.BFMatcher().knnMatch(do, dn, k=2)
        good = [a for a, b in (p for p in m if len(p) == 2) if a.distance < 0.75 * b.distance]
        if len(good) < 12:
            raise HTTPException(422, "these two blueprints do not line up (too few matching details)")
        src = np.float32([ko[a.queryIdx].pt for a in good]).reshape(-1, 1, 2)
        dst = np.float32([kn[a.trainIdx].pt for a in good]).reshape(-1, 1, 2)
        H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
        h, w_ = gn.shape
        go2 = cv2.warpPerspective(go, H, (w_, h), borderValue=255)
        inkn, inko = gn < 160, go2 < 160
        k = np.ones((3, 3), np.uint8)
        near_n = cv2.dilate(inkn.astype(np.uint8), k, iterations=2) > 0      # small misalignment is not a change
        near_o = cv2.dilate(inko.astype(np.uint8), k, iterations=2) > 0
        removed, added = inko & ~near_n, inkn & ~near_o
        img = np.full((h, w_, 3), 255, np.uint8)
        img[inkn & near_o] = (200, 200, 200)
        img[removed] = (40, 40, 220)                                           # BGR red
        img[added] = (220, 90, 20)                                             # BGR blue
        blobs = cv2.morphologyEx((removed | added).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(blobs)
        regions = [list(map(int, s[:4])) for s in stats[1:] if s[4] > 40]
        for x, y, ww, hh in regions:
            cv2.rectangle(img, (x - 6, y - 6), (x + ww + 6, y + hh + 6), (0, 0, 0), 2)
        return img, dict(regions=len(regions), removed_px=int(removed.sum()), added_px=int(added.sum()), boxes=regions[:60], width=w_, height=h)

    @api.get("/api/sheets/{sid}/diff/{old}")
    def diff(sid: int, old: int, request: Request, info: int = 0):
        w = ws(request)
        sheet_row(w, sid); sheet_row(w, old)
        img, meta = diff_image(sid, old)
        if info:
            return meta
        ok, buf = cv2.imencode(".png", img)
        return Response(buf.tobytes(), media_type="image/png", headers={"x-changed-regions": str(meta["regions"]), "access-control-expose-headers": "x-changed-regions"})

    @api.get("/api/sheets/{sid}/compare/{old}")
    def compare(sid: int, old: int, request: Request):
        """The same questions on both versions: what the answers were on the old sheet and are on the new one."""
        w = ws(request)
        sheet_row(w, sid); sheet_row(w, old)
        def answers(x):
            return {r["question_norm"]: r for r in q("SELECT question, question_norm, answer_json FROM answers WHERE sheet_id=%s AND status='done'", (x,))}
        a, b = answers(old), answers(sid)
        rows = []
        for k in a.keys() & b.keys():
            va = present(old, a[k]["question"], json.loads(a[k]["answer_json"])).get("answer")
            vb = present(sid, b[k]["question"], json.loads(b[k]["answer_json"])).get("answer")
            rows.append(dict(question=b[k]["question"], old=va, new=vb, changed=va != vb))
        rows.sort(key=lambda r: not r["changed"])
        return dict(rows=rows, only_old=[a[k]["question"] for k in a.keys() - b.keys()][:30])

    @api.get("/api/sheets/{sid}/{kind}")
    def sheet_file(sid: int, kind: str, request: Request):
        w = ws(request)
        sheet_row(w, sid)
        f = {"thumb": "thumb.jpg", "view": "view.jpg", "original": "original"}.get(kind)
        if not f or not os.path.exists(f"{FILES}/{sid}/{f}"):
            raise HTTPException(404, "no such file")
        return FileResponse(f"{FILES}/{sid}/{f}", media_type="image/jpeg" if f.endswith(".jpg") else "application/octet-stream")

    from fastapi.middleware.cors import CORSMiddleware
    api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    return api
