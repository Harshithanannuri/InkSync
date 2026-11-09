# Physical LAN test (required before demo)

A localhost integration test is included in `test_integration.py`; it proves that two TCP clients can share ordered events, undo/redo, enforce host clear permissions and recover persisted state after a server restart. It does not replace testing on separate computers.

## Test topology
- Computer A: run the server and one client.
- Computer B: run a second client.
- Optional Computer C/D: run more clients.
- All computers join the same private Wi-Fi/hotspot.
- Disable WAN/Internet access but keep the local Wi-Fi/LAN active.

## Procedure
1. On A, start `LANWhiteboardServer` or `python server.py --host 0.0.0.0 --port 8765`.
2. Find A's private LAN IPv4 address (macOS: `ipconfig getifaddr en0`; Windows: `ipconfig`).
3. Allow inbound TCP 8765 on A's firewall for the private network.
4. On A, run client, create a room and share the code/password.
5. On B/C/D, enter A's LAN IP, port 8765, username, room code and password.
6. Draw simultaneously from all clients; verify all see identical objects.
7. Move the mouse on each canvas; verify remote cursors and names appear on peers.
8. Test image insertion and PNG/PDF export.
9. Disconnect Wi-Fi on one client for 20 seconds, restore it, and verify automatic reconnect and snapshot recovery.
10. Stop/restart server A; wait for clients to reconnect and verify persisted canvas.
11. Turn off Internet/WAN while preserving local network and repeat the collaboration checks.

## Record evidence
Capture server terminal, two or more client windows, a remote cursor, chat, image on canvas, exported PDF/PNG, reconnect status and the server-restart recovery. Write down pass/fail and any observed delay. Do not claim the physical LAN test passed until your team has actually performed it.
