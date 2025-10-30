import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import ForeignKey
from datetime import datetime

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

class Admin(Base):
    __tablename__ = 'admins'
    
    id = Column(Integer, primary_key=True)
    channel_id = Column(String, ForeignKey('channel_settings.channel_id'))
    admin_id = Column(String, nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)

class ApprovalStats(Base):
    __tablename__ = 'approval_stats'
    
    id = Column(Integer, primary_key=True)
    channel_id = Column(String, nullable=False)
    date = Column(String, nullable=False)
    approvals_count = Column(Integer, default=0)

def get_engine():
    """Создание подключения к PostgreSQL"""
    database_url = os.getenv('DATABASE_URL')
    
    if database_url and database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    
    # Если нет DATABASE_URL, создаем в памяти (только для тестов)
    if not database_url:
        database_url = 'sqlite:///:memory:'
    
    engine = create_engine(database_url)
    return engine

def init_db():
    """Инициализация базы данных"""
    engine = get_engine()
    Base.metadata.create_all(engine)
    return engine

def get_session():
    """Получение сессии базы данных"""
    engine = init_db()
    Session = sessionmaker(bind=engine)
    return Session()
