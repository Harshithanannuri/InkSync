"""End-to-end TCP integration tests. Run: python -m unittest discover -s tests -v"""
import json, os, socket, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class Peer:
    def __init__(self,port):
        self.sock=socket.create_connection(("127.0.0.1",port),timeout=3)
        self.sock.settimeout(3); self.file=self.sock.makefile("rb")
    def send(self,obj):
        self.sock.sendall(json.dumps(obj,separators=(",",":")).encode()+b"\n")
    def recv(self):
        line=self.file.readline()
        if not line: raise RuntimeError("server disconnected")
        return json.loads(line)
    def until(self,kind):
        deadline=time.time()+4
        while time.time()<deadline:
            m=self.recv()
            if m.get("type")==kind:return m
        raise AssertionError(f"Did not receive {kind}")
    def close(self):
        try:self.sock.close()
        except OSError:pass

class ServerIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1",0))
            self.port=probe.getsockname()[1]
        self.db=str(Path(self.tmp.name)/"test.sqlite3")
        self.proc=None
        self.start_server()
    def start_server(self):
        self.proc=subprocess.Popen([sys.executable,str(ROOT/"server.py"),"--host","127.0.0.1",
            "--port",str(self.port),"--db",self.db],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(150):
            if self.proc.poll() is not None:
                self.fail("server process exited during startup")
            try:
                with socket.create_connection(("127.0.0.1",self.port),timeout=.2): return
            except OSError: time.sleep(.2)
        self.fail("server did not start within 30 seconds")
    def stop_server(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(3)
            except subprocess.TimeoutExpired:self.proc.kill()
    def tearDown(self):
        self.stop_server(); self.tmp.cleanup()
    def test_two_clients_sync_undo_redo_and_restart(self):
        a=Peer(self.port)
        a.send({"type":"create","name":"Integration","username":"Alice","password":"pass1234"})
        created=a.until("created"); code=created["room"]; token=created["owner_token"]
        a.until("joined"); snap_a=a.until("snapshot")
        wrong=Peer(self.port)
        wrong.send({"type":"join","room":code,"username":"Intruder","password":"wrongpass"})
        self.assertIn("Incorrect",wrong.until("error")["message"]); wrong.close()
        b=Peer(self.port)
        b.send({"type":"join","room":code,"username":"Bob","password":"pass1234"})
        b.until("joined"); snap_b=b.until("snapshot")
        self.assertEqual(snap_b["seq"],snap_a["seq"])
        a.send({"type":"cursor","point":[.55,.45]})
        cursor=b.until("cursor")
        self.assertEqual(cursor["username"],"Alice")
        self.assertEqual(cursor["point"],[.55,.45])
        duplicate=Peer(self.port)
        duplicate.send({"type":"join","room":code,"username":"Bob","password":"pass1234"})
        self.assertIn("already active",duplicate.until("error")["message"]); duplicate.close()
        op={"kind":"stroke","color":"#123456","width":3,"points":[[.1,.1],[.4,.4]]}
        a.send({"type":"op","op":op})
        ev_a=a.until("canvas_event"); ev_b=b.until("canvas_event")
        self.assertEqual(ev_a["seq"],ev_b["seq"])
        self.assertEqual(ev_a["op"]["id"],ev_b["op"]["id"])
        b.send({"type":"sync"})
        fresh=b.until("snapshot")
        self.assertEqual(len(fresh["items"]),1)
        # Invalid coordinates are rejected and never broadcast.
        b.send({"type":"op","op":{"kind":"stroke","color":"#000000","width":3,"points":[[2,.2]]}})
        self.assertIn("Coordinates",b.until("error")["message"])
        # A second client cannot clear the host's board.
        b.send({"type":"clear"})
        denied=b.until("error")
        self.assertIn("Only the host",denied["message"])
        # Concurrent operations are committed in one total sequence order.
        import threading
        op2={"kind":"line","color":"#ff0000","width":2,"points":[[.2,.2],[.8,.8]]}
        op3={"kind":"ellipse","color":"#00aa00","width":2,"points":[[.3,.3],[.7,.7]]}
        t1=threading.Thread(target=lambda:a.send({"type":"op","op":op2}))
        t2=threading.Thread(target=lambda:b.send({"type":"op","op":op3}))
        t1.start(); t2.start(); t1.join(); t2.join()
        # Both clients must receive the same two monotonically sequenced operations.
        a_events=[a.until("canvas_event"),a.until("canvas_event")]
        b_events=[b.until("canvas_event"),b.until("canvas_event")]
        self.assertEqual([e["seq"] for e in a_events],[e["seq"] for e in b_events])
        self.assertEqual([e["op"]["id"] for e in a_events],[e["op"]["id"] for e in b_events])
        a.send({"type":"undo"})
        undo_a=a.until("canvas_event"); undo_b=b.until("canvas_event")
        self.assertEqual(undo_a["action"],"undo")
        b.send({"type":"redo"})
        error=b.until("error")
        self.assertIn("Nothing to redo",error["message"])
        a.send({"type":"redo"})
        redo_a=a.until("canvas_event"); redo_b=b.until("canvas_event")
        self.assertEqual(redo_a["action"],"redo")
        a.close(); b.close()
        self.stop_server(); self.start_server()
        c=Peer(self.port)
        c.send({"type":"join","room":code,"username":"Alice","password":"pass1234","owner_token":token})
        c.until("joined"); recovered=c.until("snapshot")
        self.assertEqual(len(recovered["items"]),3)
        self.assertIn("stroke",{item["kind"] for item in recovered["items"]})
        c.close()

if __name__=="__main__":unittest.main()
