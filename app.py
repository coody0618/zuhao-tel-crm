import os, json, hashlib, secrets
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

DATABASE_URL = os.environ.get('DATABASE_URL')

# ── 資料庫連線 ────────────────────────────────────
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT DEFAULT 'sales',
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            type TEXT DEFAULT '待分類',
            source TEXT DEFAULT '',
            region TEXT DEFAULT '',
            note TEXT DEFAULT '',
            status TEXT DEFAULT '新客戶',
            next_follow TEXT DEFAULT '',
            assigned_to INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS calls (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            note TEXT DEFAULT '',
            called_at TIMESTAMP DEFAULT NOW()
        );
    ''')
    # 新增欄位（若已存在則跳過）
    cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS intent_grade TEXT DEFAULT ''")
    cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS dnc BOOLEAN DEFAULT FALSE")
    cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS follow_date TEXT DEFAULT ''")
    pw_admin = hashlib.sha256('admin1234'.encode()).hexdigest()
    pw_sales = hashlib.sha256('sales1234'.encode()).hexdigest()
    try:
        cur.execute("INSERT INTO users(username,password,name,role) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    ('admin', pw_admin, '睏足爸', 'admin'))
        cur.execute("INSERT INTO users(username,password,name,role) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    ('sales1', pw_sales, '業務員1', 'sales'))
        cur.execute("INSERT INTO users(username,password,name,role) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    ('sales2', pw_sales, '業務員2', 'sales'))
        cur.execute("INSERT INTO users(username,password,name,role) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    ('sales3', pw_sales, '業務員3', 'sales'))
        conn.commit()
    except Exception as e:
        conn.rollback()
    cur.close()
    conn.close()

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
    conn = get_db()
    cur = conn.cursor()
    today = today_str()
    if role == 'admin':
        cur.execute('''
            SELECT c.*, u.name as sales_name
            FROM clients c
            LEFT JOIN users u ON c.assigned_to = u.id
            WHERE c.status NOT IN ('拒絕','黑名單')
            AND (c.dnc IS NULL OR c.dnc = FALSE)
            AND (c.next_follow = '' OR c.next_follow <= %s)
            ORDER BY c.next_follow ASC, c.created_at ASC
            LIMIT 50
        ''', (today,))
    else:
        cur.execute('''
            SELECT c.*, u.name as sales_name
            FROM clients c
            LEFT JOIN users u ON c.assigned_to = u.id
            WHERE c.assigned_to = %s
            AND c.status NOT IN ('拒絕','黑名單')
            AND (c.dnc IS NULL OR c.dnc = FALSE)
            AND (c.next_follow = '' OR c.next_follow <= %s)
            ORDER BY c.next_follow ASC, c.created_at ASC
            LIMIT 50
        ''', (user_id, today))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

# ═══════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    pw = hashlib.sha256(d.get('password','').encode()).hexdigest()
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE username=%s AND password=%s',
                (d.get('username',''), pw))
    user = cur.fetchone()
    cur.close()
    conn.close()
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
    conn = get_db()
    cur = conn.cursor()
    t = today_str()
    uid = session['user_id']
    role = session['role']

    if role == 'admin':
        cur.execute("SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date", (t,))
        total = cur.fetchone()['count']
        cur.execute("SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date AND result != '未接'", (t,))
        reach = cur.fetchone()['count']
        cur.execute("SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date AND result = '有興趣'", (t,))
        interested = cur.fetchone()['count']
    else:
        cur.execute("SELECT COUNT(*) FROM calls WHERE user_id=%s AND called_at::date = %s::date", (uid, t))
        total = cur.fetchone()['count']
        cur.execute("SELECT COUNT(*) FROM calls WHERE user_id=%s AND called_at::date = %s::date AND result != '未接'", (uid, t))
        reach = cur.fetchone()['count']
        cur.execute("SELECT COUNT(*) FROM calls WHERE user_id=%s AND called_at::date = %s::date AND result = '有興趣'", (uid, t))
        interested = cur.fetchone()['count']

    cur.close()
    conn.close()
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
    client_id    = d.get('client_id')
    result       = d.get('result')
    note         = d.get('note', '')
    next_date    = d.get('next_date', '')
    intent_grade = d.get('intent_grade', '')

    if not client_id or not result:
        return jsonify({'error': '缺少必要欄位'}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute('INSERT INTO calls(client_id,user_id,result,note) VALUES(%s,%s,%s,%s)',
               (client_id, session['user_id'], result, note))

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

    # D級自動標 DNC
    dnc_val = True if intent_grade == 'D' else None

    if intent_grade and dnc_val is not None:
        cur.execute('''UPDATE clients
                      SET status=%s, next_follow=%s, intent_grade=%s, dnc=%s, updated_at=%s
                      WHERE id=%s''',
                   (new_status, new_next, intent_grade, dnc_val, now_str(), client_id))
    elif intent_grade:
        cur.execute('''UPDATE clients
                      SET status=%s, next_follow=%s, intent_grade=%s, updated_at=%s
                      WHERE id=%s''',
                   (new_status, new_next, intent_grade, now_str(), client_id))
    else:
        cur.execute('''UPDATE clients
                      SET status=%s, next_follow=%s, updated_at=%s
                      WHERE id=%s''',
                   (new_status, new_next, now_str(), client_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'ok': True})

# ── 客戶名單 ───────────────────────────────────────
@app.route('/api/clients')
@login_required
def clients():
    q      = request.args.get('q', '')
    type_  = request.args.get('type', '')
    status = request.args.get('status', '')
    grade  = request.args.get('grade', '')
    page   = int(request.args.get('page', 1))
    limit  = 20
    offset = (page-1)*limit

    conn = get_db()
    cur = conn.cursor()
    conds = []
    params = []

    if session['role'] != 'admin':
        conds.append('c.assigned_to = %s')
        params.append(session['user_id'])
    if q:
        conds.append('(c.name LIKE %s OR c.phone LIKE %s)')
        params += [f'%{q}%', f'%{q}%']
    if type_:
        conds.append('c.type = %s')
        params.append(type_)
    if status:
        conds.append('c.status = %s')
        params.append(status)
    if grade:
        conds.append('c.intent_grade = %s')
        params.append(grade)

    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    cur.execute(f'SELECT COUNT(*) FROM clients c {where}', params)
    total = cur.fetchone()['count']
    cur.execute(f'''
        SELECT c.*, u.name as sales_name
        FROM clients c
        LEFT JOIN users u ON c.assigned_to = u.id
        {where}
        ORDER BY c.updated_at DESC
        LIMIT %s OFFSET %s
    ''', params+[limit, offset])
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({'clients': [dict(r) for r in rows], 'total': total, 'page': page})

@app.route('/api/clients', methods=['POST'])
@login_required
def add_client():
    d = request.json or {}
    if not d.get('name') or not d.get('phone'):
        return jsonify({'error': '姓名和電話為必填'}), 400
    conn = get_db()
    cur = conn.cursor()
    assigned = d.get('assigned_to') or session['user_id']
    cur.execute('''INSERT INTO clients(name,phone,type,source,region,note,assigned_to)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)''',
               (d['name'], d['phone'],
                d.get('type','待分類'), d.get('source',''),
                d.get('region',''), d.get('note',''), assigned))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/clients/<int:cid>')
@login_required
def get_client(cid):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM clients WHERE id=%s', (cid,))
    c = cur.fetchone()
    cur.execute('''
        SELECT cl.*, u.name as sales_name
        FROM calls cl LEFT JOIN users u ON cl.user_id=u.id
        WHERE cl.client_id=%s ORDER BY cl.called_at DESC
    ''', (cid,))
    calls = cur.fetchall()
    cur.close()
    conn.close()
    if not c: return jsonify({'error': '找不到'}), 404
    return jsonify({'client': dict(c), 'calls': [dict(r) for r in calls]})

@app.route('/api/clients/<int:cid>', methods=['PUT'])
@login_required
def update_client(cid):
    d = request.json or {}
    conn = get_db()
    cur = conn.cursor()
    intent_grade = d.get('intent_grade', '')
    dnc = True if intent_grade == 'D' else bool(d.get('dnc', False))
    cur.execute('''UPDATE clients
                  SET name=%s,phone=%s,type=%s,source=%s,region=%s,note=%s,
                      assigned_to=%s,next_follow=%s,intent_grade=%s,dnc=%s,updated_at=%s
                  WHERE id=%s''',
               (d.get('name'), d.get('phone'), d.get('type','待分類'),
                d.get('source',''), d.get('region',''), d.get('note',''),
                d.get('assigned_to',0), d.get('next_follow',''),
                intent_grade, dnc, now_str(), cid))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/clients/<int:cid>/grade', methods=['PATCH'])
@login_required
def set_grade(cid):
    d = request.json or {}
    grade = d.get('intent_grade', '')
    follow_date = d.get('follow_date', '')
    dnc = True if grade == 'D' else False
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''UPDATE clients
                  SET intent_grade=%s, dnc=%s, follow_date=%s, updated_at=%s
                  WHERE id=%s''',
               (grade, dnc, follow_date, now_str(), cid))
    conn.commit()
    cur.close()
    conn.close()
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
    conn = get_db()
    cur = conn.cursor()
    count = 0
    for row in reader:
        name  = row.get('姓名','').strip() or row.get('name','').strip()
        phone = row.get('電話','').strip() or row.get('phone','').strip()
        if name and phone:
            cur.execute('''INSERT INTO clients(name,phone,type,source,region,note,assigned_to)
                          VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                       (name, phone,
                        row.get('類型','待分類').strip(),
                        row.get('來源','').strip(),
                        row.get('地區','').strip(),
                        row.get('備注','').strip(),
                        session['user_id']))
            count += 1
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'ok': True, 'count': count})

# ── 業績統計 ───────────────────────────────────────
@app.route('/api/stats')
@login_required
def stats():
    conn = get_db()
    cur = conn.cursor()
    uid = session['user_id']
    role = session['role']
    today = today_str()
    week_start = (date.today() - timedelta(days=date.today().weekday())).strftime('%Y-%m-%d')

    def q(sql, params=()):
        cur.execute(sql, params)
        return cur.fetchone()['count']

    if role == 'admin':
        p = ()
        filter_u = ''
    else:
        p = (uid,)
        filter_u = 'AND user_id = %s'

    stats_data = {
        'today': {
            'total':      q(f"SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date {filter_u}", (today,)+p),
            'reach':      q(f"SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date AND result != '未接' {filter_u}", (today,)+p),
            'interested': q(f"SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date AND result = '有興趣' {filter_u}", (today,)+p),
            'booked':     q(f"SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date AND result = '約回電' {filter_u}", (today,)+p),
        },
        'week': {
            'total':      q(f"SELECT COUNT(*) FROM calls WHERE called_at::date >= %s::date {filter_u}", (week_start,)+p),
            'reach':      q(f"SELECT COUNT(*) FROM calls WHERE called_at::date >= %s::date AND result != '未接' {filter_u}", (week_start,)+p),
            'interested': q(f"SELECT COUNT(*) FROM calls WHERE called_at::date >= %s::date AND result = '有興趣' {filter_u}", (week_start,)+p),
        }
    }

    daily = []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).strftime('%Y-%m-%d')
        t = q(f"SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date {filter_u}", (d,)+p)
        r = q(f"SELECT COUNT(*) FROM calls WHERE called_at::date = %s::date AND result != '未接' {filter_u}", (d,)+p)
        daily.append({'date': d, 'total': t, 'reach': r})

    ranking = []
    if role == 'admin':
        cur.execute('''
            SELECT u.name, COUNT(c.id) as total,
                   SUM(CASE WHEN c.result != '未接' THEN 1 ELSE 0 END) as reach,
                   SUM(CASE WHEN c.result = '有興趣' THEN 1 ELSE 0 END) as interested
            FROM users u
            LEFT JOIN calls c ON c.user_id=u.id AND c.called_at::date = %s::date
            WHERE u.role='sales'
            GROUP BY u.id, u.name
            ORDER BY total DESC
        ''', (today,))
        ranking = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()
    return jsonify({'stats': stats_data, 'daily': daily, 'ranking': ranking})

# ── 業務員列表（管理者用）────────────────────────────
@app.route('/api/users')
@login_required
def users():
    if session['role'] != 'admin':
        return jsonify({'error': '權限不足'}), 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id,username,name,role FROM users ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({'users': [dict(r) for r in rows]})

# ── 主頁（Single Page App）──────────────────────────
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def index(path):
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
