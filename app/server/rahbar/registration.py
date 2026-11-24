"""Signup-specific validation, independent of existing login credentials."""

import re
import unicodedata

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator


def normalize_phone(value: str) -> str:
    compact = re.sub(r"[\s().-]", "", value.strip())
    if not re.fullmatch(r"\+[1-9][0-9]{6,14}", compact):
        raise ValueError("Use an international phone number including + and country code")
    return compact


class RegistrationCredentials(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)
    phone_number: str = Field(min_length=7, max_length=40)

    @field_validator("full_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("Name contains unsupported control characters")
        value = " ".join(unicodedata.normalize("NFC", value).split())
        if not value or not any(char.isalpha() for char in value):
            raise ValueError("Enter your full name")
        return value

    @field_validator("phone_number")
    @classmethod
    def valid_phone(cls, value: str) -> str:
        return normalize_phone(value)
