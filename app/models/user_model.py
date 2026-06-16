"""
User data models and schemas
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """Schema for user creation"""

    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6)


class UserUpdate(BaseModel):
    """Schema for user updates"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)


class UserResponse(BaseModel):
    """Schema for user response"""

    id: str
    name: str
    email: str
    role: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserInDB(BaseModel):
    """Schema for user in database"""

    id: str
    name: str
    email: str
    role: str
    created_at: datetime
    updated_at: datetime


class LoginRequest(BaseModel):
    """Schema for login request"""

    email: EmailStr
    password: str = Field(..., min_length=6)


class LoginResponse(BaseModel):
    """Schema for login response"""

    user_id: str
    email: str
    name: str
    role: str


class CurrentUser(BaseModel):
    """Schema for current authenticated user"""

    user_id: str
    email: str
    role: str
    name: str
