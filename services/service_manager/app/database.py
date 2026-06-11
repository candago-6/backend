import os
import time
from sqlmodel import create_engine, SQLModel, Session
from sqlalchemy.exc import OperationalError
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:admin123@db:5432/assistente_whatsapp")

engine = create_engine(DATABASE_URL, echo=True)


def create_db_and_tables():
    from .models.entities import User, Conversation, Message, Feedback, MessageEvaluation  # Import to ensure they are registered
    from .models.admin import AdminUser  # Import to ensure it is registered

    max_retries = 5
    retry_interval = 5
    
    for i in range(max_retries):
        try:
            SQLModel.metadata.create_all(engine)
            print("Database tables created successfully!")
            break
        except OperationalError as e:
            if i < max_retries - 1:
                print(f"Database not ready (attempt {i+1}/{max_retries}). Retrying in {retry_interval}s...")
                time.sleep(retry_interval)
            else:
                print("Max retries reached. Could not connect to the database.")
                raise e


def get_session():
    with Session(engine) as session:
        yield session
