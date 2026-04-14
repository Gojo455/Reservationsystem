# REAL-TIME SEAT-AWARE MOVIE RECOMMENDER — BACKEND (Flask + SQLAlchemy)
#
# DFD Processes (Chapter 3, Level 1 Decomposition):
#   P1 — Authentication      (/api/register, /api/login)
#   P2 — Recommendation Engine (/api/recommendations)
#   P3 — Seat Management     (/api/seats, /api/lock-seat, /api/unlock-seat)
#   P4 — Payment Processing  (/api/payment/initialize, /api/payment/verify)
#   P5 — Preference Learning  (called internally after booking confirmation)
#
# Data Stores:
#   D1 — Users & Preferences  (User, UserPreference)
#   D2 — Movies & Showtimes   (Movie, Cinema, Hall, Showtime)
#   D3 — Seats & Seat Locks   (Seat + in-memory seat_locks dict)
#   D4 — Bookings             (Booking, BookingSeat)
#   D5 — Movie Ratings        (MovieRating)

import os
import json
import time
import hashlib
import binascii
import urllib.request
import urllib.error
from datetime import datetime, date
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

# APP INIT

app = Flask(__name__)
CORS(app)

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = (
    'sqlite:///' + os.path.join(basedir, 'booking.db') + '?check_same_thread=False'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ------------------------------------------------------------------------------
# NON-FUNCTIONAL REQUIREMENT: Concurrency
# In-memory seat lock store.
# Key   : (showtime_id, row_label, col_number)
# Value : { 'user_id': int, 'expires_at': float (Unix timestamp) }
# Locks expire after 5 minutes (300 seconds) per Chapter 3, Requirement vii.
# ------------------------------------------------------------------------------
SEAT_LOCK_DURATION = 300   # In seconds ooooooo
seat_locks = {}



# SECURITY HELPERS
# PBKDF2-HMAC-SHA256 with a 16-byte random salt.
# No third-party library required — uses Python's built-in hashlib.


def hash_password(plain_text):
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', plain_text.encode('utf-8'), salt, 100_000)
    return binascii.hexlify(salt).decode() + ':' + binascii.hexlify(key).decode()


def verify_password(stored_hash, plain_text):
    # Verify a plaintext password against a stored hash.
    try:
        salt_hex, key_hex = stored_hash.split(':')
        salt = binascii.unhexlify(salt_hex)
        key = hashlib.pbkdf2_hmac('sha256', plain_text.encode('utf-8'), salt, 100_000)
        return binascii.hexlify(key).decode() == key_hex
    except Exception:
        return False



# DATABASE MODELS

# D1  Users & Preferences


class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80),  unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    avatar_colour = db.Column(db.String(20),  default='#2ecc71')
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)
    is_admin = db.Column(db.Boolean,     default=False)

    preference = db.relationship('UserPreference', backref='user',
                                    uselist=False, cascade='all, delete-orphan')
    reservations = db.relationship('Reservation', backref='user', lazy=True)
    bookings = db.relationship('Booking',     backref='user', lazy=True)
    ratings = db.relationship('MovieRating', backref='user', lazy=True)


class UserPreference(db.Model):
    """
    D1  Implicit genre preference profile.
    Chapter 3: preferences stored as JSON dict mapping genre → score (0.0–1.0).
    One to one with User (user_id is both PK and FK).
    Updated by P5 (Preference Learning) after every confirmed booking.
    """
    __tablename__ = 'user_preference'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    # e.g. '{"Sci-Fi": 0.6, "Drama": 0.4}'  — empty dict for new users (cold-start)
    preferences = db.Column(db.Text, nullable=False, default='{}')



# D2  Movies & Showtimes


class Cinema(db.Model):
    __tablename__ = 'cinema'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(255))
    area = db.Column(db.String(100))
    halls = db.relationship('Hall', backref='cinema', lazy=True)


class Hall(db.Model):
    __tablename__ = 'hall'
    id = db.Column(db.Integer, primary_key=True)
    cinema_id = db.Column(db.Integer, db.ForeignKey('cinema.id'))
    name = db.Column(db.String(50))
    rows = db.Column(db.Integer)
    columns = db.Column(db.Integer)
    showtimes = db.relationship('Showtime', backref='hall', lazy=True)


class Movie(db.Model):
    """
    Chapter 3 ERD: title, genre, description, duration, rating (TMDB),
    language, release_date, poster_url, cast_list (JSON array),
    director, age_rating, is_featured, is_hot.
    """
    __tablename__ = 'movie'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    # Comma-separated genres, e.g. "Sci-Fi,Thriller"
    genre = db.Column(db.String(100))
    description = db.Column(db.Text)
    duration = db.Column(db.Integer)          # minutes
    rating = db.Column(db.Float, default=0.0)  # TMDB-style 0–10
    language = db.Column(db.String(30),  default='English')
    release_date = db.Column(db.String(20))
    poster_url = db.Column(db.String(255))
    # JSON array string, e.g. '["Actor A", "Actor B"]'
    cast_list = db.Column(db.Text)
    director = db.Column(db.String(100))
    age_rating = db.Column(db.String(10))
    is_featured = db.Column(db.Boolean, default=False)
    is_hot = db.Column(db.Boolean, default=False)

    showtimes = db.relationship('Showtime',    backref='movie', lazy=True)
    ratings = db.relationship('MovieRating', backref='movie', lazy=True)


class Showtime(db.Model):
    __tablename__ = 'showtime'
    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'))
    hall_id = db.Column(db.Integer, db.ForeignKey('hall.id'))
    show_date = db.Column(db.Date)
    time = db.Column(db.String(20))   # "HH:MM"
    price = db.Column(db.Float)
    seats = db.relationship('Seat', backref='showtime', lazy=True)


# D3  Seats & Seat Locks


class Seat(db.Model):
    """
    Chapter 3 ERD: row_label, col_number, category, quality_score, status.
    UNIQUE constraint on (showtime_id, row_label, col_number).
    quality_score computed by seed.py using the Chapter 3 formula.
    status values: 'available' | 'taken'
    Temporary locks are held in the in-memory seat_locks dict, NOT in this table.
    """
    __tablename__ = 'seat'
    __table_args__ = (
        db.UniqueConstraint('showtime_id', 'row_label', 'col_number',
                            name='uq_seat_showtime_row_col'),
    )
    id = db.Column(db.Integer, primary_key=True)
    showtime_id = db.Column(db.Integer, db.ForeignKey('showtime.id'))
    row_label = db.Column(db.String(5))
    col_number = db.Column(db.Integer)
    category = db.Column(db.String(20), default='standard')  # VIP/standard/back
    quality_score = db.Column(db.Float,   default=5.0)
    status = db.Column(db.String(20), default='available')  # 'available'|'taken'

    booking_seats = db.relationship('BookingSeat', backref='seat', lazy=True)
    reservations = db.relationship('Reservation', backref='seat', lazy=True)



# D4  Bookings


class Booking(db.Model):
    """
    Chapter 3 ERD: booking_reference (unique), total_amount, status, created_at.
    One booking → many seats via BookingSeat junction table.
    """
    __tablename__ = 'booking'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    showtime_id = db.Column(db.Integer, db.ForeignKey('showtime.id'))
    booking_reference = db.Column(db.String(20), unique=True, nullable=False)
    total_amount = db.Column(db.Float)
    status = db.Column(db.String(20), default='confirmed')
    created_at = db.Column(db.DateTime,   default=datetime.utcnow)

    booking_seats = db.relationship('BookingSeat', backref='booking',
                                    cascade='all, delete-orphan', lazy=True)


class BookingSeat(db.Model):
    """
    Chapter 3 ERD: Junction table — many-to-many between Booking and Seat.
    A single booking can cover multiple seats; each seat belongs to one booking.
    """
    __tablename__ = 'booking_seat'
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'))
    seat_id = db.Column(db.Integer, db.ForeignKey('seat.id'))


class Reservation(db.Model):
    """
    Legacy simple reservation kept for backward compatibility with seed.py.
    For new bookings the Booking + BookingSeat models are used instead.
    """
    __tablename__ = 'reservation'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    seat_id = db.Column(db.Integer, db.ForeignKey('seat.id'))
    booking_time = db.Column(db.DateTime, default=datetime.utcnow)


# D5  Movie Ratings

class MovieRating(db.Model):
    """
    Chapter 3 ERD: 1–5 star explicit ratings.
    UNIQUE on (user_id, movie_id) — one rating per user per movie.
    """
    __tablename__ = 'movie_rating'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'movie_id', name='uq_rating_user_movie'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'))
    stars = db.Column(db.Integer)   # 1–5


# Auto-create all tables on first run
with app.app_context():
    db.create_all()
    # Enable WAL mode for concurrent reads (Chapter 3, Non-Functional — Concurrency)
    from sqlalchemy import event, text
    @event.listens_for(db.engine, 'connect')
    def set_wal_mode(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()



# SEAT LOCK HELPERS


def _lock_key(showtime_id, row, col):
    return (showtime_id, row, col)


def _purge_expired_locks():
    """Remove all locks whose 5-minute window has passed."""
    now = time.time()
    expired = [k for k, v in seat_locks.items() if v['expires_at'] <= now]
    for k in expired:
        del seat_locks[k]


def get_seat_lock_status(showtime_id, row, col, requesting_user_id):
    """
    Returns one of:
      'available'         — no lock, seat is free
      'locked_by_you'     — current user holds this lock
      'locked_by_other'   — another user holds an active lock
      'taken'             — permanently booked in the DB
    """
    _purge_expired_locks()
    key = _lock_key(showtime_id, row, col)
    lock = seat_locks.get(key)
    if lock:
        if lock['user_id'] == requesting_user_id:
            return 'locked_by_you'
        return 'locked_by_other'
    return 'available'



# P2  The Recommendation Engine

# Hybrid scoring model — 6 signals, score capped at 99:
#   Signal I   — Genre Match          max 40 pts
#   Signal II  — Seat Availability    max 25 pts
#   Signal III — Seat Quality         max 15 pts
#   Signal IV  — Showtime Proximity   max 10 pts
#   Signal V   — Collaborative Filter max 10 pts
#   Signal VI  — Movie Rating Bonus   max  5 pts  (additive)


def compute_recommendation_scores(user_id, request_date, selected_genres=None):
    """
    Returns a list of dicts (up to 6) sorted by descending score.
    Each dict contains all the data the frontend needs to render a movie card.
    """
    selected_genres = [g.strip().lower() for g in (selected_genres or [])]

    # ── Load the requesting user's genre preference profile (D1) ─────────────
    pref_record = UserPreference.query.filter_by(user_id=user_id).first()
    user_prefs  = json.loads(pref_record.preferences) if pref_record else {}
    # Normalise keys to lowercase for case-insensitive matching
    user_prefs  = {k.lower(): v for k, v in user_prefs.items()}

    # ── Signal V prep: Jaccard collaborative filtering ────────────────────────
    # Get the requesting user's set of booked movie IDs
    my_reservations  = Reservation.query.filter_by(user_id=user_id).all()
    my_booked_seat_ids = {r.seat_id for r in my_reservations}
    my_booked_movie_ids = set()
    for seat_id in my_booked_seat_ids:
        seat = Seat.query.get(seat_id)
        if seat and seat.showtime:
            my_booked_movie_ids.add(seat.showtime.movie_id)

    # Also include Booking model (new flow)
    my_bookings = Booking.query.filter_by(user_id=user_id).all()
    for bk in my_bookings:
        if bk.showtime_id:
            st = Showtime.query.get(bk.showtime_id)
            if st:
                my_booked_movie_ids.add(st.movie_id)

    # Find up to 20 other users who share at least one booking
    other_users = User.query.filter(User.id != user_id).limit(20).all()
    similar_user_movie_ids = set()

    for other in other_users:
        other_res = Reservation.query.filter_by(user_id=other.id).all()
        other_movie_ids = set()
        for r in other_res:
            seat = Seat.query.get(r.seat_id)
            if seat and seat.showtime:
                other_movie_ids.add(seat.showtime.movie_id)
        for bk in Booking.query.filter_by(user_id=other.id).all():
            st = Showtime.query.get(bk.showtime_id)
            if st:
                other_movie_ids.add(st.movie_id)

        # Jaccard similarity = |A ∩ B| / |A ∪ B|
        if my_booked_movie_ids or other_movie_ids:
            intersection = len(my_booked_movie_ids & other_movie_ids)
            union        = len(my_booked_movie_ids | other_movie_ids)
            jaccard      = intersection / union if union > 0 else 0.0
            # Chapter 3: users with score > 0.1 are "similar users"
            if jaccard > 0.1:
                similar_user_movie_ids |= other_movie_ids

    _purge_expired_locks()
    now_hour = datetime.now().hour

    all_movies   = Movie.query.all()
    scored_movies = []

    for movie in all_movies:
        # Find the next showtime for this movie on request_date
        todays_shows = [
            st for st in movie.showtimes
            if st.show_date == request_date
        ]
        if not todays_shows:
            continue

        # Sort by time, pick the soonest upcoming one
        todays_shows.sort(key=lambda s: s.time)
        next_show = None
        for st in todays_shows:
            try:
                show_hour = int(st.time.split(':')[0])
            except (ValueError, IndexError):
                show_hour = 0
            if show_hour >= now_hour:
                next_show = st
                break
        # Fall back to last showtime of the day if all are in the past
        if next_show is None:
            next_show = todays_shows[-1]

        # Count available seats (excluding in-memory locks held by others)
        all_seats   = next_show.seats
        total_seats = len(all_seats)
        if total_seats == 0:
            continue

        available_seats = [
            s for s in all_seats
            if s.status == 'available'
            and get_seat_lock_status(next_show.id, s.row_label,
                                     s.col_number, user_id) != 'locked_by_other'
        ]
        if not available_seats:
            continue   # No seats available → not a candidate

        # ── Signal I: Genre Match (max 40 pts)
        movie_genres = [g.strip().lower() for g in (movie.genre or '').split(',') if g.strip()]
        num_genres    = len(movie_genres)

        if selected_genres:
            matched = [g for g in movie_genres if g in selected_genres]
        else:
            # No filter active → treat all genres as matching for base score
            matched = movie_genres

        match_ratio = len(matched) / num_genres if num_genres > 0 else 0.0

        # Preference adjustment: for each matched genre, add up to 0.3 × pref_score
        pref_boost = 0.0
        for g in matched:
            pref_score = user_prefs.get(g, 0.0)
            pref_boost += 0.3 * pref_score
        # Cap the total ratio + boost at 1.0 before multiplying by weight
        adjusted_ratio = min(1.0, match_ratio + pref_boost)
        signal_genre   = adjusted_ratio * 40.0

        # ── Signal II: Seat Availability (max 25 pts) ─────────────────────────
        availability_ratio   = len(available_seats) / total_seats
        signal_availability  = availability_ratio * 25.0

        # ── Signal III: Seat Quality (max 15 pts) ─────────────────────────────
        avg_quality   = (sum(s.quality_score for s in available_seats)
                         / len(available_seats))
        # quality_score is 0–10; normalise to 0–1 then multiply by 15
        signal_quality = (avg_quality / 10.0) * 15.0

        # ── Signal IV: Showtime Proximity (max 10 pts)
        try:
            show_hour = int(next_show.time.split(':')[0])
        except (ValueError, IndexError):
            show_hour = now_hour
        diff = abs(show_hour - now_hour)
        proximity_value = max(0.0, 1.0 - diff / 12.0)
        signal_proximity = proximity_value * 10.0

        # Signal V: Collaborative Filtering (max 10 pts)
        # 10-point boost if any similar user has booked this candidate movie
        signal_collab = 10.0 if movie.id in similar_user_movie_ids else 0.0

        #  Signal VI: Movie Rating Bonus (max +5 pts)
        # movie.rating is on a 0–10 TMDB scale (Chapter 3 ERD)
        signal_rating_bonus = (movie.rating / 10.0) * 5.0

        # Composite Score
        raw_score = (
            signal_genre
            + signal_availability
            + signal_quality
            + signal_proximity
            + signal_collab
            + signal_rating_bonus
        )
        # Cap at 99 for presentation (Chapter 3)
        final_score = min(99.0, round(raw_score, 1))

        scored_movies.append({
            "movie_id":         movie.id,
            "title":            movie.title,
            "genre":            movie.genre,
            "director":         movie.director,
            "cast_list":        movie.cast_list,
            "age_rating":       movie.age_rating,
            "duration":         movie.duration,
            "rating":           movie.rating,
            "poster_url":       movie.poster_url,
            "showtime_id":      next_show.id,
            "showtime_time":    next_show.time,
            "showtime_price":   next_show.price,
            "available_seats":  len(available_seats),
            "total_seats":      total_seats,
            "recommendation_score": final_score,
            # Breakdown for transparency / debugging
            "score_breakdown": {
                "genre_match":          round(signal_genre,        1),
                "seat_availability":    round(signal_availability, 1),
                "seat_quality":         round(signal_quality,      1),
                "showtime_proximity":   round(signal_proximity,    1),
                "collaborative":        round(signal_collab,       1),
                "rating_bonus":         round(signal_rating_bonus, 1),
            }
        })

    # Sort descending by recommendation score, return top 6 (Chapter 3 — FR viii)
    scored_movies.sort(key=lambda x: x['recommendation_score'], reverse=True)
    return scored_movies[:6]



# P5  PREFERENCE LEARNING
# Called internally after every confirmed booking.
# Increments each genre score by 0.2, capped at 1.0.


def update_user_preferences(user_id, movie_genres):
    """
    movie_genres: comma-separated string from Movie.genre, e.g. "Sci-Fi,Thriller"
    """
    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if not pref:
        pref = UserPreference(user_id=user_id, preferences='{}')
        db.session.add(pref)

    prefs = json.loads(pref.preferences)
    for genre in movie_genres.split(','):
        g = genre.strip()
        if g:
            prefs[g] = min(1.0, round(prefs.get(g, 0.0) + 0.2, 2))
    pref.preferences = json.dumps(prefs)
    # Caller is responsible for db.session.commit()



#  The API Routes



# P1  AUTHENTICATION  (D1: Users & Preferences)


@app.route('/api/register', methods=['POST'])
def register():
    """
    FR i — User Registration.
    Hashes password with PBKDF2-HMAC-SHA256 + 16-byte random salt.
    Creates a blank UserPreference record (cold-start handling).
    """
    data = request.json or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')

    if not username or not email or not password:
        return jsonify({"message": "All fields are required."}), 400

    if User.query.filter(
        (User.username == username) | (User.email == email)
    ).first():
        return jsonify({"message": "Username or email already exists."}), 409

    new_user = User(
        username=username,
        email=email,
        password=hash_password(password)
    )
    db.session.add(new_user)
    db.session.flush()   # assigns new_user.id before commit

    # Cold-start: create empty preference profile (Chapter 3 — Cold-Start Handling)
    pref = UserPreference(user_id=new_user.id, preferences='{}')
    db.session.add(pref)
    db.session.commit()

    return jsonify({"message": "Registration successful!"}), 201


@app.route('/api/login', methods=['POST'])
def login():
    """
    FR ii — User Authentication.
    Verifies password using PBKDF2-HMAC-SHA256.
    """
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"message": "Please fill in both fields."}), 400

    user = User.query.filter_by(username=username).first()
    if not user or not verify_password(user.password, password):
        return jsonify({"message": "Invalid username or password."}), 401

    return jsonify({
        "message":       "Login successful",
        "user_id":       user.id,
        "username":      user.username,
        "avatar_colour": user.avatar_colour,
        "is_admin":      user.is_admin
    }), 200



# P2  RECOMMENDATION ENGINE  (Using D1, D2, D3)


@app.route('/api/recommendations', methods=['GET'])
def get_recommendations():
    """
    FR viii — Movie Recommendation.
    Returns up to 6 ranked movies using the 6-signal hybrid scoring model.

    Query params:
      user_id (required)
      date    (optional, ISO format YYYY-MM-DD, defaults to today)
      genres  (optional, comma-separated filter e.g. "Sci-Fi,Drama")
    """
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"message": "user_id is required."}), 400

    date_str = request.args.get('date')
    try:
        req_date = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        req_date = date.today()

    genres_param     = request.args.get('genres', '')
    selected_genres  = [g.strip() for g in genres_param.split(',') if g.strip()]

    recommendations = compute_recommendation_scores(user_id, req_date, selected_genres)
    return jsonify(recommendations), 200



# P3 — SEAT MANAGEMENT  (D3: Seats & Seat Locks)


@app.route('/api/seats', methods=['GET'])
def get_seats():
    """
    FR vi — Interactive Seat Map.
    Returns all seats for a given showtime with their real-time status:
      available | locked_by_you | locked_by_other | taken

    Query params:
      showtime_id (required)
      user_id     (required, needed to distinguish own locks from others')
    """
    showtime_id = request.args.get('showtime_id', type=int)
    user_id = request.args.get('user_id',     type=int)

    if not showtime_id or not user_id:
        return jsonify({"message": "showtime_id and user_id are required."}), 400

    showtime = Showtime.query.get(showtime_id)
    if not showtime:
        return jsonify({"message": "Showtime not found."}), 404

    _purge_expired_locks()
    output = []
    for seat in showtime.seats:
        if seat.status == 'taken':
            display_status  = 'taken'
            lock_expires_at = None
        else:
            display_status = get_seat_lock_status(
                showtime_id, seat.row_label, seat.col_number, user_id
            )
            key  = _lock_key(showtime_id, seat.row_label, seat.col_number)
            lock = seat_locks.get(key)
            lock_expires_at = round(lock['expires_at']) if lock else None

        output.append({
            "id":             seat.id,
            "seat_label":     f"{seat.row_label}{seat.col_number}",
            "row_label":      seat.row_label,
            "col_number":     seat.col_number,
            "category":       seat.category,
            "quality_score":  seat.quality_score,
            "status":         display_status,
            "lock_expires_at": lock_expires_at,
        })

    return jsonify(output), 200


@app.route('/api/lock-seat', methods=['POST'])
def lock_seat():
    """
    FR vii — Seat Locking Mechanism.
    Temporarily locks a seat for 5 minutes.
    If the seat is already locked by another user, returns 409.

    Body: { showtime_id, seat_id, user_id }
    """
    data        = request.json or {}
    showtime_id = data.get('showtime_id')
    seat_id     = data.get('seat_id')
    user_id     = data.get('user_id')

    if not all([showtime_id, seat_id, user_id]):
        return jsonify({"message": "showtime_id, seat_id and user_id are required."}), 400

    seat = Seat.query.get(seat_id)
    if not seat:
        return jsonify({"message": "Seat not found."}), 404
    if seat.status == 'taken':
        return jsonify({"message": "Seat is already booked."}), 409

    _purge_expired_locks()
    key = _lock_key(showtime_id, seat.row_label, seat.col_number)
    lock = seat_locks.get(key)

    if lock and lock['user_id'] != user_id and lock['expires_at'] > time.time():
        return jsonify({"message": "Seat is temporarily held by another user."}), 409

    # Place or refresh lock
    seat_locks[key] = {
        'user_id':    user_id,
        'expires_at': time.time() + SEAT_LOCK_DURATION
    }
    return jsonify({
        "message":    "Seat locked successfully.",
        "seat_label": f"{seat.row_label}{seat.col_number}",
        "expires_in": SEAT_LOCK_DURATION
    }), 200


@app.route('/api/unlock-seat', methods=['POST'])
def unlock_seat():
    """
    Releases a seat lock held by the requesting user.
    Called when a user deselects a seat before payment.

    Body: { showtime_id, seat_id, user_id }
    """
    data = request.json or {}
    showtime_id = data.get('showtime_id')
    seat_id = data.get('seat_id')
    user_id = data.get('user_id')

    seat = Seat.query.get(seat_id)
    if not seat:
        return jsonify({"message": "Seat not found."}), 404

    key = _lock_key(showtime_id, seat.row_label, seat.col_number)
    lock = seat_locks.get(key)

    if lock and lock['user_id'] == user_id:
        del seat_locks[key]
        return jsonify({"message": "Seat unlocked."}), 200

    return jsonify({"message": "No active lock found for this seat."}), 404



# P4  PAYMENT PROCESSING  (Paystack integration + Demo mode)


PAYSTACK_SECRET = os.environ.get('PAYSTACK_SECRET_KEY', '')


def _paystack_request(endpoint, payload=None, method='POST'):
    """Helper: make an authenticated request to the Paystack API."""
    url = f"https://api.paystack.co{endpoint}"
    headers = {
        'Authorization': f'Bearer {PAYSTACK_SECRET}',
        'Content-Type':  'application/json'
    }
    body = json.dumps(payload).encode('utf-8') if payload else None
    req  = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {"status": False, "message": str(e)}
    except Exception as e:
        return {"status": False, "message": str(e)}


@app.route('/api/payment/initialize', methods=['POST'])
def payment_initialize():
    """
    FR x — Payment Processing (initialize).
    Calls Paystack /transaction/initialize.
    Falls back to demo mode if PAYSTACK_SECRET_KEY is not set.

    Body: { user_id, showtime_id, seat_ids: [int, ...], email }
    """
    data = request.json or {}
    user_id = data.get('user_id')
    showtime_id = data.get('showtime_id')
    seat_ids = data.get('seat_ids', [])
    email = data.get('email', '')

    if not all([user_id, showtime_id, seat_ids]):
        return jsonify({"message": "user_id, showtime_id and seat_ids are required."}), 400

    showtime = Showtime.query.get(showtime_id)
    if not showtime:
        return jsonify({"message": "Showtime not found."}), 404

    amount_ngn = showtime.price * len(seat_ids)
    amount_kobo = int(amount_ngn * 100)   # Paystack uses kobo

    # Demo mode: no live Paystack key configured (Chapter 3, Reliability NFR)
    if not PAYSTACK_SECRET:
        import uuid
        demo_ref = 'DEMO-' + str(uuid.uuid4())[:8].upper()
        return jsonify({
            "message":           "Demo mode — no Paystack key configured.",
            "demo_mode":         True,
            "reference":         demo_ref,
            "authorization_url": None,
            "amount_ngn":        amount_ngn
        }), 200

    result = _paystack_request('/transaction/initialize', {
        'email':    email,
        'amount':   amount_kobo,
        'currency': 'NGN',
        'metadata': {
            'user_id':     user_id,
            'showtime_id': showtime_id,
            'seat_ids':    seat_ids
        }
    })

    if not result.get('status'):
        return jsonify({"message": result.get('message', 'Paystack error.')}), 502

    return jsonify({
        "reference":         result['data']['reference'],
        "authorization_url": result['data']['authorization_url'],
        "amount_ngn":        amount_ngn
    }), 200


@app.route('/api/payment/verify', methods=['POST'])
def payment_verify():
    """
    FR xi — Booking Confirmation.
    Verifies payment with Paystack, then:
      1. Permanently marks seats as 'taken'
      2. Removes in-memory seat locks
      3. Creates Booking + BookingSeat records
      4. Calls P5 (Preference Learning) to update genre profile
      5. Returns booking reference

    Body: { reference, user_id, showtime_id, seat_ids: [int, ...] }
    """
    data   = request.json or {}
    reference = data.get('reference', '')
    user_id  = data.get('user_id')
    showtime_id = data.get('showtime_id')
    seat_ids = data.get('seat_ids', [])

    if not all([reference, user_id, showtime_id, seat_ids]):
        return jsonify({"message": "reference, user_id, showtime_id and seat_ids are required."}), 400

    # Demo mode bypass
    if reference.startswith('DEMO-') or not PAYSTACK_SECRET:
        payment_ok = True
    else:
        result  = _paystack_request(f'/transaction/verify/{reference}', method='GET')
        payment_ok = (result.get('status') and
                      result.get('data', {}).get('status') == 'success')

    if not payment_ok:
        return jsonify({"message": "Payment verification failed."}), 402

    try:
        showtime = Showtime.query.get(showtime_id)
        if not showtime:
            return jsonify({"message": "Showtime not found."}), 404

        # Generate unique booking reference
        import uuid
        booking_ref  = 'BK-' + str(uuid.uuid4())[:8].upper()
        total_amount = showtime.price * len(seat_ids)

        booking = Booking(
            user_id=user_id,
            showtime_id=showtime_id,
            booking_reference=booking_ref,
            total_amount=total_amount,
            status='confirmed'
        )
        db.session.add(booking)
        db.session.flush()

        booked_seat_labels = []
        for seat_id in seat_ids:
            seat = Seat.query.get(seat_id)
            if seat and seat.status == 'available':
                seat.status = 'taken'
                db.session.add(BookingSeat(booking_id=booking.id, seat_id=seat_id))
                # Release the in-memory lock
                key = _lock_key(showtime_id, seat.row_label, seat.col_number)
                seat_locks.pop(key, None)
                booked_seat_labels.append(f"{seat.row_label}{seat.col_number}")

        # P5 For Preference Learning
        movie = Movie.query.get(showtime.movie_id)
        if movie and movie.genre:
            update_user_preferences(user_id, movie.genre)

        db.session.commit()

        return jsonify({
            "message":           "Booking confirmed!",
            "booking_reference": booking_ref,
            "seats_booked":      booked_seat_labels,
            "total_amount_ngn":  total_amount
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Booking error: {str(e)}"}), 500


# SUPPORTING ROUTES


@app.route('/api/my-bookings/<int:user_id>', methods=['GET'])
def get_my_bookings(user_id):
    """Returns the full booking history for a user."""
    bookings = Booking.query.filter_by(user_id=user_id)\
                            .order_by(Booking.created_at.desc()).all()
    output = []
    for bk in bookings:
        showtime = Showtime.query.get(bk.showtime_id)
        movie    = Movie.query.get(showtime.movie_id) if showtime else None
        seats    = [bs.seat for bs in bk.booking_seats]
        output.append({
            "booking_reference": bk.booking_reference,
            "movie_title":       movie.title if movie else "Unknown",
            "showtime":          showtime.time if showtime else "—",
            "show_date":         showtime.show_date.isoformat() if showtime else "—",
            "seats":             [f"{s.row_label}{s.col_number}" for s in seats],
            "total_amount_ngn":  bk.total_amount,
            "status":            bk.status,
            "booked_at":         bk.created_at.strftime("%Y-%m-%d %H:%M")
        })

    # Legacy reservations (seed data / old flow)
    for r in Reservation.query.filter_by(user_id=user_id).all():
        seat = Seat.query.get(r.seat_id)
        if seat:
            output.append({
                "booking_reference": f"RES-{r.id}",
                "movie_title":       seat.showtime.movie.title
                                     if seat.showtime and seat.showtime.movie else "Unknown",
                "showtime":          seat.showtime.time if seat.showtime else "—",
                "show_date":         seat.showtime.show_date.isoformat()
                                     if seat.showtime else "—",
                "seats":             [f"{seat.row_label}{seat.col_number}"],
                "total_amount_ngn":  None,
                "status":            "confirmed",
                "booked_at":         r.booking_time.strftime("%Y-%m-%d %H:%M")
            })

    return jsonify(output), 200


@app.route('/api/movies', methods=['GET'])
def get_movies():
    """
    FR iii/iv — Movie Catalogue Display + Search/Filter.
    Query params: search (keyword), genre (filter)
    """
    search = request.args.get('search', '').strip().lower()
    genre  = request.args.get('genre',  '').strip().lower()

    query = Movie.query
    if search:
        query = query.filter(
            db.or_(
                Movie.title.ilike(f'%{search}%'),
                Movie.genre.ilike(f'%{search}%'),
                Movie.description.ilike(f'%{search}%')
            )
        )
    if genre:
        query = query.filter(Movie.genre.ilike(f'%{genre}%'))

    movies = query.all()
    return jsonify([{
        "id":          m.id,
        "title":       m.title,
        "genre":       m.genre,
        "duration":    m.duration,
        "rating":      m.rating,
        "age_rating":  m.age_rating,
        "director":    m.director,
        "cast_list":   m.cast_list,
        "description": m.description,
        "poster_url":  m.poster_url,
        "is_featured": m.is_featured,
        "is_hot":      m.is_hot
    } for m in movies]), 200


@app.route('/api/showtimes/<int:movie_id>', methods=['GET'])
def get_showtimes(movie_id):
    """
    FR v — Showtime Display.
    Returns all showtimes for a movie with seat availability counts.
    """
    movie = Movie.query.get(movie_id)
    if not movie:
        return jsonify({"message": "Movie not found."}), 404

    output = []
    for st in movie.showtimes:
        total     = len(st.seats)
        available = sum(1 for s in st.seats if s.status == 'available')
        output.append({
            "id":              st.id,
            "show_date":       st.show_date.isoformat(),
            "time":            st.time,
            "price":           st.price,
            "total_seats":     total,
            "available_seats": available,
            "hall":            st.hall.name    if st.hall   else None,
            "cinema":          st.hall.cinema.name
                               if st.hall and st.hall.cinema else None
        })

    output.sort(key=lambda x: (x['show_date'], x['time']))
    return jsonify(output), 200


@app.route('/api/rate-movie', methods=['POST'])
def rate_movie():
    """
    D5 — Movie Ratings.
    Body: { user_id, movie_id, stars (1–5) }
    UNIQUE constraint prevents duplicate ratings.
    """
    data     = request.json or {}
    user_id  = data.get('user_id')
    movie_id = data.get('movie_id')
    stars    = data.get('stars')

    if not all([user_id, movie_id, stars]) or stars not in range(1, 6):
        return jsonify({"message": "user_id, movie_id and stars (1–5) are required."}), 400

    existing = MovieRating.query.filter_by(user_id=user_id, movie_id=movie_id).first()
    if existing:
        existing.stars = stars
    else:
        db.session.add(MovieRating(user_id=user_id, movie_id=movie_id, stars=stars))

    db.session.commit()
    return jsonify({"message": "Rating saved."}), 200


@app.route('/api/reset', methods=['POST'])
def reset_system():
    """Dev utility: reset all bookings and seat statuses."""
    try:
        # Clear locks from memory
        seat_locks.clear()
        # Remove booking records
        db.session.query(BookingSeat).delete()
        db.session.query(Booking).delete()
        db.session.query(Reservation).delete()
        # Reset seat statuses
        Seat.query.update({"status": "available"})
        db.session.commit()
        return jsonify({"message": "System reset successfully."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Reset error: {str(e)}"}), 500




# ADMIN ROUTES — protected, only accessible when user.is_admin == True
#
# How the protection works:
#   Every route reads user_id from the request, looks up the User row,
#   and checks is_admin. If False → 403 Forbidden immediately.
#   This means even if someone discovers the URL they cannot use it
#   without supplying the ID of an admin account.
#
# Routes:
#   GET  /api/admin/halls              — list halls for the showtime dropdown
#   POST /api/admin/add-movie          — create movie + showtime + seats
#   GET  /api/admin/movies             — list all movies with showtime counts
#   DELETE /api/admin/delete-movie/<id>— remove movie + all showtimes + seats

def _require_admin(user_id):
    """
    Helper used at the top of every admin route.
    Returns (user, None) on success, or (None, error_response) on failure.

    Call it like:
        user, err = _require_admin(user_id)
        if err: return err
    """
    if not user_id:
        return None, (jsonify({"message": "user_id is required."}), 400)
    user = User.query.get(user_id)
    if not user:
        return None, (jsonify({"message": "User not found."}), 404)
    if not user.is_admin:
        return None, (jsonify({"message": "Admin access required."}), 403)
    return user, None


@app.route('/api/admin/halls', methods=['GET'])
def admin_get_halls():
    """
    Returns all halls with their cinema name, rows, and columns.
    Used to populate the hall dropdown in the admin Add Movie form.

    Query param: user_id (required, must be admin)
    """
    user_id = request.args.get('user_id', type=int)
    _, err  = _require_admin(user_id)
    if err: return err

    halls = Hall.query.all()
    return jsonify([{
        "id":      h.id,
        "name":    h.name,
        "cinema":  h.cinema.name if h.cinema else "Unknown",
        "rows":    h.rows,
        "columns": h.columns
    } for h in halls]), 200


@app.route('/api/admin/movies', methods=['GET'])
def admin_get_movies():
    """
    Returns all movies with showtime counts.
    Used to populate the movie list in the admin panel.

    Query param: user_id (required, must be admin)
    """
    user_id = request.args.get('user_id', type=int)
    _, err  = _require_admin(user_id)
    if err: return err

    movies = Movie.query.order_by(Movie.id.desc()).all()
    return jsonify([{
        "id":            m.id,
        "title":         m.title,
        "genre":         m.genre,
        "rating":        m.rating,
        "showtime_count": len(m.showtimes)
    } for m in movies]), 200


@app.route('/api/admin/add-movie', methods=['POST'])
def admin_add_movie():
    """
    Creates a new Movie, one Showtime, and all Seats for that showtime
    in a single atomic request.

    Why one request for all three?
    A movie without a showtime is invisible to the recommendation engine
    (it looks for movies with at least one showtime on the requested date).
    A showtime without seats means zero available seats, so seat_availability
    signal = 0 and the movie would score badly. All three are created together
    so the movie is immediately live and correctly scored.

    Expected JSON body:
    {
        "user_id":      1,               -- must be admin

        -- Movie fields --
        "title":        "Oppenheimer",
        "genre":        "Drama,History", -- comma-separated
        "description":  "...",
        "duration":     180,             -- minutes
        "rating":       8.3,             -- 0–10 TMDB scale
        "age_rating":   "PG-13",
        "director":     "Christopher Nolan",
        "cast_list":    "Cillian Murphy, Emily Blunt",
        "poster_url":   "https://...",   -- optional

        -- Showtime fields --
        "hall_id":      1,
        "show_date":    "2025-04-15",    -- YYYY-MM-DD
        "show_time":    "18:00",         -- HH:MM
        "price":        3000.0
    }

    What happens step by step:
    1. Validate admin access
    2. Validate all required fields
    3. Insert Movie row
    4. Insert Showtime row linked to that movie and hall
    5. Compute seat quality scores using Chapter 3 formula and insert all seats
    6. Commit everything in one transaction — if anything fails, nothing is saved
    """
    data = request.json or {}
    user_id = data.get('user_id')

    _, err = _require_admin(user_id)
    if err: return err

    # Validate required fields
    required = ['title', 'genre', 'duration', 'rating', 'age_rating',
                'director', 'hall_id', 'show_date', 'show_time', 'price']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"message": "Missing fields: " + ", ".join(missing)}), 400

    hall = Hall.query.get(data['hall_id'])
    if not hall:
        return jsonify({"message": "Hall not found."}), 404

    try:
        show_date = date.fromisoformat(data['show_date'])
    except ValueError:
        return jsonify({"message": "show_date must be YYYY-MM-DD."}), 400

    try:
        #  Step 3 Create Movie
        movie = Movie(
            title= data['title'].strip(),
            genre= data['genre'].strip(),
            description = data.get('description', '').strip(),
            duration= int(data['duration']),
            rating= float(data['rating']),
            age_rating= data['age_rating'].strip(),
            director= data.get('director', '').strip(),
            cast_list= data.get('cast_list', '').strip(),
            poster_url= data.get('poster_url', '').strip() or None,
            is_featured = bool(data.get('is_featured', False)),
            is_hot= bool(data.get('is_hot', False))
        )
        db.session.add(movie)
        db.session.flush()   # get movie.id without committing

        # ── Step 4: Create Showtime ───────────────────────────────────────────
        showtime = Showtime(
            movie_id= movie.id,
            hall_id= hall.id,
            show_date= show_date,
            time = data['show_time'].strip(),
            price= float(data['price'])
        )
        db.session.add(showtime)
        db.session.flush()   # get showtime.id
        # Step 5: Create Seats using Chapter 3 quality formula
        #
        # The formula (from Section 3.4.4):
        #   row_score    = 1 - |r - mid_row| / total_rows
        #   col_score    = 1 - |c - mid_col| / total_cols
        #   quality_score = (row_score * 0.5 + col_score * 0.5) * 10
        # Where:
        #   r, c      = zero-indexed row and column position
        #   mid_row   = (total_rows - 1) / 2   e.g. for 5 rows → 2.0
        #   mid_col   = (total_cols - 1) / 2   e.g. for 8 cols → 3.5
        # Seats closest to the centre score near 10.0
        # Seats at the corners score near 0.0
        # The score is stored in the DB and used by Signal III of the engine.

        row_labels = ['A','B','C','D','E','F','G','H','I','J'][:hall.rows]
        total_rows = hall.rows
        total_cols = hall.columns
        mid_row    = (total_rows - 1) / 2.0
        mid_col    = (total_cols - 1) / 2.0

        for r_idx, label in enumerate(row_labels):
            for c_idx in range(total_cols):
                row_score = 1 - (abs(r_idx - mid_row) / total_rows)
                col_score = 1 - (abs(c_idx - mid_col) / total_cols)
                q_score   = round((row_score * 0.5 + col_score * 0.5) * 10, 2)
                db.session.add(Seat(
                    showtime_id= showtime.id,
                    row_label= label,
                    col_number= c_idx + 1,
                    quality_score= q_score,
                    status= 'available'
                ))

        #  Step 6: Commit everything atomicall
        db.session.commit()

        return jsonify({
            "message":        "Movie added successfully!",
            "movie_id":       movie.id,
            "movie_title":    movie.title,
            "showtime_id":    showtime.id,
            "show_date":      show_date.isoformat(),
            "show_time":      showtime.time,
            "seats_created":  total_rows * total_cols
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Error adding movie: " + str(e)}), 500


@app.route('/api/admin/delete-movie/<int:movie_id>', methods=['DELETE'])
def admin_delete_movie(movie_id):
    """
    Deletes a movie and everything linked to it:
    BookingSeats → Bookings → Reservations → Seats → Showtimes → Movie

    The deletion order respects foreign key constraints.
    We delete child records first before the parent row.

    Query param: user_id (required, must be admin)
    """
    user_id = request.args.get('user_id', type=int)
    _, err  = _require_admin(user_id)
    if err: return err

    movie = Movie.query.get(movie_id)
    if not movie:
        return jsonify({"message": "Movie not found."}), 404

    try:
        title = movie.title
        for st in movie.showtimes:
            # Release any in-memory seat locks for this showtime
            keys_to_remove = [k for k in seat_locks if k[0] == st.id]
            for k in keys_to_remove:
                del seat_locks[k]

            for seat in st.seats:
                # Delete booking_seat records pointing to this seat
                db.session.query(BookingSeat).filter_by(seat_id=seat.id).delete()
                # Delete legacy reservations
                db.session.query(Reservation).filter_by(seat_id=seat.id).delete()

            # Delete bookings for this showtime (booking_seats already removed)
            db.session.query(Booking).filter_by(showtime_id=st.id).delete()

            # Delete all seats for this showtime
            db.session.query(Seat).filter_by(showtime_id=st.id).delete()

        # Delete all showtimes for this movie
        db.session.query(Showtime).filter_by(movie_id=movie_id).delete()
        # Delete the movie itself
        db.session.delete(movie)
        db.session.commit()

        return jsonify({
            "message": "Movie deleted.",
            "deleted": title
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Delete error: " + str(e)}), 500



# STATIC FILE SERVING — open index.html via http://127.0.0.1:5000
# instead of the file:// protocol (avoids CORS issues entirely)

from flask import send_from_directory

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory('.', filename)



# DEBUG ROUTE — verify recommendation engine and preference learning (Q1)
# GET /api/debug/recommendations?user_id=1&date=YYYY-MM-DD
#
# Returns a detailed breakdown of every signal for every candidate movie,
# plus the user's current preference profile so you can see how P5
# has been updating it after each booking.

@app.route('/api/debug/recommendations', methods=['GET'])
def debug_recommendations():
    """
    Development-only route.
    Returns the full score computation for every candidate movie on a date,
    alongside the requesting user's current genre preference profile.
    Useful for verifying that:
      - Genre Match scores shift when user_preferences are updated (P5)
      - Collaborative boost activates when Jaccard > 0.1
      - Seat Quality and Availability signals respond to seat bookings
      - Showtime Proximity changes as the system clock advances
    """
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({"message": "user_id is required"}), 400

    date_str = request.args.get('date')
    try:
        req_date = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        req_date = date.today()

    # Fetch preference profile so caller can see current state
    pref = UserPreference.query.filter_by(user_id=user_id).first()
    prefs = json.loads(pref.preferences) if pref else {}

    # Run the full engine — scores includes score_breakdown per movie
    scores = compute_recommendation_scores(user_id, req_date)

    # Also return ALL movies on that date (not just top 6) for full transparency
    all_movies_on_date = []
    for movie in Movie.query.all():
        shows_on_date = [s for s in movie.showtimes if s.show_date == req_date]
        if shows_on_date:
            all_movies_on_date.append(movie.title)

    return jsonify({
        "user_id":              user_id,
        "date_queried":         req_date.isoformat(),
        "current_preferences":  prefs,
        "movies_on_date":       all_movies_on_date,
        "top_6_recommendations": scores,
        "how_to_verify": {
            "Genre Match":        "Book a Sci-Fi movie, then re-call this endpoint — the Sci-Fi preference score increments by 0.2 and the Genre Match points for Sci-Fi movies rise.",
            "Seat Availability":  "Book seats for a showtime until it is nearly full — its seat_availability signal drops toward 0.",
            "Seat Quality":       "Book the centre seats (C4, C5) — the avg quality of remaining seats drops, reducing this signal.",
            "Showtime Proximity": "Changes automatically as the system clock advances through the day.",
            "Collaborative":      "Log in as Temi, book Inception. Log in as Ayo — Inception gets a +10 collab boost because Temi (similar user) booked it.",
            "Preference Learning":"Check current_preferences before and after a booking — the booked movie\'s genres should each increase by 0.2 (capped at 1.0)."
        }
    }), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)