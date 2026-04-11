from app import app, db

with app.app_context():
    print("Clearing old database tables...")
    db.drop_all()   # Drops ALL tables including UserPreference, Reservation, etc.

    print("Creating new tables from app.py models...")
    db.create_all() # Recreates every model registered with SQLAlchemy

    print("✅ Database structure is ready! Now run: python seed.py")