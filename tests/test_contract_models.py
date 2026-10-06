import pytest
from pydantic import ValidationError

from backend.contracts.models import ConversationCreate, ConversationType


def test_direct_needs_exactly_one_participant():
    ConversationCreate(type=ConversationType.DIRECT, participant_ids=["a"])
    with pytest.raises(ValidationError):
        ConversationCreate(type=ConversationType.DIRECT, participant_ids=["a", "b"])


def test_group_needs_two_participants_and_topic():
    ConversationCreate(type=ConversationType.GROUP, participant_ids=["a", "b"], topic="t")
    with pytest.raises(ValidationError):
        ConversationCreate(type=ConversationType.GROUP, participant_ids=["a"], topic="t")
    with pytest.raises(ValidationError):
        ConversationCreate(type=ConversationType.GROUP, participant_ids=["a", "b"], topic="  ")


def test_group_has_no_participant_cap():
    ids = [f"a{i}" for i in range(25)]
    ConversationCreate(type=ConversationType.GROUP, participant_ids=ids, topic="t")


def test_duplicate_participants_rejected():
    with pytest.raises(ValidationError):
        ConversationCreate(type=ConversationType.GROUP, participant_ids=["a", "a"], topic="t")
