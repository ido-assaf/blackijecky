# server.py
import socket
import time
import random
import threading
import traceback
import subprocess
import re
import ipaddress

from protocol import (
    UDP_OFFER_PORT, pack_offer,
    unpack_request,
    pack_payload_card, unpack_payload_decision,
    RESULT_NOT_OVER, RESULT_WIN, RESULT_LOSS, RESULT_TIE,
    DECISION_HIT, DECISION_STAND,
    card_value,
    PAYLOAD_C2S_STRUCT,  # 10 bytes
)

TEAM_NAME = "Blackijecky - server"

# Suit encoding: 0=Heart, 1=Diamond, 2=Club, 3=Spade
SUITS = [0, 1, 2, 3]
RANKS = list(range(1, 14))  # 1..13

# Timeouts (seconds)
REQUEST_TIMEOUT = 20.0
PLAYER_DECISION_TIMEOUT = 120.0


def get_default_route_ip() -> str:
    """Pick the local IPv4 used for outbound traffic (default route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't actually send, just selects route/interface
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()


def get_wifi_ipv4_and_mask_from_ipconfig() -> tuple[str, str] | None:
    """
    Windows-only: parse `ipconfig` and try to find Wi-Fi adapter IPv4 + Subnet Mask.
    Works with English/Hebrew output.
    """
    try:
        out = subprocess.check_output(["ipconfig"], text=True, encoding="utf-8", errors="ignore")
    except Exception:
        return None

    # Split to adapter blocks
    blocks = re.split(r"\r?\n\r?\n", out)
    for b in blocks:
        # Look for Wi-Fi adapter block (English or Hebrew usually still contains "Wi-Fi")
        if "Wi-Fi" not in b and "Wireless LAN adapter" not in b and "אלחוט" not in b:
            continue

        ipv4 = None
        mask = None

        # IPv4 line patterns (English/Hebrew)
        m_ip = re.search(r"IPv4 Address[^:]*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", b)
        if not m_ip:
            m_ip = re.search(r"כתובת IPv4[^:]*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", b)
        if m_ip:
            ipv4 = m_ip.group(1).strip()

        m_mask = re.search(r"Subnet Mask[^:]*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", b)
        if not m_mask:
            m_mask = re.search(r"מסכת רשת משנה[^:]*:\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", b)
        if m_mask:
            mask = m_mask.group(1).strip()

        if ipv4 and mask:
            return ipv4, mask

    return None


def compute_directed_broadcast(ip_str: str, mask_str: str) -> str | None:
    try:
        net = ipaddress.IPv4Network((ip_str, mask_str), strict=False)
        return str(net.broadcast_address)
    except Exception:
        return None


def build_deck():
    deck = [(r, s) for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def hand_sum(hand):
    return sum(card_value(r) for r, _ in hand)


def recv_exact(conn: socket.socket, n: int) -> bytes | None:
    """
    Read exactly n bytes from TCP.
    Return None on disconnect OR timeout OR OS error.
    """
    buf = b""
    while len(buf) < n:
        try:
            chunk = conn.recv(n - len(buf))
        except (socket.timeout, ConnectionResetError, ConnectionAbortedError, OSError):
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def decide_result(player_sum: int, dealer_sum: int) -> int:
    if player_sum > 21:
        return RESULT_LOSS
    if dealer_sum > 21:
        return RESULT_WIN
    if player_sum > dealer_sum:
        return RESULT_WIN
    if player_sum < dealer_sum:
        return RESULT_LOSS
    return RESULT_TIE


def play_one_round(conn: socket.socket) -> bool:
    """
    Play a single round. Returns True if finished normally,
    False if client disconnected/timeout while playing.
    """
    deck = build_deck()
    player = []
    dealer = []

    # Initial deal
    player.append(deck.pop())
    player.append(deck.pop())
    dealer.append(deck.pop())  # upcard
    dealer.append(deck.pop())  # hidden

    p_sum = hand_sum(player)

    # Send player's 2 cards + dealer upcard
    try:
        for r, s in player:
            conn.sendall(pack_payload_card(RESULT_NOT_OVER, r, s))

        d1r, d1s = dealer[0]
        conn.sendall(pack_payload_card(RESULT_NOT_OVER, d1r, d1s))
    except OSError:
        return False

    # Player turn
    conn.settimeout(PLAYER_DECISION_TIMEOUT)
    c2s_size = PAYLOAD_C2S_STRUCT.size  # 10 bytes

    while True:
        raw = recv_exact(conn, c2s_size)
        if raw is None:
            return False

        decision = unpack_payload_decision(raw)
        if decision not in (DECISION_HIT, DECISION_STAND):
            continue

        if decision == DECISION_HIT:
            card = deck.pop()
            player.append(card)
            p_sum = hand_sum(player)

            r, s = card
            try:
                if p_sum > 21:
                    conn.sendall(pack_payload_card(RESULT_LOSS, r, s))
                    return True
                else:
                    conn.sendall(pack_payload_card(RESULT_NOT_OVER, r, s))
            except OSError:
                return False
            continue

        # STAND
        break

    # Dealer turn: reveal hidden
    hidden = dealer[1]
    dealer_sum = hand_sum(dealer)

    hr, hs = hidden
    try:
        if dealer_sum >= 17:
            result = decide_result(p_sum, dealer_sum)
            conn.sendall(pack_payload_card(result, hr, hs))
            return True
        else:
            conn.sendall(pack_payload_card(RESULT_NOT_OVER, hr, hs))
    except OSError:
        return False

    # Dealer draws until >=17 or bust
    while True:
        card = deck.pop()
        dealer.append(card)
        dealer_sum = hand_sum(dealer)

        r, s = card
        try:
            if dealer_sum > 21:
                conn.sendall(pack_payload_card(RESULT_WIN, r, s))
                return True

            if dealer_sum >= 17:
                result = decide_result(p_sum, dealer_sum)
                conn.sendall(pack_payload_card(result, r, s))
                return True

            conn.sendall(pack_payload_card(RESULT_NOT_OVER, r, s))
        except OSError:
            return False


def parse_request_binary_or_text(conn: socket.socket) -> tuple[int, str] | None:
    """
    Prefer binary request (38 bytes). If not valid, fallback to text: b"3\\n".
    Returns (rounds, client_name) or None if invalid.
    """
    conn.settimeout(REQUEST_TIMEOUT)

    first = recv_exact(conn, 38)
    if first is None:
        return None

    req = unpack_request(first)
    if req is not None:
        return req.rounds, req.client_name

    # Text fallback
    try:
        text = first.decode("utf-8", errors="ignore")
        while "\n" not in text and len(text) < 128:
            more = conn.recv(32)
            if not more:
                break
            text += more.decode("utf-8", errors="ignore")

        rounds_str = text.strip().split()[0]
        rounds = int(rounds_str)
        if not (1 <= rounds <= 255):
            return None

        return rounds, "UnknownTextClient"
    except Exception:
        return None


def handle_client(conn: socket.socket, addr):
    try:
        parsed = parse_request_binary_or_text(conn)
        if parsed is None:
            return

        rounds, client_name = parsed

        for _ in range(rounds):
            ok = play_one_round(conn)
            if not ok:
                return

    except Exception:
        print("[SERVER] Unexpected error in client thread:")
        print(traceback.format_exc())
    finally:
        try:
            conn.close()
        except Exception:
            pass


def offer_broadcaster(stop_event: threading.Event, udp_sock: socket.socket, offer_bytes: bytes, directed_bcast: str | None):
    targets = [("<broadcast>", UDP_OFFER_PORT)]
    if directed_bcast:
        targets.append((directed_bcast, UDP_OFFER_PORT))

    while not stop_event.is_set():
        for target in targets:
            try:
                udp_sock.sendto(offer_bytes, target)
            except OSError:
                pass
        time.sleep(1)


def main():
    # TCP listener
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_sock.bind(("", 0))  # OS chooses port
    tcp_sock.listen()
    tcp_port = tcp_sock.getsockname()[1]

    # Choose correct interface (avoid VirtualBox)
    bind_ip = get_default_route_ip()

    # Compute directed broadcast for current Wi-Fi/subnet (best-effort)
    wifi_info = get_wifi_ipv4_and_mask_from_ipconfig()
    directed_bcast = None
    if wifi_info:
        wifi_ip, wifi_mask = wifi_info
        directed_bcast = compute_directed_broadcast(wifi_ip, wifi_mask)
        # Prefer Wi-Fi IP if it exists (more accurate than default-route sometimes)
        bind_ip = wifi_ip

    print(f"Server started, listening on IP address {bind_ip}, TCP port {tcp_port}")
    if directed_bcast:
        print(f"Broadcast targets: <broadcast> and {directed_bcast}")
    else:
        print("Broadcast targets: <broadcast> (no directed broadcast found)")

    # UDP broadcaster socket (bind to chosen interface!)
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.bind((bind_ip, 0))

    offer_bytes = pack_offer(tcp_port, TEAM_NAME)

    stop_event = threading.Event()
    threading.Thread(
        target=offer_broadcaster,
        args=(stop_event, udp_sock, offer_bytes, directed_bcast),
        daemon=True
    ).start()

    try:
        while True:
            conn, addr = tcp_sock.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        stop_event.set()
        try:
            udp_sock.close()
        except Exception:
            pass
        try:
            tcp_sock.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
