"""Registration validation and backward-compatible account response tests."""

import pytest
from pydantic import ValidationError
from rahbar.contracts.http import Account
from rahbar.registration import RegistrationCredentials, normalize_phone


def test_registration_requires_profile_without_changing_old_account_responses():
    with pytest.raises(ValidationError):
        RegistrationCredentials(email="student@example.com", password="Good!123")
    account = Account(account_id="student", email="student@example.com")
    assert account.full_name is None and account.phone_number is None


@pytest.mark.parametrize("value", ["Arfa Nasir", "عارفہ ناصر", "李", "O'Connor"])
def test_names_accept_international_names(value):
    profile = RegistrationCredentials(
        full_name=value,
        email="student@example.com",
        password="Good!123",
        phone_number="+92 300-1234567",
    )
    assert profile.full_name == value
    assert profile.phone_number == "+923001234567"


@pytest.mark.parametrize("value", ["", "  ", "12345", "Arfa\x00Nasir", "a" * 121])
def test_invalid_names(value):
    with pytest.raises(ValidationError):
        RegistrationCredentials(
            full_name=value,
            email="student@example.com",
            password="Good!123",
            phone_number="+923001234567",
        )


@pytest.mark.parametrize(
    "value",
    [
        "03001234567",
        "923001234567",
        "+01234",
        "+92abc3001234567",
        "+" + "9" * 16,
        "+92300; DROP TABLE accounts",
    ],
)
def test_invalid_phones(value):
    with pytest.raises(ValueError):
        normalize_phone(value)
