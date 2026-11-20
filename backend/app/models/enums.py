from enum import Enum


class TripStatus(str, Enum):
    DRAFT = "DRAFT"
    PLANNING = "PLANNING"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class OnboardingStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class ConversationSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class ConversationStage(str, Enum):
    TRIP_BASICS = "TRIP_BASICS"
    ORIGIN = "ORIGIN"
    REVIEW = "REVIEW"
    COMPLETE = "COMPLETE"


class MessageRole(str, Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class MessageType(str, Enum):
    TEXT = "TEXT"
    UI_ACTION = "UI_ACTION"
