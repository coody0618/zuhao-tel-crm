import os, json, sqlite3, hashlib, secrets
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory, render_template_string

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# ── 資料路徑 ─────────────────────────────────────
BASE = '/data' if os.path.isdir('/data') else os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, 'tel_crm.db')

# ── 資料庫初始化 ──────────────────────────────────
def get_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT DEFAULT 'sales',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            type TEXT DEFAULT '待分類',
            source TEXT DEFAULT '',
            region TEXT DEFAULT '',
            note TEXT DEFAULT '',
            status TEXT DEFAULT '新客戶',
            next_follow TEXT DEFAULT '',
            assigned_to INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            note TEXT DEFAULT '',
            called_at TEXT DEFAULT (datetime('now','localtime'))
        );
    ''')
    # 預設帳號
    pw_admin  = hashlib.sha256('admin1234'.encode()).hexdigest()
    pw_sales  = hashlib.sha256('sales1234'.encode()).hexdigest()
    try:
        db.execute("INSERT INTO users(username,password,name,role) VALUES(?,?,?,?)",
                   ('admin', pw_admin, '睏足爸', 'admin'))
        db.execute("INSERT INTO users(username,password,name,role) VALUES(?,?,?,?)",
                   ('sales1', pw_sales, '業務員1', 'sales'))
        db.execute("INSERT INTO users(username,password,name,role) VALUES(?,?,?,?)",
                   ('sales2', pw_sales, '業務員2', 'sales'))
        db.execute("INSERT INTO users(username,password,name,role) VALUES(?,?,?,?)",
                   ('sales3', pw_sales, '業務員3', 'sales'))
        db.commit()
    except:
        pass
    db.close()

init_db()

# ── 認證裝飾器 ────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': '請先登入'}), 401
        return f(*args, **kwargs)
    return decorated

def today_str(): return datetime.now().strftime('%Y-%m-%d')
def now_str():   return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# ── 今日工作台邏輯 ────────────────────────────────
def get_today_clients(user_id, role):
    db = get_db()
    today = today_str()
    if role == 'admin':
        rows = db.execute('''
            SELECT c.*, u.name as sales_name
            FROM clients c
            LEFT JOIN users u ON c.assigned_to = u.id
            WHERE c.status NOT IN ('拒絕','黑名單')
            AND (c.next_follow = '' OR c.next_follow <= ?)
            ORDER BY c.next_follow ASC, c.created_at ASC
            LIMIT 50
        ''', (today,)).fetchall()
    else:
        rows = db.execute('''
            SELECT c.*, u.name as sales_name
            FROM clients c
            LEFT JOIN users u ON c.assigned_to = u.id
            WHERE c.assigned_to = ?
            AND c.status NOT IN ('拒絕','黑名單')
            AND (c.next_follow = '' OR c.next_follow <= ?)
            ORDER BY c.next_follow ASC, c.created_at ASC
            LIMIT 50
        ''', (user_id, today)).fetchall()
    db.close()
    return [dict(r) for r in rows]

# ═══════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    pw = hashlib.sha256(d.get('password','').encode()).hexdigest()
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE username=? AND password=?',
                      (d.get('username',''), pw)).fetchone()
    db.close()
    if not user:
        return jsonify({'error': '帳號或密碼錯誤'}), 401
    session['user_id'] = user['id']
    session['role']    = user['role']
    session['name']    = user['name']
    return jsonify({'id': user['id'], 'name': user['name'], 'role': user['role']})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
@login_required
def me():
    return jsonify({'id': session['user_id'], 'name': session['name'], 'role': session['role']})

# ── 今日工作台 ─────────────────────────────────────
@app.route('/api/today')
@login_required
def today():
    clients = get_today_clients(session['user_id'], session['role'])
    db = get_db()
    t = today_str()
    uid = session['user_id']
    role = session['role']

    if role == 'admin':
        total  = db.execute("SELECT COUNT(*) FROM calls WHERE date(called_at)=?", (t,)).fetchone()[0]
        reach  = db.execute("SELECT COUNT(*) FROM calls WHERE date(called_at)=? AND result!='未接'", (t,)).fetchone()[0]
        interested = db.execute("SELECT COUNT(*) FROM calls WHERE date(called_at)=? AND result='有興趣'", (t,)).fetchone()[0]
    else:
        total  = db.execute("SELECT COUNT(*) FROM calls WHERE user_id=? AND date(called_at)=?", (uid,t)).fetchone()[0]
        reach  = db.execute("SELECT COUNT(*) FROM calls WHERE user_id=? AND date(called_at)=? AND result!='未接'", (uid,t)).fetchone()[0]
        interested = db.execute("SELECT COUNT(*) FROM calls WHERE user_id=? AND date(called_at)=? AND result='有興趣'", (uid,t)).fetchone()[0]

    db.close()
    return jsonify({
        'clients': clients,
        'stats': {
            'total': total,
            'reach': reach,
            'rate': round(reach/total*100) if total else 0,
            'interested': interested,
        }
    })

# ── 記錄通話結果 ───────────────────────────────────
@app.route('/api/call', methods=['POST'])
@login_required
def record_call():
    d = request.json or {}
    client_id = d.get('client_id')
    result    = d.get('result')   # 有興趣/約回電/未接/再跟進/拒絕/黑名單
    note      = d.get('note', '')
    next_date = d.get('next_date', '')

    if not client_id or not result:
        return jsonify({'error': '缺少必要欄位'}), 400

    db = get_db()
    db.execute('INSERT INTO calls(client_id,user_id,result,note) VALUES(?,?,?,?)',
               (client_id, session['user_id'], result, note))

    # 狀態與下次跟進邏輯
    tomorrow = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    in30days  = (date.today() + timedelta(days=30)).strftime('%Y-%m-%d')

    status_map = {
        '有興趣': '有興趣',
        '約回電': '追蹤中',
        '未接':   '追蹤中',
        '再跟進': '追蹤中',
        '拒絕':   '拒絕',
        '黑名單': '黑名單',
    }
    next_map = {
        '未接':   tomorrow,
        '拒絕':   in30days,
    }

    new_status = status_map.get(result, '追蹤中')
    new_next   = next_date if next_date else next_map.get(result, '')

    db.execute('''UPDATE clients
                  SET status=?, next_follow=?, updated_at=?
                  WHERE id=?''',
               (new_status, new_next, now_str(), client_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})

# ── 客戶名單 ───────────────────────────────────────
@app.route('/api/clients')
@login_required
def clients():
    q      = request.args.get('q', '')
    type_  = request.args.get('type', '')
    status = request.args.get('status', '')
    page   = int(request.args.get('page', 1))
    limit  = 20
    offset = (page-1)*limit

    db = get_db()
    conds = []
    params = []

    if session['role'] != 'admin':
        conds.append('c.assigned_to = ?')
        params.append(session['user_id'])
    if q:
        conds.append('(c.name LIKE ? OR c.phone LIKE ?)')
        params += [f'%{q}%', f'%{q}%']
    if type_:
        conds.append('c.type = ?')
        params.append(type_)
    if status:
        conds.append('c.status = ?')
        params.append(status)

    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    total = db.execute(f'SELECT COUNT(*) FROM clients c {where}', params).fetchone()[0]
    rows  = db.execute(f'''
        SELECT c.*, u.name as sales_name
        FROM clients c
        LEFT JOIN users u ON c.assigned_to = u.id
        {where}
        ORDER BY c.updated_at DESC
        LIMIT ? OFFSET ?
    ''', params+[limit, offset]).fetchall()
    db.close()
    return jsonify({'clients': [dict(r) for r in rows], 'total': total, 'page': page})

@app.route('/api/clients', methods=['POST'])
@login_required
def add_client():
    d = request.json or {}
    if not d.get('name') or not d.get('phone'):
        return jsonify({'error': '姓名和電話為必填'}), 400
    db = get_db()
    assigned = d.get('assigned_to') or session['user_id']
    db.execute('''INSERT INTO clients(name,phone,type,source,region,note,assigned_to)
                  VALUES(?,?,?,?,?,?,?)''',
               (d['name'], d['phone'],
                d.get('type','待分類'), d.get('source',''),
                d.get('region',''), d.get('note',''), assigned))
    db.commit()
    db.close()
    return jsonify({'ok': True})

@app.route('/api/clients/<int:cid>')
@login_required
def get_client(cid):
    db = get_db()
    c  = db.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone()
    calls = db.execute('''
        SELECT cl.*, u.name as sales_name
        FROM calls cl LEFT JOIN users u ON cl.user_id=u.id
        WHERE cl.client_id=? ORDER BY cl.called_at DESC
    ''', (cid,)).fetchall()
    db.close()
    if not c: return jsonify({'error': '找不到'}), 404
    return jsonify({'client': dict(c), 'calls': [dict(r) for r in calls]})

@app.route('/api/clients/<int:cid>', methods=['PUT'])
@login_required
def update_client(cid):
    d = request.json or {}
    db = get_db()
    db.execute('''UPDATE clients
                  SET name=?,phone=?,type=?,source=?,region=?,note=?,
                      assigned_to=?,next_follow=?,updated_at=?
                  WHERE id=?''',
               (d.get('name'), d.get('phone'), d.get('type','待分類'),
                d.get('source',''), d.get('region',''), d.get('note',''),
                d.get('assigned_to',0), d.get('next_follow',''),
                now_str(), cid))
    db.commit()
    db.close()
    return jsonify({'ok': True})

# ── CSV 匯入 ───────────────────────────────────────
@app.route('/api/import', methods=['POST'])
@login_required
def import_csv():
    import csv, io
    f = request.files.get('file')
    if not f: return jsonify({'error': '請選擇 CSV 檔案'}), 400
    content = f.read().decode('utf-8-sig')
    reader  = csv.DictReader(io.StringIO(content))
    db = get_db()
    count = 0
    for row in reader:
        name  = row.get('姓名','').strip() or row.get('name','').strip()
        phone = row.get('電話','').strip() or row.get('phone','').strip()
        if name and phone:
            db.execute('''INSERT INTO clients(name,phone,type,source,region,note,assigned_to)
                          VALUES(?,?,?,?,?,?,?)''',
                       (name, phone,
                        row.get('類型','待分類').strip(),
                        row.get('來源','').strip(),
                        row.get('地區','').strip(),
                        row.get('備注','').strip(),
                        session['user_id']))
            count += 1
    db.commit()
    db.close()
    return jsonify({'ok': True, 'count': count})

# ── 業績統計 ───────────────────────────────────────
@app.route('/api/stats')
@login_required
def stats():
    db  = get_db()
    uid = session['user_id']
    role= session['role']
    today = today_str()
    week_start = (date.today() - timedelta(days=date.today().weekday())).strftime('%Y-%m-%d')

    def q(sql, params=()):
        return db.execute(sql, params).fetchone()[0]

    if role == 'admin':
        p = ()
        filter_u = ''
    else:
        p = (uid,)
        filter_u = 'AND user_id = ?'

    stats_data = {
        'today': {
            'total':   q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)=? {filter_u}", (today,)+p),
            'reach':   q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)=? AND result!='未接' {filter_u}", (today,)+p),
            'interested': q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)=? AND result='有興趣' {filter_u}", (today,)+p),
            'booked':  q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)=? AND result='約回電' {filter_u}", (today,)+p),
        },
        'week': {
            'total':   q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)>=? {filter_u}", (week_start,)+p),
            'reach':   q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)>=? AND result!='未接' {filter_u}", (week_start,)+p),
            'interested': q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)>=? AND result='有興趣' {filter_u}", (week_start,)+p),
        }
    }

    # 七天每日數據
    daily = []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).strftime('%Y-%m-%d')
        t = q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)=? {filter_u}", (d,)+p)
        r = q(f"SELECT COUNT(*) FROM calls WHERE date(called_at)=? AND result!='未接' {filter_u}", (d,)+p)
        daily.append({'date': d, 'total': t, 'reach': r})

    # 團隊排行（管理者看）
    ranking = []
    if role == 'admin':
        rows = db.execute('''
            SELECT u.name, COUNT(c.id) as total,
                   SUM(CASE WHEN c.result!='未接' THEN 1 ELSE 0 END) as reach,
                   SUM(CASE WHEN c.result='有興趣' THEN 1 ELSE 0 END) as interested
            FROM users u
            LEFT JOIN calls c ON c.user_id=u.id AND date(c.called_at)=?
            WHERE u.role='sales'
            GROUP BY u.id
            ORDER BY total DESC
        ''', (today,)).fetchall()
        ranking = [dict(r) for r in rows]

    db.close()
    return jsonify({'stats': stats_data, 'daily': daily, 'ranking': ranking})

# ── 業務員列表（管理者用）────────────────────────────
@app.route('/api/users')
@login_required
def users():
    if session['role'] != 'admin':
        return jsonify({'error': '權限不足'}), 403
    db = get_db()
    rows = db.execute("SELECT id,username,name,role FROM users ORDER BY id").fetchall()
    db.close()
    return jsonify({'users': [dict(r) for r in rows]})

# ── 主頁（Single Page App）──────────────────────────
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def index(path):
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
