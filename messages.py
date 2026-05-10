"""
messages.py

Centralized protocol definitions for the Distributed Trivia System.
"""
from typing import Any, Dict

class MsgType:
    """Constants for all allowed network message types."""
    REGISTER = "register"
    RESOLVE = "resolve"
    JOIN = "JOIN"
    START = "START"
    QUESTION = "QUESTION"
    BUZZ = "BUZZ"
    WINNER = "WINNER"
    ANSWER = "ANSWER"
    RESULT = "RESULT"
    END = "END"

# ==========================================
# Message Builders (Factories)
# ==========================================

def make_register_msg(service: str, host: str, port: int) -> Dict[str, Any]:
    return {"type": MsgType.REGISTER, "service": service, "host": host, "port": port}

def make_resolve_msg(service: str) -> Dict[str, Any]:
    return {"type": MsgType.RESOLVE, "service": service}

def make_join(player_id: str) -> Dict[str, Any]:
    return {"type": MsgType.JOIN, "payload": {"player_id": player_id}}

def make_buzz(player_id: str, lamport_time: int, question_number: int) -> Dict[str, Any]:
    return {
        "type": MsgType.BUZZ,
        "payload": {
            "player_id": player_id,
            "lamport_time": lamport_time,
            "question_number": question_number
        }
    }

def make_answer(player_id: str, answer: str, lamport_time: int) -> Dict[str, Any]:
    return {
        "type": MsgType.ANSWER,
        "payload": {
            "player_id": player_id,
            "answer": answer,
            "lamport_time": lamport_time
        }
    }

def make_start(message: str) -> Dict[str, Any]:
    return {"type": MsgType.START, "payload": {"message": message}}

def make_question(q_num: int, question_text: str, lamport_time: int) -> Dict[str, Any]:
    return {
        "type": MsgType.QUESTION,
        "question_number": q_num,
        "payload": {
            "question": question_text,
            "timestamp": lamport_time
        }
    }

def make_winner(you_won: bool, lamport_time: int, winner_name: str = None) -> Dict[str, Any]:
    payload = {"you_won": you_won, "lamport_time": lamport_time}
    if winner_name:
        payload["winner"] = winner_name
    return {"type": MsgType.WINNER, "payload": payload}

def make_result(message: str, lamport_time: int) -> Dict[str, Any]:
    return {"type": MsgType.RESULT, "payload": {"message": message, "lamport_time": lamport_time}}

def make_end(message: str) -> Dict[str, Any]:
    return {"type": MsgType.END, "payload": {"message": message}}