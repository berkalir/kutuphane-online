from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Kullanici(Base):
    __tablename__= 'kullanicilar'
    id = Column(Integer, primary_key=True)
    isim = Column(String)
    email = Column(String, unique=True)
    sifre = Column(String)
    