from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class ResearchRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="A business question, at least 10 characters.",
    )


class StartResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    status: str
    stage: str
    sections: Dict[str, str]
    report: Optional[str] = None
    error: Optional[str] = None


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str


class HistoryItem(BaseModel):
    id: str
    question: str
    title: Optional[str] = None
    favorite: bool = False
    created_at: str


class MessageItem(BaseModel):
    role: str
    content: str


class JobDetailResponse(BaseModel):
    id: str
    question: str
    title: Optional[str] = None
    favorite: bool = False
    report: Optional[str] = None
    sections: Dict[str, str]
    messages: List[MessageItem]


class FollowUpRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)


class FollowUpResponse(BaseModel):
    reply: str


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class FavoriteRequest(BaseModel):
    favorite: bool
