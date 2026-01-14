# client.py
import socket
import time

from protocol import (
    UDP_OFFER_PORT,
    unpack_offer,
    pack_request,
    pack_payload_decision,
    unpack_payload_card,
    RESULT_NOT_OVER,
    RESULT_WIN,
    RESULT_LOSS,
    RESULT_TIE,
    DECISION_HIT,
    DECISION_STAND,
    card_value,
    PAYLOAD_S2C_STRUCT,
)

CLIENT_TEAM_NAME = "Blackijecky - client"

# Prefer connecting to our server if its offer appears within this window.
PREFERRED_SERVER_NAME = "Blackijecky - server"
PREFERRED_WAIT_SECONDS = 3.0


def recv_exact(sock: socket.socket, n: int) -> bytes | None:
    """Read exactly n bytes from a TCP socket. Return None on disconnect/timeout/error."""
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (ConnectionResetError, ConnectionAbortedError, OSError, TimeoutError):
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def suit_name(s: int) -> str:
    """Convert suit code (0..3) into a Unicode symbol."""
    return ["♥", "♦", "♣", "♠"][s]


def rank_name(r: int) -> str:
    """Convert rank (1..13) into a display string."""
    if r == 1:
        return "A"
    if 2 <= r <= 10:
        return str(r)
    return {11: "J", 12: "Q", 13: "K"}[r]


def color_card(rank: str, suit_sym: str) -> str:
    """Use ANSI color for red suits (hearts/diamonds)."""
    if suit_sym in ("♥", "♦"):
        return f"\033[31m{rank}{suit_sym}\033[0m"
    return f"{rank}{suit_sym}"


def result_name(code: int) -> str:
    if code == RESULT_WIN:
        return "WIN"
    if code == RESULT_LOSS:
        return "LOSS"
    if code == RESULT_TIE:
        return "TIE"
    return "NOT_OVER"


def ask_rounds_once() -> int | None:
    """Ask once for number of rounds per TCP session."""
    while True:
        try:
            rounds = int(input("How many rounds to play each session (1-255)? ").strip())
            if 1 <= rounds <= 255:
                return rounds
        except KeyboardInterrupt:
            print("\nClient stopped.")
            return None
        except ValueError:
            pass
        print("Please enter a number between 1 and 255.")


def open_udp_listener() -> socket.socket:
    """Open a UDP socket and bind to the fixed offer port."""
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    udp.bind(("", UDP_OFFER_PORT))
    return udp


def _normalize_server_name(server_name: object) -> str:
    """Normalize server_name (bytes/str) and trim null padding."""
    if isinstance(server_name, bytes):
        return server_name.decode("utf-8", errors="ignore").rstrip("\x00").strip()
    return str(server_name).rstrip("\x00").strip()


def wait_for_offer(udp: socket.socket) -> tuple[str, int, str]:
    """
    Block until a valid offer is received.
    Prefer our server name for a short window, otherwise fall back to the first valid offer.

    Returns (server_ip, tcp_port, server_name).
    """
    print(f"Client started, listening for offer requests on UDP {UDP_OFFER_PORT}...")

    udp.settimeout(0.5)  # allows timing logic without busy-waiting
    start = time.time()

    first_any: tuple[str, int, str] | None = None

    while True:
        try:
            data, addr = udp.recvfrom(4096)
        except socket.timeout:
            if first_any is not None and (time.time() - start) >= PREFERRED_WAIT_SECONDS:
                server_ip, tcp_port, server_name = first_any
                print(
                    f"Preferred server not found within {PREFERRED_WAIT_SECONDS:.1f}s. "
                    f"Using first offer: {server_ip} (server_name={server_name}, tcp_port={tcp_port})"
                )
                return first_any
            continue

        offer = unpack_offer(data)
        if offer is None:
            continue

        server_ip = addr[0]
        server_name_norm = _normalize_server_name(offer.server_name)

        if first_any is None:
            first_any = (server_ip, offer.tcp_port, server_name_norm)

        if server_name_norm == PREFERRED_SERVER_NAME:
            print(f"Received preferred offer from {server_ip} (server_name={server_name_norm}, tcp_port={offer.tcp_port})")
            return server_ip, offer.tcp_port, server_name_norm

        # keep listening until we either see the preferred server or timeout to fallback


def play_session(server_ip: str, tcp_port: int, rounds: int) -> tuple[int, int, int] | None:
    """
    Connect via TCP, send request, play 'rounds' rounds.
    Returns (wins, losses, ties) on success, or None if TCP connection failed.
    """
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.settimeout(3.0)

    try:
        tcp.connect((server_ip, tcp_port))
        tcp.settimeout(15.0)  # gameplay/receives timeout
        tcp.sendall(pack_request(rounds, CLIENT_TEAM_NAME))
    except (TimeoutError, OSError):
        print(f"Failed to connect to {server_ip}:{tcp_port} (TCP). Looking for another offer...")
        try:
            tcp.close()
        except Exception:
            pass
        return None

    print(f"Sent TCP request: rounds={rounds}, client_name={CLIENT_TEAM_NAME}")

    wins = losses = ties = 0
    s2c_size = PAYLOAD_S2C_STRUCT.size  # protocol-defined

    for round_idx in range(1, rounds + 1):
        print(f"\n=== ROUND {round_idx}/{rounds} ===")

        player_sum = 0

        # Receive initial 3 cards: player, player, dealer upcard
        for i in range(3):
            raw = recv_exact(tcp, s2c_size)
            if raw is None:
                print("Server disconnected.")
                tcp.close()
                return wins, losses, ties

            p = unpack_payload_card(raw)
            if p is None:
                print("Bad payload from server.")
                tcp.close()
                return wins, losses, ties

            suit_sym = suit_name(p.suit)
            card_str = color_card(rank_name(p.rank), suit_sym)

            if i < 2:
                player_sum += card_value(p.rank)
                print(f"Your card: {card_str} (sum={player_sum})")
            else:
                print(f"Dealer upcard: {card_str}")

        # Player turn
        while True:
            try:
                choice = input("Hit or Stand? ").strip().lower()
            except KeyboardInterrupt:
                print("\nClient stopped.")
                tcp.close()
                return wins, losses, ties

            if choice.startswith("h"):
                try:
                    tcp.sendall(pack_payload_decision(DECISION_HIT))
                except OSError:
                    print("Server disconnected.")
                    tcp.close()
                    return wins, losses, ties

                raw = recv_exact(tcp, s2c_size)
                if raw is None:
                    print("Server disconnected.")
                    tcp.close()
                    return wins, losses, ties

                p = unpack_payload_card(raw)
                if p is None:
                    print("Bad payload from server.")
                    tcp.close()
                    return wins, losses, ties

                suit_sym = suit_name(p.suit)
                card_str = color_card(rank_name(p.rank), suit_sym)

                player_sum += card_value(p.rank)
                print(f"You drew: {card_str} (sum={player_sum})")

                if p.result != RESULT_NOT_OVER:
                    print(f"Result: {result_name(p.result)}")
                    if p.result == RESULT_WIN:
                        wins += 1
                    elif p.result == RESULT_LOSS:
                        losses += 1
                    else:
                        ties += 1
                    break

                continue

            if choice.startswith("s"):
                try:
                    tcp.sendall(pack_payload_decision(DECISION_STAND))
                except OSError:
                    print("Server disconnected.")
                    tcp.close()
                    return wins, losses, ties

                print("You STAND. Dealer's turn...")

                # Dealer reveal/draw until result != NOT_OVER
                while True:
                    raw = recv_exact(tcp, s2c_size)
                    if raw is None:
                        print("Server disconnected.")
                        tcp.close()
                        return wins, losses, ties

                    p = unpack_payload_card(raw)
                    if p is None:
                        print("Bad payload from server.")
                        tcp.close()
                        return wins, losses, ties

                    suit_sym = suit_name(p.suit)
                    card_str = color_card(rank_name(p.rank), suit_sym)
                    print(f"Dealer shows/draws: {card_str}")

                    if p.result != RESULT_NOT_OVER:
                        print(f"Result: {result_name(p.result)}")
                        if p.result == RESULT_WIN:
                            wins += 1
                        elif p.result == RESULT_LOSS:
                            losses += 1
                        else:
                            ties += 1
                        break

                break

            print("Type Hit or Stand.")

    tcp.close()
    return wins, losses, ties


def main():
    rounds = ask_rounds_once()
    if rounds is None:
        return

    udp = open_udp_listener()
    try:
        while True:
            server_ip, tcp_port, _server_name = wait_for_offer(udp)

            result = play_session(server_ip, tcp_port, rounds)
            if result is None:
                # TCP failed -> immediately go back to listening for offers
                continue

            wins, losses, ties = result
            total = wins + losses + ties
            win_rate = (wins / total) if total else 0.0
            print(f"\nFinished playing {total} rounds, win rate: {win_rate:.3f} (W={wins}, L={losses}, T={ties})\n")
            # Immediately continue listening (as required)
    except KeyboardInterrupt:
        print("\nClient stopped.")
    finally:
        try:
            udp.settimeout(None)
        except Exception:
            pass
        try:
            udp.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
