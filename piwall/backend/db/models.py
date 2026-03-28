"""SQLAlchemy models for PIT WALL persistent data."""

import datetime
import uuid

from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Text, JSON, Boolean,
    ForeignKey, Index, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


def generate_uuid():
    return str(uuid.uuid4())


class Player(Base):
    __tablename__ = "players"

    id = Column(String, primary_key=True, default=generate_uuid)
    username = Column(String(64), unique=True, nullable=False, index=True)
    api_key = Column(String(128), unique=True, nullable=False, index=True)
    elo = Column(Float, default=1200.0)
    team_name = Column(String(64), default="Independent")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    race_results = relationship("RaceResultRow", back_populates="player")
    bot_submissions = relationship("BotSubmission", back_populates="player")
    elo_history = relationship("EloHistory", back_populates="player")


class Season(Base):
    __tablename__ = "seasons"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String(128), nullable=False)
    start_date = Column(DateTime, default=datetime.datetime.utcnow)
    end_date = Column(DateTime, nullable=True)
    track_rotation = Column(JSON, default=list)  # ["bahrain", "monaco", ...]
    active = Column(Boolean, default=True)

    races = relationship("Race", back_populates="season")


class Race(Base):
    __tablename__ = "races"

    id = Column(String, primary_key=True, default=generate_uuid)
    season_id = Column(String, ForeignKey("seasons.id"), nullable=True)
    track = Column(String(64), nullable=False)
    race_type = Column(String(32), default="quick")  # "quick" or "season"
    status = Column(String(32), default="lobby")  # lobby/countdown/running/finished
    weather_seed = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    lap_data_json = Column(JSON, nullable=True)
    events_json = Column(JSON, nullable=True)

    season = relationship("Season", back_populates="races")
    results = relationship("RaceResultRow", back_populates="race")
    submissions = relationship("BotSubmission", back_populates="race")

    __table_args__ = (Index("ix_races_status", "status"),)


class RaceResultRow(Base):
    __tablename__ = "race_results"

    id = Column(String, primary_key=True, default=generate_uuid)
    race_id = Column(String, ForeignKey("races.id"), nullable=False)
    player_id = Column(String, ForeignKey("players.id"), nullable=False)
    car_id = Column(String(32), nullable=False)
    position = Column(Integer, nullable=False)
    points = Column(Integer, default=0)
    total_time = Column(Float, nullable=True)
    pit_laps = Column(JSON, default=list)
    compounds_used = Column(JSON, default=list)
    strategy_json = Column(JSON, nullable=True)
    retired = Column(Boolean, default=False)

    race = relationship("Race", back_populates="results")
    player = relationship("Player", back_populates="race_results")

    __table_args__ = (Index("ix_rr_race_player", "race_id", "player_id"),)


class BotSubmission(Base):
    __tablename__ = "bot_submissions"

    id = Column(String, primary_key=True, default=generate_uuid)
    player_id = Column(String, ForeignKey("players.id"), nullable=False)
    race_id = Column(String, ForeignKey("races.id"), nullable=True)
    code = Column(Text, nullable=False)
    code_hash = Column(String(64), nullable=False)
    submitted_at = Column(DateTime, default=datetime.datetime.utcnow)

    player = relationship("Player", back_populates="bot_submissions")
    race = relationship("Race", back_populates="submissions")

    __table_args__ = (Index("ix_bs_player", "player_id"),)


class EloHistory(Base):
    __tablename__ = "elo_history"

    id = Column(String, primary_key=True, default=generate_uuid)
    player_id = Column(String, ForeignKey("players.id"), nullable=False)
    race_id = Column(String, ForeignKey("races.id"), nullable=False)
    elo_before = Column(Float, nullable=False)
    elo_after = Column(Float, nullable=False)
    delta = Column(Float, nullable=False)

    player = relationship("Player", back_populates="elo_history")

    __table_args__ = (Index("ix_eh_player", "player_id"),)


# Database setup helper
def create_db_engine(url: str = "sqlite:///piwall.db"):
    engine = create_engine(url, echo=False)
    return engine


def init_db(engine):
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
