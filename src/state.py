from enum import Enum
from typing import Dict
from src.models import Transaction

class UserState(str, Enum):
    IDLE = "IDLE"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_EDIT = "AWAITING_EDIT"

class ConversationState:
    """Almacena el estado conversacional de un usuario específico."""
    def __init__(self):
        self.state: UserState = UserState.IDLE
        self.pending_transaction: Transaction | None = None
        self.options: list[str] = []
        self.edit_transaction_id: str | None = None

# Diccionario global en memoria para guardar el estado por chat_id
# En un bot de producción masivo, esto iría a Redis o PostgreSQL.
user_sessions: Dict[int, ConversationState] = {}

def get_user_session(user_id: int) -> ConversationState:
    """Recupera o crea la sesión para un usuario específico."""
    if user_id not in user_sessions:
        user_sessions[user_id] = ConversationState()
    return user_sessions[user_id]
