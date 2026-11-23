"""amrooms: rooms for AirplaneMode multiplayer apps (platform repo, sdk/rooms). See README.md."""
from .identity import whois
from .rooms import CODE_ALPHABET, Room, RoomError, Rooms, Seat, clean_name, env_float

__version__ = "0.1.1"
__all__ = ["CODE_ALPHABET", "Room", "RoomError", "Rooms", "Seat", "clean_name", "env_float", "whois"]
