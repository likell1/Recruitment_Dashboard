from flask import Flask, jsonify, request, render_template, Response
import pymysql
import sqlite3
import json
import csv
import io
import urllib.parse
import yaml
import os
import subprocess
import time
import atexit
from datetime import datetime, date

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.yaml')
with open(CONFIG_FILE, 'r') as f:
    config = yaml.safe_load(f)

DB_CONFIG = config['database']
SSH_CONFIG = config.get('ssh')

# ── SSH Tunnel (ssh -L subprocess) ────────────────────────
tunnel_proc = None
_db_host = DB_CONFIG.get('host', '127.0.0.1')
_db_port = int(DB_CONFIG.get('port', 3306))

if SSH_CONFIG:
    pkey_path = SSH_CONFIG.get('pkey')
    if pkey_path:
        pkey_path = os.path.expanduser(pkey_path)

    remote_host = _db_host
    remote_port = _db_port
    local_port  = 13306

    ssh_cmd = [
        'ssh',
        '-N',
        '-L', f'127.0.0.1:{local_port}:{remote_host}:{remote_port}',
        '-i', pkey_path,
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=30',
        f"{SSH_CONFIG['username']}@{SSH_CONFIG['host']}",
        '-p', str(SSH_CONFIG.get('port', 22)),
    ]

    tunnel_proc = subprocess.Popen(ssh_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)  # 터널 안정화 대기

    if tunnel_proc.poll() is not None:
        raise RuntimeError("SSH 터널 프로세스가 시작 직후 종료됨")

    _db_host = '127.0.0.1'
    _db_port = local_port
    print(f"✅ SSH 터널 연결: localhost:{local_port} → {SSH_CONFIG['host']}:{remote_host}:{remote_port}")

    def _stop_tunnel():
        if tunnel_proc and tunnel_proc.poll() is None:
            tunnel_proc.terminate()
            print("SSH 터널 종료")
    atexit.register(_stop_tunnel)

# ── Local SQLite (평가 저장) ───────────────────────────────
EVAL_DB = os.path.join(os.path.dirname(__file__), 'evaluations.db')

def init_eval_db():
    with sqlite3.connect(EVAL_DB) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS evaluations (
                applicant_id INTEGER PRIMARY KEY,
                decision     TEXT    DEFAULT 'pending',
                score        REAL,
                memo         TEXT,
                updated_at   TEXT
            )
        ''')
        conn.commit()

init_eval_db()

# ── Helpers ───────────────────────────────────────────────
def get_mysql():
    return pymysql.connect(
        host=_db_host,
        port=_db_port,
        user=str(DB_CONFIG['user']),
        password=str(DB_CONFIG['password']),
        database=str(DB_CONFIG['database']),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10
    )

def to_dict(row):
    result = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            result[k] = v.isoformat()
        elif isinstance(v, bytes):
            result[k] = v.decode('utf-8')
        else:
            result[k] = v
    return result

def parse_json_field(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            pass
    return value

def get_evals(applicant_ids):
    if not applicant_ids:
        return {}
    with sqlite3.connect(EVAL_DB) as conn:
        conn.row_factory = sqlite3.Row
        ph = ','.join('?' * len(applicant_ids))
        rows = conn.execute(
            f'SELECT * FROM evaluations WHERE applicant_id IN ({ph})',
            applicant_ids
        ).fetchall()
    return {r['applicant_id']: dict(r) for r in rows}

# ── Routes ────────────────────────────────────────────────

@app.route('/_debug/tables')
def debug_tables():
    conn = get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW TABLES')
            tables = [list(r.values())[0] for r in cur.fetchall()]
            result = {}
            for t in tables:
                cur.execute(f'DESCRIBE `{t}`')
                result[t] = [r['Field'] for r in cur.fetchall()]
        return jsonify(result)
    finally:
        conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/evaluations')
def evaluations_dashboard():
    return render_template('evaluations.html')

@app.route('/api/recruitments')
def list_recruitments():
    conn = get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT DISTINCT recruitment_id FROM applicants ORDER BY recruitment_id DESC')
            rows = cur.fetchall()
        return jsonify([r['recruitment_id'] for r in rows])
    finally:
        conn.close()

@app.route('/api/stats')
def get_stats():
    rec_id = request.args.get('recruitment_id', '')
    cond, params = [], []
    if rec_id:
        cond.append('recruitment_id = %s')
        params.append(rec_id)
    where = f'WHERE {" AND ".join(cond)}' if cond else ''

    conn = get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) AS total FROM applicants {where}', params)
            total = cur.fetchone()['total']
            cur.execute(f'SELECT track, COUNT(*) AS cnt FROM applicants {where} GROUP BY track', params)
            by_track = cur.fetchall()
            cur.execute(f'SELECT status, COUNT(*) AS cnt FROM applicants {where} GROUP BY status', params)
            by_status = cur.fetchall()
        return jsonify({'total': total, 'by_track': by_track, 'by_status': by_status})
    finally:
        conn.close()

@app.route('/api/applicants')
def list_applicants():
    f = request.args
    track      = f.get('track', '')
    status     = f.get('status', '')
    search     = f.get('search', '')
    rec_id     = f.get('recruitment_id', '')
    decision   = f.get('decision', '')   # local eval filter (applied in Python)
    sort_col   = f.get('sort', 'created_at')
    sort_order = f.get('order', 'desc')

    allowed_sorts = {'name', 'created_at', 'track', 'university', 'major'}
    if sort_col not in allowed_sorts:
        sort_col = 'created_at'
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'

    cond, params = [], []
    if rec_id:
        cond.append('a.recruitment_id = %s'); params.append(rec_id)
    if track:
        cond.append('a.track = %s'); params.append(track)
    if status:
        cond.append('a.status = %s'); params.append(status)
    if search:
        cond.append('(a.name LIKE %s OR a.email LIKE %s OR a.university LIKE %s OR a.major LIKE %s)')
        params.extend([f'%{search}%'] * 4)

    where = f'WHERE {" AND ".join(cond)}' if cond else ''

    conn = get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                SELECT a.id, a.name, a.track, a.status, a.university, a.major,
                       a.email, a.last_semester, a.created_at, a.recruitment_id,
                       a.graduation_date
                FROM applicants a
                {where}
                ORDER BY a.{sort_col} {sort_order}
            ''', params)
            applicants = cur.fetchall()
    finally:
        conn.close()

    ids = [a['id'] for a in applicants]
    evals = get_evals(ids)

    result = []
    for a in applicants:
        d = to_dict(a)
        ev = evals.get(a['id'])
        d['eval'] = ev
        # decision filter (local)
        if decision:
            ev_decision = ev['decision'] if ev else 'pending'
            if ev_decision != decision:
                continue
        result.append(d)

    return jsonify(result)

@app.route('/api/applicants/<int:applicant_id>')
def get_applicant(applicant_id):
    conn = get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM applicants WHERE id = %s', (applicant_id,))
            applicant = cur.fetchone()
            if not applicant:
                return jsonify({'error': 'Not found'}), 404

            cur.execute('''
                SELECT aq.id          AS question_id,
                       aq.label       AS q_label,
                       aq.content     AS q_content,
                       aq.order_num   AS q_order,
                       aa.answer_text,
                       aa.answer_json
                FROM application_question aq
                LEFT JOIN applicant_answer aa
                       ON aa.question_id = aq.id AND aa.applicant_id = %s
                WHERE aq.recruitment_id = %s
                  AND (aq.category = 'COMMON' OR aq.category = %s)
                ORDER BY aq.order_num ASC
            ''', (applicant_id, applicant['recruitment_id'], applicant['track']))
            answers = cur.fetchall()
    finally:
        conn.close()

    # Local evaluation
    with sqlite3.connect(EVAL_DB) as sconn:
        sconn.row_factory = sqlite3.Row
        row = sconn.execute(
            'SELECT * FROM evaluations WHERE applicant_id = ?', (applicant_id,)
        ).fetchone()
        evaluation = dict(row) if row else None

    result = to_dict(applicant)
    result['minor_double_major'] = parse_json_field(result.get('minor_double_major'))

    processed = []
    for a in answers:
        ans = to_dict(a)
        ans['answer_json'] = parse_json_field(ans.get('answer_json'))
        processed.append(ans)

    result['answers'] = processed
    result['evaluation'] = evaluation
    return jsonify(result)

@app.route('/api/applicants/export')
def export_applicants_csv():
    f = request.args
    track      = f.get('track', '')
    status     = f.get('status', '')
    search     = f.get('search', '')
    rec_id     = f.get('recruitment_id', '')
    decision   = f.get('decision', '')
    sort_col   = f.get('sort', 'created_at')
    sort_order = f.get('order', 'desc')

    allowed_sorts = {'name', 'created_at', 'track', 'university', 'major'}
    if sort_col not in allowed_sorts:
        sort_col = 'created_at'
    if sort_order not in ('asc', 'desc'):
        sort_order = 'desc'

    cond, params = [], []
    if rec_id:
        cond.append('a.recruitment_id = %s'); params.append(rec_id)
    if track:
        cond.append('a.track = %s'); params.append(track)
    if status:
        cond.append('a.status = %s'); params.append(status)
    if search:
        cond.append('(a.name LIKE %s OR a.email LIKE %s OR a.university LIKE %s OR a.major LIKE %s)')
        params.extend([f'%{search}%'] * 4)
    where = f'WHERE {" AND ".join(cond)}' if cond else ''

    conn = get_mysql()
    try:
        with conn.cursor() as cur:
            cur.execute(f'''
                SELECT a.id, a.name, a.track, a.status, a.university, a.major,
                       a.email, a.phone, a.birth_date, a.military_status,
                       a.last_semester, a.created_at, a.recruitment_id,
                       a.graduation_date, a.minor_double_major, a.grad_school_plan
                FROM applicants a
                {where}
                ORDER BY a.{sort_col} {sort_order}
            ''', params)
            applicants = cur.fetchall()

            ids = [a['id'] for a in applicants]
            answers = []
            if ids:
                ph = ','.join(['%s'] * len(ids))
                cur.execute(f'''
                    SELECT applicant_id, question_id, answer_text, answer_json
                    FROM applicant_answer
                    WHERE applicant_id IN ({ph})
                    ORDER BY applicant_id, question_id ASC
                ''', ids)
                answers = cur.fetchall()

            # 질문 라벨/내용 조회 (해당 공고 기준)
            q_params = [rec_id] if rec_id else []
            q_where  = 'WHERE recruitment_id = %s' if rec_id else ''
            cur.execute(f'''
                SELECT id, label, content, category, order_num
                FROM application_question
                {q_where}
                ORDER BY order_num ASC
            ''', q_params)
            questions = cur.fetchall()
    finally:
        conn.close()

    # {question_id: 헤더 문자열}  label 있으면 "label_content앞30자", 없으면 "Q{id}"
    def _q_header(q):
        label   = (q.get('label') or '').strip()
        content = (q.get('content') or '').strip()
        content_short = content[:30] + ('…' if len(content) > 30 else '')
        if label:
            return f'{label}_{content_short}' if content_short else label
        return f'Q{q["id"]}'

    qid_to_header    = {q['id']: _q_header(q)    for q in questions}
    qid_to_category  = {q['id']: q['category']   for q in questions}

    # 현재 내보내는 지원자들의 트랙 집합
    present_tracks = {(a.get('track') or '').upper() for a in applicants}

    # 질문 ID 목록 — COMMON이거나 내보내는 트랙에 해당하는 질문만 포함
    all_qids = [
        q['id'] for q in questions
        if q['category'] == 'COMMON' or q['category'] in present_tracks
    ]
    # 혹시 questions 테이블에 없는 qid가 답변에 있으면 뒤에 추가
    ordered_set = set(all_qids)
    for qid in sorted(set(a['question_id'] for a in answers) - ordered_set):
        all_qids.append(qid)

    # {applicant_id: {question_id: 답변 텍스트}}
    ans_map = {}
    for a in answers:
        aid, qid = a['applicant_id'], a['question_id']
        val = ''
        raw_json = a['answer_json']
        if raw_json:
            parsed = parse_json_field(raw_json)
            if isinstance(parsed, dict):
                val = ' | '.join(f'{k}: {v}' for k, v in parsed.items())
            elif isinstance(parsed, list):
                val = ', '.join(str(x) for x in parsed)
            else:
                val = str(parsed)
        elif a['answer_text']:
            val = a['answer_text']
        ans_map.setdefault(aid, {})[qid] = val

    evals_map = get_evals(ids)

    TRACK_LABEL = {'ANALYSIS': '분석', 'VISUALIZATION': '시각화', 'ENGINEERING': '엔지니어링'}
    DEC_LABEL   = {'pending': '검토중', 'pass': '합격', 'fail': '불합격', 'hold': '보류'}

    output = io.StringIO()
    writer = csv.writer(output)

    base_headers = [
        'ID', '이름', '이메일', '전화번호', '생년월일', '병역',
        '트랙', '대학교', '전공', '복수/부전공', '학기', '졸업예정', '대학원진학',
        '제출시각', '상태', '평가결정', '점수', '메모',
    ]
    writer.writerow(base_headers + [qid_to_header.get(qid, f'Q{qid}') for qid in all_qids])

    for a in applicants:
        ev = evals_map.get(a['id'])
        ev_decision = ev['decision'] if ev else 'pending'
        if decision and ev_decision != decision:
            continue

        # minor_double_major 직렬화
        minor = a.get('minor_double_major') or ''
        if isinstance(minor, bytes):
            minor = minor.decode('utf-8')
        if minor:
            parsed = parse_json_field(minor)
            if isinstance(parsed, dict):
                minor = ' / '.join(f'{k}: {v}' for k, v in parsed.items())
            elif isinstance(parsed, list):
                minor = ', '.join(str(x) for x in parsed)

        created_at = a.get('created_at', '')
        if isinstance(created_at, (datetime, date)):
            created_at = created_at.isoformat()
        birth_date = a.get('birth_date', '')
        if isinstance(birth_date, (datetime, date)):
            birth_date = str(birth_date)[:10]

        row = [
            a['id'],
            a.get('name', ''),
            a.get('email', ''),
            a.get('phone', ''),
            birth_date,
            a.get('military_status', ''),
            TRACK_LABEL.get(a.get('track', ''), a.get('track', '')),
            a.get('university', ''),
            a.get('major', ''),
            minor,
            f"{a['last_semester']}학기" if a.get('last_semester') else '',
            a.get('graduation_date', '') or '',
            '예' if a.get('grad_school_plan') else '아니오',
            created_at,
            '제출' if a.get('status') == 'SUBMITTED' else '임시저장',
            DEC_LABEL.get(ev_decision, ev_decision),
            ev['score'] if ev else '',
            ev['memo']  if ev else '',
        ]
        applicant_track = (a.get('track') or '').upper()
        for qid in all_qids:
            category = qid_to_category.get(qid, 'COMMON')
            if category != 'COMMON' and category != applicant_track:
                row.append('')
            else:
                row.append(ans_map.get(a['id'], {}).get(qid, ''))
        writer.writerow(row)

    csv_bytes = '﻿' + output.getvalue()
    filename  = f"지원자_{datetime.now().strftime('%Y%m%d')}.csv"
    encoded   = urllib.parse.quote(filename)
    return Response(
        csv_bytes,
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{encoded}"},
    )


@app.route('/api/evaluations/<int:applicant_id>', methods=['POST'])
def save_evaluation(applicant_id):
    data = request.get_json() or {}
    decision   = data.get('decision', 'pending')
    score      = data.get('score')
    memo       = data.get('memo', '')
    updated_at = datetime.now().isoformat()

    with sqlite3.connect(EVAL_DB) as conn:
        conn.execute('''
            INSERT INTO evaluations (applicant_id, decision, score, memo, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(applicant_id) DO UPDATE SET
                decision   = excluded.decision,
                score      = excluded.score,
                memo       = excluded.memo,
                updated_at = excluded.updated_at
        ''', (applicant_id, decision, score, memo, updated_at))
        conn.commit()

    return jsonify({'ok': True})

# ── Run ───────────────────────────────────────────────────
if __name__ == '__main__':
    print("🌐 http://localhost:5050 에서 대시보드를 확인하세요")
    app.run(debug=False, port=5050, use_reloader=False)
