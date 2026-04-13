import json
from datetime import date, timedelta
from app import app, db, hash_password
from app import (User, UserPreference, Movie, Cinema,
                 Hall, Showtime, Seat, Reservation)


def seed_database():
    with app.app_context():
        try:
            print("Starting seed process...")

            # ── 1. Clear all tables in dependency order ────────────────────────
            db.session.query(Reservation).delete()
            db.session.query(UserPreference).delete()
            db.session.query(Seat).delete()
            db.session.query(Showtime).delete()
            db.session.query(Hall).delete()
            db.session.query(Movie).delete()
            db.session.query(Cinema).delete()
            db.session.query(User).delete()
            db.session.commit()

            # ── 2. Dates ───────────────────────────────────────────────────────
            today    = date.today()
            tomorrow = today + timedelta(days=1)

            # ── 3. Users ───────────────────────────────────────────────────────
            # Admin account — is_admin=True unlocks the admin panel in the UI.
            # The admin can add movies, showtimes and delete content without
            # touching seed.py or the database directly.
            admin = User(username="admin", email="admin@cineselect.ng",
                         password=hash_password("admin123"), is_admin=True)

            ayo  = User(username="Ayo",  email="ayo@example.com",
                        password=hash_password("password123"))
            temi = User(username="Temi", email="temi@example.com",
                        password=hash_password("temi456"))
            db.session.add_all([admin, ayo, temi])
            db.session.commit()

            # ── 4. Preference profiles ─────────────────────────────────────────
            # Ayo prefers Sci-Fi/Thriller — so on login those movies should
            # score higher in the Genre Match signal (verifiable via debug route)
            db.session.add_all([
                UserPreference(user_id=ayo.id,
                               preferences=json.dumps({"Sci-Fi": 0.6, "Thriller": 0.4})),
                UserPreference(user_id=temi.id,
                               preferences=json.dumps({"Drama": 0.8, "Sci-Fi": 0.2})),
            ])
            db.session.commit()

            # ── 5. Cinema + Hall ───────────────────────────────────────────────
            cinema = Cinema(name="Ayo's Grand Theatre",
                            address="123 AI Avenue, Lagos")
            db.session.add(cinema)
            db.session.commit()

            hall = Hall(cinema_id=cinema.id, name="Hall 1", rows=5, columns=8)
            db.session.add(hall)
            db.session.commit()

            # ── 6. Movies split across two dates ──────────────────────────────
            #
            # TODAY (3 movies):
            #   Inception       — Sci-Fi / Thriller   18:00  ₦3,000
            #   The Dark Knight — Action  / Thriller   16:00  ₦2,500
            #   Parasite        — Drama   / Thriller   14:00  ₦2,000
            #
            # TOMORROW (3 movies):
            #   Interstellar    — Sci-Fi  / Drama      18:00  ₦3,000
            #   Everything EAO  — Sci-Fi  / Comedy     16:00  ₦2,500
            #   Get Out         — Horror  / Thriller   14:00  ₦2,000
            #
            # Why this split matters for verification:
            #   • Ayo has Sci-Fi preference → Inception and Interstellar should
            #     both score high on the Genre Match signal (on their respective dates).
            #   • Parasite (Drama/Thriller) and Get Out (Horror/Thriller) will score
            #     lower for Ayo but higher for Temi (Drama preference 0.8).
            #   • The Showtime Proximity signal differs per date — today's movies
            #     score based on today's clock, tomorrow's score lower (they're >12h away).
            # ------------------------------------------------------------------

            schedule = [
                # (title, genre, duration, rating, age_rating, director, cast, description, show_date, show_time, price)
                (
                    "Inception", "Sci-Fi,Thriller", 148, 8.8, "PG-13",
                    "Christopher Nolan", "Leonardo DiCaprio, Joseph Gordon-Levitt",
                    "A thief who steals corporate secrets through dream-sharing technology.",
                    today, "18:00", 3000.0
                ),
                (
                    "The Dark Knight", "Action,Thriller", 152, 9.0, "PG-13",
                    "Christopher Nolan", "Christian Bale, Heath Ledger",
                    "Batman faces the Joker, a criminal mastermind who plunges Gotham into chaos.",
                    today, "16:00", 2500.0
                ),
                (
                    "Parasite", "Drama,Thriller", 132, 8.5, "R",
                    "Bong Joon-ho", "Song Kang-ho, Lee Sun-kyun",
                    "A poor family schemes to become employed by a wealthy family.",
                    today, "14:00", 2000.0
                ),
                (
                    "Interstellar", "Sci-Fi,Drama", 169, 8.6, "PG",
                    "Christopher Nolan", "Matthew McConaughey, Anne Hathaway",
                    "A team travels through a wormhole in space in search of a new home.",
                    tomorrow, "18:00", 3000.0
                ),
                (
                    "Everything Everywhere All at Once", "Sci-Fi,Comedy,Drama", 139, 7.8, "R",
                    "The Daniels", "Michelle Yeoh, Ke Huy Quan",
                    "A laundromat owner must connect with parallel universe versions of herself.",
                    tomorrow, "16:00", 2500.0
                ),
                (
                    "Get Out", "Horror,Thriller", 104, 7.7, "R",
                    "Jordan Peele", "Daniel Kaluuya, Allison Williams",
                    "A young man uncovers a disturbing secret at his girlfriend's family home.",
                    tomorrow, "14:00", 2000.0
                ),
            ]

            seeded_movies = []
            seeded_shows  = []

            for row in schedule:
                (title, genre, duration, rating, age_rating,
                 director, cast_list, description,
                 show_date, show_time, price) = row

                movie = Movie(
                    title=title, genre=genre, duration=duration,
                    rating=rating, age_rating=age_rating,
                    director=director, cast_list=cast_list,
                    description=description
                )
                db.session.add(movie)
                db.session.flush()   # get movie.id before commit

                show = Showtime(
                    movie_id=movie.id, hall_id=hall.id,
                    show_date=show_date, time=show_time, price=price
                )
                db.session.add(show)
                db.session.flush()

                seeded_movies.append(movie)
                seeded_shows.append(show)

            db.session.commit()

            # ── 7. Seats — Chapter 3 quality formula for every showtime ───────
            #
            # row_score    = 1 - |r - mid_row| / total_rows
            # col_score    = 1 - |c - mid_col| / total_cols
            # quality_score = (row_score * 0.5 + col_score * 0.5) * 10
            #
            row_labels = ['A', 'B', 'C', 'D', 'E']
            total_rows = hall.rows       # 5
            total_cols = hall.columns    # 8
            mid_row    = (total_rows - 1) / 2.0   # 2.0
            mid_col    = (total_cols - 1) / 2.0   # 3.5

            for show in seeded_shows:
                for r_idx, label in enumerate(row_labels):
                    for c_idx in range(total_cols):
                        row_score = 1 - (abs(r_idx - mid_row) / total_rows)
                        col_score = 1 - (abs(c_idx - mid_col) / total_cols)
                        q_score   = round((row_score * 0.5 + col_score * 0.5) * 10, 2)
                        db.session.add(Seat(
                            showtime_id=show.id,
                            row_label=label,
                            col_number=c_idx + 1,
                            quality_score=q_score,
                            status="available"
                        ))

            db.session.commit()

            # ── 8. Seed Temi's past booking on Inception ───────────────────────
            # Required for the Jaccard Collaborative Filtering signal to activate.
            # When Ayo logs in, the engine finds Temi has booked Inception,
            # computes Jaccard similarity, and if > 0.1 gives Inception +10 points.
            inception_show = seeded_shows[0]   # Inception is index 0
            centre_seat = Seat.query.filter_by(
                showtime_id=inception_show.id, row_label='C', col_number=4
            ).first()
            if centre_seat:
                centre_seat.status = 'taken'
                db.session.add(Reservation(user_id=temi.id, seat_id=centre_seat.id))
                db.session.commit()

            # ── 9. Print verification guide ────────────────────────────────────
            print("\n" + "="*60)
            print("✅  DATABASE SEEDED SUCCESSFULLY")
            print("="*60)
            print(f"\n  TODAY    ({today}):")
            for s in seeded_shows[:3]:
                m = Movie.query.get(s.movie_id)
                print(f"    {s.time}  {m.title:<38} ₦{s.price:,.0f}")

            print(f"\n  TOMORROW ({tomorrow}):")
            for s in seeded_shows[3:]:
                m = Movie.query.get(s.movie_id)
                print(f"    {s.time}  {m.title:<38} ₦{s.price:,.0f}")

            print(f"\n  USERS:")
            print(f"    admin →  admin123       role:  ADMIN (can add/delete movies)")
            print(f"    Ayo   →  password123   prefs: Sci-Fi 0.6, Thriller 0.4")
            print(f"    Temi  →  temi456        prefs: Drama 0.8, Sci-Fi 0.2")
            print(f"\n  SEATS: {total_rows * total_cols} per showtime × {len(seeded_shows)} showtimes = {total_rows * total_cols * len(seeded_shows)} total")
            print(f"\n  VERIFY THE ENGINE:")
            print(f"    http://127.0.0.1:5000/api/debug/recommendations?user_id=1")
            print(f"    http://127.0.0.1:5000/api/debug/recommendations?user_id=1&date={tomorrow}")
            print(f"    http://127.0.0.1:5000/api/debug/recommendations?user_id=2")
            print("="*60 + "\n")

        except Exception as e:
            db.session.rollback()
            print(f"\n❌  ERROR: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    seed_database()