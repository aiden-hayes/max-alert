"""Database tables (SQLite by default: data/maxalert.db).

    users         one row per account; password stored only as a salted hash
    logged_meals  a meal a user logged (date + meal period)
    logged_items  the dishes on that meal, with per-serving nutrition and servings

Tables are created automatically when the app starts (see app.py / `flask --app app init-db`).
"""
from __future__ import annotations

import datetime as dt

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

NUTRIENTS = ["calories", "protein_g", "carbs_g", "fat_g"]


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.timezone.utc))
    calorie_goal: Mapped[int | None]
    # True once we've offered to import the pre-accounts log (accepted or declined).
    import_offer_done: Mapped[bool] = mapped_column(default=False)

    meals: Mapped[list["LoggedMeal"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="LoggedMeal.id")

    def set_password(self, password: str) -> None:
        # werkzeug's default is scrypt with a random salt; the plain password is never stored.
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {"email": self.email, "goal": self.calorie_goal, "import_offer_done": self.import_offer_done}


class LoggedMeal(db.Model):
    __tablename__ = "logged_meals"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # campus date, YYYY-MM-DD
    meal: Mapped[str] = mapped_column(String(20))
    logged_at: Mapped[str] = mapped_column(String(40))          # ISO time with offset

    user: Mapped[User] = relationship(back_populates="meals")
    items: Mapped[list["LoggedItem"]] = relationship(
        back_populates="meal", cascade="all, delete-orphan", order_by="LoggedItem.position")

    def to_dict(self) -> dict:
        return {"id": str(self.id), "date": self.date, "meal": self.meal, "logged_at": self.logged_at,
                "items": [i.to_dict() for i in self.items]}


class LoggedItem(db.Model):
    __tablename__ = "logged_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    meal_id: Mapped[int] = mapped_column(ForeignKey("logged_meals.id"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(120))
    servings: Mapped[float] = mapped_column(default=1.0)
    source: Mapped[str] = mapped_column(String(10), default="menu")  # "menu" or "quick"
    station: Mapped[str] = mapped_column(String(60), default="")
    location: Mapped[str] = mapped_column(String(60), default="")
    portion: Mapped[str] = mapped_column(String(40), default="")
    # Per serving. None = unknown (never stored as 0).
    calories: Mapped[float | None]
    protein_g: Mapped[float | None]
    carbs_g: Mapped[float | None]
    fat_g: Mapped[float | None]

    meal: Mapped[LoggedMeal] = relationship(back_populates="items")

    def to_dict(self) -> dict:
        def num(v):
            return None if v is None else (int(v) if float(v).is_integer() else v)
        d = {"name": self.name, "servings": num(self.servings), "source": self.source,
             "station": self.station, "location": self.location, "portion": self.portion}
        d.update({k: num(getattr(self, k)) for k in NUTRIENTS})
        return d
