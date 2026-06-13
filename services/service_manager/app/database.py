import os
import time
from sqlmodel import create_engine, SQLModel, Session, text
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
            
            # Auto-migration for advanced conversational management
            with Session(engine) as session:
                session.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS whatsapp_id VARCHAR'))
                session.execute(text('CREATE INDEX IF NOT EXISTS ix_user_whatsapp_id ON "user" (whatsapp_id)'))
                session.execute(text("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS failed_attempts INTEGER DEFAULT 0"))
                session.execute(text("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS patience_msg_sent BOOLEAN DEFAULT FALSE"))
                session.execute(text("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS is_onboarded BOOLEAN DEFAULT FALSE"))
                session.execute(text("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP"))
                session.commit()
            
            print("Database tables created and migrated successfully!")
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
