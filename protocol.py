import struct
from dataclasses import dataclass

MAGIC_COOKIE = 0xabcddcba

MSG_TYPE_OFFER = 0x2
MSG_TYPE_REQUEST = 0x3
MSG_TYPE_PAYLOAD = 0x4

UDP_OFFER_PORT = 13122
TEAM_NAME_LEN = 32

# Offer: cookie(4) + type(1) + tcp_port(2) + server_name(32) = 39
OFFER_STRUCT = struct.Struct("!IBH32s")

# Request: cookie(4) + type(1) + rounds(1) + client_name(32) = 38
REQUEST_STRUCT = struct.Struct("!IBB32s")

# Payload (client -> server): cookie(4) + type(1) + decision(5) = 10
PAYLOAD_C2S_STRUCT = struct.Struct("!IB5s")

# Payload (server -> client): cookie(4) + type(1) + result(1) + rank(2) + suit(1) = 9
# rank is 1..13 in 2 bytes, suit is 0..3 in 1 byte
PAYLOAD_S2C_STRUCT = struct.Struct("!IBBHB")

# Result codes (server -> client)
RESULT_NOT_OVER = 0x0
RESULT_TIE = 0x1
RESULT_LOSS = 0x2
RESULT_WIN = 0x3

# Decisions (client -> server), must be exactly 5 bytes
DECISION_HIT = b"Hittt"
DECISION_STAND = b"Stand"


@dataclass
class Offer:
    tcp_port: int
    server_name: str


@dataclass
class Request:
    rounds: int
    client_name: str


@dataclass
class ServerPayload:
    result: int
    rank: int   # 1..13
    suit: int   # 0..3


def _encode_name(name: str) -> bytes:
    b = name.encode("utf-8", errors="ignore")
    b = b[:TEAM_NAME_LEN]
    return b.ljust(TEAM_NAME_LEN, b"\x00")


def _decode_name(raw32: bytes) -> str:
    return raw32.split(b"\x00", 1)[0].decode("utf-8", errors="ignore")


def pack_offer(tcp_port: int, server_name: str) -> bytes:
    if not (0 <= tcp_port <= 65535):
        raise ValueError("tcp_port must be 0..65535")
    return OFFER_STRUCT.pack(MAGIC_COOKIE, MSG_TYPE_OFFER, tcp_port, _encode_name(server_name))


def unpack_offer(data: bytes) -> Offer | None:
    if len(data) != OFFER_STRUCT.size:
        return None
    cookie, msg_type, tcp_port, raw_name = OFFER_STRUCT.unpack(data)
    if cookie != MAGIC_COOKIE or msg_type != MSG_TYPE_OFFER:
        return None
    return Offer(tcp_port=tcp_port, server_name=_decode_name(raw_name))


def pack_request(rounds: int, client_name: str) -> bytes:
    if not (1 <= rounds <= 255):
        raise ValueError("rounds must be 1..255")
    return REQUEST_STRUCT.pack(MAGIC_COOKIE, MSG_TYPE_REQUEST, rounds, _encode_name(client_name))


def unpack_request(data: bytes) -> Request | None:
    if len(data) != REQUEST_STRUCT.size:
        return None
    cookie, msg_type, rounds, raw_name = REQUEST_STRUCT.unpack(data)
    if cookie != MAGIC_COOKIE or msg_type != MSG_TYPE_REQUEST:
        return None
    return Request(rounds=rounds, client_name=_decode_name(raw_name))


def pack_payload_decision(decision5: bytes) -> bytes:
    if len(decision5) != 5:
        raise ValueError("decision must be exactly 5 bytes")
    return PAYLOAD_C2S_STRUCT.pack(MAGIC_COOKIE, MSG_TYPE_PAYLOAD, decision5)


def unpack_payload_decision(data: bytes) -> bytes | None:
    if len(data) != PAYLOAD_C2S_STRUCT.size:
        return None
    cookie, msg_type, decision5 = PAYLOAD_C2S_STRUCT.unpack(data)
    if cookie != MAGIC_COOKIE or msg_type != MSG_TYPE_PAYLOAD:
        return None
    return decision5


def pack_payload_card(result: int, rank: int, suit: int) -> bytes:
    if not (0 <= result <= 3):
        raise ValueError("result must be 0..3")
    if not (1 <= rank <= 13):
        raise ValueError("rank must be 1..13")
    if not (0 <= suit <= 3):
        raise ValueError("suit must be 0..3")
    return PAYLOAD_S2C_STRUCT.pack(MAGIC_COOKIE, MSG_TYPE_PAYLOAD, result, rank, suit)


def unpack_payload_card(data: bytes) -> ServerPayload | None:
    if len(data) != PAYLOAD_S2C_STRUCT.size:
        return None
    cookie, msg_type, result, rank, suit = PAYLOAD_S2C_STRUCT.unpack(data)
    if cookie != MAGIC_COOKIE or msg_type != MSG_TYPE_PAYLOAD:
        return None
    if not (0 <= result <= 3 and 1 <= rank <= 13 and 0 <= suit <= 3):
        return None
    return ServerPayload(result=result, rank=rank, suit=suit)


def card_value(rank: int) -> int:
    # As required: Ace is ALWAYS 11, no "1 or 11" logic.
    if rank == 1:
        return 11
    if 2 <= rank <= 10:
        return rank
    return 10
