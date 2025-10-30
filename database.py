from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import ForeignKey
from datetime import datetime
import os

Base = declarative_base()

class ChannelSettings(Base):
    __tablename__ = 'channel_settings'
    
    id = Column(Integer, primary_key=True)
    channel_id = Column(String, unique=True, nullable=False)
    channel_username = Column(String, nullable=False)
    owner_id = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    auto_approve = Column(Boolean, default=True)
    max_daily_approvals = Column(Integer, default=1000)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    admins = relationship("Admin", back_populates="channel")

class Admin(Base):
    __tablename__ = 'admins'
    
    id = Column(Integer, primary_key=True)
    channel_id = Column(String, ForeignKey('channel_settings.channel_id'))
    admin_id = Column(String, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)
    
    channel = relationship("ChannelSettings", back_populates="admins")

class ApprovalStats(Base):
    __tablename__ = 'approval_stats'
    
    id = Column(Integer, primary_key=True)
    channel_id = Column(String, nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD
    approvals_count = Column(Integer, default=0)

# Инициализация базы данных
def init_db():
    database_url = os.getenv('DATABASE_URL', 'sqlite:///bot_data.db')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return engine

def get_session():
    engine = init_db()
    Session = sessionmaker(bind=engine)
    return Session()