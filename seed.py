import os
from datetime import date

# BUG FIX 1: Import all models that exist in app.py
# UserPreference and Reservation are both needed
from app import app, db, User, UserPreference, Movie, Cinema, Hall, Showtime, Seat, Reservation

def seed_database():
    with app.app_context():
        try:
            print("Starting seed process...")

            # 1. Clear existing data in correct dependency order
            # BUG FIX 2: UserPreference must be cleared before User (foreign key constraint)
            db.session.query(Reservation).delete()
            db.session.query(UserPreference).delete()
            db.session.query(Seat).delete()
            db.session.query(Showtime).delete()
            db.session.query(Hall).delete()
            db.session.query(Movie).delete()
            db.session.query(Cinema).delete()
            db.session.query(User).delete()
            db.session.commit()

            # ------------------------------------------------------------------
            # 2. Create Users
            # Chapter 3 requires PBKDF2-HMAC-SHA256 hashed passwords.
            # The hash_password() helper must be imported from app.py.
            # ------------------------------------------------------------------
            from app import hash_password

            ayo = User(
                username="Ayo",
                email="ayo@example.com",
                password=hash_password("password123")
            )
            db.session.add(ayo)

            # Seed a second user so Jaccard collaborative filtering has data to work with
            temi = User(
                username="Temi",
                email="temi@example.com",
                password=hash_password("temi456")
            )
            db.session.add(temi)
            db.session.commit()

            # ------------------------------------------------------------------
            # 3. Seed UserPreference records (P5 — Preference Learning)
            # Chapter 3: preferences stored as a JSON dict, scores 0.0–1.0
            # Empty dict for new users (cold-start handling)
            # ------------------------------------------------------------------
            import json

            ayo_pref = UserPreference(
                user_id=ayo.id,
                preferences=json.dumps({"Sci-Fi": 0.6, "Thriller": 0.4})
            )
            temi_pref = UserPreference(
                user_id=temi.id,
                preferences=json.dumps({"Drama": 0.8, "Sci-Fi": 0.2})
            )
            db.session.add_all([ayo_pref, temi_pref])
            db.session.commit()

            # ------------------------------------------------------------------
            # 4. Create Cinema
            # ------------------------------------------------------------------
            cinema = Cinema(name="Ayo's Grand Theatre", address="123 AI Avenue, Lagos")
            db.session.add(cinema)
            db.session.commit()

            # ------------------------------------------------------------------
            # 5. Create Movies
            # BUG FIX 3: Only ONE movie was seeded before.
            # The recommendation engine needs multiple candidate movies to rank,
            # otherwise there is nothing to compare and the recommender is pointless.
            # Chapter 3 also requires a 'rating' field used in the Rating Bonus signal.
            # ------------------------------------------------------------------
            movies_data = [
                {
                    "title": "Inception",
                    "genre": "Sci-Fi,Thriller",
                    "duration": 148,
                    "rating": 8.8,
                    "age_rating": "PG-13",
                    "director": "Christopher Nolan",
                    "cast_list": "Leonardo DiCaprio, Joseph Gordon-Levitt",
                    "description": "A thief who steals corporate secrets through dream-sharing technology."
                },
                {
                    "title": "Interstellar",
                    "genre": "Sci-Fi,Drama",
                    "duration": 169,
                    "rating": 8.6,
                    "age_rating": "PG",
                    "director": "Christopher Nolan",
                    "cast_list": "Matthew McConaughey, Anne Hathaway",
                    "description": "A team travels through a wormhole in space in search of a new home."
                },
                {
                    "title": "The Dark Knight",
                    "genre": "Action,Thriller",
                    "duration": 152,
                    "rating": 9.0,
                    "age_rating": "PG-13",
                    "director": "Christopher Nolan",
                    "cast_list": "Christian Bale, Heath Ledger",
                    "description": "Batman faces the Joker, a criminal mastermind who plunges Gotham into chaos."
                },
                {
                    "title": "Parasite",
                    "genre": "Drama,Thriller",
                    "duration": 132,
                    "rating": 8.5,
                    "age_rating": "R",
                    "director": "Bong Joon-ho",
                    "cast_list": "Song Kang-ho, Lee Sun-kyun",
                    "description": "A poor family schemes to become employed by a wealthy family."
                },
                {
                    "title": "Everything Everywhere All at Once",
                    "genre": "Sci-Fi,Comedy,Drama",
                    "duration": 139,
                    "rating": 7.8,
                    "age_rating": "R",
                    "director": "The Daniels",
                    "cast_list": "Michelle Yeoh, Ke Huy Quan",
                    "description": "A middle-aged laundromat owner must connect with parallel universe versions of herself."
                },
                {
                    "title": "Get Out",
                    "genre": "Horror,Thriller",
                    "duration": 104,
                    "rating": 7.7,
                    "age_rating": "R",
                    "director": "Jordan Peele",
                    "cast_list": "Daniel Kaluuya, Allison Williams",
                    "description": "A young Black man uncovers a disturbing secret when visiting his girlfriend's family."
                },
            ]

            seeded_movies = []
            for md in movies_data:
                m = Movie(**md)
                db.session.add(m)
                seeded_movies.append(m)
            db.session.commit()

            # ------------------------------------------------------------------
            # 6. Create Hall
            # ------------------------------------------------------------------
            hall = Hall(cinema_id=cinema.id, name="Hall 1", rows=5, columns=8)
            db.session.add(hall)
            db.session.commit()

            # ------------------------------------------------------------------
            # 7. Create Showtimes — one per movie, staggered times today
            # The Showtime Proximity signal in Chapter 3 needs real time values
            # so the engine can compute: max(0, 1 - diff / 12) × 10
            # ------------------------------------------------------------------
            showtimes_schedule = [
                ("10:00", 2000.0),
                ("12:30", 2000.0),
                ("14:00", 2500.0),
                ("16:00", 2500.0),
                ("18:00", 3000.0),
                ("20:30", 3000.0),
            ]

            seeded_shows = []
            for movie, (show_time, price) in zip(seeded_movies, showtimes_schedule):
                show = Showtime(
                    movie_id=movie.id,
                    hall_id=hall.id,
                    show_date=date.today(),
                    time=show_time,
                    price=price
                )
                db.session.add(show)
                seeded_shows.append(show)
            db.session.commit()

            # ------------------------------------------------------------------
            # 8. Create Seats using the EXACT formula from Chapter 3, Section 3.4.4
            #
            # Formula:
            #   row_score = 1 - |r - mid_row| / total_rows
            #   col_score = 1 - |c - mid_col| / total_cols
            #   quality_score = (row_score * 0.5 + col_score * 0.5) * 10
            #
            # Where r and c are ZERO-INDEXED positions.
            # mid_row = (total_rows - 1) / 2
            # mid_col = (total_cols - 1) / 2
            #
            # BUG FIX 4 (your seed.py): The denominators in the formula must be
            # total_rows and total_cols (5 and 8), NOT the midpoint values.
            # Your original code used /5 and /8 which happened to be correct here,
            # but the midpoint calculation was commented incorrectly.
            # More importantly: mid_row should be (5-1)/2 = 2.0 ✓
            # and mid_col should be (8-1)/2 = 3.5 ✓  — these were actually correct.
            # Seats are created for EVERY showtime so each movie has its own seat map.
            # ------------------------------------------------------------------
            row_labels = ['A', 'B', 'C', 'D', 'E']
            total_rows = hall.rows    # 5
            total_cols = hall.columns  # 8
            mid_row = (total_rows - 1) / 2.0   # 2.0
            mid_col = (total_cols - 1) / 2.0   # 3.5

            for show in seeded_shows:
                for r_idx, label in enumerate(row_labels):
                    for c_idx in range(total_cols):
                        row_score = 1 - (abs(r_idx - mid_row) / total_rows)
                        col_score = 1 - (abs(c_idx - mid_col) / total_cols)
                        q_score = round((row_score * 0.5 + col_score * 0.5) * 10, 2)

                        new_seat = Seat(
                            showtime_id=show.id,
                            row_label=label,
                            col_number=c_idx + 1,   # 1-indexed for display
                            quality_score=q_score,
                            status="available"
                        )
                        db.session.add(new_seat)

            db.session.commit()

            # ------------------------------------------------------------------
            # 9. Seed a past booking for Temi on Inception
            # This gives the Jaccard collaborative filtering signal real data:
            # if Ayo also books Inception, and Temi has booked it, the similarity
            # coefficient > 0 and the collaborative boost can activate.
            # ------------------------------------------------------------------
            inception_show = seeded_shows[0]
            first_seat = Seat.query.filter_by(
                showtime_id=inception_show.id, row_label='C', col_number=4
            ).first()

            if first_seat:
                first_seat.status = 'taken'
                booking = Reservation(user_id=temi.id, seat_id=first_seat.id)
                db.session.add(booking)
                db.session.commit()

            print("✅ SUCCESS: Database seeded!")
            print("   Login with → Ayo / password123")
            print("   Login with → Temi / temi456")
            print(f"   {len(seeded_movies)} movies, {len(seeded_shows)} showtimes, "
                  f"{total_rows * total_cols * len(seeded_shows)} seats created.")

        except Exception as e:
            db.session.rollback()
            print(f"❌ ERROR DURING SEED: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    seed_database()