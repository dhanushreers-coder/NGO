import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import math

app = Flask(__name__)
app.secret_key = 'super-secret-key-creative'

os.makedirs('instance', exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ==========================================
# MODELS
# ==========================================

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.Text, nullable=False, unique=True)
    password = db.Column(db.Text, nullable=False)
    role = db.Column(db.Text, nullable=False) # 'admin' or 'volunteer'

class Request(db.Model):
    __tablename__ = 'requests'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text, nullable=False)
    skill = db.Column(db.Text, nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    urgency = db.Column(db.Integer, nullable=False)
    people_affected = db.Column(db.Integer, default=1)
    status = db.Column(db.Text, default='Pending')
    assigned_vol_id = db.Column(db.Integer, nullable=True)
    emergency_update = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    assigned_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    is_suspicious = db.Column(db.Boolean, default=False)

class Volunteer(db.Model):
    __tablename__ = 'volunteers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    name = db.Column(db.Text, nullable=False)
    email = db.Column(db.Text, nullable=False)
    skill = db.Column(db.Text, nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    available = db.Column(db.Boolean, default=True)
    points = db.Column(db.Integer, default=0)
    rating = db.Column(db.Integer, default=5)
    tasks_completed = db.Column(db.Integer, default=0)
    total_assigned = db.Column(db.Integer, default=0)

class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def log_event(msg):
    log = ActivityLog(message=msg)
    db.session.add(log)
    db.session.commit()

with app.app_context():
    db.create_all()

# ==========================================
# MODULAR AI ENGINES
# ==========================================

class PriorityEngine:
    @staticmethod
    def calculate_score(req):
        now = datetime.utcnow()
        delay_hours = (now - req.created_at).total_seconds() / 3600
        # (Urgency x 40) + (People x 20) + (Delay x 10)
        return (req.urgency * 40) + (req.people_affected * 20) + (delay_hours * 10)

class MatchingEngine:
    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371.0
        dLat = math.radians(lat2 - lat1)
        dLon = math.radians(lon2 - lon1)
        a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dLon/2)**2
        return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

    @staticmethod
    def get_best_volunteers(task):
        volunteers = Volunteer.query.filter_by(available=True).all()
        results = []
        for v in volunteers:
            score = 0
            explanation = []
            
            # 1. Skill Match
            if v.skill.lower() == task.skill.lower():
                score += 50
                explanation.append("Perfect Skill Match (+50)")
            
            # 2. Distance
            dist = MatchingEngine.haversine(task.latitude, task.longitude, v.latitude, v.longitude)
            dist_score = max(0, 30 - dist)
            score += dist_score
            if dist_score > 20: explanation.append(f"Very Close: {dist:.1f}km (+{dist_score:.0f})")
            
            # 3. Reputation
            rep_score = v.rating * 5
            score += rep_score
            explanation.append(f"Reputation: {v.rating}★ (+{rep_score})")
            
            # 4. Dynamic Load Balancing (Penalty for active tasks)
            active_tasks = v.total_assigned - v.tasks_completed
            if active_tasks > 0:
                load_penalty = active_tasks * 15
                score -= load_penalty
                explanation.append(f"Load Penalty: {active_tasks} active tasks (-{load_penalty})")
            
            results.append({
                'volunteer': v,
                'score': score,
                'distance': dist,
                'explanation': " | ".join(explanation)
            })
            
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:3]

class SecurityEngine:
    @staticmethod
    def check_suspicious(req):
        # Basic check: duplicates in same area
        similar = Request.query.filter(
            Request.title == req.title,
            Request.id != req.id
        ).all()
        for s in similar:
            dist = MatchingEngine.haversine(req.latitude, req.longitude, s.latitude, s.longitude)
            if dist < 0.5: # within 500m
                return True
        return False

# ==========================================
# ROUTES
# ==========================================

@app.route('/')
def home():
    if session.get('logged_in'):
        return redirect(url_for('volunteer_dashboard' if session.get('role') == 'volunteer' else 'dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        
        if User.query.filter_by(username=username).first():
            flash("Username taken!")
            return redirect(url_for('register'))
            
        new_user = User(username=username, password=password, role=role)
        db.session.add(new_user)
        db.session.commit()
        
        if role == 'volunteer':
            v = Volunteer(user_id=new_user.id, name=request.form.get('name'), email=request.form.get('email'),
                          skill=request.form.get('skill'), latitude=float(request.form.get('latitude') or 0),
                          longitude=float(request.form.get('longitude') or 0))
            db.session.add(v)
            db.session.commit()
            log_event(f"👤 New Volunteer registered: {v.name} ({v.skill})")
        else:
            log_event(f"🏢 New NGO Admin registered: {username}")
            
        flash("Success! Please login.")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username'), password=request.form.get('password')).first()
        if u:
            session.update({'logged_in': True, 'user_id': u.id, 'role': u.role})
            if u.role == 'volunteer':
                v = Volunteer.query.filter_by(user_id=u.id).first()
                if v: session['vol_id'] = v.id
                return redirect(url_for('volunteer_dashboard'))
            return redirect(url_for('dashboard'))
        flash("Failed.")
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect(url_for('login'))
    leaders = Volunteer.query.order_by(Volunteer.points.desc()).limit(3).all()
    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(10).all()
    reqs = Request.query.all()
    stats = {
        'total_vol': Volunteer.query.count(),
        'total_req': len(reqs),
        'completed': sum(1 for r in reqs if r.status == 'Completed'),
        'urgent': sum(1 for r in reqs if r.urgency == 3 and r.status != 'Completed')
    }
    return render_template('dashboard.html', leaders=leaders, stats=stats, logs=logs)

@app.route('/add_volunteer', methods=['GET', 'POST'])
def add_volunteer():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect(url_for('login'))
    if request.method == 'POST':
        v = Volunteer(name=request.form.get('name'), email=request.form.get('email'),
                      skill=request.form.get('skill'), latitude=float(request.form.get('latitude') or 0),
                      longitude=float(request.form.get('longitude') or 0), rating=int(request.form.get('rating') or 5))
        db.session.add(v)
        db.session.commit()
        log_event(f"👤 Admin manually added volunteer: {v.name}")
        return redirect(url_for('dashboard'))
    return render_template('add_volunteer.html')

@app.route('/add_request', methods=['GET', 'POST'])
def add_request():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect(url_for('login'))
    if request.method == 'POST':
        r = Request(title=request.form.get('title'), skill=request.form.get('skill'),
                    latitude=float(request.form.get('latitude') or 0), longitude=float(request.form.get('longitude') or 0),
                    urgency=int(request.form.get('urgency') or 1), people_affected=int(request.form.get('people_affected') or 1))
        
        # Security Engine Check
        if SecurityEngine.check_suspicious(r): 
            r.is_suspicious = True
            log_event(f"⚠️ SECURITY: Suspicious duplicate request detected: {r.title}")
        
        db.session.add(r)
        db.session.commit()
        log_event(f"📝 New Request created: {r.title} (Urgency: {r.urgency})")
        
        if r.urgency == 3 and not r.is_suspicious:
            vols = MatchingEngine.get_best_volunteers(r)
            if vols:
                best = vols[0]['volunteer']
                r.status, r.assigned_vol_id, r.assigned_at = 'Notified', best.id, datetime.utcnow()
                db.session.commit()
                log_event(f"🚨 CRISIS AUTO-ASSIGN: Notified {best.name} for {r.title}")
                flash(f"🚨 CRISIS: Email sent to {best.name} (Load Balanced Match)")
        
        return redirect(url_for('view_requests'))
    return render_template('add_request.html')

@app.route('/view_requests')
def view_requests():
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect(url_for('login'))
    reqs = Request.query.all()
    for r in reqs: r.priority_score = PriorityEngine.calculate_score(r)
    reqs.sort(key=lambda x: x.priority_score, reverse=True)
    return render_template('view_requests.html', requests=reqs)

@app.route('/match/<int:task_id>')
def match(task_id):
    if not session.get('logged_in') or session.get('role') != 'admin': return redirect(url_for('login'))
    task = Request.query.get_or_404(task_id)
    vols = MatchingEngine.get_best_volunteers(task)
    return render_template('match_volunteers.html', task=task, top_volunteers=vols)

@app.route('/dispatch/<int:task_id>/<int:vol_id>', methods=['POST'])
def dispatch(task_id, vol_id):
    r, v = Request.query.get_or_404(task_id), Volunteer.query.get_or_404(vol_id)
    r.status, r.assigned_vol_id, r.assigned_at = 'Notified', v.id, datetime.utcnow()
    db.session.commit()
    log_event(f"📧 Manual Dispatch: Admin assigned {v.name} to {r.title}")
    flash(f"Dispatch sent to {v.name}")
    return redirect(url_for('view_requests'))

@app.route('/volunteer_accept/<int:task_id>', methods=['POST'])
def volunteer_accept(task_id):
    if not session.get('logged_in') or session.get('role') != 'volunteer': return redirect(url_for('login'))
    
    task = Request.query.get_or_404(task_id)
    v = Volunteer.query.get(session.get('vol_id'))
    if not v:
        flash("Volunteer profile not found.")
        return redirect(url_for('login'))
        
    task.status = 'Assigned'
    v.total_assigned += 1
    v.points += (10 + (task.urgency * 5))
    db.session.commit()
    log_event(f"✅ Mission Accepted: {v.name} is now handling {task.title}")
    return redirect(url_for('volunteer_dashboard'))

@app.route('/volunteer_dashboard')
def volunteer_dashboard():
    if not session.get('logged_in') or session.get('role') != 'volunteer': return redirect(url_for('login'))
    vid = session.get('vol_id')
    vol = Volunteer.query.get(vid)
    if not vol:
        flash("Volunteer profile not found. Please re-login.")
        return redirect(url_for('logout'))
    tasks = Request.query.filter_by(assigned_vol_id=vid).all()
    return render_template('volunteer_dashboard.html', tasks=tasks, vol=vol)

@app.route('/complete/<int:task_id>', methods=['POST'])
def complete(task_id):
    r = Request.query.get_or_404(task_id)
    r.status, r.completed_at = 'Completed', datetime.utcnow()
    v = Volunteer.query.get(r.assigned_vol_id)
    if v: v.tasks_completed += 1
    db.session.commit()
    log_event(f"🏁 Task Completed: {r.title} closed by {v.name if v else 'System'}")
    return redirect(url_for('view_requests'))

@app.route('/api/heatmap_data')
def heatmap_data():
    reqs = Request.query.filter(Request.status != 'Completed').all()
    # [lat, lng, intensity]
    return jsonify([[r.latitude, r.longitude, r.urgency] for r in reqs])

@app.route('/api/parse_speech', methods=['POST'])
def parse_speech():
    text = request.json.get('text', '').lower()
    skill, urgency, people = 'general', 1, 1
    if any(k in text for k in ['medical', 'doctor', 'injury']): skill = 'medical'
    elif any(k in text for k in ['rescue', 'flood', 'trapped']): skill = 'rescue'
    elif any(k in text for k in ['food', 'ration']): skill = 'food'
    if any(k in text for k in ['urgent', 'high', 'emergency']): urgency = 3
    import re
    m = re.search(r'(\d+)\s*people', text)
    if m: people = int(m.group(1))
    return jsonify({'title': text.capitalize(), 'skill': skill, 'urgency': urgency, 'people_affected': people})

@app.route('/update_emergency/<int:task_id>', methods=['POST'])
def update_emergency(task_id):
    if not session.get('logged_in') or session.get('role') != 'volunteer': return redirect(url_for('login'))
    
    r = Request.query.get_or_404(task_id)
    vid = session.get('vol_id')
    
    if r.assigned_vol_id != vid:
        return "Unauthorized", 403
        
    update_text = request.form.get('update_text')
    r.emergency_update = update_text
    db.session.commit()
    
    vol = Volunteer.query.get(vid)
    log_event(f"📡 Emergency Update from {vol.name}: {update_text[:30]}...")
    flash("Emergency status updated successfully!")
    
    return redirect(url_for('volunteer_dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, port=5001)
